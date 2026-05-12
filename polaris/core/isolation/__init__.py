"""Layer 7 — Strategy Isolation Primitives.

Spec source: vault/30_components/layer-7-strategy-isolation.md.

P0 = asyncio task per strategy + central allocator (single global Lock) +
central circuit breaker (4-state) + unified-table namespace + idempotent order
keys. Process boundary promotion is P1 (gated on hard-halt cadence).
"""

from polaris.core.isolation.allocator_fence import (
    ALLOCATOR_RESERVATION_TTL_SEC,
    AllocationRequest,
    AllocatorFence,
    ReservationConflictError,
    get_process_fence,
    reset_process_fence,
)
from polaris.core.isolation.circuit_breaker import (
    ACTIVE,
    CB_EXCEPTION_THRESHOLD,
    CB_NAN_THRESHOLD,
    CB_REJECT_THRESHOLD,
    CB_SOFT_HALT_AUTO_UNBLOCK_SEC,
    CB_STALE_DATA_BY_TIMEFRAME,
    FAULT_EXCEPTION,
    FAULT_NAN,
    FAULT_REJECT,
    FAULT_STALE_DATA,
    HARD_HALT,
    RISK_ONLY,
    SOFT_HALT,
    FaultEvent,
    StrategyMode,
    compute_4_state_transition,
    current_strategy_mode,
    record_fault,
    reset_strategy_halt,
    should_allow_new_entry,
)
from polaris.core.isolation.namespace import (
    ISOLATION_TABLES,
    StrategyNamespace,
    list_open_intents,
    list_open_positions,
    list_recent_faults,
)
from polaris.core.isolation.order_keys import (
    IdempotencyConflictError,
    build_order_key,
    payload_hash,
    register_order_intent,
    resolve_duplicate_intent,
)
from polaris.core.isolation.worker import (
    AccountSnapshot,
    PipelineTaskSpec,
    StrategyProtocol,
    build_account_snapshot,
    run_strategy_task,
    supervise_pipeline_tasks,
    supervise_strategies,
)

__all__ = [
    "ACTIVE",
    "ALLOCATOR_RESERVATION_TTL_SEC",
    "AccountSnapshot",
    "AllocationRequest",
    "AllocatorFence",
    "CB_EXCEPTION_THRESHOLD",
    "CB_NAN_THRESHOLD",
    "CB_REJECT_THRESHOLD",
    "CB_SOFT_HALT_AUTO_UNBLOCK_SEC",
    "CB_STALE_DATA_BY_TIMEFRAME",
    "FAULT_EXCEPTION",
    "FAULT_NAN",
    "FAULT_REJECT",
    "FAULT_STALE_DATA",
    "FaultEvent",
    "HARD_HALT",
    "ISOLATION_TABLES",
    "IdempotencyConflictError",
    "PipelineTaskSpec",
    "RISK_ONLY",
    "ReservationConflictError",
    "SOFT_HALT",
    "StrategyMode",
    "StrategyNamespace",
    "StrategyProtocol",
    "build_account_snapshot",
    "build_order_key",
    "compute_4_state_transition",
    "current_strategy_mode",
    "get_process_fence",
    "list_open_intents",
    "list_open_positions",
    "list_recent_faults",
    "payload_hash",
    "record_fault",
    "register_order_intent",
    "reset_process_fence",
    "reset_strategy_halt",
    "resolve_duplicate_intent",
    "run_strategy_task",
    "should_allow_new_entry",
    "supervise_pipeline_tasks",
    "supervise_strategies",
]
