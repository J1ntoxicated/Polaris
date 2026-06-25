"""Registration wiring tests for the news_sentiment collector (#6 EVIDENCE).

DEMO/PAPER only. Proves the new collector is (a) in the default collector list
the producer loop runs, and (b) routed to every asset-class group by the cache,
so a fresh news payload reaches ``fuse_evidence`` for crypto/forex/commodity/
index/equity groups. SIGNAL-only: routing wires evidence, never a block.
"""

from __future__ import annotations

from polaris.core.altdata.cache import _GROUP_SOURCES, AltDataCache
from polaris.scripts.production_paper_loop import _default_altdata_collectors


def test_news_collector_in_default_list() -> None:
    names = {type(c).__name__ for c in _default_altdata_collectors()}
    assert "NewsSentimentCollector" in names
    # And the instance carries the stable cache key.
    insts = [c for c in _default_altdata_collectors() if c.name == "news_sentiment"]
    assert len(insts) == 1


def test_news_routed_to_all_asset_classes() -> None:
    for prefix in ("crypto", "forex", "commodity", "index", "equity"):
        assert "news_sentiment" in _GROUP_SOURCES[prefix], prefix


def test_cache_get_for_group_returns_news() -> None:
    cache = AltDataCache()
    cache.set(
        "news_sentiment",
        {"AAPL": {"sentiment": 0.6, "relevance": 0.8, "magnitude": 0.7, "n": 2}},
        ttl_sec=900,
        now_ts=1000.0,
    )
    out = cache.get_for_group("equity:AAPL", now_ts=1001.0)
    assert "news_sentiment" in out
    # Still present for a crypto group too (routed to all classes).
    out_c = cache.get_for_group("crypto:BTC", now_ts=1001.0)
    assert "news_sentiment" in out_c
