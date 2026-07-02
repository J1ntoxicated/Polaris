"""Regression test — P1-6 recover.py event-loop cross-bind (main-loop adapters).

DEMO/PAPER only (paper-trading bot, no real money). Aggressive bias preserved
— this is a reliability fix on the boot reconcile-import path, not a sizing
or entry-gating throttle.

``reconcile_venue_positions`` runs from inside the running production
paper-loop event loop (``run_production_paper_loop`` is ``async def``), with
venue adapters (``AlpacaAdapter`` / ``CapitalSession``) that hold a
persistent ``httpx.AsyncClient`` whose connection pool is first used on the
MAIN loop (health check / earlier ticks) before the boot reconcile-import
runs. The old ``_run_coro`` drove the adapter coroutine via
``ThreadPoolExecutor.submit(asyncio.run, coro)`` — a brand-new event loop in
a worker thread. Reusing the already-warmed httpx connection pool's internal
keep-alive lock (an ``asyncio.Event`` bound to the main loop) from that
worker-thread loop raises::

    RuntimeError: <asyncio.locks.Event object at ...> is bound to a
    different event loop

and the whole reconcile-import silently fails (caught by the broad
``except Exception`` in ``reconcile_venue_positions`` and logged as
"[reconcile] ... fetch/import failed").

FIX (P1-6): ``reconcile_venue_positions`` is now ``async`` and ``await``s the
adapter coroutine directly on the caller's loop instead of driving it via
``ThreadPoolExecutor.submit(asyncio.run, coro)`` in a separate thread/loop —
same-loop execution means the adapter's connection pool is never touched
from a different loop, so the cross-bind RuntimeError cannot occur.

This test reproduces the ORIGINAL failure shape end-to-end with a REAL
``httpx.AsyncClient`` against a local loopback ``asyncio`` TCP server (no
external network) whose keep-alive connection is first used on the main
loop, then reused via ``reconcile_venue_positions`` — and asserts the
position import actually succeeds when awaited from a running event loop
with a pre-warmed adapter.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

from polaris.core.lifecycle.recover import reconcile_venue_positions
from polaris.storage.schema import init_db


class _PreWarmedAlpaca:
    """Mimics ``AlpacaAdapter``: a persistent ``httpx.AsyncClient`` whose
    keep-alive connection pool is used once on the main loop (mirrors a
    prior tick / health check) before the boot reconcile-import call reuses
    it — same shape as the production adapter lifecycle.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_positions(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/positions")
        resp.raise_for_status()
        return [
            {
                "symbol": "SPCE",
                "qty": "4700",
                "side": "long",
                "current_price": "7.15",
                "market_value": str(4700 * 7.15),
            }
        ]


async def _serve_keepalive(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            body = b"{}"
            resp = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode()
                + b"\r\nConnection: keep-alive\r\n\r\n" + body
            )
            writer.write(resp)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionError):
        pass
    finally:
        writer.close()


@pytest.fixture
def conn(tmp_path: Any) -> Iterable[sqlite3.Connection]:
    db_path = tmp_path / "test.sqlite"
    c = init_db(db_path)
    try:
        yield c
    finally:
        c.close()


def test_reconcile_from_running_main_loop_imports_position(
    conn: sqlite3.Connection,
) -> None:
    """Awaiting reconcile_venue_positions from inside a running event loop
    with a main-loop-pre-warmed httpx adapter must NOT cross-loop-fail — the
    import must actually land, not silently return [].
    """

    async def _boot() -> list[Any]:
        server = await asyncio.start_server(_serve_keepalive, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        serve_task = asyncio.ensure_future(server.serve_forever())
        # single-connection pool forces keep-alive reuse of the SAME
        # main-loop-bound connection on the second (reconcile) call.
        limits = httpx.Limits(max_keepalive_connections=1, max_connections=1)
        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", limits=limits
        )
        try:
            adapter = _PreWarmedAlpaca(client)
            # Warm the connection pool on the MAIN loop (mirrors a prior
            # tick / health check before boot reconcile-import runs).
            await adapter.fetch_positions()

            # Awaited from within the coroutine — exactly how
            # ``run_production_paper_loop`` invokes
            # ``reconcile_venue_positions``.
            return await reconcile_venue_positions(
                conn,
                okx_adapter=None,
                capital_adapter=None,
                alpaca_adapter=adapter,
                now_ts=5000,
            )
        finally:
            await client.aclose()
            serve_task.cancel()
            server.close()

    imported = asyncio.run(_boot())

    assert len(imported) == 1
    assert imported[0].symbol == "SPCE"
    assert imported[0].venue == "alpaca"
    assert conn.execute(
        "SELECT COUNT(*) FROM positions WHERE venue='alpaca' AND symbol='SPCE'"
    ).fetchone()[0] == 1
