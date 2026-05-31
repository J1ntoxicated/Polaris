"""Polaris dashboard v1 — snapshot section queries (DB → typed row lists).

Per-section best-effort read helpers consumed by ``snapshot.collect_snapshot``.
All queries best-effort: missing tables / empty data return zero-defaults so the
dashboard never crashes a paper loop. Split out of ``snapshot.py`` to keep each
module ≤500 LOC; ``snapshot`` re-exports ``_read_positions`` for tests that
reference it via the original path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections.abc import Mapping
from typing import Any, Final, NamedTuple

from polaris.core.economics.fees import real_fee_usd
from polaris.core.sizing.constants import (
    demo_starting_equity_capital,
    demo_starting_equity_okx,
)
from polaris.core.streams.config import STREAMS
from polaris.scripts.dashboard.snapshot_models import (
    ClosedTrade,
    PositionRow,
    StrategyStat,
    StreamSummary,
)

logger = logging.getLogger(__name__)

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

# Per-stream AI-cost price table (USD per 1K tokens). DISPLAY-ONLY: feeds the
# read-only cost-monitoring breakdown, never sizing/gating. Keyed on the
# ``gate_events.model_used`` labels the orchestrator actually emits — ``gpt`` =
# GPT-mini (P0), ``gpt_p1``/``haiku`` = the P1 model, ``python`` /
# ``python_fast_path`` = deterministic gate (no LLM, $0), ``cached`` = served
# from cache ($0). This is the AUDIT's price table (Jin /debate-tunable), not a
# venue billing source of truth. Unknown labels fall back to the mini price
# (``_model_price``) so a new label is never silently free.
MODEL_PRICE_PER_1K: Final[Mapping[str, float]] = {
    "gpt": 0.000150,            # GPT-mini (P0)
    "gpt-5-mini": 0.000150,
    "gpt_p1": 0.000150,         # P1 tier downgraded to gpt-5-mini (Jin 2026-05-31)
    "gpt-5.5": 0.005,           # legacy price kept for any pre-downgrade rows
    "haiku": 0.005,             # legacy P1 label — priced at the P1 tier
    "python": 0.0,              # deterministic gate — no LLM call
    "python_fast_path": 0.0,
    "cached": 0.0,              # cache hit — no incremental cost
}
SLIPPAGE_BPS_DIVISOR: Final[float] = 10_000.0  # bps → fraction of notional


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


def _model_price(model: str | None) -> float:
    """USD per 1K tokens for a ``gate_events.model_used`` label (display only).

    Unknown / unlabelled models fall back to the mini price so a new model id
    is never silently treated as free. ``python`` / ``cached`` map to 0.0.
    """
    if not model:
        return MODEL_PRICE_PER_1K["gpt"]
    return MODEL_PRICE_PER_1K.get(str(model), MODEL_PRICE_PER_1K["gpt"])


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


class DualEquityCurve(NamedTuple):
    """Two realised-PnL equity curves from ONE fills walk (Component A).

    Both curves use the IDENTICAL gross close ``pnl_usd``; they differ only in
    the fee schedule deducted on every leg:

    - ``equity_demo`` : stored ``fills.fee_usd`` (the 0.7% OKX demo actually
      charged) — bit-for-bit the existing ``_build_equity_curve`` curve, which
      matches the real demo-account drain.
    - ``equity_real`` : fees RECOMPUTED at the REAL schedule via
      ``economics.fees.real_fee_usd(venue, size_usd)`` per fill notional — the
      go-live confidence signal (Jin 2026-05-31).

    ``*_fee_total`` are the session fee sums under each schedule;
    ``total_realised_*`` are the net realised deltas vs ``starting_capital``.
    """

    bucket_ts: list[int]
    equity_demo: list[float]
    equity_real: list[float]
    total_realised_demo: float
    total_realised_real: float
    demo_fee_total: float
    real_fee_total: float


def _build_dual_equity_curve(
    conn: sqlite3.Connection, *, now_s: int, starting_capital: float
) -> DualEquityCurve:
    """Build demo-actual + real-fee-net equity curves from one fills walk.

    Mirrors ``_build_equity_curve`` exactly for the demo leg (same session
    anchor, bucketing, base, and ``(pnl if close else 0) − fee`` per-fill delta)
    so the demo curve is a non-regressing superset. The real leg substitutes the
    stored ``fee_usd`` with ``real_fee_usd(venue, size_usd)`` on EVERY fill leg
    (open + close both incur the venue fee). Read-only; trading behavior
    unchanged.
    """
    session_start_ms = _session_start_ms(conn, now_s=now_s)
    session_start_s = session_start_ms // 1000
    rows = _safe_query(
        conn,
        """SELECT ts_ms,
                  venue,
                  size_usd,
                  (CASE WHEN is_close = 1 THEN COALESCE(pnl_usd, 0.0) ELSE 0.0 END)
                    AS gross,
                  COALESCE(fee_usd, 0.0) AS demo_fee
           FROM fills
           WHERE ts_ms >= ?
           ORDER BY ts_ms ASC""",
        (session_start_ms,),
    )

    base = starting_capital
    bucket_ts, _bucket_sec = _session_buckets(session_start_s, now_s)
    equity_demo = [base] * len(bucket_ts)
    equity_real = [base] * len(bucket_ts)

    if not rows:
        return DualEquityCurve(
            bucket_ts=bucket_ts,
            equity_demo=equity_demo,
            equity_real=equity_real,
            total_realised_demo=0.0,
            total_realised_real=0.0,
            demo_fee_total=0.0,
            real_fee_total=0.0,
        )

    cum_demo = base
    cum_real = base
    demo_fee_total = 0.0
    real_fee_total = 0.0
    fill_iter = iter(rows)
    next_fill = next(fill_iter, None)
    for i, t in enumerate(bucket_ts):
        boundary_ms = t * 1000
        while next_fill is not None and int(next_fill[0]) <= boundary_ms:
            venue = str(next_fill[1] or "")
            size_usd = float(next_fill[2] or 0.0)
            gross = float(next_fill[3] or 0.0)
            demo_fee = float(next_fill[4] or 0.0)
            real_fee = real_fee_usd(venue, size_usd)
            cum_demo += gross - demo_fee
            cum_real += gross - real_fee
            demo_fee_total += demo_fee
            real_fee_total += real_fee
            next_fill = next(fill_iter, None)
        equity_demo[i] = cum_demo
        equity_real[i] = cum_real

    return DualEquityCurve(
        bucket_ts=bucket_ts,
        equity_demo=equity_demo,
        equity_real=equity_real,
        total_realised_demo=cum_demo - starting_capital,
        total_realised_real=cum_real - starting_capital,
        demo_fee_total=demo_fee_total,
        real_fee_total=real_fee_total,
    )


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
    # Last close per instrument. The previous `WHERE (instrument_id, ts) IN (…)`
    # row-value form forced a full bars scan (SQLite can't index a row-value IN
    # against a subquery → ~45s on a multi-M-row table). Rewritten as a JOIN: the
    # GROUP BY MAX(ts) subquery resolves per-group via idx_bars_instrument_ts
    # (instrument_id, ts), and the outer JOIN seeks each (instrument_id, ts) row
    # by the same index → milliseconds.
    rows = _safe_query(
        conn,
        """SELECT b.instrument_id, b.close FROM bars b
           JOIN (SELECT instrument_id, MAX(ts) AS mts FROM bars GROUP BY instrument_id) m
             ON b.instrument_id = m.instrument_id AND b.ts = m.mts""",
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
                mfe_r=mfe_r,
                mae_r=mae_r,
                upnl_pct=upnl_pct,
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


# ---------------------------------------------------------------------------
# Section: per-stream (venue lane) summary
# ---------------------------------------------------------------------------

# Display label + web color per stream, keyed on the SSOT ``stream_id``
# (``polaris.core.streams.config.STREAMS``). The venue→stream_id mapping itself
# is NOT duplicated here — it comes from ``VENUE_TO_STREAM`` (the SSOT). This map
# only carries the two purely-presentational attributes the streams config does
# not own (a human label + a CSS hex color the web board renders). Hex colors so
# the JSON snapshot is directly consumable by the frontend (board.js).
_STREAM_DISPLAY: Final[Mapping[str, tuple[str, str]]] = {
    "A_okx_crypto": ("OKX SPOT", "#5fafff"),       # blue lane
    "B_capital_cfd": ("CAPITAL CFD", "#ffd75f"),   # gold lane
    "C_alpaca_equity": ("ALPACA EQUITY", "#87d75f"),  # green lane
}
_STREAM_DISPLAY_DEFAULT: Final[tuple[str, str]] = ("?", "#9e9e9e")


RECENT_CLOSED_PER_STREAM: Final[int] = 6  # cap of recent-closed rows per lane


def _recent_closed_by_venue(
    conn: sqlite3.Connection, *, per_venue: int = RECENT_CLOSED_PER_STREAM
) -> dict[str, list[ClosedTrade]]:
    """Most-recent closed trades per venue (newest first, capped per venue).

    Lightweight read for the per-stream OPEN/CLOSED split: each close fill is a
    closed trade, grouped by venue. Entry price/held are reconstructed from the
    close fill's own pnl (display-only; the global ``recent_trades`` panel keeps
    the exact entry-pairing logic). Empty venues yield no key → an empty list at
    the call site (graceful zero). Pure read-only.
    """
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty
           FROM fills
           WHERE is_close = 1
           ORDER BY ts_ms DESC""",
    )
    out: dict[str, list[ClosedTrade]] = {}
    for r in rows:
        venue = str(r[0] or "").lower()
        bucket = out.setdefault(venue, [])
        if len(bucket) >= per_venue:
            continue
        inst = str(r[1] or "")
        side = str(r[3] or "")
        fill_price = float(r[4] or 0.0)
        pnl = float(r[5] or 0.0)
        ts_ms = int(r[6] or 0)
        qty = float(r[7] or 0.0)
        if qty > 0 and fill_price > 0:
            sign = 1.0 if side.lower() == "sell" else -1.0
            entry_px = fill_price - (pnl / qty) * sign
        else:
            entry_px = fill_price
        reason = "TP" if pnl > 0 else ("SL" if pnl < 0 else "FLAT")
        bucket.append(
            ClosedTrade(
                ts_close=ts_ms // 1000,
                venue=venue,
                symbol=_symbol_from_inst(inst),
                strategy_id=str(r[2] or ""),
                side_close=side,
                entry_price=entry_px,
                exit_price=fill_price,
                pnl_usd=pnl,
                r_units=pnl / DEFAULT_R_USD,
                held_sec=0.0,
                exit_reason=reason,
            )
        )
    return out


