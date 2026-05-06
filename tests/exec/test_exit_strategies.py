"""Tests for src/exec/exit_strategies.py — composable exit logic (P6 pure)."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import (
    ExitDecision,
    MarketSnapshot,
    PartialTP,
    SignalReversal,
    StopLoss,
    TakeProfit,
    TimeBasedHold,
    TrailingStop,
    build_default_exits,
    evaluate_all,
)


def _market(price: float, ts_ms: int = 0, high: float | None = None,
            last_action: str | None = None) -> MarketSnapshot:
    return MarketSnapshot(ticker="BTC-USDT", price=price, ts_ms=ts_ms,
                          high_since_entry=high, last_signal_action=last_action)


# ─── TakeProfit ─────────────────────────────────────────────────────────────


class TestTakeProfit:
    def test_fires_at_threshold(self):
        ex = TakeProfit(0.006)  # +0.6%
        m = _market(price=100.61)  # +0.61% safely above
        d = ex.should_exit(entry_price=100.0, size_usd=100, open_ts_ms=0, market=m)
        assert d.should_close
        assert "tp_hit" in d.reason
        assert d.fraction == 1.0

    def test_no_fire_below_threshold(self):
        ex = TakeProfit(0.006)
        m = _market(price=100.5)
        d = ex.should_exit(entry_price=100.0, size_usd=100, open_ts_ms=0, market=m)
        assert not d.should_close

    def test_invalid_pct_raises(self):
        with pytest.raises(ValueError):
            TakeProfit(0)
        with pytest.raises(ValueError):
            TakeProfit(-0.01)


# ─── StopLoss ────────────────────────────────────────────────────────────────


class TestStopLoss:
    def test_fires_at_threshold(self):
        ex = StopLoss(0.0035)
        m = _market(price=99.60)  # -0.40% safely below
        d = ex.should_exit(100.0, 100, 0, m)
        assert d.should_close
        assert "sl_hit" in d.reason

    def test_no_fire_above_threshold(self):
        ex = StopLoss(0.0035)
        m = _market(price=99.70)
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close


# ─── TrailingStop ────────────────────────────────────────────────────────────


class TestTrailingStop:
    def test_no_fire_before_activation(self):
        ex = TrailingStop(activation_pct=0.005, trail_pct=0.003)
        m = _market(price=100.4, high=100.4)  # +0.4% < 0.5% activation
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close

    def test_no_fire_at_peak(self):
        ex = TrailingStop(activation_pct=0.005, trail_pct=0.003)
        m = _market(price=100.6, high=100.6)  # at peak, no drawdown
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close

    def test_fires_after_activation_and_drawdown(self):
        ex = TrailingStop(activation_pct=0.005, trail_pct=0.003)
        # peak 101 (+1%), now 100.7 (drawdown 0.297% from peak ≈ 0.3% trail)
        m = _market(price=100.69, high=101.0)  # drawdown ~0.307%
        d = ex.should_exit(100.0, 100, 0, m)
        assert d.should_close
        assert "trail_stop" in d.reason

    def test_no_fire_below_trail(self):
        ex = TrailingStop(activation_pct=0.005, trail_pct=0.003)
        m = _market(price=100.85, high=101.0)  # drawdown 0.15%
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close

    def test_no_fire_when_high_missing(self):
        ex = TrailingStop(activation_pct=0.005, trail_pct=0.003)
        m = _market(price=100.5)  # high_since_entry=None
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close


# ─── TimeBasedHold ───────────────────────────────────────────────────────────


class TestTimeBasedHold:
    def test_fires_after_max_hours(self):
        ex = TimeBasedHold(max_hours=4.0)
        m = _market(price=100, ts_ms=4 * 3_600_000 + 1000)
        d = ex.should_exit(100, 100, open_ts_ms=0, market=m)
        assert d.should_close
        assert "max_hold" in d.reason

    def test_no_fire_before_max(self):
        ex = TimeBasedHold(max_hours=4.0)
        m = _market(price=100, ts_ms=3 * 3_600_000)
        d = ex.should_exit(100, 100, 0, m)
        assert not d.should_close

    def test_invalid_max_raises(self):
        with pytest.raises(ValueError):
            TimeBasedHold(0)


# ─── SignalReversal ──────────────────────────────────────────────────────────


class TestSignalReversal:
    def test_fires_on_exit_action_after_min_hold(self):
        ex = SignalReversal("volume_burst", min_hold_ms=0)
        m = _market(price=100, last_action="EXIT", ts_ms=1000)
        d = ex.should_exit(100, 100, open_ts_ms=0, market=m)
        assert d.should_close
        assert "signal_exit" in d.reason

    def test_no_fire_within_min_hold(self):
        ex = SignalReversal("volume_burst", min_hold_ms=90_000)
        m = _market(price=100, last_action="EXIT", ts_ms=50_000)  # 50s held
        d = ex.should_exit(100, 100, open_ts_ms=0, market=m)
        assert not d.should_close

    def test_fires_at_min_hold_boundary(self):
        ex = SignalReversal("volume_burst", min_hold_ms=90_000)
        m = _market(price=100, last_action="EXIT", ts_ms=90_000)  # exactly 90s
        d = ex.should_exit(100, 100, open_ts_ms=0, market=m)
        assert d.should_close

    def test_no_fire_on_hold(self):
        ex = SignalReversal("volume_burst", min_hold_ms=0)
        m = _market(price=100, last_action="HOLD")
        d = ex.should_exit(100, 100, 0, m)
        assert not d.should_close


# ─── PartialTP ───────────────────────────────────────────────────────────────


class TestPartialTP:
    def test_fires_at_first_level(self):
        ex = PartialTP(levels=((0.005, 0.33), (0.010, 0.33), (0.015, 0.34)))
        m = _market(price=100.5)  # +0.5%
        d = ex.should_exit(100.0, 100, 0, m)
        assert d.should_close
        assert d.fraction == 0.33

    def test_fires_at_highest_reached(self):
        ex = PartialTP(levels=((0.005, 0.33), (0.010, 0.33), (0.015, 0.34)))
        m = _market(price=101.5)  # +1.5%
        d = ex.should_exit(100.0, 100, 0, m)
        assert d.should_close
        # Highest reached → 1.5% level → 0.34 fraction
        assert d.fraction == 0.34

    def test_no_fire_below_first(self):
        ex = PartialTP(levels=((0.005, 0.33),))
        m = _market(price=100.4)
        d = ex.should_exit(100.0, 100, 0, m)
        assert not d.should_close

    def test_invalid_levels_raise(self):
        with pytest.raises(ValueError):
            PartialTP(levels=())
        with pytest.raises(ValueError):
            PartialTP(levels=((0.0, 0.5),))  # zero pct
        with pytest.raises(ValueError):
            PartialTP(levels=((0.005, 1.5),))  # frac > 1


# ─── build_default_exits factory ─────────────────────────────────────────────


class TestBuildDefaultExits:
    def test_scalp_profile(self):
        exits = build_default_exits("scalp")
        assert len(exits) == 3
        names = [e.name for e in exits]
        assert "take_profit" in names
        assert "stop_loss" in names
        assert "time_based" in names

    def test_swing_profile(self):
        exits = build_default_exits("swing")
        # Swing TP 5% > scalp TP 0.6%
        tp = next(e for e in exits if e.name == "take_profit")
        assert tp.pct == 0.05

    def test_position_profile(self):
        exits = build_default_exits("position")
        tp = next(e for e in exits if e.name == "take_profit")
        assert tp.pct == 0.12  # +12%

    def test_unknown_profile_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            build_default_exits("nonexistent")


# ─── evaluate_all combinator ─────────────────────────────────────────────────


class TestEvaluateAll:
    def test_returns_none_when_no_fire(self):
        exits = (TakeProfit(0.01), StopLoss(0.01))
        m = _market(price=100.5)  # neither
        d = evaluate_all(exits, 100, 100, 0, m)
        assert d is None

    def test_first_fire_wins(self):
        exits = (TakeProfit(0.005), StopLoss(0.005))
        m = _market(price=100.6)  # TP hits first
        d = evaluate_all(exits, 100, 100, 0, m)
        assert d.should_close
        assert "tp_hit" in d.reason

    def test_partial_tp_filtered_by_fired_levels(self):
        # Already fired the 0.5% level → only 1.0% can fire
        exits = (PartialTP(levels=((0.005, 0.33), (0.010, 0.33))),)
        m = _market(price=100.6)  # would normally fire 0.5% level
        fired = {0.005}
        d = evaluate_all(exits, 100, 100, 0, m, fired_partial_levels=fired)
        assert d is None  # 0.5% filtered out, 1.0% not yet reached

    def test_partial_tp_unfired_level_fires(self):
        exits = (PartialTP(levels=((0.005, 0.33), (0.010, 0.33))),)
        m = _market(price=101.1)  # 1.1% — both levels reached
        fired = {0.005}  # 0.5% already fired
        d = evaluate_all(exits, 100, 100, 0, m, fired_partial_levels=fired)
        assert d.should_close
        assert d.fraction == 0.33
        assert "1.00%" in d.reason


# ─── Immutability ────────────────────────────────────────────────────────────


class TestImmutability:
    def test_exit_decision_frozen(self):
        d = ExitDecision(True, "test", 0.5)
        with pytest.raises(Exception):
            d.fraction = 1.0  # type: ignore[misc]

    def test_take_profit_frozen(self):
        ex = TakeProfit(0.005)
        with pytest.raises(Exception):
            ex.pct = 0.01  # type: ignore[misc]
