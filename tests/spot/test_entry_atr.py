"""ATR-based entry plan — ADR-007 Phase α."""
import numpy as np

from invasion.spot.execution import entry_atr


def _candles(n: int = 80, base: float = 100.0, noise: float = 0.4,
              seed: int = 7, spread: float | None = None) -> list[dict]:
    """Generate candles where high/low spread scales with noise."""
    rng = np.random.default_rng(seed)
    closes = base + np.cumsum(rng.normal(0, noise, n))
    s = spread if spread is not None else max(noise * 0.5, 0.02)
    out = []
    for i, p in enumerate(closes):
        p = float(p)
        out.append({"ts": i * 300, "open": p - s * 0.3,
                    "high": p + s, "low": p - s,
                    "close": p, "volume": 1500.0})
    return out


def test_compute_atr_pct_positive():
    candles = _candles(80)
    px = candles[-1]["close"]
    atr_pct = entry_atr.compute_atr_pct(candles, px)
    assert 0.0 < atr_pct < 0.05


def test_build_plan_default_mults():
    candles = _candles(80)
    px = candles[-1]["close"]
    plan = entry_atr.build_plan(last_px=px, candles_5m=candles)
    assert plan.enter is True
    # TP / SL signs correct
    assert plan.dynamic_tp_pct > 0
    assert plan.dynamic_sl_pct < 0
    # Default mults from preg or fallback
    assert 3.5 <= plan.atr_tp_mult <= 5.0
    assert 1.0 <= plan.atr_sl_mult <= 2.5


def test_build_plan_uses_cell_thresholds():
    candles = _candles(80)
    px = candles[-1]["close"]
    plan = entry_atr.build_plan(
        last_px=px, candles_5m=candles,
        cell_thresholds={"optimal_atr_tp_mult": 6.0,
                          "optimal_atr_sl_mult": 2.0},
    )
    assert plan.atr_tp_mult == 6.0
    assert plan.atr_sl_mult == 2.0


def test_spike_skip_when_1m_atr_high():
    """Spike: 1m candle volatility is 4× 5m → skip per atr_spike_skip_ratio."""
    calm_5m = _candles(80, noise=0.05, spread=0.05, seed=1)
    bursty_1m = _candles(60, noise=1.0, spread=2.0, seed=2)
    px = calm_5m[-1]["close"]
    plan = entry_atr.build_plan(
        last_px=px, candles_5m=calm_5m, candles_1m=bursty_1m)
    assert plan.enter is False
    assert plan.reason == "atr_spike"


def test_no_atr_returns_no_atr():
    flat_candles = [{"ts": i * 300, "open": 100, "high": 100,
                      "low": 100, "close": 100, "volume": 1.0}
                     for i in range(50)]
    plan = entry_atr.build_plan(last_px=100.0, candles_5m=flat_candles)
    assert plan.enter is False
    assert plan.reason == "no_atr"
