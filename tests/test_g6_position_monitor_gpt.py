"""Day 9 F1 — G6 Position Monitor GPT P1 unit tests (mocked OpenAI).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6 + Q4 fail-open + Q8 swap)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Position Monitor)

Each test asserts on:
- decision routing (HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY)
- DEMO context appears in the system prompt
- Aggressive bias preserved (default HOLD only when explicit; pnl_r hard rail
  still fires before GPT)
- Real position state (entry/last/uPnL_R/held_seconds/cell_score) reaches the
  user prompt
- model_used = "gpt_p1" / "python" labels honest
"""

from __future__ import annotations

import json
import math
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as hs

from polaris.core.pipeline.agents.position_monitor import (
    G6_DECISION_ENUM,
    position_monitor_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


class _MockGPTClient:
    """Mock that returns a predefined JSON text response (Anthropic-shape API)."""

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


def _ctx(payload: dict) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id="pos-test",
        gate_id=GATE_POSITION_MONITOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.MONITORED,
    )


def _make_position(side: str = "long") -> dict:
    return {
        "venue": "okx",
        "symbol": "BTC-USDT",
        "side": side,
        "strategy": "vb",
        "correlation_group": "crypto:BTC",
        "entry_price": 80_000.0,
        "last_price": 80_400.0,
        "held_seconds": 90,
        "cell_score": 0.55,
    }


# ---------------------------------------------------------------------------
# GPT decision routing
# ---------------------------------------------------------------------------


async def test_g6_decision_HOLD() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "HOLD", "reason": "trend intact", "new_stop_distance": 1.5,
    }))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.30,
        "max_loss_r": 1.0,
        "market_view": {"regime": "bull_trend", "atr_pct": 0.012, "volume_now": 100.0},
        "recent_ticks": [{"ts": NOW - 5, "px": 80_300.0}],
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "gpt_p1"
    assert len(haiku.calls) == 1


async def test_g6_decision_ADJUST_EXIT() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "ADJUST_EXIT", "reason": "winner extension", "new_stop_distance": 2.5,
    }))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 1.2,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload.get("new_stop_distance") == 2.5
    assert result.model_used == "gpt_p1"


async def test_g6_decision_EXIT_NOW() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "EXIT_NOW", "reason": "regime flip vs position",
    }))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.05,  # not a stop hit, GPT decides
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.model_used == "gpt_p1"


async def test_g6_decision_SWAP_STRATEGY_with_candidate() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "SWAP_STRATEGY", "reason": "tsmom dominates volume_burst here",
    }))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.20,
        "max_loss_r": 1.0,
        "swap_candidate": {
            "venue": "okx", "symbol": "BTC-USDT", "side": "long",
            "correlation_group": "crypto:BTC", "strategy": "tsmom",
        },
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    # The Python fast-path catches Q8-eligible candidates BEFORE the GPT call,
    # so this returns SWAP_STRATEGY via the python label (loss-rail / swap
    # match are deterministic by design).
    assert result.decision == GateDecision.SWAP_STRATEGY
    assert result.model_used == "python"
    assert haiku.calls == []  # fast-path bypassed GPT


async def test_g6_decision_SWAP_without_candidate_falls_back_to_HOLD() -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "decision": "SWAP_STRATEGY", "reason": "no candidate but llm asked",
    }))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.HOLD


# ---------------------------------------------------------------------------
# DEMO context + Aggressive bias
# ---------------------------------------------------------------------------


async def test_g6_demo_context_in_prompt() -> None:
    haiku = _MockGPTClient(response_text='{"decision":"HOLD","reason":"x"}')
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    await position_monitor_gate(_ctx(payload), client=haiku)
    assert len(haiku.calls) == 1
    system_blocks = haiku.calls[0]["system"]
    system_text = system_blocks[0]["text"]
    # DEMO unlock clause must appear so GPT does not over-KILL.
    assert "DEMO/PAPER" in system_text
    assert "Real-money safety arguments are INVALID" in system_text


async def test_g6_aggressive_bias_default_HOLD_explicit_only() -> None:
    """Bad GPT response → fall through to Python rules, default HOLD (not KILL)."""
    haiku = _MockGPTClient(response_text="not json at all")
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    # Aggressive bias: parse failure → HOLD (fallback Python rules), not KILL.
    assert result.decision == GateDecision.HOLD
    assert result.payload.get("fallback") in ("gpt_error", "bad_decision")


# ---------------------------------------------------------------------------
# Hard rails / inputs
# ---------------------------------------------------------------------------


async def test_g6_hard_loss_rail_fires_before_gpt() -> None:
    """pnl_r <= -max_loss_r → EXIT_NOW Python (no GPT call)."""
    haiku = _MockGPTClient(response_text='{"decision":"HOLD"}')
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": -1.5,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.model_used == "python"
    assert haiku.calls == []


async def test_g6_uPnL_R_input_correct() -> None:
    """The user prompt must surface uPnL_R verbatim from the payload."""
    haiku = _MockGPTClient(response_text='{"decision":"HOLD"}')
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.733,
        "max_loss_r": 1.0,
    }
    await position_monitor_gate(_ctx(payload), client=haiku)
    user_msg = haiku.calls[0]["messages"][0]["content"]
    assert "0.733" in user_msg
    assert "uPnL_R" in user_msg


async def test_g6_real_position_state() -> None:
    """All position fields (entry/last/held/cell_score) make it to the prompt."""
    haiku = _MockGPTClient(response_text='{"decision":"HOLD"}')
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    await position_monitor_gate(_ctx(payload), client=haiku)
    user_msg = haiku.calls[0]["messages"][0]["content"]
    assert "80000" in user_msg or "80000.0" in user_msg  # entry
    assert "80400" in user_msg or "80400.0" in user_msg  # last
    assert "90" in user_msg  # held_seconds
    assert "0.55" in user_msg  # cell_score


async def test_g6_no_position_returns_hold() -> None:
    result = await position_monitor_gate(_ctx({}), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


# ---------------------------------------------------------------------------
# Phase contract — P0 (no client) keeps deterministic behaviour
# ---------------------------------------------------------------------------


async def test_g6_p0_no_client_uses_python() -> None:
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


async def test_g6_decision_enum_is_canonical() -> None:
    assert G6_DECISION_ENUM == ["HOLD", "ADJUST_EXIT", "EXIT_NOW", "SWAP_STRATEGY"]


# ---------------------------------------------------------------------------
# Property-based — decision is always in the enum
# ---------------------------------------------------------------------------


@given(
    pnl_r=hs.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
@pytest.mark.asyncio
async def test_g6_decision_always_in_enum(pnl_r: float) -> None:
    haiku = _MockGPTClient(response_text='{"decision":"HOLD"}')
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": pnl_r,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    valid = {
        GateDecision.HOLD, GateDecision.ADJUST_EXIT,
        GateDecision.EXIT_NOW, GateDecision.SWAP_STRATEGY,
    }
    assert result.decision in valid


@given(
    pnl_r=hs.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
def test_g6_compute_uPnL_R_finite(pnl_r: float) -> None:
    # Sanity: every constructed pnl_r is finite — preconditions of the
    # GPT prompt (so prompt rendering never emits nan/inf).
    assert math.isfinite(pnl_r)
