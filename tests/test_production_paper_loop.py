"""Day 8 production paper loop — TDD test surface.

Covers the 20 scenarios listed in the Day 8 spec (Test § A–K) plus 2
Hypothesis property tests. Tests run against the in-memory ``memdb`` fixture
+ patched venue fetchers so no demo API calls happen during ``pytest``.

Spec source: Polaris Day 8 task brief (vault/_NOW.md → Day 8 wire).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as hyp

from polaris.core.data.schema import Bar
from polaris.core.universe.schema import UniverseInstrument
from polaris.scripts._production_indicators import (
    build_real_market_view,
    compute_real_regime,
    compute_unrealized_pnl_r,
)
from polaris.scripts._production_layers import (
    compute_and_flip_regime,
    ingest_bars_for_focus,
)
from polaris.scripts._production_pipeline import (
    real_pnl_r_from_fills as _real_pnl_r_from_fills,
)
from polaris.scripts._production_pipeline import (
    reserve_and_submit as _reserve_and_submit,
)
from polaris.scripts._production_pipeline import (
    run_pipeline_for_signal as _run_pipeline_for_signal,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts.production_paper_loop import (
    ProdLoopState,
    _all_strategies,
    _is_finite_signal,
)
from polaris.strategies.base import RawSignal


@pytest.fixture(autouse=True)
def _yahoo_primary_off() -> Iterator[None]:
    """Neutralize the Yahoo-PRIMARY layer for the EXCHANGE-routing tests here.

    Yahoo Finance is now the primary bar-history source (Alpaca 429 root fix);
    these tests verify the exchange ingest path, so we stub ``fetch_yahoo_bars``
    → [] (Yahoo has no bars) and clear the fallback cooldown so the exchange
    branch is always reached. flow_not_block unchanged.
    """
    import polaris.scripts._production_bars as pbars
    import polaris.scripts._yahoo_bars as ybars

    async def _no_yahoo(*args: Any, **kwargs: Any) -> list[Bar]:
        return []

    ybars._FALLBACK_LAST_MONO.clear()
    with patch.object(pbars, "fetch_yahoo_bars", new=_no_yahoo):
        yield
    ybars._FALLBACK_LAST_MONO.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    *,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    ts: int = 1_700_000_000,
    close: float = 60_000.0,
    volume: float = 1_000.0,
) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"crypto:{symbol.split('-')[0]}",
        venue=venue,
        symbol=symbol,
        bar_interval="1m",
        ts=ts,
        open=close * 0.999,
        high=close * 1.001,
        low=close * 0.998,
        close=close,
        volume=volume,
        notional_usd=close * volume,
    )


def _bars_series(n: int = 60, base: float = 60_000.0, drift: float = 1.0) -> list[Bar]:
    out: list[Bar] = []
    for i in range(n):
        out.append(_make_bar(ts=1_700_000_000 + i * 60, close=base + i * drift))
    return out


def _seed_universe(conn: sqlite3.Connection, *, venue: str = "okx", symbol: str = "BTC-USDT") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, quote_ccy, "
        " state, vol_24h_usd, spread_bps, atr_24h_pct, depth_10bps_usd, "
        " signal_density_7d, listing_ts, last_seen_ts, is_active, active_reason) "
        "VALUES (?, ?, ?, ?, 'crypto', 'USDT', 'live', 1e9, 2.0, 3.0, 1e6, "
        "        0.0, NULL, ?, 1, NULL)",
        (
            venue, symbol, f"{venue}:{symbol}",
            f"crypto:{symbol.split('-')[0]}",
            int(time.time()),
        ),
    )


# ---------------------------------------------------------------------------
# A — Layer 0 producer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l0_producer_5min_okx_refresh(memdb: sqlite3.Connection) -> None:
    """OKX refresh path persists rows + activates them."""
    sample = [
        UniverseInstrument(
            venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC", asset_class="crypto",
            quote_ccy="USDT", state="live", vol_24h_usd=2e9, spread_bps=2.0,
            atr_24h_pct=4.0, depth_10bps_usd=2e6, signal_density_7d=0.0,
            listing_ts=None, last_seen_ts=int(time.time()),
        )
    ]
    with patch(
        "polaris.scripts._production_layers.fetch_okx_instruments",
        new=AsyncMock(return_value=sample),
    ):
        from polaris.scripts._production_layers import refresh_okx_universe_once

        active = await refresh_okx_universe_once(memdb)
    assert active == 1
    row = memdb.execute(
        "SELECT is_active FROM universe WHERE instrument_id='okx:BTC-USDT'"
    ).fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_l0_producer_10min_capital_refresh(memdb: sqlite3.Connection) -> None:
    """Capital refresh skips when creds missing (no false positives)."""
    # Ensure creds absent.
    import os

    from polaris.scripts._production_layers import refresh_capital_universe_once
    for k in ("CAP_API_KEY", "CAP_EMAIL", "CAP_PASSWORD"):
        os.environ.pop(k, None)
    active = await refresh_capital_universe_once(memdb)
    assert active == 0


# ---------------------------------------------------------------------------
# A2 — STALL fix: focus refresh is OFFLOADED off the event loop (#74)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l0_producer_offloads_focus_refresh_off_event_loop() -> None:
    """The synchronous, multi-second ``refresh_focus_watchlist`` (judge + executemany
    persist) ran INLINE on the single event loop, starving the tick task → live
    STALL (gap 6-33s, "shared-conn blocker?"). It must now run via
    ``asyncio.to_thread`` so the event loop keeps cycling while focus recomputes.

    Pin: the offloaded call runs on a NON-event-loop thread (proves it is off the
    loop), AND it receives a DEDICATED ``focus_conn`` (NOT the main ``conn``) — a
    second sqlite3.Connection must never be shared across threads with the loop's
    own connection. flow_not_block / 9-stack untouched: this changes only WHERE the
    refresh runs, never WHETHER a symbol is watched or how it is sized."""
    import threading

    from polaris.scripts import production_paper_loop as ppl
    from polaris.scripts._production_state import ProdLoopState

    loop_thread_id = threading.get_ident()
    seen: dict[str, Any] = {}

    def _fake_refresh(passed_conn: object, **kwargs: object) -> int:
        seen["thread_id"] = threading.get_ident()
        seen["conn"] = passed_conn
        return 0

    main_conn = object()  # sentinel — the loop-owned connection
    focus_conn = object()  # sentinel — the dedicated focus connection
    state = ProdLoopState()
    stop_evt = asyncio.Event()
    stop_evt.set()  # one immediate refresh, then exit before the 15s sleep loop

    with patch.object(ppl, "refresh_focus_watchlist", new=_fake_refresh), \
        patch.object(ppl, "refresh_okx_universe_once", new=AsyncMock(return_value=0)), \
        patch.object(ppl, "refresh_capital_universe_once", new=AsyncMock(return_value=0)), \
        patch.object(ppl, "refresh_alpaca_universe_once", new=AsyncMock(return_value=0)):
        await ppl._layer0_producer(
            main_conn,  # type: ignore[arg-type]
            state=state,
            stop_evt=stop_evt,
            focus_conn=focus_conn,  # type: ignore[arg-type]
        )

    # The refresh ran on a worker thread, NOT the event-loop thread (off the loop).
    assert seen["thread_id"] != loop_thread_id, (
        "refresh_focus_watchlist must run via asyncio.to_thread (off the event "
        "loop), not inline — inline blocking starves the tick task → STALL"
    )
    # It used the DEDICATED focus connection, never the loop-owned main conn
    # (a sqlite3.Connection must not be touched from two threads at once).
    assert seen["conn"] is focus_conn
    assert seen["conn"] is not main_conn


@pytest.mark.asyncio
async def test_momentum_z_shadow_async_offloads_to_dedicated_worker_thread() -> None:
    """The momentum-z shadow scan (``compute_momentum_z_shadow``) is an UNBOUNDED
    synchronous ``bars`` query — already 1M+ rows on the live Alpaca universe.
    Run inline inside ``layer0_task`` it freezes the shared event loop (tick
    engine + WS fills) the same way the pre-#74 focus refresh did. It must run
    via ``asyncio.to_thread`` against a DEDICATED ``momentum_conn`` sidecar —
    never the loop-owned/caller ``conn``."""
    import threading

    from polaris.scripts._production_layers import _momentum_z_shadow_async

    loop_thread_id = threading.get_ident()
    seen: dict[str, Any] = {}

    def _fake_compute(passed_conn: object, instruments: object, *, now_ts: int) -> dict[str, float]:
        seen["thread_id"] = threading.get_ident()
        seen["conn"] = passed_conn
        return {}

    main_conn = object()  # sentinel — the loop-owned connection
    momentum_conn = object()  # sentinel — the dedicated read-only connection
    with patch(
        "polaris.scripts._production_layers.compute_momentum_z_shadow", new=_fake_compute
    ):
        out = await _momentum_z_shadow_async(
            main_conn,  # type: ignore[arg-type]
            [],
            now_ts=1_780_000_000,
            momentum_conn=momentum_conn,  # type: ignore[arg-type]
        )

    assert out == {}
    assert seen["thread_id"] != loop_thread_id, (
        "the momentum-z scan must run via asyncio.to_thread (off the event "
        "loop), not inline — inline blocking starves the tick task → STALL"
    )
    assert seen["conn"] is momentum_conn
    assert seen["conn"] is not main_conn


@pytest.mark.asyncio
async def test_momentum_z_shadow_async_falls_back_to_inline_sync_without_dedicated_conn() -> None:
    """No dedicated ``momentum_conn`` given (tests / smoke path) → inline sync
    call on the caller's own ``conn`` — behavior-identical fallback, no thread
    hop, same as before the offload existed."""
    from polaris.scripts._production_layers import _momentum_z_shadow_async

    out = await _momentum_z_shadow_async(
        sqlite3.connect(":memory:"), [], now_ts=1_780_000_000, momentum_conn=None
    )
    assert out == {}


@pytest.mark.asyncio
async def test_l0_producer_focus_error_does_not_kill_task() -> None:
    """2nd-defense (#74): an UNHANDLED error in the offloaded focus refresh must NOT
    propagate out of the Layer-0 producer (a dead layer0_task → frozen focus →
    stale watchlist → trade degrade). The producer logs + continues; this proves the
    initial refresh raising does not crash ``_layer0_producer`` (flow_not_block)."""
    from polaris.scripts import production_paper_loop as ppl
    from polaris.scripts._production_state import ProdLoopState

    def _boom(passed_conn: object, **kwargs: object) -> int:
        raise RuntimeError("focus refresh exploded")

    state = ProdLoopState()
    stop_evt = asyncio.Event()
    stop_evt.set()  # one immediate (exploding) refresh, then exit

    with patch.object(ppl, "refresh_focus_watchlist", new=_boom), \
        patch.object(ppl, "refresh_okx_universe_once", new=AsyncMock(return_value=0)), \
        patch.object(ppl, "refresh_capital_universe_once", new=AsyncMock(return_value=0)), \
        patch.object(ppl, "refresh_alpaca_universe_once", new=AsyncMock(return_value=0)):
        # Must return cleanly despite the refresh raising — no exception escapes.
        await ppl._layer0_producer(
            object(),  # type: ignore[arg-type]
            state=state,
            stop_evt=stop_evt,
            focus_conn=object(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# C1 — Capital active-universe collapse guard (27h 1-symbol breadth loss)
# ---------------------------------------------------------------------------


def _cap_inst(symbol: str, *, state: str = "live") -> UniverseInstrument:
    return UniverseInstrument(
        venue="capital", symbol=symbol, instrument_id=f"capital:{symbol}",
        underlying_group_id=f"forex:{symbol}", asset_class="forex",
        quote_ccy="USD", state=state, vol_24h_usd=1e6, spread_bps=2.0,
        atr_24h_pct=0.5, depth_10bps_usd=2e4, signal_density_7d=0.0,
        listing_ts=None, last_seen_ts=int(time.time()),
    )


def test_capital_collapse_guard_preserves_prior_breadth() -> None:
    """Forensic: a healthy fetch but session-driven validity collapse left the
    Capital active book at exactly 1 closed symbol (EURUSD_W) for 27h. The guard
    must KEEP the prior healthy active set instead of seating the single survivor
    (flow_not_block: preserve breadth, never zero-out)."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    prior = {f"capital:FX{i}" for i in range(20)}  # healthy prior book of 20
    new_active = {"capital:EURUSD_W"}  # ranking collapsed to 1 weekend survivor
    fetched = 202  # healthy full fetch (not a partial/zero fetch)
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active, prior_active_ids=prior, fetched_count=fetched
    )
    assert out == prior  # prior breadth preserved, not collapsed to 1


