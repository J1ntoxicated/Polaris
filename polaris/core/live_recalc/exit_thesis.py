"""Layer 6 precise-exit — adaptive-thesis assessment + mode->params mapping.

The pure per-position EXIT/MANAGEMENT-TIMING re-map: classify whether the ENTRY
thesis is still INTACT / FADING / BROKEN (``assess_thesis`` + ``_assess_health``),
map a strategy's correlation group to its exit archetype
(``bucket_from_correlation_group``), and resolve a ``ManagementMode`` to concrete
exit-schedule overrides (``mode_to_exit_params``). Pure + total — NEVER blocks an
entry, cuts size, or throttles; it only re-tunes THIS position's exit schedule.
The G6 -1.0R rail + the entry side are owned by the caller.
"""

from __future__ import annotations

import logging
from typing import Final

from polaris.core.live_recalc.exit_params import (
    _BROKEN_STREAK_CONFIRMED,
    EXIT_BAR_MFE_BEP_R,
    EXIT_BAR_MFE_LOCK_R,
    EXIT_BAR_MFE_PROTECT_R,
    EXIT_HARVEST_TRAIL_MULT,
    EXIT_LETRUN_HARVEST_TRAIL_MULT,
    EXIT_LETRUN_TRAIL_MULT,
    EXIT_PEAK_GIVEBACK_DISARM_R,
    EXIT_THESIS_BROKEN_TICKS,
    EXIT_THESIS_DEADBAND,
    EXIT_THESIS_GIVEBACK_ARM_R,
    EXIT_THESIS_GRACE_SEC,
    drift_floor_for_timeframe,
    hold_frac_for_timeframe,
    required_bars_for_horizon,
)
from polaris.core.live_recalc.exit_types import (
    Bucket,
    ManagementMode,
    MfeProtectSchedule,
    ThesisExitParams,
    ThesisGivebackParams,
    ThesisHealth,
)

logger = logging.getLogger(__name__)

# A reversion-class strategy targets a bounded revert-to-mean; its
# correlation_group_id carries one of these substrings. Everything else (momo /
# trend / breakout / event / gap / ema_trend / flow_pressure / burst_rider) is a
# TREND archetype that may run while confirmed.
# "reversion" (forward-fix, [[exit_peak_lock_bind_2026-07-10]]): cci_reversion
# (correlation_group_id="cfd_commodity_reversion") and connors_rsi2
# ("equity_connors_reversion") carry "reversion" without "mean_reversion"/"range"
# — both bounded-revert-to-mean strategies with their own profit_target_r — so
# they fell through to the TREND default and ran the let-winners-run peak-lock
# schedule instead of the bounded-target REVERSION one. "reversion" is a
# superset of "mean_reversion" (kept for history/no functional change there).
_REVERSION_GROUP_SUBSTRINGS: Final[tuple[str, ...]] = (
    "mean_reversion", "range", "reversion",
)


def bucket_from_correlation_group(correlation_group: str | None) -> Bucket:
    """Map a strategy's ``correlation_group_id`` to its exit archetype.

    Pure + total: ``None`` / unknown / "" → TREND (the let-winners-run default —
    flow_not_block: an unmapped group is never forced into a tighter schedule).
    """
    if not correlation_group:
        return Bucket.TREND
    cg = correlation_group.lower()
    for sub in _REVERSION_GROUP_SUBSTRINGS:
        if sub in cg:
            return Bucket.REVERSION
    return Bucket.TREND


# Regime tokens that mean "directionless / range-bound" — a rotation INTO one of
# these (from a directional entry regime) is a schedule REMODE, not an
# invalidation. Substring match keeps it robust to venue-specific labels.
_RANGE_REGIME_SUBSTRINGS: Final[tuple[str, ...]] = ("chop", "range", "mean")
# Directional bias of a regime token, used only to test a flip AGAINST the
# position. Anything not matched is treated as neutral (no directional opinion).
_UP_REGIME_SUBSTRINGS: Final[tuple[str, ...]] = ("up", "bull")
_DOWN_REGIME_SUBSTRINGS: Final[tuple[str, ...]] = ("down", "bear")


