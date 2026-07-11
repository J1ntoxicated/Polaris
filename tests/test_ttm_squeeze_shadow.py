"""G4 TTM Squeeze watch-feature SHADOW (frontgate-scan item #9, behavior-0).

DEMO/PAPER virtual capital only. Covers:
- ``compute_squeeze_state`` pure BB-in-Keltner detector (on / release / none).
- Live wiring: ``pre_entry_watcher_gate``'s AI-free PROCEED path now ALSO
  logs a watch-only ``gate_shadow_events`` row via ``log_shadow_event``
  (gpt_decision=None -> mismatch=0 pinned), never touching the returned
  decision/payload (behavior-0).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.pipeline.agents.pre_entry_watcher import pre_entry_watcher_gate
from polaris.core.pipeline.agents.shadow_log import fetch_shadow_events
from polaris.core.pipeline.agents.ttm_squeeze_shadow import compute_squeeze_state
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.storage.schema import init_db
from polaris.strategies.base import BarView

MIN_TS = 60


def _flat_bars(n: int, *, price: float = 100.0, day_range: float = 0.5) -> list[BarView]:
    return [
        BarView(
            ts=i * MIN_TS, open=price, high=price + day_range, low=price - day_range,
            close=price, volume=10.0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# compute_squeeze_state
# ---------------------------------------------------------------------------


def test_squeeze_state_insufficient_bars_is_no_squeeze() -> None:
    state = compute_squeeze_state(_flat_bars(10))
    assert state.squeeze_on is False
    assert state.released is False
    assert state.reason == "no_squeeze"


def test_squeeze_state_flat_bars_are_squeeze_on() -> None:
    """Zero-variance closes → BB bandwidth 0, always strictly inside a
    positive-width Keltner channel (nonzero true range) → squeeze ON."""
    state = compute_squeeze_state(_flat_bars(21))
    assert state.squeeze_on is True
    assert state.released is False
    assert state.reason == "squeeze_on"


def test_squeeze_state_release_bullish_on_upside_breakout() -> None:
    """21 flat bars (squeeze ON) then a large upside breakout bar → BB widens
    past Keltner (release) with positive momentum → bullish release."""
    bars = _flat_bars(21) + [
        BarView(ts=21 * MIN_TS, open=110.0, high=110.5, low=109.5, close=110.0, volume=10.0)
    ]
    state = compute_squeeze_state(bars)
    assert state.squeeze_on is False
    assert state.released is True
    assert state.momentum > 0.0
    assert state.reason == "squeeze_release_bullish"


def test_squeeze_state_release_bearish_on_downside_breakout() -> None:
    bars = _flat_bars(21) + [
        BarView(ts=21 * MIN_TS, open=90.0, high=90.5, low=89.5, close=90.0, volume=10.0)
    ]
    state = compute_squeeze_state(bars)
    assert state.released is True
    assert state.momentum < 0.0
    assert state.reason == "squeeze_release_bearish"


def test_squeeze_state_steady_trend_no_squeeze_no_release() -> None:
    """A steady wide-ranging trend never squeezes → not on, not released."""
    bars = [
        BarView(ts=i * MIN_TS, open=100.0 + i, high=100.1 + i, low=99.9 + i,
                 close=100.0 + i, volume=10.0)
        for i in range(22)
    ]
    state = compute_squeeze_state(bars)
    assert state.squeeze_on is False
    assert state.released is False
    assert state.reason == "no_squeeze"


# ---------------------------------------------------------------------------
# Live wiring — behavior-0
# ---------------------------------------------------------------------------


def _g4_ctx() -> GateContext:
    now = int(time.time())
    return GateContext(
        run_id="run-squeeze",
        signal_id="sig-squeeze",
        position_id=None,
        gate_id=GATE_PRE_ENTRY_WATCHER,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="s1",
        payload={
            "validated_signal": {"symbol": "BTC-USDT", "strength_scalar": 1.0},
            "tick_window": [{"ts": now, "bid": 100.0, "ask": 100.1, "mid": 100.05}],
            "cell_quartile": "mid",
            "regime": "trend_up",
        },
        started_ts=now,
        state=SignalLifecycle.VALIDATED,
    )


async def test_g4_ai_free_proceed_logs_squeeze_tag_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "squeeze.sqlite")
    try:
        result = await pre_entry_watcher_gate(_g4_ctx(), ai_free=True, shadow_conn=conn)
        assert result.decision == GateDecision.PROCEED
        rows = fetch_shadow_events(conn, gate_id=GATE_PRE_ENTRY_WATCHER)
        assert len(rows) == 1
        assert rows[0]["gpt_decision"] == ""
        assert rows[0]["mismatch"] == 0
        assert rows[0]["technical_decision"] == "PROCEED"
        # No bars ingested yet in this DB → insufficient-data reason.
        assert rows[0]["technical_reason"] == "no_squeeze"
        assert "no_squeeze" in rows[0]["technical_flags"]
    finally:
        conn.close()


async def test_g4_ai_free_proceed_no_shadow_conn_no_row() -> None:
    result = await pre_entry_watcher_gate(_g4_ctx(), ai_free=True)
    assert result.decision == GateDecision.PROCEED


async def test_g4_squeeze_tag_never_touches_payload_or_decision(tmp_path: Path) -> None:
    """Behavior-0: identical decision/payload whether or not shadow_conn wires
    the squeeze tag (only a side-effect DB row differs)."""
    conn = init_db(tmp_path / "parity.sqlite")
    try:
        ctx_a = _g4_ctx()
        ctx_b = _g4_ctx()
        result_no_shadow = await pre_entry_watcher_gate(ctx_a, ai_free=True)
        result_shadow = await pre_entry_watcher_gate(ctx_b, ai_free=True, shadow_conn=conn)
        assert result_no_shadow.decision == result_shadow.decision
        assert result_no_shadow.payload == result_shadow.payload
        assert result_no_shadow.model_used == result_shadow.model_used
    finally:
        conn.close()


async def test_g4_squeeze_tag_fail_open_on_broken_conn() -> None:
    conn = sqlite3.connect(":memory:")
    conn.close()
    result = await pre_entry_watcher_gate(_g4_ctx(), ai_free=True, shadow_conn=conn)
    assert result.decision == GateDecision.PROCEED
