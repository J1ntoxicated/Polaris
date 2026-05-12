"""Layer 2 — Signal lifecycle state + GateContext / GateResult dataclasses.

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q1 orchestrator + Q3 model split)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (8-gate state machine)

Lifecycle states:
    RAW → VALIDATED → WATCHED → SIZED → ACTIVE → MONITORED → EXIT_PENDING
        → CLOSED → REFLECTED

A signal is RAW when emitted by Gate 2; transitions happen as each gate
returns its decision. Failure (KILL / fail-closed) terminates the lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final


class SignalLifecycle(StrEnum):
    """Per-signal lifecycle states (one-way transitions)."""

    RAW = "RAW"
    VALIDATED = "VALIDATED"
    WATCHED = "WATCHED"
    SIZED = "SIZED"
    ACTIVE = "ACTIVE"
    MONITORED = "MONITORED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    REFLECTED = "REFLECTED"
    KILLED = "KILLED"


class GateDecision(StrEnum):
    """Allowed gate decisions (cross-gate enum)."""

    PASS = "PASS"
    KILL = "KILL"
    MODIFY = "MODIFY"
    PROCEED = "PROCEED"
    HOLD = "HOLD"
    ADJUST_EXIT = "ADJUST_EXIT"
    EXIT_NOW = "EXIT_NOW"
    SWAP_STRATEGY = "SWAP_STRATEGY"
    SIZED = "SIZED"
    REFLECTED = "REFLECTED"
    SKIPPED = "SKIPPED"  # fast-path bypass


# Gate IDs (1-8) — must match the L2 spec table.
GATE_UNIVERSE_SCANNER: Final[int] = 1
GATE_STRATEGY_SIGNAL: Final[int] = 2
GATE_SIGNAL_VALIDATOR: Final[int] = 3
GATE_PRE_ENTRY_WATCHER: Final[int] = 4
GATE_ENTRY_SIZER: Final[int] = 5
GATE_POSITION_MONITOR: Final[int] = 6
GATE_ADAPTIVE_EXIT: Final[int] = 7
GATE_POST_TRADE_REFLECTOR: Final[int] = 8

# Gates that fail-CLOSED (entry-creation): 3 / 4 / 5.
ENTRY_GATES: Final[frozenset[int]] = frozenset(
    {GATE_SIGNAL_VALIDATOR, GATE_PRE_ENTRY_WATCHER, GATE_ENTRY_SIZER}
)

# Gates that fail-OPEN (position management): 6 / 7 / 8.
POSITION_GATES: Final[frozenset[int]] = frozenset(
    {GATE_POSITION_MONITOR, GATE_ADAPTIVE_EXIT, GATE_POST_TRADE_REFLECTOR}
)


@dataclass(slots=True)
class GateContext:
    """Mutable per-signal context propagated across gates.

    Stays mutable so each gate can stamp results without rebuilding the dict;
    the orchestrator snapshots ``payload`` to ``gate_events`` after each gate.
    """

    run_id: str
    signal_id: str | None
    position_id: str | None
    gate_id: int
    venue: str
    symbol: str
    strategy_id: str | None
    payload: dict[str, Any]
    started_ts: int
    state: SignalLifecycle = SignalLifecycle.RAW


@dataclass(frozen=True, slots=True)
class GateResult:
    """Immutable gate output (one event per gate execution)."""

    decision: GateDecision
    next_gate: int | None
    payload: dict[str, Any] = field(default_factory=dict)
    model_used: str = "python"  # "gpt" / "python" / "gpt_p1" / "python_fast_path"
    latency_ms: int = 0
    error: str | None = None
    skipped: bool = False
