"""Layer 6 precise-exit — env helpers + trading-parameter constants.

Shared configuration primitives for the precise-exit engine: the env-override
helpers, the FSM state labels + rank, and every env-tunable trading parameter
(ATR trail / FSM rungs / loser-timeout / MFE-protect schedules / adaptive-thesis
thresholds / grace gate). Pure module-level constants — no I/O at call time.

See ``exit_engine`` for the orchestrator docstring describing each knob's role.
"""

from __future__ import annotations

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
    "POLARIS_EXIT_THESIS_GIVEBACK_HARD_FRAC", 0.60
)
EXIT_LETRUN_TRAIL_MULT: Final[float] = _env_float(
    "POLARIS_EXIT_LETRUN_TRAIL_MULT", 4.0
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

# RELATIVE floor for the ATR-in-USD unit: ≥0.01% of entry price (the close
# path's proven ``entry_price * 1e-4`` convention). A flat/stale-bar atr_pct~0
# on a high-priced instrument collapsed the absolute 1e-6 floor and exploded
# R (live: -463,734R). With the relative floor max|R| = price-move% / 0.01%.
# The absolute 1e-6 stays only as the entry_price=0 degenerate last resort.
_ATR_PCT_RELATIVE_FLOOR: Final[float] = 1e-4

# Telemetry cap for mfe_r/mae_r (|R| ≤ 100). Behaviour-neutral: the FSM tops
# out at EXIT_FSM_HARVEST_R=2.0 and pnl_r is clamped ±10 elsewhere — no close
# or transition branch can reach the cap; it only bounds persisted telemetry.
_EXCURSION_R_CAP: Final[float] = 100.0
