"""Weekend operations board — backend data builder (DEMO/PAPER, display-only).

READ-ONLY window onto the weekend shadow-first programme. Powers the dashboard
``/api/weekend`` endpoint with four sections:

  ① WEEK SETTLEMENT   — real-fee-net P&L of the trades CLOSED in the last 7d,
                        per (strategy × venue), + the best / worst single trade.
  ② SHADOW INCUBATOR  — the ``weekend_shadow_orders`` would-be entries resolved
                        forward against the ingested ``bars`` (+1R target / −1R
                        rail, SAME ATR-R basis the counterfactual sweep uses) +
                        the ``maker_fill_shadow`` real-fee net + a GATED
                        live-promotion gauge.
  ③ MONDAY READINESS  — the next regional cash-session opens (asia/europe/us)
                        as a countdown + per-symbol bar freshness.
  ④ HOUSEKEEPING QUEUE— the funding-promotion gate checklist (client-rendered
                        off the shadow section + the recent research notes).

Nothing here reads or writes sizing / risk / orders. Every query opens against a
read-only connection; a missing table / empty result yields a graceful empty
section (degrade-never-crash) — never an exception that would break the board.
The would-be P&L is resolved OFFLINE from already-stored bars (no live network),
exactly the basis ``polaris/core/economics/shadow_order.py`` documents.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, NamedTuple

from polaris.scripts._session_map import _GROUP_WINDOW

__all__ = [
    "build_weekend",
    "is_weekend_utc",
    "next_session_opens",
    "resolve_would_be_r",
]

# Sat=5, Sun=6 (``date.weekday()`` Mon=0). Mirrors the server-side weekend SSOT
# (``equity_session_gate._WEEKEND_WEEKDAYS`` / ``_session_map`` is_weekend).
_WEEKEND_WEEKDAYS = frozenset({5, 6})

# Last-7d settlement window (epoch ms uses *1000; this is the seconds span).
_WEEK_SECONDS = 7 * 24 * 3600

# The two weekend shadow-first makers (display labels for the GATED gauge note).
_FUNDING_STRAT = "weekend_funding_capitulation_maker"


def is_weekend_utc(now_ts: int | float) -> bool:
    """True when ``now_ts`` (UTC epoch seconds) falls on a Saturday or Sunday.

    Pure. The cash books (Capital FX / index / commodity, Alpaca equity) are shut
    all weekend; OKX crypto trades 24/7, so the weekend board is the OKX-shadow +
    Monday-prep view. An unparseable / negative clock → not weekend (graceful)."""
    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return False
    if ts < 0:
        return False
    return dt.datetime.fromtimestamp(ts, tz=dt.UTC).weekday() in _WEEKEND_WEEKDAYS


def next_session_opens(now_ts: int | float) -> list[dict[str, Any]]:
    """Next cash-session OPEN per region (asia/europe/us) at/after ``now_ts``.

    PURE, no I/O. For each ``_session_map._GROUP_WINDOW`` region returns
    ``{region, open_ts, seconds_until}`` for the soonest WEEKDAY open strictly
    after ``now_ts`` (weekend opens are skipped — the cash book is shut). Sorted
    soonest-first so the readiness countdown lists the next market to wake. An
    unparseable clock → empty list (graceful)."""
    try:
        now = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return []
    if now < 0:
        now = 0
    base = dt.datetime.fromtimestamp(now, tz=dt.UTC)
    base_midnight = dt.datetime(base.year, base.month, base.day, tzinfo=dt.UTC)
    out: list[dict[str, Any]] = []
    for region, (open_min, _close_min) in _GROUP_WINDOW.items():
        # Scan today + the next 7 calendar days for the first weekday open > now.
        for day_offset in range(8):
            day = base_midnight + dt.timedelta(days=day_offset)
            if day.weekday() in _WEEKEND_WEEKDAYS:
                continue  # cash book shut — no open this day
            open_ts = int((day + dt.timedelta(minutes=open_min)).timestamp())
            if open_ts > now:
                out.append({
                    "region": region,
                    "open_ts": open_ts,
                    "seconds_until": open_ts - now,
                })
                break
    out.sort(key=lambda r: r["seconds_until"])
    return out


class _WouldBe(NamedTuple):
    """Resolved would-be outcome of one shadow order against forward bars.

    ``r`` = realised R at exit: +``profit_target_r`` when the +1R target was hit
    first, −1.0 when the −1R rail was hit first, else the open mark-to-bar R at
    the last available bar (unresolved → marked at the latest close). ``status``
    is 'target' / 'stop' / 'open' so the board can tally hit-rate honestly."""

    r: float
    status: str


def resolve_would_be_r(
    *,
    side: str,
    entry_mark: float,
    atr_pct: float,
    profit_target_r: float | None,
    forward_closes: list[float],
    forward_highs: list[float] | None = None,
    forward_lows: list[float] | None = None,
) -> _WouldBe:
    """Resolve one shadow order's would-be R from the bars AFTER its entry.

    ATR-R basis (the SAME the counterfactual sweep + ``shadow_order`` use): one R
    = ``entry_mark * atr_pct`` in price. For a LONG the +target is
    ``entry_mark + R * profit_target_r`` and the −rail is ``entry_mark − R``; for
    a SHORT the signs flip. Walking the forward bars in order, the FIRST rail the
    bar's high/low (close-only fallback) touches decides the outcome — target →
    +``profit_target_r``, stop → −1.0. If neither is hit by the last bar the trade
    is 'open', marked at the last close (mark-to-bar R). Degenerate inputs (no
    bars, non-positive entry/atr) → flat open at 0.0 R. Pure, no I/O."""
    tgt_r = 1.0 if profit_target_r is None else float(profit_target_r)
    if entry_mark <= 0.0 or atr_pct <= 0.0 or not forward_closes:
        return _WouldBe(0.0, "open")
    is_long = str(side).strip().lower() in ("buy", "long")
    r_price = entry_mark * atr_pct
    if is_long:
        target_px = entry_mark + r_price * tgt_r
        stop_px = entry_mark - r_price
    else:
        target_px = entry_mark - r_price * tgt_r
        stop_px = entry_mark + r_price
    highs = forward_highs if forward_highs is not None else forward_closes
    lows = forward_lows if forward_lows is not None else forward_closes
    n = len(forward_closes)
    for i in range(n):
        hi = highs[i] if i < len(highs) else forward_closes[i]
        lo = lows[i] if i < len(lows) else forward_closes[i]
        if is_long:
            if hi >= target_px:
                return _WouldBe(tgt_r, "target")
            if lo <= stop_px:
                return _WouldBe(-1.0, "stop")
        else:
            if lo <= target_px:
                return _WouldBe(tgt_r, "target")
            if hi >= stop_px:
                return _WouldBe(-1.0, "stop")
    # Unresolved by the last bar → open mark-to-bar R at the latest close.
    last = forward_closes[-1]
    move = (last - entry_mark) if is_long else (entry_mark - last)
    return _WouldBe(move / r_price, "open")


# ── SQLite section builders (read-only) ─────────────────────────────────────


def _settlement(conn: sqlite3.Connection, *, now_ts: int) -> dict[str, Any]:
    """Section ① — last-7d CLOSED-trade real-fee-net per (strategy × venue).

    ``net_usd`` = Σ(close pnl_usd) − Σ(close fee_usd) over close fills whose
    ``ts_ms`` lands in the last 7 days. Also returns the single best / worst
    close trade (by net). RECONCILED rows never write close fills, so they are
    naturally excluded (a reconcile is not a trade). Read-only; graceful empty."""
    since_ms = (now_ts - _WEEK_SECONDS) * 1000
    rows: list[Any] = []
    try:
        rows = conn.execute(
            """SELECT strategy_id, venue,
                      COUNT(*) AS n,
                      COALESCE(SUM(pnl_usd), 0.0) AS gross,
                      COALESCE(SUM(fee_usd), 0.0) AS fee,
                      SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins
               FROM fills
               WHERE is_close = 1 AND ts_ms >= ?
               GROUP BY strategy_id, venue
               ORDER BY (SUM(pnl_usd) - SUM(fee_usd)) DESC""",
            (since_ms,),
        ).fetchall()
    except sqlite3.Error:
        return {"rows": [], "best": None, "worst": None, "total_net_usd": 0.0}
    table: list[dict[str, Any]] = []
    total_net = 0.0
    for sid, venue, n, gross, fee, wins in rows:
        net = float(gross or 0.0) - float(fee or 0.0)
        total_net += net
        n_i = int(n or 0)
        table.append({
            "strategy_id": str(sid or ""),
            "venue": str(venue or ""),
            "n": n_i,
            "win_pct": (float(wins or 0) / n_i * 100.0) if n_i else 0.0,
            "net_usd": net,
        })
    best, worst = _best_worst_trade(conn, since_ms=since_ms)
    return {
        "rows": table, "best": best, "worst": worst,
        "total_net_usd": total_net,
    }


def _best_worst_trade(
    conn: sqlite3.Connection, *, since_ms: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Single best / worst close trade (by net = pnl − fee) in the window."""
    try:
        rows = conn.execute(
            """SELECT strategy_id, venue, instrument_id,
                      (pnl_usd - fee_usd) AS net, ts_ms
               FROM fills
               WHERE is_close = 1 AND ts_ms >= ?
               ORDER BY net DESC""",
            (since_ms,),
        ).fetchall()
    except sqlite3.Error:
        return None, None
    if not rows:
        return None, None

    def _mk(r: Any) -> dict[str, Any]:
        sid, venue, inst, net, ts = r
        sym = str(inst or "").split(":")[-1]
        return {
            "strategy_id": str(sid or ""), "venue": str(venue or ""),
            "symbol": sym, "net_usd": float(net or 0.0), "ts_ms": int(ts or 0),
        }

    return _mk(rows[0]), _mk(rows[-1])


