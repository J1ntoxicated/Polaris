"""Tests for ADR-007 universe scanner + liquidity tier."""
from invasion.spot.universe import liquidity_tier as lt


def test_classify_major_threshold():
    assert lt.classify(ticker="BTC", volume_usd_24h=500_000_000) == "major"


def test_classify_meme_hint_overrides():
    # Volume in mid range, but DOGE matches a meme hint.
    assert lt.classify(ticker="DOGE", volume_usd_24h=30_000_000) == "meme"


def test_classify_below_micro_returns_micro():
    assert lt.classify(ticker="UNKN", volume_usd_24h=100_000) == "micro"


def test_filter_excludes_micro_by_default():
    out = lt.filter_universe(candidates=[
        {"ticker": "BTC", "volume_usd_24h": 5e8},
        {"ticker": "TINY", "volume_usd_24h": 5e5},
    ], max_tickers=10)
    tickers = [c["ticker"] for c in out]
    assert "BTC" in tickers
    assert "TINY" not in tickers


def test_filter_caps_to_max_tickers():
    cands = [{"ticker": f"T{i}", "volume_usd_24h": 1e7 + i}
             for i in range(20)]
    out = lt.filter_universe(candidates=cands, max_tickers=5)
    assert len(out) == 5
    # Sorted descending by volume
    vols = [c["volume_usd_24h"] for c in out]
    assert vols == sorted(vols, reverse=True)
