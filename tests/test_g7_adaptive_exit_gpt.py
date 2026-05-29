"""Day 9 F1 — G7 Adaptive Exit GPT P1 unit tests (mocked OpenAI).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q9 floor-only widening)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Adaptive Exit)

Conservative-trap avoidance (Jin 2026-05-07): TIGHTEN suggestions that move
the stop closer than the default ATR floor are reversed to HOLD. The LLM
cannot dampen winners with a smaller stop.
"""

from __future__ import annotations

import json
import uuid

from polaris.core.pipeline.agents.adaptive_exit import (
    G7_DECISION_ENUM,
    adaptive_exit_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


class _MockGPTClient:
    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)

                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _make_proposal(
    *,
    side: str = "long",
    current_stop: float = 79_000.0,
    proposed_stop: float = 78_000.0,
    pnl_r: float = 1.2,
    overrides_used: int = 0,
    cooldown: int = 60,
) -> dict:
    return {
        "side": side,
        "current_stop_price": current_stop,
        "proposed_stop_price": proposed_stop,
        "entry_price": 80_000.0,
        "unrealized_pnl_r": pnl_r,
        "max_loss_r": 1.0,
        "overrides_used": overrides_used,
        "seconds_since_last_override": cooldown,
    }


def _ctx(proposal: dict | None, exit_context: dict | None = None) -> GateContext:
    payload: dict = {}
    if proposal is not None:
        payload["widen_proposal"] = proposal
        payload["current_stop_price"] = proposal["current_stop_price"]
    if exit_context is not None:
        payload["exit_context"] = exit_context
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id="pos-test",
        gate_id=GATE_ADAPTIVE_EXIT,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=payload,
        started_ts=NOW,
        state=SignalLifecycle.MONITORED,
    )


async def test_g7_HOLD() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "HOLD", "new_exit_atr": 2.0, "reason": "no edge to widen",
    }))
    result = await adaptive_exit_gate(_ctx(_make_proposal()), client=haiku)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "gpt_p1"
    assert result.payload.get("widening_applied") is False


async def test_g7_WIDEN_accepted() -> None:
    """WIDEN with proposed stop FARTHER than current → ADJUST_EXIT applied."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "WIDEN", "new_exit_atr": 2.5, "reason": "winner extension",
    }))
    # Long: proposed 78_000 < current 79_000 = farther from price (widen).
    proposal = _make_proposal(
        current_stop=79_000.0, proposed_stop=78_000.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(_ctx(proposal), client=haiku)
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload.get("widening_applied") is True
    assert result.payload.get("new_exit_atr") == 2.5


async def test_g7_widen_only_if_farther_than_default() -> None:
    """WIDEN where proposed is NOT farther → reverse to HOLD (Q9 rail)."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "WIDEN", "reason": "tries to widen but stop is closer",
    }))
    # Long: proposed 79_500 > current 79_000 = CLOSER to price = not widening.
    proposal = _make_proposal(
        current_stop=79_000.0, proposed_stop=79_500.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(_ctx(proposal), client=haiku)
    assert result.decision == GateDecision.HOLD
    assert result.payload.get("reason") == "widen_not_farther"


async def test_g7_TIGHTEN_rejected_floor() -> None:
    """TIGHTEN that pulls stop CLOSER than current → HOLD (conservative trap)."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "TIGHTEN", "reason": "lock in some profit",
    }))
    # Long: proposed 79_500 > current 79_000 = closer = tighten.
    proposal = _make_proposal(
        current_stop=79_000.0, proposed_stop=79_500.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(_ctx(proposal), client=haiku)
    assert result.decision == GateDecision.HOLD
    assert result.payload.get("reason") == "tighten_rejected_floor"
    # Stop preserved at the default floor (current_stop).
    assert result.payload.get("stop_price") == 79_000.0


async def test_g7_EXIT_NOW() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "EXIT_NOW", "reason": "regime flip", "new_exit_atr": 0.0,
    }))
    result = await adaptive_exit_gate(_ctx(_make_proposal()), client=haiku)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.model_used == "gpt_p1"


async def test_g7_default_atr_floor() -> None:
    """No widen proposal → HOLD with current stop (default ATR floor)."""
    result = await adaptive_exit_gate(_ctx(None), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


async def test_g7_p0_no_client_uses_python_rails() -> None:
    """No GPT client → deterministic Q9 widening rail (P0 contract)."""
    proposal = _make_proposal(pnl_r=0.5)  # below widen window
    result = await adaptive_exit_gate(_ctx(proposal), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"
    assert result.payload.get("reason") == "below_widen_window"


async def test_g7_demo_context_in_prompt() -> None:
    haiku = _MockGPTClient(response_text='{"decision":"HOLD","reason":"x"}')
    await adaptive_exit_gate(_ctx(_make_proposal()), client=haiku)
    system_text = haiku.calls[0]["system"][0]["text"]
    assert "DEMO/PAPER" in system_text
    assert "Real-money safety arguments are INVALID" in system_text


async def test_g7_bad_decision_falls_through_to_python() -> None:
    """Unknown decision string → fall back to deterministic widening rail."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "MAYBE_TIGHTEN", "reason": "nonsense",
    }))
    result = await adaptive_exit_gate(_ctx(_make_proposal()), client=haiku)
    # Either ADJUST_EXIT (rail allows) or HOLD (rail blocks). Never KILL.
    assert result.decision in (GateDecision.ADJUST_EXIT, GateDecision.HOLD)


