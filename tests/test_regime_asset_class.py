"""Per-asset-class CONTEXT — vol-normalized regime + asset_class MarketView.

DEMO/PAPER only — virtual funds. SIGNAL-only: regime emits a LABEL (+ richer
per-class context fields); it NEVER sizes, blocks, exits, or halts. The
2-consecutive-close confirm gate (``detect_regime_flip``) is unchanged.

Aggressive bias preserved — this is a FLOW INCREASE: FX / index / commodity
trends that the crypto-calibrated fixed thresholds buried in ``chop`` forever
now reach bull / bear so the tick engine's trend strategies can fire. No
defensive throttle, no sizing dampen, no entry block.

Problem fixed: a fixed +0.5% / 100-bar bull threshold is crypto-calibrated.
EURUSD moves ~0.5%/day, so a +0.5% 100-bar move almost never happens → FX/index
sat in ``chop`` permanently. The TREND + CRISIS magnitude thresholds are now
normalized to the instrument's OWN window realized-vol (floored per asset_class)
so each class flips on its own scale; the (already scale-free) efficiency-ratio
chop test is unchanged.
"""

from __future__ import annotations

from polaris.core.data.schema import Bar
from polaris.scripts._production_indicators import (
    REGIME_VOL_FLOOR_BY_CLASS,
    build_real_market_view,
    compute_real_regime,
    compute_real_regime_signal,
)


def _make_bar(*, venue: str, symbol: str, group: str, ts: int, close: float,
              high: float | None = None, low: float | None = None) -> Bar:
    hi = high if high is not None else close * 1.001
    lo = low if low is not None else close * 0.999
    return Bar(
        instrument_id=f"{venue}:{symbol}", underlying_group_id=group,
        venue=venue, symbol=symbol, bar_interval="1m", ts=ts,
        open=close, high=hi, low=lo, close=close, volume=1000.0,
        notional_usd=close * 1000.0, trade_count=10, vwap=close,
        bid_close=close, ask_close=close, spread_bps_close=2.0, source="test",
    )


def _series(n: int, *, base: float, drift: float, venue: str = "okx",
            symbol: str = "BTC-USDT", group: str = "crypto:BTC") -> list[Bar]:
    return [
        _make_bar(venue=venue, symbol=symbol, group=group,
                  ts=1_700_000_000 + i * 60, close=base + i * drift)
        for i in range(n)
    ]


def _crash_series(n: int = 120, *, base: float, frac: float = 0.92,
                  venue: str = "okx", symbol: str = "BTC-USDT",
                  group: str = "crypto:BTC") -> list[Bar]:
    bars = _series(n, base=base, drift=0.0, venue=venue, symbol=symbol, group=group)
    for i in range(-30, 0):
        b = bars[i]
        bars[i] = _make_bar(venue=venue, symbol=symbol, group=group,
                            ts=b.ts, close=b.close * frac)
    return bars


# ── PART 1: vol-normalized regime thresholds ────────────────────────────────


def test_fx_scale_trend_flips_not_stuck_chop() -> None:
    """A small (~0.15%) but real FX-scale 100-bar trend flips to bull/bear when
    classified as forex — instead of being buried in chop by the crypto-scale
    +0.5% threshold. This is the core bug fix (FX trend reaches the tick engine)."""
    # EURUSD ~1.0850, +0.15% over 100 bars (a genuine FX trend).
    fx_up = _series(120, base=1.0850, drift=1.0850 * 0.0015 / 100,
                    venue="capital", symbol="EURUSD", group="forex:EURUSD")
    fx_down = _series(120, base=1.0850, drift=-1.0850 * 0.0015 / 100,
                      venue="capital", symbol="EURUSD", group="forex:EURUSD")
    assert compute_real_regime(fx_up, asset_class="forex") == "bull_trend"
    assert compute_real_regime(fx_down, asset_class="forex") == "bear_trend"


