"""equity_donchian55_breakout — Alpaca 1D Donchian-55 + ROC-20 breakout.

DEMO/PAPER virtual sleeve, per-venue clone of okx_donchian_55_breakout. Covers
the breakout/no-breakout boundary, the pre-fed vs recomputed ROC-20 branches,
the §0d liquidity gate, warmup, shadow_w40/w80 measurement tags, and
degrade-never-crash on empty/short bar lists.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY, EquityDonchian55BreakoutStrategy
from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.equity_donchian55_breakout import (
    DONCHIAN_WINDOW,
    ROC_LOOKBACK,
)

_DAY = 86_400


def _flat_bars(n: int, *, close: float = 100.0, high: float = 100.3,
                low: float = 99.7, volume: float = 1_000_000.0) -> list[BarView]:
    base = 1_700_000_000
    return [
        BarView(ts=base + i * _DAY, open=close, high=high, low=low, close=close,
                volume=volume)
        for i in range(n)
    ]


def _mv(bars: list[BarView], *, momentum_20bar: float | None = None) -> MarketView:
    return MarketView(
        symbol="XYZ", venue="alpaca", timeframe="1D",
        bars=bars, last_price=bars[-1].close if bars else 0.0, spread_bps=2.0,
        momentum_20bar=momentum_20bar,
    )


def _breakout_bars(n_flat: int = 75, *, breakout_close: float = 101.0) -> list[BarView]:
    """``n_flat`` flat bars at close=100/high=100.3, then ONE breakout bar."""
    bars = _flat_bars(n_flat)
    last_ts = bars[-1].ts + _DAY
    bars.append(BarView(ts=last_ts, open=breakout_close, high=breakout_close + 0.2,
                         low=breakout_close - 0.2, close=breakout_close,
                         volume=1_000_000.0))
    return bars


# ---------------------------------------------------------------------------
# A — entry / no-entry boundary
# ---------------------------------------------------------------------------


def test_fires_on_breakout_with_recomputed_roc() -> None:
    # 76 bars (minimum warmup): 75 flat @ high=100.3, then a breakout close
    # that clears the 55-bar prior high AND is above close 21 bars back (flat
    # 100) -> recomputed ROC-20 > 0.
    bars = _breakout_bars(75, breakout_close=101.0)
    assert len(bars) == DONCHIAN_WINDOW + ROC_LOOKBACK + 1
    sig = EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_donchian55_breakout"
    assert 0.0 < sig.strength <= 1.0
    assert float(sig.tags["roc_20"]) > 0.0


def test_no_signal_when_close_does_not_clear_prior_high() -> None:
    bars = _breakout_bars(75, breakout_close=100.2)  # <= prior high 100.3
    assert EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_when_prefed_roc_negative() -> None:
    # Breaks the prior high, but the PRE-FED momentum_20bar is negative — the
    # pre-fed field wins over recompute (is_finite fallback pattern), so a
    # negative pre-fed ROC blocks the entry even though the raw bars would
    # recompute positive.
    bars = _breakout_bars(75, breakout_close=101.0)
    sig = EquityDonchian55BreakoutStrategy().generate_raw_signal(
        _mv(bars, momentum_20bar=-0.02)
    )
    assert sig is None


def test_fires_using_prefed_positive_roc_value_in_tags() -> None:
    bars = _breakout_bars(75, breakout_close=101.0)
    sig = EquityDonchian55BreakoutStrategy().generate_raw_signal(
        _mv(bars, momentum_20bar=0.0777)
    )
    assert sig is not None
    assert sig.tags["roc_20"] == "0.0777"


# ---------------------------------------------------------------------------
# B — liquidity gate (§0d)
# ---------------------------------------------------------------------------


def test_no_signal_when_illiquid() -> None:
    bars = _flat_bars(75, volume=100.0)  # $10k/day, far below $30M floor
    last_ts = bars[-1].ts + _DAY
    bars.append(BarView(ts=last_ts, open=101.0, high=101.2, low=100.8, close=101.0,
                         volume=100.0))
    assert EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# C — warmup
# ---------------------------------------------------------------------------


def test_no_signal_before_warmup() -> None:
    bars = _breakout_bars(50, breakout_close=101.0)  # 51 bars < 76 warmup
    assert EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# D — shadow_w40 / shadow_w80 measurement tags
# ---------------------------------------------------------------------------


def test_shadow_tags_stamped_at_minimum_warmup() -> None:
    # Minimum warmup (76 bars): the 40-bar shadow window is fully available
    # (a flat plateau, so a genuine breakout ALSO clears it -> "1"), the
    # 80-bar shadow window is NOT (76 < 81) -> degrades to "0".
    bars = _breakout_bars(75, breakout_close=101.0)
    sig = EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.tags["shadow_w40"] == "1"
    assert sig.tags["shadow_w80"] == "0"


def test_shadow_w80_computed_with_sufficient_history() -> None:
    # 90 flat bars (>= 81 needed for the 80-window) then a breakout bar.
    bars = _breakout_bars(90, breakout_close=101.0)
    sig = EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.tags["shadow_w40"] == "1"
    assert sig.tags["shadow_w80"] == "1"


# ---------------------------------------------------------------------------
# E — degrade-never-crash
# ---------------------------------------------------------------------------


def test_degrade_never_crash_on_empty_bars() -> None:
    assert EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv([])) is None


def test_degrade_never_crash_on_short_bars() -> None:
    bars = _flat_bars(5)
    assert EquityDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# F — metadata (spec §1 table row #1, verbatim)
# ---------------------------------------------------------------------------


def test_metadata_matches_spec_table() -> None:
    md = EquityDonchian55BreakoutStrategy.metadata
    assert md.strategy_id == "equity_donchian55_breakout"
    assert md.timeframe == "1D"
    assert md.warmup_bars == 76
    assert md.max_positions == 8
    assert md.gross_cap == 0.24
    assert md.per_symbol_cap == 0.05
    assert md.expected_holding_bars == 25
    assert md.asset_class == "equity"
    assert md.venue == "alpaca"
    assert md.product_class == "equity"
    assert md.correlation_group_id == "equity_donchian55_trend"
    assert md.hold_overnight is True
    assert md.profit_target_r is None


def test_dispatch_eligible_is_virtual_only() -> None:
    md = EquityDonchian55BreakoutStrategy.metadata
    assert md.dispatch_eligible == virtual_loosen(True, False)
    assert md.dispatch_eligible == virtual_mode_enabled()


def test_registered_in_registry() -> None:
    assert (
        STRATEGY_REGISTRY["equity_donchian55_breakout"]
        is EquityDonchian55BreakoutStrategy
    )
