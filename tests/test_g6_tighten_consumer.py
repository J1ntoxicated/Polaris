"""G6 probe-TIGHTEN consumer — adverse HOLD escalates to ADJUST_EXIT (tighten).

DEMO/PAPER · aggressive · flow_not_block. The fix for the audited gap
(``test_g6_probe_tighten_gap`` FIXSPEC): when G6 would plain-HOLD an adverse position
(-1R < pnl_r <= +0.7R) AND the latest probe action is TIGHTEN, it now emits
ADJUST_EXIT routing a TIGHTER trail to G7 (precise exit TIMING) — NOT a HOLD->EXIT_NOW
block, NOT a size cut. The -1.0R hard rail, swap fast-path, widen window, BEP, size and
entry side are all UNTOUCHED.

Gating: the consumer is behind ``POLARIS_G6_PROBE_TIGHTEN`` (default OFF) so the live
loop is byte-identical until the flag is enabled in a supervised run. These tests force
the flag ON via the ``ai_free``-style explicit kwarg so they are env-independent.
"""

from __future__ import annotations

import asyncio

from polaris.core.pipeline.agents.position_monitor import position_monitor_gate
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)


def _ctx(payload: dict) -> GateContext:
    return GateContext(
        run_id="r",
        signal_id="s",
        position_id="pos_test",
        gate_id=GATE_POSITION_MONITOR,
        venue="capital",
        symbol="US100",
        strategy_id="index_breakout",
        payload=payload,
        started_ts=0,
        state=SignalLifecycle.MONITORED,
    )


_ADVERSE = {
    "venue": "capital", "symbol": "US100", "side": "long",
    "strategy": "index_breakout", "correlation_group": "idx",
    "entry_price": 20000.0, "last_price": 19990.0,
}


def _run(payload: dict, *, tighten_enabled: bool):
    return asyncio.run(
        position_monitor_gate(_ctx(payload), tighten_enabled=tighten_enabled)
    )


def test_flag_off_keeps_plain_hold() -> None:
    """Flag OFF → byte-identical to today: adverse + probe TIGHTEN still plain-HOLD."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
            "probe_action": "TIGHTEN",
            "probe_composite_lean": -0.49,
        },
        tighten_enabled=False,
    )
    assert res.decision == GateDecision.HOLD


def test_flag_on_adverse_tighten_emits_adjust_exit() -> None:
    """Flag ON → adverse HOLD-eligible position + probe TIGHTEN escalates to
    ADJUST_EXIT (tighten directive), never EXIT_NOW, never a size cut."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
            "probe_action": "TIGHTEN",
            "probe_composite_lean": -0.49,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload.get("tighten_intent") is True
    assert res.payload.get("reason") == "probe_tighten"


def test_flag_on_no_probe_tighten_still_holds() -> None:
    """Flag ON but no probe TIGHTEN present → unchanged HOLD."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.HOLD


def test_flag_on_probe_hold_action_not_escalated() -> None:
    """Flag ON, probe action HOLD (not TIGHTEN) → no escalation."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
            "probe_action": "HOLD",
            "probe_composite_lean": -0.05,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.HOLD


def test_flag_on_stop_hit_still_wins() -> None:
    """-1.0R hard rail outranks the tighten consumer: a stopped-out position EXITs
    NOW regardless of a probe TIGHTEN (the catastrophic backstop is untouched)."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -1.2,
            "max_loss_r": 1.0,
            "probe_action": "TIGHTEN",
            "probe_composite_lean": -0.6,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.EXIT_NOW
    assert res.payload.get("reason") == "stop_hit"


def test_flag_on_adverse_harvest_emits_adjust_exit() -> None:
    """P3 promotion (2026-07-16): HARVEST is a protect action too (probe
    performance readout: HARVEST realized +0.088R vs HOLD -0.08R) — flag ON +
    adverse HOLD-eligible position + probe HARVEST escalates to ADJUST_EXIT
    exactly like TIGHTEN, never EXIT_NOW, never a size cut."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
            "probe_action": "HARVEST",
            "probe_composite_lean": -0.49,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload.get("tighten_intent") is True
    assert res.payload.get("reason") == "probe_tighten"


def test_flag_on_probe_widen_action_never_escalated() -> None:
    """Structural exclusion (allowlist, not a WIDEN blocklist): flag ON, probe
    action WIDEN (measured -0.115R net-negative in the 2026-07-15 probe
    performance readout) never escalates — only TIGHTEN/HARVEST are promoted."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -0.05,
            "max_loss_r": 1.0,
            "probe_action": "WIDEN",
            "probe_composite_lean": 0.3,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.HOLD


def test_flag_on_stop_hit_still_wins_for_harvest() -> None:
    """-1.0R hard rail outranks HARVEST too (mirrors the TIGHTEN rail-safety
    test above) — the catastrophic backstop is untouched by either protect
    action."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": -1.2,
            "max_loss_r": 1.0,
            "probe_action": "HARVEST",
            "probe_composite_lean": -0.6,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.EXIT_NOW
    assert res.payload.get("reason") == "stop_hit"


def test_flag_on_winner_widen_window_unchanged() -> None:
    """Flag ON, a winner above +0.7R → still ADJUST_EXIT widen_window (the tighten
    consumer only fires in the adverse HOLD band; widen is untouched)."""
    res = _run(
        {
            "position": dict(_ADVERSE),
            "unrealized_pnl_r": 1.0,
            "max_loss_r": 1.0,
            "probe_action": "TIGHTEN",  # ignored — winner is in the widen window
            "probe_composite_lean": -0.49,
        },
        tighten_enabled=True,
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload.get("reason") == "widen_window"
