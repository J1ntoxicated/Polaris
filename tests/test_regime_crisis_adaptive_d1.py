"""D1 — crisis regime threshold per-class window-vol ADAPTIVE with UPPER CAP.

DEMO/PAPER only — virtual funds. SIGNAL-only calibration: the regime emits a
LABEL; it NEVER sizes, blocks, exits, or halts (flow_not_block intact). This is a
label-accuracy fix, not a defensive throttle — loss-defense stays precise-exit.

Problem (/debate D1): the equity crisis threshold was a FIXED -1.5% (floor 1.0 ×
m 1.5). Equity routinely pulls back > 1.5% over 100 bars, so the equity label was
trapped in the crisis bucket (81% mis-bucket). Fix:
  (a) crisis threshold = -m × clamp(window-vol, floor, CAP) — per-class window-vol
      ADAPTIVE with an upper cap (bull/bear are already window-adaptive).
  (b) equity vol floor raised 1.0 → 2.5%.
  (c) crypto crisis correction PRESERVED unchanged (cap == floor freezes it).
The cap bounds a crash-inflated window so crisis stays triggerable on a real
crash; the floor stops a flat window tripping on micro-noise.
"""

from __future__ import annotations

from polaris.core.data.schema import Bar
from polaris.scripts._production_indicators import (
    REGIME_CRISIS_M,
    REGIME_CRISIS_VOL_CAP_BY_CLASS,
    REGIME_VOL_FLOOR_BY_CLASS,
    compute_real_regime,
    compute_real_regime_signal,
)


def _make_bar(*, venue: str, symbol: str, group: str, ts: int, close: float) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}", underlying_group_id=group,
        venue=venue, symbol=symbol, bar_interval="1m", ts=ts,
        open=close, high=close * 1.001, low=close * 0.999, close=close,
        volume=1000.0, notional_usd=close * 1000.0, trade_count=10, vwap=close,
        bid_close=close, ask_close=close, spread_bps_close=2.0, source="test",
    )


def _equity_series(closes: list[float]) -> list[Bar]:
    return [
        _make_bar(venue="alpaca", symbol="AAPL", group="equity:AAPL",
                  ts=1_700_000_000 + i * 60, close=c)
        for i, c in enumerate(closes)
    ]


def _crash(base: float, *, venue: str, symbol: str, group: str,
           n: int = 120, frac: float = 0.92) -> list[Bar]:
    closes = [base] * n
    for i in range(n - 30, n):
        closes[i] = base * frac
    return [
        _make_bar(venue=venue, symbol=symbol, group=group,
                  ts=1_700_000_000 + i * 60, close=c)
        for i, c in enumerate(closes)
    ]


# ── core D1 fix: equity no longer trapped in crisis ─────────────────────────


def test_equity_smooth_pullback_was_crisis_now_bear() -> None:
    """A smooth ~-2% equity pullback over 100 bars (window-vol ≈ 0, efficiency ≈
    1.0 → a CLEAN trend, not a crisis). Under the old fixed -1.5% crisis floor its
    -1.67% drawdown tripped crisis; the adaptive floor 2.5 / cap path now lets it
    classify as the bear_trend it actually is."""
    base = 200.0
    closes = [base * (1.0 - 0.02 * (i / 119)) for i in range(120)]
    bars = _equity_series(closes)
    label = compute_real_regime(bars, asset_class="equity")
    assert label != "crisis", label
    assert label == "bear_trend", label


def test_equity_mis_bucket_swept_across_regimes() -> None:
    """A spread of equity series that the OLD fixed -1.5% threshold would have
    bucketed as crisis now spreads across non-crisis regimes — the equity label is
    no longer collapsed into a single crisis bucket."""
    base = 200.0
    # mild down-drift (bear), mild up-drift (bull), flat (chop)
    down = _equity_series([base * (1.0 - 0.018 * (i / 119)) for i in range(120)])
    up = _equity_series([base * (1.0 + 0.018 * (i / 119)) for i in range(120)])
    flat = _equity_series([base] * 120)
    labels = {
        compute_real_regime(down, asset_class="equity"),
        compute_real_regime(up, asset_class="equity"),
        compute_real_regime(flat, asset_class="equity"),
    }
    assert "crisis" not in labels, labels
    # genuinely populated across regimes, not collapsed to one bucket
    assert {"bull_trend", "bear_trend"} <= labels, labels


