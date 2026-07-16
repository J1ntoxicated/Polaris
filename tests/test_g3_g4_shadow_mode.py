"""P4/P5 SHADOW MODE — deterministic technical rules computed in parallel to GPT.

These tests pin the AI-conductor P0 SHADOW contract (ai_conductor_architecture_
2026-05-30 + ai_conductor_transition_2026-05-30):

- Real pipeline decision STAYS GPT (behavior 0): the shadow technical rule is
  computed alongside and logged, never returned as the gate decision.
- G3 NEVER blocks entry on a cell being "losing" (flow_not_block): a cold cell
  passes through ("모호하면 통과"); a losing cell flows (PASS) or gets a
  conservative MODIFY trim. The technical rule raises NO entry-block KILL.
- G4 PROCEED default; KILL ONLY on crossed book; stale book (per-ticker median
  tick-interval baseline, safe global fallback when absent) / spread / drift =
  flag (NOT KILL); realized-vol NEVER kills (codex BLOCKING — "expanding=기회").
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
    )
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS
    assert out.scalar == 1.0


def test_g3_warm_bottom_losing_now_flows() -> None:
    """flow_not_block: WARM quartile='bottom' + avg_pnl_r<0 NEVER blocks → PASS.

    Losing is never an entry block (loss-defense lives at EXIT). This pins the
    removal of the former ``warm_bottom_losing`` KILL.
    """
    inp = G3ShadowInputs(n_eff=8.0, quartile="bottom", avg_pnl_r=-0.4)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS
    assert "losing" not in out.reason


def test_g3_warm_bottom_positive_avg_also_passes() -> None:
    """WARM bottom quartile (winning or losing) flows the same — both PASS."""
    inp = G3ShadowInputs(n_eff=8.0, quartile="bottom", avg_pnl_r=0.1)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS


def test_g3_warm_top_quartile_pass() -> None:
    inp = G3ShadowInputs(n_eff=12.0, quartile="top", avg_pnl_r=0.6)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS


def test_g3_warm_mid_quartile_conservative_modify() -> None:
    """WARM mid quartile → conservative MODIFY scalar in [MODIFY_MIN, 1.0]."""
    inp = G3ShadowInputs(n_eff=10.0, quartile="mid", avg_pnl_r=0.0)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.MODIFY
    assert 0.5 <= out.scalar <= 1.0


def test_g3_cold_quartile_label_conservative_modify_not_kill() -> None:
    """quartile label 'cold' + losing → conservative MODIFY, never KILL."""
    inp = G3ShadowInputs(n_eff=3.0, quartile="cold", avg_pnl_r=-2.0)
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL


def test_g3_warm_cold_quartile_losing_modifies_not_kill() -> None:
    """WARM cell with a 'cold' quartile label + losing → MODIFY (not a block)."""
    inp = G3ShadowInputs(n_eff=8.0, quartile="cold", avg_pnl_r=-0.4)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.MODIFY
    assert out.decision != GateDecision.KILL


def test_g3_does_not_read_net_edge() -> None:
    """net_edge_r is NOT a field of G3ShadowInputs — the rule cannot consult it."""
    out_no_edge = technical_validate_decision(
        G3ShadowInputs(n_eff=12.0, quartile="top", avg_pnl_r=0.6)
    )
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


def test_g4_stale_book_flags_not_kill_no_baseline() -> None:
    """No per-ticker baseline → falls back to the fixed bound → FLAG, not KILL."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=120.0,  # very stale vs the STALE_TICK_MAX_SEC fallback
        spread_bps=10.0,
        baseline_p50_spread_bps=8.0,
        drift_bps=0.0,
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert "stale_book" in out.flags


def test_g4_low_cadence_symbol_normal_tick_proceeds_clean() -> None:
    """A low-cadence symbol's own baseline absorbs a tick that would've been
    stale under the old flat 60s bound — no flag at all (good signal, not
    KILLed)."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=70.0,  # > old flat STALE_TICK_MAX_SEC=60s
        baseline_p50_tick_interval_sec=90.0,  # this symbol normally ticks ~90s
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert not out.flags


def test_g4_stale_relative_to_tight_baseline_flags() -> None:
    """A fast-cadence symbol well past ITS OWN baseline → FLAG, not KILL."""
    inp = G4ShadowInputs(
        best_bid=100.0,
        best_ask=100.1,
        last_tick_age_sec=100.0,
        baseline_p50_tick_interval_sec=10.0,  # ticks ~every 10s normally
    )
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert "stale_book" in out.flags


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


async def test_g3_technical_always_drives_gpt_client_never_touched(
    memdb: sqlite3.Connection,
) -> None:
    """P2a group B: the legacy GPT branch is deleted outright — a warm-mid
    cell always returns the technical MODIFY regardless of a supplied (and
    unused) client, and the shadow row logs gpt_decision=None (no GPT call
    left to compare against)."""
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {
                "quartile": "mid",
                "n_eff": 9.0,
                "avg_pnl_r": -0.5,
            },
            "regime": "trend",
            "net_edge_r": -3.0,  # negative — must NOT influence anything
        },
        gate_id=3,
    )
    result = await signal_validator_gate(ctx, client=haiku, shadow_conn=memdb)
    assert result.decision == GateDecision.MODIFY
    # Only the relocated G4 frontgate squeeze tag (a REAL preserved
    # instrument) logs — the comparisonless G3 row is dropped.
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1  # G4 frontgate tag only — comparisonless G3 row dropped (P2a closeout)
    assert rows[0]["gate_id"] == 4
    g4_tag = rows[0]  # the only row — relocated frontgate tag
    # the tag row carries the frontgate's own PROCEED verdict (the G3 MODIFY
    # decision is asserted on `result` above; its shadow row is dropped).
    assert g4_tag["technical_decision"] == "PROCEED"
    assert g4_tag["gpt_decision"] is None or g4_tag["gpt_decision"] == ""
    assert g4_tag["mismatch"] == 0
    assert g4_tag["cell_warm"] == 1


async def test_g3_no_shadow_conn_no_side_effect() -> None:
    """Without shadow_conn, no shadow row is written — decision unaffected."""
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PASS
    assert result.payload["validated_signal"]["strength_scalar"] == 1.0


async def test_g3_crossed_book_kill_via_relocated_g4_rail(
    memdb: sqlite3.Connection,
) -> None:
    """P2a group A: the crossed-book KILL rail (relocated verbatim from G4)
    fires inside the merged G3 flow — a clean G3 technical PASS is overridden
    by the crossed-book KILL (broken microstructure, not a market judgment
    call)."""
    ctx = _ctx(
        {
            "raw_signal": {"strategy": "vb", "score": 1.0},
            "cell_routing": {"quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5},
            "tick_window": [
                {"ts": NOW, "bid": 100.2, "ask": 100.1, "mid": 100.15},  # crossed
            ],
            "spread_bps": 10.0,
            "baseline_p50_spread_bps": 8.0,
            "cell_quartile": "mid",
            "regime": "chop",
        },
        gate_id=3,
    )
    result = await signal_validator_gate(ctx, shadow_conn=memdb)
    assert result.decision == GateDecision.KILL
    assert result.payload["reason"] == "crossed_book"
    # P2a closeout: the comparisonless G3 row is dropped, and the crossed-book
    # KILL returns before the frontgate squeeze tap — so NO shadow row at all.
    rows = fetch_shadow_events(memdb)
    assert rows == []
