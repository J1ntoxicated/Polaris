"""Polaris dashboard v1 — per-strategy + per-ticker stat queries.

Stream-common realised-R per closed position and the per-strategy / per-ticker
rollups (WR/PF/avg-R/$) built on top of it. Split out of ``snapshot_queries.py``
to keep each module ≤500 LOC (move-only; no logic change). Display-only — never
a trading path.
"""

from __future__ import annotations

import sqlite3
from typing import Final, NamedTuple

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


def _strategy_class_by_id(conn: sqlite3.Connection) -> dict[str, str]:
    """{strategy_id: strategy_class} — best-effort, ignores the venue dimension.

    ``strategy_class`` is keyed ``(venue, strategy_id)`` but in practice each
    registered strategy trades exactly one venue, so a plain ``strategy_id``
    lookup is unambiguous for every real row (a strategy_id present under >1
    venue keeps its FIRST row — display-only, never a sizing read). Missing
    table / no row → absent from the map; the caller falls back to the same
    "EARN" default ``resolve_strategy_class`` uses on a bootstrap gap."""
    rows = _safe_query(
        conn, "SELECT strategy_id, strategy_class FROM strategy_class"
    )
    out: dict[str, str] = {}
    for strategy_id, cls in rows:
        sid = str(strategy_id)
        if sid not in out:
            out[sid] = str(cls or "EARN")
    return out


_SIGNALS_24H_LOOKBACK_SEC: Final = 86_400


def _signal_activity_by_strategy(
    conn: sqlite3.Connection, *, now_s: int
) -> dict[str, tuple[int, int]]:
    """{strategy_id: (signals_24h, last_signal_ts)} — "why quiet" evidence.

    ``signals_24h`` = distinct signal_id count in the last 24h; ``last_signal_ts``
    = the strategy's most-recent signal ts (0 = never signalled, ANY window).
    Two queries (24h window + all-time max) so a strategy that last fired 3 days
    ago still shows its true last-signal age instead of a false 0. Display-only;
    never a trading path."""
    since = now_s - _SIGNALS_24H_LOOKBACK_SEC
    recent_rows = _safe_query(
        conn,
        """SELECT strategy_id, COUNT(DISTINCT signal_id) FROM signals
           WHERE ts >= ? GROUP BY strategy_id""",
        (since,),
    )
    recent_n: dict[str, int] = {str(r[0]): int(r[1] or 0) for r in recent_rows}
    last_rows = _safe_query(
        conn,
        "SELECT strategy_id, MAX(ts) FROM signals GROUP BY strategy_id",
    )
    out: dict[str, tuple[int, int]] = {}
    for strategy_id, last_ts in last_rows:
        sid = str(strategy_id)
        out[sid] = (recent_n.get(sid, 0), int(last_ts or 0))
    # A strategy with 24h signals but somehow missing from last_rows (shouldn't
    # happen — MAX(ts) always covers it — kept for defensive completeness).
    for sid, n in recent_n.items():
        if sid not in out:
            out[sid] = (n, now_s)
    return out


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
        # audit2 P0-2 ②: win_rate/PF are now NET of the REAL round-trip fee
        # (``entry_fee`` CTE sums the position's is_close=0 leg fee, subtracted
        # alongside this close leg's own fee), matching the core
        # ``won = pnl_r_net > 0`` verdict direction. The old GROSS
        # ``f.pnl_usd > 0`` classifier showed a fee≈gross scalp as a winner (live
        # 2026-07-02 audit: gross 29 wins vs net 9 wins on the same 105 closes).
        # ``pnl_total`` stays GROSS (the fills.pnl_usd truth column, unchanged)
        # — only the win/PF classification is fee-net.
        """WITH entry_fee AS (
             SELECT contribution_id AS pid, SUM(fee_usd) AS fee
             FROM fills WHERE is_close = 0 GROUP BY contribution_id
           ), net AS (
             SELECT f.strategy_id AS strategy_id,
                    f.pnl_usd - f.fee_usd - COALESCE(ef.fee, 0.0) AS net_pnl,
                    f.pnl_usd AS gross_pnl
             FROM fills f
             LEFT JOIN positions p ON p.position_id = f.contribution_id
             LEFT JOIN entry_fee ef ON ef.pid = f.contribution_id
             WHERE f.is_close = 1 AND f.ts_ms >= ?
               AND (p.status IS NULL OR p.status != 'reconciled')
           )
           SELECT strategy_id,
                  COUNT(*) AS closed_n,
                  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                  COALESCE(SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END), 0.0) AS net_win,
                  COALESCE(SUM(CASE WHEN net_pnl < 0 THEN -net_pnl ELSE 0 END), 0.0) AS net_loss,
                  COALESCE(SUM(gross_pnl), 0.0) AS pnl_total
           FROM net GROUP BY strategy_id""",
        (lookback_ms,),
    )
    # Step M (2026-06-22): avg_r is the canonical risk-unit R (pnl_usd/risk_usd),
    # sourced from positions.pnl_r — the SAME R the ticker panel + close path use,
    # so a strategy shows ONE consistent R everywhere (not a flat $10 proxy).
    # Reconciled (tracking-failure) rows are excluded (pnl_r NULL there).
    avg_r_by_strat = _avg_r_by_strategy(conn)
    class_by_id = _strategy_class_by_id(conn)
    signal_activity = _signal_activity_by_strategy(conn, now_s=now_s)

    def _new_stat(sid: str) -> StrategyStat:
        signals_24h, last_signal_ts = signal_activity.get(sid, (0, 0))
        return StrategyStat(
            strategy_id=sid, open_n=0, closed_n=0, wr_pct=0.0,
            avg_r=0.0, pf=0.0, pnl_usd=0.0, notional_usd=0.0,
            strategy_class=class_by_id.get(sid, "EARN"),
            signals_24h=signals_24h, last_signal_ts=last_signal_ts,
        )

    by_strat: dict[str, StrategyStat] = {}
    for r in rows:
        sid = str(r[0])
        closed_n = int(r[1] or 0)
        wins = int(r[2] or 0)
        net_win = float(r[3] or 0.0)
        net_loss = float(r[4] or 0.0)
        pnl_total = float(r[5] or 0.0)
        wr = (wins / closed_n * 100.0) if closed_n else 0.0
        avg_r = avg_r_by_strat.get(sid, 0.0)
        pf = (net_win / net_loss) if net_loss > 0 else (net_win and 9.99 or 0.0)
        s = _new_stat(sid)
        s.closed_n = closed_n
        s.wr_pct = wr
        s.avg_r = avg_r
        s.pf = pf
        s.pnl_usd = pnl_total
        by_strat[sid] = s

    # Layer in open-position counts + notional
    for p in positions:
        s = by_strat.get(p.strategy_id) or _new_stat(p.strategy_id)
        s.open_n += 1
        s.notional_usd += p.size_usd
        by_strat[p.strategy_id] = s

    # SIGNAL/SETUP STATE ("why quiet"): a strategy that has signalled but never
    # traded (or hasn't in this session's fills window) still belongs on the
    # roster — otherwise a slow-trend book with 3 signals/24h and 0 trades
    # would silently vanish from the board instead of reading as "quiet by
    # design". Display-only; never feeds sizing/gating.
    for sid in signal_activity:
        if sid not in by_strat:
            by_strat[sid] = _new_stat(sid)

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
