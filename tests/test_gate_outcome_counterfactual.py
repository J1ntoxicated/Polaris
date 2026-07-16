"""Gate→outcome instrumentation (BUILD, behavior 0) — G3 cohort tests.

DEMO/PAPER only — virtual funds; aggressive bias preserved (measurement only,
no gate/sizing/exit decision is touched). P2a group A: Gate 4 is abolished as
a decision step (folded into G3) — the ``gate_id``/``reason`` this module
pins are G3's own now (formerly "g3g4_pass" -> "g3_pass"). Pins:

* KILL counterfactual rows are recorded ONLY for a G3 KILL (mark / same-unit
  cost_r / NULL forward marks); PASS rows share the same estimator.
* The forward-mark sweep resolves expired horizons from already-ingested 1m
  bars (long/short sign, first-bar-after-gap semantics, idempotent re-run,
  LIMIT batching, unresolvable terminal stamp).
* A successful open backfills the run's pre-position gate_events rows.
* The final full close stamps positions.pnl_r (partial / reconciled stay NULL).
* v_g34_cohort_outcomes yields the fee-adj cohort comparison in one SELECT.
* Schema migration is idempotent + upgrades legacy DBs.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

import pytest

import polaris.scripts._production_run_signal as run_signal_mod
from polaris.core.economics.fees import real_fee_usd
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GATE_SIGNAL_VALIDATOR,
    GateDecision,
    GateResult,
    SignalLifecycle,
)
from polaris.scripts._production_counterfactual import sweep_forward_marks
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.backfill_gate_outcome_links import (
    backfill_gate_event_links,
    backfill_positions_pnl_r,
)
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.schema import init_db
from polaris.strategies import RawSignal

# spot_donchian un-registered 2026-06-27 (#56 stop-bleeders KILL) — module
# preserved read-only; used here as a generic strategy vehicle, import direct.
from polaris.strategies.spot_donchian import SpotDonchianStrategy

NOW = 1_780_000_000


def _sig(side: Literal["long", "short"] = "long") -> RawSignal:
    return RawSignal(
        signal_id=f"sig-{uuid.uuid4().hex[:8]}", strategy_id="tsmom",
        symbol="BTC-USDT", side=side, strength=0.9, sizing_hint=0.5,
        ttl_bars=24, thesis_tag="t", correlation_group="g", created_at_bar=100,
    )


class _ScriptedOrch:
    """Fake orchestrator: scripted results + the ctx mutation the real one does."""

    last_run_id: str = ""

    def __init__(
        self, *, results: list[GateResult], kill_gate: int | None = None,
        log_event_conn: sqlite3.Connection | None = None,
        **_: object,
    ) -> None:
        self._results = results
        self._kill_gate = kill_gate
        self._conn = log_event_conn

    async def run(self, ctx: object, *, start_gate: int) -> list[GateResult]:
        _ScriptedOrch.last_run_id = getattr(ctx, "run_id", "")
        if self._conn is not None:
            # Mimic the real orchestrator's pre-position gate_events rows.
            for gate_id in (3, 4):
                self._conn.execute(
                    "INSERT INTO gate_events (event_id, run_id, signal_id, "
                    "position_id, gate_id, phase, decision, created_ts) "
                    "VALUES (?, ?, ?, NULL, ?, 'success', 'PASS', ?)",
                    (uuid.uuid4().hex, getattr(ctx, "run_id", ""),
                     getattr(ctx, "signal_id", ""), gate_id, NOW),
                )
        if self._kill_gate is not None:
            ctx.gate_id = self._kill_gate  # type: ignore[attr-defined]
            ctx.state = SignalLifecycle.KILLED  # type: ignore[attr-defined]
        return self._results


def _kill_result(reason: str = "validator_kill") -> GateResult:
    return GateResult(
        decision=GateDecision.KILL, next_gate=None,
        payload={"reason": reason}, model_used="gpt",
    )


def _proceed_result() -> GateResult:
    return GateResult(
        decision=GateDecision.PROCEED, next_gate=GATE_ENTRY_SIZER, payload={},
    )


async def _run_pipeline(
    conn: sqlite3.Connection,
    state: ProdLoopState,
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[GateResult],
    kill_gate: int | None,
    sig: RawSignal | None = None,
    trade: SimulatedTrade | None = None,
    log_gate_rows: bool = False,
) -> None:
    def _orch_factory(**kwargs: object) -> _ScriptedOrch:
        return _ScriptedOrch(
            results=results, kill_gate=kill_gate,
            log_event_conn=conn if log_gate_rows else None,
        )

    async def _fake_gate(_ctx: object, **_: object) -> GateResult:
        return GateResult(decision=GateDecision.HOLD, next_gate=None)

    async def _reserve_and_submit(**_: object) -> SimulatedTrade | None:
        return trade

    monkeypatch.setattr(run_signal_mod, "GateOrchestrator", _orch_factory)
    monkeypatch.setattr(run_signal_mod, "position_monitor_gate", _fake_gate)
    monkeypatch.setattr(run_signal_mod, "adaptive_exit_gate", _fake_gate)
    await run_signal_mod.run_pipeline_for_signal(
        conn=conn, haiku=None, state=state, strategy=SpotDonchianStrategy(),
        sig=sig or _sig(), venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.01, last_price=100.0, universe_rows=[], now_ts=NOW,
        reserve_and_submit=_reserve_and_submit, phase="P0",
    )


def _cf_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM gate_kill_counterfactuals").fetchall()


# ---------------------------------------------------------------------------
# 1 — KILL counterfactual row recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g3_kill_records_counterfactual_row(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[_kill_result()], kill_gate=GATE_SIGNAL_VALIDATOR,
    )
    rows = _cf_rows(memdb)
    assert len(rows) == 1
    row = rows[0]
    assert row["gate_id"] == 3
    assert row["decision"] == "KILL"
    assert row["reason"] == "validator_kill"
    assert row["model_used"] == "gpt"
    assert row["side"] == "long"
    assert row["regime"] == "bull_trend"
    # Mark = strategy-tf bar close (no quote writer) + source label.
    assert row["mark_price"] == pytest.approx(100.0)
    assert row["mark_source"] == "bar_close:1H"
    # atr_usd mirrors the close-path R unit: mark × atr_pct × 2.
    assert row["atr_usd"] == pytest.approx(100.0 * 0.01 * 2.0)
    # cost_r = 2 real fee legs on a per-unit notional / per-unit atr_usd —
    # dimensionally identical to fwd_r (price-level independent).
    expected_cost = 2.0 * real_fee_usd("okx", 100.0) / (100.0 * 0.01 * 2.0)
    assert row["cost_r"] == pytest.approx(expected_cost)
    # 2 legs × 10bps = 0.002 of mark, over atr_pct×2 = 0.02 → 0.1 R.
    assert expected_cost == pytest.approx(0.002 / 0.02)
    # Forward marks unresolved at insert.
    for col in ("fwd_close_1h", "fwd_r_1h", "fwd_close_4h", "fwd_r_4h",
                "fwd_close_24h", "fwd_r_24h"):
        assert row[col] is None
    assert row["unresolvable"] == 0


@pytest.mark.asyncio
async def test_g5_kill_records_nothing(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[_kill_result("sizing_zero")], kill_gate=GATE_ENTRY_SIZER,
    )
    assert _cf_rows(memdb) == []


@pytest.mark.asyncio
async def test_g3_pass_then_g5_kill_records_pass_row(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocker R1: a G5 cap-kill AFTER a G3 PASS still records PASS.

    P2a group A: G4 is abolished as a decision step (G3 wires directly to
    G5) — realistic run shape is now [PASS(G1), PASS(G2), PASS(G3),
    KILL(G5)] + ctx KILLED at G5. G3 passed this signal — dropping it would
    systematically exclude cap-bound signals from the PASS cohort only
    (selection bias). "Cleared G3" is identified positionally (results[2]),
    since the orchestrator walks G1->G2->G3->G5 strictly sequentially.
    """
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[
            GateResult(decision=GateDecision.PASS, next_gate=2),
            GateResult(decision=GateDecision.PASS, next_gate=3),
            GateResult(decision=GateDecision.PASS, next_gate=GATE_ENTRY_SIZER),
            _kill_result("sizing_zero"),
        ],
        kill_gate=GATE_ENTRY_SIZER,
    )
    rows = _cf_rows(memdb)
    assert len(rows) == 1
    assert rows[0]["decision"] == "PASS"
    assert rows[0]["gate_id"] == GATE_SIGNAL_VALIDATOR
    assert rows[0]["reason"] == "g3_pass"


