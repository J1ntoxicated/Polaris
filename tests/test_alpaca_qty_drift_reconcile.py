"""Alpaca QTY-level venue-drift reconcile — spec item B (RCA A′ sibling).

DEMO/PAPER only, every adapter here is mocked; no real network call happens.
``reconcile_alpaca_venue_drift`` (existing) only catches a symbol the venue no
longer holds AT ALL. ``reconcile_alpaca_qty_drift`` is the finer-grained
sibling: for every symbol BOTH sides still hold,
``diff = venue_qty - Σ(internal open qty)``.

Live evidence (fix rollout, root cause = the confirm-poll partial-fill
truncation spec item A′ closes): GVH 206/internal-206-truncated,
MSM 9/11.97, PRGS 12/39.48, SIF 45/58.37, TENB 8/39.21, AAL 35/82.60 — the
internal ledger fell short of the venue's TRUE holding by the un-trued-up
partial-fill delta (~$4,042 untracked notional across the 6).

diff > 0 (venue holds MORE) → attribute to the position's OWN entry order
(fetch_order on fills.order_id); a growth FOLDS qty/fills/risk_usd (entry
price anchor kept) + a ``qty_trueup`` audit row. An untracked slice that
cannot be attributed to any tracked position's own order is left alone (the
adopt-import path's concern, never fabricated here).

diff < 0 (internal OVER-COUNTS) → CLAMP the internal qty down to the venue
total (never fabricates a fill) + a ``qty_drift_clamp`` audit row.

flow_not_block: never blocks a fresh entry; pure post-hoc ledger honesty.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from polaris.scripts.reconcile_alpaca_zombies import reconcile_alpaca_qty_drift
from polaris.storage.schema import init_db


class _MockAlpaca:
    def __init__(
        self, *, positions: list[dict[str, Any]],
        orders_by_id: dict[str, dict[str, Any]] | None = None,
        raise_on_fetch: bool = False,
    ) -> None:
        self._positions = positions
        self._orders_by_id = orders_by_id or {}
        self.raise_on_fetch = raise_on_fetch
        self.fetch_order_calls: list[str] = []

    async def fetch_positions(self) -> list[dict[str, Any]]:
        if self.raise_on_fetch:
            raise RuntimeError("transport down")
        return list(self._positions)

    async def fetch_order(self, *, order_id: str) -> dict[str, Any]:
        self.fetch_order_calls.append(order_id)
        return dict(self._orders_by_id.get(order_id, {}))


def _venue_pos(symbol: str, qty: float) -> dict[str, Any]:
    return {"symbol": symbol, "qty": str(qty)}


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "qtydrift.sqlite")
    yield c
    c.close()


def _seed_position_with_fill(
    conn: sqlite3.Connection, *, position_id: str, symbol: str, qty: float,
    order_id: str, fill_price: float = 5.0, risk_usd: float | None = 40.0,
) -> None:
    now = int(time.time())
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "risk_usd, exit_state) "
        "VALUES (?, 'alpaca', ?, 'equity_tsmom', 'equity_tsmom', 'equity_tsmom', "
        "'long', ?, 'open', ?, ?, 'open')",
        (position_id, symbol, qty, now, risk_usd),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id, contribution_id, is_close, "
        "base_qty, quote_qty, state) "
        "VALUES (?, 'alpaca', ?, 'equity_tsmom', 'buy', ?, ?, ?, ?, ?, 0, ?, ?, "
        "'filled')",
        (
            f"{position_id}:open", f"alpaca:{symbol}", qty * fill_price,
            fill_price, now * 1000, order_id, position_id, qty, qty * fill_price,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# diff > 0 — venue holds MORE than tracked → fold via the position's OWN order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_venue_surplus_folds_into_tracked_position(
    conn: sqlite3.Connection,
) -> None:
    """GVH-shaped: internal 206 (partial-fill truncated), venue TRUE 250 —
    the same order_id now reports filled_qty=250 → fold, never a phantom drop."""
    _seed_position_with_fill(
        conn, position_id="p1", symbol="GVH", qty=206.0, order_id="gvh_ord_1",
        fill_price=5.0, risk_usd=40.0,
    )
    adapter = _MockAlpaca(
        positions=[_venue_pos("GVH", 250.0)],
        orders_by_id={
            "gvh_ord_1": {"filled_qty": "250", "filled_avg_price": "5.00"},
        },
    )
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 1
    pos = conn.execute(
        "SELECT qty, risk_usd FROM positions WHERE position_id = 'p1'"
    ).fetchone()
    assert float(pos[0]) == pytest.approx(250.0)
    assert float(pos[1]) == pytest.approx(40.0 * (250.0 / 206.0))
    fill = conn.execute(
        "SELECT base_qty, size_usd, quote_qty FROM fills "
        "WHERE order_id = 'gvh_ord_1' AND is_close = 0"
    ).fetchone()
    assert float(fill[0]) == pytest.approx(250.0)
    assert float(fill[1]) == pytest.approx(250.0 * 5.0)
    audit = conn.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'qty_trueup'"
    ).fetchone()
    assert audit is not None
    payload = json.loads(audit[0])
    assert payload["symbol"] == "GVH"
    assert payload["old_qty"] == pytest.approx(206.0)
    assert payload["new_qty"] == pytest.approx(250.0)


@pytest.mark.asyncio
async def test_unattributable_venue_surplus_left_untouched(
    conn: sqlite3.Connection,
) -> None:
    """The tracked position's OWN order never grew past its internal qty — an
    extra untracked slice at the venue is NOT fabricated into this position
    (the adopt-import path's concern, never invented here)."""
    _seed_position_with_fill(
        conn, position_id="p1", symbol="SIF", qty=45.0, order_id="sif_ord_1",
        fill_price=10.0,
    )
    adapter = _MockAlpaca(
        positions=[_venue_pos("SIF", 58.37)],
        orders_by_id={
            "sif_ord_1": {"filled_qty": "45", "filled_avg_price": "10.00"},
        },
    )
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 0
    pos = conn.execute(
        "SELECT qty FROM positions WHERE position_id = 'p1'"
    ).fetchone()
    assert float(pos[0]) == pytest.approx(45.0)  # unchanged


# ---------------------------------------------------------------------------
# diff < 0 — internal OVER-COUNTS the venue → clamp down (never fabricate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_overcount_clamped_to_venue_qty(
    conn: sqlite3.Connection,
) -> None:
    _seed_position_with_fill(
        conn, position_id="p1", symbol="AAL", qty=82.60, order_id="aal_ord_1",
        fill_price=15.0, risk_usd=60.0,
    )
    adapter = _MockAlpaca(positions=[_venue_pos("AAL", 35.0)])
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 1
    pos = conn.execute(
        "SELECT qty, risk_usd FROM positions WHERE position_id = 'p1'"
    ).fetchone()
    assert float(pos[0]) == pytest.approx(35.0)
    assert float(pos[1]) == pytest.approx(60.0 * (35.0 / 82.60))
    audit = conn.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'qty_drift_clamp'"
    ).fetchone()
    assert audit is not None
    payload = json.loads(audit[0])
    assert payload["symbol"] == "AAL"
    assert payload["old_qty"] == pytest.approx(82.60)
    assert payload["new_qty"] == pytest.approx(35.0)
    # NO fill fabricated — the fills row for the entry order is untouched.
    fill = conn.execute(
        "SELECT base_qty FROM fills WHERE order_id = 'aal_ord_1' AND is_close = 0"
    ).fetchone()
    assert float(fill[0]) == pytest.approx(82.60)


