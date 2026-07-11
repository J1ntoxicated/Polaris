"""Alt-data → STRATEGY-VISIBLE MarketView fields (TDD).

DEMO/PAPER. This wave makes the AltDataCache numerics (funding / open-interest /
COT net-spec percentile / VIX / crypto fear-greed / HY spread) reachable from a
strategy's ``MarketView`` — the substrate new-edge discovery + the entry probe
ensemble need. Strictly ADDITIVE + AGGRESSIVE/flow_not_block:

  * New MarketView fields default ``None`` (NEUTRAL no-op) — a stale / absent /
    keyless cache reads as neutral, never a stale value driving a decision.
  * Purely SIGNAL fields: no block / throttle / size-cut, no in-loop LLM, no
    sizing-chain change.
  * Every EXISTING strategy that does NOT read the new fields is byte-identical
    with vs without the fields populated (additive proof).
  * The builder NEVER raises on a missing / stale / keyless cache (fail-soft).

No live network: the cache is stubbed / hand-populated.
"""

from __future__ import annotations

from typing import Any

from polaris.scripts._production_indicators import (
    build_real_market_view,
    map_altdata_to_market_fields,
)
from polaris.strategies import (
    BarView,
    MarketView,
    RSIBBPullbackStrategy,
)
from polaris.strategies.base import AltDataView

# spot_donchian un-registered 2026-06-27 (#56 stop-bleeders KILL) — module
# preserved read-only; this altdata market-view test exercises it directly.
from polaris.strategies.spot_donchian import SpotDonchianStrategy

# ---------------------------------------------------------------------------
# Stub cache — mirrors AltDataCache.get_for_group contract
# ---------------------------------------------------------------------------


class _StubCache:
    """Minimal cache exposing ``get_for_group`` like ``AltDataCache``."""

    def __init__(self, sources: dict[str, dict[str, Any]] | None) -> None:
        self._sources = sources

    def get_for_group(
        self, underlying_group_id: str, *, now_ts: float | None = None
    ) -> dict[str, dict[str, Any]]:
        # ``None`` models a cache that returns nothing fresh (stale/keyless).
        return dict(self._sources) if self._sources else {}


class _RaisingCache:
    """A cache whose ``get_for_group`` blows up — builder must fail soft."""

    def get_for_group(
        self, underlying_group_id: str, *, now_ts: float | None = None
    ) -> dict[str, dict[str, Any]]:
        raise RuntimeError("cache exploded")


def _make_bars(n: int, *, base: float = 100.0, drift: float = 0.0) -> list[BarView]:
    out: list[BarView] = []
    for i in range(n):
        c = base + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * 60,
                open=c - 0.5,
                high=c + 0.5,
                low=c - 1.0,
                close=c,
                volume=1000.0,
                notional_usd=1000.0 * c,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 1. AltDataView default = NEUTRAL no-op (all None)
# ---------------------------------------------------------------------------


def test_altdataview_default_all_neutral() -> None:
    av = AltDataView()
    assert av.funding_rate is None
    assert av.open_interest is None
    assert av.oi_change_24h is None
    assert av.cot_net_spec_pctile is None
    assert av.vix is None
    assert av.crypto_fear_greed is None
    assert av.hy_spread is None
    assert av.dfii10 is None
    assert av.is_neutral() is True


def test_marketview_default_altdata_is_neutral() -> None:
    """A MarketView built with no alt-data carries a NEUTRAL AltDataView."""
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=_make_bars(5), last_price=100.0, spread_bps=2.0,
    )
    assert isinstance(mv.altdata, AltDataView)
    assert mv.altdata.is_neutral() is True


# ---------------------------------------------------------------------------
# 2. map_altdata_to_market_fields — crypto sources → numeric fields
# ---------------------------------------------------------------------------


