"""Tests for polaris.core.altdata.cache + fuser (#6 alt-data EVIDENCE).

DEMO/PAPER only — virtual funds. ``fuse_evidence`` returns a SUGGESTION
(regime_hint) + raw evidence. It NEVER returns an action: no size, no block,
no early-exit, no halt. A None hint means "no override" — the price-only
``compute_real_regime`` result stands (correct fallback, NOT a throttle).
"""

from __future__ import annotations

from polaris.core.altdata.cache import AltDataCache
from polaris.core.altdata.fuser import fuse_evidence

# ── Cache TTL + group routing ─────────────────────────────────────────────────


def test_cache_get_honors_ttl() -> None:
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 12}, ttl_sec=1000, now_ts=100.0)
    assert cache.get("crypto_fg", now_ts=900.0) == {"value": 12}  # within TTL
    assert cache.get("crypto_fg", now_ts=2000.0) is None  # expired
    assert cache.get("never_set") is None


def test_cache_group_routing_crypto() -> None:
    cache = AltDataCache()
    cache.set("okx_funding", {"BTC-USDT-SWAP": {"fundingRate": 0.0}}, ttl_sec=1000, now_ts=0.0)
    cache.set("crypto_fg", {"value": 50}, ttl_sec=1000, now_ts=0.0)
    cache.set("fred_macro", {"vix": 18.0}, ttl_sec=1000, now_ts=0.0)
    sources = cache.get_for_group("crypto:BTC", now_ts=1.0)
    assert "okx_funding" in sources
    assert "crypto_fg" in sources
    assert "fred_macro" not in sources  # crypto group does NOT pull macro


def test_cache_group_routing_macro() -> None:
    cache = AltDataCache()
    cache.set("fred_macro", {"vix": 18.0}, ttl_sec=1000, now_ts=0.0)
    cache.set("crypto_fg", {"value": 50}, ttl_sec=1000, now_ts=0.0)
    for gid in ("forex:EURUSD", "index:US500", "commodity:XAUUSD"):
        sources = cache.get_for_group(gid, now_ts=1.0)
        assert "fred_macro" in sources
        assert "crypto_fg" not in sources
        assert "okx_funding" not in sources


def test_cache_group_routing_drops_expired() -> None:
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 50}, ttl_sec=10, now_ts=0.0)
    sources = cache.get_for_group("crypto:BTC", now_ts=999.0)
    assert "crypto_fg" not in sources  # expired entries excluded


def test_cache_group_routing_equity_pulls_macro() -> None:
    """#6+ equity: an equity:* group pulls the FRED macro source (macro-
    sensitive), NOT the crypto sources. Reuses the already-collected
    fred_macro payload — no new collector."""
    cache = AltDataCache()
    cache.set("fred_macro", {"vix": 18.0, "hy_spread": 280.0}, ttl_sec=1000, now_ts=0.0)
    cache.set("crypto_fg", {"value": 50}, ttl_sec=1000, now_ts=0.0)
    cache.set("okx_funding", {"AAPL": {"fundingRate": 0.0}}, ttl_sec=1000, now_ts=0.0)
    for gid in ("equity:AAPL", "equity:SPY", "equity:MSFT"):
        sources = cache.get_for_group(gid, now_ts=1.0)
        assert "fred_macro" in sources  # equity is macro-sensitive
        assert "crypto_fg" not in sources
        assert "okx_funding" not in sources


# ── Fuser: empty / neutral → no override ──────────────────────────────────────


def test_fuse_empty_cache_returns_none_hint() -> None:
    cache = AltDataCache()
    hint, conf, evidence = fuse_evidence("crypto:BTC", cache)
    assert hint is None
    assert conf == 0.0
    assert evidence == {}


