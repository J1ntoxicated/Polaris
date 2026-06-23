"""Polaris dashboard v1 — open-positions queries.

Current-price lookups (WS tick overlay on bar close), entry-price + cell-mult
lookups, and the logical-key-deduped open-position reader. Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC (move-only; no logic
change). Display-only — never a trading path.
"""

from __future__ import annotations

import sqlite3

from polaris.scripts.dashboard.snapshot_models import PositionRow
from polaris.scripts.dashboard.snapshot_q_common import _now_s, _safe_query

# P4 #1 — dashboard current-price freshness window (wall-clock seconds against
# quote_ticks.ts). WS ticks stamp ``int(time.time())`` (seconds, like bars.ts).
# Generous (60s) because the dashboard is display-only: a recent WS mid is always
# preferable to the last 1m bar close, and there is no exit/sizing risk here. A
# tick older than this falls back to the bar close (graceful, no flap risk).
_DASHBOARD_TICK_FRESH_SEC = 60


def _last_prices(conn: sqlite3.Connection) -> dict[str, float]:
    # P4 #1: prefer the latest WS quote tick mid (real-time px-flash) when it is
    # fresh; otherwise fall back to the last bar close. Bars stay the calc source
    # everywhere else — this only changes what "current price" the dashboard
    # shows (behavior 0; positions.last_price reads this same dict downstream).
    #
    # bars JOIN: the previous `WHERE (instrument_id, ts) IN (…)` row-value form
    # forced a full bars scan (SQLite can't index a row-value IN against a
    # subquery → ~45s on a multi-M-row table). Rewritten as a JOIN: the GROUP BY
    # MAX(ts) subquery resolves per-group via idx_bars_instrument_ts
    # (instrument_id, ts), and the outer JOIN seeks each (instrument_id, ts) row
    # by the same index → milliseconds.
    # Exclude FUTURE-dated bars (ts > now): Capital REST bars can be +10h ahead
    # (the AEST-naive snapshotTime parse). A future bar must never be the
    # MAX(ts) "current price" — it makes the dashboard look stale and pollutes
    # the price source. The fresh WS quote overlay below is the unambiguous
    # current price when present (the venue ts fix removes the +10h at source;
    # this guard is the dashboard-side belt-and-suspenders).
    now_s = _now_s()
    rows = _safe_query(
        conn,
        """SELECT b.instrument_id, b.close FROM bars b
           JOIN (SELECT instrument_id, MAX(ts) AS mts FROM bars
                 WHERE ts <= ? GROUP BY instrument_id) m
             ON b.instrument_id = m.instrument_id AND b.ts = m.mts""",
        (now_s,),
    )
    prices = {str(r[0]): float(r[1] or 0.0) for r in rows}

    # Overlay fresh WS quote ticks (mid) on top of the bar-close baseline.
    fresh_floor = _now_s() - _DASHBOARD_TICK_FRESH_SEC
    tick_rows = _safe_query(
        conn,
        """SELECT q.instrument_id, q.mid FROM quote_ticks q
           JOIN (SELECT instrument_id, MAX(ts) AS mts FROM quote_ticks GROUP BY instrument_id) m
             ON q.instrument_id = m.instrument_id AND q.ts = m.mts
           WHERE q.ts >= ? AND q.mid > 0.0""",
        (fresh_floor,),
    )
    for inst, mid in tick_rows:
        prices[str(inst)] = float(mid or 0.0)
    return prices


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
    # E2: surface the per-position exit-FSM state + protective stop + MFE/MAE-R.
    # These are aggregated across logical-key duplicates: ``exit_state`` /
    # ``stop_price`` take the most-recent (MAX(opened_ts)) row's value via a
    # correlated read, MFE/MAE take the extremes. Display-only columns.
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
                  COUNT(*) AS row_count,                  -- r[8]
                  MAX(exit_state) AS exit_state,          -- r[9]
                  MAX(COALESCE(stop_price, 0.0)) AS stop, -- r[10]
                  MAX(COALESCE(mfe_r, 0.0)) AS mfe_r,     -- r[11]
                  MIN(COALESCE(mae_r, 0.0)) AS mae_r      -- r[12]
           FROM positions
           WHERE status NOT IN ('closed', 'cancelled', 'reconciled')
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
        exit_state = str(r[9] or "open")
        stop_price = float(r[10] or 0.0)
        mfe_r = float(r[11] or 0.0)
        mae_r = float(r[12] or 0.0)
        inst = f"{venue}:{symbol}"
        entry = entry_lookup.get((venue, inst, strat))
        if entry is None or entry <= 0.0:
            entry = last_prices.get(inst, 0.0) or 0.0
        last = last_prices.get(inst, entry)
        sign = 1.0 if side.lower() in {"long", "buy"} else -1.0
        delta_pct = ((last - entry) / entry * 100.0) if entry > 0 else 0.0
        upnl = (last - entry) * qty * sign
        size_usd = entry * abs(qty)
        # uPnL as a % of deployed notional (display-only column).
        upnl_pct = (upnl / size_usd * 100.0) if size_usd > 0 else 0.0
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
                regime=regime,
                exit_state=exit_state,
                stop_price=stop_price,
                # Hardening #6: positions.mfe_r/mae_r are the per-trade-ATR
                # EXCURSION ruler → surfaced as the *_atr_r payload fields.
                mfe_atr_r=mfe_r,
                mae_atr_r=mae_r,
                upnl_pct=upnl_pct,
            )
        )
    out.sort(key=lambda p: p.upnl_usd, reverse=True)
    return out