def _regime_dir(regime: str | None) -> int:
    """+1 up-biased, -1 down-biased, 0 neutral/range/unknown (pure, total)."""
    if not regime:
        return 0
    r = regime.lower()
    if any(s in r for s in _UP_REGIME_SUBSTRINGS):
        return 1
    if any(s in r for s in _DOWN_REGIME_SUBSTRINGS):
        return -1
    return 0


def _is_range_regime(regime: str | None) -> bool:
    if not regime:
        return False
    r = regime.lower()
    return any(s in r for s in _RANGE_REGIME_SUBSTRINGS)


def _side_sign(side: str | None) -> int:
    """+1 long, -1 short, 0 unknown (degrade safe — no directional test fires)."""
    if side == "long":
        return 1
    if side == "short":
        return -1
    return 0


def _giveback_level(
    *, mfe_r: float, pnl_r: float, giveback: ThesisGivebackParams
) -> int:
    """0 = not armed / no give-back, 1 = soft give-back, 2 = hard give-back.

    Armed only once MFE reached ``arm_r``. Surrendered fraction = how much of the
    reached peak MFE the position has handed back (peak MFE - current favourable
    PnL) / peak MFE. Pure; a non-positive / tiny peak never arms.
    """
    if mfe_r < giveback.arm_r or mfe_r <= 0.0:
        return 0
    surrendered = (mfe_r - max(pnl_r, 0.0)) / mfe_r
    if surrendered > giveback.hard_frac:
        return 2
    if surrendered > giveback.frac:
        return 1
    return 0


def tick_micro_broken(
    *, side: str | None, momentum_drift: float | None, ofi: float | None
) -> bool:
    """Does THIS tick's micro signal oppose the position beyond the deadband?

    Pure + total — the streak-tracking caller calls this each tick to maintain the
    consecutive-broken count fed back as ``broken_streak``. Mirrors the micro-break
    test inside ``_assess_health`` (deadband-gated momentum/OFI reversal); the
    regime flip is structural and intentionally NOT part of the per-tick streak.
    ``None`` inputs degrade to not-broken (no spurious streak).
    """
    sign = _side_sign(side)
    m_drift = 0.0 if momentum_drift is None else momentum_drift
    drift_dir = sign * m_drift
    momentum_reversed = drift_dir < 0.0 and abs(m_drift) > EXIT_THESIS_DEADBAND
    if ofi is None:
        return momentum_reversed
    ofi_dir = sign * ofi
    ofi_opposes = ofi_dir < 0.0 and abs(ofi) > EXIT_THESIS_DEADBAND
    return momentum_reversed or ofi_opposes


