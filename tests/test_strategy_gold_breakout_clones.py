"""silver/us100/uk100_breakout_1h — per-symbol clones of gold_breakout_1h (TDD).

DEMO/PAPER paper-trading. Opus CONFIRMED backtest spec: 3 per-symbol clones —
SILVER/US100/UK100 — each a straight per-symbol clone of gold_breakout_1h
(ADR-008 signal-generator-only): DONCHIAN_WINDOW=55, EXIT_DONCHIAN=20 (G7-owned
trail), TTL_BARS=3, warmup 60, LONG-only, venue=capital/cfd, hold_overnight=True,
profit_target_r=None, TREND let-run correlation_group (per_ticker_tailored: each
its OWN group, never shared). Mirrors ``tests/test_strategy_wave2.py``'s
gold_breakout_1h coverage: ENTRY trigger, in-module Donchian-55 recompute (NOT
pre-fed), metadata + registry wiring, no look-ahead, out-of-universe no-emit.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_thesis import bucket_from_correlation_group
from polaris.core.live_recalc.exit_types import Bucket
from polaris.strategies import STRATEGY_REGISTRY, BarView, MarketView
from polaris.strategies.silver_breakout_1h import (
    DONCHIAN_WINDOW as SILVER_WINDOW,
)
from polaris.strategies.silver_breakout_1h import (
    SilverBreakout1HStrategy,
)
from polaris.strategies.uk100_breakout_1h import (
    DONCHIAN_WINDOW as UK100_WINDOW,
)
from polaris.strategies.uk100_breakout_1h import (
    UK100Breakout1HStrategy,
)
from polaris.strategies.us100_breakout_1h import (
    DONCHIAN_WINDOW as US100_WINDOW,
)
from polaris.strategies.us100_breakout_1h import (
    US100Breakout1HStrategy,
)

_HOUR = 3_600


def _bars(n: int, *, base_close: float, drift: float) -> list[BarView]:
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * _HOUR,
                open=c - 0.2,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=1000.0,
            )
        )
    return out


def _breakout_last(bars: list[BarView], window: int) -> None:
    prior_high = max(b.high for b in bars[-(window + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )


def _mv(bars: list[BarView], *, symbol: str) -> MarketView:
    return MarketView(
        symbol=symbol,
        venue="capital",
        timeframe="1H",
        bars=bars,
        last_price=bars[-1].close if bars else 0.0,
        spread_bps=4.0,
    )


def _assert_trend_let_run(sid: str) -> None:
    m = STRATEGY_REGISTRY[sid].metadata
    assert bucket_from_correlation_group(m.correlation_group_id) is Bucket.TREND
    assert m.hold_overnight is True
    assert m.profit_target_r is None
    assert m.venue == "capital"
    assert m.product_class == "cfd"


# ---------------------------------------------------------------------------
# silver_breakout_1h
# ---------------------------------------------------------------------------


def test_silver_emits_on_break() -> None:
    s = SilverBreakout1HStrategy()
    bars = _bars(70, base_close=25.0, drift=0.05)
    _breakout_last(bars, SILVER_WINDOW)
    sig = s.generate_raw_signal(_mv(bars, symbol="SILVER"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "silver_breakout_1h"


def test_silver_no_lookahead() -> None:
    s = SilverBreakout1HStrategy()
    bars = _bars(70, base_close=25.0, drift=0.05)
    prior_high = max(b.high for b in bars[-(SILVER_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="SILVER")) is None


def test_silver_out_of_universe_no_emit() -> None:
    s = SilverBreakout1HStrategy()
    bars = _bars(70, base_close=25.0, drift=0.05)
    _breakout_last(bars, SILVER_WINDOW)
    assert s.generate_raw_signal(_mv(bars, symbol="GOLD")) is None


def test_silver_metadata_and_registry() -> None:
    _assert_trend_let_run("silver_breakout_1h")
    m = SilverBreakout1HStrategy.metadata
    assert m.timeframe == "1H"
    assert SILVER_WINDOW == 55
    assert m.asset_class == "commodity"
    assert m.correlation_group_id == "cfd_silver_breakout_1h"
    assert STRATEGY_REGISTRY["silver_breakout_1h"] is SilverBreakout1HStrategy


# ---------------------------------------------------------------------------
# us100_breakout_1h
# ---------------------------------------------------------------------------


def test_us100_emits_on_break() -> None:
    s = US100Breakout1HStrategy()
    bars = _bars(70, base_close=19000.0, drift=5.0)
    _breakout_last(bars, US100_WINDOW)
    sig = s.generate_raw_signal(_mv(bars, symbol="US100"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "us100_breakout_1h"


def test_us100_no_lookahead() -> None:
    s = US100Breakout1HStrategy()
    bars = _bars(70, base_close=19000.0, drift=5.0)
    prior_high = max(b.high for b in bars[-(US100_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="US100")) is None


def test_us100_out_of_universe_no_emit() -> None:
    s = US100Breakout1HStrategy()
    bars = _bars(70, base_close=19000.0, drift=5.0)
    _breakout_last(bars, US100_WINDOW)
    assert s.generate_raw_signal(_mv(bars, symbol="UK100")) is None


def test_us100_metadata_and_registry() -> None:
    _assert_trend_let_run("us100_breakout_1h")
    m = US100Breakout1HStrategy.metadata
    assert m.timeframe == "1H"
    assert US100_WINDOW == 55
    assert m.asset_class == "indices"
    assert m.correlation_group_id == "cfd_us100_breakout_1h"
    assert STRATEGY_REGISTRY["us100_breakout_1h"] is US100Breakout1HStrategy


# ---------------------------------------------------------------------------
# uk100_breakout_1h (+ probe per_symbol_cap)
# ---------------------------------------------------------------------------


def test_uk100_emits_on_break() -> None:
    s = UK100Breakout1HStrategy()
    bars = _bars(70, base_close=7500.0, drift=2.0)
    _breakout_last(bars, UK100_WINDOW)
    sig = s.generate_raw_signal(_mv(bars, symbol="UK100"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "uk100_breakout_1h"


def test_uk100_no_lookahead() -> None:
    s = UK100Breakout1HStrategy()
    bars = _bars(70, base_close=7500.0, drift=2.0)
    prior_high = max(b.high for b in bars[-(UK100_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, symbol="UK100")) is None


def test_uk100_out_of_universe_no_emit() -> None:
    s = UK100Breakout1HStrategy()
    bars = _bars(70, base_close=7500.0, drift=2.0)
    _breakout_last(bars, UK100_WINDOW)
    assert s.generate_raw_signal(_mv(bars, symbol="US100")) is None


def test_uk100_metadata_and_registry() -> None:
    _assert_trend_let_run("uk100_breakout_1h")
    m = UK100Breakout1HStrategy.metadata
    assert m.timeframe == "1H"
    assert UK100_WINDOW == 55
    assert m.asset_class == "indices"
    assert m.correlation_group_id == "cfd_uk100_breakout_1h"
    assert STRATEGY_REGISTRY["uk100_breakout_1h"] is UK100Breakout1HStrategy


def test_uk100_probe_cap_is_half_sibling_default() -> None:
    # PROBE: per_symbol_cap starts at HALF the sibling clones' 0.10 (IS half-
    # sample was net-negative). env-tunable, not hardcoded permanently narrow.
    m = UK100Breakout1HStrategy.metadata
    sibling = US100Breakout1HStrategy.metadata
    assert m.per_symbol_cap == sibling.per_symbol_cap / 2.0
    assert m.per_symbol_cap == 0.05


def test_uk100_per_symbol_cap_env_tunable(monkeypatch) -> None:
    import importlib

    monkeypatch.setenv("POLARIS_UK100_PER_SYMBOL_CAP_PCT", "0.08")
    import polaris.strategies.uk100_breakout_1h as mod

    importlib.reload(mod)
    try:
        assert mod.UK100Breakout1HStrategy.metadata.per_symbol_cap == 0.08
    finally:
        monkeypatch.delenv("POLARIS_UK100_PER_SYMBOL_CAP_PCT", raising=False)
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# Sibling clones share the SAME trigger/exit shape as gold_breakout_1h but each
# owns a DISTINCT correlation_group_id (per_ticker_tailored — no shared cell).
# ---------------------------------------------------------------------------


def test_clones_have_distinct_correlation_groups() -> None:
    groups = {
        STRATEGY_REGISTRY["gold_breakout_1h"].metadata.correlation_group_id,
        STRATEGY_REGISTRY["silver_breakout_1h"].metadata.correlation_group_id,
        STRATEGY_REGISTRY["us100_breakout_1h"].metadata.correlation_group_id,
        STRATEGY_REGISTRY["uk100_breakout_1h"].metadata.correlation_group_id,
    }
    assert len(groups) == 4
