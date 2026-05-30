"""Layer 2 — orchestrator + state machine + mixed failure mode tests.

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md
"""

from __future__ import annotations

import sqlite3

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.pipeline import (
    GateContext,
    GateDecision,
    GateOrchestrator,
    GateResult,
    SignalLifecycle,
    run_signal_pipeline,
)
from polaris.core.pipeline.agents.adaptive_exit import can_widen_exit
from polaris.core.pipeline.agents.position_monitor import evaluate_strategy_swap
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_ENTRY_SIZER,
    GATE_POSITION_MONITOR,
    GATE_POST_TRADE_REFLECTOR,
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GATE_STRATEGY_SIGNAL,
    GATE_UNIVERSE_SCANNER,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
)

NOW = 1_780_000_000


def _ctx_cell() -> CellContext:
    return CellContext(group="spot_intraday", session="asia", direction="long", liquidity_tier="high")


def _seed_top_quartile_cell(conn: sqlite3.Connection) -> None:
    for i in range(25):
        for j in range(25):
            tr = TradeClose(
                key=CellKeyP0("okx", "volume_burst", f"PL{i:02d}-USDT", "bull_trend"),
                context=_ctx_cell(),
                pnl_r=(i - 12) * 0.1,
                won=(i - 12) * 0.1 > 0,
                closed_ts=NOW + j,
            )
            update_on_trade_close(conn, tr)


# ---------------------------------------------------------------------------
# Mock GPT client (records calls + returns predefined response)
# ---------------------------------------------------------------------------


class _MockGPTClient:
    """Mock that returns a predefined JSON text response (Anthropic-shape API)."""

    def __init__(self, response_text: str = "{}") -> None:
        self.response_text = response_text
        self.calls: list[dict] = []

        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)

                class _Block:
                    text = outer.response_text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _intent(symbol: str = "PL24-USDT") -> SignalIntent:
    return SignalIntent(
        signal_id="sig-x",
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id="crypto:PL",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="bull_trend",
        direction="long",
        signal_strength=1.2,
        listing_age_hours=72.0,
        leverage=1.0,
        base_risk_pct=0.02,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0,
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0},
        open_positions=[],
        fill_rate_active_cut=False,
    )


