"""Portfolio-level risk + correlation (Phase 13 — shell + pure mix).

Aggregates per-HYPO balances into a single portfolio view. Computes:
- Total equity (sum of all HYPO equity)
- Total realized PnL
- Portfolio drawdown vs running high-water mark
- Cross-strategy correlation (Pearson on per-window net PnL)
- Pre-trade global drawdown halt gate

API:
    snap = compute_portfolio_snapshot(ledger, current_prices, open_positions)
    halt = should_halt_portfolio(snap, max_drawdown_pct=0.05)
    corr = compute_correlation_matrix(ledger, hypo_ids, window_h=24)

Reference:
    INSIGHT-035 — single account vs multiple HYPO independent balances.
    Live transition needs portfolio-level cap, not per-strategy.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from src.persist.ledger import TradeLedger


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Frozen snapshot of portfolio state."""
    ts_ms: int
    total_equity_usd: float
    total_realized_usd: float
    total_open_count: int
    drawdown_pct: float        # vs running high water mark
    high_water_mark_usd: float
    n_active_hypos: int


def compute_portfolio_snapshot(
    ledger: TradeLedger,
    current_prices: dict[str, float] | None = None,
    ts_ms: int = 0,
) -> PortfolioSnapshot:
    """Compute current portfolio snapshot.

    Args:
        ledger: open TradeLedger.
        current_prices: dict[ticker, price] for open-position MTM. If empty,
                        open positions valued at entry price (conservative).
        ts_ms: snapshot timestamp.

    Returns frozen PortfolioSnapshot.
    """
    if current_prices is None:
        current_prices = {}

    # Aggregate balances across all HYPOs/tickers
    rows = ledger.conn.execute(
        "SELECT hypo_id, ticker, starting_usd, cash_usd, total_realized_usd "
        "FROM balances"
    ).fetchall()
    total_cash = sum(r["cash_usd"] for r in rows)
    total_starting = sum(r["starting_usd"] for r in rows)
    total_realized = sum(r["total_realized_usd"] for r in rows)
    n_active_hypos = len({r["hypo_id"] for r in rows})

    # Open positions MTM (mark-to-market)
    opens = ledger.get_open_positions()
    open_mtm = 0.0
    for pos in opens:
        ticker = pos.ticker
        price = current_prices.get(ticker, pos.entry_price)
        unreal = pos.direction * (price - pos.entry_price) / pos.entry_price
        open_mtm += pos.size_usd * (1.0 + unreal)

    # Cash already excludes open size_usd (PaperBalance.open() debited it)
    total_equity = total_cash + open_mtm

    # High water mark from snapshots (or starting if first run)
    hwm_row = ledger.conn.execute(
        "SELECT MAX(total_equity_usd) AS hwm FROM portfolio_snapshots"
    ).fetchone()
    hwm = float(hwm_row["hwm"] or total_starting)
    hwm = max(hwm, total_equity)  # extend if new high

    drawdown_pct = max(0.0, (hwm - total_equity) / hwm) if hwm > 0 else 0.0

    return PortfolioSnapshot(
        ts_ms=ts_ms,
        total_equity_usd=total_equity,
        total_realized_usd=total_realized,
        total_open_count=len(opens),
        drawdown_pct=drawdown_pct,
        high_water_mark_usd=hwm,
        n_active_hypos=n_active_hypos,
    )


def should_halt_portfolio(
    snapshot: PortfolioSnapshot,
    max_drawdown_pct: float = 0.05,
) -> tuple[bool, str]:
    """Pre-trade global gate — halt new entries if portfolio drawdown exceeds cap.

    Returns (halt, reason). reason is empty when not halted.

    Pure decision (no I/O).
    """
    if snapshot.drawdown_pct >= max_drawdown_pct:
        return True, (
            f"portfolio_halt drawdown={snapshot.drawdown_pct*100:.2f}% "
            f">= cap={max_drawdown_pct*100:.1f}% "
            f"hwm=${snapshot.high_water_mark_usd:.0f} "
            f"equity=${snapshot.total_equity_usd:.0f}"
        )
    return False, ""


