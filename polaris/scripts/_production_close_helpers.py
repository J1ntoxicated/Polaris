"""Close-path leaf helpers split out of ``_production_close`` (line budget).

Owns the close-path read + persist leaves so the orchestrator stays <=500 LOC:

* ``real_pnl_r_from_fills`` / ``_atr_pct_from_bars`` / ``_close_excursion_r`` —
  the entry-fill + bar-drift PnL/excursion reads (re-exported by
  ``_production_close`` so existing import paths keep working).
* ``_latest_bar_close`` — FIX A: the fresh 1m mark the OKX cap-split sizes off.
* ``_persist_partial_close`` — FIX B: persist a partial close that left the
  position OPEN with a reduced qty (no untracked live venue exposure).
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.isolation.circuit_breaker import FAULT_EXCEPTION, record_fault
from polaris.core.live_recalc.excursion import compute_excursion_r
from polaris.core.metrics.risk_unit import realised_r_stream
from polaris.scripts._production_bars import BAR_TS_CLOCK_SKEW_SLACK_SEC

if TYPE_CHECKING:
    from polaris.scripts._production_state import ProdLoopState
    from polaris.scripts._smoke_fills import SimulatedTrade

logger = logging.getLogger(__name__)


# FIX B: a real close fill within this fraction of the tracked qty counts as a
# FULL close (rounding/fee dust). Anything below is a genuine partial → the
# position stays OPEN with a reduced qty so the remainder closes next tick.
# BUG A reuses the SAME eps for the pnl_usd slice gate so the full/partial
# classification and the stamping can never diverge.
_CLOSE_FULL_FILL_EPS = 0.005


def real_pnl_r_from_fills(
    conn: sqlite3.Connection, *, trade: SimulatedTrade,
    exit_price_override: float | None = None,
    close_base_qty: float | None = None,
) -> tuple[float, float, float]:
    """Read entry fill + most recent bars; compute R-units from real bar drift.

    Returns ``(pnl_r, pnl_usd, exit_price)``. When the bar history is too
    short the R denominator falls back to ``entry_price × 0.5%`` so the
    calculation is finite — but the magnitude reflects the *actual* close
    drift, not a hard-coded sign.

    ``close_base_qty`` (BUG A slice fix): the base qty THIS close fill actually
    sold. When set (real-roundtrip close path), ``pnl_usd`` is scaled to the
    slice's fraction of the entry qty — clamped to the not-yet-closed remainder
    (persisted close fills subtracted) so a duplicate/over-sell re-fire stamps
    ~0 instead of re-booking the position PnL. The legacy stamping put the FULL
    position pnl_usd on EVERY partial slice (N slices ≈ N× realised PnL — SPCE
    56/56 fills each carried the whole -579.78). ``pnl_r`` is qty-invariant
    (move quality for Layer 4/5) and is never scaled. A ~full slice
    (``frac >= 1 - _CLOSE_FULL_FILL_EPS``) skips the multiply entirely so a
    single-shot full close stays byte-identical with ``close_base_qty=None``.

    ``exit_price_override`` (P1-6 venue-wire fix): when set (real-roundtrip
    close), ``pnl_r`` / ``pnl_usd`` are computed against the **real exit fill
    price** instead of the most recent bar close, using the same ATR
    denominator. This keeps Layer 4/5 telemetry consistent with what was
    actually traded — without it a loss exit could be logged as a win when
    the seeded bars happened to trend the other way.

    Matches the entry fill by ``contribution_id = position_id`` AND
    ``instrument_id`` — the position_id alone was not instrument-unique for the
    tick engine (its signals share a generic signal_id), so a same-tick multi-
    instrument open produced colliding ids and the close cross-matched a foreign
    instrument's entry price → exploded pnl_usd/mfe_r (the instrument_id filter
    disambiguates the already-collided historical fills; the id is now made
    unique at creation too). Falls back to the legacy heuristic only when the
    trade has no ``position_id`` set (legacy callers).
    """
    if trade.position_id:
        row = conn.execute(
            """
            SELECT fill_price, size_usd, base_qty FROM fills
            WHERE contribution_id = ? AND instrument_id = ? AND is_close = 0
            ORDER BY ts_ms ASC LIMIT 1
            """,
            (trade.position_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT fill_price, size_usd, base_qty FROM fills
            WHERE strategy_id = ? AND instrument_id = ? AND is_close = 0
            ORDER BY ts_ms DESC LIMIT 1
            """,
            (trade.strategy_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()
    if row is None:
        fallback_exit = exit_price_override if exit_price_override else trade.entry_price
        return (0.0, 0.0, fallback_exit)
    entry_price = float(row[0])
    size_usd = float(row[1])
    entry_base_qty = float(row[2] or 0.0)
    trade.entry_price = entry_price
    # Exclude FUTURE-dated bars (stale +10h Capital) so the recent-bar exit mark
    # and ATR window are derived from real recent data, never a +10h ghost bar.
    ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    bar_rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE instrument_id = ? AND bar_interval = '1m' AND ts <= ?
        ORDER BY ts DESC LIMIT 14
        """,
        (f"{trade.venue}:{trade.symbol}", ts_upper),
    ).fetchall()
    # P1-6: an override exit price must drive pnl_r/pnl_usd even when there is
    # no bar history (the real close already gives us the true exit level).
    if not bar_rows and exit_price_override is None:
        return (0.0, 0.0, entry_price)
    bar_close = float(bar_rows[0][0]) if bar_rows else entry_price
    exit_price = exit_price_override if exit_price_override else bar_close
    if entry_price <= 0.0 or exit_price <= 0.0:
        return (0.0, 0.0, entry_price)
    # Step N: the realised-R denominator is the per-stream R_budget (resolved by
    # venue below), so the entry ATR anchor is no longer read here — it remains
    # the excursion (mfe_r/mae_r) denominator inside ``_close_excursion_r``.
    pnl_abs = (
        (exit_price - entry_price)
        if trade.side == "long"
        else (entry_price - exit_price)
    )
    pnl_usd = (pnl_abs / entry_price) * size_usd
    # Step N (2026-06-23, Jin LOCKED): realised R = the dollar truth rescaled by
    # the per-STREAM R_budget (BASE_RISK_PCT × the stream's starting equity), NOT
    # the per-trade ATR risk_usd. The ATR denominator was internally consistent
    # within a venue but NON-COMPARABLE across venues (1D vs 1m ATR anchor → a
    # −$2 OKX loss read as ~−1.3R while a −$44 Alpaca loss read ~−0.25R, a 123×
    # risk-unit skew). R_budget is a per-stream CONSTANT, so within a stream R
    # stays a pure linear rescale of pnl_usd (same sign, same bleeder ranking →
    # the R and $ ledgers stay reconciled) AND a $X outcome maps to a comparable
    # R on every venue. pnl_r is computed from the FULL (pre-slice) pnl_usd so it
    # stays qty-invariant — the partial slicing below only scales the $ stamp.
    # The ATR ``atr_pct`` above is RETAINED for the excursion (mfe_r/mae_r) path,
    # a DIFFERENT (per-trade stop-distance) R unit — not the realised R here.
    pnl_r = realised_r_stream(pnl_usd=pnl_usd, venue=trade.venue)
    # BUG A — slice the pnl_usd to THIS close fill's fraction of the entry qty,
    # clamped to the un-closed remainder (persisted close fills of the SAME
    # instrument subtracted; foreign-instrument fills sharing the id are the
    # historical cross-match pollution and must not shrink the remainder).
    if close_base_qty is not None and trade.position_id and entry_base_qty > 0.0:
        closed_so_far = float(conn.execute(
            "SELECT COALESCE(SUM(base_qty), 0.0) FROM fills "
            "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 1",
            (trade.position_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()[0])
        qty_eff = min(close_base_qty, max(0.0, entry_base_qty - closed_so_far))
        frac = qty_eff / entry_base_qty
        if frac < 1.0 - _CLOSE_FULL_FILL_EPS:
            # Genuine partial slice. A ~full close never reaches this multiply,
            # keeping the single-shot full-close pnl_usd byte-identical.
            pnl_usd *= frac
    # ``pnl_r`` already carries the unified ±R_CLAMP (inside ``realised_r``); it
    # is NOT re-clamped here so the realised, excursion and decision paths share
    # the one telemetry bound (Step M). The old ±10 clamp that HID −34..−100R
    # losses is gone — G6/G7/precise-exit now see the true loss magnitude.
    return (pnl_r, pnl_usd, exit_price)


def _entry_anchor_atr_pct(
    conn: sqlite3.Connection, *, trade: SimulatedTrade,
) -> float | None:
    """Entry-time ATR anchor stamped on the positions row, or ``None``.

    ``None`` (legacy row / pre-anchor DB / degenerate value) keeps the
    current-bars denominator — the pre-anchor behaviour. A legacy DB without
    the column degrades the same way (sqlite error swallowed: the close path
    must never fail on a telemetry read).
    """
    if not trade.position_id:
        return None
    try:
        row = conn.execute(
            "SELECT entry_atr_pct FROM positions WHERE position_id = ?",
            (trade.position_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    anchor = float(row[0])
    return anchor if anchor > 1e-5 else None


def _atr_pct_from_bars(bar_rows: list[Any]) -> float:
    """Mean (high-low)/close over recent 1m bars; 0.005 fallback when empty.

    Extracted from ``real_pnl_r_from_fills`` so the close-time excursion write
    shares the *same* ATR-pct denominator the realised-PnL path uses (no drift
    between pnl_r and mfe_r/mae_r R units). Pure.
    """
    samples = [
        (float(r[1]) - float(r[2])) / float(r[0])
        for r in bar_rows
        if float(r[0]) > 0.0
    ]
    mean = sum(samples) / len(samples) if samples else 0.005
    # Flat/stale bars (high==low → samples all ~0) give mean ~0, which collapses
    # the R-unit denominator downstream and explodes pnl_r/mfe_r. A degenerate
    # mean falls back to the same 0.005 real R unit the empty case uses.
    return mean if mean > 1e-5 else 0.005


def _close_excursion_r(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, exit_price: float,
) -> tuple[float, float, float]:
    """Best-effort ``(mfe_r, mae_r, atr_risk_usd)`` for a position at close time.

    BUILD_SCHEMA prerequisite: the tick loop does not yet populate the
    ``positions.peak_price`` / ``trough_price`` extremes (a later precise-exit
    stream owns that). So this reads whatever extremes the position row carries
    and falls back to the observed entry/exit bounds when they are NULL — a
    position that only ever recorded its entry and exit still yields a finite,
    correctly-signed excursion (never *under*-states MFE/MAE relative to the
    realised move). The mfe_r/mae_r R denominator is the per-trade-ATR stop
    distance (``entry_price × anchor × 2``) — the EXCURSION ruler, a DIFFERENT
    unit from the per-stream realised ``pnl_r``.

    ``atr_risk_usd`` (third return) is the WHOLE-POSITION dollar 1R for that same
    per-trade-ATR ruler — ``atr_usd(per-unit) × base_qty`` — so the close path can
    rescale ``pnl_usd`` into the SAME excursion ruler the probe-outcome columns
    (``realized_pnl_r`` / ``mfe_r_final`` / ``giveback_r``) expect. ``base_qty``
    is the entry fill's filled size (the open qty is exact); it falls back to
    ``trade.base_qty`` when the entry fill carries none. ``atr_risk_usd`` is
    ``0.0`` when entry price / base_qty are unknowable (the caller then keeps the
    backfilled excursion R at 0). Returns ``(0.0, 0.0, 0.0)`` only when entry
    price is unknowable.
    """
    entry_price = trade.entry_price
    base_qty = trade.base_qty
    if trade.position_id:
        row = conn.execute(
            "SELECT fill_price, base_qty FROM fills WHERE contribution_id = ? "
            "AND instrument_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
            (trade.position_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()
        if row is not None:
            # Entry fill qty is the exact filled size — prefer it over the
            # in-memory trade.base_qty (which the partial-close path decrements).
            if entry_price <= 0.0 and row[0] is not None:
                entry_price = float(row[0])
            if row[1] is not None and float(row[1]) > 0.0:
                base_qty = float(row[1])
    if entry_price <= 0.0:
        return (0.0, 0.0, 0.0)
    # Entry-time ATR anchor first (one R denominator shared with pnl_r);
    # legacy NULL anchor → the recent-1m-bars estimate (pre-anchor behaviour).
    anchor = _entry_anchor_atr_pct(conn, trade=trade)
    if anchor is not None:
        atr_usd = max(entry_price * anchor * 2.0, entry_price * 1e-3)
    else:
        # Exclude FUTURE-dated bars (stale +10h Capital) so the recent-bar exit
        # mark and ATR window derive from real recent data, never a +10h ghost.
        ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
        bar_rows = conn.execute(
            """
            SELECT close, high, low FROM bars
            WHERE instrument_id = ? AND bar_interval = '1m' AND ts <= ?
            ORDER BY ts DESC LIMIT 14
            """,
            (f"{trade.venue}:{trade.symbol}", ts_upper),
        ).fetchall()
        atr_usd = max(entry_price * _atr_pct_from_bars(bar_rows) * 2.0, 1e-6)
    # Whole-position dollar 1R for the per-trade-ATR excursion ruler. 0.0 when
    # base_qty is unknowable → the caller keeps the backfilled excursion R at 0.
    atr_risk_usd = atr_usd * base_qty if base_qty > 0.0 else 0.0
    # Read tracked extremes; fall back to entry/exit bounds when the tick loop
    # has not populated them. peak = max(entry, exit, tracked_peak); trough =
    # min(entry, exit, tracked_trough) so the excursion is never under-stated.
    tracked_peak: float | None = None
    tracked_trough: float | None = None
    if trade.position_id:
        prow = conn.execute(
            "SELECT peak_price, trough_price FROM positions WHERE position_id = ?",
            (trade.position_id,),
        ).fetchone()
        if prow is not None:
            tracked_peak = None if prow[0] is None else float(prow[0])
            tracked_trough = None if prow[1] is None else float(prow[1])
    peak = max(entry_price, exit_price, tracked_peak or entry_price)
    trough = min(entry_price, exit_price, tracked_trough or entry_price)
    mfe_r, mae_r = compute_excursion_r(
        entry_price=entry_price, peak_price=peak, trough_price=trough,
        side=trade.side, atr_usd=atr_usd,
    )
    return (mfe_r, mae_r, atr_risk_usd)

def close_pnl_usd_total(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, fallback: float,
) -> float:
    """Cumulative realised ``pnl_usd`` over ALL persisted close fills of the
    position (the just-committed final slice included).

    The lineage segment must carry the POSITION total: with BUG A's slice
    stamping the final close fill holds only the LAST slice's pnl, so stamping
    that alone would under-state every partially-closed position. Fail-open:
    a read error (or no position_id) returns ``fallback`` (the final slice's
    own pnl — the legacy behaviour).
    """
    if not trade.position_id:
        return fallback
    try:
        row = conn.execute(
            "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = ? "
            "AND instrument_id = ? AND is_close = 1",
            (trade.position_id, f"{trade.venue}:{trade.symbol}"),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("[close] cumulative pnl_usd read failed: %r", exc)
        return fallback
    if row is None or row[0] is None:
        return fallback
    return float(row[0])


def _note_pending_close(trade: SimulatedTrade, ref: str) -> None:
    """BUG E bookkeeping: the close order is LIVE at the venue with its fill
    unconfirmed — store the ref so the next tick CONFIRMS it first (never
    re-fires a duplicate sell). The caller then falls into the no-fill tally so
    the zombie drain keeps its backstop on a forever-pending order."""
    trade.pending_close_ref = ref
    logger.info(
        "[close/real] %s:%s close order PENDING ref=%s — confirm-first next "
        "tick (no duplicate re-fire)",
        trade.venue, trade.symbol, ref,
    )


def _latest_bar_close(
    conn: sqlite3.Connection, *, venue: str, symbol: str,
) -> float | None:
    """Latest 1m bar close for ``venue:symbol`` (FIX A fresh close-split mark).

    Returns ``None`` when there is no bar so the caller falls back to the entry
    price. Read-only; never raises into the close path. FUTURE-dated bars (stale
    +10h Capital) are excluded so the close-split mark is a real recent close.
    """
    ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    row = conn.execute(
        "SELECT close FROM bars WHERE instrument_id = ? AND bar_interval = '1m' "
        "AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (f"{venue}:{symbol}", ts_upper),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    px = float(row[0])
    return px if px > 0.0 else None


def _persist_partial_close(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    trade: SimulatedTrade,
    close_fill: Fill,
    pnl_usd: float,
    now_ts: int,
    slice_pnl_r: float = 0.0,
) -> bool:
    """FIX B — persist a partial close that left the position OPEN.

    The venue filled only ``close_fill.base_qty`` of the tracked ``base_qty``
    (a mid-sequence child reject or a within-child partial). We persist the
    partial close fill (is_close=1, the sold qty + its pnl), decrement BOTH the
    durable ``positions.qty`` and the in-memory ``trade.base_qty`` by the filled
    amount, and keep ``status='open'`` so the exit engine closes the remainder
    next tick. The trade is NOT popped from ``state.open_trades``. Returns
    ``True`` on durable persist (state preserved + reduced), ``False`` on a DB
    error (no mutation — retry next tick).

    P2 R-ledger-leak fix — ``slice_pnl_r`` (this slice's stream-common R) is
    ACCUMULATED onto ``positions.pnl_r`` in the SAME txn (``COALESCE(pnl_r,0) +
    slice``). A position that closes predominantly via partials (LUNA / DOT) no
    longer leaves ``pnl_r`` NULL — the realised R sums across slices to the
    whole-position R (``realised_r_stream`` is linear in pnl_usd, so the slice
    stamps sum exactly). Atomic with the fill persist + qty decrement so the
    durable R never diverges from the persisted slice $. Measurement only.
    """
    filled = close_fill.base_qty
    remaining = max(0.0, trade.base_qty - filled)
    try:
        conn.execute("BEGIN IMMEDIATE")
        persist_fill(
            conn, close_fill, is_close=True, pnl_usd=pnl_usd,
            contribution_id=trade.position_id,
        )
        if trade.position_id:
            conn.execute(
                "UPDATE positions SET qty = ?, "
                "pnl_r = COALESCE(pnl_r, 0.0) + ? WHERE position_id = ?",
                (remaining, slice_pnl_r, trade.position_id),
            )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error("[close/partial] persist failed (state preserved): %r", exc)
        record_fault(
            conn, strategy_id=trade.strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": "persist_partial_close", "exc": str(exc)},
        )
        state.fault_events += 1
        return False
    trade.base_qty = remaining
    state.fills_close += 1
    logger.info(
        "[close/partial] %s:%s trade_id=%s filled %.10f of %.10f base "
        "(remaining %.10f) pnl_usd=%.2f — position kept OPEN, remainder closes "
        "next tick",
        trade.venue, trade.symbol, trade.position_id or "-",
        filled, filled + remaining, remaining, pnl_usd,
    )
    return True


def _reconcile_orphan(
    conn: sqlite3.Connection,
    *,
    state: ProdLoopState,
    trade: SimulatedTrade,
    trade_idx: int,
    available: float,
    now_ts: int,
) -> bool:
    """FIX 2 — mark a true-orphan position terminal so it stops being retried.

    ``status='reconciled'`` denotes VENUE-SIDE STATE-DRIFT RECOVERY (the wallet
    no longer holds the tracked base_qty — an over-count), NOT a real-fill close:
    NO close fill is persisted. Step M (2026-06-22, Jin locked): a reconcile is a
    TRACKING FAILURE, not a trade — its ``pnl_r`` is LEFT NULL so it is EXCLUDED
    from every R aggregation (PF/WR/avg_r/ticker R). The drift is instead
    surfaced as a SEPARATE counter: a rough DOLLAR estimate (``mae_r ×
    risk_usd``) is recorded in the audit row + ``risk_events`` so the dashboard
    can show "tracking failures, not trades" alongside (never mixed into) the
    real-fill ledger. This REVERTS the earlier mae→pnl_r honesty stamp that the
    design audit found INFLATED the R ledger (−211R artefact). The position is
    popped from ``state.open_trades`` (NOT appended to ``closed_trades`` — no
    learner feed), and an ``orphan_reconciled`` audit row is written to
    ``risk_events``. Returns ``True`` (handled). Idempotent. flow_not_block.
    """
    est_drift_usd = 0.0
    try:
        conn.execute("BEGIN IMMEDIATE")
        if trade.position_id:
            # Rough DOLLAR drift estimate (display-only): worst adverse
            # excursion (mae_r, ≤0) × the persisted 1R-in-dollars. Recorded in
            # the audit row only — NOT stamped into pnl_r (a tracking failure is
            # excluded from the R ledger). risk_usd / mae_r NULL → 0.0 estimate.
            row = conn.execute(
                "SELECT mae_r, risk_usd FROM positions WHERE position_id = ?",
                (trade.position_id,),
            ).fetchone()
            if row is not None and row[0] is not None and row[1] is not None:
                est_drift_usd = min(0.0, float(row[0])) * float(row[1])
            # pnl_r stays NULL — excluded from R sums. Only the lifecycle marker
            # is written so the position stops being retried.
            conn.execute(
                "UPDATE positions SET status = 'reconciled', closed_ts = ?, "
                "exit_state = 'reconciled' WHERE position_id = ?",
                (now_ts, trade.position_id),
            )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[close/orphan] %s:%s reconcile UPDATE failed (state preserved): %r",
            trade.venue, trade.symbol, exc,
        )
        state.venue_close_rejects += 1
        return False
    # Pop from open_trades by identity (defensive re-find, mirrors the close path).
    if 0 <= trade_idx < len(state.open_trades) and state.open_trades[trade_idx] is trade:
        state.open_trades.pop(trade_idx)
    else:
        with contextlib.suppress(ValueError):
            state.open_trades.remove(trade)
    trade.closed = True
    # Audit row (best-effort) — durable record of the reconcile for forensics.
    audit = json.dumps(
        {
            "venue": trade.venue, "symbol": trade.symbol,
            "position_id": trade.position_id, "base_qty": trade.base_qty,
            "available": available, "est_drift_usd": est_drift_usd,
            "reason": "available~0 qty over-count",
        },
        separators=(",", ":"),
    )
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, 'orphan_reconciled', ?, ?)",
            (uuid.uuid4().hex, trade.strategy_id, now_ts, audit),
        )
    logger.warning(
        "[close/orphan] %s:%s trade_id=%s reconciled (available=%.10f ~0, "
        "base_qty=%.10f over-count) — status='reconciled', no fill, pnl_r=NULL "
        "(tracking failure, excluded from R ledger), est_drift_usd=%.2f; exit "
        "engine + hydrate stop retrying (state-drift recovery)",
        trade.venue, trade.symbol, trade.position_id or "-", available,
        trade.base_qty, est_drift_usd,
    )
    return True
