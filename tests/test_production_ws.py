"""Wiring tests for _production_ws.start_ws_producers (P4 — M5).

No real network. Drives:
- focus → per-venue partition,
- build_ws_clients picks the right subclass per venue + skips venues without
  creds / session,
- start_ws_producers spawns flush loop + one task per client and tears them all
  down on stop_evt (M5 contract),
- resubscribe_ws_clients pushes new symbols into live clients.
"""

from __future__ import annotations

import asyncio

import pytest

from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.scripts._production_ws import (
    build_ws_clients,
    resubscribe_ws_clients,
    start_ws_producers,
)
from polaris.storage.schema import init_db
from polaris.venues.alpaca.ws import AlpacaQuoteWS
from polaris.venues.capital.ws import CapitalMarketWS
from polaris.venues.okx.ws import OKXTickerWS


def _seed_focus(conn, rows: list[tuple[str, str, str]]) -> None:
    """rows = [(venue, symbol, asset_class)]. Seeds universe + watchlist_focus."""
    # Only watchlist_focus is needed — venue partition reads venue+symbol from
    # it directly (universe is a LEFT JOIN for asset_class, not required here).
    cycle = 1_000_000
    for i, (venue, symbol, _ac) in enumerate(rows):
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_focus "
            "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
            " evict_reason) VALUES (?, ?, ?, ?, ?, 'core', NULL)",
            (cycle, venue, symbol, 100.0 - i, i),
        )
    conn.commit()


@pytest.fixture()
def conn(tmp_path):
    c = init_db(tmp_path / "ws_test.sqlite")
    yield c
    c.close()


class _FakeCapSession:
    class _Tok:
        cst = "C"
        security_token = "S"

    @property
    def tokens(self):  # noqa: ANN201
        return self._Tok()

    async def ensure_tokens(self):  # noqa: ANN201
        return self._Tok()


def test_build_clients_routes_by_venue(conn, tmp_path, monkeypatch):
    # Alpaca creds present so its client is built.
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "s")
    _seed_focus(
        conn,
        [
            ("okx", "BTC-USDT", "crypto"),
            ("capital", "CS.D.EURUSD.CFD.IP", "forex"),
            ("alpaca", "AAPL", "equity"),
        ],
    )
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(
        conn, writer=writer, capital_session=_FakeCapSession()
    )
    by_type = {type(c).__name__: c for c in clients}
    assert set(by_type) == {"OKXTickerWS", "CapitalMarketWS", "AlpacaQuoteWS"}
    assert isinstance(by_type["OKXTickerWS"], OKXTickerWS)
    assert isinstance(by_type["CapitalMarketWS"], CapitalMarketWS)
    assert isinstance(by_type["AlpacaQuoteWS"], AlpacaQuoteWS)


def test_capital_skipped_without_session(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET", raising=False)
    monkeypatch.delenv("ARCHIVE_ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ARCHIVE_ALPACA_PAPER_SECRET", raising=False)
    _seed_focus(
        conn,
        [("okx", "BTC-USDT", "crypto"), ("capital", "EURUSD", "forex")],
    )
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(conn, writer=writer, capital_session=None)
    # Only OKX — Capital needs a session, Alpaca needs creds.
    assert [type(c).__name__ for c in clients] == ["OKXTickerWS"]


def test_clients_route_on_quote_to_writer(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    _seed_focus(conn, [("okx", "BTC-USDT", "crypto")])
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(conn, writer=writer, capital_session=None)
    okx = clients[0]
    import json

    frame = json.dumps(
        {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"instId": "BTC-USDT", "bidPx": "1.0", "askPx": "1.2", "last": "1.1"}],
        }
    )
    tick = okx.parse_message(frame)
    okx._on_quote(tick)  # the writer.on_quote bound callback
    assert writer.live_px("okx:BTC-USDT") is not None


async def test_start_and_teardown(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    _seed_focus(conn, [("okx", "BTC-USDT", "crypto")])
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    stop = asyncio.Event()
    tasks, clients = start_ws_producers(
        conn, writer=writer, stop_evt=stop, capital_session=None
    )
    # flush loop + 1 OKX client task.
    assert len(tasks) == 2
    assert [c.venue for c in clients] == ["okx"]
    await asyncio.sleep(0.05)
    # M5 teardown: stop → cancel → gather(return_exceptions=True).
    stop.set()
    for t in tasks:
        t.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # gather must not propagate (cancelled / clean exits only).
    assert all(not isinstance(r, BaseException) or isinstance(r, asyncio.CancelledError) for r in results)
    writer.close()


@pytest.mark.asyncio
async def test_resubscribe_updates_symbols(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    _seed_focus(conn, [("okx", "BTC-USDT", "crypto")])
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(conn, writer=writer, capital_session=None)
    # Replace focus with a new OKX symbol; resubscribe should pick it up.
    conn.execute("DELETE FROM watchlist_focus")
    _seed_focus(conn, [("okx", "ETH-USDT", "crypto")])
    await resubscribe_ws_clients(conn, clients)
    import json

    args = json.loads(next(iter(clients[0].subscribe_messages())))["args"]
    assert {a["instId"] for a in args} == {"ETH-USDT"}


def _seed_open_position(conn, *, venue: str, symbol: str, group_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, 'vb', 'vb', 'vb', 'long', 1.0, 'open', 1, 0)",
        (f"{venue}:{symbol}", venue, symbol, group_id),
    )
    conn.commit()


def test_held_symbol_subscribed_even_when_not_in_focus(conn, tmp_path, monkeypatch):
    """FIX 2/2 — a HELD OKX position absent from the dynamic focus is STILL
    WS-subscribed (the focus∪held union feeds the venue partition)."""
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    _seed_focus(conn, [("okx", "BTC-USDT", "crypto")])
    # HYPE is held but NOT a dynamic focus pick.
    _seed_open_position(conn, venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE")
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(conn, writer=writer, capital_session=None)
    import json

    args = json.loads(next(iter(clients[0].subscribe_messages())))["args"]
    subbed = {a["instId"] for a in args}
    assert "HYPE-USDT" in subbed  # held → forced into the WS set
    assert "BTC-USDT" in subbed  # focus pick still present


@pytest.mark.asyncio
async def test_held_symbol_drops_from_ws_after_close(conn, tmp_path, monkeypatch):
    """Once the held position closes, the resubscribe drops it from the WS set."""
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    _seed_focus(conn, [("okx", "BTC-USDT", "crypto")])
    _seed_open_position(conn, venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE")
    writer = QuoteTickWriter(tmp_path / "ws_test.sqlite")
    clients = build_ws_clients(conn, writer=writer, capital_session=None)
    conn.execute("UPDATE positions SET status = 'closed'")
    conn.commit()
    await resubscribe_ws_clients(conn, clients)
    import json

    args = json.loads(next(iter(clients[0].subscribe_messages())))["args"]
    subbed = {a["instId"] for a in args}
    assert "HYPE-USDT" not in subbed
    assert "BTC-USDT" in subbed
