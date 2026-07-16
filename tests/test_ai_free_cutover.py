"""W3 AI-free cutover — ``POLARIS_AI_FREE`` flag (default ON).

DEMO/PAPER paper bot. Spec SSOT: ``.claude/plans/organic_ops_ai_free_2026-06-11.md``
§1 W3 (Jin 2026-06-11 — in-loop LLM calls = 0 by default).

Historically: flag=1 (default) ran G3/G4/G7's deterministic technical
decisions as primary; flag=0 ran the legacy GPT path byte-identical.

P2a (2026-07-16): the legacy GPT paths for G3/G7 are deleted outright
(group B) and Gate 4 (Pre-Entry Watcher) is abolished as a decision step —
its content relocated into G3 (group A). ``ai_free_mode()`` itself is still
a live parser (pinned below), but G3/G7 no longer branch on it at all —
the technical rule is unconditionally the decision regardless of the flag.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.core.pipeline.agents._shadow_rules import MODIFY_CONSERVATIVE_SCALAR
from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.config import ai_free_mode
from polaris.core.pipeline.gate_orchestrator import run_signal_pipeline
from polaris.core.pipeline.gate_state import (
    GATE_ENTRY_SIZER,
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ForbiddenClient:
    """A client object that explodes on ANY attribute access."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"GPT client attribute touched in AI-free mode: {name}")


def _g3_ctx(
    *,
    quartile: str = "top",
    n_eff: float = 10.0,
    avg_pnl_r: float = 0.5,
    score: float = 0.5,
    regime: str = "trend_up",
    tick: dict[str, Any] | None = None,
    started_ts: int | None = None,
    cell_quartile: str = "mid",
) -> GateContext:
    """A merged G3 context carrying both G3's own inputs AND G4's former
    inputs (tick_window/spread/etc.) — matching production reality: the
    orchestrator primes ``ctx.payload`` with everything up front before the
    pipeline starts (P2a group A — G4 is folded into G3)."""
    now = int(time.time())
    if tick is None:
        tick = {"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}
    return GateContext(
        run_id="run-aifree",
        signal_id="sig-1",
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
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
            "tick_window": [tick],
            "cell_quartile": cell_quartile,
        },
        started_ts=started_ts if started_ts is not None else now,
        state=SignalLifecycle.RAW,
    )


def _g7_ctx(*, unrealized_pnl_r: float = 1.0) -> GateContext:
    return GateContext(
        run_id="run-aifree",
        signal_id="sig-1",
        position_id="pos-1",
        gate_id=7,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "widen_proposal": {
                "side": "long",
                "current_stop_price": 95.0,
                "proposed_stop_price": 93.0,
                "entry_price": 100.0,
                "unrealized_pnl_r": unrealized_pnl_r,
                "max_loss_r": 1.0,
                "overrides_used": 0,
                "seconds_since_last_override": 60,
                "initial_stop_price": 90.0,
            },
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.MONITORED,
    )


@pytest.fixture
def ai_free_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_AI_FREE", "1")


@pytest.fixture
def ai_free_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_AI_FREE", "0")


# ---------------------------------------------------------------------------
# Flag semantics (the parser itself — still a live, tested function even
# though G3/G7 no longer consult it)
# ---------------------------------------------------------------------------


