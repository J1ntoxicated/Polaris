"""W3 AI-free cutover — ``POLARIS_AI_FREE`` flag (default ON).

DEMO/PAPER paper bot. Spec SSOT: ``.claude/plans/organic_ops_ai_free_2026-06-11.md``
§1 W3 (Jin 2026-06-11 — in-loop LLM calls = 0 by default).

flag=1 (default): G3/G4/G7 return their deterministic technical decisions as the
PRIMARY decision (``model_used="python"``), ZERO GPT calls, and the
``gate_shadow_events`` comparison log naturally stops (no GPT to compare).
The deterministic promotion RAISES pass-through (measured: disagreements were
almost entirely gpt=KILL vs tech=PASS/PROCEED) — flow_not_block direction.

flag=0 (legacy): the GPT path runs byte-identical (incl. ``no_gpt_client``
fail-closed KILL and shadow logging) — pinned by the regression tests below.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.core.pipeline.agents import pre_entry_watcher as g4_mod
from polaris.core.pipeline.agents._shadow_rules import (
    MODIFY_CONSERVATIVE_SCALAR,
)
from polaris.core.pipeline.agents.adaptive_exit import adaptive_exit_gate
from polaris.core.pipeline.agents.pre_entry_watcher import pre_entry_watcher_gate
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


def _forbid_gpt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard zero-call assert: ANY call_gpt invocation in G4 explodes.

    P2a group B: G3/G7 no longer import ``call_gpt`` at all (the legacy
    branch was deleted outright, not just flag-gated) — nothing to patch
    there. G4 still carries it pending the group A gate-diet pass.
    """

    async def _boom(**kwargs: Any) -> Any:
        raise AssertionError("in-loop GPT call attempted in AI-free mode")

    monkeypatch.setattr(g4_mod, "call_gpt", _boom)


