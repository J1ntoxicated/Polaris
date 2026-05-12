"""Capital CFD vol/depth proxy tests.

Spec source: vault/30_components/layer-0-universe-discovery.md (Q2 hard filter — CFD proxy).
"""

from __future__ import annotations

import pytest

from polaris.core.universe.discovery import apply_active_filters
from polaris.core.universe.schema import UniverseInstrument, default_thresholds
from polaris.venues.capital.market_proxy import (
    CAPITAL_DEPTH_PROXY_FLOOR_USD,
    CAPITAL_VOL_PROXY_FLOOR_USD,
    CapitalProxyConfig,
    compute_depth_proxy,
    compute_vol_24h_proxy,
    passes_proxy_4_axis,
)

NOW = 1_780_000_000


def _capital_inst(
    *,
    symbol: str = "EURUSD",
    asset_class: str = "forex",
    spread_bps: float = 1.5,
    atr_pct: float = 2.5,
    vol: float = 0.0,
    depth: float = 0.0,
    state: str = "live",
) -> UniverseInstrument:
    return UniverseInstrument(
        venue="capital",
        symbol=symbol,
        instrument_id=f"capital:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy="USD",
        state=state,
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )


# ---------------------------------------------------------------------------
# Vol-proxy compute
# ---------------------------------------------------------------------------


def test_vol_24h_proxy_compute() -> None:
    candles = [
        {"closePrice": {"bid": 1.10, "ask": 1.10}, "lastTradedVolume": 1_000_000},
        {"closePrice": {"bid": 1.11, "ask": 1.11}, "lastTradedVolume": 2_000_000},
    ]
    out = compute_vol_24h_proxy(candles)
    assert out == pytest.approx(1.10 * 1_000_000 + 1.11 * 2_000_000)


def test_vol_24h_proxy_handles_flat_close_field() -> None:
    candles = [{"close": 1.10, "lastTradedVolume": 500_000}]
    assert compute_vol_24h_proxy(candles) == pytest.approx(550_000.0)


def test_vol_24h_proxy_skips_invalid_rows() -> None:
    candles = [
        {"closePrice": {"bid": 0, "ask": 0}, "lastTradedVolume": 100},
        {"closePrice": {"bid": 1.0, "ask": 1.0}, "lastTradedVolume": 0},
        {"close": 1.0, "lastTradedVolume": "garbage"},
        {"closePrice": {"bid": 1.0, "ask": 1.0}, "lastTradedVolume": 100},
    ]
    assert compute_vol_24h_proxy(candles) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Depth-proxy compute
# ---------------------------------------------------------------------------


def test_depth_proxy_spread_weighted_decreases_with_wider_spread() -> None:
    tight = compute_depth_proxy(spread_bps=0.5, atr_24h_pct=1.0, asset_class="forex")
    wide = compute_depth_proxy(spread_bps=5.0, atr_24h_pct=1.0, asset_class="forex")
    assert tight > wide
    assert tight == pytest.approx((1 / 0.5) * 1.0 * 10.0 * 10_000.0)


def test_depth_proxy_zero_inputs_returns_zero() -> None:
    assert compute_depth_proxy(spread_bps=0.0, atr_24h_pct=1.0, asset_class="forex") == 0.0
    assert compute_depth_proxy(spread_bps=1.0, atr_24h_pct=0.0, asset_class="forex") == 0.0


def test_depth_proxy_asset_class_pip_lookup() -> None:
    indices = compute_depth_proxy(spread_bps=1.0, atr_24h_pct=1.0, asset_class="indices")
    forex = compute_depth_proxy(spread_bps=1.0, atr_24h_pct=1.0, asset_class="forex")
    # indices pip notional 25 vs forex 10 ⇒ indices > forex.
    assert indices > forex


# ---------------------------------------------------------------------------
# Proxy 4-axis OR-gate
# ---------------------------------------------------------------------------


def test_passes_proxy_4_axis_either_clears() -> None:
    assert passes_proxy_4_axis(
        vol_proxy_usd=CAPITAL_VOL_PROXY_FLOOR_USD,
        depth_proxy_usd=0.0,
    )
    assert passes_proxy_4_axis(
        vol_proxy_usd=0.0,
        depth_proxy_usd=CAPITAL_DEPTH_PROXY_FLOOR_USD,
    )
    assert not passes_proxy_4_axis(
        vol_proxy_usd=CAPITAL_VOL_PROXY_FLOOR_USD - 1.0,
        depth_proxy_usd=CAPITAL_DEPTH_PROXY_FLOOR_USD - 1.0,
    )


def test_passes_proxy_4_axis_custom_config() -> None:
    cfg = CapitalProxyConfig(vol_floor_usd=1000.0, depth_floor_usd=100.0)
    assert passes_proxy_4_axis(vol_proxy_usd=1000.0, depth_proxy_usd=0.0, config=cfg)
    assert not passes_proxy_4_axis(vol_proxy_usd=999.0, depth_proxy_usd=99.0, config=cfg)


# ---------------------------------------------------------------------------
# 4-axis filter integration (Capital OR gate)
# ---------------------------------------------------------------------------


def test_4_axis_filter_pass_with_vol_proxy_only() -> None:
    """Capital row with strong vol proxy + zero depth still passes (OR-gate)."""
    inst = _capital_inst(vol=5e8, depth=0.0)
    out = apply_active_filters([inst])
    assert out == [inst]


def test_4_axis_filter_pass_with_depth_proxy_only() -> None:
    """Capital row with strong depth proxy + zero vol still passes (OR-gate)."""
    inst = _capital_inst(vol=0.0, depth=600_000.0)
    out = apply_active_filters([inst])
    assert out == [inst]


def test_4_axis_filter_capital_zero_zero_rejected() -> None:
    """Zero vol AND zero depth must still fail — OR-gate doesn't whitelist garbage."""
    inst = _capital_inst(vol=0.0, depth=0.0)
    th = default_thresholds()
    out = apply_active_filters([inst], thresholds=th)
    assert out == []


def test_4_axis_filter_strict_mode_for_capital() -> None:
    """capital_proxy_or_gate=False → OKX-style strict gate even for capital row."""
    inst = _capital_inst(vol=5e8, depth=0.0)  # depth fails
    out = apply_active_filters([inst], capital_proxy_or_gate=False)
    assert out == []


def test_4_axis_filter_okx_unaffected_by_proxy_flag() -> None:
    """OKX rows always need ALL axes (proxy flag is venue-scoped to capital)."""
    okx = UniverseInstrument(
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=200_000.0,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )
    assert apply_active_filters([okx]) == [okx]
    # Strip depth → must fail (OR-gate is capital-only).
    weak = UniverseInstrument(
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="crypto:BTC",
        asset_class="crypto",
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=10.0,
        signal_density_7d=0.0,
        listing_ts=None,
        last_seen_ts=NOW,
    )
    assert apply_active_filters([weak]) == []
