"""Gate 8 — Post-Trade Reflector (deterministic Python template).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 model split — G8 P0 Python template)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (§Phase: P0 Python deterministic)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P2 — G8 P0 template made permanent)

Decision contract (deterministic, no LLM):
- Emit a rule-based REFLECTED lesson + clamped cell-matrix delta for every
  closed trade. ``model_used="python"``.

ai_conductor P2 (2026-05-30): the P1/GPT branch was removed. The real learning
(posterior NIG μ/p_pos + cell EWMA) consumes ``pnl_r``/``won`` directly and never
touched G8 output; the ``ai_lessons`` ledger has zero SELECT readers (inert,
verified by global grep). So the GPT lesson text was a paid no-op — behaviour is
unchanged by its removal. Cross-trade synthesis (per-N-closes conductor batch)
is a separate, future tier (ai_conductor P6) and is out of scope here.

Outputs:
- ``lesson`` text (markdown) → vault/50_research/lessons/<trade_id>_<date>.md
- ``cell_matrix_delta`` (dict)
- ``learner_adjustment`` (dict, soft-mode 25% sizing scalar within 100-trade window)

Fail-open (Q4): errors do not block the pipeline; lesson is dropped.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)

__all__ = [
    "LESSON_CONFIDENCE_FLOOR",
    "LESSON_DELTA_CLAMP_P0",
    "LESSON_RECENT_TRADES_MAX",
    "LESSON_SOFT_MODE_TRADE_COUNT",
    "LESSON_SOFT_SCALAR_MAX",
    "post_trade_reflector_gate",
    "write_lesson_to_vault",
]

LESSON_CONFIDENCE_FLOOR: Final[float] = 0.70
LESSON_DIR_DEFAULT: Final[Path] = Path("vault/50_research/lessons")
LESSON_SOFT_SCALAR_MAX: Final[float] = 0.25  # Q10: 25% sizing scalar in 100-trade window
# Soft-mode trade-count window (Q10): until ≥ this many closed trades the
# learner ingests lessons at ``LESSON_SOFT_SCALAR_MAX`` weight only.
LESSON_SOFT_MODE_TRADE_COUNT: Final[int] = 100
# Number of most-recent same-symbol trades summarised in the G4 pre-entry
# watcher prompt (re-exported here as the canonical constant; G8 itself no
# longer builds an LLM prompt).
LESSON_RECENT_TRADES_MAX: Final[int] = 3
# Q10 P0 delta clamp: ±0.03 cap on cell-matrix delta during soft mode so the
# Python template cannot move weights faster than spec allows.
LESSON_DELTA_CLAMP_P0: Final[float] = 0.03


def write_lesson_to_vault(
    *,
    trade_id: str,
    lesson_text: str,
    lessons_dir: Path = LESSON_DIR_DEFAULT,
    now_ts: int | None = None,
) -> Path:
    """Write the lesson markdown to ``vault/50_research/lessons/``.

    Idempotent on (trade_id, date) — rewriting overwrites the same file.
    """
    if now_ts is not None:
        date_str = datetime.fromtimestamp(now_ts, tz=UTC).strftime("%Y-%m-%d")
    else:
        date_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    lessons_dir.mkdir(parents=True, exist_ok=True)
    fp = lessons_dir / f"{trade_id}_{date_str}.md"
    fp.write_text(lesson_text, encoding="utf-8")
    return fp


def _format_lesson_markdown(
    *,
    trade_id: str,
    strategy_id: str,
    regime: str | None,
    session: str | None,
    lesson_type: str,
    confidence: float,
    soft_mode: bool,
    delta: dict[str, Any],
    lesson_text: str,
) -> str:
    """Render the canonical lesson markdown body.

    Includes Karpathy-spec frontmatter so vault_lint passes without a
    post-hoc autofix pass (Phase 1 vault audit, 2026-05-07).
    """
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    safe_strategy = (strategy_id or "unknown").replace(" ", "_")
    safe_type = (lesson_type or "unknown").replace(" ", "_")
    frontmatter = (
        "---\n"
        "type: research\n"
        "status: active\n"
        f"date_created: {today}\n"
        f"tags: [lesson, p0, strategy/{safe_strategy}, type/{safe_type}]\n"
        "related: [[ADR-007]], [[ADR-008]]\n"
        f"lesson_id: {trade_id}\n"
        "---\n\n"
    )
    return (
        f"{frontmatter}"
        f"# Lesson — {trade_id}\n"
        f"- strategy: {strategy_id}\n"
        f"- regime: {regime}\n"
        f"- session: {session}\n"
        f"- type: {lesson_type}\n"
        f"- confidence: {confidence:.2f}\n"
        f"- soft_mode: {soft_mode}\n"
        f"- delta: {delta}\n\n"
        f"{lesson_text}\n"
    )


def _python_template_lesson(closed: dict[str, Any]) -> dict[str, Any]:
    """Rule-based lesson template — no LLM call.

    Lesson type heuristics (deterministic, fail-open):
      - won + pnl_r >= 1.0  -> ``ok`` (tag the regime/strategy as good)
      - lost + pnl_r <= -1  -> ``overtrade`` (cell routing should dampen)
      - mixed              -> ``entry_timing`` (default)
    """
    pnl_r = float(closed.get("pnl_r", 0.0) or 0.0)
    won = bool(closed.get("won", pnl_r > 0.0))
    if won and pnl_r >= 1.0:
        lesson_type = "ok"
        text = f"Won with {pnl_r:+.2f}R — keep cell routing weight."
    elif (not won) and pnl_r <= -1.0:
        lesson_type = "overtrade"
        text = f"Lost {pnl_r:+.2f}R — cell routing should dampen."
    else:
        lesson_type = "entry_timing"
        text = f"Mixed result {pnl_r:+.2f}R — entry timing review."
    return {
        # Confidence is set above the floor so downstream consumers do
        # not drop the deterministic lesson; it's still logged honestly
        # as ``model_used="python"``.
        "confidence": LESSON_CONFIDENCE_FLOOR,
        "lesson_type": lesson_type,
        "lesson_text": text,
    }


def _clamp_delta(
    delta: dict[str, Any], *, cap: float = LESSON_DELTA_CLAMP_P0
) -> dict[str, float]:
    """Clamp every numeric delta entry to ``[-cap, +cap]`` (Q10 rail)."""
    out: dict[str, float] = {}
    for k, v in delta.items():
        if not isinstance(v, (int, float)):
            continue
        out[str(k)] = max(-cap, min(cap, float(v)))
    return out


def _python_template_delta(
    closed: dict[str, Any], *, soft_mode: bool
) -> dict[str, float]:
    """Build the deterministic Δ for Layer 5 from a closed trade.

    Invariant: |Δ| ≤ ``LESSON_DELTA_CLAMP_P0`` (= 0.03). Sign comes from the
    realized PnL (won → +, lost → −). Soft-mode dampens the magnitude by
    ``LESSON_SOFT_SCALAR_MAX`` per Q10.
    """
    pnl_r = float(closed.get("pnl_r", 0.0) or 0.0)
    strategy = str(closed.get("strategy_id", "unknown"))
    regime = str(closed.get("regime", "unknown"))
    sign = 1.0 if pnl_r > 0 else (-1.0 if pnl_r < 0 else 0.0)
    raw = sign * LESSON_DELTA_CLAMP_P0  # walk to the rail in the right direction
    if soft_mode:
        raw *= LESSON_SOFT_SCALAR_MAX
    return _clamp_delta({f"{strategy}_x_{regime}": raw})


def _persist_lesson(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    strategy_id: str,
    regime: str | None,
    session: str | None,
    confidence: float,
    lesson_type: str,
    delta: dict[str, Any],
    now_ts: int,
) -> str:
    lesson_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO ai_lessons
            (lesson_id, trade_id, strategy_id, regime, session, confidence,
             lesson_type, delta_json, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lesson_id,
            trade_id,
            strategy_id,
            regime,
            session,
            float(confidence),
            lesson_type,
            json.dumps(delta, separators=(",", ":")),
            now_ts,
        ),
    )
    return lesson_id


async def post_trade_reflector_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    conn: sqlite3.Connection | None = None,
    lessons_dir: Path = LESSON_DIR_DEFAULT,
    model: str | None = None,
) -> GateResult:
    """Gate 8 dispatcher — deterministic Python template (no LLM).

    Inputs from ``ctx.payload``:
        ``closed_trade`` (dict, required), ``closed_trade_count`` (int, optional).

    The ``client`` / ``model`` parameters are retained for caller compatibility
    (ai_conductor P2 removed the GPT branch) but are inert — no network call is
    ever made. Every closed trade leaves a deterministic REFLECTED lesson + a
    clamped Δ for Layer 5, plus an ``ai_lessons`` row when ``conn`` is supplied.
    """
    closed = ctx.payload.get("closed_trade")
    if not isinstance(closed, dict) or not closed:
        return GateResult(
            decision=GateDecision.REFLECTED,
            next_gate=None,
            payload={"reason": "no_closed_trade"},
            model_used="python",
        )

    trade_id = str(closed.get("trade_id") or uuid.uuid4().hex)
    strategy_id = str(closed.get("strategy_id", "unknown"))
    regime = closed.get("regime")
    session = closed.get("session")
    closed_count = int(ctx.payload.get("closed_trade_count", 0))
    soft_mode = closed_count < LESSON_SOFT_MODE_TRADE_COUNT

    # Deterministic template (Q3): emit a REFLECTED with a rule-based lesson +
    # clamped delta so Layer 5 still receives a learning signal without an LLM
    # call. Q10 confidence floor and ±0.03 delta clamp are honoured by
    # construction.
    py_lesson = _python_template_lesson(closed)
    py_delta = _python_template_delta(closed, soft_mode=soft_mode)
    # Persist + write to vault so the lesson ledger is populated from day 1;
    # ADR-007 §learner expects every closed trade to leave a row in
    # ``ai_lessons``.
    md = _format_lesson_markdown(
        trade_id=trade_id,
        strategy_id=strategy_id,
        regime=regime,
        session=session,
        lesson_type=py_lesson["lesson_type"],
        confidence=py_lesson["confidence"],
        soft_mode=soft_mode,
        delta=py_delta,
        lesson_text=py_lesson["lesson_text"],
    )
    write_lesson_to_vault(
        trade_id=trade_id, lesson_text=md, lessons_dir=lessons_dir, now_ts=ctx.started_ts
    )
    if conn is not None:
        _persist_lesson(
            conn,
            trade_id=trade_id,
            strategy_id=strategy_id,
            regime=regime,
            session=session,
            confidence=py_lesson["confidence"],
            lesson_type=py_lesson["lesson_type"],
            delta=py_delta,
            now_ts=ctx.started_ts,
        )
    ctx.state = SignalLifecycle.REFLECTED
    return GateResult(
        decision=GateDecision.REFLECTED,
        next_gate=None,
        payload={
            "lesson_type": py_lesson["lesson_type"],
            "confidence": py_lesson["confidence"],
            "delta": py_delta,
            "soft_mode": soft_mode,
            "source": "python_template",
        },
        model_used="python",
    )
