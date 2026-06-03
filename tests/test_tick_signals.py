"""P5 tick signals — synthetic windows fire the right signal + regime gating.

Each test builds a tick window that *encodes a clear microstructure event*
(a burst / a bid-pressure imbalance / a stretched overshoot with exhausting
flow) and asserts the matching signal fires with the right side + a monotone
conviction — and that ``regime_gate`` disables the wrong signals for a regime.

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md §"신규 모듈".
"""

from __future__ import annotations

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.features import compute_tick_features
from polaris.core.ticks.regime_gate import active_signals, direction_bias, normalize_regime
from polaris.core.ticks.signals import (
    TickIntent,
    burst_rider,
    flow_pressure,
    micro_reversion,
)
from polaris.core.ticks.types import TickSample

CFG = TickEngineConfig()
NOW = 1_000.0
VENUE = "okx"
SYMBOL = "BTC-USDT"


def _tick(
    ts_ms: int,
    mid: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    last_trade_price: float | None = None,
    last_trade_size: float = 1.0,
    spread: float = 0.02,
) -> TickSample:
    half = spread / 2.0
    bid = mid - half
    ask = mid + half
    spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    return TickSample(
        ts=ts_ms,
        bid=bid,
        ask=ask,
        mid=mid,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price if last_trade_price is not None else mid,
        last_trade_size=last_trade_size,
        spread_bps=spread_bps,
    )


_BASE_MS = int((NOW - 1.0) * 1000)  # ~1s before NOW → fresh


# ---------------------------------------------------------------------------
# burst_rider — a calm baseline then a sharp up-tick with buy aggressor flow.
# ---------------------------------------------------------------------------


def _burst_window(direction: int) -> list[TickSample]:
    """16 near-flat ticks (tiny noise) then a sharp directional jump.

    ``direction`` +1 = up burst (buyer-aggressor), -1 = down burst (seller).
    Trades print on the aggressor side so aggr_flow agrees with the burst.
    """
    ticks: list[TickSample] = []
    mid = 100.0
    # Calm baseline: alternating ±0.001 noise (small, near-zero velocity).
    for i in range(16):
        mid += 0.001 if i % 2 == 0 else -0.001
        # Tiny trades on the same passive level (no strong aggressor).
        ticks.append(_tick(_BASE_MS + i * 100, mid, last_trade_price=mid, last_trade_size=0.5))
    # The burst: a big jump in ``direction``, trade prints through the mid.
    for j in range(4):
        mid += direction * 0.30
        trade_px = mid + direction * 0.05  # aggressor lifts/hits in the burst dir
        ticks.append(
            _tick(
                _BASE_MS + (16 + j) * 100,
                mid,
                last_trade_price=trade_px,
                last_trade_size=8.0,
            )
        )
    return ticks


