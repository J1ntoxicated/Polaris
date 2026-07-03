"""score_F — fee-normalized edge classification (pts-classes, group B).

score_F = Σ(net_i / max(abs(fee_i), 0.0001 * notional_i)) over every CLOSED
flat->nonzero->flat lifecycle (one ``positions`` row) for a (venue,
strategy_id) track. ``net_i`` and ``fee_i`` sum ALL fills belonging to the
lifecycle (partial closes / scale-in-out legs net together within the SAME
lifecycle — SSOT U3: positions + fills + position_strategy_segments) so a
scaled-out winner is one term, not several. Spread-only venues (Capital CFD
demo reports ``fills.fee_usd = 0`` on every fill) fold a modeled spread proxy
into ``fee_i`` via ``economics.fees.real_fee_bps`` instead of reading a real
(always-zero) fee column — otherwise the notional floor alone would overstate
their score_F.

This module is a classification SIGNAL feeder for ``strategy_class``
(group A, ``polaris.core.lifecycle.recover_classes``) — computing/rolling it
up never blocks or throttles a strategy; BENCH/KILL-classed strategies keep
signaling/learning/shadow-pricing regardless (aggressive_always_profit /
no_block_filter_architecture). No sizing/9-stack/-1.0R rail touch.

Rollup is a batch-commit sweeper (``rollup_score_f``) writing an append-only
``score_f_events`` ledger (PK=position_id, mirrors ``ladder_ledger``'s
"no mutable aggregate column" shape) — NEVER inline in the position-close hot
path (feedback_db_lock_is_architecture_signal). ``f_track_cap`` reads that
ledger's ``fee_denom_usd`` column to derive the median expected fee for a
track's capital-routing cap.
"""

from __future__ import annotations

import sqlite3
import statistics
import time
from dataclasses import dataclass

from polaris.core.economics.fees import real_fee_usd

__all__ = [
    "FTrackCapResult",
    "LifecycleFee",
    "ScoreFResult",
    "compute_score_f",
    "f_track_cap",
    "rollup_score_f",
]

# net_i/fee_i denominator floor — 0.0001 * notional_i (task spec), applied via
# max() against abs(fee_i) so a real fee that already exceeds the floor is
# never discounted (max() composition only, no new multiplier — 9-stack ban).
_NOTIONAL_FLOOR_FRAC = 0.0001

# Venues whose demo fill stream reports a real per-fill fee (fee_usd != 0) —
# everyone else is treated as spread-only and gets the modeled-spread proxy
# from ``economics.fees.real_fee_bps`` instead (Capital CFD demo: fee=0 on
# every fill per ``economics.fees`` docstring).
_REAL_FEE_VENUES = frozenset({"okx"})


@dataclass(slots=True)
class LifecycleFee:
    """One closed lifecycle's net_i / fee_i / denom terms (pre-score)."""

    position_id: str
    venue: str
    closed_ts: int
    net_usd: float
    fee_usd: float
    notional_usd: float


@dataclass(slots=True)
class ScoreFResult:
    """One closed lifecycle's score_F contribution."""

    position_id: str
    venue: str
    strategy_id: str
    closed_ts: int
    net_usd: float
    fee_usd: float
    fee_denom_usd: float
    score_contrib: float


@dataclass(slots=True)
class FTrackCapResult:
    """``F_track_cap`` — median fee-denominator over a track's last window_w
    closes, or the PROVE-candidate fallback when no history exists."""

    f_track_cap_usd: float
    n_observed: int
    used_fallback: bool


def compute_score_f(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
) -> list[ScoreFResult]:
    """Score every CLOSED lifecycle for ``(venue, strategy_id)`` not yet
    filtered by any watermark (caller-side scoping — ``rollup_score_f`` is the
    watermark-aware sweeper entry point; this is the pure per-track scorer).
    """
    positions = conn.execute(
        "SELECT position_id, symbol, closed_ts FROM positions "
        "WHERE venue = ? AND strategy_id = ? AND status = 'closed' "
        "AND closed_ts IS NOT NULL ORDER BY closed_ts ASC",
        (venue, strategy_id),
    ).fetchall()

    out: list[ScoreFResult] = []
    for position_id, symbol, closed_ts in positions:
        fees = compute_lifecycle_fee(
            conn,
            position_id=str(position_id),
            venue=venue,
            symbol=str(symbol),
            closed_ts=int(closed_ts),
        )
        denom = max(abs(fees.fee_usd), _NOTIONAL_FLOOR_FRAC * fees.notional_usd)
        score = fees.net_usd / denom if denom > 0.0 else 0.0
        out.append(
            ScoreFResult(
                position_id=fees.position_id,
                venue=venue,
                strategy_id=strategy_id,
                closed_ts=fees.closed_ts,
                net_usd=fees.net_usd,
                fee_usd=fees.fee_usd,
                fee_denom_usd=denom,
                score_contrib=score,
            )
        )
    return out


