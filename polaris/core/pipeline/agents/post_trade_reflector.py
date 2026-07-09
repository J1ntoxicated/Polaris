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
- ``ai_lessons`` row (SSOT, A3 raw data) — the cell-matrix delta + lesson_type
  for every closed trade.
- ``cell_matrix_delta`` (dict)
- ``learner_adjustment`` (dict, soft-mode 25% sizing scalar within 100-trade window)

The per-trade vault markdown export was removed (2026-06-02): the 2703 .md files
were telemetry spam with zero readers — ``ai_lessons`` is the single source of
truth. The DB INSERT below is the only persistence path.

Fail-open (Q4): errors do not block the pipeline; lesson is dropped.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Final

from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    GateResult,
    SignalLifecycle,
)
from polaris.core.sizing.kelly import kelly_fraction
from polaris.core.sizing.schema import StrategyRiskState
from polaris.storage.db_writer import DBWriter, dbwriter_enabled

__all__ = [
    "LESSON_CONFIDENCE_FLOOR",
    "LESSON_DELTA_CLAMP_P0",
    "LESSON_RECENT_TRADES_MAX",
    "LESSON_SOFT_MODE_TRADE_COUNT",
    "LESSON_SOFT_SCALAR_MAX",
    "post_trade_reflector_gate",
]

LESSON_CONFIDENCE_FLOOR: Final[float] = 0.70
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
    elif (not won) and pnl_r <= -1.0:
        lesson_type = "overtrade"
    else:
        lesson_type = "entry_timing"
    return {
        # Confidence is set above the floor so downstream consumers do
        # not drop the deterministic lesson; it's still logged honestly
        # as ``model_used="python"``.
        "confidence": LESSON_CONFIDENCE_FLOOR,
        "lesson_type": lesson_type,
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


def _strategy_risk_state_from_positions(
    conn: sqlite3.Connection, *, venue: str, strategy: str, now_ts: int
) -> StrategyRiskState:
    """Aggregate the ``positions`` close ledger into a fresh
    ``strategy_risk_state`` row — n_closed / Kelly p,q,fraction / win_streak
    (consecutive-win tail, reset the instant the most recent close is a loss)
    / hit_rate_10 (win rate over the trailing 10 closes).

    ``positions`` is the SSOT close ledger: ``status='closed'`` + ``pnl_r``
    are stamped by ``_production_close.py`` in the same commit that closes
    the trade, BEFORE G8 runs — so the just-closed trade is already included
    here. Every input feeds the existing pure ``kelly_fraction`` /
    ``resolve_tier_amplifier`` functions unchanged (no new constants).
    """
    rows = conn.execute(
        """
        SELECT pnl_r FROM positions
        WHERE venue = ? AND strategy_id = ? AND status = 'closed'
              AND pnl_r IS NOT NULL
        ORDER BY closed_ts ASC
        """,
        (venue, strategy),
    ).fetchall()
    history = [float(r[0]) for r in rows]
    n_closed = len(history)
    wins = sum(1 for r in history if r > 0.0)
    p = wins / n_closed if n_closed else 0.0
    q = (n_closed - wins) / n_closed if n_closed else 0.0

    win_streak = 0
    for r in reversed(history):
        if r <= 0.0:
            break
        win_streak += 1

    tail10 = history[-10:]
    hit_rate_10 = sum(1 for r in tail10 if r > 0.0) / len(tail10) if tail10 else 0.0

    return StrategyRiskState(
        venue=venue,
        strategy=strategy,
        closed_trades=n_closed,
        kelly_p=p,
        kelly_q=q,
        kelly_fraction=kelly_fraction(p, q),
        win_streak=win_streak,
        hit_rate_10=hit_rate_10,
        updated_ts=now_ts,
    )


def _upsert_strategy_risk_state(
    conn: sqlite3.Connection, *, state: StrategyRiskState
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_risk_state
            (venue, strategy, closed_trades, kelly_p, kelly_q, kelly_fraction,
             win_streak, hit_rate_10, updated_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, strategy) DO UPDATE SET
            closed_trades = excluded.closed_trades,
            kelly_p = excluded.kelly_p,
            kelly_q = excluded.kelly_q,
            kelly_fraction = excluded.kelly_fraction,
            win_streak = excluded.win_streak,
            hit_rate_10 = excluded.hit_rate_10,
            updated_ts = excluded.updated_ts
        """,
        (
            state.venue, state.strategy, state.closed_trades, state.kelly_p,
            state.kelly_q, state.kelly_fraction, state.win_streak,
            state.hit_rate_10, state.updated_ts,
        ),
    )


def _persist_strategy_risk_state(
    conn: sqlite3.Connection,
    *,
    db_writer: DBWriter | None,
    venue: str,
    strategy: str,
    now_ts: int,
) -> None:
    """Aggregate + UPSERT the Kelly/tier-amplifier risk row for one close.

    The aggregating SELECT runs on ``conn`` (reader-safe under WAL — many
    concurrent readers are fine); only the UPSERT is routed through the
    shared serialized writer when one is supplied (db-writer-reader-split —
    this is a hot per-close table, it must not open a second RW connection
    that competes with the writer thread's WAL lock). ``db_writer=None``
    (kill-switched or not wired by the caller) falls back to the same UPSERT
    directly on ``conn`` — byte-identical to every other migrated writer's
    fallback path.
    """
    state = _strategy_risk_state_from_positions(
        conn, venue=venue, strategy=strategy, now_ts=now_ts
    )
    if db_writer is not None and dbwriter_enabled():

        def _job(job_conn: sqlite3.Connection) -> None:
            _upsert_strategy_risk_state(job_conn, state=state)

        db_writer.submit(_job, label="strategy_risk_state")
    else:
        _upsert_strategy_risk_state(conn, state=state)


async def post_trade_reflector_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    conn: sqlite3.Connection | None = None,
    model: str | None = None,
    db_writer: DBWriter | None = None,
) -> GateResult:
    """Gate 8 dispatcher — deterministic Python template (no LLM).

    Inputs from ``ctx.payload``:
        ``closed_trade`` (dict, required), ``closed_trade_count`` (int, optional).

    The ``client`` / ``model`` parameters are retained for caller compatibility
    (ai_conductor P2 removed the GPT branch) but are inert — no network call is
    ever made. Every closed trade leaves a deterministic REFLECTED lesson + a
    clamped Δ for Layer 5, plus an ``ai_lessons`` row when ``conn`` is supplied.

    When ``conn`` is supplied this also UPSERTs the ``strategy_risk_state`` row
    for ``(ctx.venue, strategy_id)`` from the ``positions`` close ledger — the
    Kelly (n>=20) + tier-amplifier (win-streak) sizing inputs Layer 3 reads.
    ``db_writer`` (optional) routes that UPSERT through the shared serialized
    RW conn (db-writer-reader-split) instead of writing directly on ``conn``.
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
    # Persist to the ai_lessons ledger (SSOT, A3 raw data) so Layer 5 receives a
    # row for every closed trade; ADR-007 §learner expects every closed trade to
    # leave a row in ``ai_lessons``.
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
        # Kelly (n>=20) + tier-amplifier writer — the ``strategy_risk_state``
        # row was previously read-only-dead (a reader existed, no writer), so
        # every fill pinned to CS-3 cold-start regardless of win rate.
        _persist_strategy_risk_state(
            conn,
            db_writer=db_writer,
            venue=ctx.venue,
            strategy=strategy_id,
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