def _risk_state(n: int = 30) -> StrategyRiskState:
    return StrategyRiskState(
        venue="okx", strategy="volume_burst",
        closed_trades=n, kelly_p=0.55, kelly_q=0.45,
        kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


# ---------------------------------------------------------------------------
# Orchestrator: full sequential flow
# ---------------------------------------------------------------------------


async def test_8_gate_sequential_flow_validator_to_sizer(memdb: sqlite3.Connection) -> None:
    _seed_top_quartile_cell(memdb)
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')

    payload = {
        "raw_signal": {"strategy": "volume_burst", "direction": "long", "score": 1.2},
        "validated_signal": {"strategy": "volume_burst", "strength_scalar": 1.2, "direction": "long"},
        "tick_window": [],
        "signal_intent": _intent(),
        "risk_state": _risk_state(),
        "portfolio": _portfolio(),
        # Force fast-path eligibility off so watcher hits GPT.
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    # 1st call returns validator PASS, 2nd call (watcher) returns PROCEED.
    haiku.response_text = '{"decision": "PROCEED"}'

    # Run only G4 onwards — seed VALIDATED to satisfy the predecessor invariant.
    ctx, results = await run_signal_pipeline(
        run_id="r-1",
        venue="okx", symbol="PL24-USDT",
        strategy_id="volume_burst",
        payload=payload,
        conn=memdb,
        haiku_client=haiku,
        start_gate=GATE_PRE_ENTRY_WATCHER,
        initial_state=SignalLifecycle.VALIDATED,
    )

    # We started at G4 (Pre-Entry Watcher) — G5 sizer should run, then G6/G7.
    # G8 only fires when the trade has closed (EXIT_PENDING/CLOSED state); in
    # this happy path we end at G7 with state=ACTIVE (entry confirmed).
    decisions = [r.decision for r in results]
    assert GateDecision.PROCEED in decisions
    assert GateDecision.SIZED in decisions
    assert ctx.state in (
        SignalLifecycle.MONITORED,
        SignalLifecycle.ACTIVE,
        SignalLifecycle.SIZED,
        SignalLifecycle.WATCHED,
        SignalLifecycle.REFLECTED,
    )


async def test_validator_kill_terminates_pipeline(memdb: sqlite3.Connection) -> None:
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    payload = {"raw_signal": {"strategy": "vb", "direction": "long", "score": 0.4}}
    ctx, results = await run_signal_pipeline(
        run_id="r-2",
        venue="okx", symbol="BTC-USDT",
        strategy_id="vb",
        payload=payload,
        conn=memdb,
        haiku_client=haiku,
        start_gate=GATE_SIGNAL_VALIDATOR,
    )
    assert results[0].decision == GateDecision.KILL
    assert ctx.state == SignalLifecycle.KILLED


# ---------------------------------------------------------------------------
# Mixed failure mode (Q4)
# ---------------------------------------------------------------------------


def test_entry_gate_exception_fail_closed() -> None:
    # Helper static method check: Gate 5 exception → KILL.
    fb = GateOrchestrator._fallback_result(GATE_ENTRY_SIZER, error="boom", latency_ms=1)
    assert fb.decision == GateDecision.KILL
    assert fb.next_gate is None


def test_position_gate_exception_fail_open() -> None:
    fb = GateOrchestrator._fallback_result(GATE_POSITION_MONITOR, error="boom", latency_ms=1)
    assert fb.decision == GateDecision.HOLD
    assert fb.next_gate == GATE_ADAPTIVE_EXIT


def test_universe_gate_exception_fail_open_to_strategy() -> None:
    fb = GateOrchestrator._fallback_result(GATE_UNIVERSE_SCANNER, error="boom", latency_ms=1)
    assert fb.decision == GateDecision.PASS
    assert fb.next_gate == GATE_STRATEGY_SIGNAL


def test_validator_gate_exception_fail_closed() -> None:
    fb = GateOrchestrator._fallback_result(GATE_SIGNAL_VALIDATOR, error="boom", latency_ms=1)
    assert fb.decision == GateDecision.KILL


def test_pre_entry_watcher_exception_fail_closed() -> None:
    fb = GateOrchestrator._fallback_result(GATE_PRE_ENTRY_WATCHER, error="boom", latency_ms=1)
    assert fb.decision == GateDecision.KILL


def test_post_trade_reflector_exception_fail_open() -> None:
    fb = GateOrchestrator._fallback_result(GATE_POST_TRADE_REFLECTOR, error="boom", latency_ms=1)
    # Reflector is observability-only (Q4): fail-open ends with REFLECTED, not HOLD.
    assert fb.decision == GateDecision.REFLECTED
    assert fb.next_gate is None


# ---------------------------------------------------------------------------
# Q7 Fast-path (G4 only)
# ---------------------------------------------------------------------------


async def test_fast_path_high_conviction_skip(memdb: sqlite3.Connection) -> None:
    """Top quartile + strong signal + tight spread + clean → fast-path bypass."""
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.30, "strategy": "vb"},
        "cell_quartile": "top",
        "spread_bps": 2.0,
        "baseline_p50_spread_bps": 4.0,
        "recent_reject_in_6h": False,
        "listing_age_hours": 100.0,
        "session_open_shock_window": False,
    }
    # Run only G4 (so we observe the result directly).
    orch = GateOrchestrator(conn=memdb, haiku_client=haiku)
    ctx = GateContext(
        run_id="r-3", signal_id="sig-3", position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=dict(payload), started_ts=NOW,
        state=SignalLifecycle.VALIDATED,  # G4 prereq
    )
    # Override handlers to stop after G4 (no G5).
    orch.handlers = {GATE_PRE_ENTRY_WATCHER: orch._wrap_watcher}
    results = await orch.run(ctx, start_gate=GATE_PRE_ENTRY_WATCHER)
    assert results[0].decision == GateDecision.PROCEED
    assert results[0].skipped is True
    assert results[0].model_used == "python_fast_path"
    # No GPT call should have been made.
    assert haiku.calls == []


async def test_fast_path_below_strength_floor_calls_gpt(memdb: sqlite3.Connection) -> None:
    haiku = _MockGPTClient(response_text='{"decision": "PROCEED"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.0},
        "cell_quartile": "top",
        "spread_bps": 2.0,
        "baseline_p50_spread_bps": 4.0,
        "recent_reject_in_6h": False,
        "listing_age_hours": 100.0,
        "session_open_shock_window": False,
    }
    orch = GateOrchestrator(conn=memdb, haiku_client=haiku)
    orch.handlers = {GATE_PRE_ENTRY_WATCHER: orch._wrap_watcher}
    ctx = GateContext(
        run_id="r-4", signal_id="sig-4", position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=dict(payload), started_ts=NOW,
        state=SignalLifecycle.VALIDATED,
    )
    results = await orch.run(ctx, start_gate=GATE_PRE_ENTRY_WATCHER)
    assert results[0].decision == GateDecision.PROCEED
    assert results[0].skipped is False
    assert len(haiku.calls) == 1


