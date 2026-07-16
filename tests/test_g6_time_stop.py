"""G6 time-stop backstop (P1 fix) — stopless-zombie cleanup.

DEMO/PAPER only. Incident: a stop_price-null position has no other exit
backstop (the ATR-trailing-stop system needs a stop_price to ever trigger) —
it can sit indefinitely once its signal is dead. This adds a P&L-agnostic
time rail, independent of and never touching the -1.0R rail: a position held
past ``K x strategy_horizon_seconds`` (expected_holding_bars x timeframe, K
default 4, env ``POLARIS_TIME_STOP_K``) is EXIT_NOW ("time_stop"). Not a
defensive throttle — same class as the session exit rail (a calendar/time
integrity rail, never a P&L-driven dampen).
"""

from __future__ import annotations

import uuid
from typing import Any

from polaris.core.pipeline.agents.position_monitor import position_monitor_gate
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)

NOW = 1_780_000_000

# Unregistered strategy id (no StrategyMetadata) -> "1m" x 10-bar standard
# fallback horizon = 600s. K=4 default -> 2400s threshold.
UNREGISTERED_STRATEGY = "totally_unregistered_time_stop_probe"
UNREGISTERED_HORIZON_SEC = 600


def _ctx(payload: dict[str, Any]) -> GateContext:
    return GateContext(
        run_id=uuid.uuid4().hex,
        signal_id="sig-test",
        position_id="pos-test",
        gate_id=GATE_POSITION_MONITOR,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id=UNREGISTERED_STRATEGY,
        payload=dict(payload),
        started_ts=NOW,
        state=SignalLifecycle.MONITORED,
    )


def _position(*, held_seconds: int, stop_price: float | None = None) -> dict[str, Any]:
    pos: dict[str, Any] = {
        "venue": "okx",
        "symbol": "BTC-USDT",
        "side": "long",
        "strategy": UNREGISTERED_STRATEGY,
        "correlation_group": "crypto:BTC",
        "entry_price": 80_000.0,
        "last_price": 80_100.0,
        "held_seconds": held_seconds,
        "cell_score": 0.55,
    }
    if stop_price is not None:
        pos["stop_price"] = stop_price
    return pos


# ---------------------------------------------------------------------------
# Fires / doesn't fire
# ---------------------------------------------------------------------------


