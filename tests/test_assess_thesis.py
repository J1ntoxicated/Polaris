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
    # A genuine, MATERIAL reversal (-0.6 drift) PAST the maturity floor (5% of
    # the 2_073_600s horizon = 103_680s) + red still breaks the thesis → CUT
    # (the materiality floor only rejects noise, never a real break; maturity
    # only delays the momentum-only cut, never suppresses a mature one).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.3, momentum_drift=-0.6,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=110_000, horizon_seconds=2_073_600,
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


# REVERSED 2026-07-02 (audit P0-2 (2), [[trade_mess_full_audit_2026-07-02_fixplan]]):
# these two tests used to assert the corroborated-break path (OFI-opposes /
# regime-flip-against) was NEVER gated by ANY hold-time floor — that "never
# gated" was itself the pathology the audit flagged: it let a single 1m-tick
# OFI wobble or regime blip instantly CUT a 1D/1H thesis with ZERO development
# time. LIVE EVIDENCE (2026-07-02 00:26): index_dual_momentum_rotation (1D,
# horizon ≈21 bars) opened and was thesis_cut at 0.7min and 2.6min held — a
# daily-rotation thesis killed by intraday noise before it could develop.
# The fix adds a corroborated-break hold-time floor proportional to the
# strategy's own timeframe (``EXIT_THESIS_BREAK_HOLD_FRAC × horizon_seconds``):
# a FRESH 1D-horizon position (held << floor) can no longer be CUT by OFI/regime
# alone; the same signal past the floor still CUTs instantly (asserted below).
def test_ofi_opposes_fresh_1d_position_below_floor_is_not_cut() -> None:
    # 1D thesis (horizon ≈21 bars ≈ 1,814,400s), held 298s — far below the 5%
    # hold-time floor (~90,720s) — OFI alone must NOT cut a fresh thesis.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=-0.5, flow_confirmed=False, regime="trend", entry_regime="trend",
        held_seconds=298, horizon_seconds=1_814_400,
    )
    assert m is not ManagementMode.CUT


def test_ofi_opposes_past_floor_still_cuts() -> None:
    # Same 1D thesis, held PAST the hold-time floor (~90,720s) — the
    # corroborated OFI break is genuine and still CUTs (unchanged behaviour).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=-0.5, flow_confirmed=False, regime="trend", entry_regime="trend",
        held_seconds=100_000, horizon_seconds=1_814_400,
    )
    assert m is ManagementMode.CUT


def test_regime_flip_against_fresh_1d_position_below_floor_is_not_cut() -> None:
    # Same 1D horizon, held 298s (far below the hold-time floor) — a regime
    # flip alone must NOT cut a fresh thesis either.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=298, horizon_seconds=1_814_400,
    )
    assert m is not ManagementMode.CUT


def test_regime_flip_against_past_floor_still_cuts() -> None:
    # Held PAST the hold-time floor — a confirmed opposite-bias regime flip is
    # a genuine break and still CUTs (unchanged behaviour).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=None, regime="downtrend", entry_regime="uptrend",
        held_seconds=100_000, horizon_seconds=1_814_400,
    )
    assert m is ManagementMode.CUT


def test_ofi_opposes_1m_strategy_cuts_immediately() -> None:
    # A 1m-timeframe strategy's hold-time floor is <1min (5% of a ~900s
    # horizon ≈ 45s) — practically unchanged: even a fresh (held=60s) position
    # clears the floor and OFI-opposes still CUTs immediately, same as before
    # the fix (1m/5m scalps keep their existing behaviour).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.2, momentum_drift=-0.0012,
        ofi=-0.5, flow_confirmed=False, regime="trend", entry_regime="trend",
        held_seconds=60, horizon_seconds=900,
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


# --- P0-2 (4): timeframe-scaled drift floor + uncorroborated-break maturity --
# [[1d_exit_horizon_fix_2026-07-02]]. Wave A scoped the drift-measurement bar to
# the strategy's OWN timeframe, but the flat 0.0015 floor stayed calibrated on
# the OLD 1m/10-min window — a 1D bar-to-bar span routinely drifts several
# PERCENT, so the flat floor passed through unconditionally and a 1D thesis was
# cut on its FIRST recalc cycle (LIVE 2026-07-02: index_dual_momentum_rotation
# J225/AU200AU thesis_cut at 48s hold, momentum-drift path — the
# corroborated-break gate never engaged since this was an UNCORROBORATED
# momentum-only break). Two independent fixes:
#   (1) the materiality floor SCALES per ``timeframe`` (1D positions are judged
#       against the 1D noise band, not the 1m one);
#   (2) an uncorroborated (momentum-only) BROKEN read additionally requires the
#       position to have aged past ``EXIT_THESIS_BREAK_HOLD_FRAC`` (5%) of its
#       horizon — however large the drift, a fresh long-horizon thesis gets real
#       development time. CORROBORATED breaks (OFI / regime-flip) bypass BOTH
#       gates — always real, cross-signal-confirmed, never noise.
# The -1.0R hard rail / ATR trail / G6 crisis path are OWNED by the caller and
# are UNTOUCHED by either gate — layer separation proven by
# ``test_maturity_gate_never_touches_the_pnl_rail_layer`` below.


def test_1d_typical_bar_drift_does_not_cut_below_materiality_floor() -> None:
    # A "typical" 1D bar-to-bar drift (~1.3%, well inside the measured 1D noise
    # band) on a freshly-filled 1D position must NOT be material under the
    # timeframe-scaled floor — pre-fix (flat 0.0015 = 0.15%) this would CUT.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.02, momentum_drift=-0.013,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=48, horizon_seconds=21 * 86400, timeframe="1D",
    )
    assert m is not ManagementMode.CUT


