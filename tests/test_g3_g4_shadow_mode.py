"""P4/P5 SHADOW MODE — deterministic technical rules computed in parallel to GPT.

These tests pin the AI-conductor P0 SHADOW contract (ai_conductor_architecture_
2026-05-30 + ai_conductor_transition_2026-05-30):

- Real pipeline decision STAYS GPT (behavior 0): the shadow technical rule is
  computed alongside and logged, never returned as the gate decision.
- G3 cold cell (n_eff < 5) = PASS-through ALWAYS (codex BLOCKING — "모호하면 통과").
  warm (n_eff >= 5) AND quartile == 'bottom' AND avg_pnl_r < 0 → narrow KILL only.
- G4 PROCEED default; KILL ONLY on stale/crossed book; spread/drift = flag (NOT
  KILL); realized-vol NEVER kills (codex BLOCKING — "expanding=기회").
- net_edge_r is NEVER read by either technical rule (codex BLOCKING).
- Shadow log row captures technical_decision / gpt_decision / mismatch flag +
  cell_warm + regime so the acceptance gate can analyse by regime / warm.
"""

from __future__ import annotations

import sqlite3
import uuid

from polaris.core.pipeline.agents._shadow_rules import (
    G3ShadowInputs,
    G4ShadowInputs,
    ShadowDecision,
    technical_validate_decision,
    technical_watch_decision,
)
from polaris.core.pipeline.agents.pre_entry_watcher import pre_entry_watcher_gate
from polaris.core.pipeline.agents.shadow_log import (
    fetch_shadow_events,
    log_shadow_event,
)
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


class _MockGPTClient:
    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _ctx(payload: dict, *, gate_id: int) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-shadow",
        position_id=None,
        gate_id=gate_id,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


# ===========================================================================
# G3 deterministic technical rule
# ===========================================================================


