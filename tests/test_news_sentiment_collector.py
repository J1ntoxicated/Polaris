"""Tests for the news_sentiment alt-data collector + classifier (#6 EVIDENCE).

DEMO/PAPER only — virtual funds. The news collector is a pure SIGNAL/EVIDENCE
source: it SUGGESTS regime context, it never throttles/blocks/sizes/exits. A
failing or keyless collector returns ``{}`` (graceful fallback, NOT a defensive
halt) and the price-only regime stands.

No live network: the Alpaca News REST endpoint is exercised with an injected
``httpx`` MockTransport. The GPT classifier is exercised with an injected stub
client (in-loop GPT = 0 — classification is the collector's async cadence, off
the per-tick hot path). The deterministic lexicon is the keyless fallback and is
exercised WITHOUT any network or GPT call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from polaris.core.altdata.news_sentiment import (
    NewsSentimentCollector,
    _lexicon_score,
)

# ── Mock transports (mirror tests/test_altdata_collectors.py) ────────────────


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: Callable[[httpx.Request], Any]) -> None:
        self._responder = responder
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self._responder(request)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


class _ExplodingTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call to {request.url}")


def _client(responder: Callable[[httpx.Request], Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://mock.test"
    )


# ── Stub GPT client (mirrors the GPTClient.messages.create shape) ────────────


class _StubGPTMessages:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        import json

        return {"content": [{"type": "text", "text": json.dumps(self._payload)}]}


class _StubGPTClient:
    """Minimal GPTClient look-alike: exposes ``.messages.create``."""

    def __init__(self, payload: Any) -> None:
        self.messages = _StubGPTMessages(payload)


def _alpaca_news_body(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"news": items, "next_page_token": None}


# ── ABC + metadata contract ──────────────────────────────────────────────────


def test_news_collector_metadata() -> None:
    coll = NewsSentimentCollector(api_key="k", secret_key="s")
    assert coll.name == "news_sentiment"
    assert isinstance(coll.ttl_sec, int) and coll.ttl_sec == 900
    assert coll.asset_classes == ("crypto", "forex", "commodity", "index", "equity")


# ── Keyless graceful skip (no Alpaca creds → {} WITHOUT network) ──────────────


@pytest.mark.asyncio
async def test_news_no_creds_returns_empty_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET",
        "ARCHIVE_ALPACA_PAPER_API_KEY",
        "ARCHIVE_ALPACA_PAPER_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    coll = NewsSentimentCollector(api_key=None, secret_key=None)
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_news_empty_env_creds_returns_empty_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Empty-string env creds count as absent.
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "")
    monkeypatch.delenv("ARCHIVE_ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ARCHIVE_ALPACA_PAPER_SECRET", raising=False)
    coll = NewsSentimentCollector()
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


# ── Network error → {} (never raises) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_news_network_error_returns_empty() -> None:
    def responder(_req: httpx.Request) -> Any:
        return httpx.Response(500, json={"message": "boom"})

    coll = NewsSentimentCollector(api_key="k", secret_key="s")
    out = await coll.fetch(client=_client(responder))
    assert out == {}


@pytest.mark.asyncio
async def test_news_empty_feed_returns_empty() -> None:
    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body([])

    coll = NewsSentimentCollector(api_key="k", secret_key="s")
    out = await coll.fetch(client=_client(responder))
    assert out == {}


# ── Currency: recency window + headline-age surfacing ────────────────────────


@pytest.mark.asyncio
async def test_news_request_sends_recency_start_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Currency fix: the Alpaca request carries a ``start`` lower-bound so a thin
    ticker's weeks-old headline is not pulled as 'latest'. flow_not_block: this is
    a freshness WINDOW on the fetch, not a per-trade block.
    """
    monkeypatch.setenv("POLARIS_NEWS_RECENCY_HOURS", "36")
    seen: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        seen["start"] = req.url.params.get("start")
        return _alpaca_news_body([])

    coll = NewsSentimentCollector(api_key="k", secret_key="s")
    await coll.fetch(client=_client(responder))
    start = seen["start"]
    assert start is not None and start.endswith("Z")
    # Parseable ISO8601 within ~36h of now.
    import datetime as _dt

    parsed = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    age_h = (_dt.datetime.now(_dt.timezone.utc) - parsed).total_seconds() / 3600.0
    assert 35.0 <= age_h <= 37.0


