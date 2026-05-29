"""Layer 2 — GPT gate unit tests (mocked OpenAI).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G1/G3/G4/G8 + Q6 prompts)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from polaris.core.pipeline.agents.post_trade_reflector import (
    post_trade_reflector_gate,
    write_lesson_to_vault,
)
from polaris.core.pipeline.agents.pre_entry_watcher import (
    FastPathContext,
    is_fast_path_eligible,
    pre_entry_watcher_gate,
)
from polaris.core.pipeline.agents.signal_validator import (
    MODIFY_MAX,
    MODIFY_MIN,
    signal_validator_gate,
)
from polaris.core.pipeline.agents.universe_scanner import (
    FOCUS_MAX,
    FOCUS_MIN,
    SCANNER_PROMPT_UNIVERSE_CAP,
    deterministic_top_n,
    universe_scanner_gate,
)
from polaris.core.pipeline.g1_focus_gate import G1FocusCache
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000


class _MockGPTClient:
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


def _ctx(payload: dict, *, gate_id: int) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id=None,
        gate_id=gate_id,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.RAW,
    )


# ---------------------------------------------------------------------------
# Universe Scanner (G1)
# ---------------------------------------------------------------------------


async def test_universe_scanner_focus_size_in_bound() -> None:
    universe = [
        {"venue": "okx", "symbol": f"T{i:03d}-USDT", "vol_24h_usd": 1e8 - i * 1e6}
        for i in range(50)
    ]
    focus_list = [f"okx:{u['symbol']}" for u in universe[:24]]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    ctx = _ctx({"universe": universe}, gate_id=1)
    result = await universe_scanner_gate(ctx, client=haiku, universe=universe)
    assert result.decision == GateDecision.PASS
    focus = result.payload["focus"]
    assert FOCUS_MIN <= len(focus) <= FOCUS_MAX
    assert len(haiku.calls) == 1


async def test_universe_scanner_no_client_falls_back_top_n() -> None:
    universe = [
        {"venue": "okx", "symbol": f"T{i:03d}-USDT", "vol_24h_usd": 1e9 - i}
        for i in range(40)
    ]
    ctx = _ctx({"universe": universe}, gate_id=1)
    result = await universe_scanner_gate(ctx, client=None, universe=universe)
    assert result.model_used == "python"
    # Top 30 by vol.
    assert len(result.payload["focus"]) == 30


async def test_universe_scanner_gpt_error_falls_back() -> None:
    universe = [
        {"venue": "okx", "symbol": f"T{i:03d}-USDT", "vol_24h_usd": 1e9 - i * 1e6}
        for i in range(40)
    ]
    # Bad JSON → fallback path used.
    haiku = _MockGPTClient(response_text="not-json")
    ctx = _ctx({"universe": universe}, gate_id=1)
    result = await universe_scanner_gate(ctx, client=haiku, universe=universe)
    assert result.decision == GateDecision.PASS
    assert "fallback_reason" in result.payload


def test_deterministic_top_n_orders_by_vol_desc() -> None:
    rows = [
        {"venue": "okx", "symbol": "A", "vol_24h_usd": 100.0},
        {"venue": "okx", "symbol": "B", "vol_24h_usd": 200.0},
        {"venue": "okx", "symbol": "C", "vol_24h_usd": 50.0},
    ]
    top = deterministic_top_n(rows, n=2)
    assert [r["symbol"] for r in top] == ["B", "A"]


# ---------------------------------------------------------------------------
# G1-EFF — on-change-only GPT gating + prompt cap (efficiency, NOT a throttle).
# Focus DECISION preserved (GPT still chooses); only call FREQUENCY/input drop.
# G1 STILL always PASS — focus emitted every cycle (cached or fresh).
# ---------------------------------------------------------------------------


def _big_universe(n: int = 100) -> list[dict]:
    return [
        {"venue": "okx", "symbol": f"T{i:03d}-USDT", "vol_24h_usd": 1e9 - i * 1e6}
        for i in range(n)
    ]


async def test_universe_scanner_unchanged_universe_skips_gpt_reuses_focus() -> None:
    universe = _big_universe()
    focus_list = [f"okx:T{i:03d}-USDT" for i in range(24)]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    cache = G1FocusCache()

    # First scan: cold cache → GPT runs, focus cached.
    r1 = await universe_scanner_gate(
        _ctx({"universe": universe}, gate_id=1),
        client=haiku, universe=universe, focus_cache=cache, now_ts=1_000,
    )
    assert r1.decision == GateDecision.PASS
    assert r1.model_used == "gpt"
    assert len(haiku.calls) == 1

    # Second scan, identical universe inside cooldown → GPT SKIPPED, focus reused.
    r2 = await universe_scanner_gate(
        _ctx({"universe": universe}, gate_id=1),
        client=haiku, universe=universe, focus_cache=cache, now_ts=1_010,
    )
    assert r2.decision == GateDecision.PASS  # STILL always PASS (selector)
    assert r2.model_used == "cached"  # observable skip
    assert len(haiku.calls) == 1  # no new GPT call
    assert r2.payload["focus"] == r1.payload["focus"]  # decision preserved


async def test_universe_scanner_changed_universe_calls_gpt() -> None:
    universe = _big_universe()
    focus_list = [f"okx:T{i:03d}-USDT" for i in range(24)]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    cache = G1FocusCache()

    await universe_scanner_gate(
        _ctx({"universe": universe}, gate_id=1),
        client=haiku, universe=universe, focus_cache=cache, now_ts=1_000,
    )
    assert len(haiku.calls) == 1

    # A new listing appears → composition changed → GPT MUST run again.
    changed = [*universe, {"venue": "okx", "symbol": "NEW-USDT", "vol_24h_usd": 5e9}]
    r = await universe_scanner_gate(
        _ctx({"universe": changed}, gate_id=1),
        client=haiku, universe=changed, focus_cache=cache, now_ts=1_010,
    )
    assert r.model_used == "gpt"
    assert len(haiku.calls) == 2


async def test_universe_scanner_emits_focus_every_cycle_cached_or_fresh() -> None:
    universe = _big_universe()
    focus_list = [f"okx:T{i:03d}-USDT" for i in range(24)]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    cache = G1FocusCache()
    for i in range(4):
        r = await universe_scanner_gate(
            _ctx({"universe": universe}, gate_id=1),
            client=haiku, universe=universe, focus_cache=cache,
            now_ts=1_000 + i,  # all inside cooldown
        )
        # Focus list is non-empty on EVERY cycle — fresh on the first, cached after.
        assert r.decision == GateDecision.PASS
        assert FOCUS_MIN <= len(r.payload["focus"]) <= FOCUS_MAX
    # Only one GPT call across 4 unchanged cycles.
    assert len(haiku.calls) == 1


async def test_universe_scanner_prompt_row_cap_applied() -> None:
    universe = _big_universe(300)  # far more than the prompt cap
    focus_list = [f"okx:T{i:03d}-USDT" for i in range(24)]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    await universe_scanner_gate(
        _ctx({"universe": universe}, gate_id=1),
        client=haiku, universe=universe,
    )
    assert len(haiku.calls) == 1
    # The user prompt dumps at most SCANNER_PROMPT_UNIVERSE_CAP universe rows.
    user_msg = haiku.calls[0]["messages"][-1]["content"]
    row_lines = [ln for ln in user_msg.splitlines() if ln.startswith("- okx:")]
    assert len(row_lines) <= SCANNER_PROMPT_UNIVERSE_CAP
    assert SCANNER_PROMPT_UNIVERSE_CAP <= 80  # input-token savings (was 300)


async def test_universe_scanner_no_cache_calls_gpt_every_time() -> None:
    # Without a focus_cache the gate behaves exactly as before (back-compat).
    universe = _big_universe()
    focus_list = [f"okx:T{i:03d}-USDT" for i in range(24)]
    haiku = _MockGPTClient(response_text=json.dumps({"focus": focus_list}))
    for _ in range(3):
        await universe_scanner_gate(
            _ctx({"universe": universe}, gate_id=1),
            client=haiku, universe=universe,
        )
    assert len(haiku.calls) == 3


# ---------------------------------------------------------------------------
# Signal Validator (G3)
# ---------------------------------------------------------------------------


async def test_signal_validator_pass() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "PASS", "strength_scalar": 1.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PASS
    assert result.next_gate == GATE_PRE_ENTRY_WATCHER
    assert result.payload["validated_signal"]["strength_scalar"] == 1.0


async def test_signal_validator_kill() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 0.3}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL
    assert result.next_gate is None


async def test_signal_validator_modify_clamps_to_range() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "MODIFY", "strength_scalar": 5.0}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.MODIFY
    assert MODIFY_MIN <= result.payload["validated_signal"]["strength_scalar"] <= MODIFY_MAX


async def test_signal_validator_no_client_kill() -> None:
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=None)
    assert result.decision == GateDecision.KILL


async def test_signal_validator_schema_violation_kill() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "MAYBE"}')
    ctx = _ctx({"raw_signal": {"strategy": "vb", "score": 1.0}}, gate_id=3)
    result = await signal_validator_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL


# ---------------------------------------------------------------------------
# Pre-Entry Watcher (G4)
# ---------------------------------------------------------------------------


async def test_pre_entry_watcher_proceed() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "PROCEED"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.0, "strategy": "vb"},
        "tick_window": [],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=4)
    result = await pre_entry_watcher_gate(ctx, client=haiku)
    assert result.decision == GateDecision.PROCEED


async def test_pre_entry_watcher_kill_fail_closed() -> None:
    haiku = _MockGPTClient(response_text='{"decision": "KILL"}')
    payload = {
        "validated_signal": {"strength_scalar": 1.0, "strategy": "vb"},
        "tick_window": [],
        "spread_bps": 5.0,
        "baseline_p50_spread_bps": 4.0,
    }
    ctx = _ctx(payload, gate_id=4)
    result = await pre_entry_watcher_gate(ctx, client=haiku)
    assert result.decision == GateDecision.KILL


def test_fast_path_eligibility_top_quartile_ok() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert is_fast_path_eligible(fp)


def test_fast_path_eligibility_listing_too_young_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=12.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


def test_fast_path_eligibility_strength_too_low_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.10,
        spread_bps=2.0,
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


def test_fast_path_eligibility_spread_wide_rejects() -> None:
    fp = FastPathContext(
        cell_quartile="top",
        signal_strength=1.30,
        spread_bps=4.5,  # > 4.0 * 0.9 = 3.6
        baseline_p50_spread_bps=4.0,
        recent_reject_in_6h=False,
        listing_age_hours=100.0,
        session_open_shock_window=False,
    )
    assert not is_fast_path_eligible(fp)


# ---------------------------------------------------------------------------
# Post-Trade Reflector (G8)
# ---------------------------------------------------------------------------


async def test_post_trade_reflector_lesson_write_to_vault(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "entry_timing",
        "confidence": 0.85,
        "lesson_text": "Entered too early; wait for 1m close above breakout.",
        "delta": {"vb_x_bull_trend": 0.02},
    }))
    closed_trade = {
        "trade_id": "trade-1",
        "strategy_id": "vb",
        "regime": "bull_trend",
        "session": "asia",
        "pnl_r": -0.5,
    }
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 50}, gate_id=8)
    lessons_dir = tmp_path / "lessons"
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=lessons_dir
    )
    assert result.decision == GateDecision.REFLECTED
    assert result.payload["confidence"] == 0.85
    # File should be written.
    files = list(lessons_dir.glob("trade-1_*.md"))
    assert len(files) == 1
    # ai_lessons row persisted.
    row = memdb.execute("SELECT lesson_type, confidence FROM ai_lessons").fetchone()
    assert row is not None
    assert row[0] == "entry_timing"


async def test_post_trade_reflector_malformed_confidence_dropped(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    """Codex P1: malformed confidence string must not raise — drops as low confidence."""
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "ok",
        "confidence": "not-a-number",
        "lesson_text": "x",
        "delta": {},
    }))
    closed_trade = {"trade_id": "trade-malformed", "strategy_id": "vb"}
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 50}, gate_id=8)
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path
    )
    assert result.decision == GateDecision.REFLECTED
    assert result.payload.get("reason") == "low_confidence"


async def test_post_trade_reflector_low_confidence_dropped(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "entry_timing",
        "confidence": 0.50,  # below floor
        "lesson_text": "low conviction",
        "delta": {},
    }))
    closed_trade = {"trade_id": "trade-2", "strategy_id": "vb", "pnl_r": 0.0}
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 50}, gate_id=8)
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path
    )
    assert result.decision == GateDecision.REFLECTED
    assert result.payload.get("reason") == "low_confidence"
    # No row inserted.
    n = memdb.execute("SELECT COUNT(*) FROM ai_lessons").fetchone()[0]
    assert n == 0


async def test_post_trade_reflector_soft_mode_dampens_delta(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    haiku = _MockGPTClient(response_text=json.dumps({
        "lesson_type": "exit_patience",
        "confidence": 0.80,
        "lesson_text": "soft mode test",
        "delta": {"vb_x_asia": 0.1},
    }))
    closed_trade = {"trade_id": "trade-3", "strategy_id": "vb"}
    ctx = _ctx({"closed_trade": closed_trade, "closed_trade_count": 10}, gate_id=8)
    result = await post_trade_reflector_gate(
        ctx, client=haiku, conn=memdb, lessons_dir=tmp_path
    )
    # Soft mode = 25% of 0.1 = 0.025
    assert result.payload["delta"]["vb_x_asia"] == pytest.approx(0.025)
    assert result.payload["soft_mode"] is True


def test_write_lesson_to_vault_is_idempotent(tmp_path: Path) -> None:
    p1 = write_lesson_to_vault(trade_id="t-x", lesson_text="v1", lessons_dir=tmp_path, now_ts=NOW)
    p2 = write_lesson_to_vault(trade_id="t-x", lesson_text="v2", lessons_dir=tmp_path, now_ts=NOW)
    assert p1 == p2
    assert p1.read_text() == "v2"