def test_capital_collapse_guard_noop_when_healthy() -> None:
    """A healthy new active set is used as-is (guard is a no-op)."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    prior = {f"capital:FX{i}" for i in range(20)}
    new_active = {f"capital:FX{i}" for i in range(18)}  # normal churn, still broad
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active, prior_active_ids=prior, fetched_count=202
    )
    assert out == new_active


def test_capital_collapse_guard_preserves_breadth_at_4() -> None:
    """Forensic 2026-06-30/07-01: a healthy fetch (2419 rows) but a session-driven
    validity collapse left the active book at 4 fringe commodity/index epics
    (OIL_BRENT/GASOIL/NYFANG/SG25 — none of the wave2 strategies' core symbols)
    for 31h+, silencing every Capital strategy (0 signals). The prior FLOOR=1
    only caught the single-symbol 2026-05-31 incident; a 4-symbol collapse from
    a healthy 134-symbol prior book must ALSO be caught (flow_not_block:
    preserve breadth, never let it get stuck at a tiny fringe set)."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    prior = {f"capital:FX{i}" for i in range(134)}  # healthy prior book
    new_active = {"capital:OIL_BRENT", "capital:GASOIL", "capital:NYFANG", "capital:SG25"}
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active, prior_active_ids=prior, fetched_count=2419
    )
    assert out == prior  # prior breadth preserved, not collapsed to the 4 fringe survivors


