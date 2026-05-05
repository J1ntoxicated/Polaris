"""Realistic paper fill engine — slippage model (P6 pure).

Root fix for paper-vs-live divergence: paper currently fills at last trade price
(mid-ish), but live taker order crosses the spread + walks the book. This
internalizes that cost so paper PnL equals live PnL.

Fill price hierarchy (best → worst data):
    1. orderbook walk (top-N levels) → blended VWAP for target USD size
    2. bid/ask spread cross (if book empty but L1 quote available)
    3. last × (1 ± DEFAULT_FALLBACK_SLIP) — total fallback

INSIGHT-032: OKX Lv1 paper assumed 0.07%/side fee. Live also adds spread
cross (~1bp half-spread on majors, much wider on thin pairs) + market impact
(0.03%/side default). This module makes both visible in paper PnL.

ADR-013: SPOT taker model. For maker-mode (post-only limit), use
`compute_fill_price` with `mode='maker'` (TODO Phase 6).
"""
from __future__ import annotations

# Conservative default when neither book nor L1 quote is available.
# 0.05% per side ≈ majors taker median (BTC/ETH OKX Lv1 typical).
DEFAULT_FALLBACK_SLIP: float = 0.0005

# Default max acceptable spread for entry (basis points).
# 5bps ≈ 0.05% spread. Above this = thin pair, structural slippage.
DEFAULT_MAX_SPREAD_BPS: float = 5.0


def walk_book(
    levels: list[tuple[float, float]],
    target_usd: float,
) -> tuple[float, float]:
    """Walk orderbook levels to fill target USD; return (avg_price, filled_usd).

    levels: list of (price, size_in_base_currency), best price first
    target_usd: order notional to fill

    Pure: no I/O, deterministic.

    Returns (avg_fill_price, filled_usd). If book empty or target=0, returns
    (0.0, 0.0). If book has insufficient liquidity, returns partial fill at
    walked-through avg.
    """
    if not levels or target_usd <= 0:
        return 0.0, 0.0

    remaining = target_usd
    cost_sum = 0.0
    filled_usd = 0.0

    for price, size_base in levels:
        if price <= 0 or size_base <= 0:
            continue
        level_usd = price * size_base
        take_usd = min(level_usd, remaining)
        cost_sum += take_usd  # USD spent at this price
        filled_usd += take_usd
        remaining -= take_usd
        if remaining <= 0:
            break

    if filled_usd <= 0:
        return 0.0, 0.0

    # avg fill price = total_usd_spent / total_base_acquired
    # base_acquired = sum(take_usd / price) — but since cost_sum == filled_usd,
    # we recompute base from per-level take.
    base_acquired = 0.0
    remaining = target_usd
    for price, size_base in levels:
        if price <= 0 or size_base <= 0:
            continue
        level_usd = price * size_base
        take_usd = min(level_usd, remaining)
        base_acquired += take_usd / price
        remaining -= take_usd
        if remaining <= 0:
            break

    avg_price = filled_usd / base_acquired if base_acquired > 0 else 0.0
    return avg_price, filled_usd


def compute_fill_price(
    side: str,
    size_usd: float,
    book: dict,
    last: float,
    bid: float = 0.0,
    ask: float = 0.0,
) -> tuple[float, float]:
    """Compute realistic fill price for a market order.

    side: 'buy' or 'sell'
    size_usd: order notional
    book: {'bids': [(px, sz)], 'asks': [(px, sz)]} — top-N each
    last: last trade price (reference for slippage_bps)
    bid/ask: optional L1 fallback if book is empty

    Returns (fill_price, slippage_bps_vs_last). slippage_bps is always
    positive (cost magnitude) regardless of side.

    Pure: no I/O, no state.

    Fallback chain:
        1. Walk book (BUY consumes asks, SELL consumes bids)
        2. If book empty/insufficient → cross to L1 ask/bid
        3. If L1 also missing → last × (1 ± DEFAULT_FALLBACK_SLIP)
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if last <= 0:
        return 0.0, 0.0

    # 1. Try book walk
    levels_key = "asks" if side == "buy" else "bids"
    levels = book.get(levels_key, []) if isinstance(book, dict) else []
    book_avg, book_filled = walk_book(levels, size_usd)

    if book_filled >= size_usd and book_avg > 0:
        # Full book fill — best estimate
        slip_bps = abs(book_avg - last) / last * 10000
        return book_avg, slip_bps

    # 2. Partial book fill or empty → blend remainder with L1/default fallback
    fallback_price = 0.0
    if side == "buy":
        if ask > 0:
            fallback_price = ask
        else:
            fallback_price = last * (1 + DEFAULT_FALLBACK_SLIP)
    else:  # sell
        if bid > 0:
            fallback_price = bid
        else:
            fallback_price = last * (1 - DEFAULT_FALLBACK_SLIP)

    if book_filled <= 0:
        # No book fill at all → pure fallback
        slip_bps = abs(fallback_price - last) / last * 10000
        return fallback_price, slip_bps

    # Blend partial book + fallback
    remaining_usd = size_usd - book_filled
    blended = (book_avg * book_filled + fallback_price * remaining_usd) / size_usd
    slip_bps = abs(blended - last) / last * 10000
    return blended, slip_bps


def compute_spread_bps(bid: float, ask: float) -> float:
    """Spread in basis points: (ask - bid) / mid * 10000.

    Returns inf for invalid quotes (zero/crossed) to force conservative
    skip behavior.

    Pure.
    """
    if bid <= 0 or ask <= 0:
        return float("inf")
    if ask < bid:
        return float("inf")  # crossed book = invalid
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10000


def should_skip_entry_spread(
    spread_bps: float,
    max_bps: float = DEFAULT_MAX_SPREAD_BPS,
) -> bool:
    """Skip entry if spread exceeds threshold (structural slippage).

    Pure. No I/O.
    """
    return spread_bps > max_bps
