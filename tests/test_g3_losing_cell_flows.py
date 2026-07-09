"""G3 losing-cell flow (flow_not_block) — losing is NEVER an entry block.

DEMO/PAPER paper bot. Mandate ``flow_not_block`` / ``no_block_filter_architecture``:
a cell being "losing" (avg_pnl_r < 0) must NOT block entry. Loss-defense lives at
EXIT (peak-protect, fee-net), never at the entry gate. This pins the removal of the
two former G3 loss-based KILL branches in ``technical_validate_decision``:

- former Rule 1b ``warm_pool_local_bottom_losing`` (cold-quartile + warm + losing
  + local-bottom → KILL) — REMOVED.
- former Rule 2 ``warm_bottom_losing`` (warm + quartile='bottom' + losing → KILL)
  — REMOVED.

Both losing cases now FLOW (PASS or conservative MODIFY, never KILL). The genuine
micro-structure KILLs (G4 crossed/stale book) and the fail-closed entry KILLs
(missing_raw_signal etc.) are preserved — pinned here and in test_ai_free_cutover.
"""

from __future__ import annotations

import time

import pytest

from polaris.core.pipeline.agents._shadow_rules import (
    G3ShadowInputs,
    G4ShadowInputs,
    technical_validate_decision,
    technical_watch_decision,
)
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GateContext,
    GateDecision,
    SignalLifecycle,
)


class _ForbiddenClient:
    """Explodes on any attribute access — proves the AI-free path is pure."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"GPT client touched in AI-free mode: {name}")


def _g3_ctx(
    *,
    quartile: str,
    n_eff: float,
    avg_pnl_r: float,
    score: float = 0.0,
    regime: str = "trend_up",
) -> GateContext:
    return GateContext(
        run_id="run-flow",
        signal_id="sig-flow",
        position_id=None,
        gate_id=3,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "raw_signal": {"symbol": "BTC-USDT", "side": "long", "strength": 1.2},
            "cell_routing": {
                "quartile": quartile,
                "n_eff": n_eff,
                "avg_pnl_r": avg_pnl_r,
                "score": score,
            },
            "baseline": {},
            "recent_trades": [],
            "regime": regime,
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )


# ===========================================================================
# (a) losing cells FLOW — pure rule level
# ===========================================================================


def test_former_rule2_warm_bottom_losing_now_flows() -> None:
    """WARM + quartile='bottom' + avg_pnl_r<0 → was KILL, now FLOWS (no KILL)."""
    inp = G3ShadowInputs(n_eff=8.0, quartile="bottom", avg_pnl_r=-0.4)
    out = technical_validate_decision(inp)
    assert out.decision != GateDecision.KILL
    assert "losing" not in out.reason


def test_former_rule2_warm_bottom_losing_reaches_warm_pass() -> None:
    """A warm bottom-quartile losing cell now reaches the final warm PASS."""
    inp = G3ShadowInputs(n_eff=8.0, quartile="bottom", avg_pnl_r=-0.4)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.PASS
    assert out.scalar == 1.0


def test_former_rule1b_cold_quartile_warm_losing_now_modifies_not_kill() -> None:
    """Former Rule 1b case (cold-quartile + warm + losing) now → MODIFY, not KILL.

    With the warm_pool_local_bottom discriminator removed, a cold-labelled
    quartile that is warm + losing falls through to the conservative MODIFY trim
    (precise sizing, NOT a block).
    """
    inp = G3ShadowInputs(n_eff=8.0, quartile="cold", avg_pnl_r=-0.4)
    out = technical_validate_decision(inp)
    assert out.decision == GateDecision.MODIFY
    assert "losing" not in out.reason


def test_cold_quartile_warm_losing_no_local_bottom_field() -> None:
    """The warm_pool_local_bottom discriminator field is gone from inputs."""
    assert "warm_pool_local_bottom" not in G3ShadowInputs.__dataclass_fields__


def test_net_edge_still_absent() -> None:
    assert "net_edge_r" not in G3ShadowInputs.__dataclass_fields__


# ===========================================================================
# (c) losing cell FLOWS through the AI-free gate (no KILL); fail-closed kept
# ===========================================================================


@pytest.mark.asyncio
async def test_ai_free_warm_bottom_losing_now_flows() -> None:
    """AI-free G3: warm bottom losing cell now PASSES (was KILL warm_bottom_losing)."""
    res = await signal_validator_gate(
        _g3_ctx(quartile="bottom", n_eff=8.0, avg_pnl_r=-0.4),
        client=_ForbiddenClient(),
        ai_free=True,
    )
    assert res.decision != GateDecision.KILL
    assert res.model_used == "python"
    assert res.next_gate is not None


@pytest.mark.asyncio
async def test_ai_free_cold_quartile_warm_losing_now_flows() -> None:
    """AI-free G3: cold-quartile warm losing cell now MODIFIES (was KILL)."""
    res = await signal_validator_gate(
        _g3_ctx(quartile="cold", n_eff=8.0, avg_pnl_r=-0.4),
        client=_ForbiddenClient(),
        ai_free=True,
    )
    assert res.decision != GateDecision.KILL
    assert res.model_used == "python"


@pytest.mark.asyncio
async def test_ai_free_missing_raw_signal_still_kills() -> None:
    """Fail-closed KILL preserved: empty raw_signal still KILLs (not a loss-block)."""
    ctx = _g3_ctx(quartile="top", n_eff=10.0, avg_pnl_r=0.5)
    ctx.payload["raw_signal"] = {}
    res = await signal_validator_gate(ctx, client=_ForbiddenClient(), ai_free=True)
    assert res.decision == GateDecision.KILL
    assert res.payload["reason"] == "missing_raw_signal"


# ===========================================================================
# (b) crossed-book KILL preserved; stale-book downgraded to FLAG (NOT loss-block)
# ===========================================================================


def test_g4_crossed_book_kill_preserved() -> None:
    inp = G4ShadowInputs(best_bid=100.2, best_ask=100.1, last_tick_age_sec=1.0)
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.KILL
    assert out.reason == "crossed_book"


def test_g4_stale_book_downgraded_to_flag() -> None:
    """Stale-vs-fallback-bound is a FLAG on PROCEED (flow_not_block), not KILL."""
    inp = G4ShadowInputs(best_bid=100.0, best_ask=100.1, last_tick_age_sec=120.0)
    out = technical_watch_decision(inp)
    assert out.decision == GateDecision.PROCEED
    assert out.reason == "proceed_flagged"
    assert "stale_book" in out.flags