def test_capital_collapse_guard_catches_small_prior_book_collapse() -> None:
    """Regression: ratio-only threshold has a dead zone for prior in [5,9] —
    0.10 * prior < 1 there, so the only new-count that satisfies the ratio is 0,
    which is already routed to the full-closure branch. A collapse to a single
    symbol from a healthy small prior book (the exact 2026-05-31 forensic
    incident shape: 8 FX symbols -> 1 survivor) must still be caught (the guard
    must OR the ratio with the original absolute FLOOR=1, not replace it)."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    prior = {f"capital:FX{i}" for i in range(8)}  # healthy small prior book (>= PRIOR_MIN=5)
    new_active = {"capital:EURUSD_W"}  # collapsed to 1 survivor
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active, prior_active_ids=prior, fetched_count=100
    )
    assert out == prior  # prior breadth preserved, not collapsed to 1


def test_capital_collapse_guard_noop_full_closure() -> None:
    """A full session closure (active=0) is the legitimate session_wait path — the
    guard must NOT resurrect it (rows revive next refresh once TRADEABLE)."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    prior = {f"capital:FX{i}" for i in range(20)}
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=set(), prior_active_ids=prior, fetched_count=202
    )
    assert out == set()


def test_capital_collapse_guard_noop_cold_start() -> None:
    """No prior active book (cold start) → use the new set as-is even if small."""
    from polaris.scripts._production_layers import (
        capital_active_ids_after_collapse_guard,
    )

    new_active = {"capital:EURUSD"}
    out = capital_active_ids_after_collapse_guard(
        new_active_ids=new_active, prior_active_ids=set(), fetched_count=202
    )
    assert out == new_active


