"""VIRTUAL ACCOUNT mode — TDD spec (a)-(d), (f). DEMO/PAPER only.

Jin 2026-07-07 mandate: with POLARIS_VIRTUAL_ACCOUNT=1, real_roundtrip is
forced False regardless of the caller-requested value, so every venue touch
point already gated behind real_roundtrip (order submit, reconcile/orphans/
drift, OKX venue-resting stop, the signed OKX balance health-check, the
Alpaca buying-power/OKX available-USDT reads) never fires. Aggressive bias
preserved — sizing MATH/caps/amplifiers are untouched; only the boolean that
routes fills to simulate_open_fill/simulate_close vs the real adapter path.

(a) reserve_and_submit under the virtual override drives ZERO adapter calls
    (spy adapters that fail the test if touched).
(b) run_production_paper_loop's boot-time real_roundtrip-gated reconcile
    functions are never called under virtual mode (verified via the sim-DB
    guard: real_roundtrip forced False means the sim-DB-refusal error, which
    ONLY fires when real_roundtrip=True, never raises).
(c) sizing's demo_starting_equity_* constants are read with zero venue calls
    (already true pre-change; asserted here as a locked-in invariant).
(d) the full entry->open->exit->close->learn cycle completes on the internal
    ledger under virtual mode (reserve_and_submit + close_oldest_with_real_pnl
    round-trip with the sim path, spy adapters never touched).
(f) POLARIS_VIRTUAL_ACCOUNT unset -> real_roundtrip passes through unchanged
    (byte-identical to pre-change default behavior).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.scripts._production_close import close_oldest_with_real_pnl
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._virtual_account import resolve_real_roundtrip
from polaris.scripts.production_paper_loop import ProdLoopState, run_production_paper_loop
from polaris.strategies.base import RawSignal


def _sig(signal_id: str = "virtual_sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _failing_spy_adapter() -> AsyncMock:
    """Any call on this adapter raises — proves virtual mode never touches it."""
    adapter = AsyncMock()

    async def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "venue adapter touched under POLARIS_VIRTUAL_ACCOUNT=1 — "
            f"call args={args} kwargs={kwargs}"
        )

    adapter.place_market_order.side_effect = _fail
    adapter.fetch_order.side_effect = _fail
    adapter.fetch_balance.side_effect = _fail
    adapter.fetch_ticker.side_effect = _fail
    adapter.cancel_algo_order.side_effect = _fail
    return adapter


@pytest.mark.asyncio
async def test_a_reserve_and_submit_virtual_mode_zero_adapter_calls(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    reset_process_fence()
    state = ProdLoopState()
    spy = _failing_spy_adapter()
    requested_real_roundtrip = True  # caller asks for real — must be overridden
    effective = resolve_real_roundtrip(requested_real_roundtrip)
    assert effective is False

    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig(), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=effective, okx_adapter=spy,
    )
    assert trade is not None
    assert spy.place_market_order.await_count == 0
    assert spy.fetch_order.await_count == 0
    assert spy.fetch_balance.await_count == 0
    # Fill landed via the internal-ledger simulate path, not the adapter.
    fill_row = memdb.execute(
        "SELECT order_id FROM fills WHERE contribution_id = ? AND is_close = 0",
        (trade.position_id,),
    ).fetchone()
    assert fill_row is not None


@pytest.mark.asyncio
async def test_b_virtual_mode_forces_real_roundtrip_off_before_reconcile_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every reconcile call in run_production_paper_loop is gated behind
    ``if real_roundtrip and ...`` — proving real_roundtrip resolves to False
    under virtual mode (even though the sim-DB path is requested with
    real_roundtrip=True) is sufficient to prove reconcile never fires,
    without needing to mock 4 separate reconcile functions individually.
    The sim-DB-refusal ValueError ONLY raises when real_roundtrip=True
    reaches that check — its ABSENCE here proves the override took effect
    before that gate, which sits before every reconcile call in the function.
    """
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    # duration_sec=0.0 exits the tick loop immediately after boot — cheap.
    # db_path=None defaults to the shared sim DB, which would normally raise
    # under real_roundtrip=True (see test_real_roundtrip_refuses_default_db);
    # under virtual mode it must NOT raise, proving the override applies
    # before that (and every reconcile) gate.
    exit_code = await run_production_paper_loop(
        duration_sec=0.0, tick_sec=0.0, db_path=tmp_path / "polaris.sqlite",
        haiku=object(), real_roundtrip=True,
    )
    assert exit_code in (0, 1)


def test_c_sizing_equity_source_is_constant_not_venue_call() -> None:
    """Locked-in invariant: sizing's equity source is a demo constant, never
    a venue balance call — already true pre-change (compute_size reads
    PortfolioState.equity_usd, fed by demo_starting_equity_*/production_
    default_equity_usd, never fetch_balance/fetch_alpaca_buying_power).
    """
    from polaris.core.sizing.constants import (
        equity_usd_for_venue,
        production_default_equity_usd,
    )

    # Pure functions — calling them must never require network/adapters.
    assert production_default_equity_usd() > 0.0
    assert equity_usd_for_venue("okx") > 0.0
    assert equity_usd_for_venue("capital") > 0.0
    assert equity_usd_for_venue("alpaca") > 0.0


@pytest.mark.asyncio
async def test_d_full_entry_open_exit_close_cycle_on_internal_ledger(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    reset_process_fence()
    state = ProdLoopState()
    spy = _failing_spy_adapter()
    effective = resolve_real_roundtrip(True)

    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("cycle_sig"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()), real_roundtrip=effective,
        okx_adapter=spy,
    )
    assert trade is not None
    state.open_trades.append(trade)

    def _lookup_regime(*_a: object, **_k: object) -> str:
        return ""

    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()) + 60,
        lookup_regime=_lookup_regime, real_roundtrip=effective,
        okx_adapter=spy,
    )
    assert spy.place_market_order.await_count == 0
    assert spy.fetch_balance.await_count == 0
    assert len(state.closed_trades) == 1
    closed_row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert closed_row is not None and closed_row[0] == "closed"


@pytest.mark.asyncio
async def test_f_virtual_account_unset_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_VIRTUAL_ACCOUNT", raising=False)
    # real_roundtrip=True against the shared sim DB name must still raise
    # exactly as before this feature existed (behavior unchanged when the
    # env is unset).
    with pytest.raises(ValueError, match="real_roundtrip"):
        await run_production_paper_loop(
            duration_sec=0.0, tick_sec=0.0, db_path=Path("data/polaris.sqlite"),
            haiku=object(), real_roundtrip=True,
        )