def test_1d_genuinely_material_drift_still_cuts_once_mature() -> None:
    # A LARGE 1D drift (-15%, clears even the scaled 1D floor) AFTER the position
    # has aged past the 5%-of-horizon maturity floor still breaks the thesis.
    horizon = 21 * 86400
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.3, momentum_drift=-0.15,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=int(horizon * 0.06), horizon_seconds=horizon, timeframe="1D",
    )
    assert m is ManagementMode.CUT


def test_1d_large_drift_suppressed_before_maturity_floor() -> None:
    # The SAME large -15% drift, but the position is still fresh (48s held, far
    # below the 5%-of-horizon maturity floor ~90,720s) — the momentum-ONLY break
    # must be suppressed regardless of drift magnitude (mandate: "drift가 아무리
    # 커도 발달시간 전엔 컷 불가").
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.3, momentum_drift=-0.15,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=48, horizon_seconds=21 * 86400, timeframe="1D",
    )
    assert m is not ManagementMode.CUT


# NOTE(merge 2026-07-02): two branch-local tests asserting the maturity gate
# does NOT apply to corroborated breaks were deleted here — they contradicted
# the audit SSOT ([[trade_mess_full_audit_2026-07-02_fixplan]] P0-2 ② +
# verification-audit scorecard ⑩ CONFIRMED) and the four passing equivalents
# above (test_ofi_opposes_fresh_1d_position_below_floor_is_not_cut /
# test_regime_flip_against_fresh_1d_position_below_floor_is_not_cut +
# their past-floor counterparts). Corroborated breaks ARE maturity-gated;
# genuine losses are still cut by the separate -1.0R / trail / G6 crisis layer.


def test_1m_timeframe_floor_is_byte_identical_to_pre_fix_constant() -> None:
    # timeframe="1m" (ratio 1.0 by construction) resolves to EXACTLY the
    # pre-existing flat EXIT_THESIS_DRIFT_FLOOR — the scaled floor never loosens
    # or tightens the proven 1m/tick-engine calibration.
    from polaris.core.live_recalc.exit_engine import (
        EXIT_THESIS_DRIFT_FLOOR,
        drift_floor_for_timeframe,
    )

    assert drift_floor_for_timeframe("1m") == EXIT_THESIS_DRIFT_FLOOR


def test_no_timeframe_falls_back_to_1m_floor_conservative() -> None:
    # An unregistered/None timeframe (legacy caller, tick-engine synthetic
    # signal) falls back to the unscaled 1m floor — never LOOSER than pre-fix.
    from polaris.core.live_recalc.exit_engine import (
        EXIT_THESIS_DRIFT_FLOOR,
        drift_floor_for_timeframe,
    )

    assert drift_floor_for_timeframe(None) == EXIT_THESIS_DRIFT_FLOOR
    assert drift_floor_for_timeframe("unknown_tf") == EXIT_THESIS_DRIFT_FLOOR


def test_1m_strategy_grace_behaviour_unchanged_by_maturity_gate() -> None:
    # A 1m/tick-scale thesis (horizon_seconds=600, the tick-engine's typical
    # 10-min window) with a genuine deadband-clearing drift break PAST both the
    # flat GRACE (25s) and the tiny 5%-of-600s=30s maturity floor still CUTs —
    # the maturity gate is proportionally tiny at 1m scale and does not block
    # the existing fast-scalp exit responsiveness.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.03, momentum_drift=-0.6,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=60, horizon_seconds=600, timeframe="1m",
    )
    assert m is ManagementMode.CUT


def test_maturity_gate_never_touches_the_pnl_rail_layer() -> None:
    # LAYER SEPARATION proof: assess_thesis (the maturity/materiality gate) is
    # EXIT-TIMING only and knows nothing about the G6 -1.0R hard rail — a deeply
    # red (-1.0R) fresh 1D position with ONLY a sub-floor immaterial drift as the
    # break signal returns a non-CUT thesis mode (the rail is a SEPARATE
    # caller-owned layer that still fires on pnl_r <= -1.0R regardless of this
    # mode). This test asserts the re-map's OWN scope: it does not escalate to
    # CUT off pnl_r alone without a genuine break signal.
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-1.0, momentum_drift=-0.0005,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=48, horizon_seconds=21 * 86400, timeframe="1D",
    )
    assert m is not ManagementMode.CUT


# --- Live incident fixture: J225/AU200AU 48s thesis_cut (2026-07-02) ---------


def test_j225_live_incident_fixture_no_longer_cuts_at_48s() -> None:
    # Reproduces the LIVE regression: index_dual_momentum_rotation (1D,
    # horizon 21 bars), J225/AU200AU. fill 02:52:20 UTC -> first recalc cycle
    # 02:52:28 -> thesis_cut 02:52:29 (held ~9-48s per the live log trace). The
    # ONLY break signal was the bar-recalc momentum_drift (no OFI on the bar
    # path -> None; no regime flip logged) — an UNCORROBORATED momentum-only
    # break on a position that had not even completed ONE full bar of its 1D
    # horizon. With the timeframe-scaled floor + maturity gate this scenario
    # must HOLD, not CUT.
    horizon_seconds = 21 * 86400  # index_dual_momentum_rotation expected_holding_bars
    held_seconds = 48
    # A representative adverse 1D drift measured over the bar-recalc window
    # (~1.5% — well inside the measured 1D noise band, well below the ~11.2%
    # scaled 1D materiality floor).
    m = _assess(
        bucket=Bucket.TREND, mfe_r=0.0, pnl_r=-0.05, momentum_drift=-0.015,
        ofi=None, flow_confirmed=None, regime="trend", entry_regime="trend",
        held_seconds=held_seconds, horizon_seconds=horizon_seconds,
        timeframe="1D",
    )
    assert m is not ManagementMode.CUT
