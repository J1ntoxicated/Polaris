"""P0a STEP 2 — config-variant mechanism (TDD).

OFFLINE harness only. Touches NO live trading / sizing / T4 chain / gate
thresholds. behavior-0: default instances emit identical RawSignal as the
module-level constants did before the refactor.

Test plan (from design.test_plan + step brief):
  (a) behavior-0 regression: default instances emit identical RawSignal for a
      fixed MarketView fixture (representative strategies).
  (b) a variant with an overridden threshold changes emission on a crafted view.
  (c) variant_id is stable + unique.
  (d) grid respects the total cap and per-param bounds.
  (e) NO locked param appears in PARAM_BOUNDS.
"""

from __future__ import annotations

import pytest

from polaris.core.evolve.param_bounds import PARAM_BOUNDS, varyable_params
from polaris.core.evolve.variants import (
    GRID_TOTAL_CAP,
    enumerate_grid,
    make_variant,
)
from polaris.strategies import (
    BarView,
    MarketView,
    RSIBBPullbackStrategy,
    SpotDonchianStrategy,
    VolumeBurstStrategy,
    XAUIndicesTrendStrategy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bars(n: int, *, base_close: float, drift: float = 0.0,
               base_high: float = 0.0, vol: float = 1000.0) -> list[BarView]:
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * 60,
                open=c - 0.5,
                high=max(c, base_high) + 0.5,
                low=c - 1.0,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


def _vol_burst_break_view(volume_z: float) -> MarketView:
    bars = _make_bars(30, base_close=100.0, drift=0.05)
    last = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                   close=109.0, volume=5000.0, notional_usd=545000.0)
    bars[-1] = last
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=volume_z,
    )


# ---------------------------------------------------------------------------
# (a) behavior-0 regression — default instance == frozen baseline
# ---------------------------------------------------------------------------


def _signal_tuple(sig: object) -> tuple[object, ...]:
    """Stable comparable projection ignoring the random signal_id."""
    assert sig is not None
    return (
        sig.strategy_id,  # type: ignore[attr-defined]
        sig.symbol,  # type: ignore[attr-defined]
        sig.side,  # type: ignore[attr-defined]
        round(sig.strength, 9),  # type: ignore[attr-defined]
        round(sig.sizing_hint, 9),  # type: ignore[attr-defined]
        sig.ttl_bars,  # type: ignore[attr-defined]
        sig.thesis_tag,  # type: ignore[attr-defined]
        sig.correlation_group,  # type: ignore[attr-defined]
        tuple(sorted(sig.tags.items())),  # type: ignore[attr-defined]
    )


def test_behavior0_volume_burst_default_unchanged() -> None:
    mv = _vol_burst_break_view(volume_z=3.5)
    sig = VolumeBurstStrategy().generate_raw_signal(mv)
    # Relaxed baseline: VOL_Z_THRESHOLD=1.75, excess=3.5-1.75=1.75 ->
    # strength=min(1, 0.6+0.1*1.75)=0.775, sizing=min(1,0.5+0.1*1.75)=0.675, ttl=10.
    # (strength/sizing curves are unchanged; excess is measured from the relaxed
    # threshold, so the default strength shifts with the lowered emit floor.)
    assert _signal_tuple(sig) == (
        "volume_burst", "BTC-USDT", "long", 0.775, 0.675, 10,
        "vol_z=3.50>break", "spot_intraday_event",
        (("atr_pct", "0.00100"), ("vol_z", "3.50")),
    )


def test_behavior0_rsi_bb_default_unchanged() -> None:
    bars = _make_bars(210, base_close=100.0, drift=0.1)
    last = BarView(ts=bars[-1].ts + 60, open=120.0, high=121.0, low=90.0,
                   close=120.0, volume=1000.0)
    bars[-1] = last
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=120.0, spread_bps=2.0,
        rsi_14=20.0, bb_lower=95.0, ma_200=100.0,
    )
    sig = RSIBBPullbackStrategy().generate_raw_signal(mv)
    # Relaxed: RSI_THRESHOLD=39, depth=(39-20)/39=0.487..,
    # strength=min(1,max(0.4,0.487+0.5))=0.9872.., ttl=4. (strength curve
    # unchanged; depth is measured from the relaxed threshold.)
    assert sig is not None
    assert sig.side == "long"
    assert sig.ttl_bars == 4
    assert round(sig.strength, 6) == round((39.0 - 20.0) / 39.0 + 0.5, 6)


