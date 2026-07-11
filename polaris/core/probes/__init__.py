"""ADR-012 — PROBE → ENGINE → TUNING-LOG framework (Slice 1, observe-only).

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block. This package is the
reusable judgement seam attached to G6 first (generalizable to other gates):

  * **PROBE** — ``evaluate(ctx) -> ProbeReading | None`` (None = ABSTAIN). A
    probe ONLY describes the world (a signed, position-relative ``lean`` +
    ``confidence`` + ``evidence``). It NEVER names an action, NEVER closes,
    sizes, or blocks.
  * **ENGINE** (sole actor) — ``compose(readings, mode) -> EngineDecision``.
    In ``mode="observe"`` it STILL computes ``composite_lean`` + the action it
    WOULD take (for the tuning log) BUT returns ALL knobs ``None`` and
    ``applied=False`` so NOTHING can thread into ``run_precise_exit`` — the
    live exit path is provably byte-identical (mirrors the W1 sentinel sidecar
    + the G3/G4 shadow). Slice 2 adds ``trail_only`` / ``full``.

The hard rails (−1R G6, protected-BEP, loser-timeout, Q9 widen-only, hard-MAX)
BYPASS this framework entirely — the engine is a PARAMETER OVERLAY on the #26
FSM, never a new close path. 9-stack sizing is untouched (the engine touches
ZERO sizing). Rejection keywords = 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from polaris.core.probes.roles import (
    PROBE_ROLE_REGISTRY,
    ProbeRole,
    role_for_probe,
)

__all__ = [
    "PROBE_ROLE_REGISTRY",
    "EngineAction",
    "EngineDecision",
    "EngineMode",
    "ExitEngine",
    "Probe",
    "ProbeBus",
    "ProbeContext",
    "ProbeKind",
    "ProbeReading",
    "ProbeRole",
    "role_for_probe",
]

# The ONE shared action enum — reused by the engine decision AND the tuning log
# (engine action == log action; there is no second divergent vocabulary).
# Hardening #10 (2026-06-23): SWAP removed — the probe engine is a PARAMETER
# overlay on the #26 FSM and never strategy-swaps (that path is L6's
# GateDecision.SWAP_STRATEGY); _quantize never produced "SWAP", so the
# unemittable dead member is gone. Reserve it again only when a probe-composed
# strategy-swap producer actually exists.
EngineAction = Literal["HOLD", "WIDEN", "TIGHTEN", "HARVEST"]
EngineMode = Literal["observe", "trail_only", "full"]
# Probe kinds shipped in Slice 1 + the W2 regime-fit shadow (news / COT land
# in later slices).
ProbeKind = Literal["technical", "volume", "session", "loss", "profit", "regime"]


@dataclass(frozen=True, slots=True)
class ProbeContext:
    """Zero-fetch, read-only view over what ``_evaluate_position`` already holds.

    A probe receives this and nothing else — no DB handle, no network, no cache
    fetch. Every field is data the recalc pass computed inline this tick (price /
    pnl_r / excursions / market microstructure) plus the TIME-only
    ``seconds_to_close`` (precomputed from the stream's session calendar at the
    attach site). ``mfe_r`` / ``mae_r`` are the running excursions in R units.
    """

    position_id: str
    venue: str
    symbol: str
    underlying_group_id: str
    side: str
    entry_price: float
    last_price: float
    atr_pct: float
    pnl_r: float
    mfe_r: float
    mae_r: float
    held_seconds: int
    volume_now: float
    volume_z: float
    atr_slope: float
    recent_ticks: list[dict[str, Any]]
    exit_state: str
    regime: str
    now_ts: int
    # TIME-only seconds-to-next-session-close (None = no close to lean toward →
    # the session probe ABSTAINS). Precomputed at the attach site so the probe
    # stays pure / sync / zero-fetch.
    seconds_to_close: int | None = None
    # W2 (backgate-plan design-exit-matrix.md §B) — RegimeFitProbe's family
    # input: "momentum" | "reversion", CALLER-supplied (mirrors the entrance
    # judge's caller-supplied regime_lean) since only the attach site knows the
    # position's strategy. Default "momentum" preserves the pre-W2 degenerate
    # shape for any caller that does not yet supply it.
    signal_family: str = "momentum"
    # Version stamp for this dataclass's EXTENDED (post-Slice-1) shape — bumped
    # only when a field is ADDED, never on a value change. Lets an offline
    # reader over probe_readings/probe_decisions tell which ProbeContext schema
    # produced a row without inferring it from column presence. A position
    # opened before this field existed is UNAFFECTED: ProbeContext is built
    # FRESH every observe tick (never deserialized from storage), so the old
    # position's close path just gets the current version — no migration, no
    # regression (proven byte-identical by test_probe_attach_byte_identical.py).
    probe_ctx_version: int = 2


@dataclass(frozen=True, slots=True)
class ProbeReading:
    """A probe's description of the world — never an action.

    ``lean`` is SIGNED and position-relative in ``[-1, +1]``: ``-1`` = adverse
    (favor tighten / exit), ``+1`` = favorable (favor widen / run), ``0`` =
    neutral. ``confidence`` is ``[0, 1]``. ``evidence`` is a free-form dict for
    the tuning log (the raw inputs behind the lean).
    """

    probe_id: str
    kind: ProbeKind
    lean: float
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Probe(Protocol):
    """A probe describes the world. ``evaluate`` returns ``None`` to ABSTAIN.

    ``role`` (hardening #8) declares WHERE in the gate pipeline the probe may
    attach (Eligibility/Signal/Validate/Position/Exit) — orthogonal to ``kind``
    (the observation axis). The role SSOT is ``PROBE_ROLE_REGISTRY``; every
    catalog probe sets ``role`` from it (a role-missing probe fails import).
    """

    probe_id: str
    kind: ProbeKind
    role: ProbeRole

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None: ...


@dataclass(frozen=True, slots=True)
class EngineDecision:
    """The engine's composed decision + the knobs it would thread (Slice 2+).

    In ``mode="observe"`` ALL knobs are ``None`` and ``applied=False`` — the
    ``action`` + ``composite_lean`` are computed for the LOG only and can never
    reach ``run_precise_exit``. ``contributing`` lists the probe_ids that fed the
    composite (tuning-log provenance).
    """

    action: EngineAction
    composite_lean: float
    trail_mult: float | None = None
    mfe_protect: dict[str, float] | None = None
    profit_target_r: float | None = None
    widen_atr_mult: float | None = None
    contributing: list[str] = field(default_factory=list)
    applied: bool = False
    # AI-escalation observe-only seam (hardening #11, 2026-06-23). ``ambiguous``
    # = the composite landed in the HOLD dead band (between the TIGHTEN and
    # WIDEN thresholds) where a future arbiter could escalate. PURE TELEMETRY:
    # the RUNTIME action/knobs/applied above are the existing deterministic
    # fallback and GPT calls stay 0 — the consumer (arbiter) is deferred
    # (JIN-SURFACE, rank 18). ``deadband_margin`` = signed distance to the
    # nearer decisive threshold (negative = inside the band).
    ambiguous: bool = False
    deadband_margin: float | None = None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


from polaris.core.probes.bus import ProbeBus  # noqa: E402
from polaris.core.probes.engine import ExitEngine  # noqa: E402