@pytest.mark.asyncio
async def test_g3_pass_records_pass_cohort_row(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A G3-cleared run records a PASS row sharing the KILL estimator (B2)."""
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[
            GateResult(decision=GateDecision.PASS, next_gate=2),
            GateResult(decision=GateDecision.PASS, next_gate=3),
            GateResult(decision=GateDecision.PASS, next_gate=GATE_ENTRY_SIZER),
        ],
        kill_gate=None,
    )
    rows = _cf_rows(memdb)
    assert len(rows) == 1
    assert rows[0]["decision"] == "PASS"
    assert rows[0]["gate_id"] == GATE_SIGNAL_VALIDATOR
    assert rows[0]["reason"] == "g3_pass"
    assert rows[0]["mark_price"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_fresh_ws_tick_preferred_as_mark(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: a fresh live WS mid beats the (possibly 1h-stale) bar close."""

    class _QW:
        def live_px(self, instrument_id: str) -> tuple[float, float] | None:
            assert instrument_id == "okx:BTC-USDT"
            return (101.5, time.monotonic())

        def recent_ticks(self, instrument_id: str) -> list[dict[str, object]]:
            return []

    state = ProdLoopState()
    state.quote_writer = _QW()  # type: ignore[assignment]
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[_kill_result()], kill_gate=GATE_SIGNAL_VALIDATOR,
    )
    row = _cf_rows(memdb)[0]
    assert row["gate_id"] == 3
    assert row["mark_price"] == pytest.approx(101.5)
    assert row["mark_source"] == "ws_tick"


