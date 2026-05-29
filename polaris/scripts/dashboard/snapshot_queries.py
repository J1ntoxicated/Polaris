"""Polaris dashboard v1 — snapshot section queries (DB → typed row lists).

Per-section best-effort read helpers consumed by ``snapshot.collect_snapshot``.
All queries best-effort: missing tables / empty data return zero-defaults so the
dashboard never crashes a paper loop. Split out of ``snapshot.py`` to keep each
module ≤500 LOC; ``snapshot`` re-exports ``_read_positions`` for tests that
reference it via the original path.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from typing import Any, Final

from polaris.scripts.dashboard.snapshot_models import (
    PositionRow,
    StrategyStat,
)

DEFAULT_R_USD: Final[float] = 10.0          # 1 R = $10 (display heuristic)
EQUITY_BUCKET_SEC: Final[int] = 5 * 60       # legacy 5-min bucket (24h fallback)
EQUITY_LOOKBACK_SEC: Final[int] = 24 * 3600  # legacy 24h window (fallback only)
GATE_FUNNEL_LOOKBACK_SEC: Final[int] = 3600
GPT_LOOKBACK_SEC: Final[int] = 3600
LEARNER_DELTA_LOOKBACK_SEC: Final[int] = 3600

# Session-anchored curve config (Jin 2026-05-29): all PnL/DD/Sharpe lookbacks
# start at the session anchor (clean restart → DB is the current session), not
# a rolling 24h window. The session is split into N buckets, floor 60s each.
EQUITY_TARGET_BUCKETS: Final[int] = 288      # target resolution across the session
EQUITY_MIN_BUCKET_SEC: Final[int] = 60       # floor bucket size

# GPT pricing (USD per 1K tokens) — gpt-5-mini & gpt-5.5 ballpark for projection.
GPT_PRICE_PER_1K: Final[Mapping[str, float]] = {
    "gpt-5-mini": 0.000150,
    "gpt-5.5": 0.005,
    "gpt": 0.000150,           # default mini for unlabelled rows
}
GPT_TOKENS_PER_CALL: Final[int] = 1500       # heuristic — average prompt+completion


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _safe_query(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _now_s() -> int:
    return int(time.time())


def _session_start_ms(conn: sqlite3.Connection, *, now_s: int) -> int:
    """Session anchor in ms = earliest fill in the DB (clean restart → the DB is
    the current session). Falls back to ``now`` when no fills exist yet.

    All session-scoped PnL/equity/DD/Sharpe lookbacks start here instead of a
    rolling 24h window (Jin 2026-05-29).
    """
    rows = _safe_query(conn, "SELECT MIN(ts_ms) FROM fills")
    if rows and rows[0][0] is not None:
        return int(rows[0][0])
    return now_s * 1000


def _session_buckets(session_start_s: int, now_s: int) -> tuple[list[int], int]:
    """Split [session_start, now] into bucket end-timestamps (s) + bucket size.

    Session length is divided into ``EQUITY_TARGET_BUCKETS`` buckets, each at
    least ``EQUITY_MIN_BUCKET_SEC`` wide. Returns (bucket_ts, bucket_sec).
    """
    span = max(1, now_s - session_start_s)
    bucket_sec = max(EQUITY_MIN_BUCKET_SEC, span // EQUITY_TARGET_BUCKETS)
    n_buckets = max(1, (span + bucket_sec - 1) // bucket_sec)
    bucket_ts = [session_start_s + (i + 1) * bucket_sec for i in range(n_buckets)]
    return bucket_ts, bucket_sec


def _strategy_label(s: str | None) -> str:
    return (s or "?")[:18]


def _symbol_from_inst(inst: str | None) -> str:
    if not inst:
        return "?"
    return inst.split(":", 1)[-1] if ":" in inst else inst


# ---------------------------------------------------------------------------
# Section: equity / pnl / drawdown / sharpe
# ---------------------------------------------------------------------------


def _build_equity_curve(
    conn: sqlite3.Connection, *, now_s: int, starting_capital: float
) -> tuple[list[int], list[float], float]:
    """Build the SESSION equity curve from realised fills, NET of fees.

    equity_t = starting_capital
               + Σ(pnl_usd for closed fills with ts<=t)
               − Σ(fee_usd for ALL fills with ts<=t)

    Lookback starts at the session anchor (``MIN(fills.ts_ms)``) rather than a
    rolling 24h window (Jin 2026-05-29). Since the bot runs on a clean-restart
    DB, the whole DB is the current session, so there is no carry-in: ``base``
    is simply ``starting_capital``. The session span is split into N buckets
    (floor 60s).

    Fees hit on BOTH the open and close leg and are a real venue deduction
    (even on DEMO), so each fill contributes ``(pnl if close else 0) − fee``
    at its own timestamp (forensic 2026-05-29 P0 — fees were omitted).

    Returns (bucket_ts list, equity list, total_realised).
    """
    session_start_ms = _session_start_ms(conn, now_s=now_s)
    session_start_s = session_start_ms // 1000
    rows = _safe_query(
        conn,
        """SELECT ts_ms,
                  (CASE WHEN is_close = 1 THEN COALESCE(pnl_usd, 0.0) ELSE 0.0 END)
                  - COALESCE(fee_usd, 0.0) AS delta
           FROM fills
           WHERE ts_ms >= ?
           ORDER BY ts_ms ASC""",
        (session_start_ms,),
    )

    # Clean-restart DB = current session → no pre-session carry-in.
    base = starting_capital

    bucket_ts, _bucket_sec = _session_buckets(session_start_s, now_s)
    equity = [base] * len(bucket_ts)

    if not rows:
        total_realised = base - starting_capital
        return bucket_ts, equity, total_realised

    cum = base
    fill_iter = iter(rows)
    next_fill = next(fill_iter, None)
    for i, t in enumerate(bucket_ts):
        boundary_ms = t * 1000
        while next_fill is not None and int(next_fill[0]) <= boundary_ms:
            cum += float(next_fill[1] or 0.0)
            next_fill = next(fill_iter, None)
        equity[i] = cum

    total_realised = cum - starting_capital
    return bucket_ts, equity, total_realised


def _drawdown_and_sharpe(
    equity: list[float], *, starting_capital: float
) -> tuple[float, float, float]:
    """Compute (drawdown_pct, peak_equity, sharpe_24h) from equity series."""
    if not equity:
        return 0.0, starting_capital, 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    # Sharpe = mean(returns) / std(returns) * sqrt(N)
    if len(equity) < 2:
        return max_dd, peak, 0.0
    rets = []
    for i in range(1, len(equity)):
        prev = equity[i - 1] or 1e-9
        rets.append((equity[i] - prev) / prev)
    if len(rets) < 2:
        return max_dd, peak, 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = var**0.5 or 1e-9
    sharpe = (mean / std) * (len(rets) ** 0.5)
    return max_dd, peak, sharpe


def _daily_realised_pnl(
    conn: sqlite3.Connection, *, now_s: int
) -> tuple[float, int]:
    """Realised PnL (NET of fees) & closed-trade count over the SESSION.

    Lookback starts at the session anchor (``MIN(fills.ts_ms)``) instead of a
    rolling 24h window (Jin 2026-05-29). The ``daily_pnl_usd`` field name is
    kept for schema stability but now carries the session sum.

    Net = Σ(close pnl_usd) − Σ(fee_usd over ALL fills) in-window. Fees on both
    legs are real deductions (forensic 2026-05-29 P0 — were omitted).
    """
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    rows = _safe_query(
        conn,
        """SELECT COALESCE(SUM(CASE WHEN is_close = 1 THEN pnl_usd ELSE 0.0 END), 0.0)
                  - COALESCE(SUM(fee_usd), 0.0),
                  COALESCE(SUM(is_close), 0)
           FROM fills WHERE ts_ms >= ?""",
        (lookback_ms,),
    )
    if not rows:
        return 0.0, 0
    return float(rows[0][0] or 0.0), int(rows[0][1] or 0)


# ---------------------------------------------------------------------------
# Section: positions
# ---------------------------------------------------------------------------


def _last_prices(conn: sqlite3.Connection) -> dict[str, float]:
    rows = _safe_query(
        conn,
        """SELECT instrument_id, close FROM bars
           WHERE (instrument_id, ts) IN
             (SELECT instrument_id, MAX(ts) FROM bars GROUP BY instrument_id)""",
    )
    return {str(r[0]): float(r[1] or 0.0) for r in rows}


def _entry_price_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str, str], float]:
    """Latest open-fill price per (venue, instrument_id, strategy_id)."""
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, fill_price FROM fills
           WHERE is_close = 0
           ORDER BY ts_ms DESC""",
    )
    out: dict[tuple[str, str, str], float] = {}
    for venue, inst, strat, px in rows:
        key = (str(venue), str(inst), str(strat))
        if key not in out:
            out[key] = float(px or 0.0)
    return out