async def test_time_stop_fires_past_k_times_horizon() -> None:
    """held_seconds > K x horizon (default K=4, 600s horizon) -> EXIT_NOW."""
    payload = {
        "position": _position(held_seconds=UNREGISTERED_HORIZON_SEC * 4 + 1),
        "unrealized_pnl_r": 0.10,  # not a stop hit, not a widen
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "time_stop"
    assert result.model_used == "python"


async def test_time_stop_does_not_fire_under_threshold() -> None:
    """held_seconds just under K x horizon -> no time_stop (falls to HOLD)."""
    payload = {
        "position": _position(held_seconds=UNREGISTERED_HORIZON_SEC * 4 - 1),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.HOLD


async def test_time_stop_normal_held_seconds_unaffected() -> None:
    """A freshly-opened position (90s held) is nowhere near the backstop."""
    payload = {
        "position": _position(held_seconds=90),
        "unrealized_pnl_r": 0.30,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None)
    assert result.decision == GateDecision.HOLD


# ---------------------------------------------------------------------------
# stop_price null positions ALSO get the backstop (the motivating incident)
# ---------------------------------------------------------------------------


async def test_time_stop_fires_even_with_stop_price_null() -> None:
    """The whole point: a stopless position has no other backstop today."""
    payload = {
        "position": _position(
            held_seconds=UNREGISTERED_HORIZON_SEC * 4 + 1, stop_price=None,
        ),
        "unrealized_pnl_r": 0.0,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "time_stop"


# ---------------------------------------------------------------------------
# -1.0R rail independence — never touched, and still takes priority
# ---------------------------------------------------------------------------


async def test_hard_loss_rail_still_wins_over_time_stop() -> None:
    """A genuine stop-hit is reported as 'stop_hit', not 'time_stop', even
    when both conditions are true simultaneously — the rail is untouched."""
    payload = {
        "position": _position(held_seconds=UNREGISTERED_HORIZON_SEC * 10),
        "unrealized_pnl_r": -1.5,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "stop_hit"


async def test_time_stop_absent_default_env_reads_k_4() -> None:
    """time_stop_k=None reads POLARIS_TIME_STOP_K (default 4.0) — env plumbing."""
    import os

    prior = os.environ.pop("POLARIS_TIME_STOP_K", None)
    try:
        payload = {
            "position": _position(held_seconds=UNREGISTERED_HORIZON_SEC * 4 + 1),
            "unrealized_pnl_r": 0.10,
            "max_loss_r": 1.0,
        }
        result = await position_monitor_gate(_ctx(payload), client=None)
        assert result.decision == GateDecision.EXIT_NOW
        assert result.payload.get("reason") == "time_stop"
    finally:
        if prior is not None:
            os.environ["POLARIS_TIME_STOP_K"] = prior


# ---------------------------------------------------------------------------
# K env override (POLARIS_TIME_STOP_K)
# ---------------------------------------------------------------------------


async def test_time_stop_k_env_override_tightens_threshold() -> None:
    """K=1 (env override) fires far earlier than the default K=4 would."""
    held = UNREGISTERED_HORIZON_SEC + 1  # > 1x horizon, well under 4x
    payload = {
        "position": _position(held_seconds=held),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    # Default K=4 -> no fire at this held_seconds.
    default_result = await position_monitor_gate(_ctx(payload), client=None)
    assert default_result.decision == GateDecision.HOLD
    # K=1 (injected, mirrors env override) -> fires.
    tight_result = await position_monitor_gate(
        _ctx(payload), client=None, time_stop_k=1.0,
    )
    assert tight_result.decision == GateDecision.EXIT_NOW
    assert tight_result.payload.get("reason") == "time_stop"


# ---------------------------------------------------------------------------
# Registered strategy — expected_holding_bars x timeframe (not the fallback)
# ---------------------------------------------------------------------------


async def test_time_stop_uses_registered_strategy_horizon() -> None:
    """cci_reversion: timeframe + expected_holding_bars from STRATEGY_REGISTRY,
    not the unregistered 1m/10-bar fallback."""
    from polaris.core.isolation.reentry import bar_seconds
    from polaris.strategies import STRATEGY_REGISTRY

    cls = STRATEGY_REGISTRY["cci_reversion"]
    horizon = bar_seconds(cls.metadata.timeframe) * cls.metadata.expected_holding_bars

    def _cci_ctx(held_seconds: int) -> GateContext:
        pos = {
            "venue": "capital", "symbol": "GOLD", "side": "long",
            "strategy": "cci_reversion",
            "correlation_group": "cfd_commodity_reversion",
            "entry_price": 2000.0, "last_price": 2010.0,
            "held_seconds": held_seconds, "cell_score": 0.5,
        }
        return GateContext(
            run_id=uuid.uuid4().hex, signal_id="sig-test", position_id="pos-test",
            gate_id=GATE_POSITION_MONITOR, venue="capital", symbol="GOLD",
            strategy_id="cci_reversion",
            payload={"position": pos, "unrealized_pnl_r": 0.10, "max_loss_r": 1.0},
            started_ts=NOW, state=SignalLifecycle.MONITORED,
        )

    result = await position_monitor_gate(
        _cci_ctx(horizon * 4 + 1), client=None, time_stop_k=4.0,
    )
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "time_stop"

    # Just under the same threshold -> no fire.
    result_under = await position_monitor_gate(
        _cci_ctx(horizon * 4 - 1), client=None, time_stop_k=4.0,
    )
    assert result_under.decision == GateDecision.HOLD


# ---------------------------------------------------------------------------
# K clamp — non-positive K must never invert the rail into a throttle
# ---------------------------------------------------------------------------


async def test_time_stop_k_zero_injected_clamps_to_default() -> None:
    """K=0 injected directly must NOT fire on every held position (would
    invert the P&L-agnostic time rail into an exit-everything throttle) —
    clamps to TIME_STOP_K_DEFAULT (4.0), same as a fresh 90s-held position."""
    payload = {
        "position": _position(held_seconds=90),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=0.0)
    assert result.decision == GateDecision.HOLD


async def test_time_stop_k_negative_injected_clamps_to_default() -> None:
    """K<0 injected directly must not make the threshold negative."""
    payload = {
        "position": _position(held_seconds=90),
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=-1.0)
    assert result.decision == GateDecision.HOLD


async def test_time_stop_k_zero_env_clamps_to_default() -> None:
    """POLARIS_TIME_STOP_K=0 (env path) clamps to TIME_STOP_K_DEFAULT."""
    from polaris.core.pipeline.config import time_stop_k_mult

    assert time_stop_k_mult("0") == 4.0
    assert time_stop_k_mult("-2.5") == 4.0


# ---------------------------------------------------------------------------
# native_bars_seen preferred over wall-clock (bars-seen path)
# ---------------------------------------------------------------------------


async def test_time_stop_native_bars_seen_preferred_over_wall_clock() -> None:
    """A position within its native-bar horizon must NOT fire even if
    wall-clock alone (e.g. a weekend gap inflating held_seconds) looks past
    K x horizon — native_bars_seen, not held_seconds, decides."""
    pos = _position(held_seconds=UNREGISTERED_HORIZON_SEC * 4 + 1)
    pos["native_bars_seen"] = 1  # well under 10-bar x K=4 = 40 bars
    payload = {
        "position": pos,
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.HOLD


async def test_time_stop_native_bars_seen_fires_past_bar_horizon() -> None:
    """native_bars_seen past K x horizon_bars (10 x 4 = 40) -> EXIT_NOW even
    with a tiny held_seconds (fast native bar cadence)."""
    pos = _position(held_seconds=1)
    pos["native_bars_seen"] = 41
    payload = {
        "position": pos,
        "unrealized_pnl_r": 0.10,
        "max_loss_r": 1.0,
    }
    result = await position_monitor_gate(_ctx(payload), client=None, time_stop_k=4.0)
    assert result.decision == GateDecision.EXIT_NOW
    assert result.payload.get("reason") == "time_stop"
