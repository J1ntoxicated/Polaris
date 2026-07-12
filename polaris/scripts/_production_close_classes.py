"""pts-classes (group WIRE) — close-event hook: score_F -> transition -> persist.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Aggressive
bias preserved: this module NEVER blocks/throttles a strategy — it only routes
capital by updating ``strategy_class`` (EARN/PROVE/BENCH/KILL), the same
capital-ROUTING record groups A-E already defined (aggressive_always_profit /
no_block_filter_architecture). No sizing/9-stack/-1.0R rail touch.

Wires together group B's ``rollup_score_f`` (batch-commit sweeper) and group
C's ``evaluate_transition`` (pure state machine) at the ONE real close-
confirmation point (``_production_close._close_trade_with_real_pnl``'s
post-commit fan-out — mirrors ``_safe_run_g8``'s terminal-only placement:
partial-close slices never reach here, only a FULL close). Every step is
fail-open (mirrors every other ``_safe_*`` helper in
``_production_close_effects.py``) — a classification failure must never
unwind an already-committed close. ``rollup_score_f`` commits its own
transaction (its own batch, never inline in the close's ``BEGIN
IMMEDIATE``); the ``strategy_class`` UPSERT below is a SEPARATE, small
transaction — the close hot path itself performs zero extra writes
(feedback_db_lock_is_architecture_signal).

Interpretation notes (WIRE's own assembly choices — no separate SSOT doc
specifies HOW to source ``TransitionInput``'s fields from persisted state,
same ambiguous-threshold precedent as ``recover_classes.py`` /
``transition.py``'s own docstrings):

- ``dwell`` counts CLOSES observed in the current class. This hook fires once
  per full close, so dwell is incremented by 1 here BEFORE calling
  ``evaluate_transition`` (the pure function only ever holds dwell steady on
  NO_TRANSITION or resets it to 0 on a class change — the "+1 per close"
  bookkeeping is the caller's job).
- ``timeframe_bucket``: strategy metadata timeframe mapped 5m/15m/1H ->
  "intraday", 1D -> "1d_plus" (no strategy in the current registry is
  classified "swing" — the bucket exists in ``transition.py`` for a future
  registrant; unregistered/unknown timeframes default to "intraday", the
  stricter tripwire window, rather than silently exempting an unknown
  strategy from the tripwire).
- ``n_signals_recent`` / fill-gate counts: ``signals``/``fills`` are both
  scoped by ``strategy_id`` directly (no per-row venue column on
  ``signals``); since a ``strategy_id`` maps 1:1 to one venue in
  ``STRATEGY_REGISTRY``, scoping by ``strategy_id`` alone is accurate for
  every currently-registered strategy.
- ``last_50_fill_rate``: fills within the time span of the last
  ``min(50, n_signals_total)`` signals, divided by that same signal count.
  An approximation (no signal->fill join key exists) — acceptable because the
  fill gate's own floor (``n_fills_total < 10`` -> never blocks) covers the
  common early-life case; flagged as a follow-up refinement candidate.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from polaris.core.classes.remap_table import resolve_thresholds
from polaris.core.classes.score_f import rollup_score_f
from polaris.core.classes.transition import (
    Timeframe,
    TransitionInput,
    evaluate_transition,
)
from polaris.scripts._production_atr import strategy_timeframe

if TYPE_CHECKING:
    from polaris.scripts._smoke_fills import SimulatedTrade
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

__all__ = ["update_strategy_class_on_close"]

# Recent-activity window for the fill gate's n_signals_recent/n_fills_recent
# (transition.py's own 10-49 fills tier). 24h mirrors the probe_fee_24h /
# daily-cadence grain already used elsewhere in this feature.
_RECENT_WINDOW_SEC = 86_400

_TIMEFRAME_BUCKET: dict[str, Timeframe] = {
    "5m": "intraday",
    "15m": "intraday",
    "1H": "intraday",
    "1D": "1d_plus",
}


def _timeframe_bucket(strategy_id: str) -> Timeframe:
    return _TIMEFRAME_BUCKET.get(strategy_timeframe(strategy_id), "intraday")


def _decode_ring(raw: Any) -> list[float]:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [float(v) for v in value] if isinstance(value, list) else []


def _fetch_class_row(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str
) -> tuple[str, str, int, int, int, int, int, int | None, list[float]] | None:
    row = conn.execute(
        "SELECT strategy_class, kill_state, window_w, dwell, ladder_step, "
        "epoch_id, last_transition_ts, last_promotion_ts, shadow_ring "
        "FROM strategy_class WHERE venue = ? AND strategy_id = ?",
        (venue, strategy_id),
    ).fetchone()
    if row is None:
        return None
    return (
        str(row[0]), str(row[1]), int(row[2]), int(row[3]), int(row[4]),
        int(row[5]), int(row[6]), None if row[7] is None else int(row[7]),
        _decode_ring(row[8]),
    )


def _intent_scores(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, window_w: int
) -> list[float]:
    """Oldest-first score_F contributions over the last ``window_w`` closes."""
    rows = conn.execute(
        "SELECT score_contrib FROM score_f_events "
        "WHERE venue = ? AND strategy_id = ? ORDER BY closed_ts DESC LIMIT ?",
        (venue, strategy_id, window_w),
    ).fetchall()
    return [float(r[0]) for r in reversed(rows)]


def _recent_r_multiples(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, tripwire_n: int
) -> list[float]:
    """Oldest-first realized R-multiples over the last ``tripwire_n`` closes."""
    rows = conn.execute(
        "SELECT pnl_r FROM positions WHERE venue = ? AND strategy_id = ? "
        "AND status = 'closed' AND pnl_r IS NOT NULL "
        "ORDER BY closed_ts DESC LIMIT ?",
        (venue, strategy_id, tripwire_n),
    ).fetchall()
    return [float(r[0]) for r in reversed(rows)]


def _fill_gate_inputs(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, now_ts: int
) -> tuple[int, int, int, float]:
    n_fills_total = int(
        conn.execute(
            "SELECT COUNT(*) FROM fills WHERE venue = ? AND strategy_id = ?",
            (venue, strategy_id),
        ).fetchone()[0]
    )
    watermark = now_ts - _RECENT_WINDOW_SEC
    n_signals_recent = int(
        conn.execute(
            "SELECT COUNT(*) FROM signals WHERE strategy_id = ? AND ts >= ?",
            (strategy_id, watermark),
        ).fetchone()[0]
    )
    n_fills_recent = int(
        conn.execute(
            "SELECT COUNT(*) FROM fills WHERE venue = ? AND strategy_id = ? "
            "AND ts_ms >= ?",
            (venue, strategy_id, watermark * 1000),
        ).fetchone()[0]
    )
    last_50 = conn.execute(
        "SELECT ts FROM signals WHERE strategy_id = ? ORDER BY ts DESC LIMIT 50",
        (strategy_id,),
    ).fetchall()
    if not last_50:
        last_50_fill_rate = 1.0
    else:
        span_start = int(last_50[-1][0])
        n_fills_in_span = int(
            conn.execute(
                "SELECT COUNT(*) FROM fills WHERE venue = ? AND strategy_id = ? "
                "AND ts_ms >= ?",
                (venue, strategy_id, span_start * 1000),
            ).fetchone()[0]
        )
        last_50_fill_rate = n_fills_in_span / len(last_50)
    return n_fills_total, n_signals_recent, n_fills_recent, last_50_fill_rate


def _persist_transition(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
    result: Any,
    now_ts: int,
    changed: bool,
    is_promotion: bool,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE strategy_class SET strategy_class = ?, kill_state = ?, "
            "dwell = ?, ladder_step = ?, epoch_id = ?, "
            "last_transition_ts = CASE WHEN ? THEN ? ELSE last_transition_ts END, "
            "last_promotion_ts = CASE WHEN ? THEN ? ELSE last_promotion_ts END "
            "WHERE venue = ? AND strategy_id = ?",
            (
                result.strategy_class, result.kill_state, result.dwell,
                result.ladder_step, result.epoch_id,
                changed, now_ts,
                is_promotion, now_ts,
                venue, strategy_id,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def update_strategy_class_on_close(
    conn: sqlite3.Connection,
    *,
    trade: SimulatedTrade,
    now_ts: int,
    state: ProdLoopState,
) -> None:
    """Fail-open close hook: score_F rollup -> transition evaluation -> persist.

    Called ONCE per FULL terminal close (never on a partial-close slice —
    ``fold_close_slice`` does not call this). No ``strategy_class`` row yet
    (bootstrap has not run for this track) -> no-op (nothing to transition;
    bootstrap/hydrate at boot is responsible for seeding the row).
    """
    try:
        rollup_score_f(conn, now_ts=now_ts)
    except Exception as exc:  # noqa: BLE001 — fail-open, mirrors _safe_* siblings
        logger.error("[pts-classes] score_F rollup failed: %r", exc)
        return

    venue, strategy_id = trade.venue, trade.strategy_id
    try:
        row = _fetch_class_row(conn, venue=venue, strategy_id=strategy_id)
        if row is None:
            return  # not yet bootstrapped/tracked — nothing to transition
        (
            klass, kill_state, window_w, dwell, ladder_step, epoch_id,
            last_transition_ts, last_promotion_ts, shadow_scores,
        ) = row

        bucket = _timeframe_bucket(strategy_id)
        tripwire_n = 8 if bucket in ("intraday", "swing") else 5
        intent_scores = _intent_scores(
            conn, venue=venue, strategy_id=strategy_id, window_w=window_w
        )
        recent_r = _recent_r_multiples(
            conn, venue=venue, strategy_id=strategy_id, tripwire_n=tripwire_n
        )
        n_fills_total, n_signals_recent, n_fills_recent, last_50_rate = (
            _fill_gate_inputs(conn, venue=venue, strategy_id=strategy_id, now_ts=now_ts)
        )
        # fee_split_flip_r2_2026-07-12 item 2 — remap the Schmitt/stagnation/
        # ladder thresholds onto score_f.py's now-flipped judged axis
        # (gross_bps). READ-ONLY, fail-open (falls back through venue-pool to
        # hardcoded legacy defaults on any gap — see remap_table.py).
        thresholds = resolve_thresholds(conn, venue=venue, strategy_id=strategy_id)

        inp = TransitionInput(
            strategy_class=klass,
            kill_state=kill_state,
            window_w=window_w,
            dwell=dwell + 1,  # one more close observed in this class
            ladder_step=ladder_step,
            epoch_id=epoch_id,
            last_transition_ts=last_transition_ts,
            now_ts=now_ts,
            timeframe_bucket=bucket,
            shadow_scores=shadow_scores,
            intent_scores=intent_scores,
            recent_r_multiples=recent_r,
            n_fills_total=n_fills_total,
            n_signals_recent=n_signals_recent,
            n_fills_recent=n_fills_recent,
            last_50_fill_rate=last_50_rate,
            last_promotion_ts=last_promotion_ts,
            thresholds=thresholds,
        )
        result = evaluate_transition(inp)
        changed = result.strategy_class != klass
        # A promotion is PROVE->EARN, a ladder step-up (BENCH, ladder_step
        # advances), or a ladder exit-to-PROVE — every path that calls
        # transition.py's _promote() helper. _promote() is the ONLY path that
        # unconditionally bumps epoch_id (the sibling _next_epoch
        # regime-change branch only fires on NO_TRANSITION/demotion, which
        # never advances ladder_step and never raises epoch_id beyond the
        # regime-ratio check) — so a ladder_step increase OR a class change
        # to EARN/PROVE alongside an epoch bump both identify a real
        # _promote() call, distinguishing it from the regime-change bump.
        is_promotion = result.epoch_id > epoch_id and (
            changed or result.ladder_step > ladder_step
        )
        _persist_transition(
            conn, venue=venue, strategy_id=strategy_id, result=result,
            now_ts=now_ts, changed=changed, is_promotion=is_promotion,
        )
        if changed:
            logger.info(
                "[pts-classes] %s/%s transition %s -> %s (%s)",
                venue, strategy_id, klass, result.strategy_class, result.reason,
            )
        elif result.reason == "EXEC_STARVED":
            # Unconditional (not gated on `changed`) — EXEC_STARVED is itself
            # the falsifiable signal (Spec (8): a fill-starved track is stuck
            # AT its current class, so `changed` is always False here by
            # construction; gating this line on `changed` would mean it can
            # never fire in production, leaving log_scan.py's regex +
            # watchdog.py's exec_starved alert permanently dead).
            logger.info(
                "[pts-classes] %s/%s transition unchanged (EXEC_STARVED)",
                venue, strategy_id,
            )
    except Exception as exc:  # noqa: BLE001 — fail-open, never unwind the close
        logger.error(
            "[pts-classes] transition update failed for %s/%s: %r",
            trade.venue, trade.strategy_id, exc,
        )
