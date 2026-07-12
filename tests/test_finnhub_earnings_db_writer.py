"""earnings_calendar + earnings_proximity_shadow writes routed through the
shared DBWriter (db-writer-reader-split roadmap 1.10, 2026-07-12 audit item
[A] — ``FinnhubEarningsCollector._persist``).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path — covered by
``test_finnhub_earnings.py``. This file covers ONLY the new opt-in path: an
end-to-end real-DBWriter run lands the same rows a direct write would, and
the kill switch falls back to the direct-conn path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.finnhub_earnings import FinnhubEarningsCollector
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db


def _row(symbol: str, date: str, eps_est: float = 1.0, eps_act: float = 1.1) -> dict[str, Any]:
    return {
        "symbol": symbol, "date": date, "hour": "bmo",
        "epsEstimate": eps_est, "epsActual": eps_act,
        "revenueEstimate": 1000.0, "revenueActual": None,
    }


def _client(rows: list[dict[str, Any]]) -> httpx.AsyncClient:
    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"earningsCalendar": rows})

    return httpx.AsyncClient(transport=_MockTransport(), base_url="https://mock.test")


@pytest.mark.asyncio
async def test_db_writer_end_to_end_lands_same_rows_as_direct_write(
    tmp_path: Path,
) -> None:
    rows = [_row("AAPL", "2026-07-08")]

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    coll_direct = FinnhubEarningsCollector(
        api_key="k", symbols_override=("AAPL",), conn=golden_conn
    )
    await coll_direct.fetch(client=_client(rows))
    golden_cal = golden_conn.execute(
        "SELECT symbol, earnings_date, surprise_pct FROM earnings_calendar"
    ).fetchall()
    golden_shadow = golden_conn.execute(
        "SELECT symbol, surprise_pct FROM earnings_proximity_shadow"
    ).fetchall()
    golden_conn.close()
    assert golden_cal  # sanity

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = FinnhubEarningsCollector(
            api_key="k", symbols_override=("AAPL",), conn=conn, db_writer=dbw
        )
        await coll.fetch(client=_client(rows))
    finally:
        dbw.stop()
    cal = conn.execute(
        "SELECT symbol, earnings_date, surprise_pct FROM earnings_calendar"
    ).fetchall()
    shadow = conn.execute(
        "SELECT symbol, surprise_pct FROM earnings_proximity_shadow"
    ).fetchall()
    conn.close()
    assert cal == golden_cal
    assert shadow == golden_shadow


@pytest.mark.asyncio
async def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    rows = [_row("AAPL", "2026-07-08")]

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = FinnhubEarningsCollector(
            api_key="k", symbols_override=("AAPL",), conn=conn, db_writer=dbw
        )
        await coll.fetch(client=_client(rows))
        n = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    finally:
        dbw.stop()
    conn.close()
    assert n == 1
