"""G6 DEGRADED — Position Monitor does not consume probe TIGHTEN decisions.

DEMO/PAPER · aggressive · flow_not_block (precise EXIT TIMING, never block/size-cut;
the G6 -1.0R rail is untouched). Characterization + spec coverage for the audited
G6 gap ([[g7_tick_trail_atr_scale_2026-06-25]] sibling investigation).

ROOT CAUSE (confirmed, code + DB evidence):
- PRODUCER: `_production_probe_attach.observe_probes` builds a populated ProbeContext
  and persists a `probe_decisions` row (action ∈ HOLD/WIDEN/TIGHTEN/HARVEST) to the
  `data/probes.sqlite` sidecar — OBSERVE-ONLY, returns None.
- GAP: `build_monitor_payload` (payload_builder.py:380) emits only position /
  unrealized_pnl_r / max_loss_r / swap_candidate (+ market_view / recent_ticks bolted
  on by the recalc caller). NO probe action key. `position_monitor_gate`
  (agents/position_monitor.py:111) reads none of it.
- CONSUMER: G6 `_python_decision` emits HOLD for any -1R < pnl_r <= +0.7R with no
  swap, regardless of a concurrent probe TIGHTEN. The only live readers of
  `probe_decisions` are offline (/debate digest + dashboard).
- DB: probe_decisions HOLD 22,266 / TIGHTEN 7,359 / HARVEST 5,465 / WIDEN 227. The
  TIGHTEN rows carry adverse composite_lean (-0.34..-0.54) on adverse OPEN positions
  (pnl_r -0.01..-0.06). Of closed HOLD decisions ~60% realized adverse R — the
  "HOLD violated after the fact" the audit flagged.

These tests pin the gap (current behaviour) and the intended fix (xfail) so a future
flow_not_block TIGHTEN consumer flips them. The fix shape (per the audit + review):
when G6 would HOLD an adverse position AND the latest probe action is TIGHTEN, feed a
TIGHTER trail to G7 (exit TIMING) — NOT a HOLD->EXIT_NOW block, NOT a size cut. This
is a trading-behaviour change (tighten semantics + cooldown + which gate owns it)
pending design, so only the gap is locked here, not a speculative live-monitor edit.
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


_ADVERSE_HOLD_POSITION = {
    "venue": "capital", "symbol": "US100", "side": "long",
    "strategy": "index_breakout", "correlation_group": "idx",
    "entry_price": 20000.0, "last_price": 19990.0,
}


def test_g6_holds_adverse_position_without_probe() -> None:
    """Baseline: an adverse-but-not-stopped position (-0.05R) gets HOLD."""
    res = asyncio.run(
        position_monitor_gate(
            _ctx({
                "position": dict(_ADVERSE_HOLD_POSITION),
                "unrealized_pnl_r": -0.05,
                "max_loss_r": 1.0,
            })
        )
    )
    assert res.decision == GateDecision.HOLD


def test_g6_ignores_probe_tighten_today() -> None:
    """THE GAP: even with a probe TIGHTEN action present on the payload, G6 still
    returns HOLD — it does not read the probe decision. This documents the current
    (degraded) behaviour; the fix below (xfail) is the intended consumer."""
    res = asyncio.run(
        position_monitor_gate(
            _ctx({
                "position": dict(_ADVERSE_HOLD_POSITION),
                "unrealized_pnl_r": -0.05,
                "max_loss_r": 1.0,
                # A probe decision the producer wrote to the sidecar — present here
                # to show G6 ignores it regardless.
                "probe_action": "TIGHTEN",
                "probe_composite_lean": -0.49,
            })
        )
    )
    # Current behaviour: HOLD (the probe TIGHTEN is dropped). When this stops being
    # true, the fix has landed and the xfail below should flip to the real assert.
    assert res.decision == GateDecision.HOLD


def test_g6_consumes_probe_tighten_into_tighter_exit_FIXSPEC() -> None:
    """FIX LANDED (flag-gated): a HOLD-eligible adverse position whose latest probe
    action is TIGHTEN no longer plain-HOLDs when the ``POLARIS_G6_PROBE_TIGHTEN``
    consumer is ON — it escalates to ADJUST_EXIT carrying ``tighten_intent`` (precise
    exit TIMING, a tighter trail to G7), never a size cut, never a HOLD->EXIT_NOW
    block. The -1.0R hard rail and the entry side stay untouched. The default-OFF gap
    above (``test_g6_ignores_probe_tighten_today`` with no flag) is still locked so
    the live loop is byte-identical until a supervised run opts in."""
    res = asyncio.run(
        position_monitor_gate(
            _ctx({
                "position": dict(_ADVERSE_HOLD_POSITION),
                "unrealized_pnl_r": -0.05,
                "max_loss_r": 1.0,
                "probe_action": "TIGHTEN",
                "probe_composite_lean": -0.49,
            }),
            tighten_enabled=True,
        )
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.payload.get("tighten_intent") is True
