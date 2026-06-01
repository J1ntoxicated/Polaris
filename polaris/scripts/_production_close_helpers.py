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
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.isolation.circuit_breaker import FAULT_EXCEPTION, record_fault
from polaris.core.live_recalc.excursion import compute_excursion_r

if TYPE_CHECKING:
    from polaris.scripts._production_state import ProdLoopState
    from polaris.scripts._smoke_fills import SimulatedTrade

logger = logging.getLogger(__name__)


def real_pnl_r_from_fills(
    conn: sqlite3.Connection, *, trade: SimulatedTrade,
    exit_price_override: float | None = None,
) -> tuple[float, float, float]:
    """Read entry fill + most recent bars; compute R-units from real bar drift.

    Returns ``(pnl_r, pnl_usd, exit_price)``. When the bar history is too
    short the R denominator falls back to ``entry_price × 0.5%`` so the
    calculation is finite — but the magnitude reflects the *actual* close
    drift, not a hard-coded sign.

    ``exit_price_override`` (P1-6 venue-wire fix): when set (real-roundtrip
    close), ``pnl_r`` / ``pnl_usd`` are computed against the **real exit fill
    price** instead of the most recent bar close, using the same ATR
    denominator. This keeps Layer 4/5 telemetry consistent with what was
    actually traded — without it a loss exit could be logged as a win when
    the seeded bars happened to trend the other way.

    Day 8 codex P0 fix: matches the entry fill by ``contribution_id =
    position_id`` so two trades on the same (strategy, instrument) can never
    cross-price. Falls back to the legacy heuristic only when the trade has
    no ``position_id`` set (legacy callers).
    """
    if trade.position_id:
        row = conn.execute(
            """
            SELECT fill_price, size_usd FROM fills
            WHERE contribution_id = ? AND is_close = 0
            ORDER BY ts_ms ASC LIMIT 1
            """,
            (trade.position_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT fill_price, size_usd FROM fills
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
    trade.entry_price = entry_price
    bar_rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE instrument_id = ? AND bar_interval = '1m'
        ORDER BY ts DESC LIMIT 14
        """,
        (f"{trade.venue}:{trade.symbol}",),
    ).fetchall()
    # P1-6: an override exit price must drive pnl_r/pnl_usd even when there is
    # no bar history (the real close already gives us the true exit level).
    if not bar_rows and exit_price_override is None:
        return (0.0, 0.0, entry_price)
    bar_close = float(bar_rows[0][0]) if bar_rows else entry_price
    exit_price = exit_price_override if exit_price_override else bar_close
    if entry_price <= 0.0 or exit_price <= 0.0:
        return (0.0, 0.0, entry_price)
    atr_pct = _atr_pct_from_bars(bar_rows)
    pnl_abs = (
        (exit_price - entry_price)
        if trade.side == "long"
        else (entry_price - exit_price)
    )
    atr_usd = max(entry_price * atr_pct * 2.0, 1e-6)
    pnl_r = pnl_abs / atr_usd
    pnl_usd = (pnl_abs / entry_price) * size_usd
    pnl_r = max(-10.0, min(10.0, pnl_r))
    return (pnl_r, pnl_usd, exit_price)


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
    return sum(samples) / len(samples) if samples else 0.005


def _close_excursion_r(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, exit_price: float,
) -> tuple[float, float]:
    """Best-effort ``(mfe_r, mae_r)`` for a position at close time.

    BUILD_SCHEMA prerequisite: the tick loop does not yet populate the
    ``positions.peak_price`` / ``trough_price`` extremes (a later precise-exit
    stream owns that). So this reads whatever extremes the position row carries
    and falls back to the observed entry/exit bounds when they are NULL — a
    position that only ever recorded its entry and exit still yields a finite,
    correctly-signed excursion (never *under*-states MFE/MAE relative to the
    realised move). The R denominator is re-derived from the same entry fill +
    recent bars as ``real_pnl_r_from_fills`` so pnl_r and mfe_r/mae_r share one
    risk unit. Returns ``(0.0, 0.0)`` only when entry price is unknowable.
    """
    entry_price = trade.entry_price
    if entry_price <= 0.0:
        row = conn.execute(
            "SELECT fill_price FROM fills WHERE contribution_id = ? "
            "AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
            (trade.position_id,),
        ).fetchone() if trade.position_id else None
        if row is None:
            return (0.0, 0.0)
        entry_price = float(row[0])
    if entry_price <= 0.0:
        return (0.0, 0.0)
    bar_rows = conn.execute(
        """
        SELECT close, high, low FROM bars
        WHERE instrument_id = ? AND bar_interval = '1m'
        ORDER BY ts DESC LIMIT 14
        """,
        (f"{trade.venue}:{trade.symbol}",),
    ).fetchall()
    atr_usd = max(entry_price * _atr_pct_from_bars(bar_rows) * 2.0, 1e-6)
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
    return compute_excursion_r(
        entry_price=entry_price, peak_price=peak, trough_price=trough,
        side=trade.side, atr_usd=atr_usd,
    )

# FIX B: a real close fill within this fraction of the tracked qty counts as a
# FULL close (rounding/fee dust). Anything below is a genuine partial → the
# position stays OPEN with a reduced qty so the remainder closes next tick.
_CLOSE_FULL_FILL_EPS = 0.005


def _latest_bar_close(
    conn: sqlite3.Connection, *, venue: str, symbol: str,
) -> float | None:
    """Latest 1m bar close for ``venue:symbol`` (FIX A fresh close-split mark).

    Returns ``None`` when there is no bar so the caller falls back to the entry
    price. Read-only; never raises into the close path.
    """
    row = conn.execute(
        "SELECT close FROM bars WHERE instrument_id = ? AND bar_interval = '1m' "
        "ORDER BY ts DESC LIMIT 1",
        (f"{venue}:{symbol}",),
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
                "UPDATE positions SET qty = ? WHERE position_id = ?",
                (remaining, trade.position_id),
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
