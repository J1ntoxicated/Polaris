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


# ── P1: asset-class differentiated source weighting (0.75-1.25) ────────────────


def test_fuse_evidence_records_scores_weights_asset_class() -> None:
    """Evidence dict surfaces the structured synthesis context for G3/G7:
    label + scores + source_weights + asset_class (contract still 3-tuple)."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 8, "label": "Extreme Fear"}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"BTC-USDT-SWAP": {"fundingRate": -0.0025}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, conf, evidence = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    assert hint in ("bear_trend", "crisis")
    # structured synthesis context recorded
    assert evidence["asset_class"] == "crypto"
    assert evidence["label"] == hint
    assert "scores" in evidence and isinstance(evidence["scores"], dict)
    assert set(evidence["scores"]) == {"bull_trend", "bear_trend", "chop", "crisis"}
    assert "source_weights" in evidence and isinstance(evidence["source_weights"], dict)
    # crypto source-type multipliers within the 0.75-1.25 band
    for w in evidence["source_weights"].values():
        assert 0.75 <= w <= 1.25


def test_fuse_crypto_funding_weight_amplified() -> None:
    """Crypto branch amplifies funding/F&G source type (multiplier > 1.0,
    capped at 1.25). Raw evidence values are still the unscaled inputs."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 8}, ttl_sec=9999, now_ts=0.0)
    cache.set(
        "okx_funding",
        {"BTC-USDT-SWAP": {"fundingRate": -0.0025}},
        ttl_sec=9999,
        now_ts=0.0,
    )
    _, _, evidence = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    weights = evidence["source_weights"]
    # crypto-native sources are emphasised
    assert weights["crypto_fg"] > 1.0
    assert weights["okx_funding"] > 1.0
    assert weights["crypto_fg"] <= 1.25
    assert weights["okx_funding"] <= 1.25
    # raw evidence still the unscaled funding/F&G readings
    assert evidence["crypto_fg"] == 8


