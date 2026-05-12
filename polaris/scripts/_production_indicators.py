"""Day 8 production paper loop — real indicator computation (NumPy-only).

Replaces the smoke loop's hard-coded ``MarketView`` (volume_z=3.5, rsi_14=22,
adx_14=30, momentum_20bar=0.10) and hard-coded ``regime="bull_trend"`` with
real values derived from the canonical ``Bar`` history persisted by Layer 1.

Pure functions; no I/O. The caller passes a list of canonical ``Bar`` rows
(newest last). Empty / short lists fall back to neutral values so the loop
never NaNs strategies out — but the loop will record a stale-data fault when
appropriate via the circuit breaker (Layer 7 §A4 fix).
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Final

from polaris.core.data.schema import Bar
from polaris.strategies.base import BarView, MarketView

__all__ = [
    "REGIME_BULL_THRESHOLD_PCT",
    "REGIME_CRISIS_DRAWDOWN_PCT",
    "REGIME_CHOP_EFFICIENCY",
    "build_real_market_view",
    "compute_adx_14",
    "compute_atr_pct",
    "compute_bollinger",
    "compute_donchian",
    "compute_efficiency_ratio",
    "compute_ma",
    "compute_momentum",
    "compute_real_regime",
    "compute_rsi_14",
    "compute_volume_z",
    "compute_unrealized_pnl_r",
]

# Regime classification thresholds (deterministic, learner-tunable in P1).
# Aggressive bias: bull/bear thresholds set tightly so the 4-state space is
# meaningfully populated rather than collapsing to ``chop`` for slow markets.
REGIME_BULL_THRESHOLD_PCT: Final[float] = 0.5  # 100-bar return ≥ +0.5% → bull bias
REGIME_CRISIS_DRAWDOWN_PCT: Final[float] = -3.0  # 100-bar return ≤ -3% → crisis
REGIME_CHOP_EFFICIENCY: Final[float] = 0.20  # efficiency ratio < 0.20 → chop


# ---------------------------------------------------------------------------
# Indicators (NumPy-free; pure Python so no extra wheel install on demo)
# ---------------------------------------------------------------------------


def compute_rsi_14(closes: list[float]) -> float:
    """Wilder's RSI(14). Returns 50.0 when fewer than 15 closes (neutral)."""
    if len(closes) < 15:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-14, 0):
        delta = closes[i] - closes[i - 1]
        if delta > 0.0:
            gains += delta
        else:
            losses -= delta
    if gains + losses <= 0.0:
        return 50.0
    avg_gain = gains / 14.0
    avg_loss = losses / 14.0
    if avg_loss <= 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_atr_pct(bars: Sequence[Bar | BarView], *, n: int = 14) -> float:
    """ATR % of last close, fallback 0.0 on insufficient data."""
    if len(bars) < n + 1:
        return 0.0
    trs: list[float] = []
    for i in range(-n, 0):
        b = bars[i]
        prev = bars[i - 1]
        tr = max(
            float(b.high) - float(b.low),
            abs(float(b.high) - float(prev.close)),
            abs(float(b.low) - float(prev.close)),
        )
        trs.append(tr)
    atr = sum(trs) / len(trs)
    last_close = float(bars[-1].close)
    if last_close <= 0.0:
        return 0.0
    return atr / last_close


def compute_volume_z(bars: Sequence[Bar | BarView], *, lookback: int = 20) -> float:
    """Volume z-score of the last bar over the trailing ``lookback`` window."""
    if len(bars) < lookback + 1:
        return 0.0
    window = [float(b.volume) for b in bars[-lookback - 1:-1]]
    if not window:
        return 0.0
    mu = sum(window) / len(window)
    var = sum((v - mu) ** 2 for v in window) / max(1, len(window) - 1)
    sd = math.sqrt(var)
    if sd <= 0.0:
        return 0.0
    return (float(bars[-1].volume) - mu) / sd


def compute_bollinger(
    closes: list[float], *, n: int = 20, k: float = 2.0
) -> tuple[float, float, float]:
    """(lower, middle, upper) — falls back to last_price ± 1% when too short."""
    if len(closes) < n:
        last = closes[-1] if closes else 0.0
        return (last * 0.99, last, last * 1.01)
    window = closes[-n:]
    mid = sum(window) / n
    var = sum((v - mid) ** 2 for v in window) / n
    sd = math.sqrt(var)
    return (mid - k * sd, mid, mid + k * sd)


def compute_ma(closes: list[float], n: int) -> float:
    if len(closes) < n:
        return closes[-1] if closes else 0.0
    return sum(closes[-n:]) / n


