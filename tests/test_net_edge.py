"""T14 — net-edge cost infrastructure (DISPLAY/LOG-ONLY) tests.

These assert the measurement layer ONLY. There is NO gating: the kill-switch
``SKIP_ON_NEGATIVE_NET_EDGE`` is OFF, ``build_watcher_payload`` ADDS keys
without dropping/changing existing ones, and no entry is ever skipped on the
basis of net edge (flow_not_block / AGGRESSIVE bias preserved). Enabling a skip
is a future /debate decision, not shipped here.
"""

from __future__ import annotations

import sqlite3

from polaris.core.pipeline import (
    GateContext,
    GateDecision,
    GateOrchestrator,
    SignalLifecycle,
    build_watcher_payload,
)
from polaris.core.pipeline.gate_state import GATE_SIGNAL_VALIDATOR
from polaris.core.pipeline.net_edge import (
    SKIP_ON_NEGATIVE_NET_EDGE,
    gross_edge_r,
    net_edge_r,
    roundtrip_cost_r,
)

_NOW = 1_780_000_000


class _RecordingGPTClient:
    """Minimal Anthropic-shape mock that records calls (for the guard test)."""

    def __init__(self, response_text: str = '{"decision": "PROCEED"}') -> None:
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001, ANN003
                outer.calls.append(kwargs)

                class _Block:
                    text = response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


# ---------------------------------------------------------------------------
# roundtrip_cost_r — monotonic in spread_bps
# ---------------------------------------------------------------------------


def test_roundtrip_cost_monotonic_in_spread_bps() -> None:
    """Wider spread => strictly higher round-trip cost (entry+exit)."""
    prev = -1.0
    for spread in (0.0, 1.0, 2.0, 5.0, 10.0, 25.0, 100.0):
        cost = roundtrip_cost_r(spread_bps=spread, cost_model="spot_taker")
        assert cost >= 0.0
        assert cost > prev, f"cost not increasing at spread={spread}"
        prev = cost


def test_roundtrip_cost_zero_spread_is_zero_floor() -> None:
    """Zero spread => zero spread component (no negative cost)."""
    assert roundtrip_cost_r(spread_bps=0.0, cost_model="spot_taker") == 0.0


def test_roundtrip_cost_spread_component_is_double_one_way() -> None:
    """Round-trip = entry+exit: the spread component scales as spread_bps*2."""
    one = roundtrip_cost_r(spread_bps=4.0, cost_model="spot_taker")
    half = roundtrip_cost_r(spread_bps=2.0, cost_model="spot_taker")
    # Linear in spread_bps -> doubling spread doubles the cost.
    assert abs(one - 2.0 * half) < 1e-12


def test_roundtrip_cost_absolute_value_pins_double_convention() -> None:
    """Pin the EXACT round-trip value so a silent *2 -> *1 (entry+exit ->
    one-way) regression is caught. With spread_bps=10 and a 1% (1R) stop:
    cost = 10 bps * 2 (entry+exit) * 1e-4 / 0.01 = 0.20 R. One-way would be
    0.10 R, so the absolute pin (not just linearity) guards the *2 factor."""
    cost = roundtrip_cost_r(
        spread_bps=10.0, cost_model="spot_taker", stop_distance_pct=0.01
    )
    assert abs(cost - 0.20) < 1e-12
    # One-way (the regression we are guarding against) would be exactly half.
    assert abs(cost - 0.10) > 1e-9


# ---------------------------------------------------------------------------
# net_edge_r = gross - cost (pure identity)
# ---------------------------------------------------------------------------


def test_net_edge_is_gross_minus_cost() -> None:
    gross = gross_edge_r(signal_strength=1.4, atr_pct=0.012)
    cost = roundtrip_cost_r(spread_bps=6.0, cost_model="spot_taker")
    assert net_edge_r(gross_edge=gross, roundtrip_cost=cost) == gross - cost


def test_net_edge_can_go_negative_but_is_not_acted_on() -> None:
    """A negative net edge is a *measurement*; nothing gates on it (flag off)."""
    net = net_edge_r(gross_edge=0.01, roundtrip_cost=0.5)
    assert net < 0.0
    assert SKIP_ON_NEGATIVE_NET_EDGE is False


# ---------------------------------------------------------------------------
# gross_edge_r — transparent placeholder estimate
# ---------------------------------------------------------------------------


def test_gross_edge_monotonic_in_signal_strength() -> None:
    """Stronger signal => larger gross-edge estimate (same ATR)."""
    weak = gross_edge_r(signal_strength=0.5, atr_pct=0.01)
    strong = gross_edge_r(signal_strength=1.5, atr_pct=0.01)
    assert strong > weak


def test_gross_edge_non_negative() -> None:
    assert gross_edge_r(signal_strength=0.0, atr_pct=0.0) >= 0.0


# ---------------------------------------------------------------------------
# kill-switch OFF — flow_not_block / AGGRESSIVE preserved
# ---------------------------------------------------------------------------


def test_skip_on_negative_net_edge_is_off() -> None:
    """Kill-switch is OFF. Turning it on is a /debate decision, not this step."""
    assert SKIP_ON_NEGATIVE_NET_EDGE is False


# ---------------------------------------------------------------------------
# build_watcher_payload — behavior identity (ADD keys, change NOTHING)
# ---------------------------------------------------------------------------

