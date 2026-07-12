"""edgar_filings + filing_proximity_shadow writes routed through the shared
DBWriter (db-writer-reader-split roadmap 1.10, 2026-07-12 audit item [A] —
``EdgarEventsCollector._persist``, chunk-submitted executemany).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path — covered by
``test_edgar_events.py``. This file covers ONLY the new opt-in path: (1) an
end-to-end real-DBWriter run lands the same rows a direct write would, (2)
large batches are chunk-submitted (never one mega job), (3) the kill switch
falls back to the direct-conn path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.edgar_events import EdgarEventsCollector
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db

_TICKERS_BODY = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}


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


def _filing_row(i: int) -> dict[str, Any]:
    return {
        "form": "8-K", "filingDate": "2026-07-02",
        "acceptanceDateTime": f"2026-07-02T{10 + i % 10:02d}:00:00.000Z",
        "accessionNumber": f"0001-{i}",
    }


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, submissions: dict[str, Any]) -> None:
        self._submissions = submissions

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.sec.gov":
            return httpx.Response(200, json=_TICKERS_BODY)
        return httpx.Response(200, json=self._submissions)


def _client(submissions: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_MockTransport(submissions))


@pytest.mark.asyncio
async def test_db_writer_end_to_end_lands_same_rows_as_direct_write(
    tmp_path: Path,
) -> None:
    submissions = _submissions_body([_filing_row(0)])

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    coll_direct = EdgarEventsCollector(symbols_override=("AAPL",), conn=golden_conn)
    await coll_direct.fetch(client=_client(submissions))
    golden_rows = golden_conn.execute(
        "SELECT symbol, accession_number, acceptance_ts FROM edgar_filings"
    ).fetchall()
    golden_shadow = golden_conn.execute(
        "SELECT symbol, accession_number FROM filing_proximity_shadow"
    ).fetchall()
    golden_conn.close()
    assert golden_rows  # sanity: the direct path actually wrote something

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = EdgarEventsCollector(
            symbols_override=("AAPL",), conn=conn, db_writer=dbw
        )
        await coll.fetch(client=_client(submissions))
    finally:
        dbw.stop()  # final drain — queued jobs land before this returns
    rows = conn.execute(
        "SELECT symbol, accession_number, acceptance_ts FROM edgar_filings"
    ).fetchall()
    shadow = conn.execute(
        "SELECT symbol, accession_number FROM filing_proximity_shadow"
    ).fetchall()
    conn.close()
    assert rows == golden_rows
    assert shadow == golden_shadow


@pytest.mark.asyncio
async def test_db_writer_chunks_large_filing_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol carrying MORE rows than the chunk size still lands every row,
    split across multiple db_writer jobs (never one mega executemany)."""
    monkeypatch.setenv("POLARIS_EDGAR_DB_CHUNK_ROWS", "10")
    submissions = _submissions_body([_filing_row(i) for i in range(25)])

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=64, drain_ms=10)
    dbw.start()
    conn = connect(db)
    submitted_labels: list[str] = []
    orig_submit = dbw.submit

    def _spy_submit(fn: Any, *, durable: bool = False, label: str = "") -> Any:
        submitted_labels.append(label)
        return orig_submit(fn, durable=durable, label=label)

    monkeypatch.setattr(dbw, "submit", _spy_submit)
    try:
        coll = EdgarEventsCollector(
            symbols_override=("AAPL",), conn=conn, db_writer=dbw
        )
        await coll.fetch(client=_client(submissions))
    finally:
        dbw.stop()
    n = conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0]
    conn.close()
    assert n == 25
    edgar_jobs = [lbl for lbl in submitted_labels if lbl == "edgar_filings"]
    assert len(edgar_jobs) == 3  # 25 rows / chunk_rows=10 -> 3 chunks


@pytest.mark.asyncio
async def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    submissions = _submissions_body([_filing_row(0)])

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = EdgarEventsCollector(
            symbols_override=("AAPL",), conn=conn, db_writer=dbw
        )
        # db_writer is supplied but the kill switch disables routing — the
        # write runs synchronously on ``conn`` (no drain wait needed).
        await coll.fetch(client=_client(submissions))
        n = conn.execute("SELECT COUNT(*) FROM edgar_filings").fetchone()[0]
    finally:
        dbw.stop()
    conn.close()
    assert n == 1