def test_burst_rider_fires_long_on_up_burst() -> None:
    feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    assert feat.burst_z is not None and feat.burst_z > CFG.theta_burst
    intent = burst_rider(feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"
    assert intent.signal_id == "burst_rider"
    assert intent.signal_family == "momentum"
    assert 0.0 < intent.conviction <= CFG.conviction_cap
    assert intent.venue == VENUE and intent.symbol == SYMBOL and intent.ref_price == 101.0


def test_burst_rider_fires_short_on_down_burst() -> None:
    feat = compute_tick_features(_burst_window(-1), NOW, CFG)
    intent = burst_rider(feat, "bear_trend", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"


def test_burst_rider_blocked_by_wide_spread() -> None:
    # Same burst but the latest spread is far above θ_s → no entry crossing it.
    w = _burst_window(+1)
    last = w[-1]
    wide = TickSample(
        ts=last.ts,
        bid=last.mid - 1.0,
        ask=last.mid + 1.0,
        mid=last.mid,
        bid_size=last.bid_size,
        ask_size=last.ask_size,
        last_trade_price=last.last_trade_price,
        last_trade_size=last.last_trade_size,
        spread_bps=(2.0 / last.mid) * 1e4,  # ~200 bps ≫ θ_s
    )
    w[-1] = wide
    feat = compute_tick_features(w, NOW, CFG)
    assert feat.spread_bps is not None and feat.spread_bps >= CFG.theta_spread
    assert burst_rider(feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG) is None


def test_burst_rider_conviction_monotone_in_burst() -> None:
    # A bigger burst → higher (or equal, if saturated) conviction.
    small = compute_tick_features(_burst_window(+1), NOW, CFG)

    def _bigger() -> list[TickSample]:
        ticks: list[TickSample] = []
        mid = 100.0
        for i in range(16):
            mid += 0.001 if i % 2 == 0 else -0.001
            ticks.append(_tick(_BASE_MS + i * 100, mid, last_trade_size=0.5))
        for j in range(4):
            mid += 0.60  # twice the per-tick jump
            ticks.append(
                _tick(_BASE_MS + (16 + j) * 100, mid, last_trade_price=mid + 0.05, last_trade_size=8.0)
            )
        return ticks

    big = compute_tick_features(_bigger(), NOW, CFG)
    i_small = burst_rider(small, "trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
    i_big = burst_rider(big, "trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
    assert i_small is not None and i_big is not None
    assert i_big.conviction >= i_small.conviction


# ---------------------------------------------------------------------------
# flow_pressure — a sustained bid-heavy book with buyer aggressor flow.
# ---------------------------------------------------------------------------


def _imbalance_window(side_sign: int) -> list[TickSample]:
    """20 ticks with a persistent book imbalance + agreeing aggressor trades.

    ``side_sign`` +1 = bid-heavy (buy pressure), -1 = ask-heavy (sell pressure).
    """
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(20):
        mid += side_sign * 0.002  # gentle drift in the pressure direction
        if side_sign > 0:
            bid_size, ask_size = 120.0, 8.0
            trade_px = mid + 0.05  # buyer lifts the offer
        else:
            bid_size, ask_size = 8.0, 120.0
            trade_px = mid - 0.05  # seller hits the bid
        ticks.append(
            _tick(
                _BASE_MS + i * 100,
                mid,
                bid_size=bid_size,
                ask_size=ask_size,
                last_trade_price=trade_px,
                last_trade_size=4.0,
            )
        )
    return ticks


def test_flow_pressure_fires_long_on_bid_imbalance() -> None:
    feat = compute_tick_features(_imbalance_window(+1), NOW, CFG)
    assert feat.ofi is not None and feat.ofi > CFG.theta_ofi
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"
    assert intent.signal_id == "flow_pressure"
    assert intent.signal_family == "momentum"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_flow_pressure_fires_short_on_ask_imbalance() -> None:
    feat = compute_tick_features(_imbalance_window(-1), NOW, CFG)
    intent = flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"


def test_flow_pressure_silent_on_balanced_book() -> None:
    # Symmetric book, trades at mid → |ofi| ≈ 0 → no signal.
    balanced = [
        _tick(_BASE_MS + i * 100, 100.0, bid_size=50.0, ask_size=50.0, last_trade_price=100.0)
        for i in range(20)
    ]
    feat = compute_tick_features(balanced, NOW, CFG)
    assert flow_pressure(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=100.0, cfg=CFG) is None


# ---------------------------------------------------------------------------
# micro_reversion — a sharp overshoot up, then flow turns over (exhaustion).
# ---------------------------------------------------------------------------


def _overshoot_window(direction: int) -> list[TickSample]:
    """A long calm base then a sharp ``direction`` spike that flow no longer
    supports (last trade prints against the spike = exhaustion)."""
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.0005 if i % 2 == 0 else -0.0005  # near-flat anchor
        ticks.append(_tick(_BASE_MS + i * 100, mid, last_trade_price=mid, last_trade_size=0.5))
    # The overshoot: a sharp move in ``direction`` away from the EWMA anchor,
    # but the final aggressor flow OPPOSES it (the push is spent).
    for j in range(4):
        mid += direction * 0.20
        # Trade prints AGAINST the spike direction → exhausting flow.
        trade_px = mid - direction * 0.05
        ticks.append(
            _tick(
                _BASE_MS + (16 + j) * 100,
                mid,
                last_trade_price=trade_px,
                last_trade_size=6.0,
            )
        )
    return ticks


def test_micro_reversion_fades_an_up_overshoot_short() -> None:
    feat = compute_tick_features(_overshoot_window(+1), NOW, CFG)
    assert feat.overshoot_z is not None and feat.overshoot_z > CFG.theta_revert
    assert feat.aggr_flow is not None and feat.aggr_flow <= 0.0  # flow opposes
    intent = micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "short"  # fade the up overshoot
    assert intent.signal_id == "micro_reversion"
    assert intent.signal_family == "reversion"
    assert 0.0 < intent.conviction <= CFG.conviction_cap


def test_micro_reversion_fades_a_down_overshoot_long() -> None:
    feat = compute_tick_features(_overshoot_window(-1), NOW, CFG)
    intent = micro_reversion(feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=99.0, cfg=CFG)
    assert isinstance(intent, TickIntent)
    assert intent.side == "long"  # fade the down overshoot


def test_micro_reversion_silent_when_flow_still_supports_overshoot() -> None:
    # Same up overshoot, but the final trades keep LIFTING (flow still buying)
    # → not exhaustion → reversion must not fire.
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.0005 if i % 2 == 0 else -0.0005
        ticks.append(_tick(_BASE_MS + i * 100, mid, last_trade_size=0.5))
    for j in range(4):
        mid += 0.20
        ticks.append(
            _tick(_BASE_MS + (16 + j) * 100, mid, last_trade_price=mid + 0.05, last_trade_size=6.0)
        )
    feat = compute_tick_features(ticks, NOW, CFG)
    assert micro_reversion(feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=101.0, cfg=CFG) is None


# ---------------------------------------------------------------------------
# regime gate — the wrong signals are disabled per regime.
# ---------------------------------------------------------------------------


def test_regime_gate_active_sets() -> None:
    assert active_signals("bull_trend") == frozenset({"burst_rider", "flow_pressure"})
    assert active_signals("bear_trend") == frozenset({"burst_rider", "flow_pressure"})
    assert active_signals("chop") == frozenset({"micro_reversion", "flow_pressure"})
    assert active_signals("crisis") == frozenset({"micro_reversion"})
    # Unknown / None never silences the engine — flow_pressure stays on.
    assert active_signals("wat") == frozenset({"flow_pressure"})
    assert active_signals(None) == frozenset({"flow_pressure"})


def test_regime_gate_normalize_and_bias() -> None:
    assert normalize_regime("bull_trend") == "trend"
    assert normalize_regime("CHOP") == "range"
    assert normalize_regime(None) == "unknown"
    assert direction_bias("bull_trend") == 1
    assert direction_bias("bear_trend") == -1
    assert direction_bias("chop") == 0
    assert direction_bias("crisis") == 0


def test_gate_disables_burst_in_chop_and_reversion_in_trend() -> None:
    # A real burst window, but in a CHOP regime burst_rider is gated out, while
    # the reversion signal is gated out in a TREND regime.
    burst_feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    assert burst_rider(burst_feat, "chop", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None

    over_feat = compute_tick_features(_overshoot_window(+1), NOW, CFG)
    assert (
        micro_reversion(over_feat, "bull_trend", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG)
        is None
    )


def test_crisis_disables_momentum_signals() -> None:
    burst_feat = compute_tick_features(_burst_window(+1), NOW, CFG)
    imb_feat = compute_tick_features(_imbalance_window(+1), NOW, CFG)
    assert burst_rider(burst_feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None
    # crisis active set is {micro_reversion} only → flow_pressure also gated out.
    assert flow_pressure(imb_feat, "crisis", venue=VENUE, symbol=SYMBOL, ref_price=1.0, cfg=CFG) is None