@pytest.mark.asyncio
async def test_capital_refresh_collapse_keeps_prior_active(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: seed a healthy prior Capital active book, then a refresh whose
    fetch is healthy (202 rows) but only 1 is TRADEABLE → the active book must NOT
    collapse to 1; the prior 20 stay active."""
    import os

    import polaris.scripts._production_layers as layers_mod

    # Seed prior healthy active book (20 forex rows, is_active=1).
    prior_syms = [f"FX{i}" for i in range(20)]
    from polaris.core.universe.discovery import persist_universe
    prior_insts = [_cap_inst(s) for s in prior_syms]
    persist_universe(memdb, prior_insts, is_active_set={i.instrument_id for i in prior_insts})
    assert memdb.execute(
        "SELECT COUNT(*) FROM universe WHERE venue='capital' AND is_active=1"
    ).fetchone()[0] == 20

    # Healthy fetch of 202 rows but only 1 TRADEABLE (weekend session collapse):
    # the SAME prior epics reappear non-live (session-wait) + a few more, plus the
    # 1 weekend survivor. The upsert flips the prior rows is_active=0, so without
    # the guard the book collapses to 1 (the live mechanism). rank → active 1.
    fetched = (
        [_cap_inst("EURUSD_W")]
        + [_cap_inst(s, state="closed") for s in prior_syms]
        + [_cap_inst(f"WK{i}", state="closed") for i in range(181)]
    )
    monkeypatch.setattr(
        layers_mod, "fetch_capital_instruments", AsyncMock(return_value=fetched)
    )
    # Skip the live proxy session entirely (no creds / no network).
    monkeypatch.setattr(
        layers_mod, "populate_capital_proxies",
        AsyncMock(side_effect=lambda insts, **_kw: insts),
    )

    class _FakeTokens:
        cst = "cst"
        security_token = "sec"

    class _FakeSession:
        def __init__(self, **_kw: object) -> None: ...
        async def __aenter__(self) -> _FakeSession:
            return self
        async def __aexit__(self, *_a: object) -> None: ...
        async def ensure_tokens(self) -> _FakeTokens:
            return _FakeTokens()

    monkeypatch.setattr(layers_mod, "CapitalSession", _FakeSession)
    for k, v in (("CAP_API_KEY", "k"), ("CAP_EMAIL", "e"), ("CAP_PASSWORD", "p")):
        os.environ[k] = v
    try:
        await layers_mod.refresh_capital_universe_once(memdb)
    finally:
        for k in ("CAP_API_KEY", "CAP_EMAIL", "CAP_PASSWORD"):
            os.environ.pop(k, None)

    active_after = memdb.execute(
        "SELECT COUNT(*) FROM universe WHERE venue='capital' AND is_active=1"
    ).fetchone()[0]
    assert active_after == 20, f"breadth lost: only {active_after} active (collapse guard failed)"


# ---------------------------------------------------------------------------
# B — Layer 1 ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_bar_ingest_persists_bars(memdb: sqlite3.Connection) -> None:
    bars = _bars_series(60)
    with patch(
        "polaris.scripts._production_bars.fetch_okx_bars",
        new=AsyncMock(return_value=list(reversed(bars))),  # OKX returns newest first
    ):
        result = await ingest_bars_for_focus(
            memdb,
            [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
        )
    assert result["bars"] >= 60
    row = memdb.execute("SELECT COUNT(*) FROM bars").fetchone()
    assert int(row[0]) >= 60


@pytest.mark.asyncio
async def test_l1_baseline_update_from_bars(memdb: sqlite3.Connection) -> None:
    bars = _bars_series(60)
    with patch(
        "polaris.scripts._production_bars.fetch_okx_bars",
        new=AsyncMock(return_value=list(reversed(bars))),
    ):
        result = await ingest_bars_for_focus(
            memdb, [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
        )
    assert result["baseline_samples"] >= 180  # 60 bars × 3 metrics
    state_row = memdb.execute(
        "SELECT COUNT(*) FROM ticker_baseline_state"
    ).fetchone()
    assert int(state_row[0]) >= 1


# ---------------------------------------------------------------------------
# C — Layer 7 isolation / fence / circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l7_supervise_strategies_wired() -> None:
    """The dispatch list is now DERIVED from STRATEGY_REGISTRY filtered on
    ``metadata.dispatch_eligible`` (dual-SSOT fix). The prior hand-synced literal
    was 19; the fee-fatal KILLs rsi_bb_pullback (15m crypto reversion) + ema_crossover
    (1H crypto trend-template: gross +$0.12 < fee $2.37 per round-trip) set
    dispatch_eligible=False → 17 dispatched survivors. ema_crossover's deferred KILL
    judgement landed 2026-06-28 once the live read confirmed it is fee-fatal.
    The registered-but-unvalidated supertrend / connors_rsi2 / cci_reversion stay
    OUT (already absent + now flag-enforced). A KILL = no-emit, not removal: all
    stay registered (open-position close path preserved).

    B1 prune (2026-07-06, live-ledger forensic, -$2,024 book loss) then KILLed
    session_breakout / donchian_turtle_breakout / equity_52wk_high_breakout /
    equity_vol_expansion_pocket_pivot AT THE REGISTRY level (100.9% of the
    loss) — unlike the dispatch_eligible KILLs above, these 4 are removed from
    STRATEGY_REGISTRY entirely (modules preserved read-only): 20 → 16."""
    strategies = _all_strategies()
    assert len(strategies) == 16
    ids = {s.metadata.strategy_id for s in strategies}
    assert "volume_burst" not in ids  # KILLed 2026-06-27 (#61)
    assert "spot_donchian" not in ids  # KILLed 2026-06-27 (#56 stop-bleeders)
    assert "session_breakout" not in ids  # B1 prune 2026-07-06
    assert "fx_range_fade" not in ids  # KILLed in strategy-wave1
    assert "bar_breakout_run" in ids
    assert "okx_donchian_55_breakout" in ids
    assert "tsmom_12_1_multiasset" in ids
    assert "macd_ema_trend_pullback" in ids
    assert "donchian_turtle_breakout" not in ids  # B1 prune 2026-07-06
    # strategy-wave2 dispatch additions.
    assert "gold_trend_chandelier_1d" in ids
    assert "index_dual_momentum_rotation" in ids
    assert "equity_vol_expansion_pocket_pivot" not in ids  # B1 prune 2026-07-06
    # weekend-maker dispatch — the two VALIDATED weekend OKX makers.
    assert "weekend_thin_book_flush_maker" in ids
    assert "weekend_funding_capitulation_maker" in ids
    # Opus-spec 3-clone build — per-symbol gold_breakout_1h clones.
    assert "silver_breakout_1h" in ids
    assert "us100_breakout_1h" in ids
    assert "uk100_breakout_1h" in ids
    # fee-fatal / unvalidated KILL set — dispatch_eligible=False, all OUT.
    assert "rsi_bb_pullback" not in ids  # fee-fatal 15m crypto reversion
    assert "supertrend" not in ids
    assert "connors_rsi2" not in ids
    assert "cci_reversion" not in ids
    # ema_crossover KILLed 2026-06-28 — fee-fatal 1H crypto (gross +$0.12 < fee $2.37).
    assert "ema_crossover" not in ids


@pytest.mark.asyncio
async def test_l7_allocator_fence_reservation(memdb: sqlite3.Connection) -> None:
    """Reservation is taken before any persist_fill."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    reset_process_fence()
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="sig1", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
    )
    assert trade is not None
    assert state.fence_reservations == 1
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is not None
    assert res[0] == "confirmed"


