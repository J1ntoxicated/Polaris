"""Layer 6 precise-exit — env helpers + trading-parameter constants.

Shared configuration primitives for the precise-exit engine: the env-override
helpers, the FSM state labels + rank, and every env-tunable trading parameter
(ATR trail / FSM rungs / loser-timeout / MFE-protect schedules / adaptive-thesis
thresholds / grace gate). Pure module-level constants — no I/O at call time.

See ``exit_engine`` for the orchestrator docstring describing each knob's role.
"""

from __future__ import annotations

import math
import os
from typing import Final

# --- Exit-state FSM labels (TEXT in positions.exit_state) ------------------
EXIT_STATE_OPEN: Final[str] = "open"
EXIT_STATE_TOUCHED: Final[str] = "touched"
EXIT_STATE_PROTECTED: Final[str] = "protected"
EXIT_STATE_HARVEST: Final[str] = "harvest"

# FSM order so we never regress a state (max-MFE is monotone).
_STATE_RANK: Final[dict[str, int]] = {
    EXIT_STATE_OPEN: 0,
    EXIT_STATE_TOUCHED: 1,
    EXIT_STATE_PROTECTED: 2,
    EXIT_STATE_HARVEST: 3,
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# --- Trading parameters (CONSERVATIVE defaults — FLAG pending /debate) ------
EXIT_ATR_TRAIL_MULT: Final[float] = _env_float("POLARIS_EXIT_ATR_TRAIL_MULT", 2.0)
EXIT_HARVEST_TRAIL_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_HARVEST_TRAIL_MULT", 1.0
)
EXIT_FSM_TOUCH_R: Final[float] = _env_float("POLARIS_EXIT_FSM_TOUCH_R", 0.5)
EXIT_FSM_PROTECT_R: Final[float] = _env_float("POLARIS_EXIT_FSM_PROTECT_R", 1.0)
EXIT_FSM_HARVEST_R: Final[float] = _env_float("POLARIS_EXIT_FSM_HARVEST_R", 2.0)
EXIT_LOSER_TIMEOUT_SEC: Final[float] = _env_float(
    "POLARIS_EXIT_LOSER_TIMEOUT_SEC", 900.0
)
EXIT_LOSER_TIMEOUT_EXT_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_LOSER_TIMEOUT_EXT_MULT", 2.0
)

# --- 1D equity MFE-protect schedule ([[equity_exit_harvest_2026-06-23]]) ------
# The 1D bar-pipeline equity path (equity_tsmom / equity_gap_go / ...) called
# run_precise_exit WITHOUT a schedule, so the only protective floor below
# EXIT_FSM_PROTECT_R=1.0R was the wide 2-ATR trail. MEASURED: equity entries are
# DIRECTIONALLY RIGHT (22/28 closed trades had positive mfe_r, avg +0.27R, peak
# +0.65R) yet round-tripped to losses (0% win) purely because no floor armed
# below 1.0R — equity MFE never reaches the FSM PROTECT rung. This schedule is
# calibrated to that measured distribution (avg ~0.27R, p75 ~0.4R): BEP floor at
# +0.30R, lock +0.20R once +0.45R is touched. EXPECTANCY, not a throttle — it
# only ratchets the stop toward profit (size / entry side / the G6 -1.0R rail
# untouched). Env-tunable; equity-only (None for every other asset_class).
EXIT_EQUITY_MFE_BEP_R: Final[float] = _env_float("POLARIS_EXIT_EQUITY_MFE_BEP_R", 0.30)
EXIT_EQUITY_MFE_PROTECT_R: Final[float] = _env_float(
    "POLARIS_EXIT_EQUITY_MFE_PROTECT_R", 0.45
)
EXIT_EQUITY_MFE_LOCK_R: Final[float] = _env_float(
    "POLARIS_EXIT_EQUITY_MFE_LOCK_R", 0.20
)

