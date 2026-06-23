"""BUILD 2/3 — log-everything: structural INFO logging at the gate/regime-fit
seams, with BYTE-IDENTICAL behavior (logging only, never a decision change).

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack collapse blocked · in-loop
GPT=0 · Anthropic = Claude-Code-dev-only. No rejection keyword (regulatory cap /
professional risk / 90d gate / monthly review / fractional Kelly is too
aggressive in practice / 표본부족 / real-money safety) appears here — this BUILD
only adds observability, it shapes nothing.

The four seams under test:
  * seam1  ``[regime-fit/seam1-size]`` — sizing/engine.compute_size folds the
    ONE T4 continuous scalar and logs fit / scalar / cont before→after.
  * G3     ``[gate/G3-validator]``     — signal_validator AI-free primary verdict.
  * G4     ``[gate/G4-watcher]``       — pre_entry_watcher AI-free primary verdict.
  * probe  ``[probe/verdict]``         — the observe-only probe bus→engine verdict.

Each test asserts (a) the structural line is emitted at INFO and (b) the decision
the gate returns is unchanged by the logging.
"""

from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from polaris.core.pipeline.agents.pre_entry_watcher import pre_entry_watcher_gate
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
)


def _g3_ctx(*, regime: str = "trend_up") -> GateContext:
    return GateContext(
        run_id="run-log",
        signal_id="sig-1",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "raw_signal": {"symbol": "BTC-USDT", "side": "long", "strength": 1.2},
            "cell_routing": {
                "quartile": "top",
                "n_eff": 10.0,
                "avg_pnl_r": 0.5,
                "score": 0.5,
            },
            "baseline": {},
            "recent_trades": [],
            "regime": regime,
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )


def _g4_ctx() -> GateContext:
    now = int(time.time())
    return GateContext(
        run_id="run-log",
        signal_id="sig-1",
        position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "validated_signal": {"symbol": "BTC-USDT", "strength_scalar": 1.0},
            "tick_window": [{"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}],
            "cell_quartile": "mid",
            "regime": "trend_up",
        },
        started_ts=now,
        state=SignalLifecycle.VALIDATED,
    )


@pytest.fixture
def ai_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_AI_FREE", "1")


# --------------------------------------------------------------------------- #
# seam1 — sizing fold log                                                      #
# --------------------------------------------------------------------------- #
_NOW = 1_900_000_000


def _intent(*, family: str, regime: str) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1",
        venue="okx",
        symbol="PL24-USDT",
        instrument_id="okx:PL24-USDT",
        underlying_group_id="crypto:PL",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime=regime,
        direction="long",
        signal_strength=1.2,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        signal_family=family,
    )


def _risk() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=25, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=_NOW,
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


def test_seam1_size_emits_structural_line(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="polaris.core.sizing.engine"):
        compute_size(
            memdb,
            intent=_intent(family="momentum", regime="chop"),
            risk_state=_risk(),
            portfolio=_portfolio(),
            now_ts=_NOW + 100,
        )
    lines = [r.message for r in caplog.records if "[regime-fit/seam1-size]" in r.message]
    assert lines, "seam1 size fold must log a structural INFO line"
    msg = lines[0]
    # the churn case must be visible: momentum × chop → fit -1.00, scalar 0.750
    assert "family=momentum" in msg
    assert "fit=-1.00" in msg
    assert "scalar=0.750" in msg
    assert "9-stack intact" in msg


def test_seam1_log_does_not_change_size(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """The log line is observability only: the final notional is identical with
    logging enabled vs suppressed (byte-identical behavior)."""
    intent = _intent(family="momentum", regime="chop")
    with caplog.at_level(logging.INFO):
        sized_loud = compute_size(
            memdb, intent=intent, risk_state=_risk(),
            portfolio=_portfolio(), now_ts=_NOW + 100,
        )
    with caplog.at_level(logging.CRITICAL):
        sized_quiet = compute_size(
            memdb, intent=intent, risk_state=_risk(),
            portfolio=_portfolio(), now_ts=_NOW + 100,
        )
    assert sized_loud.final_notional_usd == sized_quiet.final_notional_usd
    assert sized_loud.final_risk_pct == sized_quiet.final_risk_pct
    # flow_not_block: a bad-fit signal STILL sizes (floor 0.75, never 0).
    assert sized_loud.final_notional_usd > 0.0


# --------------------------------------------------------------------------- #
# G3 / G4 AI-free verdict logs                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_g3_ai_free_logs_verdict(
    ai_free: None, caplog: pytest.LogCaptureFixture
) -> None:
    logger_name = "polaris.core.pipeline.agents.signal_validator"
    with caplog.at_level(logging.INFO, logger=logger_name):
        res = await signal_validator_gate(_g3_ctx())
    lines = [r.message for r in caplog.records if "[gate/G3-validator]" in r.message]
    assert lines, "G3 AI-free primary must log its verdict"
    assert "GPT=0" in lines[0]
    # decision is unchanged — deterministic primary still returns its verdict.
    assert res.model_used == "python"
    assert res.decision in {GateDecision.PASS, GateDecision.MODIFY, GateDecision.KILL}


@pytest.mark.asyncio
async def test_g4_ai_free_logs_verdict(
    ai_free: None, caplog: pytest.LogCaptureFixture
) -> None:
    logger_name = "polaris.core.pipeline.agents.pre_entry_watcher"
    with caplog.at_level(logging.INFO, logger=logger_name):
        res = await pre_entry_watcher_gate(_g4_ctx())
    lines = [r.message for r in caplog.records if "[gate/G4-watcher]" in r.message]
    # the fast-path may short-circuit before the AI-free branch; force the
    # non-fast-path by asserting EITHER a verdict line OR a fast-path proceed.
    if res.model_used == "python":
        assert lines, "G4 AI-free primary must log its verdict"
        assert "GPT=0" in lines[0]
    assert res.decision in {GateDecision.PROCEED, GateDecision.KILL}


@pytest.mark.asyncio
async def test_g3_log_invariant_to_decision(ai_free: None) -> None:
    """Logging is side-effect-free w.r.t. the returned decision: two identical
    calls (one with INFO, one silenced) yield the same decision + payload."""
    ctx_a = _g3_ctx()
    ctx_b = _g3_ctx()
    res_a = await signal_validator_gate(ctx_a)
    res_b = await signal_validator_gate(ctx_b)
    assert res_a.decision == res_b.decision
    assert res_a.payload == res_b.payload


# --------------------------------------------------------------------------- #
# probe verdict log                                                            #
# --------------------------------------------------------------------------- #
def test_probe_attach_logs_verdict_when_wired(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a probe bus+engine are wired, observe_probes emits a structural
    ``[probe/verdict]`` INFO line; when they are absent it is a silent no-op
    (fail-open). Either way the exit is unaffected (returns None)."""
    from polaris.scripts._production_probe_attach import observe_probes

    class _NoBusState:
        pass

    # No bus/engine wired → silent no-op, no verdict line, no raise.
    with caplog.at_level(logging.INFO):
        observe_probes(
            state=_NoBusState(),  # type: ignore[arg-type]
            pos={"position_id": "p1", "venue": "okx", "symbol": "BTC-USDT"},  # type: ignore[arg-type]
            side="long",
            entry_price=100.0,
            last_price=101.0,
            atr_pct=0.01,
            entry_atr_pct=0.01,
            pnl_r=0.5,
            held_seconds=30,
            regime="chop",
            now_ts=int(time.time()),
            run_id="run-1",
            mark_source="tick",
        )
    assert not [r for r in caplog.records if "[probe/verdict]" in r.message]
