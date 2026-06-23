"""Polaris dashboard v1 — per-strategy + per-ticker stat queries.

Stream-common realised-R per closed position and the per-strategy / per-ticker
rollups (WR/PF/avg-R/$) built on top of it. Split out of ``snapshot_queries.py``
to keep each module ≤500 LOC (move-only; no logic change). Display-only — never
a trading path.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

from polaris.core.metrics.risk_unit import realised_r_stream
from polaris.scripts.dashboard.snapshot_models import (
    PositionRow,
    StrategyStat,
    TickerStat,
)
from polaris.scripts.dashboard.snapshot_q_common import (
    _safe_query,
    _session_start_ms,
)


class _ClosedR(NamedTuple):
    """One closed position's stream-common realised R + its grouping keys.

    ``r`` is RE-DERIVED at read time from the position's realised
    ``fills.pnl_usd`` divided by the per-stream ``R_budget`` (Step N), NOT read
    from the persisted ``positions.pnl_r``. This makes OLD rows (written under
    the venue-skewed per-trade-ATR denominator) and NEW rows comparable on the
    SAME stream-common ruler without a destructive migration — the dashboard is
    self-consistent regardless of when a row was stamped.
    """

    strategy_id: str
    venue: str
    symbol: str
    pnl_usd: float
    r: float


def _closed_position_r(conn: sqlite3.Connection) -> list[_ClosedR]:
    """Stream-common realised R per CLOSED position, derived from fills.pnl_usd.

    Joins each ``status='closed'`` position to the cumulative realised
    ``Σ fills.pnl_usd`` of its close legs (``contribution_id = position_id``,
    ``is_close = 1``) and rescales by the per-stream ``R_budget(venue)``.
    RECONCILED (tracking-failure) positions are excluded by the ``status='closed'``
    filter — a reconcile is not a trade; its drift is a SEPARATE $ counter, never
    summed into R. A position with no matched close fill is skipped (no $ truth).
    Graceful empty on a missing table. Read-only; never a trading path."""
    rows = _safe_query(
        conn,
        """SELECT p.strategy_id, p.venue, p.symbol,
                  COALESCE(SUM(f.pnl_usd), 0.0) AS pnl_usd,
                  COUNT(f.fill_id) AS n_close
           FROM positions p
           JOIN fills f
             ON f.contribution_id = p.position_id AND f.is_close = 1
           WHERE p.status = 'closed'
           GROUP BY p.position_id""",
    )
    out: list[_ClosedR] = []
    for r in rows:
        if int(r[4] or 0) <= 0:
            continue
        venue = str(r[1] or "")
        pnl_usd = float(r[3] or 0.0)
        out.append(_ClosedR(
            strategy_id=str(r[0] or ""),
            venue=venue,
            symbol=str(r[2] or ""),
            pnl_usd=pnl_usd,
            r=realised_r_stream(pnl_usd=pnl_usd, venue=venue),
        ))
    return out


def _avg_r_by_strategy(conn: sqlite3.Connection) -> dict[str, float]:
    """{strategy_id: mean stream-common R} over CLOSED positions (Step N).

    R is the stream-common realised R (``pnl_usd / R_budget(stream)``) re-derived
    from ``fills.pnl_usd`` per closed position, so the per-strategy avg_r is
    comparable across venues and matches the per-ticker R + the close path — one
    definition everywhere, no venue-skewed ATR denominator, no flat proxy.
    Reconciled (tracking-failure) rows are excluded. Graceful empty when absent."""
    by_strat: dict[str, list[float]] = {}
    for row in _closed_position_r(conn):
        by_strat.setdefault(row.strategy_id, []).append(row.r)
    return {
        sid: (sum(rs) / len(rs) if rs else 0.0)
        for sid, rs in by_strat.items()
    }


def _strategy_stats(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    positions: list[PositionRow],
) -> list[StrategyStat]:
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    rows = _safe_query(
        conn,
        # Hardening #1 (2026-06-23): exclude RECONCILED close fills (tracking
        # failures) from the per-strategy WR/PF/count/$ so they match the avg_r
        # ledger (which already excludes them). LEFT JOIN; orphan fills KEPT.
        """SELECT f.strategy_id,
                  COUNT(*) AS closed_n,
                  SUM(CASE WHEN f.pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
                  COALESCE(SUM(CASE WHEN f.pnl_usd > 0 THEN f.pnl_usd ELSE 0 END), 0.0) AS gross_win,
                  COALESCE(SUM(CASE WHEN f.pnl_usd < 0 THEN -f.pnl_usd ELSE 0 END), 0.0) AS gross_loss,
                  COALESCE(SUM(f.pnl_usd), 0.0) AS pnl_total
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.is_close = 1 AND f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')
           GROUP BY f.strategy_id""",
        (lookback_ms,),
    )
    # Step M (2026-06-22): avg_r is the canonical risk-unit R (pnl_usd/risk_usd),
    # sourced from positions.pnl_r — the SAME R the ticker panel + close path use,
    # so a strategy shows ONE consistent R everywhere (not a flat $10 proxy).
    # Reconciled (tracking-failure) rows are excluded (pnl_r NULL there).
    avg_r_by_strat = _avg_r_by_strategy(conn)
    by_strat: dict[str, StrategyStat] = {}
    for r in rows:
        sid = str(r[0])
        closed_n = int(r[1] or 0)
        wins = int(r[2] or 0)
        gross_win = float(r[3] or 0.0)
        gross_loss = float(r[4] or 0.0)
        pnl_total = float(r[5] or 0.0)
        wr = (wins / closed_n * 100.0) if closed_n else 0.0
        avg_r = avg_r_by_strat.get(sid, 0.0)
        pf = (gross_win / gross_loss) if gross_loss > 0 else (gross_win and 9.99 or 0.0)
        by_strat[sid] = StrategyStat(
            strategy_id=sid,
            open_n=0,
            closed_n=closed_n,
            wr_pct=wr,
            avg_r=avg_r,
            pf=pf,
            pnl_usd=pnl_total,
            notional_usd=0.0,
        )

    # Layer in open-position counts + notional
    for p in positions:
        s = by_strat.get(p.strategy_id) or StrategyStat(
            strategy_id=p.strategy_id, open_n=0, closed_n=0, wr_pct=0.0,
            avg_r=0.0, pf=0.0, pnl_usd=0.0, notional_usd=0.0,
        )
        s.open_n += 1
        s.notional_usd += p.size_usd
        by_strat[p.strategy_id] = s

    out = sorted(by_strat.values(), key=lambda s: s.pnl_usd, reverse=True)
    return out


def _ticker_stats(
    conn: sqlite3.Connection, *, now_s: int, limit: int = 14,
) -> list[TickerStat]:
    """Step N (2026-06-23): per-ticker cumulative STREAM-COMMON realized R.

    R is the stream-common realised R (``fills.pnl_usd / R_budget(stream)``)
    re-derived per closed position, so the per-symbol R bleeders AGREE with the
    dollar bleeders AND are comparable across venues (no venue-skewed ATR
    denominator). RECONCILED (tracking-failure) positions are EXCLUDED — a
    reconcile is not a trade; its drift is surfaced as a SEPARATE $ counter,
    never mixed into R/WR. Sorted worst-first; display-only; never a trading path."""
    agg: dict[tuple[str, str], list[float]] = {}
    for row in _closed_position_r(conn):
        agg.setdefault((row.venue, row.symbol), []).append(row.r)
    out: list[TickerStat] = []
    for (venue, symbol), rs in agg.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0.0)
        out.append(TickerStat(
            venue=venue, symbol=symbol, n=n,
            wr_pct=(wins / n * 100.0) if n else 0.0,
            sum_r=sum(rs),
        ))
    out.sort(key=lambda t: t.sum_r)  # worst (most negative R) first
    return out[:limit]
