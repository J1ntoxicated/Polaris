"""Polaris dashboard v1 — snapshot section queries (DB → typed row lists).

Per-section best-effort read helpers consumed by ``snapshot.collect_snapshot``.
All queries best-effort: missing tables / empty data return zero-defaults so the
dashboard never crashes a paper loop. Split out of ``snapshot.py`` to keep each
module ≤500 LOC; ``snapshot`` re-exports ``_read_positions`` for tests that
reference it via the original path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, NamedTuple

from polaris.core.economics.fees import demo_fee_usd, real_fee_usd
from polaris.core.metrics.risk_unit import R_USD_PROXY, realised_r_stream
from polaris.core.sizing.constants import (
    demo_starting_equity_capital,
    demo_starting_equity_okx,
)
from polaris.core.streams.config import STREAMS
from polaris.scripts.dashboard.snapshot_models import (
    CadenceReasonRow,
    ClosedTrade,
    GateEvent,
    PositionRow,
    SinceResetRollup,
    StrategySinceReset,
    StrategyStat,
    StreamSummary,
    TickerStat,
)
from polaris.storage.measurement_reset import MeasurementReset, latest_reset

logger = logging.getLogger(__name__)

# Step M (2026-06-22): the flat ``$10`` display heuristic is retired in favour
# of the canonical risk$-based R (positions.pnl_r) on every panel, with the ONE
# shared ``R_USD_PROXY`` as the only fallback for fills that have no matched
# position. ``DEFAULT_R_USD`` is kept as a backward-compat alias to that single
# proxy so any external importer resolves to the SAME number (no $10/$50 split).
DEFAULT_R_USD: Final[float] = R_USD_PROXY
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
    # Hardening #1 (2026-06-23): exclude RECONCILED (tracking-failure) fills (both
    # legs) so the equity curve matches the daily headline + R ledger. Orphan
    # fills (no matched position) are KEPT. Display-only; $ truth untouched.
    rows = _safe_query(
        conn,
        """SELECT f.ts_ms,
                  (CASE WHEN f.is_close = 1 THEN COALESCE(f.pnl_usd, 0.0) ELSE 0.0 END)
                  - COALESCE(f.fee_usd, 0.0) AS delta
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')
           ORDER BY f.ts_ms ASC""",
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

    Both legs RECOMPUTE the per-fill fee from the centralized schedule by
    notional: the demo leg via ``demo_fee_usd(venue, size_usd)`` (the 70 bps OKX
    sandbox drain) and the real leg via ``real_fee_usd(venue, size_usd)`` (the
    go-live signal). The stored ``fills.fee_usd`` is no longer read here — it now
    holds the REAL fee (fill_normalizer 2026-06-01), and the demo drain is a pure
    function of notional, so recomputing keeps the demo curve meaningful without
    a stored-demo-fee column. Read-only; trading behavior unchanged.
    """
    session_start_ms = _session_start_ms(conn, now_s=now_s)
    session_start_s = session_start_ms // 1000
    # Hardening #1 (2026-06-23): exclude RECONCILED (tracking-failure) fills (both
    # legs) so both fee-schedule curves match the daily headline + R ledger.
    # Orphan fills (no matched position) are KEPT. Display-only; $ truth untouched.
    rows = _safe_query(
        conn,
        """SELECT f.ts_ms,
                  f.venue,
                  f.size_usd,
                  (CASE WHEN f.is_close = 1 THEN COALESCE(f.pnl_usd, 0.0) ELSE 0.0 END)
                    AS gross
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')
           ORDER BY f.ts_ms ASC""",
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
            demo_fee = demo_fee_usd(venue, size_usd)
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

    Net = Σ(close pnl_usd) − Σ(fee_usd over ALL fills) in-window. ``fills.fee_usd``
    now holds the REAL fee (fill_normalizer 2026-06-01), so this headline is
    REAL-fee-net — aligned with the go-live viability signal; the 70 bps demo
    drain is shown separately by the dual-equity ``equity_demo``/``demo_fee_total``.
    The per-stream ``net_pnl_usd`` rollup uses the IDENTICAL formula so the
    reconciliation invariant (Σ streams == this total) holds.
    """
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    # Hardening #1 (2026-06-23): exclude RECONCILED (tracking-failure) fills from
    # the headline — both legs — via a LEFT JOIN to ``positions`` gated on
    # ``status != 'reconciled'`` (orphan fills with no matched position are KEPT).
    # Matches the exclusion the R column (``_closed_position_r``) already applies,
    # so the dollar headline and the R ledger agree. Display-only; the per-fill
    # ``pnl_usd`` dollar truth is untouched.
    rows = _safe_query(
        conn,
        """SELECT COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd ELSE 0.0 END), 0.0)
                  - COALESCE(SUM(f.fee_usd), 0.0),
                  COALESCE(SUM(f.is_close), 0)
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')""",
        (lookback_ms,),
    )
    if not rows:
        return 0.0, 0
    return float(rows[0][0] or 0.0), int(rows[0][1] or 0)