def test_g3_cold_cell_always_pass_even_bottom_quartile() -> None:
    """COLD cell (n_eff<5) = pass-through ALWAYS, even quartile=bottom + avg<0."""
    inp = G3ShadowInputs(
        n_eff=2.0,  # cold
        quartile="bottom",
        avg_pnl_r=-1.5,  # losing
        recent_trades=[{"won": False}, {"won": False}, {"won": False}],
        spread_bps=50.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=2.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS
    assert out.scalar == 1.0


def test_g3_warm_bottom_losing_narrow_kill() -> None:
    """WARM (n_eff>=5) AND quartile=bottom AND avg_pnl_r<0 → narrow KILL."""
    inp = G3ShadowInputs(
        n_eff=8.0,
        quartile="bottom",
        avg_pnl_r=-0.4,
        recent_trades=[],
        spread_bps=4.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=1000.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.KILL


def test_g3_warm_bottom_but_positive_avg_does_not_kill() -> None:
    """WARM bottom quartile but avg_pnl_r >= 0 must NOT KILL (no losing evidence)."""
    inp = G3ShadowInputs(
        n_eff=8.0,
        quartile="bottom",
        avg_pnl_r=0.1,
        recent_trades=[],
        spread_bps=4.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=1000.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_g3_warm_top_quartile_pass() -> None:
    inp = G3ShadowInputs(
        n_eff=12.0,
        quartile="top",
        avg_pnl_r=0.6,
        recent_trades=[],
        spread_bps=3.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=1000.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS


def test_g3_warm_mid_quartile_conservative_modify() -> None:
    """WARM mid quartile → conservative MODIFY scalar in [MODIFY_MIN, 1.0]."""
    inp = G3ShadowInputs(
        n_eff=10.0,
        quartile="mid",
        avg_pnl_r=0.0,
        recent_trades=[],
        spread_bps=4.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=1000.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.MODIFY
    assert 0.5 <= out.scalar <= 1.0


def test_g3_cold_quartile_label_conservative_modify_not_kill() -> None:
    """quartile label 'cold' → conservative MODIFY, never KILL."""
    inp = G3ShadowInputs(
        n_eff=3.0,
        quartile="cold",
        avg_pnl_r=-2.0,
        recent_trades=[{"won": False}, {"won": False}],
        spread_bps=4.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=1000.0,
    )
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_g3_booster_only_reinforces_warm_kill_not_cold() -> None:
    """Cell-independent boosters (consecutive losses) do NOT KILL a COLD cell."""
    inp = G3ShadowInputs(
        n_eff=1.0,  # cold
        quartile="mid",
        avg_pnl_r=0.0,
        recent_trades=[{"won": False}, {"won": False}, {"won": False}],
        spread_bps=80.0,
        baseline_p50_spread_bps=5.0,
        listing_age_hours=0.5,
    )
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_g3_does_not_read_net_edge() -> None:
    """net_edge_r in payload must never flip the technical decision."""
    base = dict(
        n_eff=12.0, quartile="top", avg_pnl_r=0.6, recent_trades=[],
        spread_bps=3.0, baseline_p50_spread_bps=5.0, listing_age_hours=1000.0,
    )
    out_no_edge = technical_validate_decision(G3ShadowInputs(**base))
    # net_edge is NOT a field of G3ShadowInputs — the rule cannot consult it.
    assert "net_edge_r" not in G3ShadowInputs.__dataclass_fields__
    assert out_no_edge.decision == GateDecision.PASS


# ===========================================================================
# G4 deterministic technical rule
# ===========================================================================


def test_g4_proceed_default_clean_book() -> None:
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=1.0,
        spread_bps=10.0,
        baseline_p50_spread_bps=8.0,
        drift_bps=5.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert not out.flags


def test_g4_crossed_book_kill() -> None:
    """bid >= ask (crossed) → KILL (microstructure broken)."""
    inp = G4ShadowInputs(
        best_bid=100.2,
        best_ask=100.0,
        last_tick_age_sec=1.0,
        spread_bps=10.0,
        baseline_p50_spread_bps=8.0,
        drift_bps=0.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.KILL


def test_g4_stale_book_kill() -> None:
    """last tick far older than freshness bound → stale → KILL."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=120.0,  # very stale
        spread_bps=10.0,
        baseline_p50_spread_bps=8.0,
        drift_bps=0.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.KILL


def test_g4_wide_spread_flags_not_kill() -> None:
    """Spread far above baseline = FLAG, never KILL (codex C)."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.5,
        last_tick_age_sec=1.0,
        spread_bps=200.0,  # way above baseline
        baseline_p50_spread_bps=8.0,
        drift_bps=0.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert "spread_wide" in out.flags


def test_g4_drift_flags_not_kill() -> None:
    """Large adverse drift = FLAG, never KILL."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=1.0,
        spread_bps=10.0,
        baseline_p50_spread_bps=8.0,
        drift_bps=500.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert "drift" in out.flags


def test_g4_high_realized_vol_never_kills() -> None:
    """realized-vol is NOT a KILL trigger (codex BLOCKING B): no vol field."""
    assert "realized_vol" not in G4ShadowInputs.__dataclass_fields__
    assert "realized_vol_pct" not in G4ShadowInputs.__dataclass_fields__


def test_g4_does_not_read_net_edge() -> None:
    assert "net_edge_r" not in G4ShadowInputs.__dataclass_fields__


# ===========================================================================
# Shadow log infrastructure
# ===========================================================================


def test_shadow_log_records_decision_and_mismatch(memdb: sqlite3.Connection) -> None:
    log_shadow_event(
        memdb,
        run_id="r1",
        signal_id="s1",
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        regime="trend",
        technical=ShadowDecision(decision=GateDecision.KILL, scalar=0.0),
        gpt_decision=GateDecision.PASS,
        cell_warm=True,
    )
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1
    row = rows[0]
    assert row["technical_decision"] == "KILL"
    assert row["gpt_decision"] == "PASS"
    assert row["mismatch"] == 1
    assert row["cell_warm"] == 1
    assert row["regime"] == "trend"
    assert row["gate_id"] == GATE_SIGNAL_VALIDATOR


def test_shadow_log_match_no_mismatch(memdb: sqlite3.Connection) -> None:
    log_shadow_event(
        memdb,
        run_id="r1",
        signal_id="s1",
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx",
        symbol="ETH-USDT",
        regime="chop",
        technical=ShadowDecision(decision=GateDecision.PROCEED),
        gpt_decision=GateDecision.PROCEED,
        cell_warm=False,
    )
    rows = fetch_shadow_events(memdb)
    assert rows[0]["mismatch"] == 0
    assert rows[0]["cell_warm"] == 0


def test_shadow_log_noop_without_conn() -> None:
    """No conn → silent no-op (never crashes the hot path)."""
    log_shadow_event(
        None,
        run_id="r1",
        signal_id="s1",
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue="okx",
        symbol="BTC-USDT",
        regime="trend",
        technical=ShadowDecision(decision=GateDecision.PASS),
        gpt_decision=GateDecision.PASS,
        cell_warm=False,
    )


# ===========================================================================
# Behavior-0: real pipeline decision stays GPT; shadow only logs.
# ===========================================================================


async def test_g3_real_decision_stays_gpt_pass_even_when_technical_would_kill(
    memdb: sqlite3.Connection,
) -> None:
    """GPT says PASS; technical (warm bottom losing) would KILL — gate returns GPT PASS."""
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {
                "quartile": "bottom",
                "n_eff": 9.0,
                "avg_pnl_r": -0.5,
            },
            "spread_bps": 4.0,
            "baseline_p50_spread_bps": 5.0,
            "listing_age_hours": 1000.0,
            "regime": "trend",
            "net_edge_r": -3.0,  # negative — must NOT influence anything
        },
        gate_id=3,
    )
    result = await signal_validator_gate(ctx, client=haiku, shadow_conn=memdb)
    # Real decision = GPT PASS (behavior 0).
    assert result.decision == GateDecision.PASS
    # Shadow row recorded the technical KILL vs gpt PASS mismatch.
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1
    assert rows[0]["gate_id"] == GATE_SIGNAL_VALIDATOR
    assert rows[0]["technical_decision"] == "KILL"
    assert rows[0]["gpt_decision"] == "PASS"
    assert rows[0]["mismatch"] == 1
    assert rows[0]["cell_warm"] == 1


async def test_g3_shadow_off_when_no_conn_behaves_identically() -> None:
    """Without shadow_conn, gate is byte-identical to legacy (no shadow side-effect)."""
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PASS
    assert result.payload["validated_signal"]["strength_scalar"] == 1.0


async def test_g4_real_decision_stays_gpt_kill_even_when_technical_proceeds(
    memdb: sqlite3.Connection,
) -> None:
    """GPT KILL; technical PROCEED (clean book) — gate returns GPT KILL (behavior 0)."""
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    ctx = _ctx(
        {
            "validated_signal": {"strength_scalar": 1.0, "strategy": "vb"},
            "tick_window": [
                {"ts": NOW, "bid": 100.0, "ask": 100.1, "mid": 100.05},
            ],
            "spread_bps": 10.0,
            "baseline_p50_spread_bps": 8.0,
            "cell_quartile": "mid",
            "regime": "chop",
        },
        gate_id=4,
    )
    result = await pre_entry_watcher_gate(ctx, client=haiku, shadow_conn=memdb)
    assert result.decision == GateDecision.KILL  # GPT wins (behavior 0)
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1
    assert rows[0]["gate_id"] == GATE_PRE_ENTRY_WATCHER
    assert rows[0]["technical_decision"] == "PROCEED"
    assert rows[0]["gpt_decision"] == "KILL"
    assert rows[0]["mismatch"] == 1


async def test_g4_fast_path_unaffected_by_shadow(memdb: sqlite3.Connection) -> None:
    """Fast-path PROCEED still short-circuits (shadow does not change control flow)."""
    haiku = _MockGPTClient(response_text='{"decision": "PROCEED"}')
    ctx = _ctx(
        {
            "validated_signal": {"strength_scalar": 1.30, "strategy": "vb"},
            "tick_window": [],
            "cell_quartile": "top",
            "spread_bps": 2.0,
            "baseline_p50_spread_bps": 4.0,
            "recent_reject_in_6h": False,
            "listing_age_hours": 100.0,
            "session_open_shock_window": False,
        },
        gate_id=4,
    )
    result = await pre_entry_watcher_gate(ctx, client=haiku, shadow_conn=memdb)
    assert result.decision == GateDecision.PROCEED
    assert result.model_used == "python_fast_path"