async def test_g7_decision_enum_is_canonical() -> None:
    assert G7_DECISION_ENUM == ["HOLD", "WIDEN", "TIGHTEN", "EXIT_NOW"]


async def test_g7_short_widen_direction() -> None:
    """For SHORT positions, WIDEN means proposed > current (farther above)."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "WIDEN", "reason": "short winner",
    }))
    proposal = _make_proposal(
        side="short", current_stop=81_000.0, proposed_stop=82_000.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(_ctx(proposal), client=haiku)
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload.get("widening_applied") is True


# ---------------------------------------------------------------------------
# #26 — rich-context enrichment surfaced in the G7 prompt
# ---------------------------------------------------------------------------

_RICH_EXIT_CONTEXT = {
    "regime": "bull_trend",
    "atr_pct": 0.006,
    "atr_slope": 0.0012,
    "volume_z": 2.1,
    "held_seconds": 420,
    "mfe_r": 1.8,
    "mae_r": -0.4,
    "exit_state": "protect",
    "peak_price": 88_500.0,
    "recent_close_first": 80_000.0,
    "recent_close_last": 84_500.0,
    "recent_close_n": 12,
}


async def test_g7_prompt_surfaces_rich_context() -> None:
    """When exit_context is present the user prompt names regime, MFE/MAE,
    exit_state, ATR trajectory, and recent price action so G7 can make a
    time/momentum/regime-aware decision instead of a HOLD-stamp."""
    haiku = _MockGPTClient(response_text='{"decision":"HOLD","reason":"x"}')
    await adaptive_exit_gate(
        _ctx(_make_proposal(), exit_context=_RICH_EXIT_CONTEXT), client=haiku,
    )
    user_text = haiku.calls[0]["messages"][0]["content"]
    assert "bull_trend" in user_text          # regime
    assert "MFE" in user_text                  # excursion label
    assert "1.8" in user_text                  # mfe_r value
    assert "protect" in user_text              # exit_state (FSM phase)
    assert "atr_slope" in user_text or "ATR" in user_text  # ATR trajectory
    assert "420" in user_text                  # held_seconds (timing)


async def test_g7_prompt_without_context_unchanged() -> None:
    """No exit_context → the prompt still carries the proposal + the Q9
    widening rails text (decision enum + WIDEN/TIGHTEN definitions)."""
    haiku = _MockGPTClient(response_text='{"decision":"HOLD","reason":"x"}')
    await adaptive_exit_gate(_ctx(_make_proposal()), client=haiku)
    user_text = haiku.calls[0]["messages"][0]["content"]
    assert "HOLD / WIDEN / TIGHTEN / EXIT_NOW" in user_text
    assert "FARTHER" in user_text
    assert "ATR floor" in user_text


async def test_g7_rich_context_does_not_change_widen_rail() -> None:
    """Enrichment is display-only: with rich context present the WIDEN rail
    fires identically (ADJUST_EXIT) to the no-context case."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "WIDEN", "new_exit_atr": 2.5, "reason": "winner extension",
    }))
    proposal = _make_proposal(
        current_stop=79_000.0, proposed_stop=78_000.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(
        _ctx(proposal, exit_context=_RICH_EXIT_CONTEXT), client=haiku,
    )
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload.get("widening_applied") is True


async def test_g7_rich_context_does_not_change_tighten_rail() -> None:
    """Enrichment is display-only: TIGHTEN trap reversal still fires to HOLD
    even with rich context present (aggressive-bias rail unchanged)."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "TIGHTEN", "reason": "lock in some profit",
    }))
    proposal = _make_proposal(
        current_stop=79_000.0, proposed_stop=79_500.0, pnl_r=1.2,
    )
    result = await adaptive_exit_gate(
        _ctx(proposal, exit_context=_RICH_EXIT_CONTEXT), client=haiku,
    )
    assert result.decision == GateDecision.HOLD
    assert result.payload.get("reason") == "tighten_rejected_floor"