@pytest.mark.asyncio
async def test_stale_ws_tick_falls_back_to_bar_close(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StaleQW:
        def live_px(self, instrument_id: str) -> tuple[float, float] | None:
            return (101.5, time.monotonic() - 300.0)  # 5 min old

        def recent_ticks(self, instrument_id: str) -> list[dict[str, object]]:
            return []

    state = ProdLoopState()
    state.quote_writer = _StaleQW()  # type: ignore[assignment]
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[_kill_result()], kill_gate=GATE_SIGNAL_VALIDATOR,
    )
    row = _cf_rows(memdb)[0]
    assert row["mark_price"] == pytest.approx(100.0)
    assert row["mark_source"] == "bar_close:1H"


@pytest.mark.asyncio
async def test_missing_table_never_crashes_pipeline(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    memdb.execute("DROP TABLE gate_kill_counterfactuals")
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[_kill_result()], kill_gate=GATE_SIGNAL_VALIDATOR,
    )  # no raise = pass


# ---------------------------------------------------------------------------
# 2 — forward-mark sweep
# ---------------------------------------------------------------------------


def _seed_cf_row(
    conn: sqlite3.Connection, *, event_id: str, side: str = "long",
    decision_ts: int = NOW, mark: float = 100.0, atr_usd: float = 2.0,
    symbol: str = "BTC-USDT",
) -> None:
    conn.execute(
        "INSERT INTO gate_kill_counterfactuals "
        "(event_id, run_id, signal_id, gate_id, decision, venue, symbol, "
        " strategy_id, side, regime, reason, model_used, decision_ts, "
        " mark_price, mark_ts, mark_source, atr_pct, atr_usd, cost_r, "
        " created_ts) "
        "VALUES (?, 'run', 'sig', 3, 'KILL', 'okx', ?, 'tsmom', ?, 'chop', "
        " 'r', 'gpt', ?, ?, ?, 'bar_close:1m', 0.01, ?, 0.05, ?)",
        (event_id, symbol, side, decision_ts, mark, decision_ts, atr_usd,
         decision_ts),
    )


def _seed_bar(
    conn: sqlite3.Connection, *, ts: int, close: float, symbol: str = "BTC-USDT",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, ts, "
        " open, high, low, close, volume) "
        "VALUES (?, 'crypto:BTC', 'okx', ?, '1m', ?, ?, ?, ?, ?, 1.0)",
        (f"okx:{symbol}", symbol, ts, close, close, close, close),
    )


