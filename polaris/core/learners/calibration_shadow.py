"""Probability calibration shadow (frontgate-scan item #4, G5, behavior-0).

Collects (predicted p_pos, realized won) pairs for an OFFLINE Platt/PAV
calibration fit (``calibration_fit.py``) — MEASUREMENT ONLY, never read by
sizing/gating (9-stack unaffected, no new multiplier).

Two seams, two different fail-open contracts (mirrors the existing
``learners/posterior.py`` split between a raising core function and a
catching ``_safe_*`` close-path wrapper):

- :func:`record_entry_snapshot` — called from the Gate 5 dispatcher
  (``core/pipeline/agents/entry_sizer.py::entry_sizer_gate``), right after a
  SIZED (admitted, non-zero) verdict — NOT from inside
  ``core/sizing/engine.py`` itself: ``polaris/core/sizing/*.py`` is under a
  hard regression guard (``tests/test_edge_validation.py::
  test_sizing_does_not_import_posterior``) forbidding the whole ``sizing``
  package from reading ``learner_posterior``/``p_pos`` at all — the
  edge-validation posterior stays measurement-only w.r.t. the T4 chain by
  construction. G5's gate wrapper (which calls ``compute_size`` but lives
  outside the ``sizing`` package) is the correct outside-the-fence seam: the
  SAME (venue, strategy, ticker, regime) cell key ``compute_size`` used for
  ``cell_routing_mult``, read at the SAME instant. This call is
  SELF-CONTAINED fail-open (never raises, logs a warning) since the gate
  dispatcher has no fault-isolation wrapper around individual calls (unlike
  the close-path fan-out).
- :func:`record_close_outcome` — called from the close-path fan-out
  (``scripts/_production_close_effects.py::_safe_record_calibration_outcome``,
  the 5th call alongside ``_safe_update_cell_matrix`` /
  ``_safe_run_learners`` / ``_safe_record_meta_label`` /
  ``_safe_update_posterior``). RAISES ``sqlite3.Error`` on a broken
  connection, matching ``maybe_update_posterior``'s contract — the CALLER's
  existing ``_safe_*`` wrapper catches + logs + fault-records.

Look-ahead guard (non-negotiable): ``predicted_p_pos`` is read from
``learner_posterior`` and persisted ONCE, at sizing time. It is NEVER
re-derived by re-querying ``learner_posterior`` after the fact — every
subsequent close on the SAME cell mutates that table's ``p_pos``, so a
post-close re-read would leak future information into what is supposed to be
a known-at-entry-time prediction.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

__all__ = ["record_close_outcome", "record_entry_snapshot"]


def record_entry_snapshot(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
    now_ts: int,
) -> None:
    """Snapshot ``learner_posterior.p_pos`` for this cell at SIZING time.

    ``INSERT OR IGNORE`` keyed on ``signal_id`` (the ``calibration_pairs``
    primary key): a signal re-sized more than once (e.g. a G3 MODIFY re-price)
    keeps its FIRST snapshot only — the earliest sizing decision, never
    overwritten by a later re-size whose ``learner_posterior`` read could
    already reflect OTHER signals' closes on the same cell in the interim.

    No ``learner_posterior`` row yet (cold cell) → the table's own default
    ``p_pos=0.5`` / ``n_samples=0`` is recorded (a true "uninformed" snapshot,
    not skipped — the calibration/coverage report needs the cold-cell
    population too).
    """
    try:
        row = conn.execute(
            "SELECT p_pos, n_samples FROM learner_posterior "
            "WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?",
            (exchange, strategy, ticker, regime),
        ).fetchone()
        p_pos = float(row[0]) if row is not None else 0.5
        n_samples = int(row[1]) if row is not None else 0
        conn.execute(
            "INSERT OR IGNORE INTO calibration_pairs "
            "(signal_id, venue, strategy, ticker, regime, predicted_p_pos, "
            " n_samples_at_entry, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                signal_id, exchange, strategy, ticker, regime, p_pos,
                n_samples, now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — measurement-only, fail-open
        logger.warning(
            "[calibration] entry snapshot failed signal=%s %s/%s/%s/%s: %r",
            signal_id, exchange, strategy, ticker, regime, exc,
        )


def record_close_outcome(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    won: bool,
    pnl_r: float,
    now_ts: int,
) -> None:
    """Accumulate the realized outcome onto an existing entry snapshot, by
    signal_id.

    Round-3 rework (verifier note): a scaled-out (multi-slice) close calls
    this ONCE PER SLICE — ``fold_close_slice`` per partial, then again at the
    terminal full/remainder close. The prior plain-overwrite UPDATE left the
    LAST call's (remainder-only) won/pnl_r as the stored outcome, dropping
    every earlier partial slice's contribution — the exact pair this
    module's offline Platt/PAV fit + item #4's promotion metric read then got
    a per-slice outcome instead of the whole-position one. ``realized_pnl_r``
    now ACCUMULATES (mirrors ``positions.pnl_r``'s own
    ``COALESCE(pnl_r,0)+slice`` pattern in ``_production_close.py`` — partial
    + remainder sum to exactly the whole-position R) and ``realized_won`` is
    derived from the ACCUMULATED total's sign, not the caller's ``won``
    (which only knows THIS slice's own sign) — correct at both an
    intermediate partial write and the final terminal write. A single-shot
    close is unaffected (one call, same result as before). ``won`` is kept
    for call-site/API compatibility; the persisted flag is always the
    accumulated sign.

    A no-op (0 rows affected, no error) when the signal has no snapshot row —
    a calibration miss must never gate/abort a close. Raises on a broken
    connection; the caller's ``_safe_*`` wrapper is responsible for the
    fail-open catch (see module docstring).
    """
    del won  # superseded by the accumulated sign — see docstring
    conn.execute(
        "UPDATE calibration_pairs SET "
        "realized_pnl_r = COALESCE(realized_pnl_r, 0.0) + ?, "
        "realized_won = CASE WHEN COALESCE(realized_pnl_r, 0.0) + ? > 0 "
        "THEN 1 ELSE 0 END, "
        "closed_ts = ? WHERE signal_id = ?",
        (float(pnl_r), float(pnl_r), now_ts, signal_id),
    )