# Alpaca paper account equity probe (display-only). Unlike OKX/Capital, Alpaca
# has NO static starting-equity constant — the paper account is funded directly
# at the venue, so the only source of truth for its baseline is the live
# ``GET /v2/account`` call. We probe it once per TTL window and cache the result;
# the dashboard then shows the real account value instead of a $0 placeholder.
# Read-only (account query, never an order). Graceful on every failure path:
# missing keys / network error / non-200 → ``None`` → caller falls back to 0.0.
ALPACA_EQUITY_PROBE_TTL_SEC: Final[float] = 60.0


class _AlpacaEquity(NamedTuple):
    """Probed Alpaca paper-account values (USD). ``starting`` is the session
    baseline (``last_equity`` — equity at the prior market close) so the
    ``equity = starting + net_pnl + upnl`` identity reconciles with DB-tracked
    session activity exactly like the OKX/Capital lanes."""

    equity: float
    starting: float


# (monotonic_deadline, _AlpacaEquity | None) — None caches a failed/absent probe
# for the TTL window too, so a creds-less dashboard does not retry every refresh.
_alpaca_equity_cache: tuple[float, _AlpacaEquity | None] | None = None


async def _fetch_alpaca_account(api_key: str, secret: str) -> dict[str, Any]:
    """Probe ``GET /v2/account`` via the paper adapter (read-only, no order)."""
    # Lazy import so the dashboard module has no hard dependency on the venue
    # adapter (and tests that never probe never import httpx via this path).
    from polaris.venues.alpaca.adapter import AlpacaAdapter

    async with AlpacaAdapter(api_key=api_key, secret=secret) as adapter:
        return await adapter.fetch_account()