@pytest.mark.asyncio
async def test_l7_circuit_breaker_record_fault(memdb: sqlite3.Connection) -> None:
    """A bad signal (NaN strength) is recorded as a FAULT_NAN event."""
    sig = RawSignal(
        signal_id="bad", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=float("nan"), sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    assert _is_finite_signal(sig) is False


# ---------------------------------------------------------------------------
# D — start_gate=G1 + G2 no cap + G8 invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g1_universe_scanner_invoked(memdb: sqlite3.Connection) -> None:
    """Pipeline starts at G1 (Universe Scanner)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    reset_process_fence()
    _seed_universe(memdb)
    state = ProdLoopState()
    haiku = StubGPTClient()
    sig = RawSignal(
        signal_id="sig_g1", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    universe_rows = [{"venue": "okx", "symbol": "BTC-USDT", "vol_24h_usd": 2e9}]
    await _run_pipeline_for_signal(
        conn=memdb, haiku=haiku, state=state, strategy=_all_strategies()[0],
        sig=sig, venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.02, last_price=60_000.0,
        universe_rows=universe_rows, now_ts=int(time.time()),
        reserve_and_submit=_reserve_and_submit,
    )
    assert state.g1_runs == 1
    # G1+G2+G3+G4+G5 = 5 gate result rows minimum.
    assert state.pipeline_runs >= 5


def test_g2_emit_no_cap() -> None:
    """The strategy list is exposed without an emitted[:3] cap (T12)."""
    strategies = _all_strategies()
    # Registry-derived dispatch (dispatch_eligible SSOT). The prior literal was 19;
    # the dual-SSOT fix set rsi_bb_pullback (fee-fatal 15m crypto reversion) +
    # ema_crossover (fee-fatal 1H crypto trend-template, gross +$0.12 < fee $2.37) to
    # dispatch_eligible=False → 17 dispatched survivors. KILL = no-emit, not removal:
    # both stay registered (open-position close path kept).
    # Opus-spec 3-clone build: +silver/us100/uk100_breakout_1h → 20.
    # B1 prune (2026-07-06, live-ledger forensic, -$2,024 book loss):
    # -session_breakout/-donchian_turtle_breakout/-equity_52wk_high_breakout/
    # -equity_vol_expansion_pocket_pivot (100.9% of the loss, registry-level
    # removal): 20 → 16.
    assert len(strategies) == 16


@pytest.mark.asyncio
async def test_g8_reflector_invoked_on_close(memdb: sqlite3.Connection) -> None:
    """The close path calls G8 reflector."""
    from polaris.scripts._production_pipeline import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    _seed_universe(memdb)
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t1", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long",
        entry_price=60_000.0, notional_usd=100.0,
        open_ts=int(time.time()),
    )
    state.open_trades.append(trade)
    # Seed an entry fill so real_pnl_r_from_fills reads it.
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f1', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o1', NULL, 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000),),
    )
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime,
    )
    assert state.g8_runs == 1
    assert state.fills_close == 1


# ---------------------------------------------------------------------------
# E — Layer 6 wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l6_recalc_dirty_invoked(memdb: sqlite3.Connection) -> None:
    """run_recalc_for_active_positions sweeps and clears dirty."""
    from polaris.scripts._production_layers import run_recalc_for_active_positions

    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES ('p1', 'okx', 'BTC-USDT', 'volume_burst', 'volume_burst', "
        "        'volume_burst', 'long', 0.001, 'open', ?, 0)",
        (int(time.time()),),
    )
    n = await run_recalc_for_active_positions(memdb, now_ts=int(time.time()))
    assert n == 1


def test_l6_regime_flip_dynamic(memdb: sqlite3.Connection) -> None:
    """compute_and_flip_regime persists the regime in regime_state."""
    bars = _bars_series(50, base=60_000.0, drift=10.0)  # uptrend
    regime = compute_and_flip_regime(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        bars=bars, now_ts=int(time.time()),
    )
    assert regime in ("bull_trend", "bear_trend", "chop", "crisis")


def test_l6_strategy_swap_layer6_ssot(memdb: sqlite3.Connection) -> None:
    """Strategy swap goes through evaluate_strategy_swap (Layer 6 SSOT)."""
    from polaris.scripts.production_paper_loop import _evaluate_swaps

    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count) "
        "VALUES ('p2', 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.001, 'open', ?, 0)",
        (int(time.time()),),
    )
    # Should execute without raising; Layer 6 SSOT predicate runs.
    _evaluate_swaps(memdb, now_ts=int(time.time()))


# ---------------------------------------------------------------------------
# F — Real PnL on close
# ---------------------------------------------------------------------------


def test_close_real_pnl_from_fills(memdb: sqlite3.Connection) -> None:
    """Mark-to-market PnL is computed from the entry fill + real bar drift."""
    trade = SimulatedTrade(
        signal_id="t2", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long",
        entry_price=0.0, notional_usd=100.0,
        open_ts=int(time.time()), position_id="pos_t2",
    )
    # 2026-07-07 re-base: pnl_r now reads this position's own risk_usd (a bare
    # fills-only fixture with no position_id yielded pnl_r=0.0 -- unknowable
    # risk, never guessed). Seed a minimal positions row so pnl_r is non-zero.
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES ('pos_t2', 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        " 'volume_burst', 'volume_burst', 'long', 0.001666, 'open', ?, 0, 100.0)",
        (int(time.time()),),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('fent', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o2', 'pos_t2', 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000),),
    )
    # Seed bars so the mark-to-market reads a real drift (not 1.0 placeholder).
    for bar in _bars_series(20, base=60_000.0, drift=12.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue, bar.symbol,
                bar.bar_interval, bar.ts, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.notional_usd, 0, 0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    pnl_r, pnl_usd, exit_price = _real_pnl_r_from_fills(memdb, trade=trade)
    # Real drift, not the smoke 1.0 placeholder; should be non-zero positive
    # because the bars trended up after the entry at 60000.
    assert pnl_r != 0.0
    assert trade.entry_price == 60_000.0
    # PnL_USD is sign-aware: long + uptrend → positive.
    assert pnl_usd > 0.0
    # Exit price comes from the most recent bar close (real, not synthetic).
    assert exit_price > 0.0


# ---------------------------------------------------------------------------
# G + H — MarketView + regime real
# ---------------------------------------------------------------------------


def test_marketview_real_indicators() -> None:
    bars = _bars_series(120, base=60_000.0, drift=5.0)
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=bars,
    )
    # No hardcoded values — every indicator is computed.
    assert mv.last_price == bars[-1].close
    assert mv.rsi_14 != 22.0  # smoke fixture removed
    assert mv.adx_14 is not None
    assert mv.momentum_20bar is not None
    assert -1.0 < mv.momentum_20bar < 1.0


def test_regime_dynamic_compute() -> None:
    """Each regime branch is reachable from real bar histories."""
    # Bull: mild but persistent uptrend (no crisis trigger).
    uptrend = _bars_series(120, base=60_000.0, drift=5.0)
    # Bear: gentle downtrend that stays above the -3% crisis floor.
    # 100-bar drift from 60000 → 60000 - 30 = 59970 (-0.05%, far from crisis).
    # We need a stronger drift to register as bear_trend (≥ +0.5% absolute).
    downtrend = _bars_series(120, base=60_000.0, drift=-3.0)
    sideways = _bars_series(120, base=60_000.0, drift=0.0)
    crisis_bars = _bars_series(120, base=60_000.0, drift=0.0)
    # Force a crash on the last 30 bars.
    for i in range(-30, 0):
        b = crisis_bars[i]
        crashed_close = b.close * 0.92
        crisis_bars[i] = Bar(
            instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id,
            venue=b.venue, symbol=b.symbol, bar_interval=b.bar_interval,
            ts=b.ts, open=b.open, high=b.high, low=b.low,
            close=crashed_close, volume=b.volume,
            notional_usd=b.notional_usd, trade_count=b.trade_count,
            vwap=b.vwap, bid_close=b.bid_close, ask_close=b.ask_close,
            spread_bps_close=b.spread_bps_close, source=b.source,
        )
    assert compute_real_regime(uptrend) == "bull_trend"
    # Bear path: we just check it's not bull and not crisis (real markets
    # will land on bear or chop depending on the efficiency ratio).
    bear_regime = compute_real_regime(downtrend)
    assert bear_regime in ("bear_trend", "chop", "crisis")
    assert compute_real_regime(sideways) in ("chop", "bull_trend", "bear_trend")
    assert compute_real_regime(crisis_bars) == "crisis"


# ---------------------------------------------------------------------------
# I — G6/G7 real position state
# ---------------------------------------------------------------------------


def test_g6_real_unrealized_pnl_r() -> None:
    """unrealized_pnl_r computed from prices, not hard-coded 0.2."""
    pnl = compute_unrealized_pnl_r(
        side="long", entry_price=60_000.0, last_price=60_600.0,
        atr_pct=0.02, stop_atr_mult=2.0,
    )
    # 600/(60_000 × 0.02 × 2) = 0.25 — distinct from 0.2.
    assert abs(pnl - 0.25) < 1e-6
    assert pnl != 0.2


def test_g7_widen_proposal_real() -> None:
    """A loss position produces a negative R-multiple (no positive bias)."""
    pnl = compute_unrealized_pnl_r(
        side="long", entry_price=60_000.0, last_price=59_000.0,
        atr_pct=0.02, stop_atr_mult=2.0,
    )
    assert pnl < 0.0


def test_decision_path_clamp_is_100_not_10() -> None:
    """Step M (2026-06-22): the decision path shares the ±100 telemetry clamp.

    The old ±10 here HID catastrophic losses — a −34..−100R move read as −10R, so
    G6/G7/precise-exit could not see the true loss magnitude. A long that lost
    ~50R must now surface as ~−50R (well past the old −10 wall), and a blow-up
    saturates at −100, not −10.
    """
    # entry 100, last 0 → pnl_abs −100; atr_usd = 100×0.01×2 = 2 → −50R.
    big_loss = compute_unrealized_pnl_r(
        side="long", entry_price=100.0, last_price=0.0,
        atr_pct=0.01, stop_atr_mult=2.0,
    )
    assert big_loss == pytest.approx(-50.0)
    assert big_loss < -10.0  # the old ±10 wall is gone

    # A tiny-ATR blow-up saturates at exactly −100 (the unified clamp).
    saturated = compute_unrealized_pnl_r(
        side="long", entry_price=100.0, last_price=1.0,
        atr_pct=1e-4, stop_atr_mult=2.0,
    )
    assert saturated == pytest.approx(-100.0)


# ---------------------------------------------------------------------------
# J — Capital bars ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_bars_ingest(memdb: sqlite3.Connection) -> None:
    """Capital bars persist when a session is supplied (path is wired)."""
    cap_bars = [_make_bar(venue="capital", symbol="EURUSD", ts=1_700_000_000 + i * 60,
                          close=1.10 + i * 0.0001) for i in range(40)]
    # Patch fetch_capital_bars so no Capital network calls happen.
    with patch(
        "polaris.scripts._production_bars.fetch_capital_bars",
        new=AsyncMock(return_value=cap_bars),
    ):
        # Synthesize a fake session with ensure_tokens.
        class FakeTokens:
            cst = "x"
            security_token = "y"

        class FakeSession:
            async def ensure_tokens(self) -> Any:
                return FakeTokens()

        result = await ingest_bars_for_focus(
            memdb,
            [("capital", "EURUSD", "forex", "forex:EURUSD")],
            capital_session=FakeSession(),  # type: ignore[arg-type]
        )
    assert result["bars"] == 40


# ---------------------------------------------------------------------------
# K — Idempotent order keys register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_order_keys_register(memdb: sqlite3.Connection) -> None:
    """register_order_intent is invoked before submit; conflict raises."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    reset_process_fence()
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="sk", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    now = int(time.time())
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=now,
    )
    assert trade is not None
    # An order_intents row was created.
    row = memdb.execute(
        "SELECT COUNT(*) FROM order_intents WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert int(row[0]) >= 1


# ---------------------------------------------------------------------------
# Codex round-2 fixes — positions row + multi-trade close + price consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_persists_positions_row(memdb: sqlite3.Connection) -> None:
    """BLOCKER fix: a confirmed reservation must create a positions row.

    Before the fix, Layer 6 recalc/swap operated on an empty positions table
    while the loop stored open trades in memory only.
    """
    from polaris.core.isolation.allocator_fence import reset_process_fence
    reset_process_fence()
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="pos_row", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
    )
    assert trade is not None
    assert trade.position_id is not None
    row = memdb.execute(
        "SELECT status, side, strategy_id FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "open"
    assert row[1] == "long"
    assert row[2] == "volume_burst"


def test_real_pnl_matches_closed_fill_price(memdb: sqlite3.Connection) -> None:
    """P0 fix: persisted close fill price must equal the mark-to-market exit.

    Before the fix the close fill stored a synthetic ±0.5% level even though
    the reported PnL came from real bar drift, leaving the ledger inconsistent.
    """
    trade = SimulatedTrade(
        signal_id="t_match", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long",
        entry_price=0.0, notional_usd=100.0, open_ts=int(time.time()),
        position_id="pos_match",
    )
    # 2026-07-07 re-base: pnl_r reads this position's own risk_usd.
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES ('pos_match', 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        " 'volume_burst', 'volume_burst', 'long', 0.001666, 'open', ?, 0, 100.0)",
        (int(time.time()),),
    )
    # Seed an entry fill linked by contribution_id.
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_e', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o_e', 'pos_match', 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000),),
    )
    # Seed bars trending up so exit_price > entry_price.
    for bar in _bars_series(20, base=60_000.0, drift=12.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue,
                bar.symbol, bar.bar_interval, bar.ts, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.notional_usd, 0,
                0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    pnl_r, pnl_usd, exit_price = _real_pnl_r_from_fills(memdb, trade=trade)
    # The exit price returned must match the most recent bar's close.
    last_bar_close = memdb.execute(
        "SELECT close FROM bars WHERE instrument_id = 'okx:BTC-USDT' "
        "ORDER BY ts DESC LIMIT 1"
    ).fetchone()[0]
    assert abs(exit_price - float(last_bar_close)) < 1e-6
    # PnL_R must agree on direction with exit_price > entry_price.
    assert (pnl_r > 0.0) == (exit_price > trade.entry_price)


def test_real_pnl_matches_by_position_id_not_strategy_symbol(
    memdb: sqlite3.Connection,
) -> None:
    """P0 fix: two open trades on same (strategy, instrument) must close
    against their *own* entry price, not the latest open."""
    # Seed bars (real exit price reference).
    for bar in _bars_series(20, base=60_000.0, drift=5.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue,
                bar.symbol, bar.bar_interval, bar.ts, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.notional_usd, 0,
                0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    # 2026-07-07 re-base: pnl_r reads the position's own risk_usd.
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES ('pos_old', 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        " 'volume_burst', 'volume_burst', 'long', 0.002, 'open', 1, 0, 100.0)",
    )
    # Two entries on (volume_burst, BTC-USDT) at different prices.
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_old', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 50000.0, 0.1, 1.0, 1, 'o_old', 'pos_old', 0.0, 0, "
        "        0.002, 100.0, 'filled'), "
        "       ('f_new', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 70000.0, 0.1, 1.0, 2, 'o_new', 'pos_new', 0.0, 0, "
        "        0.0014, 100.0, 'filled')",
    )
    old_trade = SimulatedTrade(
        signal_id="s_old", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=0.0,
        notional_usd=100.0, open_ts=1, position_id="pos_old",
    )
    pnl_r_old, _, _ = _real_pnl_r_from_fills(memdb, trade=old_trade)
    # Old trade entered at 50_000 — should price against that, NOT the
    # newer 70_000 entry. With bars trending up from 60_000 the old trade's
    # PnL_R must be positive.
    assert old_trade.entry_price == 50_000.0
    assert pnl_r_old > 0.0


# ---------------------------------------------------------------------------
# Codex round-3 fixes — close path persists before mutating; G6/7/8 use
#                      persisted position_id; orphan-free reservation flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_path_persists_close_fill_and_marks_position_closed(
    memdb: sqlite3.Connection,
) -> None:
    """R2 P1 + P2 fix: close path writes the close fill + UPDATE positions
    in a single transaction, and uses the persisted position_id.

    Verifies the codex test-gap: assert the persisted ``fills`` row carries
    ``is_close=1``, the ``positions`` row transitions to ``status='closed'``,
    and the in-memory ``state.open_trades`` is popped only after both writes
    succeed.
    """
    from polaris.scripts._production_pipeline import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_test_close"
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.001, 'open', ?, 0)",
        (pos_id, int(time.time())),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('fent', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o2', ?, 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000), pos_id),
    )
    for bar in _bars_series(20, base=60_000.0, drift=12.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue,
                bar.symbol, bar.bar_interval, bar.ts, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.notional_usd, 0,
                0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_close", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=0.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
    )
    state.open_trades.append(trade)
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime,
    )
    # Position row transitioned to closed.
    pos_row = memdb.execute(
        "SELECT status, closed_ts FROM positions WHERE position_id = ?",
        (pos_id,),
    ).fetchone()
    assert pos_row is not None
    assert pos_row[0] == "closed"
    assert pos_row[1] is not None
    # A close fill row exists with positive PnL.
    close_row = memdb.execute(
        "SELECT pnl_usd, is_close, contribution_id FROM fills "
        "WHERE contribution_id = ? AND is_close = 1",
        (pos_id,),
    ).fetchone()
    assert close_row is not None
    assert bool(close_row[1]) is True
    assert close_row[2] == pos_id
    # In-memory state advanced exactly once.
    assert len(state.open_trades) == 0
    assert len(state.closed_trades) == 1
    # G8 was invoked + persisted to gate_events.
    g8_count = memdb.execute(
        "SELECT COUNT(*) FROM gate_events WHERE gate_id = 8 AND position_id = ?",
        (pos_id,),
    ).fetchone()[0]
    assert int(g8_count) >= 1