def _has_matured(
    *,
    held_seconds: int | None,
    horizon_seconds: int | None,
    timeframe: str | None,
    native_bars_seen: int | None,
    native_bar_interval_seconds: int | None,
    horizon_bars: int | None,
    position_id: str | None,
) -> bool:
    """Has this position developed enough to trust a break judgement?

    Two measures, bars-seen PREFERRED over wall-clock ([[waveB_sizing_params_2026-07-02]]
    agenda 3): when the caller threads ``native_bars_seen`` +
    ``native_bar_interval_seconds`` + ``horizon_bars`` (all three non-None), maturity
    is judged in NATIVE BARS the strategy's OWN timeframe has SEEN since the
    position opened (via ``required_bars_for_horizon``) — wall-clock is NEVER used
    in this path, since a session close / weekend gap would otherwise fake elapsed
    development the thesis never actually lived through. When any of the three is
    ``None`` (a caller that has not migrated to bars-seen yet), this degrades to
    the pre-existing WALL-CLOCK fraction (``held_seconds >= horizon_seconds *
    hold_frac_for_timeframe(timeframe)``) — byte-identical to the prior behaviour
    for that call site. ``horizon_seconds is None`` (legacy/unknown strategy) →
    gate INERT (matured=True) in both paths. Logs a one-line suppression record
    (elapsed/seen-or-na/required) when the gate fires.
    """
    if horizon_seconds is None:
        return True
    bars_seen_path = (
        native_bars_seen is not None
        and native_bar_interval_seconds is not None
        and horizon_bars is not None
    )
    if bars_seen_path:
        assert native_bars_seen is not None
        assert native_bar_interval_seconds is not None
        assert horizon_bars is not None
        required = required_bars_for_horizon(
            horizon_seconds=horizon_seconds,
            native_bar_interval_seconds=native_bar_interval_seconds,
            horizon_bars=horizon_bars,
        )
        matured = native_bars_seen >= required
        seen_desc = str(native_bars_seen)
    else:
        if held_seconds is None:
            return True
        required = 0  # wall-clock path has no bar count; logged as n/a below
        matured = held_seconds >= horizon_seconds * hold_frac_for_timeframe(timeframe)
        seen_desc = "n/a"
    if not matured:
        logger.info(
            "[L6/exit] maturity-gate suppressed break pos=%s elapsed=%ss "
            "seen=%s required=%s",
            position_id or "?", held_seconds, seen_desc,
            required if bars_seen_path else "n/a",
        )
    return matured


