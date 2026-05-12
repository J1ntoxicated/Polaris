"""Day 9 F1 — G8 Post-Trade Reflector phase=P1 default tests.

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G8 P1 = GPT)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (§Phase P0/P1)

Phase contract:
- production_paper_loop default phase = "P1" so G8 emits real GPT lessons.
- close_oldest_with_real_pnl(phase="P1", gpt_client=...) wires the GPT
  client through to ``post_trade_reflector_gate``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from polaris.core.pipeline.agents._gpt_client import GPT_P1_MODEL
from polaris.core.pipeline.agents.post_trade_reflector import (
    post_trade_reflector_gate,
)
from polaris.core.pipeline.gate_state import (
    GATE_POST_TRADE_REFLECTOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.scripts.production_paper_loop import run_production_paper_loop  # noqa: F401

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


def _ctx(payload: dict) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id="pos-test",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.CLOSED,
    )


async def test_g8_phase_p1_real_gpt_call(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "exit_patience",
        "confidence": 0.82,
        "lesson_text": "Held to second target — winner extension paid off.",
        "delta": {"vb_x_bull_trend": 0.05},
    }))
    closed_trade = {
        "trade_id": "trade-p1",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 1.6,
        "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 200})
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path,
        model=GPT_P1_MODEL,
    )
    assert result.decision == GateDecision.REFLECTED
    assert result.model_used == "gpt_p1"
    assert result.payload["confidence"] == 0.82
    # Real GPT call happened.
    assert len(haiku.calls) == 1


async def test_g8_lesson_markdown_write(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    """Real GPT lesson is written to vault/50_research/lessons/<trade>_<date>.md."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "ok",
        "confidence": 0.90,
        "lesson_text": "## Lesson\nWinner held to plan.",
        "delta": {"vb_x_bull_trend": 0.04},
    }))
    closed_trade = {
        "trade_id": "trade-md",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 2.0,
        "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 200})
    await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path,
        model=GPT_P1_MODEL,
    )
    files = list(tmp_path.glob("trade-md_*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "Winner held to plan" in content
    # Karpathy frontmatter
    assert content.startswith("---\n")
    assert "tags: [lesson" in content


async def test_g8_cell_delta_persist(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "entry_timing",
        "confidence": 0.85,
        "lesson_text": "x",
        "delta": {"vb_x_bull_trend": 0.06},
    }))
    closed_trade = {
        "trade_id": "trade-delta",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "pnl_r": 0.8,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 200})
    await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path,
        model=GPT_P1_MODEL,
    )
    row = memdb.execute(
        "SELECT lesson_type, delta_json FROM ai_lessons WHERE trade_id = ?",
        ("trade-delta",),
    ).fetchone()
    assert row is not None
    assert row[0] == "entry_timing"
    delta = json.loads(row[1])
    # P1 cap = ±0.10 once past soft mode (>=100 trades).
    assert abs(delta["vb_x_bull_trend"]) <= 0.10
    assert delta["vb_x_bull_trend"] > 0.0


async def test_g8_learner_adjustment_recommendation(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    """G8 result payload exposes soft_mode + delta for downstream learners."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "ok",
        "confidence": 0.80,
        "lesson_text": "y",
        "delta": {"vb_x_asia": 0.03},
    }))
    closed_trade = {
        "trade_id": "trade-soft",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 1.0,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 5})
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path,
        model=GPT_P1_MODEL,
    )
    assert result.payload["soft_mode"] is True
    assert "delta" in result.payload


async def test_paper_loop_default_phase_is_p1() -> None:
    """Production paper loop CLI defaults to phase=P1 (Day 9 mandate)."""
    import inspect

    sig = inspect.signature(run_production_paper_loop)
    assert sig.parameters["phase"].default == "P1"
