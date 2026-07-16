"""Day 9 F2 — Live recalc loop tests (G6/G7 per-tick GPT invocation).

Spec source:
- vault/30_components/layer-6-live-recalc.md (Q1 5s cadence + dirty triggers)
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6/G7 P1)

Verifies:
- 5s cadence: ``recalc_active_positions`` is invoked once per ``_run_tick``
  (the production loop calls it after ``run_recalc_for_active_positions``).
- G6 fires per active position (model_used reflects phase).
- G6 EXIT_NOW triggers a *specific* close (FIFO oldest pop NOT used).
- SWAP_STRATEGY hands off to Layer 6 SSOT ``evaluate_strategy_swap``.
- close path matches by ``contribution_id`` (specific position_id).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

import polaris.scripts._production_recalc as _production_recalc
from polaris.core.lineage import record_segment_open
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import (
    find_open_trade_by_position_id,
    load_active_position_rows,
    recalc_active_positions,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _MockGPTClient:
    """Records every call + returns a queued response."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ['{"decision":"HOLD","reason":"x"}']
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)
                idx = min(len(outer.calls) - 1, len(outer.responses) - 1)
                text = outer.responses[idx]

                class _Block:
                    pass

                _Block.text = text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _seed_position_and_fill(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    side: str = "long",
    entry_price: float = 80_000.0,
    last_price: float = 80_400.0,
    strategy: str = "vb",
    tight_bars: bool = False,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0)",
        (
            position_id, venue, symbol, "crypto:BTC", strategy, strategy,
            strategy, side, 0.001, NOW,
        ),
    )
    fill_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'filled')",
        (
            fill_id, NOW * 1000, strategy, f"{venue}:{symbol}", venue, side,
            0.001, entry_price, 80.0, 0.05, 1.0, 0.0,
            position_id, uuid.uuid4().hex,
        ),
    )
    # Provide bars so live recalc sees a real last_price.
    # [P0-5] Anchored so the newest bar lands AT opened_ts (NOW) — pre-entry
    # bars are now excluded from load_active_position_rows's window.
    instrument_id = f"{venue}:{symbol}"
    for i in range(20):
        ts = NOW - (19 - i) * 60
        if tight_bars:
            # Tight 1-unit range floors ATR (atr_pct → 1e-4), so a price gain
            # vs entry yields a large +pnl_r (deterministic ADJUST_EXIT band)
            # that the FSM precise-exit keeps open (protected, not closed).
            high, low, close = last_price + 1.0, last_price - 1.0, last_price
        else:
            high, low, close = last_price + 50.0, entry_price - 50.0, last_price
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rest')",
            (
                instrument_id, "crypto:BTC", venue, symbol, ts,
                close, high, low,
                close, 100.0, 100.0 * close, 1,
                close, close, close, 1.0,
            ),
        )


def _trade_for(position_id: str, venue: str = "okx", symbol: str = "BTC-USDT") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex,
        venue=venue,
        symbol=symbol,
        strategy_id="vb",
        side="long",
        entry_price=80_000.0,
        notional_usd=80.0,
        open_ts=NOW,
        position_id=position_id,
        correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _lookup_regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


# ---------------------------------------------------------------------------
# Active position loader
# ---------------------------------------------------------------------------


def test_load_active_position_rows(memdb: sqlite3.Connection) -> None:
    _seed_position_and_fill(memdb, position_id="pos-1")
    rows = load_active_position_rows(memdb)
    assert len(rows) == 1
    pos = rows[0]
    assert pos["position_id"] == "pos-1"
    assert pos["entry_price"] == pytest.approx(80_000.0)
    assert pos["last_price"] > 0.0
    assert pos["atr_pct"] >= 0.0


# ---------------------------------------------------------------------------
# G6 called per active position + 5s cadence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g6_called_per_active_position(memdb: sqlite3.Connection) -> None:
    """G6 runs per active position — deterministic (ai_conductor P3, no GPT)."""
    for pid in ("pos-A", "pos-B", "pos-C"):
        _seed_position_and_fill(memdb, position_id=pid)
    state = ProdLoopState()
    state.open_trades = [_trade_for(p) for p in ("pos-A", "pos-B", "pos-C")]
    haiku = _MockGPTClient(responses=['{"decision":"EXIT_NOW","reason":"x"}'] * 5)
    n = await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert n == 3
    # G6 ran once per position, deterministically.
    assert state.recalc_g6_calls == 3
    # These are small winners → G6 HOLD → no chain to G7, so NO GPT call is
    # made for G6 at all (the per-position GPT branch was removed).
    assert haiku.calls == []
    # GPT EXIT_NOW was ignored — all three remain open.
    open_n = memdb.execute(
        "SELECT COUNT(*) FROM positions WHERE status = 'open'"
    ).fetchone()[0]
    assert open_n == 3