# ---------------------------------------------------------------------------
# Q8 same-correlation-group swap
# ---------------------------------------------------------------------------


def test_swap_strategy_same_correlation_group_only() -> None:
    pos = {"correlation_group": "btc-cluster", "side": "buy", "venue": "okx", "symbol": "BTC-USDT", "strategy": "vb"}
    cand_ok = {"correlation_group": "btc-cluster", "side": "buy", "venue": "okx", "symbol": "BTC-USDT", "strategy": "tsmom"}
    cand_bad = {"correlation_group": "eth-cluster", "side": "buy", "venue": "okx", "symbol": "ETH-USDT", "strategy": "tsmom"}
    cand_side_bad = {"correlation_group": "btc-cluster", "side": "sell", "venue": "okx", "symbol": "BTC-USDT", "strategy": "tsmom"}
    assert evaluate_strategy_swap(position=pos, candidate=cand_ok) is True
    assert evaluate_strategy_swap(position=pos, candidate=cand_bad) is False
    assert evaluate_strategy_swap(position=pos, candidate=cand_side_bad) is False


# ---------------------------------------------------------------------------
# Q9 adaptive exit floor-only widening
# ---------------------------------------------------------------------------


def test_adaptive_exit_floor_only_widening_long() -> None:
    # Long: new_stop must be < current_stop (further from entry).
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=100.0,
        proposed_stop_price=99.0,
        entry_price=110.0,
        unrealized_pnl_r=1.0,
        max_loss_r=2.0,
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert ok, reason


def test_adaptive_exit_widen_below_window_rejects() -> None:
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=100.0,
        proposed_stop_price=99.0,
        entry_price=110.0,
        unrealized_pnl_r=0.5,  # below 0.7 floor
        max_loss_r=2.0,
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert not ok
    assert reason == "below_widen_window"


def test_adaptive_exit_widen_tight_rejects() -> None:
    # Long: proposed >= current = NOT widening.
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=100.0,
        proposed_stop_price=100.5,  # tighter, not wider
        entry_price=110.0,
        unrealized_pnl_r=1.0,
        max_loss_r=2.0,
        overrides_used=0,
        seconds_since_last_override=60,
    )
    assert not ok
    assert reason == "not_widening_long"


def test_adaptive_exit_cooldown_rejects() -> None:
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=100.0,
        proposed_stop_price=99.0,
        entry_price=110.0,
        unrealized_pnl_r=1.0,
        max_loss_r=2.0,
        overrides_used=1,
        seconds_since_last_override=10,
    )
    assert not ok
    assert reason == "cooldown"


def test_adaptive_exit_override_cap_rejects() -> None:
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=100.0, proposed_stop_price=99.0,
        entry_price=110.0, unrealized_pnl_r=1.0, max_loss_r=2.0,
        overrides_used=5, seconds_since_last_override=60,
    )
    assert not ok
    assert reason == "override_cap"


def test_adaptive_exit_initial_stop_floor_long() -> None:
    """When initial_stop_price is provided, it is the canonical floor (Q9)."""
    # Long: floor = 95.0, proposed 94.0 → below floor → reject.
    ok, reason = can_widen_exit(
        side="long",
        current_stop_price=98.0,
        proposed_stop_price=94.0,
        entry_price=100.0,
        unrealized_pnl_r=1.5,
        max_loss_r=1.0,
        overrides_used=0,
        seconds_since_last_override=60,
        initial_stop_price=95.0,
    )
    assert not ok
    assert reason == "below_max_loss"


def test_adaptive_exit_initial_stop_floor_short() -> None:
    """Short: floor at 105 — proposed 106 above floor → reject."""
    ok, reason = can_widen_exit(
        side="short",
        current_stop_price=102.0,
        proposed_stop_price=106.0,
        entry_price=100.0,
        unrealized_pnl_r=1.5,
        max_loss_r=1.0,
        overrides_used=0,
        seconds_since_last_override=60,
        initial_stop_price=105.0,
    )
    assert not ok
    assert reason == "above_max_loss"


# ---------------------------------------------------------------------------
# Lifecycle invariant (codex round 1 P0-1)
# ---------------------------------------------------------------------------


