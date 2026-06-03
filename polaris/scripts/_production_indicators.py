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
    "REGIME_TREND_K",
    "REGIME_CRISIS_M",
    "REGIME_VOL_FLOOR_BY_CLASS",
    "build_real_market_view",
    "compute_adx_14",
    "compute_atr_pct",
    "compute_bollinger",
    "compute_donchian",
    "compute_efficiency_ratio",
    "compute_ma",
    "compute_momentum",
    "compute_real_regime",
    "compute_real_regime_signal",
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

# ── Vol-normalized TREND + CRISIS thresholds (per-asset_class) ──
# The fixed +0.5% bull / -3% crisis thresholds above are crypto-calibrated. FX
# (EURUSD ~0.5%/day) almost never moves +0.5% over 100 1m bars, so FX/index sat
# in ``chop`` forever and the tick engine's trend strategies never fired on a
# real FX/index trend. Fix: scale the TREND + CRISIS *magnitude* thresholds to
# the instrument's OWN window realized-vol (floored per asset_class) instead of
# fixed percents. The (already scale-free) efficiency-ratio chop test is kept.
#
# scale_pct = max(window realized-vol %, floor%); flip to bull/bear when the
# signed return ≥ REGIME_TREND_K × scale_pct; crisis when drawdown ≤
# -REGIME_CRISIS_M × scale_pct. k / m are chosen so the crypto FLOOR reproduces
# the old fixed thresholds exactly (crypto behavior preserved):
#   crypto floor 2.0% → bull = 0.25·2.0 = 0.5% ; crisis = -1.5·2.0 = -3.0%.
# The per-class FLOORS echo ATR_FLOOR_BY_CLASS spirit (a dead-flat window can't
# trip on micro-noise; a too-thin/zero-vol window falls back to the fixed path).
REGIME_TREND_K: Final[float] = 0.25  # bull/bear flip at k × vol-scale
REGIME_CRISIS_M: Final[float] = 1.5  # crisis flip at m × vol-scale
REGIME_VOL_FLOOR_BY_CLASS: Final[dict[str, float]] = {
    "crypto": 2.0,
    "forex": 0.3,
    "indices": 0.4,
    "commodity": 0.5,
    "equity": 1.0,
    "other": 0.5,
}
_REGIME_VOL_FLOOR_DEFAULT: Final[float] = 0.5  # unknown class → generic floor


def _regime_vol_floor(asset_class: str) -> float:
    """Per-asset_class realized-vol floor % (unknown class → generic floor)."""
    return REGIME_VOL_FLOOR_BY_CLASS.get(
        (asset_class or "").strip().lower(), _REGIME_VOL_FLOOR_DEFAULT
    )


