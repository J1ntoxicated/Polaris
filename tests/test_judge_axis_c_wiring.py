"""#32 axis-C — verdict→behaviour wiring (flow-safe, 9-stack-safe, -1.0R-safe).

DEMO/PAPER paper bot. These tests PROVE that an A+B-gated judge verdict actually
DRIVES behaviour (dead → live):

- ENTRY SIZE_UP → folds ``judge_conviction`` into the ONE continuous scalar +
  re-clamps (NOT a new T4 multiplier; the 9-stack count is unchanged). Only EVER
  pushes toward the band ceiling — never cuts (flow_not_block).
- ENTRY REFINE_TIMING / STRENGTHEN_EVIDENCE → pure annotation stamps.
- EXIT EXTEND → widen REQUEST routed THROUGH the Q9 rail (rail FINAL; reject →
  stop unchanged). EXIT TIGHTEN → ratchet-safe tighten REQUEST routed through the
  rail (never loosen).
- PROCEED / PROTECT / shadow / weak / hostile → byte-identical no-op.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.pipeline.agents.ai_judge import (
    EntryJudgeVerdict,
    ExitJudgeVerdict,
    JudgeOutcome,
    apply_entry_verdict,
    apply_exit_verdict,
)
from polaris.core.pipeline.agents.entry_sizer import entry_sizer_gate
from polaris.core.pipeline.config import AI_JUDGE_MODE_ACTIVE, AI_JUDGE_MODE_SHADOW
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_ENTRY_SIZER,
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
)
from polaris.core.sizing.schema import (
    CONT_SCALAR_MAX,
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
)

NOW = 1_900_000_000


def _intent(judge_conviction: float = 1.0) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-c",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=0.9,  # cont below the ceiling so a boost is observable
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
        signal_family="reversion",  # neutral regime-fit so boost is isolated
        judge_conviction=judge_conviction,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst", closed_trades=25,
        kelly_p=0.55, kelly_q=0.45, kelly_fraction=0.05,
        win_streak=0, hit_rate_10=0.5, updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


# ===========================================================================
# C — ENTRY SIZE_UP folds into the ONE continuous scalar (9-stack safe)
# ===========================================================================


def test_size_up_boosts_continuous_scalar(memdb: sqlite3.Connection) -> None:
    base = compute_size(
        memdb, intent=_intent(1.0), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    boosted = compute_size(
        memdb, intent=_intent(1.15), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    # The boost folds into the SAME continuous_scalar (no new multiplier appears).
    assert boosted.proposed.continuous_scalar > base.proposed.continuous_scalar
    # Same number of chain factors — tier/cell/listing identical (9-stack intact).
    assert boosted.proposed.tier_amplifier == base.proposed.tier_amplifier
    assert boosted.proposed.cell_routing_mult == base.proposed.cell_routing_mult
    assert boosted.proposed.listing_watchdog_mult == base.proposed.listing_watchdog_mult


def test_size_up_reclamps_to_band_ceiling(memdb: sqlite3.Connection) -> None:
    # A cont already at the ceiling cannot be pushed past CONT_SCALAR_MAX.
    intent = SignalIntent(
        signal_id="s", venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC", asset_class="crypto", strategy="volume_burst",
        track="A", regime="bull_trend", direction="long", signal_strength=1.5,
        listing_age_hours=72.0, leverage=1.0, base_risk_pct=0.02,
        signal_family="reversion", judge_conviction=1.5,
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.proposed.continuous_scalar <= CONT_SCALAR_MAX


def test_size_up_never_breaches_absolute_ceiling(memdb: sqlite3.Connection) -> None:
    sized = compute_size(
        memdb, intent=_intent(1.5), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized.final_risk_pct <= SINGLE_TRADE_ABSOLUTE_CEILING_PCT


@pytest.mark.asyncio
async def test_entry_sizer_consumes_size_up_intent(memdb: sqlite3.Connection) -> None:
    """entry_sizer_gate reads ai_judge_size_up_intent → larger final_risk_pct than
    without it. Same chain, just the ONE scalar pushed toward its ceiling."""
    def _ctx(size_up: bool) -> GateContext:
        payload = {
            "signal_intent": _intent(1.0),
            "risk_state": _risk_state(),
            "portfolio": _portfolio(),
        }
        if size_up:
            payload["ai_judge_size_up_intent"] = True
        return GateContext(
            run_id="r", signal_id="s", position_id=None, gate_id=GATE_ENTRY_SIZER,
            venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
            payload=payload, started_ts=NOW, state=SignalLifecycle.WATCHED,
        )

    plain = await entry_sizer_gate(_ctx(False), conn=memdb)
    boosted = await entry_sizer_gate(_ctx(True), conn=memdb)
    assert plain.decision == GateDecision.SIZED
    assert boosted.decision == GateDecision.SIZED
    assert (
        boosted.payload["sized"]["final_risk_pct"]
        > plain.payload["sized"]["final_risk_pct"]
    )


# ===========================================================================
# C — ENTRY annotation verdicts (no behaviour)
# ===========================================================================


def _det_pass() -> GateResult:
    return GateResult(
        decision=GateDecision.PASS, next_gate=GATE_ENTRY_SIZER,
        payload={"validated_signal": {"symbol": "BTC-USDT", "strength_scalar": 1.0}},
        model_used="python",
    )


def test_refine_timing_is_flow_passthrough() -> None:
    # REFINE_TIMING carried an inert one-shot payload stamp (no consumer ever
    # read it); removed as dead telemetry. The verdict still flows unchanged.
    out = JudgeOutcome(
        verdict=EntryJudgeVerdict.REFINE_TIMING.value,
        deterministic=GateDecision.PASS, escalation_reason="t",
    )
    res = apply_entry_verdict(out, deterministic_result=_det_pass(), mode=AI_JUDGE_MODE_ACTIVE)
    assert res.decision == GateDecision.PASS  # flow unchanged
    assert "ai_judge_refine_timing" not in res.payload


def test_strengthen_evidence_stamps_annotation_only() -> None:
    out = JudgeOutcome(
        verdict=EntryJudgeVerdict.STRENGTHEN_EVIDENCE.value,
        deterministic=GateDecision.PASS, escalation_reason="t",
    )
    res = apply_entry_verdict(out, deterministic_result=_det_pass(), mode=AI_JUDGE_MODE_ACTIVE)
    assert res.decision == GateDecision.PASS
    assert res.payload["ai_judge_confidence"]["strengthened"] is True
    # STRENGTHEN does NOT request a size boost (only SIZE_UP does).
    assert "ai_judge_size_up_intent" not in res.payload


def test_proceed_is_pure_passthrough() -> None:
    out = JudgeOutcome(
        verdict=EntryJudgeVerdict.PROCEED.value,
        deterministic=GateDecision.PASS, escalation_reason="t",
    )
    res = apply_entry_verdict(out, deterministic_result=_det_pass(), mode=AI_JUDGE_MODE_ACTIVE)
    assert res.decision == GateDecision.PASS
    assert "ai_judge_size_up_intent" not in res.payload
    assert "ai_judge_refine_timing" not in res.payload


def test_shadow_mode_no_size_up_stamp() -> None:
    out = JudgeOutcome(
        verdict=EntryJudgeVerdict.SIZE_UP.value,
        deterministic=GateDecision.PASS, escalation_reason="t",
    )
    res = apply_entry_verdict(out, deterministic_result=_det_pass(), mode=AI_JUDGE_MODE_SHADOW)
    # Shadow = deterministic result verbatim, no stamp.
    assert "ai_judge_size_up_intent" not in res.payload
    assert "ai_judge" not in res.payload


# ===========================================================================
# C — EXIT EXTEND / TIGHTEN requests stamped (routed through rails downstream)
# ===========================================================================


def _det_adjust() -> GateResult:
    return GateResult(
        decision=GateDecision.ADJUST_EXIT, next_gate=None,
        payload={"stop_price": 93.0, "widening_applied": True, "reason": "ok"},
        model_used="python",
    )


def test_extend_stamps_widen_request() -> None:
    out = JudgeOutcome(
        verdict=ExitJudgeVerdict.EXTEND.value,
        deterministic=GateDecision.ADJUST_EXIT, escalation_reason="t",
    )
    res = apply_exit_verdict(out, deterministic_result=_det_adjust(), mode=AI_JUDGE_MODE_ACTIVE)
    assert res.payload["ai_judge_widen_request"]["extend_atr"] > 0.0
    assert "ai_judge_tighten_request" not in res.payload


def test_tighten_stamps_tighten_request() -> None:
    out = JudgeOutcome(
        verdict=ExitJudgeVerdict.TIGHTEN_ON_CONFIRMED_DECAY.value,
        deterministic=GateDecision.ADJUST_EXIT, escalation_reason="t",
    )
    res = apply_exit_verdict(out, deterministic_result=_det_adjust(), mode=AI_JUDGE_MODE_ACTIVE)
    assert res.payload["ai_judge_tighten_request"]["tighten_atr"] > 0.0
    assert "ai_judge_widen_request" not in res.payload


def test_protect_no_request_stamps() -> None:
    out = JudgeOutcome(
        verdict=ExitJudgeVerdict.PROTECT.value,
        deterministic=GateDecision.ADJUST_EXIT, escalation_reason="t",
    )
    res = apply_exit_verdict(out, deterministic_result=_det_adjust(), mode=AI_JUDGE_MODE_ACTIVE)
    assert "ai_judge_widen_request" not in res.payload
    assert "ai_judge_tighten_request" not in res.payload


# ===========================================================================
# C — EXIT request → RAIL routing (adaptive_exit_gate end-to-end)
# ===========================================================================


def _g7_ctx(judge_exit_escalate: bool = True) -> GateContext:
    now = int(time.time())
    return GateContext(
        run_id="r", signal_id="s", position_id="pos-1", gate_id=GATE_ADAPTIVE_EXIT,
        venue="okx", symbol="BTC-USDT", strategy_id="s1",
        payload={
            "regime": "trend_up",
            "evidence": {"label": "bull_trend", "news_sentiment": 0.4, "news_n": 5, "crypto_fg": 78},
            "baseline": {"atr": {"p50": 1.2}},
            "cell_routing": {"n_eff": 12.0},
            "ticker_ground": {"has_sentiment": True, "updated_ts": now - 60},
            "judge_exit_escalate": judge_exit_escalate,
            "widen_proposal": {
                "side": "long", "current_stop_price": 95.0, "proposed_stop_price": 93.0,
                "entry_price": 100.0, "unrealized_pnl_r": 1.0, "max_loss_r": 1.0,
                "overrides_used": 0, "seconds_since_last_override": 60,
                "initial_stop_price": 80.0,
            },
        },
        started_ts=now, state=SignalLifecycle.MONITORED,
    )


class _FakeGPT:
    def __init__(self, text: str) -> None:
        outer = self

        class _Block:
            pass

        class _Messages:
            async def create(self, **kwargs: object) -> object:
                class _R:
                    content = [type("B", (), {"text": text})()]
                    usage = None
                _ = outer
                return _R()

        self.messages = _Messages()


@pytest.mark.asyncio
async def test_extend_routes_through_widen_rail(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """An active EXTEND verdict pushes the stop FARTHER via the Q9 rail; the rail
    accepts it (pnl_r>0.7R, above floor) → stop moves past the deterministic 93.0."""
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    judge = _FakeGPT('{"verdict":"EXTEND"}')
    res = await adaptive_exit_gate(
        _g7_ctx(), client=None, judge_client=judge, shadow_conn=memdb,
    )
    # Long widen = stop LOWER (farther from price). The judge EXTEND pushed it below
    # the deterministic proposed 93.0 and the rail accepted it.
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload["widening_applied"] is True
    assert float(res.payload["stop_price"]) < 93.0
    assert res.payload["ai_judge_request"] == "EXTEND"


@pytest.mark.asyncio
async def test_extend_rail_rejects_below_floor_keeps_stop(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """If the EXTEND would push below the max-loss floor, the rail REJECTS and the
    stop is UNCHANGED (-1.0R / max_loss unbypassable; flow_not_block — never a cut)."""
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    ctx = _g7_ctx()
    # Floor just below the deterministic proposed stop so any extra-ATR push is
    # rejected by the rail (below_max_loss).
    ctx.payload["widen_proposal"]["initial_stop_price"] = 92.9
    judge = _FakeGPT('{"verdict":"EXTEND"}')
    res = await adaptive_exit_gate(
        ctx, client=None, judge_client=judge, shadow_conn=memdb,
    )
    # Rail rejected the judge's farther stop → HOLD with the current stop preserved.
    assert res.decision == GateDecision.HOLD
    assert res.payload["widening_applied"] is False
    assert float(res.payload["stop_price"]) == 95.0  # current stop, unchanged


@pytest.mark.asyncio
async def test_tighten_routes_through_tighten_rail(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """An active TIGHTEN verdict pulls the stop CLOSER via the ratchet rail (long =
    stop UP toward price); never loosens."""
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    judge = _FakeGPT('{"verdict":"TIGHTEN_ON_CONFIRMED_DECAY"}')
    res = await adaptive_exit_gate(
        _g7_ctx(), client=None, judge_client=judge, shadow_conn=memdb,
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload["tightening_applied"] is True
    # Long tighten = stop HIGHER than the current 95.0 (closer to price).
    assert float(res.payload["stop_price"]) > 95.0
    assert res.payload["ai_judge_request"] == "TIGHTEN"


@pytest.mark.asyncio
async def test_exit_escalate_false_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, memdb: sqlite3.Connection
) -> None:
    """judge_exit_escalate=False (A-skip) → the judge is NOT consulted; the
    deterministic Q9 rail (ADJUST_EXIT widen) flows unchanged (flow_not_block)."""
    from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate

    monkeypatch.setenv("POLARIS_AI_FREE", "1")
    monkeypatch.setenv("POLARIS_AI_JUDGE_MODE", "active")
    judge = _FakeGPT('{"verdict":"EXTEND"}')
    res = await adaptive_exit_gate(
        _g7_ctx(judge_exit_escalate=False), client=None, judge_client=judge,
        shadow_conn=memdb,
    )
    # No judge stamp, deterministic rail decision preserved.
    assert "ai_judge" not in res.payload
    assert "ai_judge_request" not in res.payload
    assert res.payload["stop_price"] == 93.0  # deterministic proposed, unchanged