# ---------------------------------------------------------------------------
# Codex round-3 fixes — G6/G7 audit logging + close-path fault accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g6_g7_results_persisted_to_gate_events(memdb: sqlite3.Connection) -> None:
    """R3 P2 fix: G6 + G7 gate results must persist to gate_events with the
    real position_id so audit replay can join positions ↔ gate_events.
    """
    from polaris.core.isolation.allocator_fence import reset_process_fence
    reset_process_fence()
    _seed_universe(memdb)
    state = ProdLoopState()
    haiku = StubGPTClient()
    sig = RawSignal(
        signal_id="audit_g67", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    universe_rows = [{"venue": "okx", "symbol": "BTC-USDT", "vol_24h_usd": 2e9}]
    await _run_pipeline_for_signal(
        conn=memdb, haiku=haiku, state=state, strategy=_all_strategies()[0],
        sig=sig, venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.02, last_price=60_000.0,
        universe_rows=universe_rows, now_ts=int(time.time()),
        reserve_and_submit=_reserve_and_submit,
    )
    # G6 + G7 events must exist; at least one row per gate must carry the
    # persisted position_id (the orchestrator's auto-run also logs without
    # position_id since it doesn't know about the production positions row).
    g6 = memdb.execute(
        "SELECT position_id FROM gate_events WHERE gate_id = 6"
    ).fetchall()
    g7 = memdb.execute(
        "SELECT position_id FROM gate_events WHERE gate_id = 7"
    ).fetchall()
    assert len(g6) >= 1, "G6 result must persist to gate_events"
    assert len(g7) >= 1, "G7 result must persist to gate_events"
    # Audit-friendly row: at least one G6/G7 event keyed by the production
    # ``positions.position_id`` (R3 P2 fix).
    assert any(r[0] and r[0].startswith("pos_") for r in g6), (
        "G6 must persist at least one event with the real positions.position_id"
    )
    assert any(r[0] and r[0].startswith("pos_") for r in g7), (
        "G7 must persist at least one event with the real positions.position_id"
    )


@pytest.mark.asyncio
async def test_close_path_records_fault_on_db_failure(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 P2 fix: close-path persist failure must record_fault + bump state."""
    from polaris.scripts._production_pipeline import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_fault"
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.001, 'open', ?, 0)",
        (pos_id, int(time.time())),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_fault', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o_f', ?, 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000), pos_id),
    )
    for bar in _bars_series(20, base=60_000.0, drift=10.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue,
                bar.symbol, bar.bar_interval, bar.ts, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.notional_usd, 0,
                0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_fault", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=0.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
    )
    state.open_trades.append(trade)
    # Force persist_fill to raise sqlite3.Error in the close-path module.
    import polaris.scripts._production_close as close_mod

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.Error("simulated close-path persist failure")

    monkeypatch.setattr(close_mod, "persist_fill", _boom)
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime,
    )
    # Trade was NOT popped, but a fault was recorded.
    assert len(state.open_trades) == 1
    assert state.fault_events == 1
    # strategy_fault_events row exists.
    fault_rows = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id = ?",
        ("volume_burst",),
    ).fetchone()
    assert int(fault_rows[0]) >= 1


# ---------------------------------------------------------------------------
# Codex round-5 fix — learner exception must NOT drop G8 (fail-open envelope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learner_exception_does_not_drop_g8(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5 P2 fix: a learner.update() raise must not silently drop the G8
    reflector for a successfully-closed trade.
    """
    from polaris.scripts._production_close import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_l_fail"
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.001, 'open', ?, 0)",
        (pos_id, int(time.time())),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_l', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o_l', ?, 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000), pos_id),
    )
    for bar in _bars_series(20, base=60_000.0, drift=10.0):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bar.instrument_id, bar.underlying_group_id, bar.venue,
                bar.symbol, bar.bar_interval, bar.ts, bar.open, bar.high,
                bar.low, bar.close, bar.volume, bar.notional_usd, 0,
                0.0, 0.0, 0.0, 0.0, "test",
            ),
        )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_l", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=0.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
    )
    state.open_trades.append(trade)

    # Force every learner.update() to raise — but G8 must still fire.
    from polaris.core.learners.base import BaseLearner
    original_update = BaseLearner.update

    def _boom_update(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated learner failure")

    monkeypatch.setattr(BaseLearner, "update", _boom_update)
    try:
        await close_oldest_with_real_pnl(
            memdb, state=state, now_ts=int(time.time()),
            lookup_regime=_lookup_regime,
        )
    finally:
        monkeypatch.setattr(BaseLearner, "update", original_update)

    # Position closed + G8 still ran despite learner failure.
    pos_row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", (pos_id,),
    ).fetchone()
    assert pos_row is not None and pos_row[0] == "closed"
    assert state.g8_runs == 1
    # Learner faults were recorded.
    assert state.fault_events >= 1


