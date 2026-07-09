"""G6 position_monitor — conviction-pyramid writer wiring.

DEMO/PAPER virtual capital. Aggressive bias unaffected — conviction stacking
is a pure SIDE-CHANNEL add-on judgment attached to ``GateResult.payload``; it
never changes G6's core HOLD/ADJUST_EXIT/EXIT_NOW/SWAP_STRATEGY decision
(flow_not_block). Absent ``conn`` or absent ``ctx.payload["conviction_stack"]``
→ byte-identical to the pre-wiring gate (fail-open, matches the existing Q4
missing-position contract + the ``tighten_enabled``-style opt-in precedent in
``test_g6_tighten_consumer.py``).

Spec: vault/50_research/debates/conviction_pyramid_addon_notional_2026-07-10.md
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from polaris.core.live_recalc.conviction import count_layers
from polaris.core.pipeline.agents.position_monitor import position_monitor_gate
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.pipeline.payload_builder import build_monitor_payload

_WINNER = {
    "venue": "okx", "symbol": "BTC-USDT", "side": "long",
    "strategy": "tsmom", "correlation_group": "spot_breakout",
    "entry_price": 100.0, "last_price": 106.0,
}


def _ctx(payload: dict[str, Any]) -> GateContext:
    return GateContext(
        run_id="run-conv-1",
        signal_id="pos-conv-w",
        position_id="pos-conv-w",
        gate_id=GATE_POSITION_MONITOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="tsmom",
        payload=payload,
        started_ts=1_000,
        state=SignalLifecycle.MONITORED,
    )


_STACK_INPUTS = {
    "cell_quartile": "top",
    "layer_sum_size_pct": 0.02,
    "single_trade_cap_pct": 0.08,
    "headroom_pct": 0.10,
    "regime": "bull_trend",
}


def _winner_payload(pnl_r: float = 1.0, **stack_overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "position": {**_WINNER, "position_id": "pos-conv-w"},
        "unrealized_pnl_r": pnl_r,
        "max_loss_r": 1.0,
        "conviction_stack": {**_STACK_INPUTS, **stack_overrides},
    }
    return payload


# ---------------------------------------------------------------------------
# No conn / no payload key — byte-identical (fail-open)
# ---------------------------------------------------------------------------


def test_no_conn_leaves_result_untouched() -> None:
    res = asyncio.run(position_monitor_gate(_ctx(_winner_payload(pnl_r=0.6))))
    assert res.decision == GateDecision.HOLD
    assert "conviction_stack" not in res.payload


def test_conn_but_no_stack_payload_key_untouched(memdb: sqlite3.Connection) -> None:
    payload = {
        "position": {**_WINNER, "position_id": "pos-conv-w"},
        "unrealized_pnl_r": 0.6,
        "max_loss_r": 1.0,
    }
    res = asyncio.run(position_monitor_gate(_ctx(payload), conn=memdb))
    assert res.decision == GateDecision.HOLD
    assert "conviction_stack" not in res.payload


# ---------------------------------------------------------------------------
# Wired — HOLD-band winner (pnl_r between +0.5R and +0.7R)
# ---------------------------------------------------------------------------


def test_hold_band_winner_attaches_shadow_judgment(memdb: sqlite3.Connection) -> None:
    """pnl_r=0.6R → G6 still plain HOLD (below widen window); conviction
    judgment attached in shadow mode (default OFF) by default."""
    res = asyncio.run(
        position_monitor_gate(_ctx(_winner_payload(pnl_r=0.6)), conn=memdb)
    )
    assert res.decision == GateDecision.HOLD
    stack = res.payload.get("conviction_stack")
    assert stack is not None
    assert stack["can_stack"] is True
    assert stack["active"] is False
    assert stack["layer_id"] is None
    assert count_layers(memdb, position_id="pos-conv-w") == 0


def test_hold_band_pnl_below_min_blocks(memdb: sqlite3.Connection) -> None:
    res = asyncio.run(
        position_monitor_gate(_ctx(_winner_payload(pnl_r=0.2)), conn=memdb)
    )
    assert res.decision == GateDecision.HOLD
    stack = res.payload["conviction_stack"]
    assert stack["can_stack"] is False
    assert stack["blocked_reason"] == "pnl_below_min"


# ---------------------------------------------------------------------------
# Wired — ADJUST_EXIT widen-window winner also gets the judgment attached
# ---------------------------------------------------------------------------


def test_widen_window_winner_still_gets_conviction_judgment(memdb: sqlite3.Connection) -> None:
    """pnl_r=1.0R → G6 ADJUST_EXIT (widen_window); conviction stacking is a
    parallel side-channel, so it still evaluates (winner range overlaps)."""
    res = asyncio.run(
        position_monitor_gate(_ctx(_winner_payload(pnl_r=1.0)), conn=memdb)
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload.get("reason") == "widen_window"
    stack = res.payload.get("conviction_stack")
    assert stack is not None
    assert stack["can_stack"] is True


# ---------------------------------------------------------------------------
# ACTIVE mode (explicit kwarg, env-independent per test discipline) — INSERT
# ---------------------------------------------------------------------------


def test_conviction_active_true_inserts_layer(memdb: sqlite3.Connection) -> None:
    res = asyncio.run(
        position_monitor_gate(
            _ctx(_winner_payload(pnl_r=1.0)), conn=memdb, conviction_active=True,
        )
    )
    stack = res.payload["conviction_stack"]
    assert stack["active"] is True
    assert stack["layer_id"] is not None
    assert stack["add_on_signal"]["signal_strength"] == 1.0
    assert count_layers(memdb, position_id="pos-conv-w") == 1


# ---------------------------------------------------------------------------
# Non-winner decisions (EXIT_NOW / SWAP_STRATEGY) never evaluate stacking
# ---------------------------------------------------------------------------


def test_exit_now_never_evaluates_conviction(memdb: sqlite3.Connection) -> None:
    payload = _winner_payload(pnl_r=-1.2)
    res = asyncio.run(position_monitor_gate(_ctx(payload), conn=memdb, conviction_active=True))
    assert res.decision == GateDecision.EXIT_NOW
    assert "conviction_stack" not in res.payload
    assert count_layers(memdb, position_id="pos-conv-w") == 0


def test_swap_strategy_never_evaluates_conviction(memdb: sqlite3.Connection) -> None:
    payload = _winner_payload(pnl_r=1.0)
    payload["swap_candidate"] = {
        "strategy": "spot_donchian", "correlation_group": "spot_breakout",
        "side": "long", "venue": "okx", "symbol": "BTC-USDT",
    }
    res = asyncio.run(position_monitor_gate(_ctx(payload), conn=memdb, conviction_active=True))
    assert res.decision == GateDecision.SWAP_STRATEGY
    assert "conviction_stack" not in res.payload
    assert count_layers(memdb, position_id="pos-conv-w") == 0


# ---------------------------------------------------------------------------
# build_monitor_payload — conviction_stack pass-through hook
# ---------------------------------------------------------------------------


def test_build_monitor_payload_absent_conviction_stack_is_byte_identical() -> None:
    payload = build_monitor_payload(
        position={**_WINNER, "position_id": "pos-conv-w"}, unrealized_pnl_r=0.6,
    )
    assert "conviction_stack" not in payload


def test_build_monitor_payload_threads_conviction_stack(memdb: sqlite3.Connection) -> None:
    payload = build_monitor_payload(
        position={**_WINNER, "position_id": "pos-conv-w"},
        unrealized_pnl_r=0.6,
        conviction_stack=dict(_STACK_INPUTS),
    )
    res = asyncio.run(position_monitor_gate(_ctx(payload), conn=memdb))
    assert res.decision == GateDecision.HOLD
    assert res.payload["conviction_stack"]["can_stack"] is True
