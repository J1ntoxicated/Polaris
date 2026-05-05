"""Grid Bot — sideways market recurring PnL (BingX validation, NFI pattern). Pure (P6).

HYPOTHESIS-040: Static-range grid trading (sideways 70% of time).

Basis:
  BingX 287K users, 1.27B USDT grid AUM (2024 public stats). Freqtrade NFI includes
  grid-bot as a recognized alternative income mode. ATR compression + narrow 24h range
  = reliable sideways signal.

Logic (`evaluate_grid(tick, indicators)`):
  Entry conditions (all must hold):
    1. ATR_1H < 1% of price (low volatility — ATR compression)
    2. Price is in lower 30% OR upper 30% of 24h range (range boundary entry)
    3. No existing grid active for this ticker
  Grid state:
    - 5 levels, 1% spacing, $50 per level
    - Level prices: current ± (0%, 1%, 2%, 3%, 4%)
    - Entry at lower boundary (buy below) or upper (no short — SPOT-only)
  Exit:
    - TP +0.8% per level (fee-adjusted: 0.14% each side = 0.28% rt → net +0.52%)
    - No SL — range breakout handled by evaluate_grid returning EXIT
    - Range breakout: price moves outside 24h_range ± buffer (2% of price, absolute) → close all

SPOT-only constraints (ADR-001):
  - No short levels
  - Only BUY entries at lower boundary zones
  - TP-only exit (no SL grid level — range breach = full exit)

Edge estimate:
  - Sideways: 70% of 24h periods (empirical — BingX published win rate)
  - Each level: +0.52% net after fee
  - 5 levels × 0.52% = +2.6% per full grid cycle
  - Fee round-trip: 0.40% (OKX Lv1 0.20% × 2 — Phase 5 fee correction)

pure: true
code_path: src/strategies/grid_bot.py
test_path: tests/strategies/test_grid_bot.py
"""
from __future__ import annotations

from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Grid configuration
DEFAULT_ATR_PCT_THRESHOLD: float = 0.01      # ATR < 1% of price = compressed
DEFAULT_RANGE_BOUNDARY_PCT: float = 0.30     # lower/upper 30% of 24h range
DEFAULT_GRID_LEVELS: int = 5
DEFAULT_GRID_SPACING_PCT: float = 0.01       # 1% per grid level
DEFAULT_LEVEL_SIZE_USD: float = 50.0         # $50 per level
DEFAULT_TP_PCT: float = 0.008                # +0.8% TP per level
DEFAULT_BREAKOUT_BUFFER_PCT: float = 0.02    # Phase 5 Codex: absolute 2% (was range × 5% — noise trigger risk)
DEFAULT_TARGET_SIZE_USD: float = 200.0       # Total entry (4 of 5 levels active)


def _compute_grid_signal(
    tick: dict,
    atr_pct: Optional[float],
    high_24h: Optional[float],
    low_24h: Optional[float],
    is_active: bool,
    atr_pct_threshold: float = DEFAULT_ATR_PCT_THRESHOLD,
    range_boundary_pct: float = DEFAULT_RANGE_BOUNDARY_PCT,
    breakout_buffer_pct: float = DEFAULT_BREAKOUT_BUFFER_PCT,
    target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
) -> Signal:
    """Pure core — evaluate grid entry/exit from market snapshot.

    Args:
        tick: OKX ticker dict with 'last', 'ts' fields.
        atr_pct: ATR as fraction of price (e.g. 0.008 = 0.8%). None → skip ATR guard.
        high_24h: 24h high price. None → skip range guard.
        low_24h: 24h low price. None → skip range guard.
        is_active: True if grid already active for this ticker.

    Returns:
        Signal with action ENTER_LONG / EXIT / HOLD and grid metadata in reason.

    Pure function — no I/O, no module state access.
    """
    ts = int(tick.get("ts", 0) or 0) or 1
    last = float(tick.get("last", 0) or 0)
    if last <= 0:
        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0, reason="no_price")

    # ── Active grid: check for breakout exit ────────────────────────────────
    if is_active:
        if high_24h is not None and low_24h is not None and high_24h > low_24h:
            # Phase 5 Codex: absolute buffer (last × 2%) not range × 5%.
            # range × 5% was proportional to volatility → noisy trigger on wide-range days.
            # Absolute 2% of current price is range-independent and consistent.
            abs_buffer = last * breakout_buffer_pct
            breakout_upper = high_24h + abs_buffer
            breakout_lower = low_24h - abs_buffer
            if last > breakout_upper or last < breakout_lower:
                direction = "up" if last > breakout_upper else "down"
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.EXIT,
                    confidence=0.75,
                    reason=f"grid_breakout:{direction} last={last:.4f} range=[{low_24h:.4f},{high_24h:.4f}]",
                )
        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0, reason="grid_active_no_breakout")

    # ── New grid: entry conditions ───────────────────────────────────────────
    # Guard 1: ATR compression
    if atr_pct is not None and atr_pct >= atr_pct_threshold:
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"atr_too_high:{atr_pct*100:.2f}% >= {atr_pct_threshold*100:.1f}%",
        )

    # Guard 2: Range boundary (lower N%)
    if high_24h is None or low_24h is None or high_24h <= low_24h:
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="no_24h_range",
        )

    range_span = high_24h - low_24h
    lower_boundary = low_24h + range_span * range_boundary_pct
    in_lower_zone = last <= lower_boundary

    if not in_lower_zone:
        position_pct = (last - low_24h) / range_span * 100.0
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"not_at_boundary: pos={position_pct:.1f}% range=[{low_24h:.4f},{high_24h:.4f}]",
        )

    # All conditions met → ENTER grid
    range_pct = range_span / last * 100.0
    atr_str = f"{atr_pct*100:.2f}%" if atr_pct is not None else "N/A"
    confidence = 0.70 + min(0.15, (atr_pct_threshold - (atr_pct or 0)) * 10.0)
    return Signal(
        timestamp_ms=ts,
        action=SignalAction.ENTER_LONG,
        confidence=confidence,
        target_size_usd=target_size_usd,
        reason=(
            f"grid_entry: atr={atr_str} range_pct={range_pct:.2f}% "
            f"in_lower_{range_boundary_pct*100:.0f}% last={last:.4f}"
        ),
    )


