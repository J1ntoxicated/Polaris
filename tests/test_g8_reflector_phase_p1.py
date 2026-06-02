"""G8 Post-Trade Reflector — deterministic Python template (ai_conductor P2).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G8 P0 Python template)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (§Phase P0/P1)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P2 — G8 P0 template made permanent)

ai_conductor P2 (2026-05-30): the P1/GPT lesson branch was removed. The real
learning (posterior NIG μ/p_pos + cell EWMA) consumes pnl_r/won directly and
never touched G8 output; the ``ai_lessons`` ledger has zero SELECT readers
(inert), so the GPT lesson text was a paid no-op. These tests pin the
post-removal contract:
- No GPT call is ever made, even when a ``client`` is supplied (inert param).
- Every closed trade still emits a deterministic REFLECTED lesson + clamped Δ
  and persists an ``ai_lessons`` row (SSOT). No vault markdown is written
  (per-trade .md export removed 2026-06-02 — it was telemetry spam).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from polaris.core.pipeline.agents.post_trade_reflector import (
    LESSON_DELTA_CLAMP_P0,
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
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.CLOSED,
    )


async def test_g8_never_calls_gpt_even_with_client(
    memdb: sqlite3.Connection,
) -> None:
    """A supplied client is inert — no network call is made (P2 removal)."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "exit_patience",
        "confidence": 0.82,
        "lesson_text": "GPT text that must never appear.",
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
        ctx, client=haiku, conn=memdb,
    )
    assert result.decision == GateDecision.REFLECTED
    assert result.model_used == "python"
    assert result.payload["source"] == "python_template"
    # Deterministic template: won + pnl_r >= 1.0 → "ok".
    assert result.payload["lesson_type"] == "ok"
    assert haiku.calls == []  # GPT never called


async def test_g8_no_vault_markdown_written(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    """Per-trade vault .md export removed (2026-06-02): DB row is the only output."""
    closed_trade = {
        "trade_id": "trade-md",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 2.0,
        "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 200})
    await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    # No markdown is written anywhere under the cwd-tmp tree.
    assert list(tmp_path.rglob("*.md")) == []
    # ai_lessons row IS persisted (SSOT, A3 raw data).
    row = memdb.execute(
        "SELECT lesson_type FROM ai_lessons WHERE trade_id = ?", ("trade-md",)
    ).fetchone()
    assert row is not None
    assert row[0] == "ok"


async def test_g8_cell_delta_persist(memdb: sqlite3.Connection) -> None:
    """A winning trade persists a positive, clamped Δ to ai_lessons."""
    closed_trade = {
        "trade_id": "trade-delta",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "pnl_r": 0.8,
        "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 200})
    await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    row = memdb.execute(
        "SELECT lesson_type, delta_json FROM ai_lessons WHERE trade_id = ?",
        ("trade-delta",),
    ).fetchone()
    assert row is not None
    delta = json.loads(row[1])
    # Past soft mode (>=100 trades): full ±0.03 P0 clamp, positive sign.
    assert delta["vb_x_bull_trend"] == LESSON_DELTA_CLAMP_P0
    assert delta["vb_x_bull_trend"] > 0.0


async def test_g8_soft_mode_dampens_delta(memdb: sqlite3.Connection) -> None:
    """G8 result payload exposes soft_mode + dampened delta during warm-up."""
    closed_trade = {
        "trade_id": "trade-soft",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 1.0,
        "won": True,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 5})
    result = await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    assert result.payload["soft_mode"] is True
    # Soft mode dampens to 25% of the P0 rail.
    delta = result.payload["delta"]
    assert delta["vb_x_bull_trend"] == LESSON_DELTA_CLAMP_P0 * 0.25


async def test_g8_no_closed_trade_is_noop(memdb: sqlite3.Connection) -> None:
    ctx = _ctx({})
    result = await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    assert result.decision == GateDecision.REFLECTED
    assert result.payload["reason"] == "no_closed_trade"
    assert result.model_used == "python"


async def test_paper_loop_default_phase_is_p1() -> None:
    """Production paper loop CLI still defaults to phase=P1 (unchanged)."""
    import inspect

    sig = inspect.signature(run_production_paper_loop)
    assert sig.parameters["phase"].default == "P1"