@pytest.mark.asyncio
async def test_sweep_resolves_expired_horizons(memdb: sqlite3.Connection) -> None:
    _seed_cf_row(memdb, event_id="e1", side="long")
    for offset in (3600, 14400, 86400):
        _seed_bar(memdb, ts=NOW + offset, close=104.0)
    updated = await sweep_forward_marks(memdb, now_ts=NOW + 86401)
    assert updated == 1
    row = _cf_rows(memdb)[0]
    for label in ("1h", "4h", "24h"):
        assert row[f"fwd_close_{label}"] == pytest.approx(104.0)
        # long: (104 - 100) / 2.0 = +2.0 R
        assert row[f"fwd_r_{label}"] == pytest.approx(2.0)
        assert row[f"fwd_ts_{label}"] == NOW + {"1h": 3600, "4h": 14400,
                                                "24h": 86400}[label]
    assert row["resolve_attempts"] == 1
    # Re-run is idempotent: the resolved row left the pending set.
    assert await sweep_forward_marks(memdb, now_ts=NOW + 86402) == 0


@pytest.mark.asyncio
async def test_sweep_short_sign_is_inverted(memdb: sqlite3.Connection) -> None:
    """Short: a price DROP is +R for the counterfactual."""
    _seed_cf_row(memdb, event_id="e-short", side="short")
    _seed_bar(memdb, ts=NOW + 3600, close=96.0)
    await sweep_forward_marks(memdb, now_ts=NOW + 4000)
    row = _cf_rows(memdb)[0]
    assert row["fwd_r_1h"] == pytest.approx(2.0)  # -(96-100)/2.0
    assert row["fwd_r_4h"] is None  # horizon not yet expired


@pytest.mark.asyncio
async def test_sweep_unexpired_horizon_and_missing_bar_stay_null(
    memdb: sqlite3.Connection,
) -> None:
    _seed_cf_row(memdb, event_id="e2")
    # 1h expired but NO bar at/after NOW+3600 yet.
    updated = await sweep_forward_marks(memdb, now_ts=NOW + 4000)
    assert updated == 1  # attempts counter still advanced
    row = _cf_rows(memdb)[0]
    assert row["fwd_r_1h"] is None
    assert row["resolve_attempts"] == 1
    assert row["unresolvable"] == 0


@pytest.mark.asyncio
async def test_sweep_weekend_gap_uses_first_bar_after_horizon(
    memdb: sqlite3.Connection,
) -> None:
    _seed_cf_row(memdb, event_id="e3")
    gap_bar_ts = NOW + 3600 + 7200  # first bar lands 2h past the 1h horizon
    _seed_bar(memdb, ts=gap_bar_ts, close=102.0)
    await sweep_forward_marks(memdb, now_ts=NOW + 3600 + 7300)
    row = _cf_rows(memdb)[0]
    assert row["fwd_close_1h"] == pytest.approx(102.0)
    assert row["fwd_ts_1h"] == gap_bar_ts  # real bar ts → lag auditable


@pytest.mark.asyncio
async def test_sweep_unresolvable_terminal_stamp_frees_batch(
    memdb: sqlite3.Connection,
) -> None:
    """B5: a barless row past 24h+grace is terminally closed out."""
    _seed_cf_row(memdb, event_id="e4", decision_ts=NOW)
    grace_passed = NOW + 86400 + 3 * 86400 + 1
    assert await sweep_forward_marks(memdb, now_ts=grace_passed) == 1
    row = _cf_rows(memdb)[0]
    assert row["unresolvable"] == 1
    # Terminal: no longer occupies the pending batch.
    assert await sweep_forward_marks(memdb, now_ts=grace_passed + 60) == 0


@pytest.mark.asyncio
async def test_sweep_respects_limit_batch(memdb: sqlite3.Connection) -> None:
    for i in range(5):
        _seed_cf_row(memdb, event_id=f"lim-{i}", decision_ts=NOW + i)
    assert await sweep_forward_marks(memdb, now_ts=NOW + 7200, limit=3) == 3


