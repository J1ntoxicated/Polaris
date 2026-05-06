"""NFI X7 Dip-Buy — multi-TF oversold confluence (Polaris HYPO-NFI-001). Pure (P6).

NFI X7 basis (NostalgiaForInfinity X7, strat.ninja validated):
  - 88-92% win rate on backtests (40-80 USDT pair pool)
  - Multi-TF dip confluence: RSI_3 5m/15m + RSI_14 1h + AROONU 4h overbought avoidance
  - BB lower confluence for oversold confirmation
  - Exit: RSI_14 > 84 + close > BB upper (momentum exhaustion)

Polaris simplification v2 (SPOT-only, OKX paper — NFI-style 3-of-5 OR-AND):
  5 conditions scored (each = 1 point):
    1. RSI_3 5m  < 15  (relaxed from 5 — deep dip not required)
    2. RSI_3 15m < 15
    3. RSI_14 1h < 35  (relaxed from 30)
    4. price < bb_lower × 1.005  (0.5% buffer above BB lower)
    5. 4h AROON_UP < 85  (relaxed from 80)
  ENTER_LONG when score >= min_confluence (default 3).
  Exit: 1h RSI_14 > 84 OR close > 1h BB upper (2std).

Data: 5m/15m/1H/4H candle list via evaluate_multi_tf(tf_data)
HYPO-NFI-001: forward paper validation (no historical forceOrder / NFI exact params)

pure: true
code_path: src/strategies/nfi_dipbuy.py
test_path: tests/strategies/test_nfi_dipbuy.py
"""
from __future__ import annotations

from statistics import mean, stdev
from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Entry thresholds (v2 — relaxed for NFI-style 3-of-5 OR-AND)
DEFAULT_RSI3_ENTRY: float = 15.0      # RSI_3 < 15 on 5m or 15m (relaxed from 5)
DEFAULT_RSI14_ENTRY: float = 35.0     # RSI_14 < 35 on 1h (relaxed from 30)
DEFAULT_AROON_UP_MAX: float = 85.0    # 4h AROON_UP < 85 (relaxed from 80)
DEFAULT_BB_STD: float = 2.0           # Bollinger Band std multiplier
DEFAULT_BB_WINDOW: int = 20           # BB window (1h candles)
DEFAULT_BB_BUFFER: float = 1.005      # price < bb_lower × 1.005 (0.5% slack)
DEFAULT_MIN_CONFLUENCE: int = 2       # Phase 27.5 aggressive: 2-of-5 (was 3) — Jin "위험해도 먹고 나와"

# Exit thresholds
DEFAULT_RSI14_EXIT: float = 84.0      # RSI_14 > 84 on 1h = momentum exhaustion

# Target position size
DEFAULT_TARGET_SIZE_USD: float = 200.0


def compute_rsi(closes: list[float], period: int) -> float:
    """Wilder RSI approximation from close prices.

    Requires len(closes) >= period + 1. Returns 50.0 on insufficient data.

    Pure function — no I/O, no module state.
    """
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_aroon_up(highs: list[float], period: int) -> float:
    """AROON UP from recent n high prices.

    Standard formula: AROON_UP = (period - bars_since_high) / period * 100
      bars_since_high: bars ago the highest high occurred (0 = current bar is high).

    Computation:
      window = highs[-period:]  (oldest→newest, index 0→period-1)
      reversed_window[::-1]: index 0 = newest bar.
      bars_since_high = reversed_window.index(max_val)  (0 = newest = high just now)

    AROON_UP = 100 when max is most recent bar (bars_since=0).
    AROON_UP = 100/period when max is oldest bar (bars_since=period-1).

    Requires len(highs) >= period. Returns 50.0 on insufficient data.

    Pure function — no I/O.
    """
    if len(highs) < period:
        return 50.0
    window = highs[-period:]
    max_val = max(window)
    # Search from newest (reversed): first occurrence in reversed = smallest bars_since
    bars_since = window[::-1].index(max_val)
    return (period - bars_since) / period * 100.0


