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


# --- FIX B: horizon-scoped materiality floor on the momentum-ONLY break ------
# [[1d_exit_horizon_fix_2026-06-26]]. The bar-recalc feeds momentum_drift from
# the last ~10 1m bars (a ~10-minute intraday window). A 1D thesis was declared
# BROKEN+CUT the instant that intraday window drifted fractionally against it
# (measured: 174/179 thesis_cut closes were shallow-red, median -0.03R, median
# hold 298s). The fix requires the momentum-ONLY reversal to be MATERIAL
# (|momentum_drift| >= EXIT_THESIS_DRIFT_FLOOR=0.0015) to count as BROKEN *while
# still within the strategy horizon*. The probe drift here is -0.0012: it CLEARS
# the universal EXIT_THESIS_DEADBAND (0.001) — so without this fix it WOULD break
# — but sits BELOW the 0.0015 horizon floor, so it is the case the fix suppresses.
# Corroborated breaks (OFI-opposes / regime-flip-against) and large adverse drift
# are NEVER gated — genuine breaks still CUT instantly (mandate: broken-RED always
# cuts; the -1.0R rail in the caller is untouched).


def test_immaterial_intraday_drift_within_horizon_does_not_cut() -> None:
    # A 1D thesis, a few minutes in (held << horizon), red by noise (-0.03R), and
    # the ONLY break signal is a sub-floor adverse drift (-0.0012, above the 0.001
    # deadband but below the 0.0015 horizon floor): the measured 94%-churn case →
    # must NOT be classed BROKEN → no CUT.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.03, momentum_drift=-0.0012,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=298, horizon_seconds=2_073_600,  # 24 × 1D bars
    )
    assert m is not ManagementMode.CUT


def test_material_adverse_drift_within_horizon_still_cuts() -> None:
    # A genuine, MATERIAL reversal (-0.6 drift) early in the horizon + red still
    # breaks the thesis → CUT (the floor only rejects noise, never a real break).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.3, momentum_drift=-0.6,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=298, horizon_seconds=2_073_600,
    )
    assert m is ManagementMode.CUT


def test_immaterial_drift_past_horizon_cuts_unchanged() -> None:
    # PAST the horizon the gate lifts — the thesis had its full window, so even a
    # small (but still deadband-clearing) adverse drift + red is treated as the
    # existing BROKEN+CUT (no change to the post-horizon behaviour).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.03, momentum_drift=-0.0012,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=10_000, horizon_seconds=600,
    )
    assert m is ManagementMode.CUT


def test_ofi_opposes_is_never_gated_by_floor() -> None:
    # A corroborated break (OFI firmly opposes) with sub-floor drift, within
    # horizon, red → CUT. OFI/regime are GENUINE breaks → never noise-gated.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=-0.5, flow_confirmed=False, regime="trend", entry_regime="trend",
        held_seconds=298, horizon_seconds=2_073_600,
    )
    assert m is ManagementMode.CUT


def test_regime_flip_against_is_never_gated_by_floor() -> None:
    # A corroborated break (regime flipped to oppose the long) with sub-floor
    # drift, within horizon, red → CUT (regime flip is a genuine break).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=298, horizon_seconds=2_073_600,
    )
    assert m is ManagementMode.CUT


def test_drift_floor_is_env_tunable() -> None:
    # The floor reads POLARIS_EXIT_THESIS_DRIFT_FLOOR at module import.
    from polaris.core.live_recalc.exit_engine import EXIT_THESIS_DRIFT_FLOOR

    assert EXIT_THESIS_DRIFT_FLOOR > 0.0


def test_floor_only_gates_within_horizon_none_horizon_unchanged() -> None:
    # horizon_seconds=None (legacy / unknown strategy) → the gate is INERT (no
    # horizon to be "within"), so behaviour is byte-identical to pre-fix: a
    # deadband-clearing momentum reversal + red → CUT regardless of the floor.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.03, momentum_drift=-0.0012,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=298, horizon_seconds=None,
    )
    assert m is ManagementMode.CUT
