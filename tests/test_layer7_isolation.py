"""Layer 7 — Strategy Isolation Primitives unit + property tests.

Spec source: vault/30_components/layer-7-strategy-isolation.md.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.isolation import (
    ACTIVE,
    FAULT_EXCEPTION,
    FAULT_NAN,
    FAULT_REJECT,
    FAULT_STALE_DATA,
    HARD_HALT,
    ISOLATION_TABLES,
    RISK_ONLY,
    SOFT_HALT,
    AccountSnapshot,
    AllocationRequest,
    AllocatorFence,
    FaultEvent,
    IdempotencyConflictError,
    ReservationConflictError,
    StrategyMode,
    StrategyNamespace,
    build_account_snapshot,
    build_order_key,
    compute_4_state_transition,
    current_strategy_mode,
    payload_hash,
    record_fault,
    register_order_intent,
    reset_strategy_halt,
    run_strategy_task,
    should_allow_new_entry,
    supervise_strategies,
)

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Order keys
# ---------------------------------------------------------------------------


def test_build_order_key_canonical() -> None:
    key = build_order_key(
        strategy_id="volume_burst",
        venue="okx",
        symbol="BTC-USDT",
        timeframe="1m",
        signal_ts=NOW,
        side="buy",
    )
    assert key == f"volume_burst:okx:BTC-USDT:1m:{NOW}:buy"


def test_build_order_key_validates() -> None:
    with pytest.raises(ValueError):
        build_order_key(
            strategy_id="",
            venue="okx",
            symbol="BTC-USDT",
            timeframe="1m",
            signal_ts=NOW,
            side="buy",
        )
    with pytest.raises(ValueError):
        build_order_key(
            strategy_id="x",
            venue="okx",
            symbol="BTC-USDT",
            timeframe="1m",
            signal_ts=NOW,
            side="bogus",
        )


def test_payload_hash_stable_across_dict_order() -> None:
    a = {"qty": 1.0, "px": 60_000, "side": "buy"}
    b = {"side": "buy", "px": 60_000, "qty": 1.0}
    assert payload_hash(a) == payload_hash(b)


def test_register_order_intent_idempotent_same_payload(memdb: sqlite3.Connection) -> None:
    key = build_order_key(
        strategy_id="vb", venue="okx", symbol="BTC-USDT", timeframe="1m", signal_ts=NOW, side="buy"
    )
    h = payload_hash({"qty": 1.0, "px": 60_000, "side": "buy"})
    out1 = register_order_intent(
        memdb, order_key=key, strategy_id="vb", venue="okx", symbol="BTC-USDT",
        timeframe="1m", signal_ts=NOW, side="buy", payload_hash_value=h, now_ts=NOW,
    )
    out2 = register_order_intent(
        memdb, order_key=key, strategy_id="vb", venue="okx", symbol="BTC-USDT",
        timeframe="1m", signal_ts=NOW, side="buy", payload_hash_value=h, now_ts=NOW + 1,
    )
    assert out1["dedupe_hit"] is False
    assert out2["dedupe_hit"] is True


def test_idempotent_order_key_conflict_records_fault(memdb: sqlite3.Connection) -> None:
    """Same key + different payload → IdempotencyConflictError + fault row written."""
    key = build_order_key(
        strategy_id="vb", venue="okx", symbol="BTC-USDT", timeframe="1m", signal_ts=NOW, side="buy"
    )
    h_a = payload_hash({"qty": 1.0})
    h_b = payload_hash({"qty": 2.0})
    register_order_intent(
        memdb, order_key=key, strategy_id="vb", venue="okx", symbol="BTC-USDT",
        timeframe="1m", signal_ts=NOW, side="buy", payload_hash_value=h_a, now_ts=NOW,
    )
    with pytest.raises(IdempotencyConflictError):
        register_order_intent(
            memdb, order_key=key, strategy_id="vb", venue="okx", symbol="BTC-USDT",
            timeframe="1m", signal_ts=NOW, side="buy", payload_hash_value=h_b, now_ts=NOW + 1,
        )
    n_faults = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id = 'vb' AND fault_type = 'idempotency_conflict'"
    ).fetchone()[0]
    assert n_faults == 1


# ---------------------------------------------------------------------------
# Circuit breaker — 4-state transitions
# ---------------------------------------------------------------------------


def test_circuit_breaker_4_state_transition_nan_immediate_hard() -> None:
    out = compute_4_state_transition(
        fault_type=FAULT_NAN,
        rolling_faults=[FaultEvent(FAULT_NAN, NOW)],
        now_ts=NOW,
    )
    assert out == HARD_HALT


def test_circuit_breaker_4_state_transition_nan_with_open_positions_risk_only() -> None:
    out = compute_4_state_transition(
        fault_type=FAULT_NAN,
        rolling_faults=[FaultEvent(FAULT_NAN, NOW)],
        now_ts=NOW,
        has_open_positions=True,
    )
    assert out == RISK_ONLY


def test_circuit_breaker_exception_threshold_3_300() -> None:
    history = [FaultEvent(FAULT_EXCEPTION, NOW - i) for i in (0, 1, 2)]
    out = compute_4_state_transition(
        fault_type=FAULT_EXCEPTION, rolling_faults=history, now_ts=NOW
    )
    assert out == HARD_HALT
    # 2 events = below threshold → ACTIVE
    out_below = compute_4_state_transition(
        fault_type=FAULT_EXCEPTION, rolling_faults=history[:2], now_ts=NOW
    )
    assert out_below == ACTIVE


def test_circuit_breaker_reject_threshold_3_600_soft() -> None:
    history = [FaultEvent(FAULT_REJECT, NOW - i) for i in (0, 1, 2)]
    out = compute_4_state_transition(
        fault_type=FAULT_REJECT, rolling_faults=history, now_ts=NOW
    )
    assert out == SOFT_HALT


def test_circuit_breaker_stale_data_immediate_soft() -> None:
    out = compute_4_state_transition(
        fault_type=FAULT_STALE_DATA, rolling_faults=[], now_ts=NOW
    )
    assert out == SOFT_HALT


def test_circuit_breaker_window_excludes_old_events() -> None:
    """Exception events outside the 300s window must not count."""
    too_old = [FaultEvent(FAULT_EXCEPTION, NOW - 600) for _ in range(3)]
    out = compute_4_state_transition(
        fault_type=FAULT_EXCEPTION, rolling_faults=too_old, now_ts=NOW
    )
    assert out == ACTIVE


# ---------------------------------------------------------------------------
# Circuit breaker — DB integration
# ---------------------------------------------------------------------------


def test_record_fault_opens_hard_halt(memdb: sqlite3.Connection) -> None:
    for i in range(3):
        record_fault(memdb, strategy_id="vb", fault_type=FAULT_EXCEPTION, now_ts=NOW + i)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 5) == HARD_HALT
    assert should_allow_new_entry(memdb, "vb", now_ts=NOW + 5) is False


def test_record_fault_nan_immediately_halts(memdb: sqlite3.Connection) -> None:
    record_fault(memdb, strategy_id="vb", fault_type=FAULT_NAN, now_ts=NOW)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 1) == HARD_HALT


def test_soft_halt_auto_unblock(memdb: sqlite3.Connection) -> None:
    for i in range(3):
        record_fault(memdb, strategy_id="vb", fault_type=FAULT_REJECT, now_ts=NOW + i)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 10) == SOFT_HALT
    # SOFT_HALT auto-unblock = 15min from the moment the halt opened (last fault was NOW+2).
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 2 + 900 + 1) == ACTIVE


def test_reset_strategy_halt(memdb: sqlite3.Connection) -> None:
    record_fault(memdb, strategy_id="vb", fault_type=FAULT_NAN, now_ts=NOW)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 1) == HARD_HALT
    n = reset_strategy_halt(memdb, "vb", operator="jin", now_ts=NOW + 100)
    assert n == 1
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 200) == ACTIVE


# ---------------------------------------------------------------------------
# Namespace isolation
# ---------------------------------------------------------------------------


def test_namespace_isolation_strategy_a_does_not_leak_into_b(memdb: sqlite3.Connection) -> None:
    # Strategy A registers an intent + records a fault.
    register_order_intent(
        memdb,
        order_key=build_order_key(
            strategy_id="A", venue="okx", symbol="BTC-USDT",
            timeframe="1m", signal_ts=NOW, side="buy"
        ),
        strategy_id="A", venue="okx", symbol="BTC-USDT",
        timeframe="1m", signal_ts=NOW, side="buy",
        payload_hash_value=payload_hash({"qty": 1.0}),
        now_ts=NOW,
    )
    record_fault(memdb, strategy_id="A", fault_type=FAULT_NAN, now_ts=NOW)

    ns_a = StrategyNamespace(memdb, "A")
    ns_b = StrategyNamespace(memdb, "B")

    assert len(ns_a.list_open_intents()) == 1
    assert len(ns_b.list_open_intents()) == 0
    assert len(ns_a.list_recent_faults(since_ts=NOW - 60)) == 1
    assert len(ns_b.list_recent_faults(since_ts=NOW - 60)) == 0
    # Halts are also strategy-scoped.
    assert current_strategy_mode(memdb, "A", now_ts=NOW + 1) == HARD_HALT
    assert current_strategy_mode(memdb, "B", now_ts=NOW + 1) == ACTIVE


def test_isolation_tables_constant_present() -> None:
    assert "strategy_halts" in ISOLATION_TABLES
    assert "strategy_fault_events" in ISOLATION_TABLES
    assert "allocator_reservations" in ISOLATION_TABLES
    assert "order_intents" in ISOLATION_TABLES


# ---------------------------------------------------------------------------
# Allocator fence — race condition / TTL
# ---------------------------------------------------------------------------


def _alloc_req(strategy_id: str, order_key: str) -> AllocationRequest:
    return AllocationRequest(
        strategy_id=strategy_id,
        venue="okx",
        symbol="BTC-USDT",
        side="buy",
        correlation_group="BTC-cluster",
        underlying_group_id="crypto:BTC",
        requested_notional=1000.0,
        requested_risk=20.0,
        signal_id="sig-1",
        order_key=order_key,
    )


def test_allocator_fence_basic_reservation(memdb: sqlite3.Connection) -> None:
    fence = AllocatorFence(memdb)
    req = _alloc_req("A", "A:okx:BTC-USDT:1m:0:buy")

    async def go() -> dict:
        return await fence.check_and_reserve(req, now_ts=NOW)

    out = asyncio.run(go())
    assert out["expires_ts"] > NOW
    assert out["dedupe_hit"] is False


def test_allocator_fence_race_condition(memdb: sqlite3.Connection) -> None:
    """Two distinct strategies can reserve in parallel without races."""
    fence = AllocatorFence(memdb)
    req_a = _alloc_req("A", "A:okx:BTC-USDT:1m:0:buy")
    req_b = _alloc_req("B", "B:okx:ETH-USDT:1m:0:buy")

    async def go() -> tuple[dict, dict]:
        a, b = await asyncio.gather(
            fence.check_and_reserve(req_a, now_ts=NOW),
            fence.check_and_reserve(req_b, now_ts=NOW),
        )
        return a, b

    a, b = asyncio.run(go())
    assert a["reservation_id"] != b["reservation_id"]
    assert a["dedupe_hit"] is False
    assert b["dedupe_hit"] is False
    rows = memdb.execute(
        "SELECT strategy_id, status FROM allocator_reservations"
    ).fetchall()
    assert len(rows) == 2
    assert all(s == "pending" for _, s in rows)


def test_allocator_fence_duplicate_order_key_different_strategy_conflict(
    memdb: sqlite3.Connection,
) -> None:
    fence = AllocatorFence(memdb)
    same_key = "shared:okx:BTC-USDT:1m:0:buy"
    req_a = _alloc_req("A", same_key)
    req_b = _alloc_req("B", same_key)

    async def go() -> None:
        await fence.check_and_reserve(req_a, now_ts=NOW)
        with pytest.raises(ReservationConflictError):
            await fence.check_and_reserve(req_b, now_ts=NOW)

    asyncio.run(go())


def test_allocator_fence_ttl_expiry(memdb: sqlite3.Connection) -> None:
    fence = AllocatorFence(memdb)
    req = _alloc_req("A", "A:okx:BTC-USDT:1m:0:buy")

    async def go() -> int:
        await fence.check_and_reserve(req, now_ts=NOW)
        return await fence.expire_stale_reservations(now_ts=NOW + 10)

    expired = asyncio.run(go())
    assert expired == 1
    row = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE order_key = ?", (req.order_key,)
    ).fetchone()
    assert row[0] == "expired"


def test_allocator_fence_confirm_then_release(memdb: sqlite3.Connection) -> None:
    fence = AllocatorFence(memdb)
    req = _alloc_req("A", "A:okx:BTC-USDT:1m:0:buy")

    async def go() -> None:
        out = await fence.check_and_reserve(req, now_ts=NOW)
        await fence.confirm_reservation(
            out["reservation_id"], venue_order_ref="okx-123", now_ts=NOW + 1
        )
        await fence.release_reservation(
            out["reservation_id"], reason="filled", now_ts=NOW + 2
        )

    asyncio.run(go())
    row = memdb.execute(
        "SELECT status, venue_order_ref FROM allocator_reservations WHERE order_key = ?",
        (req.order_key,),
    ).fetchone()
    assert row == ("released", "okx-123")


# ---------------------------------------------------------------------------
# Worker / supervisor
# ---------------------------------------------------------------------------


class _OkStrategy:
    strategy_id = "ok"

    def __init__(self) -> None:
        self.calls = 0

    async def tick(self, snapshot: AccountSnapshot) -> None:
        self.calls += 1


class _BoomStrategy:
    strategy_id = "boom"

    async def tick(self, snapshot: AccountSnapshot) -> None:
        raise RuntimeError("kapow")


def test_supervisor_runs_ok_strategy_and_records_fault_for_boom(
    memdb: sqlite3.Connection,
) -> None:
    ok = _OkStrategy()
    boom = _BoomStrategy()
    snap = build_account_snapshot(now_ts=NOW)

    async def supplier() -> AccountSnapshot:
        return snap

    results = asyncio.run(
        supervise_strategies(
            [ok, boom],
            supplier,
            conn=memdb,
            now_ts_provider=lambda: NOW,
        )
    )
    assert ok.calls == 1
    by_id = {r["strategy_id"]: r for r in results}
    assert by_id["ok"]["mode"] == ACTIVE
    assert by_id["boom"]["exception"] is not None
    fault_rows = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id = 'boom'"
    ).fetchone()[0]
    assert fault_rows == 1


def test_run_strategy_task_skips_when_halted(memdb: sqlite3.Connection) -> None:
    record_fault(memdb, strategy_id="ok", fault_type=FAULT_NAN, now_ts=NOW)
    snap = build_account_snapshot(now_ts=NOW + 1)
    ok = _OkStrategy()

    async def go() -> dict:
        return await run_strategy_task(ok, snap, conn=memdb, now_ts=NOW + 1)

    out = asyncio.run(go())
    assert out["mode"] == HARD_HALT
    assert ok.calls == 0


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


_FAULT_TYPES = st.sampled_from([FAULT_EXCEPTION, FAULT_REJECT, FAULT_NAN, FAULT_STALE_DATA, "unknown"])


@settings(max_examples=120, deadline=None)
@given(
    fault_type=_FAULT_TYPES,
    history_size=st.integers(min_value=0, max_value=10),
    has_pos=st.booleans(),
)
def test_property_compute_4_state_transition_membership(
    fault_type: str, history_size: int, has_pos: bool
) -> None:
    history = [FaultEvent(fault_type=fault_type, event_ts=NOW - i) for i in range(history_size)]
    out = compute_4_state_transition(
        fault_type=fault_type,
        rolling_faults=history,
        now_ts=NOW,
        has_open_positions=has_pos,
    )
    assert out in {ACTIVE, SOFT_HALT, HARD_HALT, RISK_ONLY}


def test_property_state_returns_valid_string_type() -> None:
    """Sanity: StrategyMode literal covers every emitted value."""
    valid: set[StrategyMode] = {ACTIVE, SOFT_HALT, HARD_HALT, RISK_ONLY}
    for f in (FAULT_EXCEPTION, FAULT_REJECT, FAULT_NAN, FAULT_STALE_DATA):
        out = compute_4_state_transition(
            fault_type=f, rolling_faults=[FaultEvent(f, NOW)] * 5, now_ts=NOW
        )
        assert out in valid


# ---------------------------------------------------------------------------
# Codex round 2 — monotonic severity + RISK_ONLY exit hook + singleton fence
# ---------------------------------------------------------------------------


def test_hard_halt_cannot_be_downgraded_by_soft(memdb: sqlite3.Connection) -> None:
    """Open HARD_HALT must persist even if subsequent rejects threshold to SOFT."""
    record_fault(memdb, strategy_id="vb", fault_type=FAULT_NAN, now_ts=NOW)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 1) == HARD_HALT
    # Three rejects would normally trip SOFT_HALT — must NOT downgrade HARD.
    for i in range(3):
        record_fault(memdb, strategy_id="vb", fault_type=FAULT_REJECT, now_ts=NOW + 10 + i)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 100) == HARD_HALT
    # And after the 15-min soft auto-unblock window the strategy must STILL be hard-halted.
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 2 * 3600) == HARD_HALT


def test_soft_halt_upgrade_to_hard_clears_soft(memdb: sqlite3.Connection) -> None:
    """SOFT → HARD upgrade must close the soft row so severity is unambiguous."""
    for i in range(3):
        record_fault(memdb, strategy_id="vb", fault_type=FAULT_REJECT, now_ts=NOW + i)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 5) == SOFT_HALT
    record_fault(memdb, strategy_id="vb", fault_type=FAULT_NAN, now_ts=NOW + 10)
    assert current_strategy_mode(memdb, "vb", now_ts=NOW + 11) == HARD_HALT
    open_rows = memdb.execute(
        "SELECT mode FROM strategy_halts WHERE strategy_id = 'vb' AND reset_ts IS NULL"
    ).fetchall()
    # Only the HARD row should remain open.
    assert {r[0] for r in open_rows} == {HARD_HALT}


def test_risk_only_runs_tick_risk_only_hook(memdb: sqlite3.Connection) -> None:
    """In RISK_ONLY the supervisor must call ``tick_risk_only`` (entry tick stays off)."""

    class _RiskAware:
        strategy_id = "ra"

        def __init__(self) -> None:
            self.tick_calls = 0
            self.risk_calls = 0

        async def tick(self, snapshot: AccountSnapshot) -> None:
            self.tick_calls += 1

        async def tick_risk_only(self, snapshot: AccountSnapshot) -> None:
            self.risk_calls += 1

    # Open a RISK_ONLY halt for the strategy (NaN with open positions).
    record_fault(
        memdb, strategy_id="ra", fault_type=FAULT_NAN, now_ts=NOW, has_open_positions=True
    )
    assert current_strategy_mode(memdb, "ra", now_ts=NOW + 1) == RISK_ONLY

    snap = build_account_snapshot(now_ts=NOW + 1)
    s = _RiskAware()
    out = asyncio.run(run_strategy_task(s, snap, conn=memdb, now_ts=NOW + 1))
    assert out["mode"] == RISK_ONLY
    assert s.tick_calls == 0
    assert s.risk_calls == 1


def test_risk_only_no_hook_skips_quietly(memdb: sqlite3.Connection) -> None:
    """Strategies without a ``tick_risk_only`` hook must skip cleanly (no exception path)."""

    class _OnlyTick:
        strategy_id = "ot"

        def __init__(self) -> None:
            self.calls = 0

        async def tick(self, snapshot: AccountSnapshot) -> None:
            self.calls += 1

    record_fault(
        memdb, strategy_id="ot", fault_type=FAULT_NAN, now_ts=NOW, has_open_positions=True
    )
    snap = build_account_snapshot(now_ts=NOW + 1)
    s = _OnlyTick()
    out = asyncio.run(run_strategy_task(s, snap, conn=memdb, now_ts=NOW + 1))
    assert out["mode"] == RISK_ONLY
    assert out["exception"] is None
    assert s.calls == 0


def test_get_process_fence_singleton(memdb: sqlite3.Connection) -> None:
    from polaris.core.isolation import get_process_fence, reset_process_fence

    reset_process_fence()
    a = get_process_fence(memdb)
    b = get_process_fence(memdb)
    assert a is b


def test_get_process_fence_rejects_different_conn(memdb: sqlite3.Connection) -> None:
    import sqlite3 as _sqlite

    from polaris.core.isolation import get_process_fence, reset_process_fence

    reset_process_fence()
    get_process_fence(memdb)
    other = _sqlite.connect(":memory:")
    try:
        with pytest.raises(ReservationConflictError):
            get_process_fence(other)
    finally:
        other.close()
        reset_process_fence()


def test_positions_table_exists(memdb: sqlite3.Connection) -> None:
    """Layer 7 spec required positions/orders/risk_events DDL — must be in ALL_DDL now."""
    tables = {
        r[0]
        for r in memdb.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "positions" in tables
    assert "orders" in tables
    assert "risk_events" in tables