def test_same_fx_trend_stuck_chop_under_crypto_scale() -> None:
    """The identical FX trend, classified with the crypto floor (the old uniform
    behavior), stays chop — proving the per-class normalization is what unblocks
    FX, not a global threshold loosening."""
    fx_up = _series(120, base=1.0850, drift=1.0850 * 0.0015 / 100,
                    venue="capital", symbol="EURUSD", group="forex:EURUSD")
    assert compute_real_regime(fx_up, asset_class="crypto") == "chop"


def test_crypto_behavior_preserved() -> None:
    """Crypto-scale bars keep their current classification (k/m chosen so the
    crypto floor reproduces the old +0.5% bull / -3% crisis thresholds)."""
    bull = _series(120, base=60_000.0, drift=5.0)     # +0.83% / 100 bars
    flat = _series(120, base=60_000.0, drift=0.0)
    assert compute_real_regime(bull, asset_class="crypto") == "bull_trend"
    assert compute_real_regime(flat, asset_class="crypto") == "chop"


def test_crypto_default_matches_explicit_crypto() -> None:
    """Back-compat: omitting asset_class defaults to crypto (byte-identical to
    every pre-existing caller / golden test)."""
    bull = _series(120, base=60_000.0, drift=5.0)
    assert compute_real_regime(bull) == compute_real_regime(bull, asset_class="crypto")
    lbl_default, s_default, _ = compute_real_regime_signal(bull)
    lbl_crypto, s_crypto, _ = compute_real_regime_signal(bull, asset_class="crypto")
    assert lbl_default == lbl_crypto
    assert s_default == s_crypto


def test_dead_flat_window_stays_chop_all_classes() -> None:
    """A dead-flat window must not trip on micro-noise for ANY class — the
    per-class floor guards against zero-vol false trends."""
    for cls, (base, sym, grp, ven) in {
        "forex": (1.0850, "EURUSD", "forex:EURUSD", "capital"),
        "indices": (5000.0, "US500", "indices:US500", "capital"),
        "commodity": (2000.0, "XAUUSD", "commodity:XAU", "capital"),
        "crypto": (60_000.0, "BTC-USDT", "crypto:BTC", "okx"),
    }.items():
        flat = _series(120, base=base, drift=0.0, venue=ven, symbol=sym, group=grp)
        assert compute_real_regime(flat, asset_class=cls) == "chop", cls


def test_sharp_drop_still_crisis_per_class() -> None:
    """A sharp drawdown still flips to crisis for every class (the crisis path is
    vol-normalized too, but an 8% crash clears every class's floor·m)."""
    for cls, (base, sym, grp, ven) in {
        "forex": (1.0850, "EURUSD", "forex:EURUSD", "capital"),
        "crypto": (60_000.0, "BTC-USDT", "crypto:BTC", "okx"),
    }.items():
        crash = _crash_series(base=base, venue=ven, symbol=sym, group=grp)
        assert compute_real_regime(crash, asset_class=cls) == "crisis", cls


def test_thin_window_no_crash_falls_back() -> None:
    """Thin / near-zero-vol history must not div-by-zero / NaN — it falls back to
    the bootstrap chop (and never raises)."""
    thin = _series(10, base=1.0850, drift=0.0, venue="capital",
                   symbol="EURUSD", group="forex:EURUSD")
    label, strength, evidence = compute_real_regime_signal(thin, asset_class="forex")
    assert label == "chop"
    assert strength == 0.0
    assert isinstance(evidence, dict)


def test_zero_price_window_no_div_by_zero() -> None:
    """A window whose base price is 0 must fall back, not crash."""
    bars = _series(120, base=0.0, drift=0.0, venue="capital",
                   symbol="EURUSD", group="forex:EURUSD")
    label, _, _ = compute_real_regime_signal(bars, asset_class="forex")
    assert label == "chop"


def test_unknown_asset_class_degrades_safely() -> None:
    """An unknown asset_class must not raise; it uses the generic floor."""
    bull = _series(120, base=60_000.0, drift=5.0)
    label = compute_real_regime(bull, asset_class="weird_unknown_class")
    assert label in ("bull_trend", "bear_trend", "chop", "crisis")


