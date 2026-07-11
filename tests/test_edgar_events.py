"""Tests for the SEC EDGAR filing-events collector (frontgate-scan item #1).

DEMO/PAPER only — virtual funds. Pure EVIDENCE source: on a missing-symbols /
network / parse error this returns ``{}`` (graceful, never a defensive halt).
No live network — every SEC endpoint is exercised via an injected ``httpx``
MockTransport.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.edgar_events import (
    DEFAULT_FORMS,
    EdgarEventsCollector,
    _cik_lookup,
    build_cik_map,
    parse_recent_filings,
)
from polaris.storage.schema import init_db

# ── Mock transport ───────────────────────────────────────────────────────────


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


def _client(responder: Callable[[httpx.Request], Any]) -> tuple[httpx.AsyncClient, _MockTransport]:
    transport = _MockTransport(responder)
    return httpx.AsyncClient(transport=transport), transport


_TICKERS_BODY = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def _submissions_body(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cik": "320193",
        "filings": {
            "recent": {
                "form": [r["form"] for r in rows],
                "filingDate": [r["filingDate"] for r in rows],
                "acceptanceDateTime": [r["acceptanceDateTime"] for r in rows],
                "accessionNumber": [r["accessionNumber"] for r in rows],
                "primaryDocument": [r.get("primaryDocument", "") for r in rows],
            }
        },
    }


def _router(
    submissions_by_cik: dict[str, dict[str, Any]] | None = None,
    tickers_body: dict[str, Any] | None = None,
) -> Callable[[httpx.Request], Any]:
    submissions_by_cik = submissions_by_cik or {}
    tickers_body = tickers_body if tickers_body is not None else _TICKERS_BODY

    def responder(req: httpx.Request) -> Any:
        if req.url.host == "www.sec.gov":
            return tickers_body
        if req.url.host == "data.sec.gov":
            for cik, body in submissions_by_cik.items():
                if f"CIK{int(cik):010d}.json" in req.url.path:
                    return body
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    return responder


# ── pure functions ────────────────────────────────────────────────────────


def test_parse_recent_filings_filters_to_target_forms() -> None:
    body = _submissions_body(
        [
            {"form": "4", "filingDate": "2026-06-17",
             "acceptanceDateTime": "2026-06-17T22:40:43.000Z",
             "accessionNumber": "0001-4"},
            {"form": "8-K", "filingDate": "2026-07-02",
             "acceptanceDateTime": "2026-07-02T20:30:18.000Z",
             "accessionNumber": "0001-8K", "primaryDocument": "8k.htm"},
        ]
    )
    out = parse_recent_filings(body, DEFAULT_FORMS)
    assert len(out) == 1
    assert out[0]["form_type"] == "8-K"
    assert out[0]["accession_number"] == "0001-8K"
    assert out[0]["primary_document"] == "8k.htm"
    # acceptanceDateTime -> epoch seconds (A-grade provenance).
    assert out[0]["acceptance_ts"] == 1783024218


def test_parse_recent_filings_empty_on_missing_keys() -> None:
    assert parse_recent_filings({}, DEFAULT_FORMS) == []
    assert parse_recent_filings({"filings": {}}, DEFAULT_FORMS) == []


def test_parse_recent_filings_missing_acceptance_is_none_not_crash() -> None:
    body = _submissions_body(
        [
            {"form": "10-Q", "filingDate": "2026-05-01",
             "acceptanceDateTime": None, "accessionNumber": "0001-10Q"},
        ]
    )
    out = parse_recent_filings(body, DEFAULT_FORMS)
    assert out[0]["acceptance_ts"] is None


def test_build_cik_map_normalises_ticker_and_cik() -> None:
    mapping = build_cik_map(_TICKERS_BODY)
    assert mapping["AAPL"] == "320193"
    assert mapping["NVDA"] == "1045810"


def test_build_cik_map_skips_malformed_rows() -> None:
    body = {"0": {"ticker": "AAPL"}, "1": "not-a-dict", "2": {"cik_str": None, "ticker": "X"}}
    assert build_cik_map(body) == {}


def test_cik_lookup_reconciles_dual_class_separator_mismatch() -> None:
    """SEC's index keys dual-class tickers with '-' (BRK-B) while the live
    universe stores '.' (BRK.B, per Alpaca) — and vice versa."""
    mapping = {"BRK-B": "1067983", "AGM.A": "2222"}
    assert _cik_lookup(mapping, "BRK.B") == "1067983"  # '.' -> '-' fallback
    assert _cik_lookup(mapping, "BRK-B") == "1067983"  # exact hit, no fallback needed
    assert _cik_lookup(mapping, "AGM-A") == "2222"  # '-' -> '.' fallback
    assert _cik_lookup(mapping, "ZZZZ_UNKNOWN") is None


# ── collector ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_symbols_no_conn_returns_empty_no_network() -> None:
    coll = EdgarEventsCollector()
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_returns_latest_filing_per_symbol() -> None:
    submissions = {
        "320193": _submissions_body(
            [
                {"form": "8-K", "filingDate": "2026-07-02",
                 "acceptanceDateTime": "2026-07-02T20:30:18.000Z",
                 "accessionNumber": "0001-8K", "primaryDocument": "8k.htm"},
                {"form": "10-Q", "filingDate": "2026-05-01",
                 "acceptanceDateTime": "2026-05-01T12:00:00.000Z",
                 "accessionNumber": "0001-10Q"},
            ]
        )
    }
    coll = EdgarEventsCollector(symbols_override=("AAPL",))
    client, transport = _client(_router(submissions))
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    row = out["AAPL"]
    assert row["form_type"] == "8-K"  # newest-first -> latest wins
    assert row["accession_number"] == "0001-8K"
    assert row["days_since"] is not None and row["days_since"] >= 0.0
    # sequential per-symbol pacing must not fan out extra requests.
    assert sum(1 for c in transport.calls if c.url.host == "data.sec.gov") == 1


@pytest.mark.asyncio
async def test_fetch_persists_filings_and_shadow_tag(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    submissions = {
        "320193": _submissions_body(
            [
                {"form": "8-K", "filingDate": "2026-07-02",
                 "acceptanceDateTime": "2026-07-02T20:30:18.000Z",
                 "accessionNumber": "0001-8K"},
            ]
        )
    }
    coll = EdgarEventsCollector(symbols_override=("AAPL",), conn=conn)
    client, _ = _client(_router(submissions))
    await coll.fetch(client=client)
    await client.aclose()

    rows = conn.execute("SELECT symbol, accession_number, acceptance_ts FROM edgar_filings").fetchall()
    assert rows == [("AAPL", "0001-8K", 1783024218)]
    shadow_rows = conn.execute(
        "SELECT symbol, accession_number FROM filing_proximity_shadow"
    ).fetchall()
    assert shadow_rows == [("AAPL", "0001-8K")]
    conn.close()


@pytest.mark.asyncio
async def test_refetch_same_filing_is_idempotent_dedup(tmp_path: Path) -> None:
    """Re-running fetch() with the SAME filing must not duplicate the ledger row
    (accession_number PRIMARY KEY / INSERT OR IGNORE)."""
    conn = init_db(tmp_path / "t.sqlite")
    submissions = {
        "320193": _submissions_body(
            [
                {"form": "8-K", "filingDate": "2026-07-02",
                 "acceptanceDateTime": "2026-07-02T20:30:18.000Z",
                 "accessionNumber": "0001-8K"},
            ]
        )
    }
    coll = EdgarEventsCollector(symbols_override=("AAPL",), conn=conn)
    client, _ = _client(_router(submissions))
    await coll.fetch(client=client)
    await coll.fetch(client=client)
    await client.aclose()

    n = conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0]
    assert n == 1
    conn.close()


@pytest.mark.asyncio
async def test_dual_class_shared_cik_both_symbols_persist(tmp_path: Path) -> None:
    """GOOG/GOOGL (and BRK-A/BRK-B) share one CIK and thus one accession-number
    set. The composite (symbol, accession_number) PK must keep BOTH share
    classes' rows — a bare accession_number PK would silently drop the
    second one via INSERT OR IGNORE."""
    conn = init_db(tmp_path / "t.sqlite")
    tickers_body = {
        "0": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
        "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    }
    submissions = {
        "1652044": _submissions_body(
            [
                {"form": "8-K", "filingDate": "2026-07-02",
                 "acceptanceDateTime": "2026-07-02T20:30:18.000Z",
                 "accessionNumber": "0001652044-26-000123"},
            ]
        )
    }
    coll = EdgarEventsCollector(
        symbols_override=("GOOG", "GOOGL"), conn=conn
    )
    client, _ = _client(_router(submissions, tickers_body))
    out = await coll.fetch(client=client)
    await client.aclose()

    assert out is not None
    assert set(out) == {"GOOG", "GOOGL"}
    rows = conn.execute(
        "SELECT symbol, accession_number FROM edgar_filings ORDER BY symbol"
    ).fetchall()
    assert rows == [
        ("GOOG", "0001652044-26-000123"),
        ("GOOGL", "0001652044-26-000123"),
    ]
    conn.close()


@pytest.mark.asyncio
async def test_dot_suffixed_symbol_resolves_via_hyphenated_sec_ticker(
    tmp_path: Path,
) -> None:
    """Live universe stores dot-suffixed dual-class symbols (BRK.B) but SEC's
    own company_tickers.json uses hyphens (BRK-B) — fetch() must still
    resolve the CIK instead of silently skipping the symbol."""
    conn = init_db(tmp_path / "t.sqlite")
    tickers_body = {"0": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"}}
    submissions = {
        "1067983": _submissions_body(
            [
                {"form": "10-Q", "filingDate": "2026-07-01",
                 "acceptanceDateTime": "2026-07-01T12:00:00.000Z",
                 "accessionNumber": "0001067983-26-000001"},
            ]
        )
    }
    coll = EdgarEventsCollector(symbols_override=("BRK.B",), conn=conn)
    client, _ = _client(_router(submissions, tickers_body))
    out = await coll.fetch(client=client)
    await client.aclose()

    assert out is not None
    assert set(out) == {"BRK.B"}
    conn.close()


@pytest.mark.asyncio
async def test_unknown_symbol_no_cik_is_skipped() -> None:
    coll = EdgarEventsCollector(symbols_override=("ZZZZ_UNKNOWN",))
    client, transport = _client(_router({}))
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}
    # never even attempted a submissions call for an unmapped symbol.
    assert not any(c.url.host == "data.sec.gov" for c in transport.calls)


@pytest.mark.asyncio
async def test_tickers_fetch_error_returns_empty_gracefully() -> None:
    def responder(req: httpx.Request) -> Any:
        if req.url.host == "www.sec.gov":
            return httpx.Response(500, json={})
        return httpx.Response(404, json={})

    coll = EdgarEventsCollector(symbols_override=("AAPL",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_404_submissions_for_cik_is_skipped_gracefully() -> None:
    coll = EdgarEventsCollector(symbols_override=("AAPL",))
    client, _ = _client(_router({}))  # AAPL maps to a CIK with no submissions stub -> 404
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_rotating_batch_bounds_sweep(monkeypatch: Any) -> None:
    """~1,500-symbol universe must NOT be swept in one fetch — each cycle
    processes <=_SWEEP_BATCH and the cursor advances so successive fetches
    cover disjoint slices (producer head-of-line starvation guard)."""
    from polaris.core.altdata import edgar_events as mod

    seen: list[str] = []

    async def fake_recent(self: Any, cli: Any, cik: str, headers: Any) -> list[Any]:
        seen.append(cik)
        return []

    monkeypatch.setattr(mod.EdgarEventsCollector, "_recent_filings", fake_recent)
    monkeypatch.setattr(mod, "_SWEEP_BATCH", 3)
    syms = tuple(f"SY{i}" for i in range(8))
    coll = mod.EdgarEventsCollector(symbols_override=syms)

    async def fake_cik_map(self: Any, cli: Any, headers: Any) -> dict[str, str]:
        return {s: str(1000 + i) for i, s in enumerate(syms)}

    monkeypatch.setattr(mod.EdgarEventsCollector, "_cik_map_for", fake_cik_map)
    monkeypatch.setattr(mod, "_pace_sec", lambda: 0.0)

    client, _ = _client(lambda req: {})
    await coll.fetch(client=client)
    first = list(seen)
    assert len(first) == 3
    await coll.fetch(client=client)
    second = seen[3:]
    assert len(second) == 3
    assert set(first).isdisjoint(second)
    await client.aclose()