@pytest.mark.asyncio
async def test_news_preserves_created_at_and_surfaces_max_age() -> None:
    """Currency fix: ``created_at`` is preserved per article and the per-symbol
    aggregate surfaces the OLDEST contributing headline age (``news_max_age_h``).
    """
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    fresh = (now - _dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - _dt.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [
        {"id": 1, "headline": "Apple beats earnings", "summary": "",
         "symbols": ["AAPL"], "source": "x", "created_at": fresh},
        {"id": 2, "headline": "Apple older note", "summary": "",
         "symbols": ["AAPL"], "source": "x", "created_at": stale},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    gpt_payload = {
        "results": [
            {"id": 1, "sentiment": 0.5, "relevance": 0.8, "magnitude": 0.5},
            {"id": 2, "sentiment": 0.4, "relevance": 0.6, "magnitude": 0.4},
        ]
    }
    coll = NewsSentimentCollector(
        api_key="k", secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(gpt_payload),
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None and "AAPL" in out
    # Oldest contributing headline ≈ 20h → max_age_h reflects the STALEST, not freshest.
    assert out["AAPL"]["news_max_age_h"] == pytest.approx(20.0, abs=0.5)


@pytest.mark.asyncio
async def test_news_missing_created_at_is_graceful() -> None:
    """An article with no ``created_at`` still aggregates; max-age is simply absent
    for a symbol whose headlines all lack a timestamp (no crash, no fake age)."""
    items = [
        {"id": 1, "headline": "Gold rallies hard", "summary": "",
         "symbols": ["XAUUSD"], "source": "x"},  # no created_at
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    coll = NewsSentimentCollector(api_key="k", secret_key="s")  # lexicon fallback
    out = await coll.fetch(client=_client(responder))
    assert out is not None and "XAUUSD" in out
    assert out["XAUUSD"].get("news_max_age_h") is None


# ── Happy path: GPT classifier → per-symbol flat dict ────────────────────────


@pytest.mark.asyncio
async def test_news_classifies_with_gpt_per_symbol() -> None:
    items = [
        {
            "id": 1,
            "headline": "Apple smashes earnings, raises guidance",
            "summary": "Record quarter",
            "symbols": ["AAPL"],
            "source": "benzinga",
            "created_at": "2026-06-25T12:00:00Z",
        },
        {
            "id": 2,
            "headline": "Bitcoin rallies to new highs on ETF inflows",
            "summary": "",
            "symbols": ["BTCUSD", "BTC"],
            "source": "benzinga",
            "created_at": "2026-06-25T12:05:00Z",
        },
    ]

    def responder(req: httpx.Request) -> Any:
        assert "news" in req.url.path
        return _alpaca_news_body(items)

    # GPT returns a per-headline classification keyed by headline id.
    gpt_payload = {
        "results": [
            {"id": 1, "sentiment": 0.8, "relevance": 0.9, "magnitude": 0.7},
            {"id": 2, "sentiment": 0.6, "relevance": 0.8, "magnitude": 0.6},
        ]
    }
    coll = NewsSentimentCollector(
        api_key="k",
        secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(gpt_payload),
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "AAPL" in out
    aapl = out["AAPL"]
    assert aapl["sentiment"] == pytest.approx(0.8)
    assert aapl["relevance"] == pytest.approx(0.9)
    assert aapl["magnitude"] == pytest.approx(0.7)
    assert aapl["n"] == 1
    assert "headline" in aapl and aapl["headline"]
    # BTC headline maps to its symbols too.
    assert "BTC" in out or "BTCUSD" in out
    # Values are flat scalars (fuser contract): no nested dicts.
    for sym_payload in out.values():
        for v in sym_payload.values():
            assert isinstance(v, (int, float, str))


@pytest.mark.asyncio
async def test_news_multi_headline_same_symbol_aggregates() -> None:
    items = [
        {"id": 1, "headline": "Tesla beats", "symbols": ["TSLA"], "source": "x"},
        {"id": 2, "headline": "Tesla recall risk", "symbols": ["TSLA"], "source": "x"},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    gpt_payload = {
        "results": [
            {"id": 1, "sentiment": 0.6, "relevance": 0.8, "magnitude": 0.5},
            {"id": 2, "sentiment": -0.4, "relevance": 0.7, "magnitude": 0.5},
        ]
    }
    coll = NewsSentimentCollector(
        api_key="k",
        secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(gpt_payload),
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "TSLA" in out
    assert out["TSLA"]["n"] == 2
    # Aggregate sentiment is between the two (relevance-weighted mean), not raised.
    assert -0.4 <= out["TSLA"]["sentiment"] <= 0.6


# ── GPT unavailable → deterministic lexicon fallback (no GPT call) ───────────


@pytest.mark.asyncio
async def test_news_lexicon_fallback_when_no_gpt() -> None:
    items = [
        {"id": 1, "headline": "Stock surges on strong record profit beat",
         "symbols": ["NVDA"], "source": "x"},
        {"id": 2, "headline": "Company plunges on fraud lawsuit and loss",
         "symbols": ["XYZ"], "source": "x"},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    # gpt_client_factory returns None → keyless GPT → lexicon fallback path.
    coll = NewsSentimentCollector(
        api_key="k", secret_key="s", gpt_client_factory=lambda: None
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "NVDA" in out and "XYZ" in out
    assert out["NVDA"]["sentiment"] > 0.0  # bullish lexicon hits
    assert out["XYZ"]["sentiment"] < 0.0  # bearish lexicon hits


@pytest.mark.asyncio
async def test_news_crypto_pair_symbol_aliases_to_canonical_base() -> None:
    """Alpaca tags crypto as BTCUSD; the fuser routes crypto:BTC — emit BOTH so
    the canonical-base lookup hits (else crypto news is silently dropped)."""
    items = [
        {"id": 7, "headline": "Bitcoin rallies on ETF inflows",
         "symbols": ["BTCUSD"], "source": "x"},
        {"id": 8, "headline": "Ether climbs", "symbols": ["ETH/USD"], "source": "x"},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    gpt_payload = {
        "results": [
            {"id": 7, "sentiment": 0.7, "relevance": 0.9, "magnitude": 0.6},
            {"id": 8, "sentiment": 0.5, "relevance": 0.8, "magnitude": 0.5},
        ]
    }
    coll = NewsSentimentCollector(
        api_key="k", secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(gpt_payload),
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    # Raw pair key AND the canonical crypto base alias both present.
    assert "BTCUSD" in out and "BTC" in out
    assert out["BTC"]["sentiment"] == pytest.approx(out["BTCUSD"]["sentiment"])
    # Slash form keeps its raw key AND emits the canonical base alias.
    assert "ETH/USD" in out and "ETH" in out


@pytest.mark.asyncio
async def test_news_gpt_string_ids_still_match() -> None:
    """GPT may echo numeric ids as JSON strings — must not drop the batch."""
    items = [
        {"id": 5, "headline": "AAA beats and surges", "symbols": ["AAA"], "source": "x"},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    # GPT returns the id as a STRING "5", not int 5.
    gpt_payload = {"results": [{"id": "5", "sentiment": 0.6, "relevance": 0.8, "magnitude": 0.5}]}
    coll = NewsSentimentCollector(
        api_key="k", secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(gpt_payload),
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "AAA" in out
    assert out["AAA"]["sentiment"] == pytest.approx(0.6)


def test_lexicon_score_signs() -> None:
    # Pure deterministic lexicon scorer — directionally correct, bounded [-1, 1].
    pos = _lexicon_score("record profit beats and surges higher on upgrade")
    neg = _lexicon_score("misses, plunges on fraud bankruptcy and downgrade")
    flat = _lexicon_score("company holds annual shareholder meeting tuesday")
    assert pos > 0.0
    assert neg < 0.0
    assert flat == pytest.approx(0.0)
    for s in (pos, neg, flat):
        assert -1.0 <= s <= 1.0


@pytest.mark.asyncio
async def test_news_gpt_error_falls_back_to_lexicon() -> None:
    """If the GPT call raises, the collector must still return lexicon scores."""
    items = [
        {"id": 1, "headline": "record profit surges on strong beat",
         "symbols": ["AAA"], "source": "x"},
    ]

    def responder(_req: httpx.Request) -> Any:
        return _alpaca_news_body(items)

    class _BoomMessages:
        async def create(self, **_kwargs: Any) -> Any:
            raise RuntimeError("gpt down")

    class _BoomClient:
        def __init__(self) -> None:
            self.messages = _BoomMessages()

    coll = NewsSentimentCollector(
        api_key="k", secret_key="s", gpt_client_factory=lambda: _BoomClient()
    )
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "AAA" in out
    assert out["AAA"]["sentiment"] > 0.0  # lexicon recovered the bullish tone