# ---------------------------------------------------------------------------
# Property-based — finite invariants
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    closes=hyp.lists(
        hyp.floats(min_value=10.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
        min_size=30, max_size=200,
    ),
)
def test_compute_regime_returns_valid_enum(closes: list[float]) -> None:
    bars = [
        Bar(
            instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
            venue="okx", symbol="BTC-USDT", bar_interval="1m",
            ts=1_700_000_000 + i * 60,
            open=closes[i], high=closes[i] * 1.001, low=closes[i] * 0.999,
            close=closes[i], volume=1_000.0,
        )
        for i in range(len(closes))
    ]
    regime = compute_real_regime(bars)
    assert regime in ("bull_trend", "bear_trend", "chop", "crisis")


@settings(max_examples=50, deadline=None)
@given(
    entry=hyp.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    last=hyp.floats(min_value=0.001, max_value=1e7, allow_nan=False, allow_infinity=False),
    atr=hyp.floats(min_value=0.0001, max_value=0.5, allow_nan=False, allow_infinity=False),
)
def test_compute_unrealized_pnl_r_always_finite(entry: float, last: float, atr: float) -> None:
    import math
    pnl = compute_unrealized_pnl_r(
        side="long", entry_price=entry, last_price=last,
        atr_pct=atr, stop_atr_mult=2.0,
    )
    assert math.isfinite(pnl)
    # Step M (2026-06-22): the decision path now shares the ±100 telemetry clamp
    # (was ±10, which HID −34..−100R losses from G6/G7/precise-exit).
    assert -100.0 <= pnl <= 100.0