async def test_lifecycle_invariant_blocks_skip_to_sizer(memdb: sqlite3.Connection) -> None:
    """Starting at G5 (Entry Sizer) from RAW state must be rejected.

    G5 prereq is {RAW, WATCHED} — actually RAW is allowed (orchestrator
    seeds RAW for tests/smoke). Let's pick a gate whose prereq excludes RAW:
    G7 (Adaptive Exit) requires {RAW, ACTIVE, MONITORED, EXIT_PENDING} —
    RAW is permitted as a debug shortcut. So the only real invariant test
    is when caller seeds an explicit non-permitted state.
    """
    haiku = _MockGPTClient()
    payload = {"any": "thing"}
    # Use the orchestrator directly so we can force a state mismatch.
    orch = GateOrchestrator(conn=memdb, haiku_client=haiku)
    ctx = GateContext(
        run_id="r-inv", signal_id="sig", position_id=None,
        gate_id=GATE_ENTRY_SIZER,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload=dict(payload), started_ts=NOW,
        state=SignalLifecycle.REFLECTED,  # cannot regress to SIZED
    )
    results = await orch.run(ctx, start_gate=GATE_ENTRY_SIZER)
    # Synthetic KILL fired due to invariant violation.
    assert results[0].decision == GateDecision.KILL
    assert "lifecycle_invariant" in str(results[0].error)
    assert ctx.state == SignalLifecycle.KILLED


async def test_lifecycle_exit_pending_transitions_to_closed_then_reflected(
    memdb: sqlite3.Connection,
) -> None:
    """G7 in EXIT_PENDING state advances to CLOSED then G8 → REFLECTED."""
    haiku = _MockGPTClient(response_text='{"lesson_type":"ok","confidence":0.9,"lesson_text":"x","delta":{}}')
    payload = {
        "current_stop_price": 100.0,
        "closed_trade": {"trade_id": "t-flow", "strategy_id": "vb"},
        "closed_trade_count": 50,
    }
    ctx, results = await run_signal_pipeline(
        run_id="r-flow",
        venue="okx", symbol="BTC-USDT",
        strategy_id="vb",
        payload=payload,
        conn=memdb,
        haiku_client=haiku,
        start_gate=GATE_ADAPTIVE_EXIT,
        initial_state=SignalLifecycle.EXIT_PENDING,
    )
    # G7 transitions to CLOSED, then G8 dispatches and ends REFLECTED.
    assert ctx.state == SignalLifecycle.REFLECTED
    last = results[-1]
    assert last.decision == GateDecision.REFLECTED


def test_lifecycle_active_state_assigned_on_first_monitor(memdb: sqlite3.Connection) -> None:
    """Re-uses the orchestrator helper to verify SIZED → ACTIVE transition."""
    ctx = GateContext(
        run_id="r-active", signal_id="s", position_id=None, gate_id=GATE_POSITION_MONITOR,
        venue="okx", symbol="BTC-USDT", strategy_id="vb",
        payload={}, started_ts=NOW, state=SignalLifecycle.SIZED,
    )
    fake_result = GateResult(
        decision=GateDecision.HOLD,
        next_gate=GATE_ADAPTIVE_EXIT,
        payload={},
        model_used="python",
    )
    GateOrchestrator._update_lifecycle(ctx, GATE_POSITION_MONITOR, fake_result)
    assert ctx.state == SignalLifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Day 6 R2 P0/P1 phase invariant — G8 must be Python template at P0 even
# when a GPT client is wired (spec: layer-2 Q3 + ADR-004 §Phase).
# ---------------------------------------------------------------------------


async def test_g8_p0_phase_forces_python_even_with_gpt_client(
    memdb: sqlite3.Connection,
) -> None:
    """``GateOrchestrator(phase='P0')`` must suppress the Haiku client for G8."""
    haiku = _MockGPTClient(
        response_text='{"lesson_type":"ok","confidence":0.9,"lesson_text":"x","delta":{}}'
    )
    payload = {
        "current_stop_price": 100.0,
        "closed_trade": {"trade_id": "t-p0", "strategy_id": "vb"},
        "closed_trade_count": 5,
    }
    ctx, results = await run_signal_pipeline(
        run_id="r-p0",
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=payload,
        conn=memdb,
        haiku_client=haiku,
        start_gate=GATE_ADAPTIVE_EXIT,
        initial_state=SignalLifecycle.EXIT_PENDING,
        phase="P0",
    )
    # G8 must report model_used="python" — GPT client suppressed at P0.
    g8_result = next(r for r in results if r.decision == GateDecision.REFLECTED)
    assert g8_result.model_used == "python"
    assert ctx.state == SignalLifecycle.REFLECTED