def test_map_crypto_sources_populates_fields() -> None:
    cache = _StubCache({
        "crypto_fg": {"value": 72, "label": "Greed"},
        "okx_funding": {
            "BTC-USDT-SWAP": {"fundingRate": 0.0012, "oi": 50000.0},
            "ETH-USDT-SWAP": {"fundingRate": 0.0008, "oi": 30000.0},
        },
    })
    av = map_altdata_to_market_fields("crypto:BTC", cache, now_ts=1000.0)
    # funding = mean of the per-instrument rates.
    assert av.funding_rate is not None
    assert abs(av.funding_rate - 0.0010) < 1e-9
    # open_interest = sum of the per-instrument oi.
    assert av.open_interest == 80000.0
    assert av.crypto_fear_greed == 72.0
    # macro/COT not routed to crypto → neutral.
    assert av.vix is None
    assert av.hy_spread is None
    assert av.cot_net_spec_pctile is None
    assert av.is_neutral() is False


def test_map_macro_sources_populates_vix_hy() -> None:
    cache = _StubCache({"fred_macro": {"vix": 18.5, "hy_spread": 320.0}})
    av = map_altdata_to_market_fields("index:US500", cache, now_ts=1000.0)
    assert av.vix == 18.5
    assert av.hy_spread == 320.0
    # crypto-only fields neutral.
    assert av.funding_rate is None
    assert av.crypto_fear_greed is None


def test_map_macro_sources_populates_dfii10() -> None:
    """Frontgate item #5 — DFII10 gold conviction context, same additive
    None-default contract as vix/hy_spread."""
    cache = _StubCache({"fred_macro": {"dfii10": 2.31}})
    av = map_altdata_to_market_fields("commodity:XAU", cache, now_ts=1000.0)
    assert av.dfii10 == 2.31
    assert av.is_neutral() is False


def test_map_absent_dfii10_is_neutral() -> None:
    cache = _StubCache({"fred_macro": {"vix": 16.0}})
    av = map_altdata_to_market_fields("commodity:XAU", cache, now_ts=1000.0)
    assert av.dfii10 is None


def test_map_commodity_cot_populates_pctile() -> None:
    cache = _StubCache({
        "fred_macro": {"vix": 16.0},
        "cftc_cot": {"XAU": {"net_spec_pctile": 0.88, "net_spec_pct": 0.21}},
    })
    av = map_altdata_to_market_fields("commodity:XAU", cache, now_ts=1000.0)
    assert av.cot_net_spec_pctile == 0.88
    assert av.vix == 16.0


def test_map_cot_absent_symbol_is_neutral() -> None:
    """COT payload present but THIS symbol absent → pctile neutral (no-op)."""
    cache = _StubCache({"cftc_cot": {"CL": {"net_spec_pctile": 0.9}}})
    av = map_altdata_to_market_fields("commodity:XAU", cache, now_ts=1000.0)
    assert av.cot_net_spec_pctile is None


# ---------------------------------------------------------------------------
# 3. Stale / absent / keyless cache → NEUTRAL (no-op, never an error)
# ---------------------------------------------------------------------------


def test_map_empty_cache_is_neutral() -> None:
    av = map_altdata_to_market_fields("crypto:BTC", _StubCache({}), now_ts=1000.0)
    assert av.is_neutral() is True


def test_map_none_cache_is_neutral() -> None:
    """A None cache (alt-data disabled) → neutral, no raise."""
    av = map_altdata_to_market_fields("crypto:BTC", None, now_ts=1000.0)
    assert av.is_neutral() is True


def test_map_keyless_source_payload_is_neutral() -> None:
    """Keyless source (Coinglass/MyFxBook absent) yields {} payload → neutral."""
    cache = _StubCache({"coinglass": {}})  # present source, empty payload
    av = map_altdata_to_market_fields("crypto:BTC", cache, now_ts=1000.0)
    assert av.is_neutral() is True


def test_map_raising_cache_fails_soft() -> None:
    """A cache that raises must NOT propagate — neutral fallback (fail-soft)."""
    av = map_altdata_to_market_fields("crypto:BTC", _RaisingCache(), now_ts=1000.0)
    assert av.is_neutral() is True


# ---------------------------------------------------------------------------
# 4. build_real_market_view wiring — fresh data carried; stale → neutral
# ---------------------------------------------------------------------------


def test_builder_carries_altdata_when_fresh() -> None:
    cache = _StubCache({
        "crypto_fg": {"value": 30},
        "okx_funding": {"BTC-USDT-SWAP": {"fundingRate": -0.0009, "oi": 12000.0}},
    })
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=_make_bars(60),
        asset_class="crypto", altdata_cache=cache,
        underlying_group_id="crypto:BTC", now_ts=1000.0,
    )
    assert mv.altdata.crypto_fear_greed == 30.0
    assert mv.altdata.funding_rate is not None
    assert abs(mv.altdata.funding_rate - (-0.0009)) < 1e-9
    assert mv.altdata.open_interest == 12000.0