def _shadow_incubator(conn: sqlite3.Connection) -> dict[str, Any]:
    """Section ② — weekend_shadow_orders resolved forward + maker-fill status.

    Each shadow order is resolved against the OKX bars stored AFTER its
    ``created_ts`` (1m native) into a would-be R via ``resolve_would_be_r``; the
    per-strategy rollup carries n / target-hit / stop-hit / open + the median &
    mean would-be R. The ``maker_fill_shadow`` real-fee row count is reported
    honestly (0 = persist not yet wired, FIX-EXEC pending). Read-only.

    ``conn`` — the marketdata-domain conn (storage-split, 2026-07-14):
    ``weekend_shadow_orders``, ``bars``, and ``maker_fill_shadow`` are all
    marketdata-domain tables (every ``*_shadow`` table except
    ``gate_shadow_events``)."""
    try:
        orders = conn.execute(
            """SELECT strategy_id, venue, symbol, side, entry_mark,
                      atr_pct, profit_target_r, created_ts
               FROM weekend_shadow_orders
               ORDER BY created_ts ASC""",
        ).fetchall()
    except sqlite3.Error:
        orders = []
    by_strat: dict[str, dict[str, Any]] = {}
    for sid, venue, symbol, side, mark, atr, tgt, created in orders:
        sid_s = str(sid or "")
        wb = _resolve_one(
            conn, venue=str(venue or ""), symbol=str(symbol or ""),
            side=str(side or ""), entry_mark=float(mark or 0.0),
            atr_pct=float(atr or 0.0),
            profit_target_r=None if tgt is None else float(tgt),
            created_ts=int(created or 0),
        )
        slot = by_strat.setdefault(sid_s, {
            "strategy_id": sid_s, "n": 0, "target": 0, "stop": 0, "open": 0,
            "_rs": [],
        })
        slot["n"] += 1
        slot[wb.status] += 1
        slot["_rs"].append(wb.r)
    strat_rows: list[dict[str, Any]] = []
    for slot in by_strat.values():
        rs = sorted(slot.pop("_rs"))
        slot["mean_r"] = sum(rs) / len(rs) if rs else 0.0
        slot["median_r"] = _median(rs)
        slot["win_pct"] = (
            slot["target"] / slot["n"] * 100.0 if slot["n"] else 0.0
        )
        strat_rows.append(slot)
    strat_rows.sort(key=lambda s: s["mean_r"], reverse=True)
    maker_n = _table_count(conn, "maker_fill_shadow")
    return {
        "strategies": strat_rows,
        "order_n": len(orders),
        "maker_fill_shadow_n": maker_n,
        "maker_persist_wired": maker_n > 0,
    }


