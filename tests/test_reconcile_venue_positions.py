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


def test_import_stamps_risk_usd_anchor_and_flat_excursion(
    conn: sqlite3.Connection,
) -> None:
    """[P0-5] a reconcile-import row must NOT be an R-measurement blind spot.

    Injects a stub ``atr_anchor_fn`` (the DI seam ``production_paper_loop``
    wires to the real ``timeframe_anchor_atr_pct`` — kept out of ``core`` per
    the layering rail) so the import resolves ``entry_atr_pct`` the same way
    a normal entry does — then asserts ``risk_usd`` / ``entry_atr_pct`` are
    stamped (not NULL) and ``peak_price`` / ``trough_price`` start AT the
    entry mark (a flat/zero excursion at t=0, never inherited from the
    venue's historical cost basis or pre-entry market noise).
    """
    now_ts = 100_000

    def _stub_anchor(
        conn: sqlite3.Connection, instrument_id: str, now_ts: int,
    ) -> tuple[float, str] | None:
        assert instrument_id == "alpaca:SPCE"
        return (0.02, "1m")

    alpaca = _MockAlpaca([_alpaca_spce()])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=now_ts, atr_anchor_fn=_stub_anchor,
    ))
    assert len(imported) == 1

    row = conn.execute(
        "SELECT risk_usd, entry_atr_pct, peak_price, trough_price, exit_state "
        "FROM positions WHERE venue='alpaca' AND symbol='SPCE'"
    ).fetchone()
    risk_usd, entry_atr_pct, peak_price, trough_price, exit_state = row
    assert risk_usd is not None and risk_usd > 0.0
    assert entry_atr_pct is not None and entry_atr_pct > 0.0
    # entry mark = 7.15 (current_price) — peak/trough must start there, not
    # at the ignored historical avg_entry_price (3.10) or any stale bar.
    assert peak_price == pytest.approx(7.15)
    assert trough_price == pytest.approx(7.15)
    assert exit_state == "open"


def test_import_no_bars_leaves_atr_anchor_and_risk_usd_null(
    conn: sqlite3.Connection,
) -> None:
    """No bars window for the symbol (no ``atr_anchor_fn`` injected) → NULL
    ``entry_atr_pct`` (no fabricated ATR read) AND NULL ``risk_usd`` — the
    dashboard's 'n/a' render gate (``snapshot_q_positions.py``
    ``mfe_atr_r``/``mae_atr_r``) keys off ``risk_usd IS NULL``, so stamping
    a floored-but-ATR-disconnected ``risk_usd`` here would fabricate an
    honest-looking R value with no real ATR anchor behind it. ``risk_usd``
    is only ever computed when a REAL anchor resolved (see the anchor-hit
    test above, which asserts ``risk_usd > 0.0``).
    """
    alpaca = _MockAlpaca([_alpaca_spce()])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=5000,
    ))
    assert len(imported) == 1
    row = conn.execute(
        "SELECT risk_usd, entry_atr_pct FROM positions "
        "WHERE venue='alpaca' AND symbol='SPCE'"
    ).fetchone()
    assert row[0] is None
    assert row[1] is None


def test_import_no_bars_row_renders_mfe_mae_na_on_dashboard(
    conn: sqlite3.Connection,
) -> None:
    """End-to-end audit-spec item ④: a REAL reconcile-import row (no injected
    ``atr_anchor_fn``, i.e. no bars) must render 'n/a' (``None``) for
    ``mfe_atr_r``/``mae_atr_r`` on the POSITIONS board — through the actual
    ``_import_one`` persist path, not a hand-crafted SQL fixture that the
    real code can no longer produce.
    """
    from polaris.scripts.dashboard.snapshot_queries import (
        _cell_mult_lookup,
        _entry_price_lookup,
        _last_prices,
        _now_s,
        _read_positions,
    )
    from polaris.scripts.dashboard.snapshot_sections import _regime_bars

    alpaca = _MockAlpaca([_alpaca_spce()])
    capital = _MockCapital({"positions": []})
    okx = _MockOKX()
    now_s = _now_s()

    imported = asyncio.run(reconcile_venue_positions(
        conn, okx_adapter=okx, capital_adapter=capital, alpaca_adapter=alpaca,
        now_ts=now_s,
    ))
    assert len(imported) == 1

    # (wave D 병렬 병합: misc-batch가 _regime_bars에 now_s kwarg 추가 — stale 필터)
    _bars, regime_lookup = _regime_bars(conn, now_s=now_s)
    positions = _read_positions(
        conn,
        now_s=now_s,
        last_prices=_last_prices(conn),
        entry_lookup=_entry_price_lookup(conn),
        cell_mult=_cell_mult_lookup(conn),
        regime_lookup=regime_lookup,
    )
    by_sym = {p.symbol: p for p in positions}
    spce = by_sym["SPCE"]
    assert spce.mfe_atr_r is None
    assert spce.mae_atr_r is None