# ---------------------------------------------------------------------------
# dust / no-drift / idempotent / fail-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_qty_is_a_noop(conn: sqlite3.Connection) -> None:
    _seed_position_with_fill(
        conn, position_id="p1", symbol="TENB", qty=39.21, order_id="tenb_ord_1",
    )
    adapter = _MockAlpaca(positions=[_venue_pos("TENB", 39.21)])
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 0
    assert adapter.fetch_order_calls == []


@pytest.mark.asyncio
async def test_sub_dust_diff_is_ignored(conn: sqlite3.Connection) -> None:
    """A drift under the $5 dust floor (at the entry price) is left alone —
    float/rounding noise, not a real fold/clamp-worthy drift."""
    _seed_position_with_fill(
        conn, position_id="p1", symbol="MSM", qty=11.97, order_id="msm_ord_1",
        fill_price=1.0,
    )
    adapter = _MockAlpaca(positions=[_venue_pos("MSM", 11.99)])  # $0.02 drift
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 0


@pytest.mark.asyncio
async def test_symbol_with_no_tracked_position_is_skipped(
    conn: sqlite3.Connection,
) -> None:
    """A venue symbol with ZERO tracked positions is not this function's
    concern — the whole-qty untracked orphan is the adopt-import path's job,
    never fabricated here."""
    adapter = _MockAlpaca(positions=[_venue_pos("PRGS", 39.48)])
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 0


@pytest.mark.asyncio
async def test_fetch_positions_failure_skips_whole_pass(
    conn: sqlite3.Connection,
) -> None:
    _seed_position_with_fill(
        conn, position_id="p1", symbol="GVH", qty=206.0, order_id="gvh_ord_1",
    )
    adapter = _MockAlpaca(positions=[], raise_on_fetch=True)
    touched = await reconcile_alpaca_qty_drift(conn, adapter)
    assert touched == 0
    pos = conn.execute(
        "SELECT qty FROM positions WHERE position_id = 'p1'"
    ).fetchone()
    assert float(pos[0]) == pytest.approx(206.0)  # untouched, never blind


@pytest.mark.asyncio
async def test_idempotent_second_pass_touches_zero(
    conn: sqlite3.Connection,
) -> None:
    _seed_position_with_fill(
        conn, position_id="p1", symbol="GVH", qty=206.0, order_id="gvh_ord_1",
        fill_price=5.0,
    )
    adapter = _MockAlpaca(
        positions=[_venue_pos("GVH", 250.0)],
        orders_by_id={
            "gvh_ord_1": {"filled_qty": "250", "filled_avg_price": "5.00"},
        },
    )
    first = await reconcile_alpaca_qty_drift(conn, adapter)
    assert first == 1
    second = await reconcile_alpaca_qty_drift(conn, adapter)
    assert second == 0
