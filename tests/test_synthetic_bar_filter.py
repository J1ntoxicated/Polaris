"""Synthetic / flat-bar data-quality filter + regime-alignment id coverage.

Two correctness fixes from the 2026-06-22 research agenda (B/D):

1. OKX SYNTHETIC-BAR FILTER — fabricated flat / zero-volume forward-fill bars
   must be EXCLUDED from indicator computation (vol_z / ATR / donchian) so
   signals are measured on REAL price action. An all-synthetic window yields
   no signal (data ABSENCE, not an error / not a block). flow_not_block:
   data-quality only, no throttle, auto-resumes on real bars.

2. score.py regime-alignment frozensets must cover EVERY live STRATEGY_REGISTRY
   id (4 equity/fx ids were silently excluded → no regime alignment).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as hyp

from polaris.core.cell_matrix.score import (
    _COUNTER_TREND_STRATEGIES,
    _TREND_STRATEGIES,
    REGIME_ALIGN_AMPLIFY,
    REGIME_ALIGN_DAMPEN,
    regime_alignment_mult,
)
from polaris.core.data.schema import Bar
from polaris.scripts._production_indicators import (
    build_real_market_view,
    compute_volume_z,
    filter_real_bars,
    is_synthetic_bar,
)
from polaris.strategies import STRATEGY_REGISTRY

# ---------------------------------------------------------------------------
# Bar builders
# ---------------------------------------------------------------------------


def _real_bar(*, ts: int, close: float, volume: float = 1_000.0) -> Bar:
    """A genuine bar with non-zero range and positive volume."""
    return Bar(
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        venue="okx",
        symbol="BTC-USDT",
        bar_interval="1m",
        ts=ts,
        open=close * 0.999,
        high=close * 1.002,
        low=close * 0.997,
        close=close,
        volume=volume,
    )


def _flat_bar(*, ts: int, price: float, volume: float = 1_000.0) -> Bar:
    """A zero-range fabricated bar: open == high == low == close."""
    return Bar(
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        venue="okx",
        symbol="BTC-USDT",
        bar_interval="1m",
        ts=ts,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


def _zero_vol_bar(*, ts: int, close: float) -> Bar:
    """A bar with real-looking range but ZERO volume (placeholder)."""
    return _real_bar(ts=ts, close=close, volume=0.0)


# ---------------------------------------------------------------------------
# is_synthetic_bar / filter_real_bars
# ---------------------------------------------------------------------------


def test_flat_bar_is_synthetic() -> None:
    assert is_synthetic_bar(_flat_bar(ts=0, price=60_000.0)) is True


def test_zero_volume_bar_is_synthetic() -> None:
    assert is_synthetic_bar(_zero_vol_bar(ts=0, close=60_000.0)) is True


def test_real_bar_is_not_synthetic() -> None:
    assert is_synthetic_bar(_real_bar(ts=0, close=60_000.0)) is False


def test_filter_drops_flat_and_zero_vol_keeps_real() -> None:
    bars = [
        _real_bar(ts=0, close=100.0),
        _flat_bar(ts=60, price=100.0),
        _zero_vol_bar(ts=120, close=101.0),
        _real_bar(ts=180, close=102.0),
    ]
    kept = filter_real_bars(bars)
    assert len(kept) == 2
    assert [b.ts for b in kept] == [0, 180]  # order (newest last) preserved


# ---------------------------------------------------------------------------
# vol_z computed on REAL bars only (the core measurement-first guarantee)
# ---------------------------------------------------------------------------


def test_vol_z_excludes_synthetic_bars_via_market_view() -> None:
    """vol_z over a window padded with flat/zero-vol bars must equal vol_z over
    the real bars alone — the synthetic bars never enter the computation."""
    real = [_real_bar(ts=i * 60, close=100.0 + i, volume=1_000.0 + i * 10) for i in range(30)]
    # Interleave fabricated flats / zero-vol bars that would skew the z-score.
    polluted: list[Bar] = []
    t = 0
    for b in real:
        polluted.append(b)
        polluted.append(_flat_bar(ts=(t := t + 1) * 60 + 30, price=b.close, volume=9_999_999.0))
        polluted.append(_zero_vol_bar(ts=(t := t + 1) * 60 + 45, close=b.close))

    mv_polluted = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=polluted,
    )
    expected = compute_volume_z(filter_real_bars(real))
    assert mv_polluted.volume_z == pytest.approx(expected)
    # And the huge synthetic-volume bar did NOT inflate the z-score.
    mv_raw_polluted_vol_z = compute_volume_z(polluted)  # includes garbage
    assert mv_polluted.volume_z != pytest.approx(mv_raw_polluted_vol_z)


def test_all_flat_symbol_yields_no_signal_not_error() -> None:
    """A window of ONLY synthetic bars → empty MarketView (data absence). No
    exception; bars empty so every strategy's warmup gate suppresses emission."""
    all_flat = [_flat_bar(ts=i * 60, price=60_000.0) for i in range(50)]
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=all_flat,
    )
    assert mv.bars == []
    assert mv.last_price == 0.0
    assert mv.volume_z == 0.0  # default neutral, not a crash


