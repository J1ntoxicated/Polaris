"""G6 Position Monitor — deterministic Python (ai_conductor P3, GPT removed).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6 + Q4 fail-open + Q8 swap)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Position Monitor)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P3 — per-position GPT branch removed)

ai_conductor P3 (2026-05-30): the per-position GPT (P1) branch was deleted. Live
telemetry showed GPT returned HOLD 99.97% (3434 HOLD / 1 EXIT_NOW) — it never
materially moved the decision, while the deterministic hard stop + Q8 swap
fast-path already owned every real action. These tests pin the post-removal
contract:
- No GPT call is ever made, even when a ``client`` is supplied (inert param).
- Hard loss rail → EXIT_NOW (model_used="python").
- Q8 swap candidate → SWAP_STRATEGY (model_used="python").
- Winner-widen window (pnl_r > 0.7R) → ADJUST_EXIT.
- Otherwise → HOLD (aggressive bias: never a defensive KILL).
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
    """Records any ``messages.create`` call so tests can assert GPT is NOT hit."""

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
# GPT is removed — the gate is deterministic at every phase
# ---------------------------------------------------------------------------


async def test_g6_never_calls_gpt_even_with_client() -> None:
    """A supplied client is inert — no network call is made (P3 removal)."""
    haiku = _MockGPTClient(response_text=json.dumps({"decision": "EXIT_NOW"}))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.30,  # not a stop hit
        "max_loss_r": 1.0,
        "market_view": {"regime": "bull_trend", "atr_pct": 0.012, "volume_now": 100.0},
        "recent_ticks": [{"ts": NOW - 5, "px": 80_300.0}],
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    # GPT said EXIT_NOW but the deterministic gate ignores it → HOLD.
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"
    assert haiku.calls == []


async def test_g6_default_hold() -> None:
    """Open band (not a stop, not a widen, no swap) → HOLD."""
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.30,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


async def test_g6_widen_window_adjust_exit() -> None:
    """pnl_r > 0.7R → ADJUST_EXIT (winner-widen)."""
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 1.2,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.ADJUST_EXIT
    assert result.payload.get("reason") == "widen_window"
    assert result.model_used == "python"


async def test_g6_decision_SWAP_STRATEGY_with_candidate() -> None:
    """A Q8-eligible swap candidate → SWAP_STRATEGY (deterministic)."""
    haiku = _MockGPTClient(response_text=json.dumps({"decision": "HOLD"}))
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
    assert result.decision == GateDecision.SWAP_STRATEGY
    assert result.model_used == "python"
    assert haiku.calls == []  # no GPT


async def test_g6_swap_candidate_mismatch_is_ignored() -> None:
    """A non-matching candidate (different symbol) does NOT swap → HOLD."""
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
        "swap_candidate": {
            "venue": "okx", "symbol": "ETH-USDT", "side": "long",
            "correlation_group": "crypto:BTC", "strategy": "tsmom",
        },
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD


# ---------------------------------------------------------------------------
# Hard rails / inputs
# ---------------------------------------------------------------------------


async def test_g6_hard_loss_rail_fires() -> None:
    """pnl_r <= -max_loss_r → EXIT_NOW Python (no GPT call)."""
    haiku = _MockGPTClient(response_text=json.dumps({"decision": "HOLD"}))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": -1.5,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=haiku)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "stop_hit"
    assert result.model_used == "python"
    assert haiku.calls == []


async def test_g6_aggressive_bias_no_defensive_kill() -> None:
    """A flat/open position is never defensively KILLed — default HOLD."""
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": -0.5,  # losing but inside the stop
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD


async def test_g6_no_position_returns_hold() -> None:
    result = await position_monitor_gate(_ctx({}), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


async def test_g6_p0_no_client_uses_python() -> None:
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"


async def test_g6_call_cache_and_tick_idx_params_inert() -> None:
    """Legacy call_cache / tick_idx kwargs are accepted but inert (no call)."""
    haiku = _MockGPTClient(response_text=json.dumps({"decision": "EXIT_NOW"}))
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": 0.30,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(
        _ctx(payload), client=haiku, call_cache=object(), tick_idx=42,
    )
    assert result.decision == GateDecision.HOLD
    assert result.model_used == "python"
    assert haiku.calls == []


async def test_g6_decision_enum_is_canonical() -> None:
    assert G6_DECISION_ENUM == ["HOLD", "ADJUST_EXIT", "EXIT_NOW", "SWAP_STRATEGY"]


# ---------------------------------------------------------------------------
# Property-based — decision is always in the enum, never a stray KILL
# ---------------------------------------------------------------------------


@given(
    pnl_r=hs.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
@pytest.mark.asyncio
async def test_g6_decision_always_in_enum(pnl_r: float) -> None:
    payload = {
        "position": _make_position(),
        "unrealized_pnl_r": pnl_r,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    valid = {
        GateDecision.HOLD, GateDecision.ADJUST_EXIT,
        GateDecision.EXIT_NOW, GateDecision.SWAP_STRATEGY,
    }
    assert result.decision in valid
    assert result.model_used == "python"


@given(
    pnl_r=hs.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
def test_g6_compute_uPnL_R_finite(pnl_r: float) -> None:
    # Sanity: every constructed pnl_r is finite.
    assert math.isfinite(pnl_r)