# ---------------------------------------------------------------------------
# Section: positions
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Section: per-strategy stats
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Section: measurement-reset baseline (forward edge since the latest reset)
# ---------------------------------------------------------------------------


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
    # Step N: each close row's R is the STREAM-COMMON realised R derived from its
    # OWN venue + ``fills.pnl_usd`` (``pnl_usd / R_budget(venue)``) — the same
    # ruler every other panel uses, comparable across venues, no stored-pnl_r
    # read (so OLD venue-skewed rows display on the new ruler too). An unknown
    # venue yields R_budget 0 → the shared R_USD_PROXY fallback (one number).
    rows = _safe_query(
        conn,
        """SELECT venue, instrument_id, strategy_id, side, fill_price,
                  pnl_usd, ts_ms, base_qty, contribution_id
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
        # Stream-common R from this venue's R_budget; unknown venue → proxy.
        r_units = realised_r_stream(pnl_usd=pnl, venue=venue)
        if r_units == 0.0 and pnl != 0.0:
            r_units = pnl / R_USD_PROXY
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
                r_units=r_units,
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
    # Hardening #1 (2026-06-23): exclude RECONCILED (tracking-failure) fills (both
    # legs) so the per-venue net/count match the global daily headline EXACTLY —
    # the reconciliation invariant (Σ streams == daily) is preserved because both
    # apply the identical ``status != 'reconciled'`` LEFT JOIN. Orphan fills KEPT.
    fill_rows = _safe_query(
        conn,
        """SELECT f.venue,
                  COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd ELSE 0.0 END), 0.0)
                  - COALESCE(SUM(f.fee_usd), 0.0) AS net_pnl,
                  COALESCE(SUM(f.is_close), 0) AS closed_n,
                  COALESCE(SUM(f.fee_usd), 0.0) AS fee_total,
                  COALESCE(SUM(f.slippage_bps / ? * f.size_usd), 0.0) AS slip_total
           FROM fills f
           LEFT JOIN positions p ON p.position_id = f.contribution_id
           WHERE f.ts_ms >= ?
             AND (p.status IS NULL OR p.status != 'reconciled')
           GROUP BY f.venue""",
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
    # source). NULL-position_id gate_events are UNATTRIBUTABLE and intentionally
    # excluded; their tokens are not assigned to any stream (documented gap,
    # display-only). NOTE (gate→outcome instrumentation): a run that ACTUALLY
    # OPENED now backfills its G1-G5 rows with the position_id, so opened
    # entries' pre-position tokens DO attribute here; only killed / never-opened
    # runs remain unattributed (per-venue AI cost is higher + more complete).
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


# ---------------------------------------------------------------------------
# Section: recent gate-event feed
# ---------------------------------------------------------------------------

# Gate id → short label, kept LOCAL (snapshot_sections imports from this module,
# so this module must not import back from it). Mirror of the _GATE_LABELS map.
_GATE_EVENT_LABELS: Final[Mapping[int, str]] = {
    1: "Universe",
    2: "Strategy",
    3: "Validator",
    4: "PreEntry",
    5: "Sizer",
    6: "Monitor",
    7: "Exit",
    8: "Reflector",
}

RECENT_GATE_EVENTS_N: Final[int] = 60


def _payload_strategy_symbol_reason(
    payload_json: str | None,
) -> tuple[str, str, str]:
    """Best-effort (strategy, symbol, reason) from a gate_events ``payload_json``.

    The table has no dedicated strategy/symbol columns — they live nested in the
    payload, and the shape varies by gate (``raw_signal`` for G2, ``validated_signal``
    for G3 MODIFY, top-level ``reason`` for G3 KILL / G6/G7, etc.). All lookups
    are graceful: a missing key / non-dict / bad JSON yields an empty string so
    the feed never crashes a refresh."""
    if not payload_json:
        return "", "", ""
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return "", "", ""
    if not isinstance(data, dict):
        return "", "", ""
    # The signal envelope can be nested under a few known keys.
    sig: dict[str, Any] = {}
    for key in ("raw_signal", "validated_signal", "signal", "sized_signal"):
        inner = data.get(key)
        if isinstance(inner, dict):
            sig = inner
            break
    strategy = str(sig.get("strategy_id") or data.get("strategy_id") or "")
    symbol = str(sig.get("symbol") or data.get("symbol") or "")
    reason = str(data.get("reason") or data.get("thesis_tag") or "")
    return strategy[:24], symbol[:18], reason[:80]


def _as_dict(v: Any) -> dict[str, Any]:
    """Narrow an arbitrary JSON value to a dict (empty when not a dict)."""
    return v if isinstance(v, dict) else {}


def _payload_detail(gate_id: int, payload_json: str | None) -> dict[str, Any]:
    """Per-gate rich detail for the live feed (display-only, all keys optional).

    G5 → the T4 size line (risk_pct/notional/scalar/tier/cell/leverage);
    G8 → the lesson (lesson_type/confidence); G1 → focus count. Other gates carry
    no extra detail (empty dict). Graceful on bad / absent JSON. NEVER feeds
    sizing/gating — pure feed chrome."""
    if not payload_json:
        return {}
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if gate_id == 5:
        sized = _as_dict(data.get("sized"))
        prop = _as_dict(sized.get("proposal"))
        out: dict[str, Any] = {}
        rp = sized.get("final_risk_pct")
        if isinstance(rp, (int, float)):
            out["risk_pct"] = round(float(rp) * 100.0, 3)
        nt = sized.get("final_notional_usd")
        if isinstance(nt, (int, float)):
            out["notional_usd"] = round(float(nt), 1)
        lev = sized.get("leverage")
        if isinstance(lev, (int, float)):
            out["leverage"] = float(lev)
        for k_src, k_dst in (
            ("continuous_scalar", "scalar"),
            ("tier_amplifier", "tier"),
            ("cell_routing_mult", "cell"),
        ):
            v = prop.get(k_src)
            if isinstance(v, (int, float)):
                out[k_dst] = round(float(v), 3)
        return out
    if gate_id == 8:
        out2: dict[str, Any] = {}
        lt = data.get("lesson_type")
        if isinstance(lt, str) and lt:
            out2["lesson_type"] = lt
        cf = data.get("confidence")
        if isinstance(cf, (int, float)):
            out2["confidence"] = round(float(cf), 3)
        return out2
    if gate_id == 1:
        focus = data.get("focus")
        if isinstance(focus, list):
            return {"focus_n": len(focus)}
    return {}


def _recent_gate_events(
    conn: sqlite3.Connection, *, n: int = RECENT_GATE_EVENTS_N
) -> list[GateEvent]:
    """Last ``n`` per-gate decisions (newest first) for the live gate feed.

    Display-only: only rows with a concrete ``decision`` are surfaced (a NULL
    decision is an in-flight ``request`` phase, not a verdict). strategy/symbol/
    reason are decoded best-effort from ``payload_json``, then back-filled via the
    linked position (``position_id``) and signal (``signal_id``) so HOLD/ADJUST
    monitor/exit events — whose payloads carry no signal envelope — still show a
    ticker + strategy label. Graceful empty when the table is absent."""
    # Volume guard (flow_not_block steady state): G6/G7 HOLD repeats every cycle
    # per open position (measured: G6 HOLD ≈ 99.97% of monitor events) and would
    # drown the meaningful trade-shaping decisions (G5 size / G6 ADJUST·EXIT·SWAP
    # / G7 ADJUST·EXIT / G8 lesson). Those repetitive HOLDs are still tallied in
    # the per-gate decision SUMMARY (``_gate_decisions``); here the feed carries
    # the notable decisions only.
    rows = _safe_query(
        conn,
        """SELECT ge.gate_id, ge.decision, ge.payload_json, ge.created_ts,
                  p.symbol, p.strategy_id, s.instrument_id, s.strategy_id
           FROM gate_events ge
           LEFT JOIN positions p ON p.position_id = ge.position_id
           LEFT JOIN signals s ON s.signal_id = ge.signal_id
           WHERE ge.decision IS NOT NULL
             AND NOT (ge.gate_id IN (6, 7) AND ge.decision = 'HOLD')
           ORDER BY ge.created_ts DESC
           LIMIT ?""",
        (n,),
    )
    out: list[GateEvent] = []
    for r in rows:
        gid = int(r[0] or 0)
        decision = str(r[1] or "").upper()
        strategy, symbol, reason = _payload_strategy_symbol_reason(r[2])
        # Back-fill blank labels: payload → positions → signals (display-only).
        symbol = symbol or str(r[4] or r[6] or "")[:18]
        strategy = strategy or str(r[5] or r[7] or "")[:24]
        out.append(
            GateEvent(
                gate_id=gid,
                label=_GATE_EVENT_LABELS.get(gid, f"G{gid}"),
                decision=decision,
                strategy=strategy,
                symbol=symbol,
                reason=reason,
                ts=int(r[3] or 0),
                detail=_payload_detail(gid, r[2]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Section: strategy descriptions (vault notes — file-based, no DB)
# ---------------------------------------------------------------------------

# Repo root: tools live under <repo>/polaris/scripts/dashboard/ → parents[3].
_STRATEGY_NOTES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3] / "vault" / "20_strategies"
)
_FRONTMATTER_SID_RE: Final[re.Pattern[str]] = re.compile(
    r"^strategy_id:\s*(.+)$", re.MULTILINE
)


def _one_line_description(text: str) -> str:
    """First non-empty prose line under a ``## Role`` heading, else the H1 title.

    The strategy notes put a one-line role summary under ``## Role``; fall back to
    the ``# …`` H1 (front-matter stripped) when no Role section exists. Returns ''
    when neither is present."""
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if raw.strip().lower().startswith("## role"):
            for follow in lines[i + 1 :]:
                s = follow.strip()
                if not s:
                    continue
                if s.startswith("#"):  # next heading before any prose
                    break
                return s.lstrip("-* ").strip()[:120]
            break
    for raw in lines:
        s = raw.strip()
        if s.startswith("# "):
            return s[2:].strip()[:120]
    return ""


def _strategy_descriptions(
    notes_dir: Path = _STRATEGY_NOTES_DIR,
) -> dict[str, str]:
    """{strategy_id: one-line description} from vault/20_strategies/*.md.

    File-based (no DB). Each note's ``strategy_id`` front-matter key supplies the
    map key (falls back to the file stem); the value is the ``## Role`` one-liner
    (or the H1). Graceful empty when the directory is absent and per-file
    best-effort so a single unreadable note never breaks the map. Display-only."""
    out: dict[str, str] = {}
    if not notes_dir.is_dir():
        return out
    for path in sorted(notes_dir.glob("*.md")):
        if path.name.startswith("MOC-"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _FRONTMATTER_SID_RE.search(text)
        sid = (m.group(1).strip() if m else path.stem).strip()
        desc = _one_line_description(text)
        if sid and desc:
            out[sid] = desc
    return out
