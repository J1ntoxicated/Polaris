"""Layer 6 precise-exit — state/decision types + adaptive-thesis enums.

The persisted-state + decision dataclasses (``ExitState`` / ``ExitDecision``),
the MFE-protect schedule (``MfeProtectSchedule`` + its dict<->dataclass wire
adapters), and the adaptive-thesis re-map vocabulary (``Bucket`` /
``ThesisHealth`` / ``ManagementMode`` enums + the ``ThesisGivebackParams`` /
``ThesisExitParams`` param tuples). Pure data — no behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


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
      - ``peak_lock_arm_r`` / ``peak_lock_frac`` (let-winners-run,
        [[ab_letrun_maker_2026-06-24]]): once MFE reaches ``peak_lock_arm_r`` the
        floor RATCHETS to a FRACTION of the REACHED peak MFE —
        ``entry ± peak_mfe_r * peak_lock_frac * atr_r`` (R-units) — so a confirmed
        big winner (burst_rider peaked +7.33R) is locked at ~peak% instead of being
        flat-lined at the small fixed ``lock_r``. The fixed rungs above stay the
        SUB-ARM floor (they govern below the arm); the engine composes the two with
        ``max()`` (phase: fixed dominates below the arm-crossover, peak-fraction
        above) and the ratchet keeps it monotone. ``peak_lock_arm_r == 0.0``
        (default) DISABLES the peak-fraction branch → byte-identical to the
        fixed-only schedule (every legacy caller).
    """

    bep_at_r: float
    protect_at_r: float
    lock_r: float
    peak_lock_arm_r: float = 0.0
    peak_lock_frac: float = 0.0


# Hardening #9 (2026-06-23) — dict<->MfeProtectSchedule wire-point adapter
# (Slice-2 forward-correctness). The probe ``EngineDecision.mfe_protect`` carries
# the schedule as a plain ``dict[str, float]`` (JSON-serialisable for the tuning
# log); ``run_precise_exit`` consumes a typed ``MfeProtectSchedule``. While the
# engine is sealed (``applied=False``, every mode returns ``None``) this is pure
# plumbing — but pinning the round-trip now makes the future Slice-2 flip a
# one-line change instead of a silent dict/dataclass mismatch. PURE/total, no
# behaviour change (no caller threads a non-None compose schedule yet).
# The three REQUIRED fixed rungs (a partial dict is a programming error). The
# let-winners-run ``peak_lock_*`` pair is OPTIONAL on the wire — an older
# probe/tuning-log dict that predates the peak-fraction floor round-trips to the
# DISABLED default (0.0/0.0 → fixed-only, byte-identical).
_MFE_PROTECT_FIELDS: Final[tuple[str, str, str]] = (
    "bep_at_r", "protect_at_r", "lock_r",
)


def mfe_protect_to_dict(sched: MfeProtectSchedule) -> dict[str, float]:
    """Serialise an ``MfeProtectSchedule`` to the ``EngineDecision`` dict form."""
    return {
        "bep_at_r": sched.bep_at_r,
        "protect_at_r": sched.protect_at_r,
        "lock_r": sched.lock_r,
        "peak_lock_arm_r": sched.peak_lock_arm_r,
        "peak_lock_frac": sched.peak_lock_frac,
    }


def mfe_protect_from_dict(
    payload: dict[str, float] | None,
) -> MfeProtectSchedule | None:
    """Adapt an ``EngineDecision.mfe_protect`` dict to the typed schedule.

    ``None`` (the observe-mode default — no schedule threaded) → ``None``, so a
    sealed compose stays byte-identical at the wire point. A dict MUST carry all
    three fixed rungs (a partial dict is a programming error, raised loudly rather
    than silently floored). The ``peak_lock_*`` pair is optional — an older dict
    without it defaults to the DISABLED peak-fraction floor (0.0). Round-trips with
    ``mfe_protect_to_dict``.
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
        peak_lock_arm_r=float(payload.get("peak_lock_arm_r", 0.0)),
        peak_lock_frac=float(payload.get("peak_lock_frac", 0.0)),
    )


# --- Adaptive thesis re-map: types -----------------------------------------


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