def _cell_mult_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str, str, str], float]:
    """Compute per-cell mult from cell_matrix_p0 score quartile.

    top quartile (score percentile ≥ 75) → 1.5
    bottom quartile (≤ 25) → 0.5
    middle → 1.0
    Cells with n_eff < 20 → 1.0 (warmup, pool default).
    """
    rows = _safe_query(
        conn,
        """SELECT exchange, strategy, ticker, regime, n_eff, score
           FROM cell_matrix_p0
           ORDER BY score DESC""",
    )
    if not rows:
        return {}
    eligible = [(r[0], r[1], r[2], r[3], float(r[5] or 0.0))
                for r in rows if float(r[4] or 0.0) >= 20.0]
    if not eligible:
        return {(str(r[0]), str(r[1]), str(r[2]), str(r[3])): 1.0 for r in rows}
    eligible.sort(key=lambda x: x[4], reverse=True)
    n = len(eligible)
    top_cut = max(1, n // 4)
    bot_cut = max(1, n // 4)
    out: dict[tuple[str, str, str, str], float] = {}
    for i, (ex, strat, tic, reg, _score) in enumerate(eligible):
        if i < top_cut:
            mult = 1.5
        elif i >= n - bot_cut:
            mult = 0.5
        else:
            mult = 1.0
        out[(str(ex), str(strat), str(tic), str(reg))] = mult
    # Default for ineligible
    for r in rows:
        key = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        if key not in out:
            out[key] = 1.0
    return out


def _read_positions(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    last_prices: dict[str, float],
    entry_lookup: dict[tuple[str, str, str], float],
    cell_mult: dict[tuple[str, str, str, str], float],
    regime_lookup: dict[tuple[str, str], str],
) -> list[PositionRow]:
    # Logical-key dedup (B-P0-1, 2026-05-10):
    #   GROUP BY (venue, symbol, strategy_id, side) so legacy duplicate rows
    #   from the lifecycle drift incident collapse to one ``PositionRow``.
    #   ``row_count`` surfaces the duplication so the renderer can tag drift
    #   without hiding the live position. ``held_sec`` uses MIN(opened_ts) so
    #   drift collapses to the oldest entry's age. ``qty`` is summed so the
    #   reported notional matches what scale-in / drift accumulated.
    #
    #   This is the temporary GROUP BY before A-PR5 introduces the
    #   ``v_open_positions`` view; the view will preserve the same logical
    #   key shape so this reader switches with a one-line SQL change.
    # Column order documented inline so the unpacking below stays legible.
    # NOTE: ``SUM(qty)`` is inflated for legacy drift rows (each accidental
    # duplicate added its own qty); collapses back to a single physical row
    # post A-PR2/A-PR4 so the inflation disappears with migration.
    rows = _safe_query(
        conn,
        """SELECT venue,                                  -- r[0]
                  symbol,                                 -- r[1]
                  MAX(underlying_group_id) AS group_id,   -- r[2]
                  strategy_id,                            -- r[3]
                  MAX(active_strategy_id) AS active_sid,  -- r[4]
                  side,                                   -- r[5]
                  SUM(qty) AS qty,                        -- r[6]
                  MIN(opened_ts) AS opened_ts,            -- r[7]
                  COUNT(*) AS row_count                   -- r[8]
           FROM positions
           WHERE status NOT IN ('closed', 'cancelled')
           GROUP BY venue, symbol, strategy_id, side
           ORDER BY MIN(opened_ts) DESC""",
    )
    out: list[PositionRow] = []
    for r in rows:
        venue = str(r[0])
        symbol = str(r[1])
        group_id = str(r[2] or "")
        strat = str(r[4] or r[3])
        side = str(r[5])
        qty = float(r[6] or 0.0)
        opened = int(r[7] or 0)
        row_count = int(r[8] or 1)
        inst = f"{venue}:{symbol}"
        entry = entry_lookup.get((venue, inst, strat))
        if entry is None or entry <= 0.0:
            entry = last_prices.get(inst, 0.0) or 0.0
        last = last_prices.get(inst, entry)
        sign = 1.0 if side.lower() in {"long", "buy"} else -1.0
        delta_pct = ((last - entry) / entry * 100.0) if entry > 0 else 0.0
        upnl = (last - entry) * qty * sign
        size_usd = entry * abs(qty)
        regime = regime_lookup.get((venue, group_id), "chop")
        mult = cell_mult.get((venue, strat, symbol, regime), 1.0)
        held_sec = max(0.0, float(now_s - opened))
        out.append(
            PositionRow(
                venue=venue,
                symbol=symbol,
                strategy_id=strat,
                side=side,
                qty=qty,
                entry_price=entry,
                last_price=last,
                delta_pct=delta_pct * sign,
                upnl_usd=upnl,
                size_usd=size_usd,
                held_sec=held_sec,
                cell_mult=mult,
                row_count=row_count,
            )
        )
    out.sort(key=lambda p: p.upnl_usd, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Section: per-strategy stats
# ---------------------------------------------------------------------------


def _strategy_stats(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    positions: list[PositionRow],
) -> list[StrategyStat]:
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    rows = _safe_query(
        conn,
        """SELECT strategy_id,
                  COUNT(*) AS closed_n,
                  SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
                  COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END), 0.0) AS gross_win,
                  COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN -pnl_usd ELSE 0 END), 0.0) AS gross_loss,
                  COALESCE(SUM(pnl_usd), 0.0) AS pnl_total
           FROM fills
           WHERE is_close = 1 AND ts_ms >= ?
           GROUP BY strategy_id""",
        (lookback_ms,),
    )
    by_strat: dict[str, StrategyStat] = {}
    for r in rows:
        sid = str(r[0])
        closed_n = int(r[1] or 0)
        wins = int(r[2] or 0)
        gross_win = float(r[3] or 0.0)
        gross_loss = float(r[4] or 0.0)
        pnl_total = float(r[5] or 0.0)
        wr = (wins / closed_n * 100.0) if closed_n else 0.0
        avg_r = (pnl_total / closed_n / DEFAULT_R_USD) if closed_n else 0.0
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