# ---------------------------------------------------------------------------
# 3 — PASS backfill of gate_events.position_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_backfills_run_gate_events_position_id(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Foreign-run row must stay untouched.
    memdb.execute(
        "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
        "gate_id, phase, decision, created_ts) "
        "VALUES ('other', 'other-run', 'sig-x', NULL, 3, 'success', 'PASS', ?)",
        (NOW,),
    )
    trade = SimulatedTrade(
        signal_id="sig-b", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=80.0, open_ts=NOW,
        position_id="pos-backfill",
    )
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[
            _proceed_result(),
            GateResult(
                decision=GateDecision.SIZED, next_gate=None,
                payload={"sized": {"final_notional_usd": 80.0}},
            ),
        ],
        kill_gate=None, trade=trade, log_gate_rows=True,
    )
    run_id = _ScriptedOrch.last_run_id
    linked = memdb.execute(
        "SELECT position_id FROM gate_events WHERE run_id = ?", (run_id,),
    ).fetchall()
    assert linked and all(r[0] == "pos-backfill" for r in linked)
    other = memdb.execute(
        "SELECT position_id FROM gate_events WHERE run_id = 'other-run'",
    ).fetchone()
    assert other[0] is None


@pytest.mark.asyncio
async def test_backfill_survives_missing_gate_events_table(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    memdb.execute("DROP TABLE gate_events")
    trade = SimulatedTrade(
        signal_id="sig-b", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=100.0, notional_usd=80.0, open_ts=NOW,
        position_id="pos-x",
    )
    state = ProdLoopState()
    await _run_pipeline(
        memdb, state, monkeypatch,
        results=[
            GateResult(
                decision=GateDecision.SIZED, next_gate=None,
                payload={"sized": {"final_notional_usd": 80.0}},
            ),
        ],
        kill_gate=None, trade=trade,
    )  # no raise = pass


# ---------------------------------------------------------------------------
# 4 — positions.pnl_r stamped on the final full close only
# ---------------------------------------------------------------------------


def _seed_position_and_fill(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float = 100.0,
    entry_price: float = 100.0, bar_close: float = 130.0, risk_usd: float = 500.0,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0, ?)",
        (position_id, base_qty, NOW, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', 'long', ?, ?, ?, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, base_qty, entry_price,
         base_qty * entry_price, position_id, uuid.uuid4().hex),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume) "
            "VALUES ('okx:SOL-USDT', 'crypto:SOL', 'okx', 'SOL-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0)",
            (ts, bar_close, bar_close + 1.0, bar_close - 1.0, bar_close),
        )


@pytest.mark.asyncio
async def test_full_close_stamps_positions_pnl_r(memdb: sqlite3.Connection) -> None:
    from polaris.scripts._production_close import close_oldest_with_real_pnl

    _seed_position_and_fill(memdb, position_id="pos-pnlr")
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=10_000.0, open_ts=NOW, position_id="pos-pnlr",
    )
    trade.base_qty = 100.0
    state = ProdLoopState()
    state.open_trades = [trade]
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=NOW + 60,
        lookup_regime=lambda _c, _v, _s: "bull_trend",
        gpt_client=None, phase="P0",
    )
    assert trade.closed is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id = 'pos-pnlr'",
    ).fetchone()
    assert row[0] == "closed"
    assert row[1] is not None
    assert row[1] == pytest.approx(trade.pnl_r)
    assert row[1] > 0.0  # bar drift 100 → 130 is a win