_BASE_KWARGS = dict(
    spread_bps=8.0,
    baseline_p50_spread_bps=10.0,
    listing_age_hours=100.0,
    recent_reject_in_6h=False,
    session_open_shock_window=False,
    tick_window=[],
)

# Keys that existed before T14 — these MUST be untouched.
_LEGACY_KEYS = {
    "spread_bps",
    "baseline_p50_spread_bps",
    "listing_age_hours",
    "recent_reject_in_6h",
    "session_open_shock_window",
    "tick_window",
}

_NEW_KEYS = {"net_edge_r", "gross_edge_r", "roundtrip_cost_r", "cost_model"}


def test_watcher_payload_preserves_legacy_keys_and_values() -> None:
    """The new net-edge keys are ADDED; legacy keys keep identical values."""
    legacy = build_watcher_payload(**_BASE_KWARGS)  # type: ignore[arg-type]
    full = build_watcher_payload(  # type: ignore[arg-type]
        **_BASE_KWARGS,
        venue="okx",
        signal_strength=1.3,
        atr_pct=0.011,
    )
    # Every legacy key present with the SAME value.
    for k in _LEGACY_KEYS:
        assert k in full
        assert full[k] == legacy[k], f"legacy key {k} mutated"
    # New keys appear only when net-edge inputs are supplied.
    assert _NEW_KEYS.issubset(full.keys())
    assert not (_NEW_KEYS & set(legacy.keys())), "net-edge keys leaked w/o inputs"


def test_watcher_payload_net_edge_values_are_consistent() -> None:
    """Surfaced net_edge_r == gross_edge_r - roundtrip_cost_r (display math)."""
    full = build_watcher_payload(  # type: ignore[arg-type]
        **_BASE_KWARGS,
        venue="okx",
        signal_strength=1.3,
        atr_pct=0.011,
    )
    assert full["cost_model"] == "spot_taker"  # resolve_stream("okx")
    assert (
        abs(full["net_edge_r"] - (full["gross_edge_r"] - full["roundtrip_cost_r"]))
        < 1e-12
    )


def test_watcher_payload_default_call_unchanged_for_okx_path() -> None:
    """No net-edge inputs => payload is byte-identical to the pre-T14 shape.

    Guards the OKX behavior-identity invariant: a caller that does not opt in
    sees exactly the legacy 6-key payload (no surprise branching surface).
    """
    payload = build_watcher_payload(**_BASE_KWARGS)  # type: ignore[arg-type]
    assert set(payload.keys()) == _LEGACY_KEYS


# ---------------------------------------------------------------------------
# Behavior identity at the consuming gate (G3, folding in G4's former inputs —
# P2a group A): the net-edge keys must NOT change the decision, the skip
# flag, or whether GPT is called. i.e. adding measurement never skips/changes
# an entry. flow_not_block / AGGRESSIVE intact.
# ---------------------------------------------------------------------------

# A payload shaped so G3 lands on its normal (non-fast-path) flow — the most
# control-flow-sensitive case.
_G4_SLOW = dict(
    spread_bps=2.0,
    baseline_p50_spread_bps=4.0,
    listing_age_hours=100.0,
    recent_reject_in_6h=False,
    session_open_shock_window=False,
)


async def _run_g3(payload: dict[str, object]) -> tuple[GateDecision, bool, int]:
    memdb = sqlite3.connect(":memory:")
    try:
        client = _RecordingGPTClient()
        orch = GateOrchestrator(conn=memdb, haiku_client=client)
        orch.handlers = {GATE_SIGNAL_VALIDATOR: orch._wrap_validator}
        ctx = GateContext(
            run_id="r", signal_id="s", position_id=None,
            gate_id=GATE_SIGNAL_VALIDATOR, venue="okx", symbol="BTC-USDT",
            strategy_id="vb", payload=dict(payload), started_ts=_NOW,
            state=SignalLifecycle.RAW,
        )
        results = await orch.run(ctx, start_gate=GATE_SIGNAL_VALIDATOR)
        return results[0].decision, bool(results[0].skipped), len(client.calls)
    finally:
        memdb.close()


async def test_g3_decision_identical_with_and_without_net_edge_keys() -> None:
    """Adding net_edge_r/gross_edge_r/roundtrip_cost_r/cost_model to the G3
    payload does NOT change the gate decision, the skip flag, or the (zero)
    GPT call count. No entry is skipped on net edge (kill-switch off)."""
    base = {
        **_G4_SLOW,
        "raw_signal": {"strategy": "vb", "score": 1.0},
        "cell_routing": {"quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5},
        "cell_quartile": "top",
    }
    # Legacy payload (no net-edge keys).
    legacy = build_watcher_payload(**_G4_SLOW)  # type: ignore[arg-type]
    legacy_payload = {**base, **legacy}
    # T14 payload WITH net-edge keys + a deeply negative net edge surfaced.
    full = build_watcher_payload(  # type: ignore[arg-type]
        **_G4_SLOW, venue="okx", signal_strength=0.0, atr_pct=0.0,
    )
    full_payload = {**base, **full}
    # Force the surfaced net edge negative to prove it is NOT acted on.
    assert full_payload["net_edge_r"] <= 0.0

    legacy_result = await _run_g3(legacy_payload)
    full_result = await _run_g3(full_payload)
    # Identical decision, identical skip flag, identical (zero) GPT-call count.
    assert legacy_result == full_result
    # And the entry was NOT skipped/blocked — deterministic PASS, GPT untouched.
    assert full_result[0] == GateDecision.PASS
    assert full_result[2] == 0