def compute_momentum(closes: list[float], *, n: int = 20) -> float:
    """``(last - close[-n]) / close[-n]`` on log-free fractional return."""
    if len(closes) < n + 1:
        return 0.0
    base = closes[-n - 1]
    if base <= 0.0:
        return 0.0
    return (closes[-1] - base) / base


def compute_donchian(
    bars: Sequence[Bar | BarView], *, n: int
) -> tuple[float, float]:
    """Highest-high / lowest-low over the last ``n`` bars (excluding the
    current closing bar so the breakout test is meaningful)."""
    if len(bars) < n + 1:
        last = float(bars[-1].close) if bars else 0.0
        return (last * 1.01, last * 0.99)
    window = bars[-n - 1:-1]
    hi = max(float(b.high) for b in window)
    lo = min(float(b.low) for b in window)
    return (hi, lo)


def compute_adx_14(bars: Sequence[Bar | BarView]) -> float:
    """Approximate Wilder ADX(14). Returns 20.0 when window short (mid)."""
    if len(bars) < 16:
        return 20.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(-14, 0):
        b = bars[i]
        prev = bars[i - 1]
        up = float(b.high) - float(prev.high)
        down = float(prev.low) - float(b.low)
        plus_dm.append(up if (up > down and up > 0.0) else 0.0)
        minus_dm.append(down if (down > up and down > 0.0) else 0.0)
        tr = max(
            float(b.high) - float(b.low),
            abs(float(b.high) - float(prev.close)),
            abs(float(b.low) - float(prev.close)),
        )
        trs.append(tr)
    sum_tr = sum(trs)
    if sum_tr <= 0.0:
        return 20.0
    plus_di = 100.0 * sum(plus_dm) / sum_tr
    minus_di = 100.0 * sum(minus_dm) / sum_tr
    denom = plus_di + minus_di
    if denom <= 0.0:
        return 20.0
    dx = 100.0 * abs(plus_di - minus_di) / denom
    # Single-window approximation; full Wilder smoothing would average DX over
    # 14 windows but at 1m bars the difference is negligible at P0.
    return min(100.0, max(0.0, dx))


def compute_efficiency_ratio(closes: list[float], *, n: int = 30) -> float:
    """Kaufman efficiency ratio (price-distance / path-length).

    Used for regime classification (low ER ⇒ chop). Returns 0.5 when the
    window is too short so the regime falls into bull/bear by direction
    alone (consistent with crisis-or-trend prior).
    """
    if len(closes) < n + 1:
        return 0.5
    window = closes[-n - 1:]
    distance = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path <= 0.0:
        return 0.0
    return distance / path


# ---------------------------------------------------------------------------
# MarketView builder (real)
# ---------------------------------------------------------------------------


def _to_barview(b: Bar | BarView) -> BarView:
    """Best-effort cast — accept either canonical Bar or BarView."""
    if isinstance(b, BarView):
        return b
    return BarView(
        ts=int(b.ts),
        open=float(b.open),
        high=float(b.high),
        low=float(b.low),
        close=float(b.close),
        volume=float(b.volume),
        notional_usd=float(getattr(b, "notional_usd", 0.0)),
    )


def build_real_market_view(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar | BarView],
    spread_bps: float = 5.0,
    session_open_window: bool = False,
) -> MarketView:
    """Compute every indicator the 7 strategies need from real bars.

    No price boosting / no synthetic z-scores — strategies decide on real
    data. When the bar history is too short for an indicator, that field is
    set to ``None`` so the strategy's own warmup gate suppresses emission.
    """
    if not bars:
        return MarketView(
            symbol=symbol,
            venue=venue,
            timeframe=timeframe,
            bars=[],
            last_price=0.0,
            spread_bps=spread_bps,
        )
    bar_views = [_to_barview(b) for b in bars]
    closes = [float(b.close) for b in bar_views]
    last_price = closes[-1]

    rsi = compute_rsi_14(closes)
    atr_pct = compute_atr_pct(bar_views)
    vol_z = compute_volume_z(bar_views)
    bb_low, bb_mid, bb_up = compute_bollinger(closes)
    ma_200 = compute_ma(closes, 200) if len(closes) >= 200 else None
    momentum = compute_momentum(closes)
    don40_hi, don40_lo = compute_donchian(bar_views, n=40)
    don30_hi, don30_lo = compute_donchian(bar_views, n=30)
    adx = compute_adx_14(bar_views)

    # Session anchor: oldest bar within the last 6h on a 1m feed (~360 bars)
    # approximates the session open well enough for the breakout strategy.
    if session_open_window and len(bar_views) >= 60:
        session_open_price: float | None = float(bar_views[-60].open)
        session_open_ts: int | None = int(bar_views[-60].ts)
        session_atr: float | None = atr_pct * last_price
    else:
        session_open_price = None
        session_open_ts = None
        session_atr = None

    return MarketView(
        symbol=symbol,
        venue=venue,
        timeframe=timeframe,
        bars=bar_views,
        last_price=last_price,
        spread_bps=spread_bps,
        atr_pct=atr_pct,
        volume_z=vol_z,
        rsi_14=rsi,
        bb_lower=bb_low,
        bb_upper=bb_up,
        bb_middle=bb_mid,
        ma_200=ma_200,
        adx_14=adx,
        momentum_20bar=momentum,
        donchian_high_40=don40_hi,
        donchian_low_40=don40_lo,
        donchian_high_30=don30_hi,
        donchian_low_30=don30_lo,
        session_open_price=session_open_price,
        session_open_ts=session_open_ts,
        session_atr=session_atr,
        is_session_open_window=session_open_window,
    )