def test_partial_close_stamps_slice_pnl_r(memdb: sqlite3.Connection) -> None:
    """P2 R-ledger-leak fix: a partial close ACCUMULATES the slice's R onto
    positions.pnl_r (status stays 'open'). Previously left NULL — the LUNA / DOT
    leak where a partial-only close never trained the R ledger. Two slices
    accumulate; ``slice_pnl_r`` defaults to 0.0 so a legacy caller is harmless."""
    from polaris.core.data.fill_normalizer import Fill
    from polaris.scripts._production_close_helpers import _persist_partial_close

    _seed_position_and_fill(memdb, position_id="pos-part")
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=10_000.0, open_ts=NOW, position_id="pos-part",
    )
    trade.base_qty = 100.0
    fill = Fill(
        venue="okx", instrument_id="okx:SOL-USDT", strategy_id="tsmom",
        side="sell", size_usd=60.0 * 130.0, fill_price=130.0, fee_usd=0.1,
        slippage_bps=0.0, ts_ms=(NOW + 60) * 1000, order_id=uuid.uuid4().hex,
        base_qty=60.0,
    )
    ok = _persist_partial_close(
        memdb, state=ProdLoopState(), trade=trade, close_fill=fill,
        pnl_usd=100.0, now_ts=NOW + 60, slice_pnl_r=0.3,
    )
    assert ok is True
    row = memdb.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id = 'pos-part'",
    ).fetchone()
    assert row[0] == "open"  # partial keeps the position open
    assert row[1] == pytest.approx(0.3)  # slice R stamped (no longer NULL)
    # A second partial slice accumulates onto the first.
    fill2 = Fill(
        venue="okx", instrument_id="okx:SOL-USDT", strategy_id="tsmom",
        side="sell", size_usd=25.0 * 130.0, fill_price=130.0, fee_usd=0.05,
        slippage_bps=0.0, ts_ms=(NOW + 120) * 1000, order_id=uuid.uuid4().hex,
        base_qty=25.0,
    )
    _persist_partial_close(
        memdb, state=ProdLoopState(), trade=trade, close_fill=fill2,
        pnl_usd=50.0, now_ts=NOW + 120, slice_pnl_r=0.2,
    )
    row2 = memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-part'",
    ).fetchone()
    assert row2[0] == pytest.approx(0.5)  # 0.3 + 0.2 accumulated


# ---------------------------------------------------------------------------
# 5 — schema idempotency + legacy upgrade + cohort view
# ---------------------------------------------------------------------------


def test_init_db_idempotent_and_legacy_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    # Legacy DB: positions WITHOUT pnl_r (pre-instrumentation shape).
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE positions (position_id TEXT PRIMARY KEY, venue TEXT, "
        "symbol TEXT, strategy_id TEXT NOT NULL DEFAULT '', side TEXT, "
        "qty REAL, status TEXT, opened_ts INTEGER)"
    )
    legacy.commit()
    legacy.close()
    conn = init_db(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    assert "pnl_r" in cols
    # View + table exist and are queryable.
    conn.execute("SELECT * FROM v_g34_cohort_outcomes").fetchall()
    conn.execute("SELECT * FROM gate_kill_counterfactuals").fetchall()
    conn.close()
    # Second init is idempotent.
    conn2 = init_db(db)
    conn2.execute("SELECT * FROM v_g34_cohort_outcomes").fetchall()
    conn2.close()


def test_cohort_view_single_select(tmp_path: Path) -> None:
    """PASS (won/lost) + KILL (resolved/unresolved) → one SELECT, fee-adj R."""
    conn = init_db(tmp_path / "v.sqlite")
    # KILL resolved at 1h only.
    _seed_cf_row(conn, event_id="k1")
    conn.execute(
        "UPDATE gate_kill_counterfactuals SET fwd_close_1h = 104.0, "
        "fwd_ts_1h = ?, fwd_r_1h = 2.0 WHERE event_id = 'k1'",
        (NOW + 3600,),
    )
    # KILL fully unresolved → contributes NO rows.
    _seed_cf_row(conn, event_id="k2")
    # PASS rows (one winner, one loser) with realized pnl_r via the run link.
    for tag, run_id, pnl in (("p1", "run-p1", 1.5), ("p2", "run-p2", -0.7)):
        conn.execute(
            "INSERT INTO gate_kill_counterfactuals "
            "(event_id, run_id, signal_id, gate_id, decision, venue, symbol, "
            " strategy_id, side, regime, reason, model_used, decision_ts, "
            " mark_price, mark_ts, mark_source, atr_pct, atr_usd, cost_r, "
            " fwd_close_1h, fwd_ts_1h, fwd_r_1h, created_ts) "
            "VALUES (?, ?, 'sig', 4, 'PASS', 'okx', 'BTC-USDT', 'tsmom', "
            " 'long', 'chop', 'g3g4_pass', '', ?, 100.0, ?, 'ws_tick', 0.01, "
            " 2.0, 0.05, 101.0, ?, 0.5, ?)",
            (tag, run_id, NOW, NOW, NOW + 3600, NOW),
        )
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, "
            "opened_ts, pnl_r) "
            "VALUES (?, 'okx', 'BTC-USDT', 'tsmom', 'tsmom', 'tsmom', 'long', "
            "1.0, 'closed', ?, ?)",
            (f"pos-{tag}", NOW, pnl),
        )
        conn.execute(
            "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
            "gate_id, phase, decision, created_ts) "
            "VALUES (?, ?, 'sig', ?, 3, 'success', 'PASS', ?)",
            (uuid.uuid4().hex, run_id, f"pos-{tag}", NOW),
        )
    rows = conn.execute(
        "SELECT cohort, horizon, fee_adj_fwd_r, realized_pnl_r, fwd_lag_sec "
        "FROM v_g34_cohort_outcomes ORDER BY cohort, event_id",
    ).fetchall()
    conn.close()
    # k2 (unresolved) contributes nothing; k1 + p1 + p2 → one '1h' row each.
    assert len(rows) == 3
    kill_rows = [r for r in rows if r[0] == "KILL"]
    pass_rows = [r for r in rows if r[0] == "PASS"]
    assert len(kill_rows) == 1 and len(pass_rows) == 2
    assert kill_rows[0][1] == "1h"
    assert kill_rows[0][2] == pytest.approx(2.0 - 0.05)  # fee-adj fwd R
    assert kill_rows[0][3] is None  # KILL has no realized outcome
    assert kill_rows[0][4] == 0  # bar exactly at the horizon → zero lag
    assert {r[3] for r in pass_rows} == {1.5, -0.7}
    assert all(r[2] == pytest.approx(0.5 - 0.05) for r in pass_rows)