def test_all_zero_volume_symbol_yields_no_signal() -> None:
    all_zero_vol = [_zero_vol_bar(ts=i * 60, close=60_000.0 + i) for i in range(50)]
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=all_zero_vol,
    )
    assert mv.bars == []
    assert mv.last_price == 0.0


def test_real_bars_preserved_indicators_nonzero() -> None:
    """A clean real-bar window still produces a populated view (filter is a
    no-op on real data — no over-filtering of genuine movement)."""
    real = [_real_bar(ts=i * 60, close=100.0 + i) for i in range(40)]
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=real,
    )
    assert len(mv.bars) == 40
    assert mv.last_price == pytest.approx(139.0)
    assert mv.atr_pct > 0.0


@settings(max_examples=50)
@given(
    n_real=hyp.integers(min_value=5, max_value=40),
    n_synth=hyp.integers(min_value=0, max_value=40),
)
def test_filter_keeps_exactly_real_count(n_real: int, n_synth: int) -> None:
    bars: list[Bar] = []
    for i in range(n_real):
        bars.append(_real_bar(ts=i, close=100.0 + i))
    for j in range(n_synth):
        if j % 2 == 0:
            bars.append(_flat_bar(ts=1_000 + j, price=100.0))
        else:
            bars.append(_zero_vol_bar(ts=1_000 + j, close=100.0 + j))
    kept = filter_real_bars(bars)
    assert len(kept) == n_real
    assert all(not is_synthetic_bar(b) for b in kept)


# ---------------------------------------------------------------------------
# score.py regime-alignment id coverage
# ---------------------------------------------------------------------------

# The 4 ids the agenda found silently excluded (verified vs STRATEGY_REGISTRY).
_NEWLY_COVERED_TREND = ("equity_tsmom", "equity_gap_go")
_NEWLY_COVERED_COUNTER = ("equity_rsi_bb_pullback", "fx_range_fade")


def test_every_registry_id_has_an_alignment_classification() -> None:
    """No live strategy may be missing from BOTH alignment frozensets — every
    registered id must be scored as trend OR counter-trend (or be deliberately
    neutral). The 4 previously-excluded ids are now present."""
    classified = _TREND_STRATEGIES | _COUNTER_TREND_STRATEGIES
    for sid in (*_NEWLY_COVERED_TREND, *_NEWLY_COVERED_COUNTER):
        assert sid in classified, f"{sid} missing from alignment frozensets"


def test_new_trend_ids_in_trend_frozenset() -> None:
    for sid in _NEWLY_COVERED_TREND:
        assert sid in _TREND_STRATEGIES


def test_new_counter_ids_in_counter_frozenset() -> None:
    for sid in _NEWLY_COVERED_COUNTER:
        assert sid in _COUNTER_TREND_STRATEGIES


def test_alignment_ids_are_real_registry_ids() -> None:
    """Guard against typos: every id in the frozensets must be a live registry
    id (so a misspelled id can't silently never-match)."""
    live = set(STRATEGY_REGISTRY.keys())
    for sid in _TREND_STRATEGIES | _COUNTER_TREND_STRATEGIES:
        assert sid in live, f"{sid} not in STRATEGY_REGISTRY"


def test_new_ids_get_nonneutral_alignment_in_matching_regime() -> None:
    """The whole point: the 4 ids now actually move the score in the regime
    that matches their edge (amplify), and dampen in the opposing regime —
    never neutral 1.0 anymore."""
    for sid in _NEWLY_COVERED_TREND:
        assert regime_alignment_mult(strategy=sid, regime="bull_trend") == REGIME_ALIGN_AMPLIFY
        assert regime_alignment_mult(strategy=sid, regime="chop") == REGIME_ALIGN_DAMPEN
    for sid in _NEWLY_COVERED_COUNTER:
        assert regime_alignment_mult(strategy=sid, regime="chop") == REGIME_ALIGN_AMPLIFY
        assert regime_alignment_mult(strategy=sid, regime="bull_trend") == REGIME_ALIGN_DAMPEN