def _assess_health(
    *,
    sign: int,
    bucket: Bucket,
    mfe_r: float,
    pnl_r: float,
    momentum_drift: float,
    atr_slope: float,
    ofi: float | None,
    flow_confirmed: bool | None,
    regime: str | None,
    entry_regime: str | None,
    broken_streak: int,
    held_seconds: int | None,
    horizon_seconds: int | None,
    timeframe: str | None,
    native_bars_seen: int | None,
    native_bar_interval_seconds: int | None,
    horizon_bars: int | None,
    position_id: str | None,
) -> ThesisHealth:
    """Classify the entry thesis as INTACT / FADING / BROKEN (pure, total).

    BROKEN: momentum reversed against the position, OR OFI firmly opposes, OR the
    regime flipped to the OPPOSITE directional bias of the entry regime — but ONLY
    when the adverse signal is SUSTAINED: its magnitude exceeds ``DEADBAND`` (1-tick
    noise inside the band never breaks) AND the consecutive-broken ``broken_streak``
    has reached ``EXIT_THESIS_BROKEN_TICKS`` (a single noisy tick never flips a
    fresh winner). A regime FLIP is structural, not tick noise → it bypasses the
    streak/deadband (a confirmed opposite-bias regime is a real break). Both OFI
    and regime-flip breaks additionally require the hold-time floor below.
    FADING: the position was green (mfe armed) but momentum flattened / OFI decayed
    — the edge is leaking.
    INTACT: same-direction momentum / OFI still present (or simply nothing
    indicating a break).

    Horizon-scoped materiality floor ([[1d_exit_horizon_fix_2026-06-26]],
    timeframe-scaled 2026-07-02): while the position is still WITHIN its strategy
    horizon (``held < horizon``), the momentum-ONLY reversal must additionally be
    MATERIAL (``|momentum_drift| >= drift_floor_for_timeframe(timeframe)``) to
    count as BROKEN — a sub-floor adverse drift is intraday NOISE for a
    long-horizon thesis and must not fake a daily-thesis break. The floor SCALES
    with ``timeframe`` (a 1D thesis tolerates the 1D noise band, not the 1m one);
    an unregistered/``None`` timeframe falls back to the proven 1m floor
    (conservative). A None horizon (legacy / unknown strategy) leaves the gate
    inert → byte-identical to pre-fix.

    Maturity gate — BOTH break flavours ([[trade_mess_full_audit_2026-07-02_fixplan]]
    P0-2 + verification audit P0-1; per-timeframe hold_frac + bars-seen counting
    [[waveB_sizing_params_2026-07-02]] agenda 3): below the maturity floor (see
    ``_has_matured`` — bars-seen when threaded, else the pre-existing wall-clock
    fraction of ``horizon_seconds``), NEITHER a corroborated break (OFI-opposes /
    regime-flip-against, live: 1D thesis_cut at 0.7min) NOR a momentum-only
    material drift may flip the thesis to BROKEN — a fresh long-horizon thesis
    gets real development time first (it still falls through to the FADING /
    INTACT tests below). Past that floor — or with no horizon — both flavours
    flip BROKEN exactly as before. The -1.0R hard rail / ATR trail / G6 crisis
    exits are a SEPARATE layer and are never gated by any of this.
    """
    drift_dir = sign * momentum_drift  # >0 = momentum WITH the position
    ofi_dir = None if ofi is None else sign * ofi  # >0 = flow WITH the position

    # --- adverse micro-signal (deadband-gated): only count an opposition whose
    #     magnitude clears the small deadband — a tiny 1-tick oscillation inside
    #     the band is noise, not a thesis break.
    # HORIZON-scoped materiality floor: while still within the strategy horizon, a
    # momentum-ONLY reversal must ALSO clear the (timeframe-scaled) DRIFT floor so
    # intraday noise cannot fake a break on a long-horizon thesis. Past horizon —
    # or with no horizon — the floor is inert (the thesis had its full window).
    # OFI / regime breaks below are unaffected.
    within_horizon = (
        held_seconds is not None
        and horizon_seconds is not None
        and held_seconds < horizon_seconds
    )
    drift_floor = drift_floor_for_timeframe(timeframe)
    drift_is_material = not within_horizon or abs(momentum_drift) >= drift_floor
    # MATURITY gate ([[waveB_sizing_params_2026-07-02]] agenda 3): within horizon,
    # BOTH break flavours additionally require the position to have matured — see
    # ``_has_matured`` (bars-seen when threaded, else the pre-existing wall-clock
    # fraction). Computed ONCE and shared by the momentum-only and corroborated
    # paths below (both flavours are gated uniformly).
    has_matured = _has_matured(
        held_seconds=held_seconds, horizon_seconds=horizon_seconds,
        timeframe=timeframe, native_bars_seen=native_bars_seen,
        native_bar_interval_seconds=native_bar_interval_seconds,
        horizon_bars=horizon_bars, position_id=position_id,
    )
    drift_is_mature = not within_horizon or has_matured
    momentum_reversed = (
        drift_dir < 0.0
        and abs(momentum_drift) > EXIT_THESIS_DEADBAND
        and drift_is_material
        and drift_is_mature
    )

    # CORROBORATED-break maturity gate (audit P0-2, bars-seen 2026-07-02 agenda 3):
    # OFI-opposes / regime-flip are genuine breaks (unlike momentum-only, which
    # already has its own ``drift_is_material`` horizon gate above), but each
    # still needs the SAME maturity floor as momentum-only before it can flip
    # BROKEN — else a 1D/1H thesis is executed off a single 1m-tick wobble the
    # instant it opens. Past the floor — or no horizon (legacy) — corroborated
    # breaks fire instantly, unchanged.
    corroborated_break_matured = not within_horizon or has_matured
    ofi_opposes = (
        ofi_dir is not None
        and ofi_dir < 0.0
        and abs(ofi_dir) > EXIT_THESIS_DEADBAND
        and corroborated_break_matured
    )
    # SUSTAINED confirmation: a micro break (momentum/OFI) only counts once the
    # consecutive-broken streak has reached the floor — 1-tick noise never breaks.
    micro_broken = (momentum_reversed or ofi_opposes) and (
        broken_streak >= EXIT_THESIS_BROKEN_TICKS
    )

    # --- regime flip (structural — bypasses the micro streak/deadband, still
    #     gated by the corroborated-break hold-time floor above) ---
    entry_dir = _regime_dir(entry_regime)
    now_dir = _regime_dir(regime)
    regime_flipped_against = (
        entry_dir != 0
        and now_dir != 0
        and now_dir == -entry_dir
        and sign * now_dir < 0  # the new regime opposes the position
        and corroborated_break_matured
    )
    if micro_broken or regime_flipped_against:
        return ThesisHealth.BROKEN

    # --- FADING tests (only meaningful once the position printed MFE) ---
    if mfe_r >= EXIT_THESIS_GIVEBACK_ARM_R:
        momentum_flat = abs(momentum_drift) <= 1e-9 or drift_dir <= 0.0
        ofi_decayed = ofi_dir is not None and ofi_dir <= 0.0
        flow_lost = flow_confirmed is False
        if (momentum_flat and (ofi_decayed or flow_lost)) or (
            ofi_decayed and flow_lost
        ):
            return ThesisHealth.FADING

    return ThesisHealth.INTACT


