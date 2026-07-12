"""stablecoin_liquidity writes routed through the shared DBWriter
(db-writer-reader-split roadmap 1.10, 2026-07-12 audit item [A] —
``DefiLlamaStablesCollector._persist``).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path — covered by
``test_defillama_stables.py``. This file covers ONLY the new opt-in path: an
end-to-end real-DBWriter run lands the same rows a direct write would, and
the kill switch falls back to the direct-conn path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.defillama_stables import DefiLlamaStablesCollector
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db


def _asset(symbol: str, cur: float, prev_day: float, prev_week: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "circulating": {"peggedUSD": cur},
        "circulatingPrevDay": {"peggedUSD": prev_day},
        "circulatingPrevWeek": {"peggedUSD": prev_week},
    }


def _client(assets: list[dict[str, Any]]) -> httpx.AsyncClient:
    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"peggedAssets": assets})

    return httpx.AsyncClient(transport=_MockTransport(), base_url="https://mock.test")


@pytest.mark.asyncio
async def test_db_writer_end_to_end_lands_same_rows_as_direct_write(
    tmp_path: Path,
) -> None:
    assets = [_asset("USDT", 200.0, 199.0, 195.0)]

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    coll_direct = DefiLlamaStablesCollector(conn=golden_conn)
    await coll_direct.fetch(client=_client(assets))
    golden_rows = golden_conn.execute(
        "SELECT symbol, mcap_usd FROM stablecoin_liquidity ORDER BY symbol"
    ).fetchall()
    golden_conn.close()
    assert golden_rows == [("TOTAL", 200.0), ("USDT", 200.0)]

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = DefiLlamaStablesCollector(conn=conn, db_writer=dbw)
        await coll.fetch(client=_client(assets))
    finally:
        dbw.stop()
    rows = conn.execute(
        "SELECT symbol, mcap_usd FROM stablecoin_liquidity ORDER BY symbol"
    ).fetchall()
    conn.close()
    assert rows == golden_rows


@pytest.mark.asyncio
async def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    assets = [_asset("USDT", 200.0, 199.0, 195.0)]

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        coll = DefiLlamaStablesCollector(conn=conn, db_writer=dbw)
        await coll.fetch(client=_client(assets))
        n = conn.execute("SELECT COUNT(*) FROM stablecoin_liquidity").fetchone()[0]
    finally:
        dbw.stop()
    conn.close()
    assert n == 2  # USDT + TOTAL