@pytest.mark.asyncio
async def test_recalc_5s_cadence_log_each_tick(memdb: sqlite3.Connection) -> None:
    """Simulating two consecutive ticks (5s apart) → 2× the G6 calls."""
    _seed_position_and_fill(memdb, position_id="pos-cadence")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-cadence")]
    haiku = _MockGPTClient(responses=['{"decision":"HOLD"}'] * 4)
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW + 5, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert state.recalc_g6_calls == 2


@pytest.mark.asyncio
async def test_recalc_hoists_dirty_marks_into_single_batch_flush(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """writer-migration-completion design #4: the per-position dirty mark is
    hoisted OUT of the ``_evaluate_position`` loop — ``mark_positions_dirty``
    fires exactly ONCE per sweep (collapses N lock acquisitions into 1), and
    every active position still lands in ``position_live_recalc_state``
    (dirty visibility to the async recalc sweep unchanged)."""
    pids = ("pos-A", "pos-B", "pos-C")
    for pid in pids:
        _seed_position_and_fill(memdb, position_id=pid, symbol=f"{pid}-USDT")
    state = ProdLoopState()
    state.open_trades = [_trade_for(p, symbol=f"{p}-USDT") for p in pids]

    calls: list[list[tuple[str, str, int]]] = []
    real_mark_positions_dirty = _production_recalc.mark_positions_dirty

    def _spy(conn: sqlite3.Connection, entries: list[tuple[str, str, int]]) -> None:
        calls.append(list(entries))
        real_mark_positions_dirty(conn, entries)

    monkeypatch.setattr(_production_recalc, "mark_positions_dirty", _spy)

    n = await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert n == 3
    assert len(calls) == 1, "expected exactly ONE batch flush, not per-position calls"
    flushed_ids = {entry[0] for entry in calls[0]}
    assert flushed_ids == set(pids)

    # Dirty rows landed for all 3 positions (read-your-writes into the sweep).
    dirty_ids = {r[0] for r in memdb.execute(
        "SELECT position_id FROM position_live_recalc_state"
    ).fetchall()}
    assert dirty_ids == set(pids)


@pytest.mark.asyncio
async def test_recalc_batch_dirty_flush_failure_does_not_block_sweep(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The docstring's existing contract — 'an exception ... does not block
    the sweep' — must still hold for the hoisted batch flush: a raising
    ``mark_positions_dirty`` degrades (counted via ``fault_events``), it does
    NOT abort G6/G7 evaluation for every position in the cycle."""
    pids = ("pos-A", "pos-B")
    for pid in pids:
        _seed_position_and_fill(memdb, position_id=pid, symbol=f"{pid}-USDT")
    state = ProdLoopState()
    state.open_trades = [_trade_for(p, symbol=f"{p}-USDT") for p in pids]

    def _raising(conn: sqlite3.Connection, entries: list[tuple[str, str, int]]) -> None:
        raise sqlite3.OperationalError("simulated batch flush failure")

    monkeypatch.setattr(_production_recalc, "mark_positions_dirty", _raising)

    n = await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert n == 2  # sweep still evaluated both positions
    assert state.fault_events >= 1


# ---------------------------------------------------------------------------
# G6 EXIT_NOW triggers SPECIFIC close (not FIFO)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_now_triggers_specific_close(memdb: sqlite3.Connection) -> None:
    """A stop-loss close hits the losing position specifically (not FIFO oldest).

    ai_conductor P3 (2026-05-30): G6 GPT is removed; precise stop closes are
    owned by the FSM precise-exit (the deterministic G6 -1.0R rail is a backstop
    behind it). Seed pos-A as a small winner and pos-B as a hard loser — only
    pos-B closes, regardless of state.open_trades order.
    """
    # Distinct symbols so each position reads its own bar series (bars key on
    # instrument_id = venue:symbol — same symbol would share one series).
    _seed_position_and_fill(
        memdb, position_id="pos-A", symbol="BTC-USDT",
        last_price=80_400.0, tight_bars=True,
    )
    # pos-B: price below entry → pnl_r << -1.0 → FSM stop close.
    _seed_position_and_fill(
        memdb, position_id="pos-B", symbol="ETH-USDT",
        last_price=79_000.0, tight_bars=True,
    )
    state = ProdLoopState()
    state.open_trades = [
        _trade_for("pos-A", symbol="BTC-USDT"),
        _trade_for("pos-B", symbol="ETH-USDT"),
    ]
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    rows = memdb.execute(
        "SELECT position_id, status FROM positions ORDER BY position_id"
    ).fetchall()
    closed_ids = [r[0] for r in rows if r[1] == "closed"]
    open_ids = [r[0] for r in rows if r[1] == "open"]
    # Exactly one close — the loser, specifically (not the FIFO-oldest pos-A).
    assert closed_ids == ["pos-B"]
    assert open_ids == ["pos-A"]
    # And the surviving open trade in memory matches the surviving DB row.
    assert len(state.open_trades) == 1
    assert state.open_trades[0].position_id == "pos-A"


@pytest.mark.asyncio
async def test_g6_stop_hit_names_lineage_reason(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6's own -1.0R ``stop_hit`` rail (the backstop BEHIND the FSM precise-exit
    — see ``position_monitor.py``) closes the position and names its trigger in
    the lineage ``exit_reason`` (P2-12), not the 'exit' fallback.

    ``run_precise_exit`` is stubbed to no-op (returns False / does not close)
    so this test isolates G6's own EXIT_NOW branch from the FSM ATR-trail
    stop that normally fires first in a real tick (see
    ``test_exit_now_triggers_specific_close``). Observability only — no
    close-path behaviour change.
    """
    async def _no_op_precise_exit(**kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "polaris.scripts._production_recalc.run_precise_exit",
        _no_op_precise_exit,
    )
    _seed_position_and_fill(
        memdb, position_id="pos-stophit", symbol="ETH-USDT",
        last_price=79_000.0, tight_bars=True,
    )
    record_segment_open(
        memdb, position_id="pos-stophit", trade_id="pos-stophit", venue="okx",
        ticker="ETH-USDT", strategy_id="vb", regime="bull_trend", entry_ts=NOW,
    )
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-stophit", symbol="ETH-USDT")]
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", ("pos-stophit",),
    ).fetchone()[0]
    assert status == "closed"
    exit_reason = memdb.execute(
        "SELECT exit_reason FROM position_strategy_segments WHERE position_id = ?",
        ("pos-stophit",),
    ).fetchone()[0]
    assert exit_reason == "g6_stop_hit"


# ---------------------------------------------------------------------------
# Close path specific contribution_id (FIFO 폐기)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_path_specific_contribution_id(memdb: sqlite3.Connection) -> None:
    _seed_position_and_fill(memdb, position_id="pos-1")
    _seed_position_and_fill(memdb, position_id="pos-2")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-1"), _trade_for("pos-2")]
    ok = await close_specific_position(
        memdb, state=state, position_id="pos-2", now_ts=NOW,
        lookup_regime=_lookup_regime_stub, gpt_client=None, phase="P0",
    )
    assert ok is True
    # pos-2 is the one closed; pos-1 still open.
    statuses = dict(memdb.execute(
        "SELECT position_id, status FROM positions"
    ).fetchall())
    assert statuses["pos-1"] == "open"
    assert statuses["pos-2"] == "closed"
    assert len(state.open_trades) == 1
    assert state.open_trades[0].position_id == "pos-1"


@pytest.mark.asyncio
async def test_close_specific_position_unknown_returns_false(
    memdb: sqlite3.Connection,
) -> None:
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-1")]
    ok = await close_specific_position(
        memdb, state=state, position_id="does-not-exist", now_ts=NOW,
        lookup_regime=_lookup_regime_stub, gpt_client=None, phase="P0",
    )
    assert ok is False
    assert len(state.open_trades) == 1


# ---------------------------------------------------------------------------
# SWAP_STRATEGY → Layer 6 SSOT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swap_strategy_layer6_ssot_called(memdb: sqlite3.Connection) -> None:
    """G6 SWAP_STRATEGY result must trigger Layer 6 SSOT evaluator (no exception)."""
    _seed_position_and_fill(memdb, position_id="pos-swap")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-swap")]
    # G6 returns SWAP_STRATEGY but without a swap_candidate — fast-path
    # rejects to HOLD; recalc loop emits SWAP only if Q8 fast-path matches.
    # We simulate by injecting candidate via raw GPT JSON — recalc payload
    # has no candidate so this becomes HOLD (Q8 invariant). Test the
    # absence of crash + clean fallback.
    haiku = _MockGPTClient(responses=[
        '{"decision":"SWAP_STRATEGY","reason":"x"}',
    ])
    n = await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert n == 1
    # No exception raised; counters consistent.
    assert state.fault_events == 0


# ---------------------------------------------------------------------------
# G6 ADJUST_EXIT chains G7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g6_adjust_exit_chains_g7(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # W3 cutover adaptation (NOT a behavior change): pins the LEGACY GPT
    # path under POLARIS_AI_FREE=0; flag=1 is covered by test_ai_free_cutover.py.
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    """A strong winner → deterministic G6 ADJUST_EXIT → chains to G7.

    ai_conductor P3 (2026-05-30): G6 is deterministic (pnl_r > 0.7R → ADJUST_EXIT),
    so the tight-bar winner (pnl_r ≈ +10R, FSM keeps it open) flows G6 → G7.
    G7 (adaptive exit) still uses GPT at P1, so exactly ONE GPT call is made
    (G7 only — G6 never calls GPT).
    """
    _seed_position_and_fill(
        memdb, position_id="pos-adj", last_price=80_400.0, tight_bars=True,
    )
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-adj")]
    haiku = _MockGPTClient(responses=[
        '{"decision":"WIDEN","reason":"extend stop","new_exit_atr":2.5}',  # G7
    ])
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert state.recalc_g6_calls == 1
    assert state.recalc_g7_calls == 1
    # Exactly one GPT call — G7 only (G6 deterministic).
    assert len(haiku.calls) == 1


@pytest.mark.asyncio
async def test_g7_exit_now_closes_and_names_lineage_reason(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6 ADJUST_EXIT chains to G7; G7 EXIT_NOW closes the position specifically
    (P2-12: the close names 'g7_exit_now' in the lineage exit_reason, not the
    'exit' fallback). Observability only — no close-path behaviour change.
    """
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    _seed_position_and_fill(
        memdb, position_id="pos-g7exit", last_price=80_400.0, tight_bars=True,
    )
    record_segment_open(
        memdb, position_id="pos-g7exit", trade_id="pos-g7exit", venue="okx",
        ticker="BTC-USDT", strategy_id="vb", regime="bull_trend", entry_ts=NOW,
    )
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-g7exit")]
    haiku = _MockGPTClient(responses=[
        '{"decision":"EXIT_NOW","reason":"reversal"}',  # G7
    ])
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", ("pos-g7exit",),
    ).fetchone()[0]
    assert status == "closed"
    exit_reason = memdb.execute(
        "SELECT exit_reason FROM position_strategy_segments WHERE position_id = ?",
        ("pos-g7exit",),
    ).fetchone()[0]
    assert exit_reason == "g7_exit_now"


# ---------------------------------------------------------------------------
# find_open_trade_by_position_id helper
# ---------------------------------------------------------------------------


def test_find_open_trade_by_position_id() -> None:
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-1"), _trade_for("pos-2")]
    found = find_open_trade_by_position_id(state, "pos-2")
    assert found is not None
    assert found.position_id == "pos-2"
    assert find_open_trade_by_position_id(state, "missing") is None


# ---------------------------------------------------------------------------
# Phase=P0 still runs (deterministic path, no GPT call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalc_phase_p0_no_gpt_calls(memdb: sqlite3.Connection) -> None:
    _seed_position_and_fill(memdb, position_id="pos-p0")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-p0")]
    haiku = _MockGPTClient(responses=['{"decision":"HOLD"}'])
    n = await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert n == 1
    # Phase=P0 → G6 client is None → no GPT call.
    assert haiku.calls == []
    assert state.recalc_g6_calls == 1


# ---------------------------------------------------------------------------
# storage-split (round 4 MED fix): the exit maturity gate's
# native_bars_seen_since call must read state.md_conn, not the trading conn.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalc_native_bars_seen_reads_md_conn(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bars is marketdata-domain — a trading-conn read sees a permanently
    empty table post-split, so the maturity gate always saw 0 bars (permanent
    suppression). Confirms the call now routes to ``state.md_conn``."""
    from polaris.storage.schema import ALL_DDL

    _seed_position_and_fill(memdb, position_id="pos-md")
    md_conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        md_conn.execute(stmt)
    state = ProdLoopState(md_conn=md_conn)
    state.open_trades = [_trade_for("pos-md")]

    from polaris.scripts._production_atr import native_bars_seen_since as real_fn

    captured: dict[str, object] = {}

    def _spy(conn: sqlite3.Connection, **kwargs: object) -> int:
        captured["conn"] = conn
        return real_fn(conn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_production_recalc, "native_bars_seen_since", _spy)
    haiku = _MockGPTClient(responses=['{"decision":"HOLD"}'])
    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P0",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )
    assert captured.get("conn") is md_conn
    md_conn.close()
