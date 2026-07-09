"""Layer 6 — Live Recalc stub tests (TDD).

Spec coverage:
  - vault/30_components/layer-6-live-recalc.md (Q1-Q5)
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polaris.core.live_recalc import (
    CONVICTION_LAYER_MULTS,
    CONVICTION_MAX_LAYERS,
    LIVE_RECALC_CADENCE_SEC,
    SWAP_MAX_PER_TRADE,
    ConvictionGateInputs,
    SwapCandidate,
    can_stack_conviction,
    classify_regime,
    compute_stack_size_mult,
    count_layers,
    detect_regime_flip,
    evaluate_strategy_swap,
    fetch_regime,
    mark_position_dirty,
    mark_positions_dirty,
    recompute_exit_params,
    run_live_recalc_cycle,
    should_run_recalc,
)
from polaris.storage.schema_ddl_ext import DDL_POSITION_LIVE_RECALC_STATE

# ---------------------------------------------------------------------------
# tick_recalc — dirty trigger + 5s cadence
# ---------------------------------------------------------------------------


def test_dirty_trigger_5s_cadence(memdb: sqlite3.Connection) -> None:
    """Dirty mark + ``should_run_recalc`` returns True; 5s cadence enforced."""
    mark_position_dirty(memdb, position_id="p1", reason="regime_flip", now_ts=100)
    state = memdb.execute(
        "SELECT position_id, last_check_ts, dirty_reason, dirty_ts, "
        "cooldown_until_ts FROM position_live_recalc_state WHERE position_id='p1'"
    ).fetchone()
    state_dict = {
        "position_id": state[0],
        "last_check_ts": state[1],
        "dirty_reason": state[2],
        "dirty_ts": state[3],
        "cooldown_until_ts": state[4],
    }
    assert should_run_recalc(state_dict, now_ts=110) is True

    # Run recalc → dirty cleared + last_check_ts advanced.
    log = recompute_exit_params(memdb, position={"position_id": "p1"}, now_ts=110)
    assert log.decision == "would_recalc"
    assert log.last_check_ts == 110

    # Within cadence floor + clean → skipped_clean.
    state2 = memdb.execute(
        "SELECT position_id, last_check_ts, dirty_reason, dirty_ts, "
        "cooldown_until_ts FROM position_live_recalc_state WHERE position_id='p1'"
    ).fetchone()
    sd2 = {
        "position_id": state2[0],
        "last_check_ts": state2[1],
        "dirty_reason": state2[2],
        "dirty_ts": state2[3],
        "cooldown_until_ts": state2[4],
    }
    assert should_run_recalc(sd2, now_ts=111) is False  # not dirty


def test_cadence_constant_is_5s() -> None:
    assert LIVE_RECALC_CADENCE_SEC == 5


def test_run_live_recalc_cycle_async(memdb: sqlite3.Connection) -> None:
    mark_position_dirty(memdb, position_id="p1", reason="pnl_break", now_ts=100)
    mark_position_dirty(memdb, position_id="p2", reason="regime_flip", now_ts=100)
    out = asyncio.run(
        run_live_recalc_cycle(
            memdb, now_ts=120,
            active_positions=[{"position_id": "p1"}, {"position_id": "p2"}],
        )
    )
    assert len(out) == 2
    assert all(r.decision == "would_recalc" for r in out)


# ---------------------------------------------------------------------------
# mark_positions_dirty — batch form (writer-migration-completion design #3):
# hoists per-position dirty marks out of the recalc sweep loop, collapsing
# N lock acquisitions into 1 multi-row INSERT/COMMIT.
# ---------------------------------------------------------------------------


def _recalc_state_snapshot(conn: sqlite3.Connection) -> dict[str, tuple[str | None, int | None, int]]:
    rows = conn.execute(
        "SELECT position_id, dirty_reason, dirty_ts, last_check_ts "
        "FROM position_live_recalc_state"
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def test_mark_positions_dirty_equivalent_to_individual_calls(
    memdb: sqlite3.Connection,
) -> None:
    """N batched marks == N individual mark_position_dirty calls, including
    within-batch duplicate position_id -> most-recent (last) entry wins."""
    entries = [
        ("p1", "reason_a", 100),
        ("p2", "reason_b", 100),
        ("p1", "reason_c", 200),  # duplicate p1 — must win over reason_a
    ]
    for position_id, reason, ts in entries:
        mark_position_dirty(memdb, position_id=position_id, reason=reason, now_ts=ts)
    individual = _recalc_state_snapshot(memdb)

    memdb.execute("DELETE FROM position_live_recalc_state")
    mark_positions_dirty(memdb, entries)
    batched = _recalc_state_snapshot(memdb)

    assert batched == individual
    # most-recent (last) entry's dirty_reason/dirty_ts wins for a duplicate
    # position_id — last_check_ts is untouched by ON CONFLICT (only set on
    # the row's first insert), matching mark_position_dirty's own semantics.
    assert batched["p1"] == ("reason_c", 200, 100)


def test_mark_positions_dirty_read_your_writes(memdb: sqlite3.Connection) -> None:
    """Same-conn SELECT sees every mark immediately after the flush call."""
    entries = [("p1", "tick_5s_g6", 100), ("p2", "tick_5s_g6", 100), ("p3", "tick_5s_g6", 100)]
    mark_positions_dirty(memdb, entries)
    seen = {r[0] for r in memdb.execute(
        "SELECT position_id FROM position_live_recalc_state"
    ).fetchall()}
    assert seen == {"p1", "p2", "p3"}
    state = memdb.execute(
        "SELECT dirty_reason, dirty_ts FROM position_live_recalc_state WHERE position_id='p2'"
    ).fetchone()
    assert state == ("tick_5s_g6", 100)


def test_mark_positions_dirty_empty_entries_is_noop(memdb: sqlite3.Connection) -> None:
    mark_positions_dirty(memdb, [])  # must not raise / must not touch the table
    count = memdb.execute("SELECT COUNT(*) FROM position_live_recalc_state").fetchone()[0]
    assert count == 0


def test_mark_positions_dirty_single_insert_statement(memdb: sqlite3.Connection) -> None:
    """Collapses N lock acquisitions into 1 — asserted via exactly one INSERT
    statement issued for an N-row batch (not a loop of N single-row inserts)."""
    statements: list[str] = []
    memdb.set_trace_callback(lambda sql: statements.append(sql))
    try:
        mark_positions_dirty(
            memdb,
            [(f"p{i}", "tick_5s_g6", 100) for i in range(10)],
        )
    finally:
        memdb.set_trace_callback(None)
    insert_stmts = [s for s in statements if s.strip().upper().startswith("INSERT")]
    assert len(insert_stmts) == 1


@given(
    entries=st.lists(
        st.tuples(
            st.sampled_from(["p1", "p2", "p3", "p4"]),
            st.text(alphabet="abcde", min_size=1, max_size=6),
            st.integers(min_value=1, max_value=10_000),
        ),
        min_size=0, max_size=15,
    ),
)
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_mark_positions_dirty_repeated_flush_idempotent(
    entries: list[tuple[str, str, int]],
) -> None:
    """Flushing the SAME batch twice in a row is idempotent (final state
    equals a single flush) — each example gets its own isolated in-memory DB."""
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.execute(DDL_POSITION_LIVE_RECALC_STATE)
    try:
        mark_positions_dirty(conn, entries)
        once = _recalc_state_snapshot(conn)
        mark_positions_dirty(conn, entries)
        twice = _recalc_state_snapshot(conn)
        assert once == twice
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# regime_flip — 2 consecutive close confirm + immediate crisis
# ---------------------------------------------------------------------------


def test_regime_flip_2_consecutive_close(memdb: sqlite3.Connection) -> None:
    # initial seed
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=100,
    )
    # candidate=chop, first close → pending
    d1 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="chop", now_ts=200,
    )
    assert d1.confirmed is False
    assert d1.consecutive_count == 1
    # second close → flip
    d2 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="chop", now_ts=300,
    )
    assert d2.confirmed is True
    assert d2.reason == "confirmed_2x"
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "chop"


def test_regime_flip_crisis_immediate(memdb: sqlite3.Connection) -> None:
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=100,
    )
    d = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="crisis", now_ts=200,
    )
    assert d.confirmed is True
    assert d.reason == "immediate_crisis"
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "crisis"


def test_regime_flip_initial_seed_not_confirmed(memdb: sqlite3.Connection) -> None:
    """codex Day 4 R1 P2 fix: bootstrap must not look like a confirmed flip."""
    d = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=100,
    )
    assert d.confirmed is False
    assert d.reason == "initial_seed"
    # row should be persisted however
    assert fetch_regime(memdb, venue="okx", underlying_group_id="crypto:BTC") == "bull_trend"


def test_regime_flip_resets_when_candidate_changes(memdb: sqlite3.Connection) -> None:
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=100,
    )
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="chop", now_ts=200,
    )
    # different candidate resets count.
    d3 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bear_trend", now_ts=300,
    )
    assert d3.consecutive_count == 1
    assert d3.confirmed is False


def test_classify_regime_validates_enum() -> None:
    with pytest.raises(ValueError):
        classify_regime(
            venue="okx", underlying_group_id="crypto:BTC",
            candidate_regime="rocket_mode",
        )


# ---------------------------------------------------------------------------
# strategy_swap — max 1 per trade + correlation guard
# ---------------------------------------------------------------------------


def _seed_position(conn: sqlite3.Connection, pid: str = "pos1") -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, swap_count) VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', "
        "'volume_burst', 'volume_burst', 'volume_burst', 'long', 1.0, 'OPEN', "
        "100, 0)",
        (pid,),
    )


def test_strategy_swap_max_1_per_trade(memdb: sqlite3.Connection) -> None:
    _seed_position(memdb)
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="volume_burst",
        to_strategy_id="tsmom",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    # First swap → applied.
    d1 = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d1.decision == "applied"
    assert d1.swap_count_after == 1
    # Second swap (now from active=tsmom) → must hit the per-trade cap.
    cand_next = SwapCandidate(
        position_id="pos1",
        from_strategy_id="tsmom",  # post-first-swap active
        to_strategy_id="spot_donchian",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d2 = evaluate_strategy_swap(memdb, candidate=cand_next, now_ts=300, apply=True)
    assert d2.decision == "skipped_cap"
    assert SWAP_MAX_PER_TRADE == 1


def test_strategy_swap_position_mismatch_blocks(memdb: sqlite3.Connection) -> None:
    """codex Day 4 R1 P1 fix: candidate venue/symbol/side must match position row."""
    _seed_position(memdb)
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="volume_burst",
        to_strategy_id="spot_donchian",
        venue="okx",
        symbol="ETH-USDT",  # mismatched symbol
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d.decision == "skipped_position_mismatch"
    row = memdb.execute(
        "SELECT active_strategy_id, swap_count FROM positions WHERE position_id='pos1'"
    ).fetchone()
    assert row[0] == "volume_burst"  # untouched
    assert int(row[1] or 0) == 0


def test_strategy_swap_same_correlation_group_only(memdb: sqlite3.Connection) -> None:
    _seed_position(memdb)
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="volume_burst",
        to_strategy_id="rsi_bb_pullback",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_mean_reversion",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d.decision == "skipped_correlation_mismatch"
    # active_strategy_id unchanged
    row = memdb.execute(
        "SELECT active_strategy_id, swap_count FROM positions WHERE position_id='pos1'"
    ).fetchone()
    assert row[0] == "volume_burst"
    assert int(row[1] or 0) == 0


def test_legacy_positions_table_migrates_swap_columns(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """codex Day 4 R4 fix: pre-Day-4 ``positions`` rows must gain swap columns.

    A real upgrade path can land on an existing DB whose ``positions`` table
    predates Layer 6 Q3 invariants. ``init_db()`` must apply idempotent
    ALTERs adding ``entry_strategy_id``, ``active_strategy_id``,
    ``underlying_group_id`` and ``swap_count``.
    """
    from polaris.storage.schema import init_db  # noqa: PLC0415

    db_path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(str(db_path), isolation_level=None)
    legacy.execute(
        "CREATE TABLE positions ("
        " position_id TEXT PRIMARY KEY,"
        " venue TEXT NOT NULL,"
        " symbol TEXT NOT NULL,"
        " strategy_id TEXT NOT NULL,"
        " side TEXT NOT NULL,"
        " qty REAL NOT NULL,"
        " status TEXT NOT NULL,"
        " opened_ts INTEGER NOT NULL DEFAULT 0,"
        " closed_ts INTEGER)"
    )
    # Pre-existing row to verify backfill.
    legacy.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "side, qty, status, opened_ts) "
        "VALUES ('legacy-1', 'okx', 'BTC-USDT', 'volume_burst', 'long', 1.0, 'OPEN', 100)"
    )
    legacy.commit()
    legacy.close()
    conn = init_db(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    assert {
        "entry_strategy_id",
        "active_strategy_id",
        "underlying_group_id",
        "swap_count",
    }.issubset(cols)
    row = conn.execute(
        "SELECT entry_strategy_id, active_strategy_id "
        "FROM positions WHERE position_id = 'legacy-1'"
    ).fetchone()
    assert row[0] == "volume_burst"
    assert row[1] == "volume_burst"
    conn.close()


def test_partially_migrated_positions_table_backfills(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """codex Day 4 R5 fix: ``init_db`` must repair empty-string strategy ids.

    A DB that already has the new columns but kept the legacy ``''`` defaults
    (partial migration / hand-edited row) must end up with the columns
    populated from ``strategy_id``.
    """
    from polaris.storage.schema import init_db  # noqa: PLC0415

    db_path = tmp_path / "partial.sqlite"
    pre = sqlite3.connect(str(db_path), isolation_level=None)
    pre.execute(
        "CREATE TABLE positions ("
        " position_id TEXT PRIMARY KEY,"
        " venue TEXT NOT NULL,"
        " symbol TEXT NOT NULL,"
        " underlying_group_id TEXT NOT NULL DEFAULT '',"
        " strategy_id TEXT NOT NULL,"
        " entry_strategy_id TEXT NOT NULL DEFAULT '',"
        " active_strategy_id TEXT NOT NULL DEFAULT '',"
        " side TEXT NOT NULL,"
        " qty REAL NOT NULL,"
        " status TEXT NOT NULL,"
        " opened_ts INTEGER NOT NULL DEFAULT 0,"
        " closed_ts INTEGER,"
        " swap_count INTEGER NOT NULL DEFAULT 0)"
    )
    pre.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, swap_count) VALUES "
        "('legacy-2','okx','BTC-USDT','','volume_burst','','','long',1.0,'OPEN',100,0)"
    )
    pre.close()
    conn = init_db(db_path)
    row = conn.execute(
        "SELECT entry_strategy_id, active_strategy_id "
        "FROM positions WHERE position_id = 'legacy-2'"
    ).fetchone()
    assert row[0] == "volume_burst"
    assert row[1] == "volume_burst"
    conn.close()


def test_strategy_swap_active_mismatch_blocks(memdb: sqlite3.Connection) -> None:
    """codex Day 4 R3 fix: candidate.from_strategy_id MUST match position.active_strategy_id.

    Layer 6 §Q3: ``active_strategy_id`` is the current driving strategy. A
    candidate whose ``from_strategy_id`` mismatches the live active row
    would corrupt attribution if applied — must be rejected.
    """
    _seed_position(memdb)
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="rsi_bb_pullback",  # mismatched: live active = volume_burst
        to_strategy_id="spot_donchian",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d.decision == "skipped_active_mismatch"
    row = memdb.execute(
        "SELECT active_strategy_id, swap_count FROM positions WHERE position_id='pos1'"
    ).fetchone()
    assert row[0] == "volume_burst"  # untouched
    assert int(row[1] or 0) == 0


def test_strategy_swap_seeds_entry_segment_and_regime(memdb: sqlite3.Connection) -> None:
    """codex Day 4 R2 fix:
    - first applied swap MUST seed an entry segment (40% attribution).
    - regime_at_start MUST be sourced from regime_state SSOT
      (venue × underlying_group_id) — not (venue, symbol)."""
    _seed_position(memdb)
    # Seed regime SSOT so the swap inherits it.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=100,
    )
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="crisis", now_ts=150,  # immediate flip → "crisis"
    )
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="volume_burst",
        to_strategy_id="spot_donchian",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d.decision == "applied"
    rows = memdb.execute(
        "SELECT strategy_id, regime_at_start, attribution_weight, entry_reason, "
        "exit_reason FROM position_strategy_segments "
        "WHERE position_id = 'pos1' ORDER BY started_ts ASC"
    ).fetchall()
    assert len(rows) == 2  # entry + active
    entry = rows[0]
    active = rows[1]
    assert entry[0] == "volume_burst"
    assert entry[1] == "crisis"  # SSOT regime captured
    assert entry[2] == pytest.approx(0.40)
    assert entry[3] == "entry"
    assert entry[4] == "swap"
    assert active[0] == "spot_donchian"
    assert active[1] == "crisis"
    assert active[2] == pytest.approx(0.60)
    assert active[3] == "swap"
    assert active[4] is None  # still open


def test_strategy_swap_p0_log_only_default(memdb: sqlite3.Connection) -> None:
    _seed_position(memdb)
    cand = SwapCandidate(
        position_id="pos1",
        from_strategy_id="volume_burst",
        to_strategy_id="spot_donchian",
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200)  # apply=False
    assert d.decision == "would_swap"
    row = memdb.execute(
        "SELECT active_strategy_id, swap_count FROM positions WHERE position_id='pos1'"
    ).fetchone()
    assert row[0] == "volume_burst"  # untouched
    assert int(row[1] or 0) == 0


# ---------------------------------------------------------------------------
# Conviction stacking — 4 gate + max 3 layers
# ---------------------------------------------------------------------------


def test_conviction_stack_max_3_layers() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="top",
        unrealized_pnl_r=1.0, existing_layer_count=3,
        layer_sum_size_pct=0.05, single_trade_cap_pct=0.08,
        headroom_pct=0.10,
    )
    d = can_stack_conviction(inputs)
    assert d.can_stack is False
    assert d.blocked_reason == "max_layers"
    assert CONVICTION_MAX_LAYERS == 3


def test_conviction_layer_mults_descending() -> None:
    assert CONVICTION_LAYER_MULTS == (1.0, 0.7, 0.5)
    assert compute_stack_size_mult(0) == 1.0
    assert compute_stack_size_mult(1) == 0.7
    assert compute_stack_size_mult(2) == 0.5
    assert compute_stack_size_mult(3) == 0.0


def test_conviction_blocks_non_top_quartile() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="mid",
        unrealized_pnl_r=1.0, existing_layer_count=0,
        layer_sum_size_pct=0.05, single_trade_cap_pct=0.08,
        headroom_pct=0.10,
    )
    d = can_stack_conviction(inputs)
    assert d.can_stack is False
    assert d.blocked_reason == "not_top_quartile"


def test_conviction_blocks_pnl_below_min() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="top",
        unrealized_pnl_r=0.4, existing_layer_count=0,
        layer_sum_size_pct=0.05, single_trade_cap_pct=0.08,
        headroom_pct=0.10,
    )
    d = can_stack_conviction(inputs)
    assert d.blocked_reason == "pnl_below_min"


def test_conviction_count_layers_reads_db(memdb: sqlite3.Connection) -> None:
    """``count_layers`` must read existing rows so stacking gates respect prior layers."""
    pid = "pos-stack"
    assert count_layers(memdb, position_id=pid) == 0
    for idx, mult in enumerate(CONVICTION_LAYER_MULTS):
        memdb.execute(
            "INSERT INTO position_conviction_layers "
            "(layer_id, position_id, layer_index, size_mult, opened_ts, "
            " strategy_id, venue, symbol, side) "
            "VALUES (?, ?, ?, ?, ?, 'volume_burst', 'okx', 'BTC-USDT', 'long')",
            (f"layer-{idx}", pid, idx, mult, 100 + idx),
        )
    assert count_layers(memdb, position_id=pid) == CONVICTION_MAX_LAYERS
    # Gate at max → blocked.
    decision = can_stack_conviction(
        ConvictionGateInputs(
            position_id=pid, cell_quartile="top",
            unrealized_pnl_r=1.0,
            existing_layer_count=count_layers(memdb, position_id=pid),
            layer_sum_size_pct=0.05, single_trade_cap_pct=0.08,
            headroom_pct=0.10,
        )
    )
    assert decision.can_stack is False
    assert decision.blocked_reason == "max_layers"


def test_conviction_pass_returns_correct_mult() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="top",
        unrealized_pnl_r=1.0, existing_layer_count=1,
        layer_sum_size_pct=0.05, single_trade_cap_pct=0.08,
        headroom_pct=0.10,
    )
    d = can_stack_conviction(inputs)
    assert d.can_stack is True
    assert d.next_layer_mult == 0.7
    assert d.next_layer_index == 1
