"""Whale Wall Detection — pure (P6).

HYPOTHESIS-026: Large bid wall in OKX order book = strong support → mean-revert long.

Logic (`evaluate_book(okx_tick, book)`):
  OKX books5 — scan bid levels for large order.
  wall_detected = any bid level with size × price >= min_wall_usd ($100k)
  AND bid level within wall_proximity_pct (0.3%) of current price.

  If wall detected:
    - Current price is within wall_proximity_pct above the wall → ENTER_LONG
      (price near support, expect mean-revert / bounce)
  Exit:
    - Wall disappeared (no large bid in range) → EXIT (support removed)

Guards:
  - Book depth < 2 → HOLD (insufficient data)
  - Wall exists but price has moved too far above it (> exit_above_wall_pct) → HOLD
    (too late to mean-revert, wall already absorbed)

Source: OKX books5 WS (already active, HYPO-011 deprecated but WS still running).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_MIN_WALL_USD: float = 100_000.0    # $100k minimum wall size
DEFAULT_WALL_PROXIMITY_PCT: float = 0.003  # wall must be within 0.3% below current price
DEFAULT_EXIT_ABOVE_WALL_PCT: float = 0.015  # if price > wall by 1.5%, too late → HOLD
DEFAULT_TARGET_SIZE_USD: float = 200.0


class WhaleWall(Strategy):
    """Whale bid wall support mean-reversion — pure (P6).

    NOTE: standard `evaluate(window)` returns HOLD.
    Call `evaluate_book(okx_tick, book)`.

    book: dict with 'bids' = [(price, size), ...] and 'asks' = [(price, size), ...].
    """

    name = "whale_wall"
    min_window = 1

    def __init__(
        self,
        min_wall_usd: float = DEFAULT_MIN_WALL_USD,
        wall_proximity_pct: float = DEFAULT_WALL_PROXIMITY_PCT,
        exit_above_wall_pct: float = DEFAULT_EXIT_ABOVE_WALL_PCT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.min_wall_usd = min_wall_usd
        self.wall_proximity_pct = wall_proximity_pct
        self.exit_above_wall_pct = exit_above_wall_pct
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="WhaleWall requires book — use evaluate_book",
        )

    def _find_wall(self, bids: list, current_price: float) -> tuple[float, float] | None:
        """Find largest bid wall within proximity. Returns (wall_price, wall_usd) or None.

        Pure function — no I/O, no side effects.
        """
        if current_price <= 0:
            return None
        best_wall: tuple[float, float] | None = None
        best_usd = 0.0
        for bid_price, bid_size in bids:
            if bid_price <= 0 or bid_size <= 0:
                continue
            # Wall must be below current price but within proximity
            dist = (current_price - bid_price) / current_price
            if dist < 0 or dist > self.wall_proximity_pct:
                continue
            wall_usd = bid_price * bid_size
            if wall_usd >= self.min_wall_usd and wall_usd > best_usd:
                best_usd = wall_usd
                best_wall = (bid_price, wall_usd)
        return best_wall

    def evaluate_book(self, okx_tick: dict, book: dict) -> Signal:
        """Evaluate whale wall support signal.

        Args:
            okx_tick: OKX ticker dict with 'last' and 'ts'.
            book: dict with 'bids' = [(price, size), ...].

        Pure function — no I/O.
        """
        ts = int(okx_tick.get("ts", 0) or 0) or 1
        last = float(okx_tick.get("last", 0) or 0)

        if last <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="invalid last price",
            )

        bids = book.get("bids", [])
        if len(bids) < 2:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"insufficient book depth n={len(bids)}",
            )

        wall = self._find_wall(bids, last)

        if wall is None:
            # No wall detected — if we were in a position, this means support gone
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.60,
                reason="no whale wall in proximity (support removed)",
            )

        wall_price, wall_usd = wall
        dist_pct = (last - wall_price) / last

        # Too far above wall — missed the entry
        if dist_pct > self.exit_above_wall_pct:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"price too far above wall dist={dist_pct*100:.2f}% > {self.exit_above_wall_pct*100:.1f}%",
            )

        # Wall detected + price near support → enter long
        confidence = min(0.82, 0.55 + wall_usd / self.min_wall_usd * 0.05)
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.ENTER_LONG,
            confidence=confidence,
            target_size_usd=self.target_size_usd,
            reason=(
                f"whale_wall ${wall_usd:,.0f} @ {wall_price:.4g} "
                f"dist={dist_pct*100:.2f}%"
            ),
        )