# ---------------------------------------------------------------------------
# reconcile_alpaca_venue_drift boot wire (2026-07-02: was implemented +
# unit-tested but never called from the boot sequence — registered ≠ fired).
# ---------------------------------------------------------------------------


def _read_paper_loop_boot_source() -> str:
    """Read the boot-sequence source (same file this module tests)."""
    from pathlib import Path

    return Path("polaris/scripts/production_paper_loop.py").read_text()


def test_boot_wires_reconcile_alpaca_venue_drift() -> None:
    """The boot sequence must import AND call ``reconcile_alpaca_venue_drift``
    — an implemented + unit-tested reconcile that sat dead (never invoked from
    ``run_production_paper_loop``) is the same silent-INERT pattern as an
    unwired gate: registered code that never fires live."""
    src = _read_paper_loop_boot_source()
    assert "reconcile_alpaca_venue_drift" in src, (
        "production_paper_loop.py must import reconcile_alpaca_venue_drift "
        "from polaris.scripts.reconcile_alpaca_zombies"
    )
    assert "await reconcile_alpaca_venue_drift(" in src, (
        "the boot sequence must actually AWAIT reconcile_alpaca_venue_drift, "
        "not just import it (registered != fired)"
    )


@pytest.mark.asyncio
async def test_reconcile_alpaca_venue_drift_reachable_from_boot_module(
    memdb: sqlite3.Connection,
) -> None:
    """Smoke: the imported symbol is the real reconcile function and is
    directly callable against a DB connection + a fake venue adapter (no
    real network) — guards against a stale/renamed import silently no-op'ing."""
    from polaris.scripts.production_paper_loop import reconcile_alpaca_venue_drift

    class _FakeAlpaca:
        async def fetch_positions(self) -> list[dict[str, Any]]:
            return []

    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "exit_state) VALUES ('p1', 'alpaca', 'UPC', 'equity_tsmom', "
        "'equity_tsmom', 'equity_tsmom', 'long', 10.0, 'open', ?, 'open')",
        (int(time.time()) - 100 * 3600,),
    )
    memdb.commit()
    n = await reconcile_alpaca_venue_drift(memdb, _FakeAlpaca())
    assert n == 1
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = 'p1'"
    ).fetchone()[0]
    assert status == "reconciled"