def _compute_nfi_signal(
    m5_closes: list[float],
    m15_closes: list[float],
    h1_closes: list[float],
    h4_highs: list[float],
    ts_ms: int,
    rsi3_entry: float = DEFAULT_RSI3_ENTRY,
    rsi14_entry: float = DEFAULT_RSI14_ENTRY,
    aroon_up_max: float = DEFAULT_AROON_UP_MAX,
    bb_std: float = DEFAULT_BB_STD,
    bb_window: int = DEFAULT_BB_WINDOW,
    bb_buffer: float = DEFAULT_BB_BUFFER,
    min_confluence: int = DEFAULT_MIN_CONFLUENCE,
    rsi14_exit: float = DEFAULT_RSI14_EXIT,
    target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
) -> Signal:
    """Pure NFI dip-buy signal from multi-TF close lists (v2 — 3-of-5 OR-AND).

    5 conditions each scored 1 point:
      1. RSI_3 5m  < rsi3_entry  (default 15)
      2. RSI_3 15m < rsi3_entry  (default 15)
      3. RSI_14 1h < rsi14_entry (default 35)
      4. price < bb_lower × bb_buffer  (default 1.005 = 0.5% above BB lower)
      5. 4h AROON_UP < aroon_up_max   (default 85)
    ENTER_LONG when score >= min_confluence (default 3).

    Args:
        m5_closes: 5m close price list (newest last). Min 4 required.
        m15_closes: 15m close price list (newest last). Min 4 required.
        h1_closes: 1h close price list (newest last). Min 20 required for BB + RSI_14.
        h4_highs: 4h high price list (newest last). Min 14 required for AROON.
        ts_ms: signal timestamp in milliseconds (>0).
        rsi3_entry: RSI_3 threshold for dip entry.
        rsi14_entry: RSI_14 threshold for 1h oversold.
        aroon_up_max: 4h AROON_UP upper bound (filter overbought).
        bb_std: Bollinger Band standard deviation multiplier.
        bb_window: BB lookback window (1h candles).
        bb_buffer: multiplier on bb_lower for price proximity check (1.005 = 0.5% slack).
        min_confluence: minimum score to trigger ENTER_LONG (1-5).
        rsi14_exit: RSI_14 threshold for exit signal.
        target_size_usd: position size for ENTER_LONG signal.

    Returns:
        Signal — ENTER_LONG / EXIT / HOLD.

    Pure function — no I/O.
    """
    hold = lambda reason: Signal(
        timestamp_ms=ts_ms, action=SignalAction.HOLD, confidence=0.0, reason=reason
    )

    # Data sufficiency guards
    if len(m5_closes) < 4:
        return hold("insufficient_5m")
    if len(m15_closes) < 4:
        return hold("insufficient_15m")
    if len(h1_closes) < bb_window + 1:
        return hold("insufficient_1h")
    if len(h4_highs) < 14:
        return hold("insufficient_4h")

    # ── 1h Bollinger Bands ──────────────────────────────────────────────────
    h1_window = h1_closes[-bb_window:]
    h1_mean = mean(h1_window)
    h1_std = stdev(h1_window) if len(h1_window) > 1 else 0.0
    bb_upper = h1_mean + bb_std * h1_std
    bb_lower = h1_mean - bb_std * h1_std
    h1_last = h1_closes[-1]

    # ── RSI indicators ──────────────────────────────────────────────────────
    rsi3_5m = compute_rsi(m5_closes, 3)
    rsi3_15m = compute_rsi(m15_closes, 3)
    rsi14_1h = compute_rsi(h1_closes, 14)

    # ── 4h AROON UP ─────────────────────────────────────────────────────────
    aroon_up_4h = compute_aroon_up(h4_highs, 14)

    # ── Exit check (takes priority over entry) ───────────────────────────────
    if rsi14_1h > rsi14_exit or h1_last > bb_upper:
        reason = (
            f"exit: rsi14_1h={rsi14_1h:.1f}>{rsi14_exit}"
            if rsi14_1h > rsi14_exit
            else f"exit: close={h1_last:.4f}>bb_upper={bb_upper:.4f}"
        )
        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.EXIT,
            confidence=0.70,
            reason=reason,
        )

    # ── 3-of-5 confluence scoring (NFI-style OR-AND) ────────────────────────
    # Each condition contributes 1 point; ENTER_LONG when score >= min_confluence.
    cond_rsi3_5m = rsi3_5m < rsi3_entry           # c1
    cond_rsi3_15m = rsi3_15m < rsi3_entry         # c2
    cond_rsi14_1h = rsi14_1h < rsi14_entry        # c3
    cond_bb = h1_last < bb_lower * bb_buffer      # c4: price within bb_buffer of BB lower
    cond_aroon = aroon_up_4h < aroon_up_max       # c5

    score = sum([cond_rsi3_5m, cond_rsi3_15m, cond_rsi14_1h, cond_bb, cond_aroon])

    if score >= min_confluence:
        # Confidence: base 0.60 + depth bonuses (capped 0.90)
        rsi14_depth = max(0.0, rsi14_entry - rsi14_1h) / 100.0 if cond_rsi14_1h else 0.0
        rsi3_depth = max(0.0, rsi3_entry - min(rsi3_5m, rsi3_15m)) / 100.0 if (cond_rsi3_5m or cond_rsi3_15m) else 0.0
        confidence = min(0.90, 0.60 + rsi14_depth + rsi3_depth)
        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.ENTER_LONG,
            confidence=confidence,
            target_size_usd=target_size_usd,
            reason=(
                f"nfi_dip conf={score}/5:"
                f" rsi3_5m={rsi3_5m:.1f} rsi3_15m={rsi3_15m:.1f}"
                f" rsi14_1h={rsi14_1h:.1f} bb_lower={bb_lower:.4f}"
                f" aroon_4h={aroon_up_4h:.0f}"
            ),
        )

    # Build HOLD reason for monitoring
    reasons = []
    if not cond_rsi3_5m:
        reasons.append(f"rsi3_5m={rsi3_5m:.1f}>={rsi3_entry}")
    if not cond_rsi3_15m:
        reasons.append(f"rsi3_15m={rsi3_15m:.1f}>={rsi3_entry}")
    if not cond_rsi14_1h:
        reasons.append(f"rsi14_1h={rsi14_1h:.1f}>={rsi14_entry}")
    if not cond_bb:
        reasons.append(f"price={h1_last:.4f}>={bb_lower:.4f}*{bb_buffer}")
    if not cond_aroon:
        reasons.append(f"aroon_4h={aroon_up_4h:.0f}>={aroon_up_max}")
    return hold(f"no_confluence({score}/5): " + "; ".join(reasons))