def test_builder_neutral_when_no_cache() -> None:
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=_make_bars(60),
        asset_class="crypto",
    )
    assert mv.altdata.is_neutral() is True


def test_builder_neutral_when_cache_stale() -> None:
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=_make_bars(60),
        asset_class="crypto", altdata_cache=_StubCache({}),
        underlying_group_id="crypto:BTC", now_ts=1000.0,
    )
    assert mv.altdata.is_neutral() is True


def test_builder_never_raises_on_raising_cache() -> None:
    """Fail-soft: a raising cache does not crash the builder."""
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=_make_bars(60),
        asset_class="crypto", altdata_cache=_RaisingCache(),
        underlying_group_id="crypto:BTC", now_ts=1000.0,
    )
    assert mv.altdata.is_neutral() is True


def test_builder_empty_bars_still_carries_altdata() -> None:
    """Even the no-real-bars early return path must not crash with a cache."""
    mv = build_real_market_view(
        venue="okx", symbol="BTC-USDT", timeframe="1m", bars=[],
        asset_class="crypto", altdata_cache=_StubCache({"crypto_fg": {"value": 55}}),
        underlying_group_id="crypto:BTC", now_ts=1000.0,
    )
    # Empty-bar early return → neutral altdata default (no crash).
    assert isinstance(mv.altdata, AltDataView)


# ---------------------------------------------------------------------------
# 5. ADDITIVE PROOF — existing strategy output byte-identical w/ vs w/o alt-data
# ---------------------------------------------------------------------------


def _populated_altdata() -> AltDataView:
    return AltDataView(
        funding_rate=-0.0009, open_interest=12000.0, oi_change_24h=None,
        cot_net_spec_pctile=0.9, vix=42.0, crypto_fear_greed=10.0,
        hy_spread=650.0,
    )


def test_donchian_byte_identical_with_and_without_altdata() -> None:
    """SpotDonchian signal must be byte-identical regardless of alt-data fields."""
    bars = _make_bars(60, base=100.0, drift=0.6)  # uptrend → donchian breakout
    base_kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT", venue="okx", timeframe="1m", bars=bars,
        last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01,
        donchian_high_40=bars[-1].close - 0.1, donchian_low_40=50.0,
        donchian_high_30=bars[-1].close - 0.1, donchian_low_30=50.0,
        adx_14=30.0,
    )
    mv_plain = MarketView(**base_kwargs)
    mv_alt = MarketView(**base_kwargs, altdata=_populated_altdata())
    s = SpotDonchianStrategy()
    sig_plain = s.generate_raw_signal(mv_plain)
    sig_alt = s.generate_raw_signal(mv_alt)
    assert _sig_tuple(sig_plain) == _sig_tuple(sig_alt)


def test_rsi_bb_byte_identical_with_and_without_altdata() -> None:
    """RSIBBPullback signal byte-identical with vs without alt-data populated."""
    bars = _make_bars(60, base=100.0, drift=-0.4)  # downtrend → oversold fade
    base_kwargs: dict[str, Any] = dict(
        symbol="BTC-USDT", venue="okx", timeframe="1m", bars=bars,
        last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01,
        rsi_14=18.0, bb_lower=bars[-1].close + 5.0, bb_upper=200.0,
        bb_middle=bars[-1].close + 10.0,
    )
    mv_plain = MarketView(**base_kwargs)
    mv_alt = MarketView(**base_kwargs, altdata=_populated_altdata())
    s = RSIBBPullbackStrategy()
    assert _sig_tuple(s.generate_raw_signal(mv_plain)) == _sig_tuple(
        s.generate_raw_signal(mv_alt)
    )


def _sig_tuple(sig: Any) -> Any:
    """Stable comparable projection of a RawSignal (signal_id is random)."""
    if sig is None:
        return None
    return (
        sig.strategy_id, sig.symbol, sig.side, sig.strength,
        sig.sizing_hint, sig.ttl_bars, sig.thesis_tag, sig.correlation_group,
    )