def test_fuse_neutral_returns_none_hint_no_override() -> None:
    """Neutral readings must NOT cross the conviction floor → no override."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 50, "label": "Neutral"}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"BTC-USDT-SWAP": {"fundingRate": 0.0001, "oi": 1.0, "oi_change_24h": 0.0}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    assert hint is None  # below conviction floor → no suggestion
    assert evidence  # raw values still surfaced for read-only context


# ── Fuser: extreme fear → bear/crisis suggestion ──────────────────────────────


def test_fuse_extreme_fear_suggests_bear_or_crisis() -> None:
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 8, "label": "Extreme Fear"}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"BTC-USDT-SWAP": {"fundingRate": -0.0025, "oi": 1.0, "oi_change_24h": 0.0}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    assert hint in ("bear_trend", "crisis")
    assert 0.3 <= conf <= 1.0
    assert evidence["crypto_fg"] == 8


def test_fuse_macro_crisis_vix_suggests_crisis() -> None:
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 45.0, "hy_spread": 620.0, "move": 150.0, "yield_curve": -0.5},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("forex:EURUSD", cache, now_ts=1.0)
    assert hint == "crisis"
    assert evidence["vix"] == 45.0


def test_fuse_calm_macro_suggests_bull() -> None:
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 12.0, "hy_spread": 250.0, "move": 60.0, "yield_curve": 1.0},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("index:US500", cache, now_ts=1.0)
    assert hint == "bull_trend"


# ── Equity macro branch: same conservative discipline as FX macro ─────────────


def test_fuse_equity_macro_crisis_vix_suggests_crisis() -> None:
    """Equity is macro-sensitive: high VIX/HY → crisis suggestion (evidence)."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 45.0, "hy_spread": 620.0, "move": 150.0, "yield_curve": -0.5},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("equity:AAPL", cache, now_ts=1.0)
    assert hint == "crisis"
    assert evidence["vix"] == 45.0
    assert 0.3 <= conf <= 1.0


def test_fuse_equity_calm_macro_suggests_bull() -> None:
    """Risk-on macro (low VIX + low HY) → bull suggestion for equity."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 12.0, "hy_spread": 250.0, "move": 60.0, "yield_curve": 1.0},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, _, _ = fuse_evidence("equity:SPY", cache, now_ts=1.0)
    assert hint == "bull_trend"


def test_fuse_equity_neutral_macro_no_override() -> None:
    """Neutral macro (VIX between bands) → None hint; price-only equity stands."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 18.0, "hy_spread": 350.0, "move": 80.0, "yield_curve": 0.5},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, _, evidence = fuse_evidence("equity:MSFT", cache, now_ts=1.0)
    assert hint is None  # neither crisis/bear nor (low_vix & low_hy) → no override
    assert evidence["vix"] == 18.0  # raw evidence still surfaced


def test_fuse_equity_conviction_floor_respected() -> None:
    """One mild bear signal (VIX 26 → +1.0) stays below the 1.5 floor."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 26.0, "hy_spread": 350.0, "move": 80.0, "yield_curve": 0.5},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, _, evidence = fuse_evidence("equity:AAPL", cache, now_ts=1.0)
    assert hint is None  # 1.0 < 1.5 conviction floor
    assert evidence["vix"] == 26.0


def test_fuse_equity_empty_cache_no_override() -> None:
    """No fresh FRED → {} → price-only equity regime stands (fallback)."""
    cache = AltDataCache()
    hint, conf, evidence = fuse_evidence("equity:AAPL", cache, now_ts=1.0)
    assert hint is None
    assert conf == 0.0
    assert evidence == {}


def test_fuse_equity_ignores_crypto_sources() -> None:
    """An equity group must NOT pick up crypto F&G / funding (routing isolation)."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 8}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding", {"BTC-USDT-SWAP": {"fundingRate": -0.005}}, ttl_sec=9999, now_ts=0.0
    )
    hint, _, evidence = fuse_evidence("equity:AAPL", cache, now_ts=1.0)
    # No fred_macro set and crypto sources are not routed → no evidence at all.
    assert hint is None
    assert evidence == {}


def test_fuse_strong_bull_funding() -> None:
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 82, "label": "Extreme Greed"}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"ETH-USDT-SWAP": {"fundingRate": 0.0045, "oi": 1.0, "oi_change_24h": 0.0}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("crypto:ETH", cache, now_ts=1.0)
    assert hint == "bull_trend"


# ── Conviction floor: a single weak signal must not cross 1.5 ──────────────────


def test_fuse_conviction_floor_respected() -> None:
    """One mild signal (VIX 26 → bear +1.0 only) stays below the 1.5 floor."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 26.0, "hy_spread": 350.0, "move": 80.0, "yield_curve": 0.5},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("forex:GBPUSD", cache, now_ts=1.0)
    assert hint is None  # 1.0 < 1.5 conviction floor
    assert evidence["vix"] == 26.0


def test_fuse_label_in_polaris_four() -> None:
    """Any non-None hint must be one of Polaris' 4 canonical labels."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 5, "label": "Extreme Fear"}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"BTC-USDT-SWAP": {"fundingRate": -0.005, "oi": 1.0, "oi_change_24h": 0.0}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, _, _ = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    assert hint in ("bull_trend", "bear_trend", "chop", "crisis")