def compute_window_vol_pct(closes: list[float], *, n: int) -> float:
    """Window realized-vol % = stdev(per-bar pct returns) × √n × 100.

    Scale-aware magnitude of how far the instrument *typically* travels over the
    ``n``-bar window. Returns 0.0 on a thin / degenerate window (caller floors
    it, so a 0 here just defers to the per-class floor — no div-by-zero/NaN)."""
    if n < 2 or len(closes) < n + 1:
        return 0.0
    rets: list[float] = []
    for i in range(len(closes) - n, len(closes)):
        prev = closes[i - 1]
        if prev <= 0.0:
            continue
        rets.append((closes[i] - prev) / prev)
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var) if var > 0.0 else 0.0
    out = sd * math.sqrt(n) * 100.0
    return out if math.isfinite(out) else 0.0


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
    asset_class: str = "crypto",
) -> MarketView:
    """Compute every indicator the 7 strategies need from real bars.

    No price boosting / no synthetic z-scores — strategies decide on real
    data. When the bar history is too short for an indicator, that field is
    set to ``None`` so the strategy's own warmup gate suppresses emission.

    The 15 base indicators are unchanged for a given bar set (the 7 strategies
    depend on them byte-identically). ``asset_class`` adds optional EMPHASIS
    context only (additive new fields — see ``_asset_class_emphasis``): ema20/
    ema50/ema_cross for FX & index (trend emphasis), a Kaufman trend_efficiency
    for commodity, and crypto keeps its momentum_20bar (no new fields). An
    unknown asset_class degrades to the base view (no new fields). Defaults to
    ``"crypto"`` so every existing caller is byte-identical.
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

    # Equity stream only (venue==alpaca → product_class equity in StreamConfig):
    # the gap_go strategy needs the prior session close and the open-vs-prev-close
    # gap. On the 1D equity feed each bar is one session, so prev_close is the
    # 2nd-to-last close. Left None for crypto/CFD (default), so the OKX/Capital
    # strategies' MarketView is unaffected (backward-compatible).
    prev_close: float | None = None
    gap_pct: float | None = None
    if venue.lower() == "alpaca" and len(bar_views) >= 2:
        prev_close = float(bar_views[-2].close)
        if prev_close > 0.0:
            gap_pct = (float(bar_views[-1].open) - prev_close) / prev_close

    ema_20, ema_50, ema_cross, trend_eff = _asset_class_emphasis(closes, asset_class)

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
        prev_close=prev_close,
        gap_pct=gap_pct,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_cross=ema_cross,
        trend_efficiency=trend_eff,
    )


def _asset_class_emphasis(
    closes: list[float], asset_class: str
) -> tuple[float | None, float | None, float | None, float | None]:
    """Per-asset_class EMPHASIS context — additive only, no base-field change.

    Returns ``(ema_20, ema_50, ema_cross, trend_efficiency)``:
      * FX / index → ema20 + ema50 + ema_cross (+1 fast>slow / -1 / 0). FX & index
        trades are trend-following on the EMA cross, not crypto momentum bursts.
      * commodity → trend_efficiency (Kaufman ER over the window), the strength of
        a directional commodity move.
      * crypto / unknown → all ``None`` (crypto keeps its momentum_20bar emphasis;
        an unknown class degrades to the base view).
    """
    cls = (asset_class or "").strip().lower()
    if cls in ("forex", "indices"):
        ema_fast = compute_ma(closes, min(20, len(closes)))
        ema_slow = compute_ma(closes, min(50, len(closes)))
        cross = 1.0 if ema_fast > ema_slow else (-1.0 if ema_fast < ema_slow else 0.0)
        return ema_fast, ema_slow, cross, None
    if cls == "commodity":
        return None, None, None, compute_efficiency_ratio(closes, n=min(30, len(closes) - 1))
    return None, None, None, None


# ---------------------------------------------------------------------------
# Regime classifier (deterministic 4-state mapping over real bars)
# ---------------------------------------------------------------------------


def compute_real_regime_signal(
    bars: Sequence[Bar | BarView],
    *,
    lookback: int = 100,
    asset_class: str = "crypto",
) -> tuple[str, float, dict[str, float]]:
    """Map bar history → ``(label, strength, evidence)`` (L3 price regime SIGNAL).

    ``strength`` (0..1) is a *magnitude boost only*: a confidence/conviction
    proxy consumed downstream by the weighted synthesis + dynamic confidence; it
    never changes which label is produced.

    Label algorithm (Layer 6 §Q2 + Day 8 spec H), now per-asset_class:
    1. ≤ ``-REGIME_CRISIS_M × vol_scale`` peak-to-last drawdown → ``crisis``.
    2. Efficiency ratio < ``REGIME_CHOP_EFFICIENCY`` → ``chop`` (scale-free).
    3. Else: signed return vs ``±REGIME_TREND_K × vol_scale`` chooses bull / bear.

    The TREND + CRISIS magnitude thresholds are normalized to the instrument's
    OWN window realized-vol (``compute_window_vol_pct``), floored by
    ``_regime_vol_floor(asset_class)``, so FX/index flip on their own scale
    instead of sitting in ``chop`` forever under the crypto-calibrated fixed
    percents. ``asset_class`` defaults to ``"crypto"``: the crypto floor
    reproduces the old fixed +0.5% / -3% thresholds exactly (crypto preserved,
    every existing caller byte-identical). Pure / deterministic; no lookahead.

    Strength inputs (보강값 only):
      * |return| vs the bull/bear threshold (directional magnitude),
      * efficiency ratio (directional persistence),
      * EMA20/50 cross alignment with the label,
      * 24h ATR ratio (volatility context — caps strength for crisis high).

    Falls back to ``("chop", 0.0, {})`` when there is too little history (also
    the safe path on a thin / zero-vol / zero-price window — no div-by-zero/NaN).
    """
    if len(bars) < 30:
        return "chop", 0.0, {}
    closes = [float(b.close) for b in bars]
    n = min(lookback, len(closes) - 1)
    base = closes[-n - 1]
    last = closes[-1]
    if base <= 0.0:
        return "chop", 0.0, {}
    ret_pct = (last - base) / base * 100.0
    er = compute_efficiency_ratio(closes, n=min(30, n))
    atr_ratio = compute_atr_pct(bars, n=min(14, len(bars) - 1))

    peak = max(closes[-n:])
    drawdown_pct = (last - peak) / peak * 100.0 if peak > 0.0 else 0.0

    ema_fast = compute_ma(closes, min(20, len(closes)))
    ema_slow = compute_ma(closes, min(50, len(closes)))
    ema_cross = 1.0 if ema_fast > ema_slow else (-1.0 if ema_fast < ema_slow else 0.0)

    # Per-asset_class vol-normalized magnitude thresholds.
    #   TREND  → k × max(window realized-vol %, floor): vol-ADAPTIVE so a quietly
    #            trending FX/index (small move on its own scale) still flips.
    #   CRISIS → m × floor (the per-class BASELINE vol): a deliberately FIXED
    #            tail-risk scale. The drawdown leg that defines a crisis is itself
    #            part of the window, so using the window's (crash-inflated) vol for
    #            the crisis threshold would let a crash lift its own bar and never
    #            trip — so crisis is normalized to baseline (floor) vol only. This
    #            also reproduces crypto's old -3% crisis exactly (m·2.0 = 3.0).
    floor_pct = _regime_vol_floor(asset_class)
    vol_pct = compute_window_vol_pct(closes, n=n)
    trend_scale = max(vol_pct, floor_pct)
    bull_thresh = REGIME_TREND_K * trend_scale
    crisis_thresh = -REGIME_CRISIS_M * floor_pct

    evidence: dict[str, float] = {
        "ret_pct": ret_pct,
        "efficiency_ratio": er,
        "drawdown_pct": drawdown_pct,
        "atr_ratio": atr_ratio,
        "ema_cross": ema_cross,
        "vol_scale_pct": trend_scale,
        "bull_thresh_pct": bull_thresh,
        "crisis_thresh_pct": crisis_thresh,
    }

    # ── Label (vol-normalized magnitudes; chop test scale-free) ──
    if drawdown_pct <= crisis_thresh:
        label = "crisis"
    elif er < REGIME_CHOP_EFFICIENCY:
        label = "chop"
    elif ret_pct >= bull_thresh:
        label = "bull_trend"
    elif ret_pct <= -bull_thresh:
        label = "bear_trend"
    else:
        label = "chop"

    strength = _regime_strength(
        label, ret_pct, er, drawdown_pct, ema_cross, bull_thresh, crisis_thresh
    )
    evidence["strength"] = strength
    return label, strength, evidence


def _regime_strength(
    label: str,
    ret_pct: float,
    er: float,
    drawdown_pct: float,
    ema_cross: float,
    bull_thresh: float,
    crisis_thresh: float,
) -> float:
    """Conviction proxy in ``[0, 1]`` — magnitude boost only, never a label gate.

    ``bull_thresh`` / ``crisis_thresh`` are the per-instrument vol-normalized flip
    thresholds, so the magnitude scaling tracks each class's own scale (FX +0.1%
    is as convincing on FX as crypto +0.5% is on crypto)."""
    if label == "crisis":
        # Deeper-than-threshold drawdown → high conviction (scaled past floor).
        excess = abs(drawdown_pct) - abs(crisis_thresh)
        denom = max(1e-9, abs(crisis_thresh) * (20.0 / 3.0))
        return min(1.0, 0.8 + max(0.0, excess) / denom)
    if label == "chop":
        # Chop conviction grows as efficiency falls below the chop floor.
        return min(1.0, max(0.0, (REGIME_CHOP_EFFICIENCY - er) / REGIME_CHOP_EFFICIENCY))
    # bull/bear trend: blend return magnitude, efficiency, EMA alignment.
    direction = 1.0 if label == "bull_trend" else -1.0
    full_scale = max(1e-9, bull_thresh * 10.0)
    mag = min(1.0, abs(ret_pct) / full_scale)
    align = 1.0 if (ema_cross == direction) else (0.5 if ema_cross == 0.0 else 0.0)
    raw = 0.55 * mag + 0.25 * min(1.0, er) + 0.20 * align
    return max(0.0, min(1.0, raw))


def compute_real_regime(
    bars: Sequence[Bar | BarView],
    *,
    lookback: int = 100,
    asset_class: str = "crypto",
) -> str:
    """Label-only wrapper over ``compute_real_regime_signal`` (back-compat).

    Returns just the regime label string. ``asset_class`` defaults to ``"crypto"``
    → byte-identical to the pre-existing behaviour. New callers wanting
    strength/evidence use ``compute_real_regime_signal``.
    """
    label, _, _ = compute_real_regime_signal(
        bars, lookback=lookback, asset_class=asset_class
    )
    return label


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