def test_equity_real_crash_still_crisis() -> None:
    """The cap keeps crisis TRIGGERABLE: a real ~-8% equity crash still flips to
    crisis (m × cap < crash drawdown), so the fix narrows the bucket without
    disabling crisis detection."""
    crash = _crash(200.0, venue="alpaca", symbol="AAPL", group="equity:AAPL")
    assert compute_real_regime(crash, asset_class="equity") == "crisis"


# ── crypto correction PRESERVED unchanged ───────────────────────────────────


def test_crypto_crisis_threshold_frozen_at_minus_3pct() -> None:
    """crypto cap == crypto floor (2.0) pins the crypto crisis threshold to
    -m·2.0 = -3.0% for ANY window vol — byte-identical to the legacy fixed crypto
    crisis. The crypto correction is preserved exactly."""
    assert REGIME_CRISIS_VOL_CAP_BY_CLASS["crypto"] == REGIME_VOL_FLOOR_BY_CLASS["crypto"]
    # a quiet crypto window and a volatile one both report -3.0% crisis_thresh.
    quiet = [
        _make_bar(venue="okx", symbol="BTC-USDT", group="crypto:BTC",
                  ts=1_700_000_000 + i * 60, close=60_000.0)
        for i in range(120)
    ]
    volatile = [
        _make_bar(venue="okx", symbol="BTC-USDT", group="crypto:BTC",
                  ts=1_700_000_000 + i * 60,
                  close=60_000.0 * (1.0 + (0.03 if i % 2 else -0.03)))
        for i in range(120)
    ]
    _, _, ev_q = compute_real_regime_signal(quiet, asset_class="crypto")
    _, _, ev_v = compute_real_regime_signal(volatile, asset_class="crypto")
    assert abs(ev_q["crisis_thresh_pct"] - (-REGIME_CRISIS_M * 2.0)) < 1e-9
    assert abs(ev_v["crisis_thresh_pct"] - (-REGIME_CRISIS_M * 2.0)) < 1e-9


def test_crypto_crash_still_crisis_unchanged() -> None:
    """crypto crash classification is unchanged (still crisis at the -3.0% line)."""
    crash = _crash(60_000.0, venue="okx", symbol="BTC-USDT", group="crypto:BTC")
    assert compute_real_regime(crash, asset_class="crypto") == "crisis"


# ── the cap bounds the threshold (both sides clamped) ───────────────────────


def test_cap_bounds_crisis_threshold_magnitude() -> None:
    """The crisis vol-scale is clamped to [floor, cap]: even a crash-inflated
    window (vol ≫ cap) reports a crisis_thresh no deeper than -m × cap. Without
    the cap the threshold would run past the crash's own drawdown."""
    crash = _crash(200.0, venue="alpaca", symbol="AAPL", group="equity:AAPL")
    _, _, ev = compute_real_regime_signal(crash, asset_class="equity")
    cap = REGIME_CRISIS_VOL_CAP_BY_CLASS["equity"]
    # window vol of an 8% crash (~8%) exceeds the cap → threshold pinned at -m·cap
    assert abs(ev["crisis_thresh_pct"] - (-REGIME_CRISIS_M * cap)) < 1e-9
    assert ev["crisis_scale_pct"] == cap


def test_floor_bounds_crisis_threshold_on_flat_window() -> None:
    """A dead-flat window (vol ≈ 0) clamps UP to the floor, not 0 — so crisis
    can't degenerate to a -0% threshold that fires on any micro-dip."""
    flat = _equity_series([200.0] * 120)
    _, _, ev = compute_real_regime_signal(flat, asset_class="equity")
    floor = REGIME_VOL_FLOOR_BY_CLASS["equity"]
    assert ev["crisis_scale_pct"] == floor
    assert abs(ev["crisis_thresh_pct"] - (-REGIME_CRISIS_M * floor)) < 1e-9


def test_per_class_caps_above_floor_except_crypto() -> None:
    """Every non-crypto class cap sits above its floor (room to ADAPT); crypto cap
    == floor (frozen)."""
    for cls, cap in REGIME_CRISIS_VOL_CAP_BY_CLASS.items():
        floor = REGIME_VOL_FLOOR_BY_CLASS[cls]
        if cls == "crypto":
            assert cap == floor, cls
        else:
            assert cap > floor, cls