# --- generalized bar-strategy MFE-protect schedule ([[harvest_generalization]]) -
# The equity harvest above was wired ONLY to asset_class=='equity'; the 9 OTHER
# bar strategies (spot / fx / index / commodity) passed mfe_protect=None → below
# EXIT_FSM_PROTECT_R=1.0R the only floor was the wide 2-ATR trail. MEASURED on the
# live ledger: avg MFE +0.278R, realized -0.947R, giveback 1.225R; 29.2% of trades
# reach +0.30R and 19.7% reach +0.45R yet round-trip. Only 6.5% ever reach +1.0R
# (the dead PROTECT rung). This DEFAULT schedule — the SAME proven calibration as
# equity (BEP +0.30R where ~30% reach, lock +0.20R once +0.45R is touched) — is
# now applied to EVERY bar asset_class so those positive-MFE round-trips become
# break-even-or-better exits. EXPECTANCY, not a throttle: it only ratchets the
# stop toward profit (the let-winners-run ATR trail still runs ABOVE the floor; the
# size / entry side / the G6 -1.0R rail are untouched). Env-tunable.
EXIT_BAR_MFE_BEP_R: Final[float] = _env_float("POLARIS_EXIT_BAR_MFE_BEP_R", 0.30)
EXIT_BAR_MFE_PROTECT_R: Final[float] = _env_float(
    "POLARIS_EXIT_BAR_MFE_PROTECT_R", 0.45
)
EXIT_BAR_MFE_LOCK_R: Final[float] = _env_float("POLARIS_EXIT_BAR_MFE_LOCK_R", 0.20)

# --- Adaptive thesis re-map ([[adaptive_thesis_remap_2026-06-23]]) ------------
# A per-position EXIT/MANAGEMENT-TIMING re-map driven by whether the ENTRY THESIS
# is still healthy (same-direction momentum / OFI + regime unchanged), fading, or
# broken. It NEVER blocks an entry, cuts size, or throttles — it only re-tunes
# THIS position's exit schedule: LET_RUN widens the trail so a confirmed winner
# runs (aggressive); HARVEST tightens / banks near the reached peak; CUT closes an
# INVALIDATED (broken + red) position now; REMODE swaps the trend<->range exit
# schedule when the regime rotated without invalidating the position. The G6 -1.0R
# rail + the entry side are untouched. All thresholds env-tunable; the whole
# re-map is gated behind POLARIS_EXIT_ADAPTIVE_THESIS (default ON).
#
#   * ``EXIT_THESIS_GIVEBACK_ARM_R`` (default 0.30): MFE in R the position must
#     have reached before the give-back modifier can arm.
#   * ``EXIT_THESIS_GIVEBACK_FRAC`` (default 0.50): fraction of the reached peak
#     MFE surrendered above which HARVEST is forced (orthogonal to thesis health).
#   * ``EXIT_THESIS_GIVEBACK_HARD_FRAC`` (default 0.60): fraction surrendered above
#     which the position is HARVESTED IMMEDIATELY (thesis_harvest fast-close).
#   * ``EXIT_LETRUN_TRAIL_MULT`` (default 4.0): the WIDE let-winners-run ATR trail
#     LET_RUN installs (vs the module-default 2.0) so a confirmed thesis runs.
EXIT_THESIS_GIVEBACK_ARM_R: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_GIVEBACK_ARM_R", 0.30
)
EXIT_THESIS_GIVEBACK_FRAC: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_GIVEBACK_FRAC", 0.50
)
EXIT_THESIS_GIVEBACK_HARD_FRAC: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_GIVEBACK_HARD_FRAC", 0.75
)
EXIT_LETRUN_TRAIL_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_LETRUN_TRAIL_MULT", 4.0
)

