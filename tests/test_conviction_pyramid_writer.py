"""Conviction pyramiding — live writer (G6 caller-side glue), TDD.

Spec source:
- vault/30_components/layer-6-live-recalc.md (Q4 conviction stacking)
- vault/50_research/debates/conviction_pyramid_addon_notional_2026-07-10.md
  (sizing-change /debate consensus — D1 shadow-first no compute_size() call
  this slice, D2 gate_shadow_events + POLARIS_CONVICTION_PYRAMID_ACTIVE,
  D3 projected-sum group-cap fix lives in the CALLER, not can_stack_conviction)

DEMO/PAPER virtual capital only. Aggressive bias preserved — this module only
ever ADDS to a proven winner (pnl_r >= +0.5R); it never trims/exits/blocks.
9-stack collapse permanently sealed: the add-on's ``layer_size_mult`` must
NEVER reach a T4 continuous-scalar/tier/cell pre-clip term.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from polaris.core.live_recalc.conviction import (
    CONVICTION_LAYER_MULTS,
    ConvictionGateInputs,
    build_stack_signal,
    can_stack_conviction,
    count_layers,
    record_conviction_layer,
)
from polaris.core.live_recalc.conviction_writer import (
    evaluate_conviction_stack,
)
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.config import CONVICTION_PYRAMID_ACTIVE_ENV, conviction_pyramid_active
from polaris.core.pipeline.gate_state import GATE_POSITION_MONITOR

_POSITION = {
    "position_id": "pos-conv-1",
    "venue": "okx",
    "symbol": "BTC-USDT",
    "side": "long",
    "strategy": "tsmom",
    "underlying_group_id": "crypto:BTC",
    "correlation_group": "spot_breakout",
}


def _winner_inputs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "position": dict(_POSITION),
        "unrealized_pnl_r": 1.0,
        "cell_quartile": "top",
        "layer_sum_size_pct": 0.02,
        "single_trade_cap_pct": 0.08,
        "headroom_pct": 0.10,
        "run_id": "run-1",
        "regime": "bull_trend",
        "now_ts": 1_000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# build_stack_signal — 9-stack guard (pure function)
# ---------------------------------------------------------------------------


def test_build_stack_signal_pins_baseline_signal_strength() -> None:
    """signal_strength MUST stay 1.0 regardless of layer_mult — 9-stack guard."""
    for mult in CONVICTION_LAYER_MULTS:
        sig = build_stack_signal(_POSITION, mult)
        assert sig["signal_strength"] == 1.0
        assert sig["layer_size_mult"] == mult


def test_build_stack_signal_carries_position_identity() -> None:
    sig = build_stack_signal(_POSITION, 0.7)
    assert sig["venue"] == "okx"
    assert sig["symbol"] == "BTC-USDT"
    assert sig["side"] == "long"
    assert sig["strategy"] == "tsmom"
    assert sig["parent_position_id"] == "pos-conv-1"
    # Never a pre-computed notional — that is the (deferred) dispatcher's job.
    assert "final_notional_usd" not in sig


# ---------------------------------------------------------------------------
# record_conviction_layer — DB write + idempotent-insert race guard
# ---------------------------------------------------------------------------


def test_record_conviction_layer_inserts_row(memdb: sqlite3.Connection) -> None:
    layer_id = record_conviction_layer(
        memdb, position=_POSITION, layer_index=0, size_mult=1.0, now_ts=100,
    )
    assert layer_id
    row = memdb.execute(
        "SELECT position_id, layer_index, size_mult, strategy_id, venue, "
        "symbol, side FROM position_conviction_layers WHERE layer_id = ?",
        (layer_id,),
    ).fetchone()
    assert row == ("pos-conv-1", 0, 1.0, "tsmom", "okx", "BTC-USDT", "long")
    assert count_layers(memdb, position_id="pos-conv-1") == 1


def test_record_conviction_layer_duplicate_index_is_noop(memdb: sqlite3.Connection) -> None:
    """Race guard (debate round-2 blind spot): a second INSERT at the SAME
    (position_id, layer_index) must not create a duplicate row."""
    first = record_conviction_layer(
        memdb, position=_POSITION, layer_index=0, size_mult=1.0, now_ts=100,
    )
    second = record_conviction_layer(
        memdb, position=_POSITION, layer_index=0, size_mult=1.0, now_ts=101,
    )
    assert second is None
    assert first is not None
    assert count_layers(memdb, position_id="pos-conv-1") == 1


# ---------------------------------------------------------------------------
# evaluate_conviction_stack — shadow-first writer glue
# ---------------------------------------------------------------------------


def test_shadow_mode_default_logs_only_no_insert_no_signal(memdb: sqlite3.Connection) -> None:
    """Default (no env, active=None) — shadow-only: gate_shadow_events row,
    NO position_conviction_layers INSERT, NO add-on signal."""
    result = evaluate_conviction_stack(memdb, **_winner_inputs())
    assert result is not None
    assert result.can_stack is True
    assert result.active is False
    assert result.layer_id is None
    assert result.add_on_signal is None
    assert count_layers(memdb, position_id="pos-conv-1") == 0

    events = fetch_shadow_events(memdb, gate_id=GATE_POSITION_MONITOR)
    assert len(events) == 1
    assert events[0]["signal_id"] == "pos-conv-1"
    assert events[0]["gpt_decision"] == ""
    assert events[0]["mismatch"] == 0
    assert events[0]["technical_decision"] == "SIZED"  # would-stack


def test_shadow_mode_blocked_logs_hold_decision(memdb: sqlite3.Connection) -> None:
    result = evaluate_conviction_stack(memdb, **_winner_inputs(cell_quartile="mid"))
    assert result is not None
    assert result.can_stack is False
    assert result.blocked_reason == "not_top_quartile"
    events = fetch_shadow_events(memdb, gate_id=GATE_POSITION_MONITOR)
    assert events[0]["technical_decision"] == "HOLD"
    assert events[0]["technical_reason"] == "not_top_quartile"


def test_active_mode_inserts_layer_and_builds_add_on_signal(memdb: sqlite3.Connection) -> None:
    """writer integration test (mandate spec): pnl_r 임계 통과 → layer INSERT →
    add-on intent 생성."""
    result = evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
    assert result is not None
    assert result.can_stack is True
    assert result.active is True
    assert result.layer_id is not None
    assert result.next_layer_mult == 1.0  # first add-on layer
    assert result.add_on_signal is not None
    assert result.add_on_signal["signal_strength"] == 1.0
    assert result.add_on_signal["layer_size_mult"] == 1.0

    row = memdb.execute(
        "SELECT layer_index, size_mult FROM position_conviction_layers "
        "WHERE position_id = 'pos-conv-1'"
    ).fetchone()
    assert row == (0, 1.0)
    assert count_layers(memdb, position_id="pos-conv-1") == 1

    # Shadow log is ALWAYS written too (observe-first even in active mode).
    events = fetch_shadow_events(memdb, gate_id=GATE_POSITION_MONITOR)
    assert len(events) == 1


def test_active_mode_second_layer_uses_next_mult(memdb: sqlite3.Connection) -> None:
    evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
    result2 = evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
    assert result2 is not None
    assert result2.can_stack is True
    assert result2.next_layer_mult == 0.7
    assert count_layers(memdb, position_id="pos-conv-1") == 2


def test_active_mode_pnl_below_min_blocks_no_insert(memdb: sqlite3.Connection) -> None:
    result = evaluate_conviction_stack(
        memdb, active=True, **_winner_inputs(unrealized_pnl_r=0.2),
    )
    assert result is not None
    assert result.can_stack is False
    assert result.blocked_reason == "pnl_below_min"
    assert result.layer_id is None
    assert count_layers(memdb, position_id="pos-conv-1") == 0


def test_conn_none_returns_none_fail_open() -> None:
    """Fail-open (Q4 pattern): missing conn → no judgment, no crash."""
    assert evaluate_conviction_stack(None, **_winner_inputs()) is None


# ---------------------------------------------------------------------------
# cap-respect — group_cap exceeded blocks stacking (mandate spec test)
# ---------------------------------------------------------------------------


def test_group_cap_exceeded_blocks_active_insert(memdb: sqlite3.Connection) -> None:
    """layer_sum already at the single_trade_cap*2.2 ceiling → can_stack False,
    no INSERT even in ACTIVE mode."""
    result = evaluate_conviction_stack(
        memdb, active=True,
        **_winner_inputs(layer_sum_size_pct=0.08 * 2.2, single_trade_cap_pct=0.08),
    )
    assert result is not None
    assert result.can_stack is False
    assert result.blocked_reason == "group_cap_exceeded"
    assert count_layers(memdb, position_id="pos-conv-1") == 0


def test_projected_sum_blocks_before_breaching_cap_after_add(memdb: sqlite3.Connection) -> None:
    """Debate D3 fix: the CALLER projects layer_sum_size_pct forward (current +
    this layer's own upper-bound share) so a layer that would PUSH the total
    past single_trade_cap*2.2 is blocked NOW, not one tick late.

    single_trade_cap_pct=0.08 → ceiling = 0.176. Existing layer_sum=0.15 (pre-add,
    itself still <= ceiling) + layer0's own upper bound (0.08*1.0=0.08) would
    project to 0.23 > 0.176 → must block, even though the RAW pre-add sum alone
    would have passed the legacy (non-projected) check.
    """
    result = evaluate_conviction_stack(
        memdb, active=True,
        **_winner_inputs(layer_sum_size_pct=0.15, single_trade_cap_pct=0.08),
    )
    assert result is not None
    assert result.can_stack is False
    assert result.blocked_reason == "group_cap_exceeded"
    assert count_layers(memdb, position_id="pos-conv-1") == 0


# ---------------------------------------------------------------------------
# max 3-layer clamp (mandate spec test) — 4th attempt rejected
# ---------------------------------------------------------------------------


def test_max_3_layer_clamp_rejects_4th_stack(memdb: sqlite3.Connection) -> None:
    for _ in range(3):
        r = evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
        assert r is not None and r.can_stack is True
    assert count_layers(memdb, position_id="pos-conv-1") == 3

    fourth = evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
    assert fourth is not None
    assert fourth.can_stack is False
    assert fourth.blocked_reason == "max_layers"
    assert count_layers(memdb, position_id="pos-conv-1") == 3


# ---------------------------------------------------------------------------
# 9-stack regression — add-on signal never enters the T4 continuous-scalar
# chain (mandate spec test)
# ---------------------------------------------------------------------------


def test_add_on_signal_never_enters_continuous_scalar_chain(memdb: sqlite3.Connection) -> None:
    """Every layer's add-on signal carries baseline signal_strength=1.0 — the
    layer mult travels ONLY in ``layer_size_mult``, a field the T4 engine
    (``polaris.core.sizing.engine.continuous_scalar`` / ``compute_proposed``)
    never reads. This asserts the writer's OUTPUT SHAPE keeps the two
    concepts structurally separate across all 3 layers (1.0/0.7/0.5)."""
    seen_mults = []
    for _ in range(3):
        r = evaluate_conviction_stack(memdb, active=True, **_winner_inputs())
        assert r is not None and r.add_on_signal is not None
        assert r.add_on_signal["signal_strength"] == 1.0
        seen_mults.append(r.add_on_signal["layer_size_mult"])
    assert seen_mults == [1.0, 0.7, 0.5]
    # The add-on signal shape has NO field name that could be mistaken for a
    # T4 pre-clip multiplier (continuous_scalar / judge_conviction /
    # strength_scalar / tier_amplifier / cell_routing_mult).
    forbidden_keys = {
        "continuous_scalar", "judge_conviction", "strength_scalar",
        "tier_amplifier", "cell_routing_mult",
    }
    assert not forbidden_keys & build_stack_signal(_POSITION, 0.7).keys()


def test_conviction_writer_module_never_imports_t4_engine() -> None:
    """Static guard: the writer must not import ``compute_size`` (debate D1 —
    this slice defers T4 re-invocation + its side effects to a later
    dispatcher slice)."""
    import ast
    import inspect

    from polaris.core.live_recalc import conviction_writer

    src = inspect.getsource(conviction_writer)
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "polaris.core.sizing.engine" not in imported
    assert "polaris.core.sizing" not in imported


# ---------------------------------------------------------------------------
# can_stack_conviction — group_cap_exceeded coverage (mandate spec's existing
# unit list names this explicitly; the pure gate already implements it —
# pin the branch so a future edit can't silently regress it).
# ---------------------------------------------------------------------------


def test_can_stack_conviction_group_cap_exceeded_reason() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="top",
        unrealized_pnl_r=1.0, existing_layer_count=1,
        layer_sum_size_pct=0.20, single_trade_cap_pct=0.08,  # 0.20 > 0.08*2.2
        headroom_pct=0.10,
    )
    d = can_stack_conviction(inputs)
    assert d.can_stack is False
    assert d.blocked_reason == "group_cap_exceeded"


def test_can_stack_conviction_no_headroom_blocks() -> None:
    inputs = ConvictionGateInputs(
        position_id="p", cell_quartile="top",
        unrealized_pnl_r=1.0, existing_layer_count=0,
        layer_sum_size_pct=0.02, single_trade_cap_pct=0.08,
        headroom_pct=0.0,
    )
    d = can_stack_conviction(inputs)
    assert d.can_stack is False
    assert d.blocked_reason == "no_headroom"


def test_conviction_pyramid_active_env_default_off() -> None:
    assert conviction_pyramid_active(env_value="") is False
    assert conviction_pyramid_active(env_value="0") is False
    assert conviction_pyramid_active(env_value="1") is True
    assert CONVICTION_PYRAMID_ACTIVE_ENV == "POLARIS_CONVICTION_PYRAMID_ACTIVE"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