class GridBot(Strategy):
    """Grid Bot — sideways range recurring BUY/TP cycling. Pure (P6).

    HYPOTHESIS-040: BingX-validated grid strategy for crypto sideways markets.
    SPOT-only: only lower-boundary BUY entries (no short grid levels).

    pure: true
    code_path: src/strategies/grid_bot.py
    test_path: tests/strategies/test_grid_bot.py
    """

    name = "grid_bot"
    min_window = 14  # need 14 candles for ATR

    def __init__(
        self,
        atr_pct_threshold: float = DEFAULT_ATR_PCT_THRESHOLD,
        range_boundary_pct: float = DEFAULT_RANGE_BOUNDARY_PCT,
        grid_levels: int = DEFAULT_GRID_LEVELS,
        grid_spacing_pct: float = DEFAULT_GRID_SPACING_PCT,
        level_size_usd: float = DEFAULT_LEVEL_SIZE_USD,
        tp_pct: float = DEFAULT_TP_PCT,
        breakout_buffer_pct: float = DEFAULT_BREAKOUT_BUFFER_PCT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.atr_pct_threshold = atr_pct_threshold
        self.range_boundary_pct = range_boundary_pct
        self.grid_levels = grid_levels
        self.grid_spacing_pct = grid_spacing_pct
        self.level_size_usd = level_size_usd
        self.tp_pct = tp_pct
        self.breakout_buffer_pct = breakout_buffer_pct
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Candle-based evaluate — computes ATR + 24h range from window.

        Satisfies Strategy ABC. Realtime runner also uses evaluate_grid(tick, indicators).

        Args:
            window: list of Candle, most recent last. Needs >= 14 for ATR.

        Returns:
            Signal from _compute_grid_signal.
        """
        if not window:
            return Signal(timestamp_ms=1, action=SignalAction.HOLD, confidence=0.0, reason="empty_window")
        last_candle = window[-1]
        ts = last_candle.timestamp_ms
        last_price = last_candle.close

        # ATR from last 14 candles (simple true range average)
        atr_pct: Optional[float] = None
        if len(window) >= 14:
            trs = []
            for i in range(1, min(15, len(window))):
                c = window[-i]
                prev_c = window[-i - 1] if i < len(window) else c
                tr = max(
                    c.high - c.low,
                    abs(c.high - prev_c.close),
                    abs(c.low - prev_c.close),
                )
                trs.append(tr)
            atr = sum(trs) / len(trs) if trs else 0.0
            atr_pct = atr / last_price if last_price > 0 else None

        # 24h range from window (last 24 1H candles = 24h)
        recent = window[-24:] if len(window) >= 24 else window
        high_24h = max(c.high for c in recent) if recent else None
        low_24h = min(c.low for c in recent) if recent else None

        tick = {"last": last_price, "ts": ts}
        return _compute_grid_signal(
            tick, atr_pct, high_24h, low_24h, is_active=False,
            atr_pct_threshold=self.atr_pct_threshold,
            range_boundary_pct=self.range_boundary_pct,
            breakout_buffer_pct=self.breakout_buffer_pct,
            target_size_usd=self.target_size_usd,
        )

    def evaluate_grid(
        self,
        tick: dict,
        atr_pct: Optional[float],
        high_24h: Optional[float],
        low_24h: Optional[float],
        is_active: bool = False,
    ) -> Signal:
        """Tick-driven grid evaluation with pre-computed indicators. Pure.

        Args:
            tick: OKX tickers payload with 'last', 'ts'.
            atr_pct: Pre-computed ATR as fraction of price. None → skip ATR guard.
            high_24h: 24h high (from OKX tickers 'high24h' field). None → skip range guard.
            low_24h: 24h low (from OKX tickers 'low24h' field). None → skip range guard.
            is_active: True if a grid position is already open for this ticker.

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.

        Pure function — delegates to _compute_grid_signal with instance params.
        """
        return _compute_grid_signal(
            tick, atr_pct, high_24h, low_24h, is_active,
            atr_pct_threshold=self.atr_pct_threshold,
            range_boundary_pct=self.range_boundary_pct,
            breakout_buffer_pct=self.breakout_buffer_pct,
            target_size_usd=self.target_size_usd,
        )
