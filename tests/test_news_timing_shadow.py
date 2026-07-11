"""News publication/ingestion timestamp audit + syndicate dedup SHADOW
(frontgate-scan item #7, G2/G3, behavior-0). DEMO/PAPER virtual capital only.

Covers:
- ``normalize_headline`` / ``dedup_syndicate`` pure dedup rule.
- ``log_news_timing_shadow`` DB write (per-article, pre-aggregation).
- The ``news_timing_shadow`` migration (fresh DB, additive table).
- Behavior-0: ``NewsSentimentCollector.fetch`` returns the BYTE-IDENTICAL
  aggregate with or without ``shadow_conn`` wired — only a side-effect DB
  write differs.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.news_sentiment import NewsSentimentCollector
from polaris.core.altdata.news_timing_shadow import (
    dedup_syndicate,
    log_news_timing_shadow,
    normalize_headline,
)
from polaris.storage.schema import init_db

UTC = _dt.UTC


# ---------------------------------------------------------------------------
# normalize_headline / dedup_syndicate
# ---------------------------------------------------------------------------


def test_normalize_headline_case_and_punctuation_insensitive() -> None:
    a = normalize_headline("Fed Cuts Rates by 25bps!")
    b = normalize_headline("fed cuts rates by 25bps")
    assert a == b


def test_normalize_headline_collapses_whitespace() -> None:
    assert normalize_headline("Apple   beats\testimates") == "apple beats estimates"


def test_dedup_syndicate_identical_headlines_share_group() -> None:
    articles = [
        {"id": 1, "headline": "Fed cuts rates by 25bps"},
        {"id": 2, "headline": "FED CUTS RATES BY 25BPS"},  # syndicated re-publish
        {"id": 3, "headline": "Apple beats earnings"},
    ]
    groups = dedup_syndicate(articles)
    assert groups[1] == groups[2] == "1"
    assert groups[3] != groups[1]


def test_dedup_syndicate_empty_headline_is_singleton() -> None:
    articles = [{"id": 1, "headline": ""}, {"id": 2, "headline": ""}]
    groups = dedup_syndicate(articles)
    assert groups[1] != groups[2]


# ---------------------------------------------------------------------------
# log_news_timing_shadow
# ---------------------------------------------------------------------------


def test_log_news_timing_shadow_writes_per_symbol_rows(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "news.sqlite")
    try:
        now = _dt.datetime(2026, 6, 25, 13, 0, 0, tzinfo=UTC)
        articles = [
            {
                "id": 1, "headline": "Bitcoin rallies on ETF inflows",
                "symbols": ["BTCUSD", "BTC"], "created_at": "2026-06-25T12:00:00Z",
            },
        ]
        scored = {"1": {"sentiment": 0.6, "relevance": 0.8, "magnitude": 0.5}}
        n = log_news_timing_shadow(conn, articles=articles, scored=scored, now=now)
        assert n == 2  # fans out to both tagged symbols
        rows = conn.execute(
            "SELECT symbol, headline_id, publication_ts, ingestion_ts, delay_h, "
            "dedup_group_id, sentiment, relevance, n_syndicate FROM news_timing_shadow "
            "ORDER BY symbol"
        ).fetchall()
        assert [r[0] for r in rows] == ["BTC", "BTCUSD"]
        row = rows[0]
        assert row[1] == "1"
        assert row[2] == int(_dt.datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC).timestamp())
        assert row[3] == int(now.timestamp())
        assert row[4] == pytest.approx(1.0)  # 12:00 -> 13:00 = 1h delay
        assert row[5] == "1"
        assert row[6] == pytest.approx(0.6)
        assert row[7] == pytest.approx(0.8)
        assert row[8] == 1
    finally:
        conn.close()


def test_log_news_timing_shadow_syndicate_count_reflected(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "syndicate.sqlite")
    try:
        now = _dt.datetime(2026, 6, 25, 13, 0, 0, tzinfo=UTC)
        articles = [
            {"id": 1, "headline": "Fed cuts rates by 25bps", "symbols": ["SPY"],
             "created_at": "2026-06-25T12:55:00Z"},
            {"id": 2, "headline": "FED CUTS RATES BY 25BPS", "symbols": ["SPY"],
             "created_at": "2026-06-25T12:56:00Z"},
        ]
        scored = {
            "1": {"sentiment": 0.1, "relevance": 0.5, "magnitude": 0.2},
            "2": {"sentiment": 0.1, "relevance": 0.5, "magnitude": 0.2},
        }
        log_news_timing_shadow(conn, articles=articles, scored=scored, now=now)
        rows = conn.execute(
            "SELECT n_syndicate, dedup_group_id FROM news_timing_shadow ORDER BY headline_id"
        ).fetchall()
        assert rows[0][0] == 2
        assert rows[0][1] == rows[1][1] == "1"
    finally:
        conn.close()


def test_log_news_timing_shadow_missing_created_at_null_publication_ts(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "no_ts.sqlite")
    try:
        now = _dt.datetime(2026, 6, 25, 13, 0, 0, tzinfo=UTC)
        articles = [{"id": 1, "headline": "x", "symbols": ["AAPL"], "created_at": ""}]
        scored = {"1": {"sentiment": 0.0, "relevance": 0.5, "magnitude": 0.0}}
        log_news_timing_shadow(conn, articles=articles, scored=scored, now=now)
        row = conn.execute(
            "SELECT publication_ts, delay_h FROM news_timing_shadow"
        ).fetchone()
        assert row[0] is None
        assert row[1] is None
    finally:
        conn.close()


def test_log_news_timing_shadow_noop_without_conn() -> None:
    n = log_news_timing_shadow(
        None, articles=[{"id": 1, "headline": "x", "symbols": ["A"]}],
        scored={"1": {"sentiment": 0.0, "relevance": 0.0}}, now=_dt.datetime.now(UTC),
    )
    assert n == 0


def test_log_news_timing_shadow_noop_empty_articles(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "empty.sqlite")
    try:
        n = log_news_timing_shadow(conn, articles=[], scored={}, now=_dt.datetime.now(UTC))
        assert n == 0
    finally:
        conn.close()


def test_log_news_timing_shadow_skips_unscored_articles(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "unscored.sqlite")
    try:
        articles = [{"id": 1, "headline": "x", "symbols": ["A"], "created_at": ""}]
        n = log_news_timing_shadow(conn, articles=articles, scored={}, now=_dt.datetime.now(UTC))
        assert n == 0
        assert conn.execute("SELECT COUNT(*) FROM news_timing_shadow").fetchone()[0] == 0
    finally:
        conn.close()


def test_news_timing_shadow_table_exists_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(news_timing_shadow)")}
        assert {
            "symbol", "headline_id", "publication_ts", "ingestion_ts", "delay_h",
            "dedup_group_id", "sentiment", "relevance", "n_syndicate", "created_ts",
        } <= cols
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Behavior-0 — NewsSentimentCollector.fetch() wiring
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"news": self._items, "next_page_token": None})


class _StubGPTMessages:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def create(self, **kwargs: Any) -> Any:
        import json

        return {"content": [{"type": "text", "text": json.dumps(self._payload)}]}


class _StubGPTClient:
    def __init__(self, payload: Any) -> None:
        self.messages = _StubGPTMessages(payload)


def _client(items: list[dict[str, Any]]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://data.alpaca.markets", transport=_MockTransport(items)
    )


_ITEMS = [
    {
        "id": 1, "headline": "Apple smashes earnings, raises guidance",
        "summary": "Record quarter", "symbols": ["AAPL"], "source": "benzinga",
        "created_at": "2026-06-25T12:00:00Z",
    },
]
_GPT_PAYLOAD = {"results": [{"id": 1, "sentiment": 0.8, "relevance": 0.9, "magnitude": 0.7}]}


@pytest.mark.asyncio
async def test_fetch_byte_identical_with_or_without_shadow_conn(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "wired.sqlite")
    try:
        coll_no_shadow = NewsSentimentCollector(
            api_key="k", secret_key="s",
            gpt_client_factory=lambda: _StubGPTClient(_GPT_PAYLOAD),
        )
        out_no_shadow = await coll_no_shadow.fetch(client=_client(_ITEMS))

        coll_shadow = NewsSentimentCollector(
            api_key="k", secret_key="s",
            gpt_client_factory=lambda: _StubGPTClient(_GPT_PAYLOAD),
            shadow_conn=conn,
        )
        out_shadow = await coll_shadow.fetch(client=_client(_ITEMS))

        assert out_shadow == out_no_shadow
        # The tag DID fire — proves the wiring is live, not dead code.
        assert conn.execute("SELECT COUNT(*) FROM news_timing_shadow").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fetch_shadow_log_fail_open_on_broken_conn() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.close()  # broken — any .execute() raises
    coll = NewsSentimentCollector(
        api_key="k", secret_key="s",
        gpt_client_factory=lambda: _StubGPTClient(_GPT_PAYLOAD),
        shadow_conn=conn,
    )
    out = await coll.fetch(client=_client(_ITEMS))
    assert out is not None
    assert "AAPL" in out
