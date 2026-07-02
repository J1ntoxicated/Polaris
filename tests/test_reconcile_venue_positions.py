"""Tests for ``recover.reconcile_venue_positions`` (VENUE startup import).

After a fresh-DB reset the bot starts FLAT and is BLIND to real venue
holdings (Alpaca 4 positions ~$44k, etc.). ``reconcile_venue_positions``
fetches live venue positions and imports any NOT already tracked in the DB
as ``status='open'`` rows + a synthetic ENTRY fill at the CURRENT mark
(entry = current price ⇒ unrealized PnL starts ~0, NO fabricated cost basis).

No network: adapters are mocked. NO order placement.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from typing import Any

import pytest

from polaris.core.lifecycle.recover import reconcile_venue_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.storage.schema import init_db


class _MockAlpaca:
    def __init__(self, positions: list[dict[str, Any]]) -> None:
        self._positions = positions

    async def fetch_positions(self) -> list[dict[str, Any]]:
        return self._positions


class _MockCapital:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    async def list_positions(self) -> dict[str, Any]:
        return self._body


class _MockOKX:
    def __init__(self) -> None:
        self.called = False

    async def fetch_positions(self, inst_type: str = "SPOT") -> dict[str, Any]:
        self.called = True
        return {"data": []}

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        self.called = True
        return {"data": []}


def _seed_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    side: str = "long",
    qty: float = 1.0,
    status: str = "open",
) -> None:
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, underlying_group_id, strategy_id,
             entry_strategy_id, active_strategy_id, side, qty, status,
             opened_ts, swap_count)
        VALUES (?, ?, ?, '', 'tsmom', 'tsmom', 'tsmom', ?, ?, ?, 100, 0)
        """,
        (position_id, venue, symbol, side, qty, status),
    )


@pytest.fixture
def conn(tmp_path: Any) -> Iterable[sqlite3.Connection]:
    db_path = tmp_path / "test.sqlite"
    c = init_db(db_path)
    try:
        yield c
    finally:
        c.close()


def _alpaca_spce(qty: str = "4700", price: str = "7.15") -> dict[str, Any]:
    mv = str(float(qty) * float(price))
    return {
        "symbol": "SPCE",
        "qty": qty,
        "side": "long",
        "avg_entry_price": "3.10",  # historical cost — must be IGNORED
        "current_price": price,
        "market_value": mv,
    }


def test_imports_new_alpaca_position(conn: sqlite3.Connection) -> None:
    alpaca = _MockAlpaca([_alpaca_spce()])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))

    # one SimulatedTrade returned (same shape as hydrate)
    assert len(imported) == 1
    t = imported[0]
    assert isinstance(t, SimulatedTrade)
    assert t.venue == "alpaca"
    assert t.symbol == "SPCE"
    assert t.side == "long"
    # entry = CURRENT mark (7.15), NOT historical avg_entry_price (3.10)
    assert t.entry_price == pytest.approx(7.15)
    assert t.notional_usd == pytest.approx(4700 * 7.15)
    assert t.base_qty == pytest.approx(4700.0)
    assert t.strategy_id == "_reconcile_import"

    # exactly one position row + one entry fill persisted
    prow = conn.execute(
        "SELECT strategy_id, side, qty, status FROM positions "
        "WHERE venue='alpaca' AND symbol='SPCE'"
    ).fetchall()
    assert len(prow) == 1
    assert prow[0][0] == "_reconcile_import"
    assert prow[0][3] == "open"

    frow = conn.execute(
        "SELECT fill_price, size_usd, base_qty, fee_usd, is_close, strategy_id "
        "FROM fills WHERE instrument_id='alpaca:SPCE'"
    ).fetchall()
    assert len(frow) == 1
    fill_price, size_usd, base_qty, fee_usd, is_close, strat = frow[0]
    assert fill_price == pytest.approx(7.15)  # entry at current mark
    assert size_usd == pytest.approx(4700 * 7.15)
    assert base_qty == pytest.approx(4700.0)
    assert fee_usd == 0.0  # no fabricated fee
    assert is_close == 0
    assert strat == "_reconcile_import"

    # unrealized PnL ~0: entry_price == current mark
    assert t.entry_price == pytest.approx(float(_alpaca_spce()["current_price"]))


def test_already_tracked_not_double_imported(conn: sqlite3.Connection) -> None:
    _seed_position(conn, position_id="pos_existing", venue="alpaca", symbol="SPCE")
    alpaca = _MockAlpaca([_alpaca_spce()])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))

    assert imported == []
    # still exactly one position (the pre-existing one); no reconcile import row
    rows = conn.execute(
        "SELECT position_id FROM positions WHERE venue='alpaca' AND symbol='SPCE'"
    ).fetchall()
    assert [r[0] for r in rows] == ["pos_existing"]
    # no synthetic entry fill written
    assert conn.execute(
        "SELECT COUNT(*) FROM fills WHERE instrument_id='alpaca:SPCE'"
    ).fetchone()[0] == 0


def test_imports_new_capital_position(conn: sqlite3.Connection) -> None:
    capital_body = {
        "positions": [
            {
                "position": {
                    "dealId": "DEAL123",
                    "direction": "BUY",
                    "size": 2.0,
                    "level": 100.0,
                },
                "market": {
                    "epic": "GOLD",
                    "bid": 109.0,
                    "offer": 111.0,
                },
            }
        ]
    }
    alpaca = _MockAlpaca([])
    capital = _MockCapital(capital_body)
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))

    assert len(imported) == 1
    t = imported[0]
    assert t.venue == "capital"
    assert t.symbol == "GOLD"
    assert t.side == "long"  # BUY → long
    # current mark = mid(bid, offer) = 110.0
    assert t.entry_price == pytest.approx(110.0)
    assert t.base_qty == pytest.approx(2.0)
    assert t.notional_usd == pytest.approx(2.0 * 110.0)
    assert t.deal_id == "DEAL123"
    assert conn.execute(
        "SELECT COUNT(*) FROM positions WHERE venue='capital' AND symbol='GOLD'"
    ).fetchone()[0] == 1


def test_okx_dust_skipped_by_default(conn: sqlite3.Connection) -> None:
    alpaca = _MockAlpaca([])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))

    assert imported == []
    # OKX adapter never touched when import_okx_spot=False
    assert okx.called is False


def test_empty_venues_noop(conn: sqlite3.Connection) -> None:
    alpaca = _MockAlpaca([])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))
    assert imported == []
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0


def test_none_adapters_noop(conn: sqlite3.Connection) -> None:
    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=None,
        now_ts=5000,
    ))
    assert imported == []
