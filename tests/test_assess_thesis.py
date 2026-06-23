"""Adaptive thesis re-map — pure ``assess_thesis`` ManagementMode unit tests.

EXPECTANCY per-position EXIT/MANAGEMENT-TIMING only (flow_not_block):
- LET_RUN widens the trail (winners run), HARVEST tightens/banks near the peak,
  CUT closes an invalidated (broken + red) position, REMODE swaps the exit
  schedule (NOT the strategy), HOLD keeps existing defaults.
- NEVER an entry block / size cut / throttle; G6 -1.0R rail untouched.
- ``assess_thesis`` is PURE + TOTAL: never raises, None inputs degrade safe.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    Bucket,
    ManagementMode,
    ThesisGivebackParams,
    assess_thesis,
    bucket_from_correlation_group,
)

# Default giveback params (mirror the env defaults).
GB = ThesisGivebackParams(arm_r=0.30, frac=0.50, hard_frac=0.60)


def _assess(**kw: object) -> ManagementMode:
    """Call assess_thesis with safe defaults; override per-test."""
    base: dict[str, object] = dict(
        side="long",
        bucket=Bucket.TREND,
        mfe_r=0.0,
        mae_r=0.0,
        pnl_r=0.0,
        momentum_drift=0.0,
        atr_slope=0.0,
        ofi=None,
        flow_confirmed=None,
        regime="trend",
        entry_regime="trend",
        held_seconds=60,
        horizon_seconds=600,
        giveback=GB,
    )
    base.update(kw)
    return assess_thesis(**base)  # type: ignore[arg-type]


# --- Bucket mapping --------------------------------------------------------


def test_bucket_reversion_from_mean_reversion() -> None:
    assert bucket_from_correlation_group("spot_mean_reversion") is Bucket.REVERSION
    assert bucket_from_correlation_group("equity_mean_reversion") is Bucket.REVERSION


def test_bucket_reversion_from_range() -> None:
    assert bucket_from_correlation_group("cfd_fx_range") is Bucket.REVERSION


def test_bucket_trend_default() -> None:
    for cg in (
        "spot_cross_sectional_momo",
        "cfd_fx_trend",
        "spot_breakout",
        "spot_intraday_event",
        "equity_gap",
        "cfd_index_commodity_trend",
        "spot_ema_trend",
        "cfd_session_event",
    ):
        assert bucket_from_correlation_group(cg) is Bucket.TREND


def test_bucket_none_degrades_to_trend() -> None:
    assert bucket_from_correlation_group(None) is Bucket.TREND
    assert bucket_from_correlation_group("") is Bucket.TREND


# --- INTACT health → LET_RUN (trend) / HOLD (reversion) --------------------


def test_trend_intact_green_let_run() -> None:
    # green, same-dir momentum, OFI confirms, regime unchanged → thesis INTACT.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=1.0, pnl_r=0.8, momentum_drift=0.5,
        atr_slope=0.2, ofi=0.4, flow_confirmed=True,
    )
    assert m is ManagementMode.LET_RUN


def test_reversion_intact_does_not_let_run() -> None:
    # A reversion thesis intact does NOT widen (its edge is bounded) → HOLD.
    m = _assess(
        bucket=Bucket.REVERSION, mfe_r=0.5, pnl_r=0.4, momentum_drift=0.1,
        ofi=0.2, flow_confirmed=True,
    )
    assert m is ManagementMode.HOLD


# --- FADING health → HARVEST -----------------------------------------------


def test_trend_fading_after_green_harvests() -> None:
    # was green (mfe), momentum flattened + OFI decayed → FADING → HARVEST.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.6, pnl_r=0.3, momentum_drift=0.0,
        atr_slope=-0.05, ofi=0.0, flow_confirmed=False,
    )
    assert m is ManagementMode.HARVEST


def test_reversion_fading_harvests() -> None:
    m = _assess(
        bucket=Bucket.REVERSION, mfe_r=0.5, pnl_r=0.3, momentum_drift=0.0,
        ofi=0.0, flow_confirmed=False,
    )
    assert m is ManagementMode.HARVEST


# --- BROKEN health → CUT (red) / HARVEST (green) ---------------------------


def test_trend_broken_red_cuts() -> None:
    # momentum reversed against a long + red P&L → BROKEN + red → CUT.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.1, pnl_r=-0.4, momentum_drift=-0.6,
        atr_slope=0.1, ofi=-0.5, flow_confirmed=False,
    )
    assert m is ManagementMode.CUT


def test_trend_broken_green_harvests_not_cut() -> None:
    # thesis broke (momentum reversed) but the position is GREEN → never CUT a
    # green-broken position; bank it (HARVEST).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.8, pnl_r=0.5, momentum_drift=-0.6,
        ofi=-0.5, flow_confirmed=False,
    )
    assert m is ManagementMode.HARVEST


def test_broken_by_regime_flip_against_long_red_cuts() -> None:
    # regime flipped from trend (entry) to a down regime against the long + red.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.5, momentum_drift=-0.3,
        ofi=-0.3, regime="downtrend", entry_regime="uptrend",
    )
    assert m is ManagementMode.CUT


def test_ofi_opposes_short_broken_red_cuts() -> None:
    # short position, OFI firmly positive (buyers) opposes the short + red → CUT.
    m = _assess(
        side="short", bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.4,
        momentum_drift=0.6, ofi=0.5, flow_confirmed=False,
    )
    assert m is ManagementMode.CUT


# --- mfe_giveback modifier (orthogonal, highest precedence) ----------------


def test_giveback_soft_forces_harvest() -> None:
    # armed (mfe >= arm_r) and surrendered > frac of the peak but < hard_frac.
    # peak +1.0R, now +0.4R → surrendered 0.6 frac > 0.5 → HARVEST even if INTACT.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=1.0, pnl_r=0.4, momentum_drift=0.5,
        ofi=0.5, flow_confirmed=True,
    )
    assert m is ManagementMode.HARVEST


def test_giveback_hard_forces_harvest_immediate() -> None:
    # surrendered > hard_frac (0.6): peak +1.0R, now +0.3R → 0.7 frac → HARVEST
    # (the immediate thesis_harvest close is wired in mode_to_exit_params).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=1.0, pnl_r=0.3, momentum_drift=0.5,
        ofi=0.5, flow_confirmed=True,
    )
    assert m is ManagementMode.HARVEST


def test_giveback_not_armed_below_arm_r_no_force() -> None:
    # mfe 0.2 < arm_r 0.3 → giveback never arms; INTACT trend → LET_RUN.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.2, pnl_r=0.1, momentum_drift=0.5,
        ofi=0.4, flow_confirmed=True,
    )
    assert m is ManagementMode.LET_RUN


def test_giveback_precedence_over_broken_green() -> None:
    # broken-green would HARVEST anyway, but giveback also forces HARVEST — the
    # result is HARVEST (giveback never escalates a green to CUT).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=1.0, pnl_r=0.35, momentum_drift=-0.6,
        ofi=-0.5,
    )
    assert m is ManagementMode.HARVEST


# --- REMODE: regime flipped trend<->range but not against the position -----


def test_remode_regime_to_range_for_trend_bucket() -> None:
    # entry in a trend regime, now chop/range, position green & not broken →
    # swap the exit schedule (REMODE), don't widen or cut.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.4, pnl_r=0.2, momentum_drift=0.1,
        ofi=0.1, regime="chop", entry_regime="trend",
    )
    assert m is ManagementMode.REMODE


# --- HOLD: nothing decisive ------------------------------------------------


def test_flat_nothing_decisive_holds() -> None:
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=0.0, momentum_drift=0.0,
        atr_slope=0.0, ofi=None, flow_confirmed=None,
    )
    assert m is ManagementMode.HOLD


# --- Totality: None inputs never raise, degrade safe -----------------------


def test_all_none_inputs_degrade_to_hold() -> None:
    m = assess_thesis(
        side="long", bucket=Bucket.TREND, mfe_r=None, mae_r=None, pnl_r=None,
        momentum_drift=None, atr_slope=None, ofi=None, flow_confirmed=None,
        regime=None, entry_regime=None, held_seconds=None,
        horizon_seconds=None, giveback=GB,
    )
    assert m is ManagementMode.HOLD


def test_none_side_does_not_raise() -> None:
    # a malformed side must not raise — degrade safe.
    m = assess_thesis(
        side=None, bucket=Bucket.TREND, mfe_r=1.0, mae_r=0.0, pnl_r=0.5,
        momentum_drift=0.0, atr_slope=0.0, ofi=0.0, flow_confirmed=False,
        regime="trend", entry_regime="trend", held_seconds=60,
        horizon_seconds=600, giveback=GB,
    )
    assert isinstance(m, ManagementMode)


def test_never_cuts_a_green_position_property() -> None:
    # CUT requires red — exhaustively assert green never yields CUT.
    for drift in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for ofi in (-1.0, 0.0, 1.0):
            for regime, entry in (("downtrend", "uptrend"), ("trend", "trend")):
                m = _assess(
                    bucket=Bucket.TREND, mfe_r=0.5, pnl_r=0.3,
                    momentum_drift=drift, ofi=ofi, regime=regime,
                    entry_regime=entry,
                )
                assert m is not ManagementMode.CUT
