"""Layer 2 — GPT gate unit tests (mocked OpenAI).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G1/G3/G4/G8 + Q6 prompts)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from polaris.core.pipeline.agents.post_trade_reflector import (
    post_trade_reflector_gate,
)
from polaris.core.pipeline.agents.pre_entry_watcher import (
    FastPathContext,
    is_fast_path_eligible,
    pre_entry_watcher_gate,
)
from polaris.core.pipeline.agents.signal_validator import (
    MODIFY_MAX,
    MODIFY_MIN,
    signal_validator_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000



@pytest.fixture(autouse=True)
def _legacy_gpt_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """W3 AI-free cutover adaptation (NOT a behavior change): this module pins
    the LEGACY GPT path, so force POLARIS_AI_FREE=0 explicitly (the flag now
    defaults ON). The flag=1 deterministic-primary path is covered by
    tests/test_ai_free_cutover.py."""
    monkeypatch.setenv("POLARIS_AI_FREE", "0")

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


def _ctx(payload: dict, *, gate_id: int) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id=None,
        gate_id=gate_id,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


# ---------------------------------------------------------------------------
# Signal Validator (G3)
# ---------------------------------------------------------------------------


async def test_signal_validator_pass() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PASS
    assert result.next_gate == GATE_PRE_ENTRY_WATCHER
    assert result.payload["validated_signal"]["strength_scalar"] == 1.0


async def test_signal_validator_kill() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 0.3}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL
    assert result.next_gate is None


async def test_signal_validator_modify_clamps_to_range() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "MODIFY", "strength_scalar": 5.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.MODIFY
    assert MODIFY_MIN <= result.payload["validated_signal"]["strength_scalar"] <= MODIFY_MAX


async def test_signal_validator_no_client_kill() -> None:
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.decision == GateDecision.KILL


async def test_signal_validator_schema_violation_kill() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "MAYBE"}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL


# ---------------------------------------------------------------------------
# Pre-Entry Watcher (G4)
# ---------------------------------------------------------------------------


async def test_pre_entry_watcher_proceed() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "PROCEED"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.0, "strategy": "vb"},
        "tick_window": [],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=4)
    result = await pre_entry_watcher_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PROCEED


async def test_pre_entry_watcher_kill_fail_closed() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.0, "strategy": "vb"},
        "tick_window": [],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=4)
    result = await pre_entry_watcher_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL


def test_fast_path_eligibility_top_quartile_ok() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert is_fast_path_eligible(fp)


def test_fast_path_eligibility_listing_too_young_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=12.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


def test_fast_path_eligibility_strength_too_low_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.10,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


def test_fast_path_eligibility_spread_wide_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=4.5,  # > 4.0 * 0.9 = 3.6
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


# ---------------------------------------------------------------------------
# Post-Trade Reflector (G8)
# ---------------------------------------------------------------------------


async def test_post_trade_reflector_persists_ai_lessons_row(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    """ai_conductor P2: deterministic template persists an ai_lessons row (SSOT).

    GPT branch removed — a supplied client is inert. A losing trade
    (pnl_r=-0.5, mixed) → ``entry_timing`` at the confidence floor. No vault
    .md is written (per-trade export removed 2026-06-02).
    """
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "GPT_VALUE_IGNORED",
        "confidence": 0.85,
        "lesson_text": "GPT text that must never appear.",
        "delta": {"vb_x_bull_trend": 0.02},
    }))
    closed_trade = {
        "trade_id": "trade-1",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": -0.5,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 50}, gate_id=8)
    result = await post_trade_reflector_gate(ctx, client=haiku, conn=memdb)
    assert result.decision == GateDecision.REFLECTED
    assert result.model_used == "python"
    assert haiku.calls == []  # GPT never called
    # Deterministic: mixed result → entry_timing at the confidence floor.
    assert result.payload["lesson_type"] == "entry_timing"
    assert result.payload["confidence"] == 0.70
    # No vault .md written.
    assert list(tmp_path.rglob("*.md")) == []
    # ai_lessons row persisted.
    row = memdb.execute("SELECT lesson_type, confidence FROM ai_lessons").fetchone()
    assert row is not None
    assert row[0] == "entry_timing"


async def test_post_trade_reflector_always_persists_no_drop(
    memdb: sqlite3.Connection,
) -> None:
    """ai_conductor P2: the deterministic template never drops a lesson.

    The old GPT low-confidence / malformed-confidence drop branches are gone —
    every closed trade leaves exactly one ai_lessons row at the confidence floor.
    """
    closed_trade = {"trade_id": "trade-keep", "strategy_id": "vb", "pnl_r": 0.0}
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 50}, gate_id=8)
    result = await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    assert result.decision == GateDecision.REFLECTED
    assert result.payload["confidence"] == 0.70
    assert "reason" not in result.payload  # not a drop
    n = memdb.execute("SELECT COUNT(*) FROM ai_lessons").fetchone()[0]
    assert n == 1


async def test_post_trade_reflector_soft_mode_dampens_delta(
    memdb: sqlite3.Connection,
) -> None:
    """Soft mode (n<100) dampens the deterministic Δ to 25% of the P0 rail."""
    closed_trade = {
        "trade_id": "trade-3", "strategy_id": "vb", "regime": "asia",
        "pnl_r": 1.0, "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 10}, gate_id=8)
    result = await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    # Soft mode = 25% of the +0.03 P0 rail = +0.0075.
    assert result.payload["delta"]["vb_x_asia"] == pytest.approx(0.03 * 0.25)
    assert result.payload["soft_mode"] is True