def test_vol_floor_table_matches_atr_floor_spirit() -> None:
    """The regime vol floors echo ATR_FLOOR_BY_CLASS spirit (crypto 2.0 /
    forex 0.3 / index 0.4 / commodity 0.5 / equity 1.0)."""
    assert REGIME_VOL_FLOOR_BY_CLASS["crypto"] == 2.0
    assert REGIME_VOL_FLOOR_BY_CLASS["forex"] == 0.3
    assert REGIME_VOL_FLOOR_BY_CLASS["indices"] == 0.4
    assert REGIME_VOL_FLOOR_BY_CLASS["commodity"] == 0.5
    assert REGIME_VOL_FLOOR_BY_CLASS["equity"] == 1.0


# ── PART 2: asset_class-differentiated MarketView ───────────────────────────


def _ohlc_bars(n: int, *, base: float, drift: float, venue: str,
               symbol: str, group: str) -> list[Bar]:
    return _series(n, base=base, drift=drift, venue=venue, symbol=symbol, group=group)


def test_marketview_base_15_fields_byte_identical() -> None:
    """GOLDEN: the 15 base indicators are unchanged for a given bar set when the
    asset_class param is added (the 7 strategies depend on them byte-identically)."""
    bars = _series(120, base=60_000.0, drift=5.0)
    before = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=bars,
    )
    after = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=bars,
        asset_class="crypto",
    )
    for fld in (
        "last_price", "spread_bps", "atr_pct", "volume_z", "rsi_14",
        "bb_lower", "bb_upper", "bb_middle", "ma_200", "adx_14",
        "momentum_20bar", "donchian_high_40", "donchian_low_40",
        "donchian_high_30", "donchian_low_30",
    ):
        assert getattr(before, fld) == getattr(after, fld), fld


def test_marketview_fx_index_emphasis_fields() -> None:
    """FX / index get ema20/ema50 + ema_cross populated (trend-emphasis context)."""
    bars = _series(120, base=1.0850, drift=1.0850 * 0.0015 / 100,
                   venue="capital", symbol="EURUSD", group="forex:EURUSD")
    mv = build_real_market_view(
        venue="capital", symbol="EURUSD", timeframe="1m", bars=bars,
        asset_class="forex",
    )
    assert mv.ema_20 is not None
    assert mv.ema_50 is not None
    assert mv.ema_cross in (-1.0, 0.0, 1.0)
    # an uptrend → fast above slow → +1
    assert mv.ema_cross == 1.0


def test_marketview_commodity_trend_strength_field() -> None:
    """Commodity gets a trend-strength / efficiency field populated."""
    bars = _series(120, base=2000.0, drift=0.4, venue="capital",
                   symbol="XAUUSD", group="commodity:XAU")
    mv = build_real_market_view(
        venue="capital", symbol="XAUUSD", timeframe="1m", bars=bars,
        asset_class="commodity",
    )
    assert mv.trend_efficiency is not None
    assert 0.0 <= mv.trend_efficiency <= 1.0


def test_marketview_crypto_no_new_fields_default() -> None:
    """Crypto keeps its existing momentum emphasis — the new optional fields stay
    None so nothing downstream changes for crypto."""
    bars = _series(120, base=60_000.0, drift=5.0)
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=bars,
        asset_class="crypto",
    )
    assert mv.ema_20 is None
    assert mv.ema_50 is None
    assert mv.ema_cross is None
    assert mv.trend_efficiency is None
    assert mv.momentum_20bar is not None  # crypto momentum kept


def test_marketview_unknown_class_degrades_to_base() -> None:
    """An unknown asset_class degrades to the base view (no new fields, no crash)."""
    bars = _series(120, base=60_000.0, drift=5.0)
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=bars,
        asset_class="weird_unknown_class",
    )
    assert mv.ema_20 is None
    assert mv.trend_efficiency is None
    assert mv.last_price == bars[-1].close


def test_marketview_empty_bars_with_asset_class() -> None:
    """Empty bars + asset_class must not crash (early neutral return)."""
    mv = build_real_market_view(
        venue="capital", symbol="EURUSD", timeframe="1m", bars=[],
        asset_class="forex",
    )
    assert mv.last_price == 0.0
    assert mv.ema_20 is None
