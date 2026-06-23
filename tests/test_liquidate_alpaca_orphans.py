"""Alpaca venue-orphan liquidation — free buying power by closing UNTRACKED holds.

ROOT CAUSE this pins: the Alpaca PAPER venue held 37 untracked long positions
(opened by an earlier bot generation whose fills were never persisted, then lost
on a reset) consuming the full ~$55.7k initial margin → ``buying_power = $0`` →
every new entry rejected ``insufficient_buying_power``. The DB tracks ZERO Alpaca
positions, so the venue holdings are pure orphans the exit engine can never see.

This module is the VENUE-side sibling of ``reconcile_alpaca_zombies`` (which only
marks DB ``status='open'`` rows terminal — useless here, the DB has 0 rows). It
fetches live venue positions, subtracts the bot's tracked-open set (HARD GUARD),
and SELLS each remaining orphan to free buying power.

GUARD CONTRACT (the load-bearing invariant):
* A position whose symbol IS in the bot's tracked-open set (``positions`` rows
  where ``venue='alpaca'`` and ``status IN ('open','active')``) is NEVER sold —
  a live tracked position is owned by the exit engine, never touched here.
* Only venue positions NOT in that tracked set are liquidated.
* Each close uses the EXACT venue qty (no dust, no oversell) + an idempotent
  ``client_order_id``.

flow_not_block: this FREES capital to TRADE — it is bot hygiene, never a
defensive throttle / size-cut / entry-block. DEMO/PAPER only; virtual funds.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from polaris.scripts.liquidate_alpaca_orphans import liquidate_alpaca_orphans
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures + adapter mock
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, is_open: bool) -> None:
        self.is_open = is_open


class _AlpacaMock:
    """Minimal Alpaca adapter mock: positions + a sell that always fills."""

    def __init__(
        self,
        *,
        positions: list[dict[str, Any]],
        is_open: bool = True,
        sell_ok: bool = True,
    ) -> None:
        self._positions = positions
        self._is_open = is_open
        self._sell_ok = sell_ok
        self.sell_orders: list[dict[str, Any]] = []

    async def fetch_clock(self) -> _Clock:
        return _Clock(self._is_open)

    async def fetch_positions(self) -> list[dict[str, Any]]:
        return list(self._positions)

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float | None = None,
        client_order_id: str,
        time_in_force: str = "day",
        **_: Any,
    ) -> Any:
        self.sell_orders.append(
            {"symbol": symbol, "side": side, "qty": qty, "cl": client_order_id,
             "tif": time_in_force}
        )

        ok = self._sell_ok

        class _Resp:
            def __init__(self) -> None:
                self.ok = ok
                self.venue_order_id = "alp_x" if ok else None
                self.client_order_id = client_order_id
                self.code = "accepted" if ok else "rejected"
                self.msg = ""
                self.raw: dict[str, Any] = {}

        return _Resp()


@pytest.fixture()
def conn(tmp_path: Path) -> Any:
    c = init_db(tmp_path / "liq.sqlite")
    yield c
    c.close()


def _insert_tracked(
    conn: sqlite3.Connection, *, symbol: str, status: str = "open"
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "exit_state) VALUES (?, 'alpaca', ?, 'equity_tsmom', 'equity_tsmom', "
        "'equity_tsmom', 'long', 5.0, ?, ?, 'open')",
        (f"pid_{symbol}", symbol, status, int(time.time())),
    )
    conn.commit()


def _venue_pos(symbol: str, qty: float) -> dict[str, Any]:
    return {"symbol": symbol, "qty": str(qty), "market_value": str(qty * 100.0),
            "asset_class": "us_equity"}


def _crypto_pos(symbol: str, qty: float) -> dict[str, Any]:
    return {"symbol": symbol, "qty": str(qty), "market_value": str(qty * 100.0),
            "asset_class": "crypto"}


# ---------------------------------------------------------------------------
# Core guard: tracked NEVER liquidated, untracked IS closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_untracked_venue_positions_are_closed(conn: sqlite3.Connection) -> None:
    """DB tracks 0 Alpaca → all venue positions are orphans → all sold (BP frees)."""
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0)]
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 2
    sold = {o["symbol"] for o in adapter.sell_orders}
    assert sold == {"NVDA", "ORCL"}
    for o in adapter.sell_orders:
        assert o["side"] == "sell"
    # Exact venue qty sold (no dust / oversell).
    by_sym = {o["symbol"]: o["qty"] for o in adapter.sell_orders}
    assert by_sym["NVDA"] == pytest.approx(10.0)
    assert by_sym["ORCL"] == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_tracked_position_is_never_liquidated(conn: sqlite3.Connection) -> None:
    """HARD GUARD: a venue symbol that IS in the bot's tracked-open set is NEVER
    sold — only the genuinely-untracked one is closed."""
    _insert_tracked(conn, symbol="NVDA", status="open")
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0)]
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 1
    sold = {o["symbol"] for o in adapter.sell_orders}
    assert sold == {"ORCL"}, "tracked NVDA must NOT be sold"


@pytest.mark.asyncio
async def test_active_status_is_also_treated_as_tracked(
    conn: sqlite3.Connection,
) -> None:
    """The tracked-open set covers both 'open' AND 'active' — neither is sold."""
    _insert_tracked(conn, symbol="NVDA", status="open")
    _insert_tracked(conn, symbol="ORCL", status="active")
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0),
                   _venue_pos("INTC", 7.0)]
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 1
    assert {o["symbol"] for o in adapter.sell_orders} == {"INTC"}


@pytest.mark.asyncio
async def test_closed_or_reconciled_rows_are_not_tracked(
    conn: sqlite3.Connection,
) -> None:
    """A 'closed'/'reconciled' DB row does NOT protect its symbol — it is no
    longer a live tracked position, so a same-symbol venue orphan IS closed."""
    _insert_tracked(conn, symbol="NVDA", status="closed")
    _insert_tracked(conn, symbol="ORCL", status="reconciled")
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0)]
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 2
    assert {o["symbol"] for o in adapter.sell_orders} == {"NVDA", "ORCL"}


# ---------------------------------------------------------------------------
# Safety branches: market closed, dry-run, empty, sell reject, audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_closed_submits_nothing(conn: sqlite3.Connection) -> None:
    """US market closed → no sell submitted (transient — retry next boot/open).
    A queued market order would just sit; we wait for RTH so the close fills."""
    adapter = _AlpacaMock(positions=[_venue_pos("NVDA", 10.0)], is_open=False)
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 0
    assert adapter.sell_orders == []


@pytest.mark.asyncio
async def test_dry_run_plans_but_submits_nothing(conn: sqlite3.Connection) -> None:
    """dry_run=True returns the orphan count but places NO order (audit/preview)."""
    adapter = _AlpacaMock(positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0)])
    n = await liquidate_alpaca_orphans(conn, adapter, dry_run=True)
    assert n == 2
    assert adapter.sell_orders == []


@pytest.mark.asyncio
async def test_empty_venue_is_noop(conn: sqlite3.Connection) -> None:
    adapter = _AlpacaMock(positions=[])
    assert await liquidate_alpaca_orphans(conn, adapter) == 0
    assert adapter.sell_orders == []


@pytest.mark.asyncio
async def test_sell_reject_does_not_count_and_does_not_abort(
    conn: sqlite3.Connection,
) -> None:
    """A venue reject on one close is best-effort: it is not counted as closed,
    and the sweep does not raise (flow_not_block — keep trying the rest)."""
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _venue_pos("ORCL", 4.0)],
        sell_ok=False,
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 0  # both rejected → none counted closed
    assert len(adapter.sell_orders) == 2  # but both were attempted


@pytest.mark.asyncio
async def test_writes_audit_row_per_closed_orphan(conn: sqlite3.Connection) -> None:
    adapter = _AlpacaMock(positions=[_venue_pos("NVDA", 10.0)])
    await liquidate_alpaca_orphans(conn, adapter)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM risk_events "
        "WHERE event_type = 'alpaca_orphan_liquidated'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["venue"] == "alpaca"
    assert payload["symbol"] == "NVDA"
    assert payload["qty"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_zero_qty_position_is_skipped(conn: sqlite3.Connection) -> None:
    """A venue position with qty<=0 (or unparseable) is skipped, never an
    oversell or a 0-qty order."""
    adapter = _AlpacaMock(positions=[_venue_pos("NVDA", 0.0), _venue_pos("ORCL", 3.0)])
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 1
    assert {o["symbol"] for o in adapter.sell_orders} == {"ORCL"}


@pytest.mark.asyncio
async def test_short_position_is_bought_to_close(conn: sqlite3.Connection) -> None:
    """A negative venue qty (short orphan) closes with a BUY of the abs qty —
    never an oversell, never a wrong-side sell that doubles the position."""
    adapter = _AlpacaMock(positions=[{"symbol": "TSLA", "qty": "-5.0",
                                      "asset_class": "us_equity"}])
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 1
    assert adapter.sell_orders[0]["side"] == "buy"
    assert adapter.sell_orders[0]["qty"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_equity_uses_day_tif_crypto_uses_gtc(
    conn: sqlite3.Connection,
) -> None:
    """A crypto orphan must close with TIF=gtc (Alpaca rejects 'day' for crypto
    — 'invalid crypto time_in_force'); an equity orphan stays TIF=day."""
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _crypto_pos("AAVEUSD", 2.0)]
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 2
    tif = {o["symbol"]: o["tif"] for o in adapter.sell_orders}
    assert tif["NVDA"] == "day"
    assert tif["AAVEUSD"] == "gtc"


@pytest.mark.asyncio
async def test_market_closed_still_closes_crypto(conn: sqlite3.Connection) -> None:
    """Equity session closed → equity orphan deferred, but crypto (24/7) still
    closes now (TIF=gtc)."""
    adapter = _AlpacaMock(
        positions=[_venue_pos("NVDA", 10.0), _crypto_pos("AAVEUSD", 2.0)],
        is_open=False,
    )
    n = await liquidate_alpaca_orphans(conn, adapter)
    assert n == 1
    assert {o["symbol"] for o in adapter.sell_orders} == {"AAVEUSD"}
    assert adapter.sell_orders[0]["tif"] == "gtc"
