"""Probability calibration shadow entry-seam wiring (frontgate-scan item #4,
G5) — ``entry_sizer_gate`` (Gate 5) snapshots THIS signal's cell p_pos at
sizing time, right after a SIZED verdict.

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.

The seam lives in ``core/pipeline/agents/entry_sizer.py`` (the G5 gate
wrapper), NOT inside ``core/sizing/engine.py`` — ``polaris/core/sizing/*.py``
is under a hard regression guard
(``tests/test_edge_validation.py::test_sizing_does_not_import_posterior``)
forbidding the whole sizing package from referencing ``learner_posterior`` /
``p_pos`` / ``posterior`` at all, so the calibration snapshot's
``learner_posterior`` read must stay OUTSIDE that package.

Behavior-0 proof: the gate's ``GateResult`` (decision + sized payload) is
byte-identical whether the calibration snapshot succeeds OR fails (table
dropped) — the seam is a fail-open measurement side effect, never a sizing
input.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from polaris.core.pipeline.agents.entry_sizer import entry_sizer_gate
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState

NOW = 1_780_000_000


def _intent(signal_id: str = "sig-cal-1") -> SignalIntent:
    return SignalIntent(
        signal_id=signal_id, venue="okx", symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT", underlying_group_id="crypto:BTC",
        asset_class="crypto", strategy="tsmom", track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=720.0, leverage=1.0, base_risk_pct=0.02,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="tsmom",
        closed_trades=25, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


def _ctx(intent: SignalIntent) -> GateContext:
    payload: dict[str, Any] = {
        "signal_intent": intent, "risk_state": _risk_state(), "portfolio": _portfolio(),
    }
    return GateContext(
        run_id="r", signal_id=intent.signal_id, position_id=None,
        gate_id=GATE_ENTRY_SIZER, venue=intent.venue, symbol=intent.symbol,
        strategy_id=intent.strategy, payload=payload, started_ts=NOW,
        state=SignalLifecycle.WATCHED,
    )


@pytest.mark.asyncio
async def test_entry_sizer_gate_snapshots_calibration_pair(
    memdb: sqlite3.Connection,
) -> None:
    result = await entry_sizer_gate(_ctx(_intent("sig-cal-1")), conn=memdb)
    assert result.decision == GateDecision.SIZED
    row = memdb.execute(
        "SELECT venue, strategy, ticker, regime, predicted_p_pos "
        "FROM calibration_pairs WHERE signal_id = 'sig-cal-1'"
    ).fetchone()
    assert row is not None
    assert row[:4] == ("okx", "tsmom", "BTC-USDT", "bull_trend")


@pytest.mark.asyncio
async def test_entry_sizer_gate_reads_live_learner_posterior_at_sizing_time(
    memdb: sqlite3.Connection,
) -> None:
    memdb.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime, p_pos, n_samples, updated_ts) "
        "VALUES ('okx', 'tsmom', 'BTC-USDT', 'bull_trend', 0.81, 40, ?)",
        (NOW - 100,),
    )
    await entry_sizer_gate(_ctx(_intent("sig-cal-2")), conn=memdb)
    row = memdb.execute(
        "SELECT predicted_p_pos, n_samples_at_entry FROM calibration_pairs "
        "WHERE signal_id = 'sig-cal-2'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0.81
    assert row[1] == 40


@pytest.mark.asyncio
async def test_gate_result_byte_identical_when_calibration_table_missing(
    memdb: sqlite3.Connection,
) -> None:
    """Behavior-0: a broken calibration_pairs table (fail-open) must not
    change the gate's decision or sized payload in any way."""
    baseline = await entry_sizer_gate(_ctx(_intent("sig-cal-3")), conn=memdb)

    memdb.execute("DROP TABLE calibration_pairs")
    broken = await entry_sizer_gate(_ctx(_intent("sig-cal-4")), conn=memdb)

    assert baseline.decision == broken.decision
    assert baseline.payload["sized"] == broken.payload["sized"]
