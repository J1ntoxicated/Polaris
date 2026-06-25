"""Tests for the dashboard CONTEXT/INTEL panel BACKEND — read-only altdata read.

DEMO/PAPER, virtual funds. Seeds the ``altdata_snapshot`` audit table and asserts
``_collect_context_intel`` rolls up the LATEST row per source into a one-line
display summary ({source, asset_class, latest_value, age_sec, signal, fresh}).

Pure display layer — the panel reads the read-only ``altdata_snapshot`` audit
table only; it NEVER feeds sizing / gating / exit / strategy / loop. The bot's
behavior is unchanged whether or not this panel renders. This is "the bot's eyes"
surfaced for the operator: every alt-data / context input the regime fuser sees,
collected in one place.

Covered:
- One row PER source, carrying the freshest snapshot (LATEST ts wins).
- Per-source summary extraction: crypto_fg value+label, okx_funding avg funding,
  fred_macro VIX, cftc_cot net-spec, and an UNKNOWN source (generic fallback).
- ``signal`` direction (bullish / bearish / neutral) derived from the payload.
- ``age_sec`` + ``fresh`` freshness (fresh when age < the source TTL window).
- news_sentiment auto-display when present (the news collector lands rows here).
- Graceful empty — missing table / no rows → empty list (never crashes).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_e2 import _collect_context_intel
from polaris.storage.schema import init_db


def _put(conn: sqlite3.Connection, *, ts: int, source: str, asset_class: str, payload: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO altdata_snapshot (ts, source, asset_class, payload_json) "
        "VALUES (?, ?, ?, ?)",
        (ts, source, asset_class, json.dumps(payload)),
    )


def _seed(conn: sqlite3.Connection) -> int:
    now = int(time.time())
    # crypto_fg — current + an OLDER stale row for the same source (latest wins).
    _put(conn, ts=now - 9999, source="crypto_fg", asset_class="crypto",
         payload={"value": 12, "label": "Extreme Fear", "one_week_ago": 40})
    _put(conn, ts=now - 100, source="crypto_fg", asset_class="crypto",
         payload={"value": 78, "label": "Extreme Greed", "one_week_ago": 55})
    # okx_funding — nested per-instrument dict.
    _put(conn, ts=now - 50, source="okx_funding", asset_class="crypto",
         payload={"BTC-USDT-SWAP": {"fundingRate": 0.0002, "oi": 1.0},
                  "ETH-USDT-SWAP": {"fundingRate": 0.0004, "oi": 2.0}})
    # fred_macro — flat scalars.
    _put(conn, ts=now - 200, source="fred_macro", asset_class="forex",
         payload={"vix": 31.5, "hy_spread": 480.0, "move": 110.0, "yield_curve": -0.3})
    # cftc_cot — per-symbol nested with percentile.
    _put(conn, ts=now - 400, source="cftc_cot", asset_class="commodity",
         payload={"GOLD": {"net_spec_pctile": 0.92, "net_spec_pct": 0.3,
                           "net_spec_chg": 0.05, "report_date": "2026-06-20"}})
    # unknown source — generic fallback (no bespoke extractor).
    _put(conn, ts=now - 30, source="myfxbook", asset_class="forex",
         payload={"community_long_pct": 62.0})
    conn.commit()
    return now


def test_context_intel_one_row_per_source_latest_wins() -> None:
    conn = init_db(":memory:")
    try:
        now = _seed(conn)
        rows = _collect_context_intel(conn, now_s=now)
        by_src = {r.source: r for r in rows}
        # one row per distinct source (the stale crypto_fg row is collapsed).
        assert len(rows) == len(by_src)
        assert set(by_src) == {"crypto_fg", "okx_funding", "fred_macro",
                               "cftc_cot", "myfxbook"}
        # latest crypto_fg row wins (value 78, not the older 12).
        assert "78" in by_src["crypto_fg"].latest_value
    finally:
        conn.close()


def test_context_intel_signal_direction() -> None:
    conn = init_db(":memory:")
    try:
        now = _seed(conn)
        by_src = {r.source: r for r in _collect_context_intel(conn, now_s=now)}
        # Extreme Greed (78) → bullish; elevated VIX (31.5) → bearish/risk-off.
        assert by_src["crypto_fg"].signal == "bullish"
        assert by_src["fred_macro"].signal == "bearish"
        # cftc GOLD net-spec percentile 0.92 (crowded long) → bullish.
        assert by_src["cftc_cot"].signal == "bullish"
    finally:
        conn.close()


def test_context_intel_freshness_age() -> None:
    conn = init_db(":memory:")
    try:
        now = _seed(conn)
        by_src = {r.source: r for r in _collect_context_intel(conn, now_s=now)}
        # crypto_fg latest row is 100s old; age is computed off now_s.
        assert by_src["crypto_fg"].age_sec == 100
        # 100s < crypto_fg's 1800s TTL → fresh.
        assert by_src["crypto_fg"].fresh is True
    finally:
        conn.close()


def test_context_intel_stale_when_past_ttl() -> None:
    conn = init_db(":memory:")
    try:
        now = int(time.time())
        # okx_funding TTL is 300s; a 1000s-old row is stale (grey).
        _put(conn, ts=now - 1000, source="okx_funding", asset_class="crypto",
             payload={"BTC-USDT-SWAP": {"fundingRate": 0.0001}})
        conn.commit()
        by_src = {r.source: r for r in _collect_context_intel(conn, now_s=now)}
        assert by_src["okx_funding"].fresh is False
    finally:
        conn.close()


def test_context_intel_unknown_source_generic_fallback() -> None:
    conn = init_db(":memory:")
    try:
        now = _seed(conn)
        by_src = {r.source: r for r in _collect_context_intel(conn, now_s=now)}
        mfx = by_src["myfxbook"]
        # no bespoke extractor → generic summary is non-empty + neutral signal.
        assert mfx.latest_value != ""
        assert mfx.signal == "neutral"
    finally:
        conn.close()


def test_context_intel_news_sentiment_auto_display() -> None:
    """The news collector (separate agent) lands rows under source='news_sentiment'
    shaped PER-SYMBOL (like funding/COT) — the panel must fold them to one line
    with a headline + aggregate sentiment, picking the most-relevant headline."""
    conn = init_db(":memory:")
    try:
        now = int(time.time())
        # Real NewsSentimentCollector._aggregate shape: {sym: {sentiment, relevance,
        # magnitude, n, headline}}. The higher-relevance BTC headline wins display.
        _put(conn, ts=now - 60, source="news_sentiment", asset_class="crypto",
             payload={
                 "BTC": {"sentiment": 0.70, "relevance": 0.9, "magnitude": 0.6,
                         "n": 3, "headline": "ETF inflows hit record"},
                 "ETH": {"sentiment": 0.40, "relevance": 0.5, "magnitude": 0.3,
                         "n": 1, "headline": "Upgrade ships on schedule"},
             })
        conn.commit()
        by_src = {r.source: r for r in _collect_context_intel(conn, now_s=now)}
        assert "news_sentiment" in by_src
        ni = by_src["news_sentiment"]
        assert ni.signal == "bullish"  # relevance-weighted mean > 0.15
        assert "ETF inflows" in ni.latest_value  # highest-relevance headline
        assert "4 headlines" in ni.latest_value  # n summed across symbols
    finally:
        conn.close()


def test_context_intel_graceful_empty() -> None:
    conn = init_db(":memory:")
    try:
        # no altdata rows seeded → empty list, never raises.
        assert _collect_context_intel(conn, now_s=int(time.time())) == []
    finally:
        conn.close()


def test_context_intel_missing_table_graceful() -> None:
    """A bare DB without the altdata_snapshot table degrades to empty (older schema)."""
    conn = sqlite3.connect(":memory:")
    try:
        assert _collect_context_intel(conn, now_s=int(time.time())) == []
    finally:
        conn.close()


def test_context_intel_on_snapshot(tmp_path: Path) -> None:
    """collect_snapshot surfaces context_intel end-to-end (serializable dataclass)."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    try:
        _seed(conn)
    finally:
        conn.close()
    snap = collect_snapshot(db)
    assert isinstance(snap.context_intel, list)
    sources = {r.source for r in snap.context_intel}
    assert "crypto_fg" in sources and "fred_macro" in sources