# --- let-winners-run peak-fraction floor ([[ab_letrun_maker_2026-06-24]]) ------
# Diagnosis: burst_rider peaked +7.33R but realised +0.137R — the small fixed
# lock (+0.25R) + 60% give-back force-close + the 1-ATR HARVEST trail flat-lined
# the winner at break-even. The peak-fraction floor RATCHETS the protective stop
# to a FRACTION of the REACHED peak MFE once an arm rung is passed
# (``entry ± peak_mfe_r * frac * atr_r``) so a confirmed big winner is locked at
# ~peak% instead of breakeven. The fixed rungs above stay the SUB-ARM floor (they
# govern below the arm); the engine composes the two with ``max()`` (phase). The
# HARD give-back backstop is RAISED 0.60→0.75 (not OFF — red-team: an ATR 4x
# expansion could otherwise hand a +7R back to 0; the backstop catches that) and
# is gated while the peak-fraction trail is armed so the two never double-cut.
#
# Red-team CALIBRATION (load-bearing): arm at +0.45R — the COMMON-CASE rung
# (#19 give-back fix, post-reset 2026-06-25 ledger: avg peak +0.39R; only 7.9%
# of trades ever reach +1.0R but 18.4% reach +0.45R — the old +1.0R arm STARVED
# the common case, so a small +0.39R peak round-tripped 100% on the wide trail
# with NOTHING locked). +0.45R is the FEE-SAFE rung: frac 0.50 → the lock is
# >= +0.225R at the arm (clears fees), and it is 2.3x the +1.0R coverage. frac
# stays ~0.50. EXPECTANCY, not a throttle: it ONLY ratchets the stop toward
# profit; size / entry side / the G6 -1.0R rail are untouched. ``arm_r == 0.0``
# DISABLES it (byte-identical).
#
# The HARVEST trail for a confirmed-momentum TREND winner must NOT collapse to the
# 1-ATR ``EXIT_HARVEST_TRAIL_MULT`` (that flat-lined the burst); it stays WIDE so
# the peak-fraction floor (not the trail) supplies the lock.
EXIT_PEAK_LOCK_ARM_R: Final[float] = _env_float("POLARIS_EXIT_PEAK_LOCK_ARM_R", 0.45)
EXIT_PEAK_LOCK_FRAC: Final[float] = _env_float("POLARIS_EXIT_PEAK_LOCK_FRAC", 0.50)
EXIT_LETRUN_HARVEST_TRAIL_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_LETRUN_HARVEST_TRAIL_MULT", 3.5
)

# --- #47 runner give-back disarm ([[exit_recalib_2026-06-26]]) -----------------
# BINARY let-the-runner-run: once a position's REACHED peak MFE clears this rung
# (+1.0R), the HARD give-back force-close (``thesis_harvest``) is DISARMED so the
# RARE big winner rides on the peak-fraction floor + the wide ATR trail instead of
# being banked at the hard-frac surrender point. ASYMMETRY (the let-winners-run
# core): a COMMON small peak (< 1.0R) keeps the give-back catch (it round-trips
# otherwise); the RARE runner (>= 1.0R) is let to run (the floor already locks
# ~peak% so it is protected, not naked). EXPECTANCY, not a throttle: it only
# REMOVES a profit-side fast-close — size / entry side / the protected-BEP /
# loser-timeout / -1.0R rail are all untouched (a red runner still closes on those).
# Env-tunable; a huge value effectively disables the disarm (give-back always on).
EXIT_PEAK_GIVEBACK_DISARM_R: Final[float] = _env_float(
    "POLARIS_EXIT_PEAK_GIVEBACK_DISARM_R", 1.0
)

# --- Grace + sustained gate ([[exit_thesis_grace_2026-06-23]]) ----------------
# ROOT CAUSE (live, PID 351): a JUST-OPENED position opened on positive OFI; the
# very next tick's OFI noise opposed → _assess_health returned BROKEN → CUT →
# thesis_cut fast-close at 0-1s hold (pnl_r 0). 73 trades closed in 0-2s; 27
# thesis_cut in 2h. The thesis was judged on a SINGLE fresh tick with no aging
# guard. flow_not_block — EXIT-TIMING precision: let a fresh thesis ESTABLISH
# before judging it. NEVER a throttle / size-cut / entry-block.
#
#   * ``EXIT_THESIS_GRACE_SEC`` (default 25.0): a position cannot be CUT or
#     thesis-HARVESTed until it has aged PAST this minimum held_seconds. Before
#     the grace, assess_thesis returns HOLD (or a non-closing LET_RUN for a
#     confirmed green winner) regardless of a momentary BROKEN read. This
#     directly kills the 0-1s instant cut. Short enough that flow_pressure's
#     legit ~10-60s scalps are still managed once aged.
#   * ``EXIT_THESIS_DEADBAND`` (default 1e-3): the adverse momentum/OFI magnitude
#     a tick must EXCEED to count as opposing — 1-tick noise inside the deadband
#     never flips the thesis to BROKEN.
#   * ``EXIT_THESIS_BROKEN_TICKS`` (default 2): consecutive broken reads required
#     before BROKEN is confirmed. The caller threads ``broken_streak`` (count of
#     PRIOR consecutive broken reads); a single noisy tick (streak below the
#     floor) stays INTACT. Omitting ``broken_streak`` (legacy callers / synthetic
#     replay) defaults to confirmed — back-compatible.
EXIT_THESIS_GRACE_SEC: Final[float] = _env_float("POLARIS_EXIT_THESIS_GRACE_SEC", 25.0)
EXIT_THESIS_DEADBAND: Final[float] = _env_float("POLARIS_EXIT_THESIS_DEADBAND", 1e-3)