class _ForbiddenClient:
    """A client object that explodes on ANY attribute access."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"GPT client attribute touched in AI-free mode: {name}")


class _FakeGPT:
    """Anthropic-shaped permissive client (legacy flag=0 path tests)."""

    def __init__(self, response_text: str) -> None:
        self.call_count = 0
        outer = self

        class _Block:
            text = response_text

        class _Resp:
            content = [_Block()]
            usage = None

        class _Messages:
            async def create(self, **kwargs: Any) -> Any:
                outer.call_count += 1
                return _Resp()

        self.messages = _Messages()


def _g3_ctx(
    *,
    quartile: str = "top",
    n_eff: float = 10.0,
    avg_pnl_r: float = 0.5,
    score: float = 0.5,
    regime: str = "trend_up",
) -> GateContext:
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
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )


def _g4_ctx(
    *,
    tick: dict[str, Any] | None = None,
    started_ts: int | None = None,
    quartile: str = "mid",
    strength: float = 1.0,
) -> GateContext:
    now = int(time.time())
    if tick is None:
        tick = {"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}
    return GateContext(
        run_id="run-aifree",
        signal_id="sig-1",
        position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "validated_signal": {"symbol": "BTC-USDT", "strength_scalar": strength},
            "tick_window": [tick],
            "cell_quartile": quartile,
            "regime": "trend_up",
        },
        started_ts=started_ts if started_ts is not None else now,
        state=SignalLifecycle.VALIDATED,
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
    _forbid_gpt(monkeypatch)


@pytest.fixture
def ai_free_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_AI_FREE", "0")


# ---------------------------------------------------------------------------
# Flag semantics
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
        ("0", False),  # explicit opt-out → legacy GPT path
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
# G3 — deterministic primary (flag=1)
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
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
    assert res.payload["validated_signal"]["strength_scalar"] == 1.0
    # P2a group B: the technical decision is still logged for measurement
    # continuity — gpt_decision is None now (no GPT call to compare against).
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] is None or rows[0]["gpt_decision"] == ""


@pytest.mark.asyncio
async def test_g3_ai_free_warm_mid_modify_scalar(ai_free_on: None) -> None:
    res = await signal_validator_gate(
        _g3_ctx(quartile="mid"), client=_ForbiddenClient()
    )
    assert res.decision == GateDecision.MODIFY
    assert res.model_used == "python"
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
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
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
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
    assert res.next_gate == GATE_PRE_ENTRY_WATCHER
    assert "losing" not in res.payload["reason"]
    rows = fetch_shadow_events(memdb, gate_id=GATE_SIGNAL_VALIDATOR)
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] is None or rows[0]["gpt_decision"] == ""


@pytest.mark.asyncio
async def test_g3_ai_free_missing_raw_signal_kill_unchanged(ai_free_on: None) -> None:
    ctx = _g3_ctx()
    ctx.payload["raw_signal"] = {}
    res = await signal_validator_gate(ctx, client=_ForbiddenClient())
    assert res.decision == GateDecision.KILL
    assert res.payload["reason"] == "missing_raw_signal"
    assert res.model_used == "python"


# ---------------------------------------------------------------------------
# G4 — deterministic primary (flag=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g4_ai_free_fresh_book_proceed(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
    res = await pre_entry_watcher_gate(
        _g4_ctx(), client=_ForbiddenClient(), shadow_conn=memdb
    )
    assert res.decision == GateDecision.PROCEED
    assert res.model_used == "python"
    assert res.next_gate == GATE_ENTRY_SIZER
    assert res.payload["watched_signal"]["symbol"] == "BTC-USDT"
    # frontgate-scan item #9 (TTM Squeeze watch tag): the AI-free PROCEED path
    # now ALSO logs a watch-only gate_shadow_events row (gpt_decision=""
    # pins mismatch=0 — a watch tag, never a technical-vs-GPT comparison).
    rows = fetch_shadow_events(memdb, gate_id=GATE_PRE_ENTRY_WATCHER)
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] == ""
    assert rows[0]["mismatch"] == 0


@pytest.mark.asyncio
async def test_g4_ai_free_crossed_book_kill(ai_free_on: None) -> None:
    now = int(time.time())
    res = await pre_entry_watcher_gate(
        _g4_ctx(tick={"ts": now, "bid": 100.2, "ask": 100.1, "mid": 100.15}),
        client=_ForbiddenClient(),
    )
    assert res.decision == GateDecision.KILL
    assert res.model_used == "python"
    assert res.payload["reason"] == "crossed_book"


@pytest.mark.asyncio
async def test_g4_ai_free_stale_book_flags_not_kill(ai_free_on: None) -> None:
    """No per-ticker cadence baseline in payload → fixed fallback bound →
    stale is a FLAG on a PROCEED (flow_not_block), never a KILL."""
    now = int(time.time())
    res = await pre_entry_watcher_gate(
        _g4_ctx(
            tick={"ts": now - 1000, "bid": 100.0, "ask": 100.1, "mid": 100.05},
            started_ts=now,
        ),
        client=_ForbiddenClient(),
    )
    assert res.decision == GateDecision.PROCEED
    assert res.model_used == "python"
    assert "stale_book" in res.payload.get("watch_flags", [])


@pytest.mark.asyncio
async def test_g4_ai_free_fast_path_untouched(ai_free_on: None) -> None:
    # Fast-path eligibility runs BEFORE the flag seam — unchanged either way.
    ctx = _g4_ctx(quartile="top", strength=1.3)
    ctx.payload["spread_bps"] = 1.0
    ctx.payload["baseline_p50_spread_bps"] = 5.0
    ctx.payload["listing_age_hours"] = 100.0
    res = await pre_entry_watcher_gate(ctx, client=_ForbiddenClient())
    assert res.decision == GateDecision.PROCEED
    assert res.model_used == "python_fast_path"
    assert res.skipped is True


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
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] is None or rows[0]["gpt_decision"] == ""


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
async def test_pipeline_g3_g4_ai_free_gate_events(
    ai_free_on: None, memdb: sqlite3.Connection
) -> None:
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
        "SELECT gate_id, decision, model_used FROM gate_events "
        "WHERE gate_id IN (3, 4) ORDER BY gate_id"
    ).fetchall()
    assert (3, "PASS", "python") in rows
    assert (4, "PROCEED", "python") in rows
    # P2a group B: G3 now ALSO logs its technical decision for measurement
    # continuity (gpt_decision=None, mismatch=0) — plus G4's pre-existing
    # frontgate-scan item #9 watch-only row (squeeze tag). Both mismatch=0
    # (no GPT decision left anywhere to diverge from).
    shadow_rows = fetch_shadow_events(memdb)
    assert len(shadow_rows) == 2
    gate_ids = {r["gate_id"] for r in shadow_rows}
    assert gate_ids == {GATE_SIGNAL_VALIDATOR, GATE_PRE_ENTRY_WATCHER}
    assert all(r["mismatch"] == 0 for r in shadow_rows)


# ---------------------------------------------------------------------------
# flag=0 — G4 legacy GPT path byte-identical (regression pin, group A scope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g4_legacy_gpt_path_and_shadow(
    ai_free_off: None, memdb: sqlite3.Connection
) -> None:
    fake = _FakeGPT('{"decision": "PROCEED", "reason": "ok"}')
    res = await pre_entry_watcher_gate(_g4_ctx(), client=fake, shadow_conn=memdb)
    assert res.decision == GateDecision.PROCEED
    assert res.model_used == "gpt"
    assert fake.call_count == 1
    assert len(fetch_shadow_events(memdb, gate_id=GATE_PRE_ENTRY_WATCHER)) == 1


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
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] is None or rows[0]["gpt_decision"] == ""


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
    assert len(rows) == 1
    assert rows[0]["gpt_decision"] is None or rows[0]["gpt_decision"] == ""


@pytest.mark.asyncio
async def test_g7_flag_off_no_client_still_rails(ai_free_off: None) -> None:
    res = await adaptive_exit_gate(_g7_ctx(), client=None)
    assert res.decision == GateDecision.ADJUST_EXIT
    assert res.model_used == "python"
