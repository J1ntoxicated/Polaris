"""Layer 2 — GPT gate unit tests (mocked OpenAI).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G1/G3/G4/G8 + Q6 prompts)

P2a (2026-07-16): G3's legacy GPT branch is deleted outright (group B) and
Gate 4 (Pre-Entry Watcher) is abolished as a decision step, its fast-path
content relocated verbatim into ``_g4_frontgate.py`` (group A) — see the G3
section below.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from polaris.core.pipeline.agents._g4_frontgate import (
    FastPathContext,
    is_fast_path_eligible,
)
from polaris.core.pipeline.agents.post_trade_reflector import (
    post_trade_reflector_gate,
)
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
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


async def test_signal_validator_gpt_client_never_called() -> None:
    """P2a group B: GPT removed from the G3 live path — the deterministic
    technical rule always drives, a supplied client is never invoked."""
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert haiku.calls == []
    assert result.model_used == "python"
    assert result.next_gate == GATE_ENTRY_SIZER  # G3->G5 direct (P2a group A)


async def test_signal_validator_no_client_still_flows() -> None:
    """No client at all → same deterministic flow (the legacy fail-closed
    ``no_gpt_client`` KILL only existed inside the now-deleted branch)."""
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.model_used == "python"
    assert result.decision in (GateDecision.PASS, GateDecision.MODIFY)


async def test_signal_validator_missing_raw_signal_kill() -> None:
    ctx = _ctx({}, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.decision == GateDecision.KILL
    assert result.payload["reason"] == "missing_raw_signal"


# ---------------------------------------------------------------------------
# G4 content, relocated into G3 (P2a group A) — crossed-book KILL rail
# ---------------------------------------------------------------------------


async def test_g3_crossed_book_kill_fail_closed() -> None:
    """Relocated G4 rail: a crossed book (bid >= ask) KILLs inside G3's flow —
    the pre-existing deterministic KILL set, not a new block."""
    payload = {
        "raw_signal": {"strategy": "vb", "score": 1.0},
        "tick_window": [{"ts": NOW, "bid": 100.2, "ask": 100.1, "mid": 100.15}],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.decision == GateDecision.KILL
    assert result.payload["reason"] == "crossed_book"


async def test_g3_clean_book_proceeds_deterministic() -> None:
    payload = {
        "raw_signal": {"strategy": "vb", "score": 1.0},
        "tick_window": [{"ts": NOW, "bid": 100.0, "ask": 100.1, "mid": 100.05}],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.decision in (GateDecision.PASS, GateDecision.MODIFY)
    assert result.next_gate == GATE_ENTRY_SIZER


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
