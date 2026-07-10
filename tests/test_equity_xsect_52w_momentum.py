"""equity_xsect_52w_momentum — Alpaca 1D 252-bar prior-high + 2-close
persistence + ATR follow-through confirm.

DEMO/PAPER virtual sleeve. Covers the "원샷 틱 진입 금지" (no one-shot-tick
entry) persistence gate, the ATR follow-through confirm, the §0d liquidity
gate, warmup, and degrade-never-crash on empty/short bar lists.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY, EquityXsect52wMomentumStrategy
from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import BarView, MarketView

_DAY = 86_400


def _bars_window_plus_confirm(
    n_total: int,
    *,
    window_high: float = 100.3,
    window_close: float = 100.0,
    window_low: float = 99.7,
    confirm1_close: float,
    confirm1_high: float,
    confirm1_low: float,
    confirm2_close: float,
    confirm2_high: float,
    confirm2_low: float,
    volume: float = 1_000_000.0,
) -> list[BarView]:
    """``n_total`` bars: all but the last 2 are a flat plateau (so
    ``prior_high252`` == ``window_high`` for any ``n_total >= 254``), then two
    CONFIRM bars (``bars[-2]``, ``bars[-1]``) with the given OHLC."""
    base = 1_700_000_000
    bars: list[BarView] = []
    for i in range(n_total - 2):
        bars.append(BarView(ts=base + i * _DAY, open=window_close, high=window_high,
                             low=window_low, close=window_close, volume=volume))
    bars.append(BarView(ts=base + (n_total - 2) * _DAY, open=confirm1_close,
                         high=confirm1_high, low=confirm1_low, close=confirm1_close,
                         volume=volume))
    bars.append(BarView(ts=base + (n_total - 1) * _DAY, open=confirm2_close,
                         high=confirm2_high, low=confirm2_low, close=confirm2_close,
                         volume=volume))
    return bars


def _mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="XYZ", venue="alpaca", timeframe="1D",
        bars=bars, last_price=bars[-1].close if bars else 0.0, spread_bps=2.0,
    )


def _strong_breakout_bars(n_total: int = 280) -> list[BarView]:
    return _bars_window_plus_confirm(
        n_total,
        confirm1_close=101.0, confirm1_high=101.2, confirm1_low=100.8,
        confirm2_close=101.5, confirm2_high=101.7, confirm2_low=101.3,
    )


# ---------------------------------------------------------------------------
# A — fires on a genuine 2-close persistent breakout with follow-through
# ---------------------------------------------------------------------------


def test_fires_on_persistent_breakout_with_follow_through() -> None:
    bars = _strong_breakout_bars()
    sig = EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_xsect_52w_momentum"
    assert 0.0 < sig.strength <= 1.0
    assert sig.tags["prior_high_252"] == "100.3000"
    assert float(sig.tags["excess_atr"]) > 0.0


# ---------------------------------------------------------------------------
# B — one-shot tick entry MUST be blocked (persistence gate)
# ---------------------------------------------------------------------------


def test_no_one_shot_tick_entry_when_only_last_close_breaks_out() -> None:
    # bars[-2] does NOT clear prior_high252 (stays at the flat close); bars[-1]
    # breaks out hugely. A single-tick spike must NOT fire.
    bars = _bars_window_plus_confirm(
        280,
        confirm1_close=100.0, confirm1_high=100.2, confirm1_low=99.8,
        confirm2_close=105.0, confirm2_high=105.2, confirm2_low=104.8,
    )
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_when_only_first_confirm_breaks_out() -> None:
    # The mirror case: bars[-2] breaks out but bars[-1] reverts back under the
    # prior high — persistence is NOT sustained through the current close.
    bars = _bars_window_plus_confirm(
        280,
        confirm1_close=101.0, confirm1_high=101.2, confirm1_low=100.8,
        confirm2_close=100.1, confirm2_high=100.3, confirm2_low=99.9,
    )
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# C — ATR follow-through confirm
# ---------------------------------------------------------------------------


def test_no_signal_when_follow_through_too_shallow() -> None:
    # Both confirm bars persist above prior_high252, but only by a few cents
    # (excess << 0.25*ATR14) — a weak, unconvincing breakout.
    bars = _bars_window_plus_confirm(
        280,
        confirm1_close=100.32, confirm1_high=100.42, confirm1_low=100.22,
        confirm2_close=100.35, confirm2_high=100.45, confirm2_low=100.25,
    )
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# D — liquidity gate (§0d)
# ---------------------------------------------------------------------------


def test_no_signal_when_illiquid() -> None:
    bars = _bars_window_plus_confirm(
        280,
        confirm1_close=101.0, confirm1_high=101.2, confirm1_low=100.8,
        confirm2_close=101.5, confirm2_high=101.7, confirm2_low=101.3,
        volume=10.0,  # ~$1k/day, far below the $30M floor
    )
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# E — warmup
# ---------------------------------------------------------------------------


def test_no_signal_before_warmup() -> None:
    bars = _strong_breakout_bars(n_total=260)  # < 270 warmup
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# F — degrade-never-crash
# ---------------------------------------------------------------------------


def test_degrade_never_crash_on_empty_bars() -> None:
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv([])) is None


def test_degrade_never_crash_on_short_bars() -> None:
    bars = _strong_breakout_bars(n_total=280)[:5]
    assert EquityXsect52wMomentumStrategy().generate_raw_signal(_mv(bars)) is None


# ---------------------------------------------------------------------------
# G — metadata (spec §1 table row #2, verbatim)
# ---------------------------------------------------------------------------


def test_metadata_matches_spec_table() -> None:
    md = EquityXsect52wMomentumStrategy.metadata
    assert md.strategy_id == "equity_xsect_52w_momentum"
    assert md.timeframe == "1D"
    assert md.warmup_bars == 270
    assert md.max_positions == 3
    assert md.gross_cap == 0.12
    assert md.per_symbol_cap == 0.05
    assert md.expected_holding_bars == 20
    assert md.asset_class == "equity"
    assert md.venue == "alpaca"
    assert md.product_class == "equity"
    assert md.correlation_group_id == "equity_xsect_52w_momentum"
    assert md.hold_overnight is True
    assert md.profit_target_r is None


def test_dispatch_eligible_is_virtual_only() -> None:
    md = EquityXsect52wMomentumStrategy.metadata
    assert md.dispatch_eligible == virtual_loosen(True, False)
    assert md.dispatch_eligible == virtual_mode_enabled()


def test_registered_in_registry() -> None:
    assert (
        STRATEGY_REGISTRY["equity_xsect_52w_momentum"]
        is EquityXsect52wMomentumStrategy
    )