class NFIDipBuy(Strategy):
    """NFI X7 dip-buy — multi-TF oversold confluence. Pure (P6).

    HYPO-NFI-001: NFI X7 key parameters ported to Polaris (forward paper validation).

    Requires multi-TF candle data via evaluate_multi_tf(tf_data):
      tf_data keys: '5m', '15m', '1H', '4H'

    Minimum candle requirements:
      5m: 4 candles (RSI_3)
      15m: 4 candles (RSI_3)
      1H: 21 candles (RSI_14 + BB_20)
      4H: 14 candles (AROON_14)

    pure: true
    code_path: src/strategies/nfi_dipbuy.py
    test_path: tests/strategies/test_nfi_dipbuy.py
    """

    name = "nfi_dipbuy"
    min_window = 21  # 1H RSI_14 + BB_20 (the highest candle requirement)

    def __init__(
        self,
        rsi3_entry: float = DEFAULT_RSI3_ENTRY,
        rsi14_entry: float = DEFAULT_RSI14_ENTRY,
        aroon_up_max: float = DEFAULT_AROON_UP_MAX,
        bb_std: float = DEFAULT_BB_STD,
        bb_window: int = DEFAULT_BB_WINDOW,
        bb_buffer: float = DEFAULT_BB_BUFFER,
        min_confluence: int = DEFAULT_MIN_CONFLUENCE,
        rsi14_exit: float = DEFAULT_RSI14_EXIT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.rsi3_entry = rsi3_entry
        self.rsi14_entry = rsi14_entry
        self.aroon_up_max = aroon_up_max
        self.bb_std = bb_std
        self.bb_window = bb_window
        self.bb_buffer = bb_buffer
        self.min_confluence = min_confluence
        self.rsi14_exit = rsi14_exit
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Single-TF evaluate from 1H candles (satisfies Strategy ABC).

        For realtime use, call evaluate_multi_tf(tf_data) instead.
        Uses only 1H data — no 5m/15m/4H context.
        RSI_3 dip condition uses 1H RSI_3 as approximation.

        Args:
            window: 1H candles (newest last). Min 21 required.
        """
        if not window or len(window) < self.min_window:
            ts = window[-1].timestamp_ms if window else 1
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"insufficient_candles n={len(window)}<{self.min_window}",
            )
        ts = window[-1].timestamp_ms
        closes = [c.close for c in window]
        highs = [c.high for c in window]
        # Approximate multi-TF: use 1H closes for all RSI (single-TF fallback)
        return _compute_nfi_signal(
            m5_closes=closes[-4:],     # 1H as proxy
            m15_closes=closes[-4:],    # 1H as proxy
            h1_closes=closes,
            h4_highs=highs[-14:],
            ts_ms=ts,
            rsi3_entry=self.rsi3_entry,
            rsi14_entry=self.rsi14_entry,
            aroon_up_max=self.aroon_up_max,
            bb_std=self.bb_std,
            bb_window=self.bb_window,
            bb_buffer=self.bb_buffer,
            min_confluence=self.min_confluence,
            rsi14_exit=self.rsi14_exit,
            target_size_usd=self.target_size_usd,
        )

    def evaluate_multi_tf(self, tf_data: dict[str, list[Candle]]) -> Signal:
        """Multi-TF evaluate from 5m/15m/1H/4H candle data. Primary eval path.

        Args:
            tf_data: dict mapping timeframe string to candle list (newest last).
                     Keys: '5m', '15m', '1H', '4H'.

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.

        Pure function — no I/O.
        """
        m5 = tf_data.get("5m", [])
        m15 = tf_data.get("15m", [])
        h1 = tf_data.get("1H", [])
        h4 = tf_data.get("4H", [])

        # Extract close/high lists
        m5_closes = [c.close for c in m5]
        m15_closes = [c.close for c in m15]
        h1_closes = [c.close for c in h1]
        h4_highs = [c.high for c in h4]

        ts_ms = h1[-1].timestamp_ms if h1 else (m5[-1].timestamp_ms if m5 else 1)

        return _compute_nfi_signal(
            m5_closes=m5_closes,
            m15_closes=m15_closes,
            h1_closes=h1_closes,
            h4_highs=h4_highs,
            ts_ms=ts_ms,
            rsi3_entry=self.rsi3_entry,
            rsi14_entry=self.rsi14_entry,
            aroon_up_max=self.aroon_up_max,
            bb_std=self.bb_std,
            bb_window=self.bb_window,
            bb_buffer=self.bb_buffer,
            min_confluence=self.min_confluence,
            rsi14_exit=self.rsi14_exit,
            target_size_usd=self.target_size_usd,
        )