def _resolve_one(
    conn: sqlite3.Connection, *, venue: str, symbol: str, side: str,
    entry_mark: float, atr_pct: float, profit_target_r: float | None,
    created_ts: int,
) -> _WouldBe:
    """Read the forward bars for one shadow order + resolve its would-be R."""
    try:
        rows = conn.execute(
            """SELECT high, low, close FROM bars
               WHERE venue = ? AND symbol = ? AND bar_interval = '1m'
                 AND ts >= ?
               ORDER BY ts ASC LIMIT 2880""",
            (venue, symbol, created_ts),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    highs = [float(r[0]) for r in rows]
    lows = [float(r[1]) for r in rows]
    closes = [float(r[2]) for r in rows]
    return resolve_would_be_r(
        side=side, entry_mark=entry_mark, atr_pct=atr_pct,
        profit_target_r=profit_target_r, forward_closes=closes,
        forward_highs=highs, forward_lows=lows,
    )


def _readiness(conn: sqlite3.Connection, *, now_ts: int) -> dict[str, Any]:
    """Section ③ — next session opens + per-symbol bar freshness.

    The countdown is pure (``next_session_opens``). Bar freshness is the age of
    the newest stored bar per (venue, symbol) for the symbols carrying weekend
    shadow orders (the names that matter for the Monday hand-off). Read-only.

    ``conn`` — the marketdata-domain conn (storage-split, 2026-07-14): ``bars``
    and ``weekend_shadow_orders`` both live there."""
    opens = next_session_opens(now_ts)
    try:
        rows = conn.execute(
            """SELECT venue, symbol, MAX(ts) AS last_ts
               FROM bars
               WHERE symbol IN (
                   SELECT DISTINCT symbol FROM weekend_shadow_orders
               )
               GROUP BY venue, symbol
               ORDER BY last_ts DESC""",
        ).fetchall()
    except sqlite3.Error:
        rows = []
    freshness = [
        {
            "venue": str(v or ""), "symbol": str(s or ""),
            "last_ts": int(ts or 0), "age_sec": max(0, now_ts - int(ts or 0)),
        }
        for v, s, ts in rows
    ]
    return {"session_opens": opens, "bar_freshness": freshness}


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    """Row count of ``table``, or 0 when the table is absent (graceful)."""
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def _median(sorted_vals: list[float]) -> float:
    """Median of an ALREADY-SORTED list; 0.0 when empty. Pure."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def build_weekend(
    conn: sqlite3.Connection, *, now_ts: int,
    md_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Assemble the full Weekend-tab payload (read-only, degrade-never-crash).

    Each section is built independently; a failure in one yields its empty shape
    rather than blanking the whole board. ``is_weekend`` lets the client badge /
    auto-focus the tab only when the cash books are actually shut.

    ``conn`` — the trading conn (``fills`` for section ①). ``md_conn`` — the
    marketdata-domain conn (storage-split, 2026-07-14) for sections ②/③
    (``bars`` / ``weekend_shadow_orders`` / ``maker_fill_shadow``); falls back
    to ``conn`` when omitted (byte-identical for single-conn callers/tests)."""
    _md = md_conn if md_conn is not None else conn
    return {
        "ts_now": now_ts,
        "is_weekend": is_weekend_utc(now_ts),
        "settlement": _settlement(conn, now_ts=now_ts),
        "shadow": _shadow_incubator(_md),
        "readiness": _readiness(_md, now_ts=now_ts),
        # The funding-promotion gate is a fixed 3-item checklist (the GATED
        # promotion criteria from the weekend research notes); the live state is
        # derived from the shadow section (maker persist wired? funding rounds?).
        "funding_strategy": _FUNDING_STRAT,
    }