# ---------------------------------------------------------------------------
# (b) overridden threshold changes emission
# ---------------------------------------------------------------------------


def test_variant_threshold_changes_entry_set() -> None:
    # volume_z=1.6 is BELOW the relaxed 1.75 default (default -> no signal) but
    # ABOVE a lower variant threshold of 1.5 (variant -> signal). A variant knob
    # still changes WHICH bars emit, now anchored to the relaxed default floor.
    mv = _vol_burst_break_view(volume_z=1.6)
    base = VolumeBurstStrategy()
    assert base.generate_raw_signal(mv) is None

    variant = make_variant(VolumeBurstStrategy, {"vol_z_threshold": 1.5})
    sig = variant.generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"


def test_second_entry_threshold_changes_entry_set() -> None:
    # atr_floor_pct is the 2nd entry-trigger knob: a higher floor blocks an
    # otherwise-firing burst (changes WHICH trades happen, not just strength).
    mv = _vol_burst_break_view(volume_z=3.5)  # atr_pct=0.001 in the fixture
    assert VolumeBurstStrategy().generate_raw_signal(mv) is not None
    # floor raised above the fixture's atr_pct=0.001 -> the entry is blocked.
    blocked = make_variant(VolumeBurstStrategy, {"atr_floor_pct": 0.01})
    assert blocked.generate_raw_signal(mv) is None


# ---------------------------------------------------------------------------
# (c) variant_id stable + unique
# ---------------------------------------------------------------------------


def test_variant_id_stable() -> None:
    a = make_variant(VolumeBurstStrategy, {"vol_z_threshold": 2.0, "atr_floor_pct": 0.0005})
    b = make_variant(VolumeBurstStrategy, {"atr_floor_pct": 0.0005, "vol_z_threshold": 2.0})
    # Order-independent, deterministic.
    assert a.variant_id == b.variant_id


def test_variant_id_unique_per_override() -> None:
    a = make_variant(VolumeBurstStrategy, {"vol_z_threshold": 2.0})
    b = make_variant(VolumeBurstStrategy, {"vol_z_threshold": 3.0})
    c = make_variant(SpotDonchianStrategy, {"adx_threshold": 18.0})
    assert a.variant_id != b.variant_id
    assert a.variant_id != c.variant_id


def test_variant_id_includes_strategy_and_params() -> None:
    v = make_variant(VolumeBurstStrategy, {"vol_z_threshold": 2.0, "atr_floor_pct": 0.001})
    assert v.variant_id.startswith("volume_burst|")
    assert "atr_floor_pct=0.001" in v.variant_id
    assert "vol_z_threshold=2.0" in v.variant_id


def test_default_variant_is_behavior0() -> None:
    # Empty override -> base values -> identical emission to the bare class.
    mv = _vol_burst_break_view(volume_z=3.5)
    base_sig = VolumeBurstStrategy().generate_raw_signal(mv)
    var_sig = make_variant(VolumeBurstStrategy, {}).generate_raw_signal(mv)
    assert _signal_tuple(base_sig) == _signal_tuple(var_sig)


# ---------------------------------------------------------------------------
# (d) grid respects cap + bounds
# ---------------------------------------------------------------------------


def test_grid_per_param_at_most_3_points() -> None:
    for strat_id, params in PARAM_BOUNDS.items():
        for name, grid in params.items():
            assert len(grid) <= 3, f"{strat_id}.{name} has >3 grid points"
            assert len(grid) >= 1


def test_grid_respects_total_cap() -> None:
    variants, truncated = enumerate_grid(VolumeBurstStrategy, cap=4)
    # volume_burst has 2 entry-set params x 3 points = 9 > 4 -> truncated.
    assert truncated is True
    assert len(variants) <= 4


def test_grid_full_when_under_cap() -> None:
    variants, truncated = enumerate_grid(VolumeBurstStrategy, cap=GRID_TOTAL_CAP)
    # volume_burst: vol_z_threshold(3) x atr_floor_pct(3) = 9 <= cap -> full.
    assert truncated is False
    assert len(variants) == 9
    # All variant_ids unique.
    ids = [v.variant_id for v in variants]
    assert len(set(ids)) == len(ids)


def test_strategy_with_no_entry_knob_has_empty_grid() -> None:
    # xau_indices_trend has a bare momentum>0 trigger: NO entry-set knob ->
    # empty grid (default-only evaluation downstream).
    variants, truncated = enumerate_grid(XAUIndicesTrendStrategy, cap=GRID_TOTAL_CAP)
    assert variants == []
    assert truncated is False
    assert varyable_params("xau_indices_trend") == ()