def _bucket_pnl_by_hour(
    ledger: TradeLedger,
    hypo_id: str,
    since_ms: int,
) -> dict[int, float]:
    """Return {hour_ms: sum_net_usd} for closed positions in [since_ms, ∞)."""
    rows = ledger.conn.execute(
        "SELECT close_ts_ms, COALESCE(net_usd, 0) AS net "
        "FROM positions WHERE status='closed' AND hypo_id=? AND close_ts_ms >= ?",
        (hypo_id, since_ms),
    ).fetchall()
    buckets: dict[int, float] = {}
    HOUR_MS = 3_600_000
    for r in rows:
        h = (r["close_ts_ms"] // HOUR_MS) * HOUR_MS
        buckets[h] = buckets.get(h, 0.0) + r["net"]
    return buckets


def compute_correlation_matrix(
    ledger: TradeLedger,
    hypo_ids: list[str],
    window_h: int = 24,
    now_ms: int = 0,
) -> dict[tuple[str, str], float]:
    """Pearson correlation of hourly net PnL between strategy pairs.

    Returns dict[(hypo_a, hypo_b)] = pearson_r in [-1, 1].
    Symmetric: result[(a, b)] == result[(b, a)]. Diagonal == 1.0 (when n>=2).

    Returns NaN-equivalent (0.0) when insufficient data.

    Pure aggregation over ledger reads.
    """
    if not hypo_ids:
        return {}
    HOUR_MS = 3_600_000
    if now_ms <= 0:
        import time as _t
        now_ms = int(_t.time() * 1000)
    since_ms = now_ms - window_h * HOUR_MS

    # Per-hypo per-hour net PnL bucket
    buckets: dict[str, dict[int, float]] = {
        hid: _bucket_pnl_by_hour(ledger, hid, since_ms) for hid in hypo_ids
    }

    # Union of hour buckets (use 0 for missing)
    all_hours = sorted({h for b in buckets.values() for h in b.keys()})

    # Build aligned series
    series: dict[str, list[float]] = {
        hid: [buckets[hid].get(h, 0.0) for h in all_hours] for hid in hypo_ids
    }

    out: dict[tuple[str, str], float] = {}
    for a in hypo_ids:
        for b in hypo_ids:
            try:
                r = _pearson(series[a], series[b])
            except statistics.StatisticsError:
                r = 0.0
            out[(a, b)] = r
    return out


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient — pure (P6).

    Returns 0.0 if either series has zero variance or n < 2.
    """
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx2 = sum((x - mx) ** 2 for x in xs)
    dy2 = sum((y - my) ** 2 for y in ys)
    denom = (dx2 * dy2) ** 0.5
    if denom == 0:
        return 0.0
    return num / denom


def attribution_by_hypo(
    ledger: TradeLedger,
    since_ms: int = 0,
) -> list[dict]:
    """Per-HYPO attribution of net PnL since since_ms.

    Returns list[dict] sorted by net_usd desc:
        [{hypo_id, n_trades, n_wins, win_rate, total_net_usd, avg_net_pct}]
    """
    rows = ledger.conn.execute(
        """
        SELECT
            hypo_id,
            COUNT(*) AS n,
            SUM(CASE WHEN net_usd > 0 THEN 1 ELSE 0 END) AS wins,
            COALESCE(SUM(net_usd), 0) AS total_net,
            COALESCE(AVG(net_usd / NULLIF(entry_size_usd, 0)), 0) AS avg_net_pct
        FROM positions
        WHERE status = 'closed' AND close_ts_ms >= ?
        GROUP BY hypo_id
        ORDER BY total_net DESC
        """,
        (since_ms,),
    ).fetchall()
    return [
        {
            "hypo_id": r["hypo_id"],
            "n_trades": int(r["n"]),
            "n_wins": int(r["wins"] or 0),
            "win_rate": (r["wins"] / r["n"]) if r["n"] else 0.0,
            "total_net_usd": float(r["total_net"]),
            "avg_net_pct": float(r["avg_net_pct"] or 0),
        }
        for r in rows
    ]