def compute_lifecycle_fee(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    closed_ts: int,
) -> LifecycleFee:
    """Sum every fill belonging to ``position_id`` into one lifecycle's
    net_i (close-leg pnl_usd only, partial slices net together) and fee_i
    (ALL legs — open + close; modeled spread on spread-only venues).

    Scoped by ``contribution_id + instrument_id`` (NOT ``strategy_id``) —
    mirrors ``ladder._net_pnl_usd_for_position`` /
    ``_production_close_helpers.close_pnl_usd_total`` exactly: a fill's
    ``strategy_id`` can differ from the position's current ``strategy_id``
    after a Layer 6 strategy swap, so filtering fills by strategy_id would
    silently drop legs of a swapped position. The instrument_id half guards
    the documented cross-match bug (a foreign instrument's close fill sharing
    the same ``contribution_id``).
    """
    inst = f"{venue}:{symbol}"
    net_row = conn.execute(
        "SELECT COALESCE(SUM(pnl_usd), 0.0) FROM fills "
        "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 1",
        (position_id, inst),
    ).fetchone()
    net_usd = float(net_row[0]) if net_row is not None else 0.0

    legs = conn.execute(
        "SELECT fee_usd, size_usd FROM fills WHERE contribution_id = ? AND instrument_id = ?",
        (position_id, inst),
    ).fetchall()

    notional_usd = sum(float(size or 0.0) for _fee, size in legs)
    if venue.lower() in _REAL_FEE_VENUES:
        fee_usd = sum(float(fee or 0.0) for fee, _size in legs)
    else:
        # Spread-only venue — model each leg's cost via the real-fee proxy
        # schedule (economics.fees.real_fee_bps: Capital = COST_BPS_CAPITAL,
        # 3 bps/leg) rather than trusting the reported (always-zero) fee.
        fee_usd = sum(real_fee_usd(venue, float(size or 0.0)) for _fee, size in legs)

    return LifecycleFee(
        position_id=position_id,
        venue=venue,
        closed_ts=closed_ts,
        net_usd=net_usd,
        fee_usd=fee_usd,
        notional_usd=notional_usd,
    )


# ---------------------------------------------------------------------------
# Batch-commit rollup sweeper (mirrors ladder.materialize_credits)
# ---------------------------------------------------------------------------


def rollup_score_f(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Scan every (venue, strategy_id) track's newly-closed lifecycles since
    the checkpoint watermark, score each, and append one ``score_f_events``
    row per lifecycle. Returns the count of NEW rows inserted.

    Idempotent: ``score_f_events.position_id`` is PRIMARY KEY — a re-scan of
    an already-scored lifecycle is a silent ``INSERT OR IGNORE`` no-op. The
    checkpoint only advances after a batch's inserts all succeed, so a crash
    mid-batch safely re-scans (never skips, never double-counts — the PK is
    the actual idempotency guarantee, the checkpoint is a scan-window
    optimization only, same shape as ``ladder_credit_checkpoint``).

    Zero writes happen at position-close time — this is the ONLY writer of
    ``score_f_events``, invoked as a periodic/on-demand sweeper
    (feedback_db_lock_is_architecture_signal: the close hot path is untouched).
    """
    checkpoint_row = conn.execute(
        "SELECT last_scanned_closed_ts FROM score_f_checkpoint WHERE id = 1"
    ).fetchone()
    watermark = int(checkpoint_row[0]) if checkpoint_row is not None else 0

    tracks = conn.execute(
        "SELECT DISTINCT venue, strategy_id FROM positions "
        "WHERE status = 'closed' AND closed_ts IS NOT NULL AND closed_ts >= ?",
        (watermark,),
    ).fetchall()

    inserted = 0
    max_closed_ts = watermark
    for venue, strategy_id in tracks:
        results = compute_score_f(conn, venue=str(venue), strategy_id=str(strategy_id))
        for r in results:
            if r.closed_ts < watermark:
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(r.closed_ts))
            cur = conn.execute(
                "INSERT OR IGNORE INTO score_f_events "
                "(position_id, venue, strategy_id, day, closed_ts, net_usd, "
                "fee_denom_usd, score_contrib) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.position_id,
                    r.venue,
                    r.strategy_id,
                    day,
                    r.closed_ts,
                    r.net_usd,
                    r.fee_denom_usd,
                    r.score_contrib,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            max_closed_ts = max(max_closed_ts, r.closed_ts)

    conn.execute(
        "INSERT INTO score_f_checkpoint (id, last_scanned_closed_ts) "
        "VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET last_scanned_closed_ts = "
        "MAX(last_scanned_closed_ts, excluded.last_scanned_closed_ts)",
        (max_closed_ts,),
    )
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# F_track_cap — median(denom_i) over last-W closes, PROVE fallback
# ---------------------------------------------------------------------------


def f_track_cap(
    conn: sqlite3.Connection,
    *,
    venue: str,
    strategy_id: str,
    window_w: int,
    prove_fallback_usd: float,
) -> FTrackCapResult:
    """``F_track_cap`` = median(max(abs(fee_i), 0.0001*notional_i)) over the
    last ``window_w`` live+shadow closes for this (venue, strategy_id) track,
    read from the ``score_f_events`` ledger (rollup must have run first).

    07:30-frozen semantics: this is a pure read of whatever the ledger holds
    at call time (the caller freezes/invokes it once per daily cadence) — no
    history yet (brand-new candidate) falls back to ``prove_fallback_usd``
    (the current PROVE candidate pool's median expected fee, supplied by the
    caller) rather than raising or defaulting to 0.0.
    """
    rows = conn.execute(
        "SELECT fee_denom_usd FROM score_f_events "
        "WHERE venue = ? AND strategy_id = ? ORDER BY closed_ts DESC LIMIT ?",
        (venue, strategy_id, window_w),
    ).fetchall()
    denoms = [float(r[0]) for r in rows]
    if not denoms:
        return FTrackCapResult(
            f_track_cap_usd=prove_fallback_usd,
            n_observed=0,
            used_fallback=True,
        )
    return FTrackCapResult(
        f_track_cap_usd=statistics.median(denoms),
        n_observed=len(denoms),
        used_fallback=False,
    )