def _alpaca_account_equity() -> _AlpacaEquity | None:
    """Live Alpaca paper-account equity (USD), TTL-cached. ``None`` if unavailable.

    Reads credentials from ``os.environ`` ONLY (it does not load ``.env`` itself
    — the dashboard server loads it at startup, and the test suite never sets the
    keys, so tests stay fully offline → this returns ``None`` → the alpaca lane
    keeps its 0.0 baseline, unchanged behavior). Secrets are never logged. Any
    error (no keys / transport / non-200 / parse) is swallowed and cached as
    ``None`` for the TTL window. Display-only; never feeds sizing/gating/orders.
    """
    global _alpaca_equity_cache
    now = time.monotonic()
    if _alpaca_equity_cache is not None and now < _alpaca_equity_cache[0]:
        return _alpaca_equity_cache[1]

    result: _AlpacaEquity | None = None
    # Resolve creds from the environment only (no .env auto-load here). Mirrors
    # the adapter's PAPER-first / ARCHIVE-fallback order without importing it
    # when the keys are absent.
    key = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_API_KEY", ""
    )
    secret = os.environ.get("ALPACA_PAPER_SECRET") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_SECRET", ""
    )
    if key and secret:
        try:
            account = asyncio.run(_fetch_alpaca_account(key, secret))
            equity = float(account.get("equity") or account.get("portfolio_value") or 0.0)
            # ``last_equity`` = equity at the prior market close → session-start
            # baseline. Fall back to current equity when absent (first session).
            starting = float(account.get("last_equity") or equity)
            if equity > 0.0:
                result = _AlpacaEquity(equity=equity, starting=starting)
        except Exception as exc:  # noqa: BLE001 — display-only, never crash a refresh
            # Log the class only — never the keys or full response.
            logger.warning("[dashboard] alpaca equity probe failed: %s", type(exc).__name__)
            result = None

    _alpaca_equity_cache = (now + ALPACA_EQUITY_PROBE_TTL_SEC, result)
    return result