def test_flag_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_AI_FREE", raising=False)
    assert ai_free_mode() is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),  # unset → ON (Jin 지시 기본)
        ("", True),  # empty = unset → ON
        ("1", True),
        ("true", True),
        ("ON", True),
        ("0", False),
        ("false", False),
        ("off", False),
    ],
)
def test_flag_parse(
    raw: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The raw=None case falls through to the real env — pin it so an exported
    # POLARIS_AI_FREE=0 in the outer shell cannot flip this test.
    monkeypatch.delenv("POLARIS_AI_FREE", raising=False)
    assert ai_free_mode(raw) is expected


@pytest.mark.asyncio
async def test_g3_ai_free_param_is_ignored_deterministic_always_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2a group B: ``ai_free``/``client`` are compat-only now — G3's legacy
    GPT branch is deleted outright, so an env that says "legacy" (0) plus an
    exploding client still never gets touched; the technical rule always
    drives."""
    monkeypatch.setenv("POLARIS_AI_FREE", "0")
    res = await signal_validator_gate(
        _g3_ctx(quartile="top"), client=_ForbiddenClient(), ai_free=False
    )
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"


# ---------------------------------------------------------------------------
# loop-level client resolution — flag=1 builds NO client at all
# ---------------------------------------------------------------------------


def test_resolve_gpt_client_ai_free_returns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """flag=1 (default): the loop never constructs a GPT client — the gates'
    deterministic primaries run with client=None (mutation guard for the
    loop-level ``if ai_free_mode():`` branch)."""
    from polaris.scripts.production_paper_loop import _resolve_gpt_client

    monkeypatch.delenv("POLARIS_AI_FREE", raising=False)
    with caplog.at_level("INFO"):
        client = _resolve_gpt_client(None)
    assert client is None
    assert any("AI-FREE" in r.message for r in caplog.records)


def test_resolve_gpt_client_legacy_falls_back_to_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flag=0: the legacy factory→Stub fallback is byte-identical — a factory
    RuntimeError degrades to the permissive StubGPTClient."""
    import polaris.core.pipeline.agents._gpt_client as gpt_client_mod
    from polaris.scripts.production_paper_loop import (
        StubGPTClient,
        _resolve_gpt_client,
    )

    monkeypatch.setenv("POLARIS_AI_FREE", "0")

    def _boom() -> Any:
        raise RuntimeError("no key")

    monkeypatch.setattr(gpt_client_mod, "default_gpt_factory", _boom)
    client = _resolve_gpt_client(None)
    assert isinstance(client, StubGPTClient)


def test_resolve_gpt_client_passthrough_explicit() -> None:
    """An explicitly injected client (tests / legacy callers) is returned
    untouched regardless of the flag."""
    from polaris.scripts.production_paper_loop import _resolve_gpt_client

    sentinel = object()
    assert _resolve_gpt_client(sentinel) is sentinel


# ---------------------------------------------------------------------------
# G3 (+ former G4, relocated P2a group A) — deterministic primary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g3_ai_free_warm_top_pass(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
    res = await signal_validator_gate(
        _g3_ctx(quartile="top"), client=_ForbiddenClient(), shadow_conn=memdb
    )
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"
    assert res.next_gate == GATE_ENTRY_SIZER
    assert res.payload["validated_signal"]["strength_scalar"] == 1.0
    # P2a group B/A: Only the relocated G4 frontgate
    # squeeze tag (gate_id=4, a REAL preserved instrument) logs.
    rows = fetch_shadow_events(memdb)
    assert len(rows) == 1  # G4 frontgate tag only — comparisonless G3 row dropped (P2a closeout)
    assert rows[0]["gate_id"] == 4
    assert all(r["gpt_decision"] is None or r["gpt_decision"] == "" for r in rows)


@pytest.mark.asyncio
async def test_g3_ai_free_warm_mid_modify_scalar(ai_free_on: None) -> None:
    res = await signal_validator_gate(
        _g3_ctx(quartile="mid"), client=_ForbiddenClient()
    )
    assert res.decision == GateDecision.MODIFY
    assert res.model_used == "python"
    assert res.next_gate == GATE_ENTRY_SIZER
    assert (
        res.payload["validated_signal"]["strength_scalar"]
        == MODIFY_CONSERVATIVE_SCALAR
    )


@pytest.mark.asyncio
async def test_g3_ai_free_warm_bottom_losing_now_flows(ai_free_on: None) -> None:
    # flow_not_block: a warm bottom-quartile losing cell is NOT blocked — losing
    # is never an entry block (loss-defense lives at EXIT). Now PASSES (was the
    # removed warm_bottom_losing KILL).
    res = await signal_validator_gate(
        _g3_ctx(quartile="bottom", avg_pnl_r=-0.4), client=_ForbiddenClient()
    )
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"
    assert res.next_gate == GATE_ENTRY_SIZER
    assert "losing" not in res.payload["reason"]


@pytest.mark.asyncio
async def test_g3_ai_free_cold_cell_passthrough(ai_free_on: None) -> None:
    # Cold cell = pass-through ALWAYS (모호하면 통과) — no new entry block.
    res = await signal_validator_gate(
        _g3_ctx(quartile="cold", n_eff=1.0, avg_pnl_r=0.0), client=_ForbiddenClient()
    )
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"


@pytest.mark.asyncio
async def test_g3_ai_free_cold_quartile_warm_losing_now_modifies(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
    # flow_not_block: a cold-quartile warm losing cell (the former local-bottom
    # KILL case) now gets a conservative MODIFY trim — never blocked. The conn no
    # longer feeds any loss-discriminator; the signal flows on.
    res = await signal_validator_gate(
        _g3_ctx(quartile="cold", n_eff=8.0, avg_pnl_r=-0.3, score=-0.5),
        client=_ForbiddenClient(),
        shadow_conn=memdb,
    )
    assert res.decision == GateDecision.MODIFY
    assert res.model_used == "python"
    assert res.next_gate == GATE_ENTRY_SIZER
    assert "losing" not in res.payload["reason"]


@pytest.mark.asyncio
async def test_g3_ai_free_missing_raw_signal_kill_unchanged(ai_free_on: None) -> None:
    ctx = _g3_ctx()
    ctx.payload["raw_signal"] = {}
    res = await signal_validator_gate(ctx, client=_ForbiddenClient())
    assert res.decision == GateDecision.KILL
    assert res.payload["reason"] == "missing_raw_signal"
    assert res.model_used == "python"


@pytest.mark.asyncio
async def test_g3_crossed_book_kill(ai_free_on: None) -> None:
    """Relocated G4 rail: a crossed book (bid >= ask) KILLs even though G3's
    OWN technical rule never would (microstructure broken, not a market
    judgment call)."""
    now = int(time.time())
    res = await signal_validator_gate(
        _g3_ctx(tick={"ts": now, "bid": 100.2, "ask": 100.1, "mid": 100.15}),
        client=_ForbiddenClient(),
    )
    assert res.decision == GateDecision.KILL
    assert res.model_used == "python"
    assert res.payload["reason"] == "crossed_book"


@pytest.mark.asyncio
async def test_g3_stale_book_flags_not_kill(ai_free_on: None) -> None:
    """No per-ticker cadence baseline in payload → fixed fallback bound →
    stale is a FLAG (flow_not_block), never a KILL."""
    now = int(time.time())
    res = await signal_validator_gate(
        _g3_ctx(
            tick={"ts": now - 1000, "bid": 100.0, "ask": 100.1, "mid": 100.05},
            started_ts=now,
        ),
        client=_ForbiddenClient(),
    )
    assert res.decision in (GateDecision.PASS, GateDecision.MODIFY)
    assert res.model_used == "python"
    assert "stale_book" in res.payload.get("watch_flags", [])


@pytest.mark.asyncio
async def test_g3_fast_path_structurally_unreachable_via_live_gate(
    ai_free_on: None,
) -> None:
    """Fast-path eligibility requires signal_strength >= 1.25, but G3's OWN
    technical rule never emits a scalar above 1.0 (PASS=1.0, MODIFY<=1.0) —
    so even a clean top-quartile/tight-spread/aged listing never triggers it
    through the live gate. This mirrors PRE-EXISTING production reality (the
    same cap applied when G4 ran separately and read G3's already-stamped
    validated_signal — the gate audit measured G4 100% no-op / PROCEED,
    never fast-path). The eligibility FUNCTION itself is unchanged/tested
    directly in test_layer2_gpt_gates.py — this only pins that the live gate
    never manufactures an artificial strength override."""
    ctx = _g3_ctx(quartile="top", cell_quartile="top")
    ctx.payload["spread_bps"] = 1.0
    ctx.payload["baseline_p50_spread_bps"] = 5.0
    ctx.payload["listing_age_hours"] = 100.0
    res = await signal_validator_gate(ctx, client=_ForbiddenClient())
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"  # NOT python_fast_path — structurally capped
    assert res.skipped is False


# ---------------------------------------------------------------------------
# G7 — deterministic primary (flag=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g7_ai_free_widen_rails_primary(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
    res = await adaptive_exit_gate(
        _g7_ctx(unrealized_pnl_r=1.0), client=_ForbiddenClient(), shadow_conn=memdb
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.model_used == "python"
    assert res.payload["widening_applied"] is True
    assert res.payload["stop_price"] == 93.0
    # P2a group B: the Q9 rail decision is still logged for measurement
    # continuity — gpt_decision is None now (no GPT call to compare against).
    rows = fetch_shadow_events(memdb, gate_id=7)
    # P2a conductor closeout (2026-07-16): comparisonless live-path shadow
    # writes are dropped — gate_events records the decision instead.
    assert rows == []


@pytest.mark.asyncio
async def test_g7_ai_free_hold_below_window(ai_free_on: None) -> None:
    res = await adaptive_exit_gate(
        _g7_ctx(unrealized_pnl_r=0.1), client=_ForbiddenClient()
    )
    assert res.decision == GateDecision.HOLD
    assert res.model_used == "python"
    assert res.payload["reason"] == "below_widen_window"
    assert res.payload["stop_price"] == 95.0


# ---------------------------------------------------------------------------
# Orchestrator e2e — gate_events carry model_used='python' (flag=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_g3_ai_free_gate_events(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
    """P2a group A: the orchestrator no longer runs a separate G4 step — G3
    wires directly to G5 (SIZED or a sizing-side KILL), so gate_events never
    gets a gate_id=4 row (natural — G4 no longer exists as a step)."""
    now = int(time.time())
    payload = {
        "signal_id": "sig-e2e",
        "raw_signal": {"symbol": "BTC-USDT", "side": "long", "strength": 1.2},
        "cell_routing": {
            "quartile": "top", "n_eff": 10.0, "avg_pnl_r": 0.5, "score": 0.5,
        },
        "baseline": {},
        "recent_trades": [],
        "regime": "trend_up",
        "tick_window": [{"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}],
        "cell_quartile": "mid",
    }
    _ctx, results = await run_signal_pipeline(
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload=payload,
        conn=memdb,
        haiku_client=_ForbiddenClient(),
        start_gate=GATE_SIGNAL_VALIDATOR,
        phase="P1",
    )
    rows = memdb.execute(
        "SELECT gate_id, decision, model_used FROM gate_events ORDER BY gate_id"
    ).fetchall()
    assert (3, "PASS", "python") in rows
    assert all(r[0] != GATE_PRE_ENTRY_WATCHER for r in rows)  # no G4 row — natural
    # Only the relocated G4 frontgate squeeze tag
    # (gate_id=4, a REAL preserved instrument) still logs.
    shadow_rows = fetch_shadow_events(memdb)
    assert len(shadow_rows) == 1  # G4 frontgate tag only — comparisonless G3 row dropped (P2a closeout)
    assert shadow_rows[0]["gate_id"] == 4
    assert all(r["mismatch"] == 0 for r in shadow_rows)


# ---------------------------------------------------------------------------
# P2a group B — G3/G7 GPT call removed UNCONDITIONALLY (flag=0 too)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g3_flag_off_still_never_calls_gpt(
    ai_free_off: None, memdb: sqlite3.Connection
) -> None:
    """The legacy G3 branch is deleted, not just flag-gated: even with
    POLARIS_AI_FREE=0 and a client that explodes on touch, the technical
    rule drives and the shadow row logs gpt_decision=None."""
    res = await signal_validator_gate(
        _g3_ctx(quartile="top"), client=_ForbiddenClient(), shadow_conn=memdb,
    )
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    # P2a conductor closeout (2026-07-16): comparisonless live-path shadow
    # writes are dropped — gate_events records the decision instead.
    assert rows == []


@pytest.mark.asyncio
async def test_g3_no_client_no_longer_fail_closed(ai_free_off: None) -> None:
    """No client at all → same deterministic flow (the old ``no_gpt_client``
    fail-closed KILL only existed inside the now-deleted legacy branch)."""
    res = await signal_validator_gate(_g3_ctx(quartile="top"), client=None)
    assert res.decision == GateDecision.PASS
    assert res.model_used == "python"


@pytest.mark.asyncio
async def test_g7_flag_off_still_never_calls_gpt(
    ai_free_off: None, memdb: sqlite3.Connection
) -> None:
    """Same guarantee for G7: an exploding client is never touched regardless
    of the (now-vestigial) flag; the Q9 rail drives and the shadow row logs
    gpt_decision=None."""
    res = await adaptive_exit_gate(
        _g7_ctx(unrealized_pnl_r=1.0), client=_ForbiddenClient(), shadow_conn=memdb,
    )
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.model_used == "python"
    rows = fetch_shadow_events(memdb, gate_id=7)
    # P2a conductor closeout (2026-07-16): comparisonless live-path shadow
    # writes are dropped — gate_events records the decision instead.
    assert rows == []


@pytest.mark.asyncio
async def test_g7_flag_off_no_client_still_rails(ai_free_off: None) -> None:
    res = await adaptive_exit_gate(_g7_ctx(), client=None)
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.model_used == "python"