# ---------------------------------------------------------------------------
# 6 — offline backfill script
# ---------------------------------------------------------------------------


def test_offline_backfill_pnl_r_from_segments(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES ('pos-old', 'okx', 'BTC-USDT', 'tsmom', 'tsmom', 'tsmom', "
        "'long', 1.0, 'closed', ?)",
        (NOW,),
    )
    memdb.execute(
        "INSERT INTO position_strategy_segments (position_id, segment_id, "
        "strategy_id, started_ts, ended_ts, pnl_r) "
        "VALUES ('pos-old', 'seg1', 'tsmom', ?, ?, 1.25)",
        (NOW, NOW + 600),
    )
    assert backfill_positions_pnl_r(memdb) == 1
    assert memdb.execute(
        "SELECT pnl_r FROM positions WHERE position_id = 'pos-old'",
    ).fetchone()[0] == pytest.approx(1.25)
    # Idempotent — second run touches nothing.
    assert backfill_positions_pnl_r(memdb) == 0


def test_offline_backfill_links_unique_signal_only(
    memdb: sqlite3.Connection,
) -> None:
    # Unique signal → linked; duplicated signal → skipped.
    for pid, sig_id in (("pos-u", "sig-unique"), ("pos-d1", "sig-dup"),
                        ("pos-d2", "sig-dup")):
        memdb.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, "
            "opened_ts, signal_id) "
            "VALUES (?, 'okx', 'BTC-USDT', 'tsmom', 'tsmom', 'tsmom', 'long', "
            "1.0, 'closed', ?, ?)",
            (pid, NOW, sig_id),
        )
    for sig_id in ("sig-unique", "sig-dup"):
        memdb.execute(
            "INSERT INTO gate_events (event_id, run_id, signal_id, position_id, "
            "gate_id, phase, decision, created_ts) "
            "VALUES (?, 'r', ?, NULL, 3, 'success', 'PASS', ?)",
            (uuid.uuid4().hex, sig_id, NOW + 5),
        )
    stats = backfill_gate_event_links(memdb)
    assert stats["linked_rows"] == 1
    assert stats["linked_positions"] == 1
    assert stats["ambiguous_signal_ids"] == 1
    linked = memdb.execute(
        "SELECT position_id FROM gate_events WHERE signal_id = 'sig-unique'",
    ).fetchone()[0]
    assert linked == "pos-u"
    dup = memdb.execute(
        "SELECT position_id FROM gate_events WHERE signal_id = 'sig-dup'",
    ).fetchone()[0]
    assert dup is None