# --- Horizon-scoped materiality floor on the momentum-ONLY break --------------
# [[1d_exit_horizon_fix_2026-06-26]]. The bar-recalc path feeds ``momentum_drift``
# from the last ~10 1m bars — a ~10-MINUTE intraday window. A 1D (daily-momentum)
# thesis was therefore declared BROKEN (→ thesis_cut) the instant that intraday
# window drifted fractionally against it: MEASURED 174/179 Alpaca thesis_cut
# closes were shallow-red (median -0.03R) at a median hold of 298s (5 min), with
# the -1.0R rail nowhere near. The flat ``EXIT_THESIS_DEADBAND`` (0.10%) is the
# universal 1-tick-noise floor; this is the ADDITIONAL, HORIZON-scoped materiality
# bar (a long-horizon thesis tolerates more intraday wiggle than a scalp): while
# the position is still within its strategy horizon (``held < horizon``), a
# momentum-ONLY reversal must clear ``|momentum_drift| >= floor`` to count as
# BROKEN. It CORRECTS a misclassification (intraday noise faking a daily-thesis
# break). Measured 10-min |drift|: median 0.40%, p25 0.14% → 0.15% suppresses the
# noise cluster; real reversals (p75 1.2%, p90 3.1%) pass. Env-tunable.
EXIT_THESIS_DRIFT_FLOOR: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_DRIFT_FLOOR", 0.0015
)

# --- Timeframe-scaled drift floor ([[1d_exit_horizon_fix_2026-07-02]]) --------
# P0-2 follow-up: Wave A scoped ATR (trail width / R denominator) to the
# strategy's OWN timeframe, but the drift-measurement bar feeding
# ``momentum_drift`` (``load_active_position_rows``'s ``bar_row`` ->
# ``_recent_market_state`` -> ``_recent_tick_drift``) was STILL hardcoded to
# the last 20x1m bars for EVERY strategy — a review BLOCKER caught this: this
# floor and the earlier P0-2(4) ratio calibration below were both measured
# against GENUINE per-timeframe bars, but the RUNTIME signal compared against
# them was not. Fixed alongside this floor
# (``_production_atr.timeframe_bar_rows`` — the missing counterpart to
# ``timeframe_atr_pct``, wired into ``load_active_position_rows``) so the
# SIGNAL and the FLOOR are now commensurate. Pre-signal-fix the flat 0.0015
# floor (calibrated on the OLD 1m/10-min window) passed through unconditionally
# against a 1D bar-to-bar span (routinely several PERCENT), so a 1D thesis was
# cut on its first recalc (LIVE, 2026-07-02: index_dual_momentum_rotation
# J225/AU200AU thesis_cut at 48s hold, momentum-drift path, corroborated-break
# gate never engaged since this was an UNCORROBORATED momentum-only break).
#
# Each rung = ``EXIT_THESIS_DRIFT_FLOOR`` (the proven 1m floor) scaled by the
# ratio of that timeframe's measured 10-bar |drift| q65 to the 1m q65, computed
# read-only from the live OKX+Capital bar table (``bars``, non-Alpaca — the
# venue mix the exit engine actually trades) with the SAME (last-first)/first
# 10-bar-window estimator ``_recent_tick_drift``/``bars_atr_pct`` use elsewhere.
# See ``tools/research/drift_floor_quantiles.py`` for the derivation script.
# Measured q65 (2026-07-02 snapshot, non-Alpaca): 1m 0.00061, 5m 0.00066,
# 15m 0.00349, 1H 0.00833, 1D 0.04549 → ratio-to-1m 1.00 / 1.08 / 5.74 / 13.68 /
# 74.71. "1m" is PINNED to the existing constant (ratio 1.0 by construction) so
# the tick-engine / 1m-strategy exit behaviour stays byte-identical; every other
# timeframe scales up so a long-horizon thesis is judged against ITS OWN
# noise band instead of the 1m band. Unregistered strategy id → ``strategy_timeframe``
# already degrades to "1m" (byte-identical); a timeframe missing from this map
# (future new interval) falls back to ``EXIT_THESIS_DRIFT_FLOOR`` unscaled
# (conservative — never LOOSER than the proven 1m floor).
EXIT_THESIS_DRIFT_FLOOR_RATIO: Final[dict[str, float]] = {
    "1m": 1.0,
    "5m": 1.083,
    "15m": 5.738,
    "1H": 13.676,
    "1D": 74.711,
}


