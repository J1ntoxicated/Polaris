"""Polaris dashboard v1 — equity / pnl / drawdown / sharpe queries.

Session-anchored realised-PnL equity curves (demo + real-fee), drawdown/Sharpe
math, and the daily realised-PnL headline. Split out of ``snapshot_queries.py``
to keep each module ≤500 LOC (move-only; no logic change). Display-only — never
a trading path.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

from polaris.core.economics.fees import demo_fee_usd, real_fee_usd
from polaris.scripts.dashboard.snapshot_q_common import (
    _safe_query,
    _session_buckets,
    _session_start_ms,
    _today_start_ms,
)
from polaris.storage.measurement_reset import latest_reset


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


def _trend_start_ms(conn: sqlite3.Connection, *, now_s: int) -> int:
    """TREND curve anchor (ms) = max(session_start, latest stamped reset_ts).

    ``_session_start_ms`` alone anchors on the DB-restart / earliest fill. But a
    stamped ``measurement_resets`` row marks a main-logic batch that should
    restart the FORWARD measurement window (Jin 2026-06-23) — before this fix
    the TREND sparkline ignored that anchor and kept pre-reset fills baked into
    the curve. Falls back to the plain session anchor when no reset is stamped
    (the common case) or when the reset predates the session (already
    dominated by ``max``). Display-only; never a trading path.
    """
    session_start_ms = _session_start_ms(conn, now_s=now_s)
    reset = latest_reset(conn)
    if reset is None:
        return session_start_ms
    return max(session_start_ms, reset.reset_ts * 1000)


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

    Lookback anchors at ``_trend_start_ms`` (P0-2, Jin 2026-07-02) — the later of
    the session start or the latest stamped measurement-reset — so the TREND
    sparkline restarts at a main-logic reset instead of always spanning the
    whole session.
    """
    session_start_ms = _trend_start_ms(conn, now_s=now_s)
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


def _realised_pnl_since(
    conn: sqlite3.Connection, *, lookback_ms: int, venue: str | None = None
) -> tuple[float, int]:
    """Realised PnL (NET of fees) & closed-trade count since ``lookback_ms``.

    Net = Σ(close pnl_usd) − Σ(fee_usd over ALL fills) in-window. ``fills.fee_usd``
    now holds the REAL fee (fill_normalizer 2026-06-01), so this is REAL-fee-net
    — aligned with the go-live viability signal; the 70 bps demo drain is shown
    separately by the dual-equity ``equity_demo``/``demo_fee_total``.

    Hardening #1 (2026-06-23): excludes RECONCILED (tracking-failure) fills —
    both legs — via a LEFT JOIN to ``positions`` gated on
    ``status != 'reconciled'`` (orphan fills with no matched position are KEPT).
    Matches the exclusion the R column (``_closed_position_r``) already applies,
    so the dollar headline and the R ledger agree. Display-only; the per-fill
    ``pnl_usd`` dollar truth is untouched.

    ``venue`` (Jin 2026-07-08 dashboard-live-net fix) narrows to one venue's
    fills when given — reused by ``snapshot_q_virtual`` to scope the VIRTUAL
    ledger's per-venue 'today'/'session' aggregates without a second query.
    ``None`` (the default) keeps the original unfiltered behavior byte-identical.
    """
    if venue is None:
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
    else:
        rows = _safe_query(
            conn,
            """SELECT COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd ELSE 0.0 END), 0.0)
                      - COALESCE(SUM(f.fee_usd), 0.0),
                      COALESCE(SUM(f.is_close), 0)
               FROM fills f
               LEFT JOIN positions p ON p.position_id = f.contribution_id
               WHERE f.ts_ms >= ? AND f.venue = ?
                 AND (p.status IS NULL OR p.status != 'reconciled')""",
            (lookback_ms, venue),
        )
    if not rows:
        return 0.0, 0
    return float(rows[0][0] or 0.0), int(rows[0][1] or 0)


def _daily_realised_pnl(
    conn: sqlite3.Connection, *, now_s: int
) -> tuple[float, int]:
    """Realised PnL (NET of fees) & closed-trade count over 'TODAY' (P0-2).

    'Today' floors the lookback at ``max(session_start, latest AEST midnight)``
    (``_today_start_ms``) so this headline never spans more than ~24h even
    after multi-day bot uptime (Jin 2026-07-02) — before this fix a multi-day
    session silently accumulated days of PnL under the "Today" label. The raw
    whole-session sum is preserved separately by ``_session_realised_pnl``.
    The per-stream ``net_pnl_usd`` rollup uses the IDENTICAL lookback so the
    reconciliation invariant (Σ streams == this total) holds.
    """
    lookback_ms = _today_start_ms(conn, now_s=now_s)
    return _realised_pnl_since(conn, lookback_ms=lookback_ms)


def _session_realised_pnl(
    conn: sqlite3.Connection, *, now_s: int
) -> tuple[float, int]:
    """Realised PnL (NET of fees) & closed-trade count over the whole SESSION.

    Lookback starts at the session anchor (``MIN(fills.ts_ms)``) — the pre-P0-2
    behavior of ``_daily_realised_pnl``, preserved under its own name/field
    ('SESSION' on the board) now that 'Today' floors at AEST midnight."""
    lookback_ms = _session_start_ms(conn, now_s=now_s)
    return _realised_pnl_since(conn, lookback_ms=lookback_ms)
