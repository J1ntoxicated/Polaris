"""Polaris dashboard v1 — measurement-reset baseline queries (forward edge).

Closed-since-reset rollups (PF / net$ / win% / avg-R), the close_reason ×
cadence split, and the per-strategy since-reset edge. Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC (move-only; no logic
change). Display-only — never a trading path.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

from polaris.core.metrics.risk_unit import realised_r_stream
from polaris.scripts.dashboard.snapshot_models import (
    CadenceReasonRow,
    SinceResetRollup,
    StrategySinceReset,
)
from polaris.scripts.dashboard.snapshot_q_common import _safe_query
from polaris.storage.measurement_reset import MeasurementReset, latest_reset


class _SinceResetRow(NamedTuple):
    """One CLOSED position opened AT/AFTER the reset_ts + its fee-net realised $.

    ``net_usd`` = Σ(close pnl) − Σ(close fee) over the position's close legs (the
    fills.$ truth). ``r`` = stream-common realised R from the GROSS close pnl
    (matches every other R panel — R is a pure rescale of the gross $)."""

    strategy_id: str
    venue: str
    gross_pnl: float
    net_usd: float
    r: float


def _closed_since_reset(
    conn: sqlite3.Connection, *, reset_ts: int
) -> list[_SinceResetRow]:
    """CLOSED positions OPENED at/after ``reset_ts`` joined to their close fills.

    The since-reset window key is ``positions.opened_ts >= reset_ts`` — a trade
    counts only if it was OPENED under the new logic (not merely closed under it).
    RECONCILED (tracking-failure) rows are excluded by ``status='closed'`` exactly
    like the all-time ticker/strategy rollups (a reconcile is not a trade). A
    position with no matched close fill is skipped (no $ truth). Read-only."""
    rows = _safe_query(
        conn,
        """SELECT p.strategy_id, p.venue,
                  COALESCE(SUM(f.pnl_usd), 0.0) AS gross_pnl,
                  COALESCE(SUM(f.fee_usd), 0.0) AS fee_total,
                  COUNT(f.fill_id) AS n_close
           FROM positions p
           JOIN fills f
             ON f.contribution_id = p.position_id AND f.is_close = 1
           WHERE p.status = 'closed' AND p.opened_ts >= ?
           GROUP BY p.position_id""",
        (int(reset_ts),),
    )
    out: list[_SinceResetRow] = []
    for r in rows:
        if int(r[4] or 0) <= 0:
            continue
        venue = str(r[1] or "")
        gross = float(r[2] or 0.0)
        fee = float(r[3] or 0.0)
        out.append(_SinceResetRow(
            strategy_id=str(r[0] or ""),
            venue=venue,
            gross_pnl=gross,
            net_usd=gross - fee,
            r=realised_r_stream(pnl_usd=gross, venue=venue),
        ))
    return out


def _cadence_reason_split(
    conn: sqlite3.Connection, *, reset_ts: int
) -> list[CadenceReasonRow]:
    """close_reason × exit_cadence split over the since-reset window (hardening #7).

    Groups CLOSED, since-reset positions by the lineage ``exit_reason``
    (close_reason) and ``positions.exit_cadence`` (bar / tick / unknown when the
    legacy column is NULL), summing the fee-net realised $ per cell. Same window
    key + RECONCILED exclusion as ``_closed_since_reset``. The split surfaces the
    bar-vs-tick thesis-cut asymmetry before any streak threading. Read-only;
    sorted by n desc then net$ asc. Never a trading path."""
    rows = _safe_query(
        conn,
        """SELECT COALESCE(NULLIF(s.exit_reason, ''), 'exit') AS reason,
                  COALESCE(NULLIF(p.exit_cadence, ''), 'unknown') AS cadence,
                  COUNT(DISTINCT p.position_id) AS n,
                  COALESCE(SUM(f.pnl_usd), 0.0) AS gross,
                  COALESCE(SUM(f.fee_usd), 0.0) AS fee
           FROM positions p
           JOIN fills f
             ON f.contribution_id = p.position_id AND f.is_close = 1
           LEFT JOIN position_strategy_segments s
             ON s.position_id = p.position_id
           WHERE p.status = 'closed' AND p.opened_ts >= ?
           GROUP BY reason, cadence
           ORDER BY n DESC, (SUM(f.pnl_usd) - SUM(f.fee_usd)) ASC""",
        (int(reset_ts),),
    )
    out: list[CadenceReasonRow] = []
    for r in rows:
        out.append(CadenceReasonRow(
            close_reason=str(r[0] or "exit"),
            cadence=str(r[1] or "unknown"),
            n=int(r[2] or 0),
            net_usd=float(r[3] or 0.0) - float(r[4] or 0.0),
        ))
    return out


def _rollup_metrics(
    rows: list[_SinceResetRow],
) -> tuple[int, float, float, float, float]:
    """(n, pf, net_usd, win_pct, avg_r) over a since-reset row list.

    PF is over the GROSS close pnl (Σ win / Σ loss), matching the all-time
    confidence/strategy PF. net_usd is the fee-net realised $. win_pct counts a
    trade a win on positive GROSS pnl. avg_r is the mean stream-common R."""
    n = len(rows)
    if n == 0:
        return 0, 0.0, 0.0, 0.0, 0.0
    gross_win = sum(x.gross_pnl for x in rows if x.gross_pnl > 0.0)
    gross_loss = sum(-x.gross_pnl for x in rows if x.gross_pnl < 0.0)
    wins = sum(1 for x in rows if x.gross_pnl > 0.0)
    net_usd = sum(x.net_usd for x in rows)
    avg_r = sum(x.r for x in rows) / n
    win_pct = wins / n * 100.0
    pf = (gross_win / gross_loss) if gross_loss > 0.0 else (9.99 if gross_win > 0.0 else 0.0)
    return n, pf, net_usd, win_pct, avg_r


def _since_reset_rollup(conn: sqlite3.Connection) -> SinceResetRollup | None:
    """Forward edge since the LATEST measurement reset, or None when none stamped.

    Measures PF / net$ (fee-net) / win% / avg-R / equity-change over the trades
    OPENED at/after the latest ``measurement_resets.reset_ts`` — the new-logic
    window Jin reads. None when no reset exists → the board falls back to all-time
    cleanly (the ALL-TIME panels stay available regardless). Display-only; never a
    trading path."""
    reset: MeasurementReset | None = latest_reset(conn)
    if reset is None:
        return None
    rows = _closed_since_reset(conn, reset_ts=reset.reset_ts)
    n, pf, net_usd, win_pct, avg_r = _rollup_metrics(rows)
    return SinceResetRollup(
        reset_ts=reset.reset_ts,
        label=reset.label,
        git_sha=reset.git_sha,
        equity_baseline_usd=reset.equity_baseline_usd,
        n=n,
        pf=pf,
        net_usd=net_usd,
        win_pct=win_pct,
        avg_r=avg_r,
        # Realised fee-net change over the window = since-reset net$ (the baseline
        # is the equity at the reset ts; equity_now == baseline + this change).
        equity_change_usd=net_usd,
        # Hardening #7: close_reason × cadence split (bar vs tick) over the window.
        cadence_split=_cadence_reason_split(conn, reset_ts=reset.reset_ts),
    )


def _strategy_since_reset(
    conn: sqlite3.Connection,
) -> list[StrategySinceReset]:
    """Per-strategy forward edge since the latest reset (empty when none stamped).

    Same window key as ``_since_reset_rollup`` (``opened_ts >= reset_ts``), sliced
    by strategy_id so the per-strategy table shows the new-logic edge next to the
    all-time row. Sorted by net$ desc. Display-only."""
    reset = latest_reset(conn)
    if reset is None:
        return []
    by_strat: dict[str, list[_SinceResetRow]] = {}
    for row in _closed_since_reset(conn, reset_ts=reset.reset_ts):
        by_strat.setdefault(row.strategy_id, []).append(row)
    out: list[StrategySinceReset] = []
    for sid, rows in by_strat.items():
        n, pf, net_usd, win_pct, avg_r = _rollup_metrics(rows)
        out.append(StrategySinceReset(
            strategy_id=sid, n=n, wr_pct=win_pct, pf=pf,
            avg_r=avg_r, net_usd=net_usd,
        ))
    out.sort(key=lambda s: s.net_usd, reverse=True)
    return out
