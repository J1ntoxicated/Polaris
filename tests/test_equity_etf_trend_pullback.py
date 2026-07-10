"""equity_etf_trend_pullback — Alpaca 1D MACD re-acceleration inside a
200-EMA uptrend. Per-venue clone of macd_ema_trend_pullback, fixed
SPY/QQQ/GLD universe.

DEMO/PAPER virtual sleeve. Covers the SUPPORTED_SYMBOLS gate, the bullish-
cross / EMA-regime / volume-confirm entry conditions, the tsmom_12_1 shadow
comparator (present/absent by history length), warmup, and
degrade-never-crash on empty/short bar lists.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY, EquityEtfTrendPullbackStrategy
from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.equity_etf_trend_pullback import SUPPORTED_SYMBOLS

_DAY = 86_400


def _bars_from_closes(closes: list[float], *, last_volume: float = 2_000_000.0,
                       volume: float = 1_000_000.0) -> list[BarView]:
    base = 1_700_000_000
    out: list[BarView] = []
    for i, c in enumerate(closes):
        v = last_volume if i == len(closes) - 1 else volume
        out.append(BarView(ts=base + i * _DAY, open=c, high=c + 0.1, low=c - 0.1,
                            close=c, volume=v))
    return out


def _mv(bars: list[BarView], symbol: str = "SPY") -> MarketView:
    return MarketView(
        symbol=symbol, venue="alpaca", timeframe="1D",
        bars=bars, last_price=bars[-1].close if bars else 0.0, spread_bps=1.0,
    )


def _pullback_closes(n_up: int, *, up_step: float = 0.1, dip_len: int = 30,
                      dip_step: float = 0.2, rebound: float = 3.0) -> list[float]:
    """A long uptrend, a shallow multi-bar pullback (drives MACD <=0), then a
    single re-acceleration bar that crosses MACD back above its signal line
    while still <=0 — the canonical setup this strategy targets."""
    closes = [100.0 + up_step * i for i in range(n_up)]
    last = closes[-1]
    for i in range(1, dip_len + 1):
        closes.append(last - dip_step * i)
    closes.append(closes[-1] + rebound)
    return closes


def _pure_uptrend_closes(n: int = 251, *, start: float = 100.0,
                          step: float = 0.1) -> list[float]:
    return [start + step * i for i in range(n)]


def _downtrend_closes(n: int = 251, *, start: float = 200.0,
                       step: float = 0.3) -> list[float]:
    return [start - step * i for i in range(n)]


# ---------------------------------------------------------------------------
# A — fires on the canonical pullback re-acceleration
# ---------------------------------------------------------------------------


def test_fires_on_macd_reacceleration_inside_uptrend() -> None:
    bars = _bars_from_closes(_pullback_closes(220))  # n=251, no tsmom shadow yet
    sig = EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "SPY"))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "equity_etf_trend_pullback"
    assert 0.0 < sig.strength <= 1.0
    assert float(sig.tags["macd_line"]) <= 0.0
    assert "tsmom_12_1" not in sig.tags


def test_fires_and_stamps_tsmom_12_1_shadow_when_history_sufficient() -> None:
    bars = _bars_from_closes(_pullback_closes(223))  # n=254 -> >= 253 lookback
    sig = EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "QQQ"))
    assert sig is not None
    assert "tsmom_12_1" in sig.tags
    assert sig.tags["tsmom_12_1_pos"] in {"0", "1"}


# ---------------------------------------------------------------------------
# B — SUPPORTED_SYMBOLS gate (fixed ETF sleeve, not the §0d liquidity gate)
# ---------------------------------------------------------------------------


def test_supported_symbols_is_the_fixed_etf_sleeve() -> None:
    assert frozenset({"SPY", "QQQ", "GLD"}) == SUPPORTED_SYMBOLS


def test_no_signal_on_unsupported_symbol() -> None:
    bars = _bars_from_closes(_pullback_closes(220))
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "AAPL")) is None


# ---------------------------------------------------------------------------
# C — entry condition gates
# ---------------------------------------------------------------------------


def test_no_signal_without_a_fresh_bullish_cross() -> None:
    # A pure steady uptrend never produces a FRESH cross (MACD/signal have
    # long since converged and stay in lockstep) — no re-acceleration event.
    bars = _bars_from_closes(_pure_uptrend_closes())
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "SPY")) is None


def test_no_signal_when_below_ema_filter() -> None:
    bars = _bars_from_closes(_downtrend_closes())
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "GLD")) is None


def test_no_signal_when_volume_does_not_confirm() -> None:
    bars = _bars_from_closes(_pullback_closes(220), last_volume=100.0)
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "SPY")) is None


# ---------------------------------------------------------------------------
# D — warmup
# ---------------------------------------------------------------------------


def test_no_signal_before_warmup() -> None:
    bars = _bars_from_closes(_pullback_closes(190, dip_len=30))  # n < 235
    assert len(bars) < 235
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "SPY")) is None


# ---------------------------------------------------------------------------
# E — degrade-never-crash
# ---------------------------------------------------------------------------


def test_degrade_never_crash_on_empty_bars() -> None:
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv([], "SPY")) is None


def test_degrade_never_crash_on_short_bars() -> None:
    bars = _bars_from_closes(_pullback_closes(220))[:5]
    assert EquityEtfTrendPullbackStrategy().generate_raw_signal(_mv(bars, "SPY")) is None


# ---------------------------------------------------------------------------
# F — metadata (spec §1 table row #3, verbatim)
# ---------------------------------------------------------------------------


def test_metadata_matches_spec_table() -> None:
    md = EquityEtfTrendPullbackStrategy.metadata
    assert md.strategy_id == "equity_etf_trend_pullback"
    assert md.timeframe == "1D"
    assert md.warmup_bars == 235
    assert md.max_positions == 3
    assert md.gross_cap == 0.15
    assert md.per_symbol_cap == 0.06
    assert md.expected_holding_bars == 15
    assert md.asset_class == "equity"
    assert md.venue == "alpaca"
    assert md.product_class == "equity"
    assert md.correlation_group_id == "equity_etf_trend_continuation"
    assert md.hold_overnight is True
    assert md.profit_target_r is None


def test_dispatch_eligible_is_virtual_only() -> None:
    md = EquityEtfTrendPullbackStrategy.metadata
    assert md.dispatch_eligible == virtual_loosen(True, False)
    assert md.dispatch_eligible == virtual_mode_enabled()


def test_registered_in_registry() -> None:
    assert (
        STRATEGY_REGISTRY["equity_etf_trend_pullback"]
        is EquityEtfTrendPullbackStrategy
    )
