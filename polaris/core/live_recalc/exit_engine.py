"""Layer 6 — precise-exit engine (#26 EXPECTANCY, not a defensive throttle).

Jin's #1 loss-defense: 손실방어 = 정밀 엑싯 (adaptive stop / timing), NOT size
reduction and NOT entry blocking. This module is pure per-position exit math —
it lets winners run (ATR-anchored trailing stop + MFE-driven harvest FSM) and
cuts dead losers (round-trip break-even + stale-loser timeout). It never:

* reduces position size (T4 sizing chain is untouched / OUTSIDE this module),
* blocks or vetoes an entry (entry-side ``flow_not_block`` unchanged),
* adds a P&L / strategy / bot halt (per-position close decisions only).

The G6 hard ``stop_hit`` rail (pnl_r <= -1.0R) is the catastrophic backstop and
stays in the orchestrator — these precise exits ADD on top of it.

Pure + unit-testable: no I/O. The tick loop reads tracked state from the
``positions`` row, calls these helpers, and persists the returned state back.

Trading parameters (CONSERVATIVE defaults adopted from the auto_invasion
``exit_fsm`` reference; env-overridable; FLAGGED as pending /debate calibration):

* ``EXIT_ATR_TRAIL_MULT`` (POLARIS_EXIT_ATR_TRAIL_MULT, default 2.0) — trailing
  stop distance in ATR units below the peak (long) / above the trough (short).
* ``EXIT_FSM_TOUCH_R`` (POLARIS_EXIT_FSM_TOUCH_R, default 0.5) — MFE in R to
  advance OPEN -> TOUCHED.
* ``EXIT_FSM_PROTECT_R`` (POLARIS_EXIT_FSM_PROTECT_R, default 1.0) — MFE in R to
  advance -> PROTECTED.
* ``EXIT_FSM_HARVEST_R`` (POLARIS_EXIT_FSM_HARVEST_R, default 2.0) — MFE in R to
  advance -> HARVEST.
* ``EXIT_HARVEST_TRAIL_MULT`` (POLARIS_EXIT_HARVEST_TRAIL_MULT, default 1.0) —
  tighter ATR trail once in HARVEST (locks more of a big winner; still only
  ratchets toward profit, never loosens).
* ``EXIT_LOSER_TIMEOUT_SEC`` (POLARIS_EXIT_LOSER_TIMEOUT_SEC, default 900) —
  age in seconds after which a still-OPEN losing position (never touched
  profit) is closed.
* ``EXIT_LOSER_TIMEOUT_EXT_MULT`` (POLARIS_EXIT_LOSER_TIMEOUT_EXT_MULT,
  default 2.0) — peak-extension: a position that once touched profit gets the
  timeout multiplied by this before a stale-loser close fires.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
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


@dataclass(slots=True)
class ExitState:
    """Per-position precise-exit state (mirrors the persisted columns).

    ``peak_price`` / ``trough_price`` are the running price extremes; ``stop_price``
    is the ratcheting ATR-trailing stop; ``exit_state`` is the FSM label.
    ``mfe_r`` / ``mae_r`` are the excursion telemetry in R units.
    """

    peak_price: float
    trough_price: float
    stop_price: float | None
    exit_state: str
    mfe_r: float = 0.0
    mae_r: float = 0.0


@dataclass(slots=True)
class ExitDecision:
    """Outcome of one precise-exit evaluation for a position."""

    state: ExitState
    close: bool
    close_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MfeProtectSchedule:
    """MFE-driven protective-stop schedule (flow_pressure EXIT precision).

    Once favourable excursion (``mfe_r``) reaches a rung, the protective stop is
    RATCHETED toward profit on TOP of the let-winners-run ATR trail (the
    protection-tighter of the two is taken). EXPECTANCY, not a throttle: it only
    moves the stop toward profit (never loosens — the ATR ratchet + the
    protected-BEP / loser-timeout / G6 -1.0R rail are untouched), capturing the
    +0.67R avg MFE and cutting the 17% >0.5R give-back (late exit).

      - ``bep_at_r``: MFE at which the stop floors at BREAK-EVEN (entry) — leaves
        initial risk.
      - ``protect_at_r``: MFE at which a meaningful POSITIVE R is locked.
      - ``lock_r``: the positive R the stop locks once ``protect_at_r`` is hit
        (stop floors at entry ± ``lock_r`` in R-units).
    """

    bep_at_r: float
    protect_at_r: float
    lock_r: float


# Hardening #9 (2026-06-23) — dict<->MfeProtectSchedule wire-point adapter
# (Slice-2 forward-correctness). The probe ``EngineDecision.mfe_protect`` carries
# the schedule as a plain ``dict[str, float]`` (JSON-serialisable for the tuning
# log); ``run_precise_exit`` consumes a typed ``MfeProtectSchedule``. While the
# engine is sealed (``applied=False``, every mode returns ``None``) this is pure
# plumbing — but pinning the round-trip now makes the future Slice-2 flip a
# one-line change instead of a silent dict/dataclass mismatch. PURE/total, no
# behaviour change (no caller threads a non-None compose schedule yet).
_MFE_PROTECT_FIELDS: Final[tuple[str, str, str]] = (
    "bep_at_r", "protect_at_r", "lock_r",
)


def mfe_protect_to_dict(sched: MfeProtectSchedule) -> dict[str, float]:
    """Serialise an ``MfeProtectSchedule`` to the ``EngineDecision`` dict form."""
    return {
        "bep_at_r": sched.bep_at_r,
        "protect_at_r": sched.protect_at_r,
        "lock_r": sched.lock_r,
    }


def mfe_protect_from_dict(
    payload: dict[str, float] | None,
) -> MfeProtectSchedule | None:
    """Adapt an ``EngineDecision.mfe_protect`` dict to the typed schedule.

    ``None`` (the observe-mode default — no schedule threaded) → ``None``, so a
    sealed compose stays byte-identical at the wire point. A dict MUST carry all
    three rungs (a partial dict is a programming error, raised loudly rather than
    silently floored). Round-trips with ``mfe_protect_to_dict``.
    """
    if payload is None:
        return None
    missing = [k for k in _MFE_PROTECT_FIELDS if k not in payload]
    if missing:
        raise ValueError(
            f"mfe_protect dict missing rungs {missing}; got keys "
            f"{sorted(payload)}"
        )
    return MfeProtectSchedule(
        bep_at_r=float(payload["bep_at_r"]),
        protect_at_r=float(payload["protect_at_r"]),
        lock_r=float(payload["lock_r"]),
    )


# --- Adaptive thesis re-map: types + pure assessment -----------------------


class Bucket(Enum):
    """The exit-archetype the position's strategy belongs to.

    TREND theses (momo / trend / breakout / event / gap / flow_pressure /
    burst_rider) want to RUN while confirmed; REVERSION theses (mean_reversion /
    range / micro_reversion) target a BOUNDED revert-to-mean and never widen.
    """

    TREND = "trend"
    REVERSION = "reversion"


class ThesisHealth(Enum):
    """Whether the ENTRY thesis is still valid for THIS position.

    INTACT  = same-direction momentum / OFI still present + regime not flipped
              against the position.
    FADING  = the position went green but momentum flattened / OFI decayed (the
              edge is leaking) — bank it.
    BROKEN  = momentum reversed / OFI firmly opposes / regime flipped against the
              position / a breakout fell back inside — the entry reason is gone.
    """

    INTACT = "intact"
    FADING = "fading"
    BROKEN = "broken"


class ManagementMode(Enum):
    """The exit-schedule re-map decision for THIS position (EXIT timing only).

    LET_RUN = widen the trail + BEP floor (a confirmed winner runs).
    HARVEST = tighten to a BEP+lock floor at the reached rung; fast-close if the
              give-back is hard.
    CUT     = close now (ONLY a broken + red/flat position — never a green one).
    REMODE  = swap the trend<->range exit schedule (exit-params only — NOT a
              strategy swap).
    HOLD    = keep the existing per-strategy defaults (nothing decisive).
    """

    LET_RUN = "let_run"
    HARVEST = "harvest"
    CUT = "cut"
    REMODE = "remode"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class ThesisGivebackParams:
    """MFE give-back thresholds for the orthogonal HARVEST modifier.

    ``arm_r``: MFE in R the position must reach before the modifier can arm.
    ``frac``: fraction of the reached peak MFE surrendered above which HARVEST is
        forced (soft).
    ``hard_frac``: fraction surrendered above which the position is HARVESTED
        IMMEDIATELY (the thesis_harvest fast-close).
    """

    arm_r: float
    frac: float
    hard_frac: float


@dataclass(frozen=True, slots=True)
class ThesisExitParams:
    """Exit-schedule overrides a ManagementMode maps to (consumed by the FSM).

    ``trail_mult`` / ``mfe_protect`` / ``profit_target_r`` override the same-named
    ``evaluate_exit`` knobs (``None`` = leave the caller's value). ``thesis_harvest``
    / ``thesis_cut`` are TOP-of-ladder immediate-close flags (checked BEFORE the
    existing close reasons). ``None``/all-default = byte-identical to no re-map.
    """

    trail_mult: float | None = None
    mfe_protect: MfeProtectSchedule | None = None
    profit_target_r: float | None = None
    thesis_harvest: bool = False
    thesis_cut: bool = False


# A reversion-class strategy targets a bounded revert-to-mean; its
# correlation_group_id carries one of these substrings. Everything else (momo /
# trend / breakout / event / gap / ema_trend / flow_pressure / burst_rider) is a
# TREND archetype that may run while confirmed.
_REVERSION_GROUP_SUBSTRINGS: Final[tuple[str, ...]] = ("mean_reversion", "range")


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
) -> ThesisHealth:
    """Classify the entry thesis as INTACT / FADING / BROKEN (pure, total).

    BROKEN: momentum reversed against the position, OR OFI firmly opposes, OR the
    regime flipped to the OPPOSITE directional bias of the entry regime — but ONLY
    when the adverse signal is SUSTAINED: its magnitude exceeds ``DEADBAND`` (1-tick
    noise inside the band never breaks) AND the consecutive-broken ``broken_streak``
    has reached ``EXIT_THESIS_BROKEN_TICKS`` (a single noisy tick never flips a
    fresh winner). A regime FLIP is structural, not tick noise → it bypasses the
    streak/deadband (a confirmed opposite-bias regime is a real break).
    FADING: the position was green (mfe armed) but momentum flattened / OFI decayed
    — the edge is leaking.
    INTACT: same-direction momentum / OFI still present (or simply nothing
    indicating a break).
    """
    drift_dir = sign * momentum_drift  # >0 = momentum WITH the position
    ofi_dir = None if ofi is None else sign * ofi  # >0 = flow WITH the position

    # --- adverse micro-signal (deadband-gated): only count an opposition whose
    #     magnitude clears the small deadband — a tiny 1-tick oscillation inside
    #     the band is noise, not a thesis break.
    momentum_reversed = drift_dir < 0.0 and abs(momentum_drift) > EXIT_THESIS_DEADBAND
    ofi_opposes = (
        ofi_dir is not None and ofi_dir < 0.0 and abs(ofi_dir) > EXIT_THESIS_DEADBAND
    )
    # SUSTAINED confirmation: a micro break (momentum/OFI) only counts once the
    # consecutive-broken streak has reached the floor — 1-tick noise never breaks.
    micro_broken = (momentum_reversed or ofi_opposes) and (
        broken_streak >= EXIT_THESIS_BROKEN_TICKS
    )

    # --- regime flip (structural — bypasses the micro streak/deadband) ---
    entry_dir = _regime_dir(entry_regime)
    now_dir = _regime_dir(regime)
    regime_flipped_against = (
        entry_dir != 0
        and now_dir != 0
        and now_dir == -entry_dir
        and sign * now_dir < 0  # the new regime opposes the position
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
        # Tight floor at the reached rung: lock positive R once the BEP rung is
        # passed (BEP+lock). A HARD give-back closes immediately (thesis_harvest).
        return ThesisExitParams(
            trail_mult=EXIT_HARVEST_TRAIL_MULT,
            mfe_protect=base_mfe_protect or bar_mfe_protect,
            profit_target_r=base_profit_target_r,
            thesis_harvest=gb >= 2,
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


def _atr_one_usd(*, entry_price: float, atr_pct: float) -> float:
    """One-ATR distance in price terms (relative floor, finite)."""
    return max(
        entry_price * max(atr_pct, 0.0),
        entry_price * _ATR_PCT_RELATIVE_FLOOR,
        _ATR_USD_FLOOR,
    )


def _atr_r_usd(*, entry_price: float, atr_pct: float) -> float:
    """R-unit denominator in price terms — matches the realised-PnL path
    (``entry_price * atr_pct * 2.0`` with the same 2x stop convention as
    ``compute_unrealized_pnl_r`` / ``real_pnl_r_from_fills``)."""
    return max(
        entry_price * max(atr_pct, 0.0) * 2.0,
        entry_price * _ATR_PCT_RELATIVE_FLOOR,
        _ATR_USD_FLOOR,
    )


def init_exit_state(*, entry_price: float, side: str) -> ExitState:
    """Seed extremes at entry; no stop yet (set on first ratchet)."""
    return ExitState(
        peak_price=entry_price,
        trough_price=entry_price,
        stop_price=None,
        exit_state=EXIT_STATE_OPEN,
    )


def _next_fsm_state(current: str, mfe_r: float) -> str:
    """Advance the FSM by MFE (monotone — never regress)."""
    if mfe_r >= EXIT_FSM_HARVEST_R:
        target = EXIT_STATE_HARVEST
    elif mfe_r >= EXIT_FSM_PROTECT_R:
        target = EXIT_STATE_PROTECTED
    elif mfe_r >= EXIT_FSM_TOUCH_R:
        target = EXIT_STATE_TOUCHED
    else:
        target = EXIT_STATE_OPEN
    cur_rank = _STATE_RANK.get(current, 0)
    tgt_rank = _STATE_RANK.get(target, 0)
    return target if tgt_rank > cur_rank else current


def _trailing_stop(
    *, side: str, anchor_extreme: float, atr_one: float, trail_mult: float,
    prev_stop: float | None,
) -> float:
    """ATR-anchored stop that only ratchets TOWARD profit (never loosens).

    Long: ``peak - trail_mult*ATR``, clamped up to ``max(prev_stop, ...)``.
    Short: ``trough + trail_mult*ATR``, clamped down to ``min(prev_stop, ...)``.
    """
    if side == "long":
        candidate = anchor_extreme - trail_mult * atr_one
        return candidate if prev_stop is None else max(prev_stop, candidate)
    candidate = anchor_extreme + trail_mult * atr_one
    return candidate if prev_stop is None else min(prev_stop, candidate)


def evaluate_exit(
    *,
    prev: ExitState,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    pnl_r: float,
    held_seconds: int,
    loser_timeout_sec: float | None = None,
    profit_target_r: float | None = None,
    entry_atr_pct: float | None = None,
    trail_mult: float | None = None,
    mfe_protect: MfeProtectSchedule | None = None,
    mode: ManagementMode | None = None,
    thesis_bucket: Bucket = Bucket.TREND,
    thesis_giveback: ThesisGivebackParams | None = None,
) -> ExitDecision:
    """Advance excursion + stop + FSM for one position; decide close-or-hold.

    Returns the NEW ``ExitState`` (to persist) and whether a precise exit
    fired. This NEVER changes size and NEVER blocks an entry — close-or-hold of
    THIS position only. The G6 -1.0R hard stop_hit rail stays in the caller.

    ``loser_timeout_sec`` (Component B): the stale-loser timeout floor for THIS
    position, scaled to its strategy timeframe by the caller (a 1H thesis is not
    force-closed at the flat 900s). ``None`` keeps the flat
    ``EXIT_LOSER_TIMEOUT_SEC`` default (fast strategies stay short). This only
    moves the TIMEOUT horizon — the ATR-trailing stop and the protected-BEP
    exit (the precise exits) are untouched.

    ``profit_target_r``: a fixed take-profit in R for mean-reversion strategies
    (set per-strategy from ``StrategyMetadata.profit_target_r``). When set and
    favourable excursion reaches it, the position is HARVESTED immediately —
    BEFORE the wide let-winners-run ATR trail can give a bounded revert-to-mean
    gain back (a BB fade reverts extreme→middle ≈ 1 R, then bounces; the 2-ATR
    trail would round-trip the whole move). ``None`` (default) keeps the trend
    exit for every momentum strategy — byte-identical. EXPECTANCY, not a
    throttle: a per-position close target only — size / entry side / halt rail
    untouched.

    ``entry_atr_pct``: the ENTRY-TIME ATR anchor for the R-unit DENOMINATOR
    (mfe_r/mae_r + the FSM thresholds they drive). The per-tick recomputed
    ``atr_pct`` shrank during volatility contraction and inflated excursions
    4-8x; anchoring pins the measuring unit at the entry-time risk. The TRAIL
    width intentionally keeps the CURRENT ``atr_pct`` — a Chandelier trail
    tracks today's noise band and the ratchet already forbids loosening.
    ``None`` (legacy rows / callers) keeps the current-ATR denominator —
    byte-identical to the pre-anchor behaviour.

    ``trail_mult``: per-position override of the let-winners-run ATR-trail width
    (in ATR units). ``None`` keeps the module default ``EXIT_ATR_TRAIL_MULT``
    (=2.0) — byte-identical for every existing caller. A WIDER value lets a
    favourable drift run further before the trail closes it: the tick engine
    passes a wider trail for ``flow_pressure`` momentum positions so OFI drift is
    captured past the old ~75s scalp (honest fee-net retune w02ccvq0q — the
    longer-hold cohort was the only gross-cost-positive bucket). EXPECTANCY, not
    a throttle: it only LOOSENS the trail (lets the winner run); the ratchet still
    forbids loosening the already-set stop, the protected-BEP exit + loser-timeout
    + the G6 -1.0R hard rail (the loss-defence) are all untouched. HARVEST still
    tightens to ``EXIT_HARVEST_TRAIL_MULT`` once a big winner is locked.

    ``mfe_protect`` (flow_pressure EXIT precision,
    [[flow_pressure_calibration_ai_2026-06-23]]): an MFE-driven protective-stop
    schedule that RATCHETS the stop toward profit once favourable excursion
    appears (BEP at ``bep_at_r``, lock ``lock_r`` positive R at ``protect_at_r``).
    The protection-tighter of the ATR trail and the schedule floor is taken, so it
    only ever tightens — capturing the +0.67R avg MFE and cutting the 17% >0.5R
    give-back. ``None`` (every existing caller) → byte-identical. EXPECTANCY, not
    a throttle: size / entry side / the G6 -1.0R rail untouched; it cannot loosen
    the trail (the ratchet below still maxes/mins against the prior stop).

    ``mode`` (adaptive thesis re-map, [[adaptive_thesis_remap_2026-06-23]]): a
    ``ManagementMode`` from ``assess_thesis`` that RE-MAPS this position's exit
    schedule from entry-thesis health. ``None`` (default) → byte-identical for
    EVERY existing caller (no override applied, no thesis close checked). When set,
    ``mode_to_exit_params`` resolves trail_mult / mfe_protect / profit_target
    OVERRIDES (the mode tuple wins over the same-named caller kwargs) and the
    thesis_harvest / thesis_cut fast-closes are checked at the TOP of the close
    ladder (before the existing reasons). EXIT timing only — LET_RUN widens (winner
    runs), HARVEST tightens/banks, CUT closes an invalidated (broken+red) position,
    REMODE swaps the trend<->range schedule; size / entry / the G6 -1.0R rail
    untouched. ``thesis_bucket`` / ``thesis_giveback`` feed the param mapping (a
    None giveback degrades to the module defaults).
    """
    # 1. Update price extremes (running peak / trough over the position life).
    peak = max(prev.peak_price, last_price)
    trough = min(prev.trough_price, last_price)

    # 2. Excursion in R units (favourable >= 0, adverse <= 0) — denominator
    #    anchored at entry when available; telemetry capped at |100R|.
    r_denominator_pct = atr_pct if entry_atr_pct is None else entry_atr_pct
    atr_r = _atr_r_usd(entry_price=entry_price, atr_pct=r_denominator_pct)
    if side == "long":
        mfe_r = max(0.0, (peak - entry_price) / atr_r)
        mae_r = min(0.0, (trough - entry_price) / atr_r)
    else:
        mfe_r = max(0.0, (entry_price - trough) / atr_r)
        mae_r = min(0.0, (entry_price - peak) / atr_r)
    mfe_r = min(mfe_r, _EXCURSION_R_CAP)
    mae_r = max(mae_r, -_EXCURSION_R_CAP)

    # 2b. Adaptive thesis re-map (mode override). ``mode is None`` → NOTHING
    #     changes (the trail_mult / mfe_protect / profit_target_r below are the
    #     caller's, and the thesis closes are off) → byte-identical to every
    #     existing caller. When a mode is supplied, the mode tuple OVERRIDES the
    #     same-named exit knobs (EXIT timing only — size / entry / G6 rail
    #     untouched) and arms the thesis_harvest / thesis_cut top-of-ladder closes.
    thesis_harvest = False
    thesis_cut = False
    if mode is not None:
        giveback = (
            thesis_giveback
            if thesis_giveback is not None
            else ThesisGivebackParams(
                arm_r=EXIT_THESIS_GIVEBACK_ARM_R,
                frac=EXIT_THESIS_GIVEBACK_FRAC,
                hard_frac=EXIT_THESIS_GIVEBACK_HARD_FRAC,
            )
        )
        tp = mode_to_exit_params(
            mode, bucket=thesis_bucket, mfe_r=mfe_r, pnl_r=pnl_r,
            giveback=giveback, base_mfe_protect=mfe_protect,
            base_profit_target_r=profit_target_r,
        )
        if tp.trail_mult is not None:
            trail_mult = tp.trail_mult
        mfe_protect = tp.mfe_protect
        profit_target_r = tp.profit_target_r
        thesis_harvest = tp.thesis_harvest
        thesis_cut = tp.thesis_cut

    # 3. FSM advance by MFE (monotone — never regress).
    new_state = _next_fsm_state(prev.exit_state, mfe_r)

    # 4. ATR-trailing stop. Tighter trail once in HARVEST (locks the winner;
    #    still only ratchets toward profit — never loosens).
    atr_one = _atr_one_usd(entry_price=entry_price, atr_pct=atr_pct)
    # Let-winners-run trail width: the per-position ``trail_mult`` override (e.g.
    # a wider flow_pressure trail) when supplied, else the module default. HARVEST
    # still tightens to EXIT_HARVEST_TRAIL_MULT regardless — a locked big winner
    # is protected even under a wider running trail.
    run_trail_mult = EXIT_ATR_TRAIL_MULT if trail_mult is None else trail_mult
    effective_trail_mult = (
        EXIT_HARVEST_TRAIL_MULT
        if new_state == EXIT_STATE_HARVEST
        else run_trail_mult
    )
    anchor = peak if side == "long" else trough
    stop_price = _trailing_stop(
        side=side, anchor_extreme=anchor, atr_one=atr_one,
        trail_mult=effective_trail_mult, prev_stop=prev.stop_price,
    )

    # 4b. MFE-protect floor (flow_pressure EXIT precision): once favourable
    #     excursion reaches a rung, ratchet the stop toward profit — BEP at
    #     ``bep_at_r`` (leave initial risk), lock ``lock_r`` positive R at
    #     ``protect_at_r``. Take the protection-tighter of the ATR trail and the
    #     floor (long → higher stop, short → lower) so it ONLY tightens; the
    #     ratchet above already forbids loosening. ``None`` → unchanged.
    if mfe_protect is not None:
        protect_floor: float | None = None
        if mfe_r >= mfe_protect.protect_at_r:
            offset = mfe_protect.lock_r * atr_r  # positive R locked, in price
            protect_floor = (
                entry_price + offset if side == "long" else entry_price - offset
            )
        elif mfe_r >= mfe_protect.bep_at_r:
            protect_floor = entry_price  # break-even: leave initial risk
        if protect_floor is not None:
            stop_price = (
                max(stop_price, protect_floor)
                if side == "long"
                else min(stop_price, protect_floor)
            )

    state = ExitState(
        peak_price=peak, trough_price=trough, stop_price=stop_price,
        exit_state=new_state, mfe_r=mfe_r, mae_r=mae_r,
    )

    # 5. Close decisions (precise exits — per-position only).
    #    (a-) ADAPTIVE THESIS top-of-ladder closes (checked BEFORE every existing
    #         reason). ``thesis_cut``: the entry thesis is BROKEN and the position
    #         is red/flat (assess_thesis only emits CUT then) → close NOW.
    #         ``thesis_harvest``: a HARD give-back on a once-green position → bank
    #         it NOW (near the peak). EXIT timing only; ``mode is None`` leaves both
    #         False → byte-identical.
    if thesis_cut:
        return ExitDecision(state=state, close=True, close_reason="thesis_cut")
    if thesis_harvest:
        return ExitDecision(state=state, close=True, close_reason="thesis_harvest")

    #    (a0) TAKE-PROFIT at the mean-reversion target FIRST: a strategy that
    #         opts in with a fixed ``profit_target_r`` (a BB fade) HARVESTS the
    #         instant current PnL reaches the target — before the wide
    #         let-winners-run ATR trail can round-trip a bounded revert-to-mean.
    #         ``None`` keeps every momentum strategy on the trend exit (no fixed
    #         target). EXPECTANCY — per-position close only; never size / entry /
    #         halt.
    if profit_target_r is not None and pnl_r >= profit_target_r:
        return ExitDecision(state=state, close=True, close_reason="target_harvest")

    #    (a) PROTECTED break-even FIRST: a round-tripped winner that gave it all
    #        back closes at break-even — the tighter, more precise exit takes
    #        priority over the wider ATR trail (don't wait for the trail when a
    #        once-protected winner has already round-tripped negative).
    if new_state in (EXIT_STATE_PROTECTED, EXIT_STATE_HARVEST) and pnl_r < 0.0:
        return ExitDecision(state=state, close=True, close_reason="protected_bep")

    #    (b) ATR-trailing stop touched (let-winners-run trail).
    stop_touched = (
        (side == "long" and last_price <= stop_price)
        or (side == "short" and last_price >= stop_price)
    )
    if stop_touched:
        return ExitDecision(state=state, close=True, close_reason="atr_trail_stop")

    #    (c) Stale-loser timeout — a currently-losing position past its timeout
    #        is closed. Peak-extension: a position that ONCE touched profit
    #        earned more rope, so its timeout is multiplied by
    #        EXIT_LOSER_TIMEOUT_EXT_MULT before the close fires (a never-profit
    #        loser still times out at the BASE EXIT_LOSER_TIMEOUT_SEC). Still a
    #        per-position exit — no size change, no entry block, no halt.
    touched_profit = _STATE_RANK.get(new_state, 0) > _STATE_RANK[EXIT_STATE_OPEN]
    timeout = (
        EXIT_LOSER_TIMEOUT_SEC
        if loser_timeout_sec is None
        else loser_timeout_sec
    )
    if touched_profit:
        timeout *= EXIT_LOSER_TIMEOUT_EXT_MULT
    if pnl_r < 0.0 and held_seconds > timeout:
        return ExitDecision(state=state, close=True, close_reason="loser_timeout")

    return ExitDecision(state=state, close=False, close_reason=None)


__all__ = [
    "EXIT_ADAPTIVE_THESIS_ON",
    "EXIT_ATR_TRAIL_MULT",
    "EXIT_BAR_MFE_BEP_R",
    "EXIT_BAR_MFE_LOCK_R",
    "EXIT_BAR_MFE_PROTECT_R",
    "EXIT_EQUITY_MFE_BEP_R",
    "EXIT_EQUITY_MFE_LOCK_R",
    "EXIT_EQUITY_MFE_PROTECT_R",
    "EXIT_FSM_HARVEST_R",
    "EXIT_FSM_PROTECT_R",
    "EXIT_FSM_TOUCH_R",
    "EXIT_HARVEST_TRAIL_MULT",
    "EXIT_LETRUN_TRAIL_MULT",
    "EXIT_LOSER_TIMEOUT_EXT_MULT",
    "EXIT_LOSER_TIMEOUT_SEC",
    "EXIT_STATE_HARVEST",
    "EXIT_STATE_OPEN",
    "EXIT_STATE_PROTECTED",
    "EXIT_STATE_TOUCHED",
    "EXIT_THESIS_BROKEN_TICKS",
    "EXIT_THESIS_DEADBAND",
    "EXIT_THESIS_GIVEBACK_ARM_R",
    "EXIT_THESIS_GIVEBACK_FRAC",
    "EXIT_THESIS_GIVEBACK_HARD_FRAC",
    "EXIT_THESIS_GRACE_SEC",
    "Bucket",
    "ExitDecision",
    "ExitState",
    "ManagementMode",
    "MfeProtectSchedule",
    "ThesisExitParams",
    "ThesisGivebackParams",
    "ThesisHealth",
    "assess_thesis",
    "bucket_from_correlation_group",
    "evaluate_exit",
    "init_exit_state",
    "mfe_protect_from_dict",
    "mfe_protect_to_dict",
    "mode_to_exit_params",
    "tick_micro_broken",
]