def test_grid_values_within_bounds() -> None:
    variants, _ = enumerate_grid(VolumeBurstStrategy, cap=GRID_TOTAL_CAP)
    grid = PARAM_BOUNDS["volume_burst"]
    for v in variants:
        for name, value in v.param_overrides.items():
            assert value in grid[name]


# ---------------------------------------------------------------------------
# (e) NO locked param appears in PARAM_BOUNDS
# ---------------------------------------------------------------------------


_LOCKED_NAMES = {
    # windows / periods (precomputed, fixed-window) + sizing-curve constants.
    "lookback", "LOOKBACK", "lookback_bars", "LOOKBACK_BARS",
    "rsi_period", "RSI_PERIOD", "bb_window", "BB_WINDOW", "bb_std", "BB_STD",
    "trend_filter_ma", "TREND_FILTER_MA", "window", "WINDOW",
    "adx_period", "ADX_PERIOD", "donchian_window", "DONCHIAN_WINDOW",
    "momentum_lookback", "MOMENTUM_LOOKBACK", "atr_period", "ATR_PERIOD",
    "open_window_minutes", "OPEN_WINDOW_MINUTES", "warmup_bars", "WARMUP_BARS",
    "strength_base", "strength_slope", "strength_floor", "strength_offset",
    "sizing_base", "sizing_slope", "adx_strength_denom", "excess_gain",
    "leverage_max", "LEVERAGE_MAX",
}


def test_no_locked_param_in_bounds() -> None:
    for strat_id, params in PARAM_BOUNDS.items():
        for name in params:
            assert name.lower() not in _LOCKED_NAMES, (
                f"locked param {name} leaked into PARAM_BOUNDS[{strat_id}]"
            )


def test_inert_and_sizing_knobs_not_varyable() -> None:
    # FIX 2/7c: ttl_bars is inert-in-replay (the precise-exit FSM never reads
    # it) and momentum_gain / gap_gain are signal-STRENGTH (sizing) knobs that
    # change pnl but NOT the trade SET. None may be a P0a entry-search target.
    removed = {"ttl_bars", "momentum_gain", "gap_gain"}
    for strat_id in PARAM_BOUNDS:
        offending = removed & set(varyable_params(strat_id))
        assert not offending, (
            f"inert/sizing knob(s) {sorted(offending)} leaked into "
            f"PARAM_BOUNDS[{strat_id}] — must NOT be searched"
        )
    # also assert at the SSOT dict level (no strategy declares them).
    for strat_id, params in PARAM_BOUNDS.items():
        assert removed.isdisjoint(params), strat_id


def test_make_variant_rejects_removed_knobs() -> None:
    # The removed knobs are no longer valid override keys (a typo/locked param
    # must never silently no-op).
    for cls, key in (
        (VolumeBurstStrategy, "ttl_bars"),
        (XAUIndicesTrendStrategy, "momentum_gain"),
    ):
        with pytest.raises(ValueError):
            make_variant(cls, {key: 1.0})


def test_bounds_only_cover_known_strategies() -> None:
    # The live-in-replay OKX strategies WITH an entry-set knob MUST be present.
    # tsmom has a bare momentum>0 trigger (no entry-set knob) so it is ABSENT
    # by design (its default config is still evaluated downstream, 1 trial).
    for required in ("volume_burst", "rsi_bb_pullback", "spot_donchian"):
        assert required in PARAM_BOUNDS
    assert "tsmom" not in PARAM_BOUNDS


def test_varyable_params_helper_matches_class_attrs() -> None:
    # Every PARAM_BOUNDS knob must be a real class attribute on the strategy
    # (so make_variant can override it via subclassing).
    name_to_cls = {
        "volume_burst": VolumeBurstStrategy,
        "rsi_bb_pullback": RSIBBPullbackStrategy,
        "spot_donchian": SpotDonchianStrategy,
    }
    for strat_id, cls in name_to_cls.items():
        for name in varyable_params(strat_id):
            assert hasattr(cls, name), f"{cls.__name__} missing attr {name}"


def test_make_variant_rejects_unknown_param() -> None:
    with pytest.raises(ValueError):
        make_variant(VolumeBurstStrategy, {"not_a_real_param": 1.0})
