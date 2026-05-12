"""Gate 8 — Post-Trade Reflector (P0 = Python template, P1 = GPT-5.5).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 model split — G8 P0 Python template)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (§Phase: P0 Python deterministic)

Phase contract:
- **P0**: ``client is None`` (enforced by ``GateOrchestrator(phase="P0")``);
  the gate emits a deterministic Python template REFLECTED result —
  ``model_used="python"`` — and never opens a network call.
- **P1**: caller passes a GPT ``client`` and the gate enriches the Python
  template with model-derived lesson text + delta. Confidence guard
  (Q10: ``confidence < 0.70`` → lesson dropped) applies only to the
  LLM-driven branch.

Outputs:
- ``lesson`` text (markdown) → vault/50_research/lessons/<trade_id>_<date>.md
- ``cell_matrix_delta`` (dict)
- ``learner_adjustment`` (dict, soft-mode 25% sizing scalar within 100-trade window)

Fail-open (Q4): errors do not block the pipeline; lesson is dropped.

Migration (2026-05-07): Anthropic Haiku/Sonnet → OpenAI GPT (Jin mandate).
P1 model is now ``gpt-5.5`` (was ``claude-sonnet-*``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPT_P0_MODEL,
    GPT_P1_MODEL,
    GPTCallResult,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)

__all__ = [
    "LESSON_CONFIDENCE_FLOOR",
    "LESSON_DELTA_CLAMP_P0",
    "LESSON_DELTA_CLAMP_P1",
    "LESSON_MAX_TOKENS",
    "LESSON_PROMPT_TRADE_TRUNCATE_CHARS",
    "LESSON_RECENT_TRADES_MAX",
    "LESSON_SOFT_MODE_TRADE_COUNT",
    "LESSON_SOFT_SCALAR_MAX",
    "post_trade_reflector_gate",
    "write_lesson_to_vault",
]

LESSON_CONFIDENCE_FLOOR: Final[float] = 0.70
LESSON_DIR_DEFAULT: Final[Path] = Path("vault/50_research/lessons")
LESSON_SOFT_SCALAR_MAX: Final[float] = 0.25  # Q10: 25% sizing scalar in 100-trade window
# Cap closed-trade JSON size in the user prompt — keeps Haiku/Sonnet input
# token budget bounded (~1.5k chars ≈ 400 tokens) regardless of payload size.
LESSON_PROMPT_TRADE_TRUNCATE_CHARS: Final[int] = 1500
# Token budget for the lesson response — Q6 G8 spec (lesson + delta JSON).
LESSON_MAX_TOKENS: Final[int] = 400
# Soft-mode trade-count window (Q10): until ≥ this many closed trades the
# learner ingests lessons at ``LESSON_SOFT_SCALAR_MAX`` weight only.
LESSON_SOFT_MODE_TRADE_COUNT: Final[int] = 100
# Number of most-recent same-symbol trades summarised in the prompt.
LESSON_RECENT_TRADES_MAX: Final[int] = 3
# Q10 P0 delta clamp: ±0.03 cap on cell-matrix delta during soft mode so
# Python template (and any LLM-driven branch with no client) cannot move
# weights faster than spec allows. P1 caller widens to ADR-007 ±0.10.
LESSON_DELTA_CLAMP_P0: Final[float] = 0.03
# Q10 P1 delta clamp: ±0.10 (ADR-007 §learner_delta range — wider band
# unlocked once a Haiku/Sonnet client is wired and ≥ 100 trades closed).
LESSON_DELTA_CLAMP_P1: Final[float] = 0.10


def _build_user_prompt(closed_trade: dict[str, Any]) -> str:
    truncated = json.dumps(closed_trade, default=str)[:LESSON_PROMPT_TRADE_TRUNCATE_CHARS]
    return (
        f"# Closed trade\n{truncated}\n\n"
        'Output JSON: {"lesson_type": "entry_timing|exit_patience|overtrade|...",'
        ' "confidence": 0.0-1.0, "lesson_text": "...", '
        '"delta": {"strategy_id_x_regime": 0.01}}'
    )


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
    """Render the canonical lesson markdown body shared by P0 + P1 paths.

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
    """Rule-based P0 lesson template — no LLM call.

    Outputs the same shape as the LLM JSON branch so downstream code
    (vault write + ai_lessons row + GateResult payload) stays uniform.

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

    P0 invariant: |Δ| ≤ ``LESSON_DELTA_CLAMP_P0`` (= 0.03). Sign comes
    from the realized PnL (won → +, lost → −). Soft-mode dampens the
    magnitude by ``LESSON_SOFT_SCALAR_MAX`` per Q10.
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
    model: str = GPT_P0_MODEL,
) -> GateResult:
    """Gate 8 dispatcher.

    Inputs from ``ctx.payload``:
        ``closed_trade`` (dict, required), ``closed_trade_count`` (int, optional).

    Fail-open (Q4): no client → no-op REFLECTED.

    ``model`` selects the OpenAI model id (P0 caller never reaches the LLM
    branch — orchestrator passes ``client=None``; P1 caller passes
    ``GPT_P1_MODEL`` together with a live client). ``model_used`` in the
    returned ``GateResult`` derives from this argument so callers can
    audit the actual phase wiring (``gpt`` vs ``gpt_p1``).
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

    if client is None:
        # P0 Python template (Q3): emit a deterministic REFLECTED with a
        # rule-based lesson + clamped delta so Layer 5 still receives a
        # learning signal without an LLM call. Q10 confidence floor and
        # ±0.03 delta clamp are honoured by construction.
        py_lesson = _python_template_lesson(closed)
        py_delta = _python_template_delta(closed, soft_mode=soft_mode)
        # Persist + write to vault even at P0 so the lesson ledger is
        # populated from day 1; ADR-007 §learner expects every closed
        # trade to leave a row in ``ai_lessons``.
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

    system = make_system_prefix(
        role="Polaris Post-Trade Reflector — emit lesson + cell delta + learner adjustment.",
        decision_enum=["entry_timing", "exit_patience", "overtrade", "ok"],
        cell_summary=str(ctx.payload.get("cell_routing", {})),
        baseline_summary=str(ctx.payload.get("baseline", {})),
        recent_trades_summary=str(
            ctx.payload.get("recent_trades", [])[:LESSON_RECENT_TRADES_MAX]
        ),
    )
    # ``model_label`` derives from the caller-supplied ``model`` so the
    # GateResult honestly reports which model tier executed the call
    # (P0 never reaches this branch; P1 passes GPT_P1_MODEL + label "gpt_p1").
    # Tier predicate: any model id matching the ``GPT_P1_MODEL`` constant
    # (or a date-stamped variant of the same family) is the P1 tier; the
    # P0 model id is the default fallback. This routes through the
    # constants instead of hardcoding the literal "gpt-5.5" so the next
    # model rename only needs the constant updated.
    p1_root = GPT_P1_MODEL.lower()
    p0_root = GPT_P0_MODEL.lower()
    m = model.lower()
    is_p1_tier = m == p1_root or m.startswith(f"{p1_root}-")
    is_p0_tier = m == p0_root or m.startswith(f"{p0_root}-")
    if is_p1_tier:
        model_label = "gpt_p1"
    elif is_p0_tier:
        model_label = "gpt"
    else:
        # Unknown family — treat as the safer P0 label so audit trails do
        # not mislabel an unexpected tier as P1 (Q3 invariant).
        model_label = "gpt"
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=_build_user_prompt(closed),
        max_tokens=LESSON_MAX_TOKENS,
        model=model,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    if res.error or res.parsed is None:
        return GateResult(
            decision=GateDecision.REFLECTED,
            next_gate=None,
            payload={"reason": f"{model_label}_error", "error": res.error},
            model_used=model_label,
            latency_ms=res.latency_ms,
            error=res.error,
        )

    parsed = res.parsed
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < LESSON_CONFIDENCE_FLOOR:
        # Q10: drop low-confidence lessons.
        return GateResult(
            decision=GateDecision.REFLECTED,
            next_gate=None,
            payload={"reason": "low_confidence", "confidence": confidence},
            model_used=model_label,
            latency_ms=res.latency_ms,
        )

    lesson_text = str(parsed.get("lesson_text", "")).strip() or "(empty lesson)"
    lesson_type = str(parsed.get("lesson_type", "ok"))
    raw_delta = parsed.get("delta", {})
    raw_dict = raw_delta if isinstance(raw_delta, dict) else {}

    # Soft mode: dampen delta to 25% for first 100 closed trades.
    soft_scaled = (
        {k: float(v) * LESSON_SOFT_SCALAR_MAX for k, v in raw_dict.items() if isinstance(v, (int, float))}
        if soft_mode
        else raw_dict
    )
    # Q10 cap: P0 (no client / soft mode) → ±0.03; P1 (LLM client active +
    # past soft-mode window) → ±0.10 (ADR-007 §learner_delta). Soft-mode
    # callers stay clamped to P0 even with a client to prevent LLM
    # hallucination from over-driving Δ before sample-count is sufficient.
    cap = LESSON_DELTA_CLAMP_P0 if soft_mode else LESSON_DELTA_CLAMP_P1
    delta: dict[str, Any] = _clamp_delta(soft_scaled, cap=cap)

    md = _format_lesson_markdown(
        trade_id=trade_id,
        strategy_id=strategy_id,
        regime=regime,
        session=session,
        lesson_type=lesson_type,
        confidence=confidence,
        soft_mode=soft_mode,
        delta=delta,
        lesson_text=lesson_text,
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
            confidence=confidence,
            lesson_type=lesson_type,
            delta=delta,
            now_ts=ctx.started_ts,
        )

    ctx.state = SignalLifecycle.REFLECTED
    return GateResult(
        decision=GateDecision.REFLECTED,
        next_gate=None,
        payload={
            "lesson_type": lesson_type,
            "confidence": confidence,
            "delta": delta,
            "soft_mode": soft_mode,
        },
        model_used=model_label,
        latency_ms=res.latency_ms,
    )