def assess_thesis(
    *,
    side: str | None,
    bucket: Bucket,
    mfe_r: float | None,
    mae_r: float | None,
    pnl_r: float | None,
    momentum_drift: float | None,
    atr_slope: float | None,
    ofi: float | None,
    flow_confirmed: bool | None,
    regime: str | None,
    entry_regime: str | None,
    held_seconds: int | None,
    horizon_seconds: int | None,
    giveback: ThesisGivebackParams,
    broken_streak: int = _BROKEN_STREAK_CONFIRMED,
    timeframe: str | None = None,
    native_bars_seen: int | None = None,
    native_bar_interval_seconds: int | None = None,
    horizon_bars: int | None = None,
    position_id: str | None = None,
) -> ManagementMode:
    """Re-map THIS position's exit schedule from entry-thesis health (pure/total).

    NEVER raises; ``None`` inputs degrade safe (treated as 0.0 / neutral). EXIT /
    MANAGEMENT-TIMING only — it returns a ``ManagementMode`` the FSM converts to
    exit-param overrides; it touches NO entry, NO size, NO halt. The G6 -1.0R rail
    is owned by the caller.

    GRACE ([[exit_thesis_grace_2026-06-23]]): a position INSIDE the grace window
    (``held_seconds`` ≤ ``EXIT_THESIS_GRACE_SEC``) can NOT be CUT or thesis-HARVESTed
    — a momentary BROKEN/give-back read is suppressed to a non-closing decision
    (HOLD, or LET_RUN for a confirmed green trend winner) so a fresh thesis is let
    to ESTABLISH before judging it. This kills the live 0-1s instant cut. None /
    unknown ``held_seconds`` degrades to inside-grace (safest — never an instant cut
    on an unknown age). ``broken_streak`` (consecutive prior BROKEN reads) feeds the
    SUSTAINED gate so 1-tick OFI noise never flips a fresh winner to BROKEN; the
    default treats a non-threading caller as already confirmed (back-compatible).
    ``timeframe`` (default ``None``) scales the horizon drift-materiality floor
    ([[1d_exit_horizon_fix_2026-07-02]]) — ``None``/unregistered → the proven 1m
    floor (byte-identical to every existing caller that doesn't thread it).

    ``native_bars_seen`` / ``native_bar_interval_seconds`` / ``horizon_bars``
    ([[waveB_sizing_params_2026-07-02]] agenda 3, default ``None``): the maturity
    gate's bars-seen measure — see ``_has_matured``. All three default ``None`` →
    the gate falls back to the pre-existing WALL-CLOCK maturity fraction
    (byte-identical for every caller that has not migrated to bars-seen yet).

    Precedence (highest first): give-back-hard → CUT(broken + red) → HARVEST
    (give-back / fading / broken-green) → REMODE → LET_RUN(trend intact) → HOLD.
    """
    sign = _side_sign(side)
    m_mfe = 0.0 if mfe_r is None else mfe_r
    m_pnl = 0.0 if pnl_r is None else pnl_r
    m_drift = 0.0 if momentum_drift is None else momentum_drift
    m_slope = 0.0 if atr_slope is None else atr_slope

    is_red = m_pnl < 0.0
    # GRACE: a position that has not yet aged past the grace floor cannot be
    # CLOSED by the thesis re-map (CUT / thesis-HARVEST). None held_seconds →
    # inside grace (safest). The closing modes are remapped to non-closing below.
    in_grace = held_seconds is None or held_seconds <= EXIT_THESIS_GRACE_SEC

    # 1. Give-back modifier (orthogonal, highest precedence). A position that
    #    reached MFE and handed back too much of it is HARVESTED regardless of
    #    thesis health — but give-back NEVER escalates a green position to CUT.
    #    Inside grace it is SUPPRESSED (no fast thesis_harvest at 0-1s).
    gb = _giveback_level(mfe_r=m_mfe, pnl_r=m_pnl, giveback=giveback)
    if gb >= 1 and not in_grace:
        return ManagementMode.HARVEST

    health = _assess_health(
        sign=sign, bucket=bucket, mfe_r=m_mfe, pnl_r=m_pnl,
        momentum_drift=m_drift, atr_slope=m_slope, ofi=ofi,
        flow_confirmed=flow_confirmed, regime=regime, entry_regime=entry_regime,
        broken_streak=broken_streak,
        held_seconds=held_seconds, horizon_seconds=horizon_seconds,
        timeframe=timeframe,
        native_bars_seen=native_bars_seen,
        native_bar_interval_seconds=native_bar_interval_seconds,
        horizon_bars=horizon_bars,
        position_id=position_id,
    )

    # GRACE gate on the CLOSING health verdicts: inside grace, a BROKEN or FADING
    # read does NOT close the fresh position. A confirmed green trend winner may
    # still LET_RUN (non-closing); everything else HOLDs. flow_not_block — let the
    # thesis establish; the aged path below still CUTs / HARVESTs.
    if in_grace:
        if (
            bucket is Bucket.TREND
            and health is not ThesisHealth.BROKEN
            and m_mfe > 0.0
            and not is_red
        ):
            return ManagementMode.LET_RUN
        return ManagementMode.HOLD

    # 2. BROKEN → CUT only when red/flat; a broken-GREEN position is HARVESTED
    #    (bank the gain — never CUT a winner).
    if health is ThesisHealth.BROKEN:
        return ManagementMode.CUT if is_red else ManagementMode.HARVEST

    # 3. FADING → HARVEST (bank the leaking edge).
    if health is ThesisHealth.FADING:
        return ManagementMode.HARVEST

    # 4. REMODE: the regime rotated INTO a range/chop regime from a NON-range
    #    (trend / directional) entry regime WITHOUT invalidating the position —
    #    swap the exit schedule (trend<->range) instead of widening. Not a
    #    strategy swap. (A broken/red position already CUT/HARVESTED above.)
    if (
        _is_range_regime(regime)
        and bool(entry_regime)
        and not _is_range_regime(entry_regime)
        and not is_red
    ):
        return ManagementMode.REMODE

    # 5. INTACT trend → LET_RUN (widen so the confirmed winner runs). A reversion
    #    thesis intact does NOT widen — its edge is bounded → HOLD.
    if bucket is Bucket.TREND and m_mfe > 0.0 and not is_red:
        return ManagementMode.LET_RUN

    return ManagementMode.HOLD


