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
from typing import Any, Final

from polaris.core.data.schema import Bar
from polaris.core.metrics.risk_unit import clamp_r
from polaris.strategies.base import AltDataView, BarView, MarketView

__all__ = [
    "REGIME_BULL_THRESHOLD_PCT",
    "REGIME_CRISIS_DRAWDOWN_PCT",
    "REGIME_CHOP_EFFICIENCY",
    "REGIME_TREND_K",
    "REGIME_CRISIS_M",
    "REGIME_VOL_FLOOR_BY_CLASS",
    "is_synthetic_bar",
    "filter_real_bars",
    "build_real_market_view",
    "map_altdata_to_market_fields",
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
    "session_anchor_index",
    "session_window_now",
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
# signed return ≥ REGIME_TREND_K × scale_pct. k is chosen so the crypto FLOOR
# reproduces the old fixed thresholds exactly (crypto behavior preserved):
#   crypto floor 2.0% → bull = 0.25·2.0 = 0.5%.
# The per-class FLOORS echo ATR_FLOOR_BY_CLASS spirit (a dead-flat window can't
# trip on micro-noise; a too-thin/zero-vol window falls back to the fixed path).
#
# CRISIS (D1 calibration — equity was 81% mis-bucketed as crisis):
# crisis when drawdown ≤ -REGIME_CRISIS_M × crisis_scale. Previously crisis_scale
# was the FIXED per-class floor, so equity sat at a single -1.5% bucket (floor
# 1.0 × m 1.5) — a routine -2% equity pullback tripped crisis and the equity
# label collapsed into crisis. Now crisis_scale is per-class window-vol ADAPTIVE,
# bounded BOTH ways: clamp(window-vol, floor, CAP). The floor keeps a dead-flat
# window from tripping on micro-noise; the CAP keeps the threshold triggerable on
# a real crash (a crash inflates its OWN window vol, so without the cap the crisis
# threshold could run away past the very drawdown that defines the crisis and
# never trip — the cap bounds that). Crypto is preserved EXACTLY by setting its
# crisis CAP == its floor (2.0): clamp pins crypto to -1.5·2.0 = -3.0% regardless
# of window vol, byte-identical to the old fixed crypto crisis.
REGIME_TREND_K: Final[float] = 0.25  # bull/bear flip at k × vol-scale
REGIME_CRISIS_M: Final[float] = 1.5  # crisis flip at m × crisis-scale
REGIME_VOL_FLOOR_BY_CLASS: Final[dict[str, float]] = {
    "crypto": 2.0,
    "forex": 0.3,
    "indices": 0.4,
    "commodity": 0.5,
    "equity": 2.5,
    "other": 0.5,
}
_REGIME_VOL_FLOOR_DEFAULT: Final[float] = 0.5  # unknown class → generic floor

# Per-class UPPER CAP on the crisis vol-scale (clamp(window-vol, floor, cap)).
# crypto cap == crypto floor → crypto crisis frozen at the old fixed -3.0%.
# Other classes' caps sit above their floors to give the window-vol room to
# ADAPT (so a genuinely volatile instrument's crisis threshold deepens) while
# staying low enough that a real crash (≈ -8% drawdown in the golden series)
# still clears m × cap and trips crisis.
REGIME_CRISIS_VOL_CAP_BY_CLASS: Final[dict[str, float]] = {
    "crypto": 2.0,
    "forex": 2.0,
    "indices": 2.5,
    "commodity": 3.0,
    "equity": 4.0,
    "other": 3.0,
}
_REGIME_CRISIS_VOL_CAP_DEFAULT: Final[float] = 3.0  # unknown class → generic cap


def _regime_vol_floor(asset_class: str) -> float:
    """Per-asset_class realized-vol floor % (unknown class → generic floor)."""
    return REGIME_VOL_FLOOR_BY_CLASS.get(
        (asset_class or "").strip().lower(), _REGIME_VOL_FLOOR_DEFAULT
    )


def _regime_crisis_vol_cap(asset_class: str) -> float:
    """Per-asset_class UPPER CAP on the crisis vol-scale (clamp ceiling).

    Bounds the adaptive crisis threshold so a crash-inflated window cannot push
    the threshold past its own drawdown (crisis stays triggerable). crypto cap ==
    crypto floor freezes crypto at the legacy -3.0% crisis exactly."""
    return REGIME_CRISIS_VOL_CAP_BY_CLASS.get(
        (asset_class or "").strip().lower(), _REGIME_CRISIS_VOL_CAP_DEFAULT
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
# Synthetic / flat-bar data-quality filter (measurement-first, NOT a block)
# ---------------------------------------------------------------------------
# The OKX 1m feed forward-fills gaps with FABRICATED flat bars (high==low==
# open==close) and zero-volume placeholders. Computing vol_z / ATR / donchian
# on those garbage bars poisons every signal (live DB ≈ 73% flat/zero-vol).
# This is DATA QUALITY, not an entry-block: a synthetic bar carries no real
# price action, so we skip it for INDICATOR purposes only. A symbol whose
# window is ALL-synthetic simply has no valid signal (data ABSENCE) — the
# strategy warmup gate sees too few real bars and emits nothing; it auto-
# resumes the instant real bars arrive. No throttle, no size-cut, no reject.
#
# VOLUME-LESS VENUES (Capital CFD): the Capital feed is PRICE-ONLY — every bar
# carries volume=0 by construction even though the OHLC is real market action.
# Applying the zero-volume rule there discarded the ENTIRE FX universe (live DB:
# ~106k volume-0-but-MOVING Capital bars), so filter_real_bars returned [] →
# build_real_market_view yielded an empty MarketView → the FX breakout
# strategies (fx_breakout_basket / session_breakout FX legs) had 0 real bars,
# never cleared warmup, and were permanently INERT (0 emit). On these venues a
# bar is synthetic by the zero-RANGE rule ALONE; volume==0 is the norm, not a
# placeholder. The range rule stays venue-agnostic (a flat bar is always inert).

# Venues whose price feed carries NO volume (price-only). On these a volume of 0
# is the NORM, not a forward-fill placeholder, so the zero-volume synthetic rule
# does not apply — only the zero-range rule does.
VOLUMELESS_VENUES: Final[frozenset[str]] = frozenset({"capital"})


def is_synthetic_bar(bar: Bar | BarView) -> bool:
    """True when a bar is fabricated/flat and carries no real price action.

    Synthetic ⇔ a zero-range bar where ``open == high == low == close`` (always),
    OR — on venues that DO report volume — zero (or non-positive) volume (an OKX
    forward-fill placeholder). On volume-less venues (Capital CFD, see
    ``VOLUMELESS_VENUES``) volume==0 is the price-only-feed norm and is NOT a
    synthetic signal, so only the zero-range rule applies there. Such a bar is
    excluded from indicator computation so vol_z / ATR / donchian are measured on
    REAL movement only. Pure / side-effect-free.
    """
    o = float(bar.open)
    h = float(bar.high)
    low = float(bar.low)
    c = float(bar.close)
    if o == h == low == c:
        return True  # zero-range: no price action (venue-agnostic)
    # zero-volume placeholder — only where the venue actually reports volume.
    venue = str(getattr(bar, "venue", "")).lower()
    return venue not in VOLUMELESS_VENUES and float(bar.volume) <= 0.0


def filter_real_bars(bars: Sequence[Bar | BarView]) -> list[Bar | BarView]:
    """Drop synthetic/flat bars, preserving order (newest last).

    Returns a NEW list of the real bars only. An empty result (all-synthetic
    window) signals data ABSENCE to the caller — handled as "no valid signal",
    never an error.
    """
    return [b for b in bars if not is_synthetic_bar(b)]


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


def map_altdata_to_market_fields(
    underlying_group_id: str,
    cache: Any,
    *,
    now_ts: float | None = None,
) -> AltDataView:
    """Map the FRESH AltDataCache snapshot for a group → STRATEGY-VISIBLE numerics.

    Reads the already-populated cache snapshot (NO network — the collector loop
    refreshes it on its own cadence). Pulls only the load-bearing numerics the
    vault flags as edge-relevant: funding / open-interest / COT net-spec
    percentile / VIX / crypto fear-greed / HY spread. Every absent / stale /
    keyless source leaves its field ``None`` = NEUTRAL no-op. SIGNAL only — never
    a block / throttle. Fail-soft: a cache that raises returns the neutral view.
    """
    if cache is None:
        return AltDataView()
    try:
        sources = cache.get_for_group(underlying_group_id, now_ts=now_ts)
    except Exception:  # noqa: BLE001 — fail-soft: any cache fault → neutral no-op
        return AltDataView()
    if not sources:
        return AltDataView()

    funding_rate, open_interest = _funding_oi_from(sources.get("okx_funding"))
    fg = sources.get("crypto_fg") or {}
    fg_val = fg.get("value")
    crypto_fear_greed = float(fg_val) if isinstance(fg_val, (int, float)) else None

    macro = sources.get("fred_macro") or {}
    vix_raw = macro.get("vix")
    hy_raw = macro.get("hy_spread")
    vix = float(vix_raw) if isinstance(vix_raw, (int, float)) else None
    hy_spread = float(hy_raw) if isinstance(hy_raw, (int, float)) else None

    cot_pctile = _cot_pctile_from(
        sources.get("cftc_cot"), underlying_group_id
    )

    return AltDataView(
        funding_rate=funding_rate,
        open_interest=open_interest,
        oi_change_24h=None,  # OKX public OI has no 24h delta → neutral
        cot_net_spec_pctile=cot_pctile,
        vix=vix,
        crypto_fear_greed=crypto_fear_greed,
        hy_spread=hy_spread,
    )


def _funding_oi_from(
    funding: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Mean funding rate + summed open interest across the perp instruments."""
    if not funding:
        return None, None
    rates = [
        row["fundingRate"]
        for row in funding.values()
        if isinstance(row, dict) and isinstance(row.get("fundingRate"), (int, float))
    ]
    ois = [
        row["oi"]
        for row in funding.values()
        if isinstance(row, dict) and isinstance(row.get("oi"), (int, float))
    ]
    funding_rate = sum(rates) / len(rates) if rates else None
    open_interest = float(sum(ois)) if ois else None
    return funding_rate, open_interest


def _cot_pctile_from(
    cot: dict[str, Any] | None, underlying_group_id: str
) -> float | None:
    """CFTC large-spec net percentile for THIS group's symbol, else neutral."""
    if not cot:
        return None
    symbol = (
        underlying_group_id.split(":", 1)[1] if ":" in underlying_group_id else ""
    )
    row = cot.get(symbol)
    if not isinstance(row, dict):
        return None
    pctile = row.get("net_spec_pctile")
    return float(pctile) if isinstance(pctile, (int, float)) else None


def build_real_market_view(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar | BarView],
    spread_bps: float = 5.0,
    session_open_window: bool = False,
    asset_class: str = "crypto",
    altdata_cache: Any = None,
    underlying_group_id: str | None = None,
    now_ts: float | None = None,
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

    ``altdata_cache`` (+ ``underlying_group_id``) is the OPTIONAL alt-data wire:
    when supplied, the cached funding / OI / COT / VIX / fear-greed / HY snapshot
    for the group is mapped into ``MarketView.altdata`` (STRATEGY-VISIBLE SIGNAL
    context). Absent / stale / keyless → a NEUTRAL no-op view (all None); a cache
    fault fails soft to neutral. No network. Every existing caller (no cache) gets
    the neutral default, so the view is byte-identical for them.
    """
    altdata = map_altdata_to_market_fields(
        underlying_group_id or f"{asset_class}:{symbol}",
        altdata_cache,
        now_ts=now_ts,
    )
    # Data-quality gate (measurement-first): compute indicators on REAL price
    # action only — drop OKX synthetic/flat/zero-vol forward-fill bars so vol_z
    # / ATR / donchian are not poisoned by fabricated flats. An all-synthetic
    # window collapses to the empty view → strategy warmup sees no real bars →
    # no signal (data ABSENCE, not a block; auto-resumes on real bars).
    real_bars = filter_real_bars(bars)
    if not real_bars:
        return MarketView(
            symbol=symbol,
            venue=venue,
            timeframe=timeframe,
            bars=[],
            last_price=0.0,
            spread_bps=spread_bps,
            altdata=altdata,
        )
    bar_views = [_to_barview(b) for b in real_bars]
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

    # Session anchor: the TRUE session-open bar (first bar at/after the most-
    # recent UTC session-open boundary), measured relative to the freshest bar's
    # timestamp. The old ``bar_views[-60]`` was a 1m-feed assumption (~1h back)
    # that ran ~5h STALE on the 5m feed session_breakout actually uses, so the
    # open±ATR breakout band was measured from the wrong reference. ``now`` is the
    # newest bar's ts (the freshest market clock in this context).
    session_open_price: float | None
    session_open_ts: int | None
    session_atr: float | None
    anchor_idx = (
        session_anchor_index(bar_views, now_ts=int(bar_views[-1].ts))
        if session_open_window
        else None
    )
    if anchor_idx is not None:
        session_open_price = float(bar_views[anchor_idx].open)
        session_open_ts = int(bar_views[anchor_idx].ts)
        session_atr = atr_pct * last_price
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
        altdata=altdata,
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
    #   CRISIS → m × clamp(window realized-vol %, floor, cap): vol-ADAPTIVE with a
    #            per-class UPPER CAP (D1). The floor stops a flat window tripping on
    #            micro-noise; the cap keeps the threshold from running away on a
    #            crash-inflated window (the crash is part of the window, so an
    #            uncapped window-vol crisis threshold could outrun its own drawdown
    #            and never trip). crypto cap == crypto floor (2.0) pins crypto to
    #            -1.5·2.0 = -3.0% — old crypto crisis preserved byte-identically.
    floor_pct = _regime_vol_floor(asset_class)
    crisis_cap_pct = _regime_crisis_vol_cap(asset_class)
    vol_pct = compute_window_vol_pct(closes, n=n)
    trend_scale = max(vol_pct, floor_pct)
    crisis_scale = min(max(vol_pct, floor_pct), crisis_cap_pct)
    bull_thresh = REGIME_TREND_K * trend_scale
    crisis_thresh = -REGIME_CRISIS_M * crisis_scale

    evidence: dict[str, float] = {
        "ret_pct": ret_pct,
        "efficiency_ratio": er,
        "drawdown_pct": drawdown_pct,
        "atr_ratio": atr_ratio,
        "ema_cross": ema_cross,
        "vol_scale_pct": trend_scale,
        "crisis_scale_pct": crisis_scale,
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
    # Step M (2026-06-22): the decision path now shares the ONE telemetry clamp
    # (±R_CLAMP, ±100) with the realised + excursion paths. The old ±10 here HID
    # catastrophic losses — a −34..−100R move read as −10R, so G6/G7/precise-exit
    # could not see the true loss magnitude. Measurement fix, not a throttle.
    return clamp_r(r)


# UTC session-open hours (00 = Asia, 07 = London, 13 = NY). SSOT for both the
# in-window predicate and the true-session-open anchor below.
_SESSION_OPEN_HOURS: Final[tuple[int, ...]] = (0, 7, 13)

# /debate-confirmable (Jin veto): per-session OPEN-window length in minutes,
# measured from each ``_SESSION_OPEN_HOURS`` boundary. Widened from a uniform
# 30-min rule (~90 min/day, 6.25% of the clock) to the real FX/index liquidity
# opens — London 07:00-08:30 and NY 13:00-14:30 (90 min, where EURUSD/GBPUSD/
# US500/US100 breakouts actually occur), Asia/midnight 00:00-01:00 (60 min,
# preserved). ~4-6x the aperture = MORE session_breakout signals (aggressive /
# flow_not_block). Anchor logic (session_anchor_index) is unchanged: it keys off
# _SESSION_OPEN_HOURS, not this map. old: uniform 30 min at all three hours.
_SESSION_WINDOW_MINUTES: Final[dict[int, int]] = {0: 60, 7: 90, 13: 90}


def session_window_now(now_ts: int | None = None) -> bool:
    """Approximate "session open" — within each UTC open's liquidity window.

    Asia/midnight 00:00-01:00 (60 min), London 07:00-08:30 (90 min), NY
    13:00-14:30 (90 min), per ``_SESSION_WINDOW_MINUTES``. A window may run past
    its open hour (London/NY are 90 min), so this measures minutes-elapsed since
    the most-recent open boundary, not ``minute < 30`` within the open hour.

    Used by build_real_market_view; production loop sets it per ticker via the
    venue calendar in P1. P0 uses this UTC fallback so SessionBreakoutStrategy
    can emit across the major liquidity opens.
    """
    ts = now_ts if now_ts is not None else int(time.time())
    minute_of_day = (ts // 60) % 1440
    for open_hour, window_minutes in _SESSION_WINDOW_MINUTES.items():
        open_minute = open_hour * 60
        if 0 <= (minute_of_day - open_minute) < window_minutes:
            return True
    return False


def session_anchor_index(
    bar_views: Sequence[BarView], now_ts: int | None = None
) -> int | None:
    """Index of the TRUE session-open bar (first bar at/after the open boundary).

    The session-open boundary is the most-recent UTC ``_SESSION_OPEN_HOURS``
    (00/07/13) timestamp at/before ``now_ts``. The anchor is the FIRST bar whose
    ``ts >= boundary`` — the actual session-start bar — so the open±ATR breakout
    band is measured from the real session open, NOT ``bar_views[-60]`` (a ~5h-
    stale 1m-feed assumption on the 5m feed session_breakout runs). Returns
    ``None`` when ``bar_views`` is empty or no bar falls at/after the boundary
    (degrades to no anchor → no signal, not a manufactured one). Pure / no I/O.
    """
    if not bar_views:
        return None
    ts = now_ts if now_ts is not None else int(time.time())
    hour = (ts // 3600) % 24
    day_start = (ts // 86400) * 86400
    # Most-recent open-hour boundary at/before ``ts`` (walk back through today's
    # open hours, else the latest open hour of the prior day).
    boundary: int | None = None
    for h in sorted(_SESSION_OPEN_HOURS, reverse=True):
        if h <= hour:
            boundary = day_start + h * 3600
            break
    if boundary is None:
        boundary = day_start - 86400 + max(_SESSION_OPEN_HOURS) * 3600
    for i, bv in enumerate(bar_views):
        if int(bv.ts) >= boundary:
            return i
    return None