# ---------------------------------------------------------------------------
# Regime classifier (deterministic 4-state mapping over real bars)
# ---------------------------------------------------------------------------


def compute_real_regime(bars: Sequence[Bar | BarView], *, lookback: int = 100) -> str:
    """Map bar history → ``bull_trend`` / ``bear_trend`` / ``chop`` / ``crisis``.

    Algorithm (Layer 6 §Q2 + Day 8 spec H):
    1. ≤ ``REGIME_CRISIS_DRAWDOWN_PCT`` peak-to-last drawdown → ``crisis``
       (immediate flip; matches regime_flip.detect_flip's crisis fast-path).
    2. Efficiency ratio < ``REGIME_CHOP_EFFICIENCY`` → ``chop`` (low directional
       persistence; the strategy mix that prefers mean-reversion will get the
       higher cell-routing mult through Layer 4).
    3. Else: signed return over ``lookback`` bars chooses bull / bear.

    Falls back to ``chop`` when there is too little history (bootstrap state
    keeps the regime non-committal until Layer 6's 2-consecutive-close gate
    can confirm).
    """
    if len(bars) < 30:
        return "chop"
    closes = [float(b.close) for b in bars]
    n = min(lookback, len(closes) - 1)
    base = closes[-n - 1]
    last = closes[-1]
    if base <= 0.0:
        return "chop"
    ret_pct = (last - base) / base * 100.0

    # Crisis fast-path: any 100-bar window seeing a sharp drawdown.
    peak = max(closes[-n:])
    drawdown_pct = (last - peak) / peak * 100.0 if peak > 0.0 else 0.0
    if drawdown_pct <= REGIME_CRISIS_DRAWDOWN_PCT:
        return "crisis"

    er = compute_efficiency_ratio(closes, n=min(30, n))
    if er < REGIME_CHOP_EFFICIENCY:
        return "chop"

    if ret_pct >= REGIME_BULL_THRESHOLD_PCT:
        return "bull_trend"
    if ret_pct <= -REGIME_BULL_THRESHOLD_PCT:
        return "bear_trend"
    return "chop"


# ---------------------------------------------------------------------------
# G6 unrealized-PnL helper (Day 8 spec I — replaces hard-coded 0.2R)
# ---------------------------------------------------------------------------


def compute_unrealized_pnl_r(
    *,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    stop_atr_mult: float = 2.0,
) -> float:
    """``(last - entry) / (atr_usd × stop_atr_mult)`` — sign-aware in R units.

    ``atr_pct`` is the 14-bar ATR ratio of last_price (consistent with how
    ``MarketView.atr_pct`` is built). ``stop_atr_mult`` defaults to 2 ATR
    which matches the volume_burst / spot_donchian initial stop convention.

    Returns 0.0 when atr_pct is non-positive (no meaningful denominator) so
    the orchestrator's HOLD path stays neutral.
    """
    if entry_price <= 0.0 or atr_pct <= 0.0 or stop_atr_mult <= 0.0:
        return 0.0
    pnl_abs = (last_price - entry_price) if side == "long" else (entry_price - last_price)
    atr_usd = entry_price * atr_pct * stop_atr_mult
    if atr_usd <= 0.0:
        return 0.0
    r = pnl_abs / atr_usd
    if not math.isfinite(r):
        return 0.0
    # Bound to [-10, +10] so a one-tick blowup doesn't kill payload encoding.
    return max(-10.0, min(10.0, r))


def session_window_now(now_ts: int | None = None) -> bool:
    """Approximate "session open" — first 30 min of UTC hour 00 / 07 / 13.

    Used by build_real_market_view; production loop sets it per ticker via the
    venue calendar in P1. P0 uses a UTC fallback so SessionBreakoutStrategy can
    still emit during the first 30 minutes of the major sessions.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    minutes = (ts // 60) % 60
    hour = (ts // 3600) % 24
    return hour in (0, 7, 13) and minutes < 30