def _per_stream_summary(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    positions: list[PositionRow] | None = None,
) -> list[StreamSummary]:
    """Per-stream (venue lane) rollup — one row per registered stream.

    Emits a row for **every** stream in the SSOT (``STREAMS``) even when a venue
    has zero activity, so the dashboard always renders all lanes. Reconciliation
    invariant (the dashboard never lies):

    - ``Σ net_pnl_usd`` == global ``daily_pnl_usd`` (``_daily_realised_pnl``):
      both use the identical session lookback + ``Σ(close pnl) − Σ(all fees)``
      formula, grouped by venue here.
    - ``Σ daily_trades`` == global closed-trade count.
    - ``Σ open_positions_n`` / ``upnl_usd`` / ``exposed_usd`` decompose the same
      ``positions`` list the global snapshot aggregates, so they sum exactly.

    ``positions`` is the already-built list from ``_read_positions`` (passed by
    ``collect_snapshot`` for exact reconciliation); when omitted it is read here.
    Pure read-only; no trading behavior touched.
    """
    if positions is None:
        last_prices = _last_prices(conn)
        entry_lookup = _entry_price_lookup(conn)
        cell_mult = _cell_mult_lookup(conn)
        # regime_lookup is only used to refine cell_mult; an empty map is fine
        # for the rollup (mult does not affect pnl/upnl/exposed aggregation).
        positions = _read_positions(
            conn,
            now_s=now_s,
            last_prices=last_prices,
            entry_lookup=entry_lookup,
            cell_mult=cell_mult,
            regime_lookup={},
        )

    # --- fills side: net realised pnl + closed-trade count, GROUP BY venue.
    # Same formula + session lookback as ``_daily_realised_pnl`` so the per-venue
    # sum reconciles to the global total exactly.
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    # ``net_pnl`` already nets fees (Σ close pnl − Σ all fees) so it reconciles
    # to the global ``_daily_realised_pnl``. ``fee_total`` + ``slip_total`` are
    # display-only cost breakdowns surfaced alongside it. slippage_usd is derived
    # from ``slippage_bps`` (no explicit slippage_usd column in fills):
    # slippage_bps / 10000 × size_usd, summed per venue.
    fill_rows = _safe_query(
        conn,
        """SELECT venue,
                  COALESCE(SUM(CASE WHEN is_close = 1 THEN pnl_usd ELSE 0.0 END), 0.0)
                  - COALESCE(SUM(fee_usd), 0.0) AS net_pnl,
                  COALESCE(SUM(is_close), 0) AS closed_n,
                  COALESCE(SUM(fee_usd), 0.0) AS fee_total,
                  COALESCE(SUM(slippage_bps / ? * size_usd), 0.0) AS slip_total
           FROM fills WHERE ts_ms >= ?
           GROUP BY venue""",
        (SLIPPAGE_BPS_DIVISOR, lookback_ms),
    )
    pnl_by_venue: dict[str, float] = {}
    trades_by_venue: dict[str, int] = {}
    fee_by_venue: dict[str, float] = {}
    slip_by_venue: dict[str, float] = {}
    for r in fill_rows:
        venue = str(r[0] or "").lower()
        pnl_by_venue[venue] = float(r[1] or 0.0)
        trades_by_venue[venue] = int(r[2] or 0)
        fee_by_venue[venue] = float(r[3] or 0.0)
        slip_by_venue[venue] = float(r[4] or 0.0)

    # --- AI cost side: gate_events has NO venue column, so attribute each event
    # to a venue via the position_id → positions.venue join (the SSOT venue
    # source). Pre-position gate_events (NULL position_id — G1..G5 before a
    # position exists) are UNATTRIBUTABLE and intentionally excluded; their
    # tokens are not assigned to any stream (documented gap, display-only).
    # Cost = Σ (input+output tokens)/1000 × MODEL_PRICE_PER_1K[model_used].
    ai_cost_by_venue: dict[str, float] = {}
    ai_rows = _safe_query(
        conn,
        """SELECT p.venue, g.model_used,
                  COALESCE(SUM(g.input_tokens + g.output_tokens), 0)
           FROM gate_events g
           JOIN positions p ON g.position_id = p.position_id
           WHERE g.created_ts >= ?
             AND g.position_id IS NOT NULL AND g.position_id != ''
           GROUP BY p.venue, g.model_used""",
        (lookback_ms // 1000,),
    )
    for r in ai_rows:
        venue = str(r[0] or "").lower()
        tokens = int(r[2] or 0)
        cost = tokens / 1000.0 * _model_price(r[1])
        ai_cost_by_venue[venue] = ai_cost_by_venue.get(venue, 0.0) + cost

    # --- positions side: open_n / exposed / upnl, grouped by venue from the
    # already-aggregated PositionRow list (exact decomposition of the globals).
    open_by_venue: dict[str, int] = {}
    exposed_by_venue: dict[str, float] = {}
    upnl_by_venue: dict[str, float] = {}
    for p in positions:
        v = p.venue.lower()
        open_by_venue[v] = open_by_venue.get(v, 0) + 1
        exposed_by_venue[v] = exposed_by_venue.get(v, 0.0) + p.size_usd
        upnl_by_venue[v] = upnl_by_venue.get(v, 0.0) + p.upnl_usd

    # Per-venue starting capital. OKX/Capital use the static demo-equity SSOT;
    # Alpaca has NO static constant (the paper account is funded at the venue),
    # so we probe the live ``/v2/account`` baseline. ``equity = starting +
    # net_pnl + upnl`` then reconciles with DB session activity for every lane.
    # Probe unavailable (no keys / error) → 0.0, exactly the prior behavior.
    alpaca_equity = _alpaca_account_equity()
    starting_by_venue: dict[str, float] = {
        "okx": demo_starting_equity_okx(),
        "capital": demo_starting_equity_capital(),
        "alpaca": alpaca_equity.starting if alpaca_equity is not None else 0.0,
    }

    # OPEN vs CLOSED split — per-venue recent-closed trades (newest first).
    recent_closed_by_venue = _recent_closed_by_venue(conn)

    out: list[StreamSummary] = []
    # Stable lane order = SSOT registration order (A, B, C).
    for stream_id, cfg in STREAMS.items():
        venue = cfg.venue
        label, color = _STREAM_DISPLAY.get(stream_id, _STREAM_DISPLAY_DEFAULT)
        net_pnl = pnl_by_venue.get(venue, 0.0)
        upnl = upnl_by_venue.get(venue, 0.0)
        starting = starting_by_venue.get(venue, 0.0)
        equity = starting + net_pnl + upnl
        # Naive per-stream DD: shortfall of current equity vs starting (peak
        # proxy). Best-effort display only — the global curve owns the true DD.
        drawdown_pct = (
            max(0.0, (starting - equity) / starting * 100.0) if starting > 0 else 0.0
        )
        fee = fee_by_venue.get(venue, 0.0)
        slippage = slip_by_venue.get(venue, 0.0)
        ai_cost = ai_cost_by_venue.get(venue, 0.0)
        # Display-only "evidence-based profit". ``net_pnl`` is ALREADY net of
        # fees (Σ close pnl − Σ fee, line above), so only the remaining cost
        # legs (slippage + ai_cost) are subtracted here — fees are NOT counted
        # twice. The economic identity reported = gross_close_pnl − fee −
        # slippage − ai_cost (matches posterior.py:_apply_cost, the canonical
        # gross-minus-costs model). Never feeds sizing/gating.
        net_after_cost = net_pnl - slippage - ai_cost
        out.append(
            StreamSummary(
                stream_id=stream_id,
                venue=venue,
                label=label,
                product_class=cfg.product_class,
                color=color,
                starting_capital=starting,
                equity_usd=equity,
                net_pnl_usd=net_pnl,
                upnl_usd=upnl,
                exposed_usd=exposed_by_venue.get(venue, 0.0),
                open_positions_n=open_by_venue.get(venue, 0),
                daily_trades=trades_by_venue.get(venue, 0),
                drawdown_pct=drawdown_pct,
                fee_usd=fee,
                slippage_usd=slippage,
                ai_cost_usd=ai_cost,
                net_after_cost_usd=net_after_cost,
                # OPEN vs CLOSED split: closed_n == this lane's closed-trade
                # count (same is_close sum as daily_trades — one source of
                # truth, surfaced under a clearer name). recent_closed is the
                # lane's most-recent closed trades (empty list when none).
                closed_n=trades_by_venue.get(venue, 0),
                recent_closed=recent_closed_by_venue.get(venue, []),
            )
        )
    return out