def mode_to_exit_params(
    mode: ManagementMode,
    *,
    bucket: Bucket,
    mfe_r: float,
    pnl_r: float,
    giveback: ThesisGivebackParams,
    base_mfe_protect: MfeProtectSchedule | None,
    base_profit_target_r: float | None,
) -> ThesisExitParams:
    """Map a ManagementMode to concrete exit-schedule overrides (pure, total).

    LET_RUN widens the trail to ``EXIT_LETRUN_TRAIL_MULT`` + a BEP floor; HARVEST
    installs a tight BEP+lock floor at the reached rung and fast-closes
    (thesis_harvest) when the give-back is HARD; CUT requests an immediate
    thesis_cut close; REMODE swaps the trend<->range schedule (a reversion target
    for a now-range position, else a tighter harvest floor); HOLD keeps the
    caller's defaults. EXIT params only — never size / entry / halt.
    """
    if mode is ManagementMode.HOLD:
        return ThesisExitParams(
            mfe_protect=base_mfe_protect, profit_target_r=base_profit_target_r
        )

    # Hardening #12 (2026-06-23): the non-HOLD re-map modes synthesise the BAR
    # MFE-protect floor only when the caller did NOT supply one. A caller-supplied
    # ``base_mfe_protect`` (e.g. a venue-/timeframe-correct equity schedule) is
    # PRESERVED so the thesis engine engaging cannot silently clobber it. Latent
    # today (EXIT_EQUITY_MFE_* == EXIT_BAR_MFE_* and no override → zero delta),
    # but a future POLARIS_EXIT_EQUITY_MFE_* override would otherwise be lost.
    # Give-back protect direction (winner protection) is unchanged.
    bar_mfe_protect = MfeProtectSchedule(
        bep_at_r=EXIT_BAR_MFE_BEP_R,
        protect_at_r=EXIT_BAR_MFE_PROTECT_R,
        lock_r=EXIT_BAR_MFE_LOCK_R,
    )

    if mode is ManagementMode.LET_RUN:
        return ThesisExitParams(
            trail_mult=EXIT_LETRUN_TRAIL_MULT,
            mfe_protect=base_mfe_protect or bar_mfe_protect,
            profit_target_r=base_profit_target_r,
        )

    if mode is ManagementMode.HARVEST:
        gb = _giveback_level(mfe_r=mfe_r, pnl_r=pnl_r, giveback=giveback)
        # Let-winners-run gate ([[ab_letrun_maker_2026-06-24]]): when the caller's
        # schedule carries a PEAK-FRACTION arm AND this winner has reached it, the
        # peak-fraction floor (in the FSM) already ratchets the stop toward profit
        # at peak% — so a SOFT give-back must NOT collapse the trail to the 1-ATR
        # EXIT_HARVEST_TRAIL_MULT (that is the flat-line bug). The WIDE
        # let-run-harvest trail is held and the floor supplies the lock.
        #
        # #47 RUNNER DISARM ([[exit_recalib_2026-06-26]]): the HARD give-back
        # backstop (gb>=2 → thesis_harvest) stays armed for the COMMON small peak
        # (< EXIT_PEAK_GIVEBACK_DISARM_R, +1.0R) — that small winner round-trips
        # otherwise. But once the REACHED peak clears +1.0R this is the RARE runner:
        # the give-back force-close is DISARMED so it rides the floor + wide ATR
        # trail (ASYMMETRY = small peak banks / rare runner runs). The floor already
        # locks ~peak% so the runner is protected, NOT naked; the protected-BEP /
        # loser-timeout / -1.0R rail still own the loss side. A schedule WITHOUT a
        # peak-arm keeps the original tighten.
        eff = base_mfe_protect or bar_mfe_protect
        peak_armed = (
            eff is not None
            and eff.peak_lock_arm_r > 0.0
            and mfe_r >= eff.peak_lock_arm_r
        )
        return ThesisExitParams(
            trail_mult=(
                EXIT_LETRUN_HARVEST_TRAIL_MULT
                if peak_armed
                else EXIT_HARVEST_TRAIL_MULT
            ),
            mfe_protect=eff,
            profit_target_r=base_profit_target_r,
            thesis_harvest=gb >= 2 and mfe_r < EXIT_PEAK_GIVEBACK_DISARM_R,
        )

    if mode is ManagementMode.CUT:
        return ThesisExitParams(thesis_cut=True)

    # REMODE: swap the trend<->range exit schedule. A now-range position takes a
    # bounded revert-to-mean target (if the strategy already declares one) plus
    # the tighter harvest trail; otherwise it tightens to the harvest floor. NOT a
    # strategy swap — exit params only.
    return ThesisExitParams(
        trail_mult=EXIT_HARVEST_TRAIL_MULT,
        mfe_protect=base_mfe_protect or bar_mfe_protect,
        profit_target_r=base_profit_target_r,
    )