async def test_g8_p1_phase_stays_python_gpt_removed(
    memdb: sqlite3.Connection,
) -> None:
    """``phase='P1'`` no longer upgrades G8 to GPT — it stays deterministic.

    ai_conductor P2 (2026-05-30) removed the G8 GPT lesson branch (the real
    learning is posterior NIG + cell EWMA; ``ai_lessons`` has zero readers).
    The reflector is now a Python template at every phase, so a P1 orchestrator
    must still report ``model_used="python"`` and never call the client.
    """
    haiku = _MockGPTClient(
        response_text='{"lesson_type":"ok","confidence":0.9,"lesson_text":"x","delta":{}}'
    )
    payload = {
        "current_stop_price": 100.0,
        "closed_trade": {"trade_id": "t-p1", "strategy_id": "vb"},
        "closed_trade_count": 5,
    }
    ctx, results = await run_signal_pipeline(
        run_id="r-p1",
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=payload,
        conn=memdb,
        haiku_client=haiku,
        start_gate=GATE_ADAPTIVE_EXIT,
        initial_state=SignalLifecycle.EXIT_PENDING,
        phase="P1",
    )
    g8_result = next(r for r in results if r.decision == GateDecision.REFLECTED)
    assert g8_result.model_used == "python"
    assert ctx.state == SignalLifecycle.REFLECTED


def test_orchestrator_rejects_invalid_phase() -> None:
    import pytest

    with pytest.raises(ValueError, match="phase"):
        GateOrchestrator(phase="P2")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Day 6 R5 P0 Python-template invariant — G8 must persist a deterministic
# lesson + clamped delta even without an LLM client (spec: layer-2 Q3/Q10 +
# ADR-007 §learner_network "every closed trade emits a row").
# ---------------------------------------------------------------------------


async def test_g8_p0_python_template_writes_lesson_row(
    memdb: sqlite3.Connection, tmp_path: object,
) -> None:
    """P0 path (no client) writes ai_lessons row + vault file deterministically."""
    from polaris.core.pipeline.agents.post_trade_reflector import (
        post_trade_reflector_gate,
    )
    from polaris.core.pipeline.gate_state import GATE_POST_TRADE_REFLECTOR

    closed = {
        "trade_id": "py-tpl-1",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 1.5,
        "won": True,
    }
    ctx = GateContext(
        run_id="r-pytpl",
        signal_id="s-pytpl",
        position_id="p-pytpl",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload={"closed_trade": closed, "closed_trade_count": 5},
        started_ts=NOW,
        state=SignalLifecycle.CLOSED,
    )
    from pathlib import Path as _Path

    lessons_dir = _Path(str(tmp_path)) / "lessons"  # type: ignore[arg-type]
    res = await post_trade_reflector_gate(
        ctx, client=None, conn=memdb, lessons_dir=lessons_dir
    )
    assert res.decision == GateDecision.REFLECTED
    assert res.model_used == "python"
    assert res.payload.get("source") == "python_template"
    # ai_lessons row written.
    rows = memdb.execute(
        "SELECT trade_id, strategy_id, lesson_type, confidence, delta_json "
        "FROM ai_lessons WHERE trade_id = ?",
        (closed["trade_id"],),
    ).fetchall()
    assert len(rows) == 1
    # Vault file written.
    files = list(lessons_dir.glob(f"{closed['trade_id']}_*.md"))
    assert len(files) == 1


async def test_g8_p0_python_template_clamps_delta_to_rail(
    memdb: sqlite3.Connection,
) -> None:
    """P0 Δ entries must respect ±LESSON_DELTA_CLAMP_P0 = ±0.03 cap."""
    from polaris.core.pipeline.agents.post_trade_reflector import (
        LESSON_DELTA_CLAMP_P0,
        post_trade_reflector_gate,
    )
    from polaris.core.pipeline.gate_state import GATE_POST_TRADE_REFLECTOR

    closed = {
        "trade_id": "py-clamp-1",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": 5.0,  # huge win — sign +
        "won": True,
    }
    ctx = GateContext(
        run_id="r-clamp",
        signal_id="s-clamp",
        position_id="p-clamp",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload={
            "closed_trade": closed,
            "closed_trade_count": 200,  # past soft mode → no soft scalar
        },
        started_ts=NOW,
        state=SignalLifecycle.CLOSED,
    )
    res = await post_trade_reflector_gate(ctx, client=None, conn=memdb)
    delta = res.payload["delta"]
    assert delta, "delta must be populated even at P0"
    for v in delta.values():
        assert abs(v) <= LESSON_DELTA_CLAMP_P0 + 1e-9, f"delta {v} exceeds rail"