def test_fuse_macro_weight_amplified_for_fx_commodity() -> None:
    """FX / commodity branch amplifies the macro source type (>1.0, <=1.25)."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 45.0, "hy_spread": 620.0},
        ttl_sec=9999,
        now_ts=0.0,
    )
    for gid, ac in (("forex:EURUSD", "forex"), ("commodity:XAUUSD", "commodity")):
        _, _, evidence = fuse_evidence(gid, cache, now_ts=1.0)
        assert evidence["asset_class"] == ac
        assert evidence["source_weights"]["fred_macro"] > 1.0
        assert evidence["source_weights"]["fred_macro"] <= 1.25


def test_fuse_weight_routing_isolation_crypto_vs_macro() -> None:
    """Routing isolation stays fixed: a crypto group never weights macro and a
    macro group never weights crypto sources (only its own routed sources get a
    multiplier)."""
    cache = AltDataCache()
    cache.set("crypto_fg", {"value": 8}, ttl_sec=9999, now_ts=0.0)
    cache.set("okx_funding", {"X": {"fundingRate": -0.0025}}, ttl_sec=9999, now_ts=0.0)
    cache.set("fred_macro", {"vix": 45.0, "hy_spread": 620.0}, ttl_sec=9999, now_ts=0.0)

    _, _, crypto_ev = fuse_evidence("crypto:BTC", cache, now_ts=1.0)
    assert "fred_macro" not in crypto_ev["source_weights"]  # macro not routed to crypto
    assert "crypto_fg" in crypto_ev["source_weights"]

    _, _, macro_ev = fuse_evidence("forex:EURUSD", cache, now_ts=1.0)
    assert "crypto_fg" not in macro_ev["source_weights"]  # crypto not routed to fx
    assert "okx_funding" not in macro_ev["source_weights"]
    assert "fred_macro" in macro_ev["source_weights"]


def test_fuse_weighting_preserves_label_outcomes() -> None:
    """The 0.75-1.25 weighting is a tilt on the existing base scores; it must
    not change which canonical label wins for the already-covered fixtures
    (label산출 동일, behavior-identical for these convictions)."""
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 12.0, "hy_spread": 250.0},
        ttl_sec=9999,
        now_ts=0.0,
    )
    hint, _, _ = fuse_evidence("index:US500", cache, now_ts=1.0)
    assert hint == "bull_trend"  # unchanged from pre-P1

    cache2 = AltDataCache()
    cache2.set("fred_macro", {"vix": 26.0, "hy_spread": 350.0}, ttl_sec=9999, now_ts=0.0)
    hint2, _, _ = fuse_evidence("forex:GBPUSD", cache2, now_ts=1.0)
    assert hint2 is None  # single mild bear (+1.0 * 1.25 = 1.25) still below 1.5 floor


# ── CFTC COT positioning → commodity regime evidence ──────────────────────────
def test_cot_net_long_extreme_yields_bull_hint() -> None:
    # Positioning at the 92nd percentile of THIS contract's own range → strong
    # net-long-for-this-contract → bull (2.0 * 1.25 = 2.5 >= 1.5 floor).
    cache = AltDataCache()
    cache.set("cftc_cot", {"OIL_CRUDE": {"net_spec_pctile": 0.92, "net_spec_pct": 0.05}},
              ttl_sec=1000, now_ts=0.0)
    hint, conf, ev = fuse_evidence("commodity:OIL_CRUDE", cache, now_ts=1.0)
    assert hint == "bull_trend"
    assert ev["cot_net_spec_pctile"] == 0.92
    assert conf >= 0.3


def test_cot_net_short_extreme_yields_bear_hint() -> None:
    cache = AltDataCache()
    cache.set("cftc_cot", {"GOLD": {"net_spec_pctile": 0.08, "net_spec_pct": 0.20}},
              ttl_sec=1000, now_ts=0.0)
    hint, _conf, ev = fuse_evidence("commodity:GOLD", cache, now_ts=1.0)
    assert hint == "bear_trend"
    assert ev["cot_net_spec_pctile"] == 0.08


def test_cot_at_own_median_is_neutral_no_override() -> None:
    # The structural-bias fix: a HIGH raw level sitting at its own median
    # (pctile ~0.5) yields NO hint — gold at +0.44 is no longer permanently bull.
    cache = AltDataCache()
    cache.set("cftc_cot", {"GOLD": {"net_spec_pctile": 0.50, "net_spec_pct": 0.44}},
              ttl_sec=1000, now_ts=0.0)
    hint, conf, _ev = fuse_evidence("commodity:GOLD", cache, now_ts=1.0)
    assert hint is None and conf == 0.0


def test_cot_absent_symbol_is_noop_macro_stands() -> None:
    # COT payload has OIL_CRUDE but the group is GOLD → no COT evidence for GOLD.
    cache = AltDataCache()
    cache.set("cftc_cot", {"OIL_CRUDE": {"net_spec_pctile": 0.92}}, ttl_sec=1000, now_ts=0.0)
    hint, conf, ev = fuse_evidence("commodity:GOLD", cache, now_ts=1.0)
    assert hint is None and conf == 0.0
    assert "cot_net_spec_pctile" not in ev


def test_cot_mild_extremity_below_floor_no_override() -> None:
    # Mild (pctile 0.75 in [0.70,0.85)): 1.0 * 1.25 = 1.25 < 1.5 floor → no
    # override, evidence still surfaced.
    cache = AltDataCache()
    cache.set("cftc_cot", {"CORN": {"net_spec_pctile": 0.75, "net_spec_pct": 0.16}},
              ttl_sec=1000, now_ts=0.0)
    hint, _conf, ev = fuse_evidence("commodity:CORN", cache, now_ts=1.0)
    assert hint is None
    assert ev["cot_net_spec_pctile"] == 0.75


def test_cot_does_not_route_to_forex() -> None:
    # cftc_cot is a commodity-only source; a forex group never sees it.
    cache = AltDataCache()
    cache.set("cftc_cot", {"OIL_CRUDE": {"net_spec_pctile": 0.92}}, ttl_sec=1000, now_ts=0.0)
    sources = cache.get_for_group("forex:EURUSD", now_ts=1.0)
    assert "cftc_cot" not in sources


# ── Currency: per-source observation-date propagation (age labelling) ─────────
def test_cot_report_date_propagated_to_evidence() -> None:
    """Currency fix: the COLLECTOR already keeps ``report_date``; the fuser must
    PROPAGATE it so the judge prompt + axis-B can age-discount a stale (weekly)
    COT print. flow_not_block: no drop, only a label.
    """
    cache = AltDataCache()
    cache.set(
        "cftc_cot",
        {"OIL_CRUDE": {"net_spec_pctile": 0.92, "net_spec_pct": 0.05,
                       "report_date": "2026-06-16"}},
        ttl_sec=1000, now_ts=0.0,
    )
    _hint, _conf, ev = fuse_evidence("commodity:OIL_CRUDE", cache, now_ts=1.0)
    assert ev["cot_report_date"] == "2026-06-16"


def test_cot_missing_report_date_is_graceful() -> None:
    cache = AltDataCache()
    cache.set("cftc_cot", {"OIL_CRUDE": {"net_spec_pctile": 0.92}}, ttl_sec=1000, now_ts=0.0)
    _hint, _conf, ev = fuse_evidence("commodity:OIL_CRUDE", cache, now_ts=1.0)
    # No report_date in the row → key simply absent (no crash, no fake date).
    assert "cot_report_date" not in ev


def test_macro_asof_and_age_surfaced() -> None:
    """Currency fix: the macro scorer surfaces the FRED observation date(s) and a
    computed ``macro_age_days`` (oldest present series) so a Friday VIX read over a
    weekend is visibly stale to the judge.
    """
    # now_ts = 2026-06-29 00:00:00 UTC; vix observed 2026-06-26 → 3 days old.
    import datetime as _dt

    now = _dt.datetime(2026, 6, 29, tzinfo=_dt.timezone.utc).timestamp()
    cache = AltDataCache()
    cache.set(
        "fred_macro",
        {"vix": 18.5, "hy_spread": 280.0,
         "vix_asof": "2026-06-26", "hy_asof": "2026-06-26"},
        ttl_sec=9999, now_ts=now,  # set + fuse at the same clock (TTL not expired)
    )
    _hint, _conf, ev = fuse_evidence("forex:EURUSD", cache, now_ts=now)
    assert ev["vix_asof"] == "2026-06-26"
    assert ev["macro_age_days"] == 3


def test_macro_no_asof_is_graceful() -> None:
    # Pre-fix payload with no asof keys → no age surfaced, scorer still runs.
    cache = AltDataCache()
    cache.set("fred_macro", {"vix": 18.5, "hy_spread": 280.0}, ttl_sec=9999, now_ts=0.0)
    _hint, _conf, ev = fuse_evidence("forex:EURUSD", cache, now_ts=1.0)
    assert "vix_asof" not in ev
    assert "macro_age_days" not in ev
    assert ev["vix"] == 18.5  # scorer behaviour unchanged