def drift_floor_for_timeframe(timeframe: str | None) -> float:
    """Timeframe-scaled materiality floor for a momentum-ONLY thesis break.

    ``None`` / unregistered timeframe → the unscaled ``EXIT_THESIS_DRIFT_FLOOR``
    (the pre-fix 1m calibration — conservative fallback, never looser).
    """
    ratio = EXIT_THESIS_DRIFT_FLOOR_RATIO.get(timeframe or "", 1.0)
    return EXIT_THESIS_DRIFT_FLOOR * ratio


# --- Thesis-break maturity gate — BOTH break flavours -------------------------
# ([[trade_mess_full_audit_2026-07-02_fixplan]] P0-2 ② + verification audit
# P0-1, merged 2026-07-02; per-timeframe hold_frac + bars-seen counting per
# [[waveB_sizing_params_2026-07-02]] agenda 3). The flat ``EXIT_THESIS_GRACE_SEC``
# (25s) kills the 0-1s instant cut but is ~0.00003 of a 21-day horizon. This gate
# scales WITH the strategy horizon: below ``hold_frac_for_timeframe(...)`` of
# ``horizon_seconds`` held, NEITHER a momentum-ONLY material drift NOR a
# corroborated break (OFI-opposes / regime-flip-against — live: 1D thesis_cut
# at 0.7min) may flip the thesis BROKEN — a fresh long-horizon thesis gets real
# development time first. Past the floor — or with a None horizon (legacy/unknown
# strategy → gate INERT, byte-identical pre-fix) — both flavours flip BROKEN
# exactly as before. The G6 -1.0R hard rail / ATR trail / G6 EXIT_NOW crisis path
# are OWNED by the caller and completely untouched (a real loss is still cut on
# those layers regardless of thesis maturity). flow_not_block: removes premature
# CUTs on healthy long-horizon winners — asymmetric payoff strengthened.
#
# hold_frac is TIMEFRAME-DIFFERENTIATED: daily-or-slower (native bar >= 1D) =
# 0.10 (raised from the flat 5% — a 1D thesis needs more than 1 bar-fraction of
# development before a break judgement is trusted); intraday (< 1D) keeps the
# proven 0.05. ``POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC`` is a single global
# override — unset = timeframe-differentiated; set = wins for EVERY timeframe
# (back-compat one-knob emergency override / existing tuning workflows).
EXIT_THESIS_BREAK_HOLD_FRAC_DAILY: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC_DAILY", 0.10
)
EXIT_THESIS_BREAK_HOLD_FRAC_INTRADAY: Final[float] = _env_float(
    "POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC_INTRADAY", 0.05
)
# None (default) = use the timeframe-differentiated pair above; set = overrides
# BOTH buckets with one value (also still read directly by legacy wall-clock
# call sites as the flat fraction).
_hold_frac_override_raw = os.environ.get("POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC")
EXIT_THESIS_BREAK_HOLD_FRAC: Final[float | None] = (
    None
    if _hold_frac_override_raw is None
    else _env_float("POLARIS_EXIT_THESIS_BREAK_HOLD_FRAC", 0.05)
)
# One native-timeframe bar of 1D (86400s) is the daily-or-slower threshold — any
# native bar interval AT OR ABOVE this counts as "daily-or-slower".
_DAILY_BAR_INTERVAL_SEC: Final[int] = 86400


def hold_frac_for_timeframe(native_timeframe: str | None) -> float:
    """The maturity-gate hold_frac for a strategy's OWN (native) timeframe.

    ``EXIT_THESIS_BREAK_HOLD_FRAC`` (single global env override) wins when set.
    Otherwise: daily-or-slower (native bar interval >= 86400s) uses the DAILY
    frac (0.10); everything else (intraday, or an unrecognised/None token —
    ``bar_seconds`` falls back to the flat 300s default, well under 1D) uses the
    INTRADAY frac (0.05). Pure + total — never raises.
    """
    if EXIT_THESIS_BREAK_HOLD_FRAC is not None:
        return EXIT_THESIS_BREAK_HOLD_FRAC
    from polaris.core.isolation.reentry import bar_seconds

    if bar_seconds(native_timeframe or "") >= _DAILY_BAR_INTERVAL_SEC:
        return EXIT_THESIS_BREAK_HOLD_FRAC_DAILY
    return EXIT_THESIS_BREAK_HOLD_FRAC_INTRADAY


def required_bars_for_horizon(
    *, horizon_seconds: int, native_bar_interval_seconds: int, horizon_bars: int
) -> int:
    """Minimum NATIVE bars a position must have SEEN before a break can close it.

    ``required_bars = max(ceil(horizon_seconds * hold_frac / native_bar_interval),
    floor_bars)``, ``floor_bars = 2 if horizon_bars >= 10 else 1`` — a short
    (<10-bar) horizon strategy takes the 1-bar floor so the maturity gate never
    eats 25%+ of its whole horizon (a flat 2-bar floor on e.g. a 5-bar horizon
    would suppress 40% of it); a 1-bar floor still kills the 0-bar instant-cut.
    ``hold_frac`` resolves via the same daily/intraday split as
    ``hold_frac_for_timeframe``, keyed off the bar interval directly (no
    timeframe-string re-derivation needed). Pure + total; a non-positive
    interval degrades to the floor only (never raises, never returns 0 — a 0
    required_bars would disable the gate outright).
    """
    floor_bars = 2 if horizon_bars >= 10 else 1
    if native_bar_interval_seconds <= 0:
        return max(1, floor_bars)
    hold_frac = (
        EXIT_THESIS_BREAK_HOLD_FRAC
        if EXIT_THESIS_BREAK_HOLD_FRAC is not None
        else (
            EXIT_THESIS_BREAK_HOLD_FRAC_DAILY
            if native_bar_interval_seconds >= _DAILY_BAR_INTERVAL_SEC
            else EXIT_THESIS_BREAK_HOLD_FRAC_INTRADAY
        )
    )
    frac_bars = math.ceil(horizon_seconds * hold_frac / native_bar_interval_seconds)
    return max(frac_bars, floor_bars, 1)

EXIT_THESIS_BROKEN_TICKS: Final[int] = int(
    _env_float("POLARIS_EXIT_THESIS_BROKEN_TICKS", 2.0)
)

# Sentinel: a ``broken_streak`` an omitting caller supplies → treated as already
# confirmed (preserves pre-grace BROKEN behaviour for callers that don't yet
# thread a consecutive count).
_BROKEN_STREAK_CONFIRMED: Final[int] = 1 << 30


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


# Master gate for the adaptive thesis re-map. Default ON; OFF = every caller is
# byte-identical (mode is never assessed, evaluate_exit's mode= stays None).
EXIT_ADAPTIVE_THESIS_ON: Final[bool] = _env_flag("POLARIS_EXIT_ADAPTIVE_THESIS", True)

_ATR_USD_FLOOR: Final[float] = 1e-6

# RELATIVE floor for the ATR-in-USD unit: ≥0.1% of entry price (the close
# path's proven ``entry_price * 1e-3`` convention). A flat/stale-bar atr_pct~0
# on a high-priced instrument collapsed the absolute 1e-6 floor and exploded
# R (live: -463,734R). With the relative floor max|R| = price-move% / 0.1%.
# At 1e-3 it matches the entry-anchor sane-band floor (5e-4 × the 2-ATR stop),
# so a low-anchor instrument no longer inflates the FSM-driving mfe_r ~4x off a
# too-loose excursion floor. The absolute 1e-6 stays only as the entry_price=0
# degenerate last resort.
_ATR_PCT_RELATIVE_FLOOR: Final[float] = 1e-3

# Telemetry cap for mfe_r/mae_r (|R| ≤ 100). Behaviour-neutral: the FSM tops
# out at EXIT_FSM_HARVEST_R=2.0 and pnl_r is clamped ±10 elsewhere — no close
# or transition branch can reach the cap; it only bounds persisted telemetry.
_EXCURSION_R_CAP: Final[float] = 100.0
