"""Unified cross-venue orphan reconcile + EOD flatten (OKX + Capital + Alpaca).

Sibling of ``test_liquidate_alpaca_orphans`` — the same load-bearing guard
(tracked-open set protects live positions; only UNTRACKED venue holdings are
closed) now applied across all 3 venues, plus EOD flatten of TRACKED positions
before each venue's close (OKX 24/7 EXEMPT; hold_overnight opt-out; default =
flatten). DEMO/PAPER, flow_not_block — never an entry block or size cut.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.scripts.reconcile_orphans import (
    EOD_EXEMPT_VENUES,
    flatten_venue_eod,
    reconcile_venue_orphans,
    reset_alpaca_liquidation_guard,
)
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, *, is_open: bool, next_close: str) -> None:
        self.is_open = is_open
        self.next_close = next_close


class _AlpacaMock:
    def __init__(
        self,
        *,
        positions: list[dict[str, Any]],
        is_open: bool = True,
        next_close: str = "",
        order_ok: bool = True,
        fail_fetch: bool = False,
        own_coid_symbols: frozenset[str] = frozenset(),
    ) -> None:
        self._positions = positions
        self._is_open = is_open
        self._next_close = next_close
        self._order_ok = order_ok
        self._fail_fetch = fail_fetch
        # Sweep coid-ownership guard (spec item C): symbols in here report a
        # 'polA*' (our own) client_order_id on their most recent closed order
        # — an adopt candidate, never liquidated. Every OTHER symbol reports a
        # foreign coid (the pre-existing test default: a genuine orphan).
        self._own_coid_symbols = own_coid_symbols
        self.orders: list[dict[str, Any]] = []

    async def fetch_clock(self) -> _Clock:
        return _Clock(is_open=self._is_open, next_close=self._next_close)

    async def fetch_positions(self) -> list[dict[str, Any]]:
        if self._fail_fetch:
            raise RuntimeError("alpaca down")
        return list(self._positions)

    async def fetch_orders(
        self, *, symbol: str, status: str = "closed", limit: int = 50,
    ) -> list[dict[str, Any]]:
        coid = "polAxyz" if symbol in self._own_coid_symbols else "foreign_manual_1"
        return [{"client_order_id": coid, "symbol": symbol, "status": "filled"}]

    async def place_market_order(
        self, *, symbol: str, side: str, qty: float | None = None,
        client_order_id: str, time_in_force: str = "day", **_: Any,
    ) -> Any:
        self.orders.append(
            {"symbol": symbol, "side": side, "qty": qty, "tif": time_in_force,
             "cl": client_order_id}
        )
        ok = self._order_ok

        class _R:
            def __init__(self) -> None:
                self.ok = ok
                self.venue_order_id = "alp_x" if ok else None
                self.code = "accepted" if ok else "rejected"
                self.msg = ""
        return _R()


class _OKXMock:
    def __init__(
        self, *, details: list[dict[str, Any]], order_ok: bool = True,
        fail_fetch: bool = False,
    ) -> None:
        self._details = details
        self._order_ok = order_ok
        self._fail_fetch = fail_fetch
        self.orders: list[dict[str, Any]] = []

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        if self._fail_fetch:
            raise RuntimeError("okx down")
        return {"data": [{"details": list(self._details)}]}

    async def place_market_order(
        self, *, inst_id: str, side: str, notional_usd: float,
        client_order_id: str, **_: Any,
    ) -> Any:
        self.orders.append({"inst": inst_id, "side": side, "notional": notional_usd})
        ok = self._order_ok

        class _R:
            def __init__(self) -> None:
                self.ok = ok
                self.venue_order_id = "okx_x" if ok else None
                self.code = "0" if ok else "1"
                self.msg = ""
        return _R()


class _CapitalMock:
    def __init__(
        self, *, positions: list[dict[str, Any]], close_ok: bool = True,
        fail_list: bool = False,
    ) -> None:
        self._positions = positions
        self._close_ok = close_ok
        self._fail_list = fail_list
        self.closed: list[str] = []

    async def list_positions(self) -> dict[str, Any]:
        if self._fail_list:
            raise RuntimeError("capital down")
        return {"positions": list(self._positions)}

    async def close_position(self, deal_id: str) -> Any:
        self.closed.append(deal_id)
        ok = self._close_ok

        class _R:
            def __init__(self) -> None:
                self.ok = ok
                self.status = "CLOSED" if ok else "REJECTED"
                self.reason = ""
        return _R()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path) -> Any:
    c = init_db(tmp_path / "rec.sqlite")
    yield c
    c.close()


def _track(conn: sqlite3.Connection, *, venue: str, symbol: str,
           status: str = "open", strategy_id: str = "equity_tsmom") -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "exit_state) VALUES (?, ?, ?, ?, ?, ?, 'long', 5.0, ?, ?, 'open')",
        (f"pid_{venue}_{symbol}", venue, symbol, strategy_id, strategy_id,
         strategy_id, status, int(time.time())),
    )
    conn.commit()


def _alpaca_pos(symbol: str, qty: float, asset_class: str = "us_equity") -> dict[str, Any]:
    return {"symbol": symbol, "qty": str(qty), "asset_class": asset_class}


def _okx_bag(ccy: str, avail: float, eq_usd: float) -> dict[str, Any]:
    return {"ccy": ccy, "availBal": str(avail), "eqUsd": str(eq_usd)}


def _cap_pos(epic: str, deal_id: str, direction: str = "BUY",
             size: float = 1.0) -> dict[str, Any]:
    return {"position": {"dealId": deal_id, "direction": direction, "size": size},
            "market": {"epic": epic}}


# ===========================================================================
# (1) ORPHAN RECONCILE — tracked guard across all 3 venues
# ===========================================================================


@pytest.mark.asyncio
async def test_alpaca_untracked_closed_tracked_protected(conn: sqlite3.Connection) -> None:
    reset_alpaca_liquidation_guard()
    _track(conn, venue="alpaca", symbol="NVDA")
    alp = _AlpacaMock(positions=[_alpaca_pos("NVDA", 10.0), _alpaca_pos("ORCL", 4.0)])
    # Coid-ownership guard (spec C): a foreign-coid orphan is liquidated only
    # after 2 CONSECUTIVE unattributed sweep cycles — the first sweep defers.
    first = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert first["alpaca"] == 0
    assert alp.orders == []
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert counts["alpaca"] == 1
    assert {o["symbol"] for o in alp.orders} == {"ORCL"}


@pytest.mark.asyncio
async def test_okx_untracked_bag_sold_tracked_protected(conn: sqlite3.Connection) -> None:
    """OKX gains the tracked guard it lacked: a tracked SPOT long is never sold;
    only an untracked altcoin bag is liquidated to USDT."""
    _track(conn, venue="okx", symbol="ETH-USDT", strategy_id="spot_donchian")
    okx = _OKXMock(details=[
        _okx_bag("USDT", 1000.0, 1000.0),  # quote — skipped
        _okx_bag("ETH", 2.0, 5000.0),      # TRACKED (ETH-USDT) — protected
        _okx_bag("DOGE", 100.0, 50.0),     # untracked orphan bag — sold
        _okx_bag("SHIB", 1.0, 2.0),        # dust < $5 — skipped
    ])
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=okx, capital_adapter=None, alpaca_adapter=None,
    )
    assert counts["okx"] == 1
    assert [o["inst"] for o in okx.orders] == ["DOGE-USDT"]
    assert okx.orders[0]["side"] == "sell"


@pytest.mark.asyncio
async def test_capital_untracked_closed_tracked_protected(conn: sqlite3.Connection) -> None:
    """NEW Capital branch: close an untracked orphan by dealId; a tracked epic is
    never closed."""
    _track(conn, venue="capital", symbol="GOLD", strategy_id="xau_indices_trend")
    cap = _CapitalMock(positions=[
        _cap_pos("GOLD", "deal_gold"),   # TRACKED — protected
        _cap_pos("US500", "deal_us500"), # untracked orphan — closed by dealId
    ])
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=cap, alpaca_adapter=None,
    )
    assert counts["capital"] == 1
    assert cap.closed == ["deal_us500"]


@pytest.mark.asyncio
async def test_idempotent_rerun_after_clean_sweep_closes_zero(
    conn: sqlite3.Connection,
) -> None:
    reset_alpaca_liquidation_guard()
    alp = _AlpacaMock(positions=[_alpaca_pos("ORCL", 4.0)])
    # 2 sweeps needed before a foreign-coid orphan is liquidation-eligible.
    first = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert first["alpaca"] == 0
    second = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert second["alpaca"] == 1
    # Venue now flat (sweep cleared the orphan) → a re-run closes 0.
    alp._positions = []
    third = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert third["alpaca"] == 0


@pytest.mark.asyncio
async def test_per_venue_fetch_error_skips_only_that_venue(
    conn: sqlite3.Connection,
) -> None:
    """A venue read failure SKIPS that venue (returns 0) and NEVER blind-closes;
    the other venues still sweep (per-venue isolation, never assume-empty)."""
    reset_alpaca_liquidation_guard()
    alp = _AlpacaMock(positions=[_alpaca_pos("ORCL", 4.0)])
    okx = _OKXMock(details=[], fail_fetch=True)
    cap = _CapitalMock(positions=[], fail_list=True)
    # 2 sweeps to clear the coid-ownership miss threshold on the healthy venue.
    await reconcile_venue_orphans(
        conn, okx_adapter=okx, capital_adapter=cap, alpaca_adapter=alp,
    )
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=okx, capital_adapter=cap, alpaca_adapter=alp,
    )
    assert counts["okx"] == 0  # skipped, not blind-closed
    assert counts["capital"] == 0
    assert counts["alpaca"] == 1  # healthy venue still swept
    assert okx.orders == []
    assert cap.closed == []


@pytest.mark.asyncio
async def test_dry_run_submits_nothing(conn: sqlite3.Connection) -> None:
    reset_alpaca_liquidation_guard()
    alp = _AlpacaMock(positions=[_alpaca_pos("ORCL", 4.0)])
    cap = _CapitalMock(positions=[_cap_pos("US500", "d1")])
    # First sweep only registers the coid-ownership miss (deferred, dry_run=0);
    # the second crosses the threshold and dry_run reports the would-close.
    await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=cap, alpaca_adapter=alp,
        dry_run=True,
    )
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=cap, alpaca_adapter=alp,
        dry_run=True,
    )
    assert counts["alpaca"] == 1 and counts["capital"] == 1
    assert alp.orders == [] and cap.closed == []


@pytest.mark.asyncio
async def test_capital_close_writes_audit_with_dealid(conn: sqlite3.Connection) -> None:
    cap = _CapitalMock(positions=[_cap_pos("US500", "deal_xyz")])
    await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=cap, alpaca_adapter=None,
    )
    rows = conn.execute(
        "SELECT payload_json FROM risk_events "
        "WHERE event_type = 'capital_orphan_liquidated'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["venue"] == "capital"
    assert payload["deal_id"] == "deal_xyz"


# ===========================================================================
# (1b) SWEEP COID-OWNERSHIP GUARD — spec item C. Live incident: a self-sell of
# our OWN ``polA*`` open orders was misread as a foreign orphan and liquidated
# 7 times without an R-ledger update (13:40 UTC). This guard makes that class
# of self-liquidation structurally impossible: a symbol with a live
# ``pending_opens`` ref is never touched; an untracked symbol whose own most
# recent closed order carries our ``polA*`` coid prefix is an adopt candidate,
# never liquidated on sight; only a symbol that is either confirmed
# coid-FOREIGN or unattributed for 2 CONSECUTIVE sweeps is liquidated.
# ===========================================================================


@pytest.mark.asyncio
async def test_pending_open_ref_symbol_never_liquidated(
    conn: sqlite3.Connection,
) -> None:
    """A symbol with a still-live pending_opens ref (open-confirm/true-up in
    flight) is skipped OUTRIGHT — even across many sweeps — never becomes a
    liquidation candidate via the miss counter."""
    reset_alpaca_liquidation_guard()
    conn.execute(
        "INSERT INTO pending_opens (venue, symbol, strategy_id, side, "
        "venue_order_id, client_order_id, notional_usd, last_price, state, "
        "position_id, created_ts, updated_ts) VALUES ('alpaca', 'ENTX', "
        "'equity_tsmom', 'long', 'ord1', 'polAabc', 4000.0, 5.0, "
        "'partial_trueup', 'posX', ?, ?)",
        (int(time.time()), int(time.time())),
    )
    conn.commit()
    alp = _AlpacaMock(positions=[_alpaca_pos("ENTX", 400.0)])
    for _ in range(3):
        counts = await reconcile_venue_orphans(
            conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
        )
        assert counts["alpaca"] == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_coid_owned_untracked_symbol_is_adopt_candidate_not_liquidated(
    conn: sqlite3.Connection,
) -> None:
    """An untracked Alpaca symbol whose own most recent closed order carries
    OUR 'polA*' coid is NEVER liquidated (any number of sweeps) — it is a
    fold/adopt concern for the B/import paths, structurally distinct from a
    genuinely foreign orphan."""
    reset_alpaca_liquidation_guard()
    alp = _AlpacaMock(
        positions=[_alpaca_pos("GVH", 250.0)],
        own_coid_symbols=frozenset({"GVH"}),
    )
    for _ in range(3):
        counts = await reconcile_venue_orphans(
            conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
        )
        assert counts["alpaca"] == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_foreign_coid_orphan_still_liquidates_after_two_misses(
    conn: sqlite3.Connection,
) -> None:
    """A genuinely foreign-coid untracked symbol IS liquidated — just gated
    behind 2 consecutive unattributed sweeps rather than on sight."""
    reset_alpaca_liquidation_guard()
    alp = _AlpacaMock(positions=[_alpaca_pos("MANUAL1", 10.0)])
    first = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert first["alpaca"] == 0
    second = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert second["alpaca"] == 1
    assert {o["symbol"] for o in alp.orders} == {"MANUAL1"}


@pytest.mark.asyncio
async def test_coid_lookup_failure_defers_to_miss_counter_never_blind_liquidates(
    conn: sqlite3.Connection,
) -> None:
    """A coid-ownership READ failure never blind-liquidates on sight — it
    degrades to the same miss-counter path as an unattributed symbol."""
    reset_alpaca_liquidation_guard()

    class _NoFetchOrders(_AlpacaMock):
        async def fetch_orders(self, **_: Any) -> list[dict[str, Any]]:
            raise RuntimeError("orders endpoint down")

    alp = _NoFetchOrders(positions=[_alpaca_pos("XYZ", 3.0)])
    first = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert first["alpaca"] == 0
    second = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert second["alpaca"] == 1


@pytest.mark.asyncio
async def test_new_entry_signal_never_blocked_by_qty_drift_or_sweep_guard(
    conn: sqlite3.Connection,
) -> None:
    """flow_not_block sanity: the coid-ownership guard only affects the
    orphan-LIQUIDATION sweep's own decision to close/defer — it has no
    tracked-set/entry-path side effect (a fresh signal on an unrelated symbol
    is fully independent, verified here by asserting the tracked guard still
    protects a live position exactly as before)."""
    reset_alpaca_liquidation_guard()
    _track(conn, venue="alpaca", symbol="NVDA")
    alp = _AlpacaMock(positions=[_alpaca_pos("NVDA", 10.0)])
    counts = await reconcile_venue_orphans(
        conn, okx_adapter=None, capital_adapter=None, alpaca_adapter=alp,
    )
    assert counts["alpaca"] == 0
    assert alp.orders == []


# ===========================================================================
# (2) EOD FLATTEN — default flatten, hold opt-out, OKX exempt, guards
# ===========================================================================


def _state_with(trades: list[SimulatedTrade]) -> ProdLoopState:
    st = ProdLoopState()
    st.open_trades = list(trades)
    return st


def _trade(*, venue: str, symbol: str, strategy_id: str,
           deal_id: str | None = None, base_qty: float = 3.0,
           side: str = "long") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id="s", venue=venue, symbol=symbol, strategy_id=strategy_id,
        side=side, entry_price=100.0, notional_usd=300.0, open_ts=0,
        position_id=f"pid_{venue}_{symbol}", deal_id=deal_id, base_qty=base_qty,
    )


# A UTC timestamp 5 min before Alpaca's next_close (inside the 15-min lead).
_NEXT_CLOSE_ISO = "2026-06-23T20:00:00Z"
_NOW_INSIDE = 1782244500  # 2026-06-23T19:55:00Z (5 min before close)
_NOW_OUTSIDE = 1782240900  # 2026-06-23T18:55:00Z (65 min before close)


@pytest.mark.asyncio
async def test_eod_flattens_tracked_alpaca_in_window(conn: sqlite3.Connection) -> None:
    _track(conn, venue="alpaca", symbol="NVDA", strategy_id="equity_tsmom")
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="NVDA",
                                strategy_id="equity_tsmom", base_qty=3.0)])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 1
    assert alp.orders[0]["symbol"] == "NVDA"
    assert alp.orders[0]["side"] == "sell"  # long → sell to flatten
    assert alp.orders[0]["qty"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_eod_does_not_flatten_outside_window(conn: sqlite3.Connection) -> None:
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="NVDA",
                                strategy_id="equity_tsmom")])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_OUTSIDE,
    )
    assert n == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_hold_overnight_strategy_is_not_flattened(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strategy whose metadata.hold_overnight=True KEEPS its position overnight;
    a default (False) strategy in the same window flattens."""
    import polaris.scripts.reconcile_orphans as mod

    class _SwingMeta:
        hold_overnight = True

    class _Swing:
        metadata = _SwingMeta()

    monkeypatch.setitem(mod.STRATEGY_REGISTRY, "swing_strat", _Swing)
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([
        _trade(venue="alpaca", symbol="HELD", strategy_id="swing_strat"),
        _trade(venue="alpaca", symbol="FLAT", strategy_id="equity_tsmom"),
    ])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 1
    assert {o["symbol"] for o in alp.orders} == {"FLAT"}


@pytest.mark.asyncio
async def test_unknown_strategy_defaults_to_flatten(conn: sqlite3.Connection) -> None:
    """Unknown / _reconcile_import strategy_id → flatten (the directive's base)."""
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="X",
                                strategy_id="_reconcile_import")])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 1


@pytest.mark.asyncio
async def test_okx_crypto_is_never_eod_flattened(conn: sqlite3.Connection) -> None:
    """HARD EXEMPT: an OKX tracked position is NEVER flattened (24/7 crypto)."""
    assert "okx" in EOD_EXEMPT_VENUES
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="okx", symbol="BTC-USDT",
                                strategy_id="spot_donchian")])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_eod_market_closed_submits_nothing(conn: sqlite3.Connection) -> None:
    """Equity session already closed → no blind market order queued (defer)."""
    alp = _AlpacaMock(positions=[], is_open=False, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="NVDA",
                                strategy_id="equity_tsmom")])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_eod_clock_error_submits_nothing(conn: sqlite3.Connection) -> None:
    """A clock fetch failure → SKIP (never flatten blind on an outage)."""
    class _BadClock(_AlpacaMock):
        async def fetch_clock(self) -> _Clock:
            raise RuntimeError("clock down")

    alp = _BadClock(positions=[], next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="NVDA",
                                strategy_id="equity_tsmom")])
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n == 0
    assert alp.orders == []


@pytest.mark.asyncio
async def test_eod_idempotent_second_tick_in_window_is_noop(
    conn: sqlite3.Connection,
) -> None:
    """After the first flatten flips the trade closing, a re-tick inside the same
    window submits nothing more (idempotent)."""
    _track(conn, venue="alpaca", symbol="NVDA", strategy_id="equity_tsmom")
    alp = _AlpacaMock(positions=[], is_open=True, next_close=_NEXT_CLOSE_ISO)
    state = _state_with([_trade(venue="alpaca", symbol="NVDA",
                                strategy_id="equity_tsmom")])
    n1 = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    n2 = await flatten_venue_eod(
        conn, state, alpaca_adapter=alp, capital_adapter=None,
        lead_sec=900, now_ts=_NOW_INSIDE,
    )
    assert n1 == 1
    assert n2 == 0
    assert len(alp.orders) == 1


@pytest.mark.asyncio
async def test_eod_capital_closes_by_dealid_in_window(conn: sqlite3.Connection) -> None:
    """A tracked Capital index position inside its session-close window flattens
    via close_position(dealId)."""
    _track(conn, venue="capital", symbol="US500", strategy_id="xau_indices_trend")
    cap = _CapitalMock(positions=[])
    state = _state_with([_trade(venue="capital", symbol="US500",
                                strategy_id="xau_indices_trend",
                                deal_id="deal_us500")])
    # 2026-06-23T20:50:00Z = 10 min before the 21:00 UTC index close.
    now_capital = 1782247800
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=None, capital_adapter=cap,
        lead_sec=900, now_ts=now_capital,
    )
    assert n == 1
    assert cap.closed == ["deal_us500"]


# ---------------------------------------------------------------------------
# (2b) EOD FLATTEN — PER-EPIC openingHours primary (fixture-backed)
# ---------------------------------------------------------------------------

_CAP_FIXTURES = Path(__file__).parent / "fixtures" / "capital_markets"


def _seed_constraint(state: ProdLoopState, epic: str) -> None:
    """Parse a REAL fixture into a CapitalMarketConstraint (with openingHours)
    and seed it into the loop-state cache exactly as the live fetch would."""
    from polaris.venues.capital.constraint_translator import payload_to_constraint

    body = json.loads((_CAP_FIXTURES / f"{epic}.json").read_text())
    constraint = payload_to_constraint(epic, body)
    # Mirror CapitalConstraintCache internal layout: {epic: (constraint, ts)}.
    state.capital_constraints._entries[epic] = (constraint, time.monotonic())


def _utc_ts(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> int:
    import datetime as _dt

    return int(_dt.datetime(y, mo, d, h, mi, s, tzinfo=_dt.UTC).timestamp())


@pytest.mark.asyncio
async def test_eod_capital_per_epic_us500_primary(conn: sqlite3.Connection) -> None:
    """PRIMARY = per-epic openingHours: US500 (close 21:00) seeded, now 20:55 UTC
    (inside the 900s lead) → flattens via the per-epic probe, not the table."""
    _track(conn, venue="capital", symbol="US500", strategy_id="xau_indices_trend")
    cap = _CapitalMock(positions=[])
    state = _state_with([_trade(venue="capital", symbol="US500",
                                strategy_id="xau_indices_trend",
                                deal_id="deal_us500")])
    _seed_constraint(state, "US500")
    now = _utc_ts(2026, 6, 22, 20, 55)  # Mon, 5 min before 21:00
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=None, capital_adapter=cap,
        lead_sec=900, now_ts=now,
    )
    assert n == 1
    assert cap.closed == ["deal_us500"]


@pytest.mark.asyncio
async def test_eod_capital_per_epic_gold_defers_past_own_close(
    conn: sqlite3.Connection,
) -> None:
    """GOLD closes at 20:59 (per-epic) — at 20:59:30 it is PAST its own close and
    inside the 20:59->22:00 break → per-epic probe None → NOT flattened. The
    legacy commodity table (21:00) WOULD have flattened here — proves the fix."""
    _track(conn, venue="capital", symbol="GOLD", strategy_id="xau_indices_trend")
    cap = _CapitalMock(positions=[])
    state = _state_with([_trade(venue="capital", symbol="GOLD",
                                strategy_id="xau_indices_trend",
                                deal_id="deal_gold")])
    _seed_constraint(state, "GOLD")
    now = _utc_ts(2026, 6, 22, 20, 59, 30)  # Mon, 30s after the 20:59 close
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=None, capital_adapter=cap,
        lead_sec=900, now_ts=now,
    )
    assert n == 0
    assert cap.closed == []


@pytest.mark.asyncio
async def test_eod_capital_fx_eurusd_exempt_intraday(conn: sqlite3.Connection) -> None:
    """EURUSD (CURRENCIES) per-epic probe is 24/5-exempt → None intraday → NOT
    flattened (the legacy Friday-weekly FX branch owns FX EOD)."""
    _track(conn, venue="capital", symbol="EURUSD", strategy_id="fx_trend")
    cap = _CapitalMock(positions=[])
    state = _state_with([_trade(venue="capital", symbol="EURUSD",
                                strategy_id="fx_trend", deal_id="deal_eur")])
    _seed_constraint(state, "EURUSD")
    now = _utc_ts(2026, 6, 24, 12, 0)  # Wed midday
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=None, capital_adapter=cap,
        lead_sec=900, now_ts=now,
    )
    assert n == 0
    assert cap.closed == []


@pytest.mark.asyncio
async def test_eod_capital_cold_miss_falls_back_to_asset_class(
    conn: sqlite3.Connection,
) -> None:
    """Constraint not cached for the epic (lazy cold-miss, peek==None) → graceful
    fall-through to the legacy asset-class table — byte-identical to today, no
    exception, the position still flattens on the coarse 21:00 close."""
    _track(conn, venue="capital", symbol="US500", strategy_id="xau_indices_trend")
    cap = _CapitalMock(positions=[])
    state = _state_with([_trade(venue="capital", symbol="US500",
                                strategy_id="xau_indices_trend",
                                deal_id="deal_us500")])
    # No _seed_constraint → peek() returns None → legacy path.
    now = _utc_ts(2026, 6, 22, 20, 50)  # 10 min before the table's 21:00 close
    n = await flatten_venue_eod(
        conn, state, alpaca_adapter=None, capital_adapter=cap,
        lead_sec=900, now_ts=now,
    )
    assert n == 1
    assert cap.closed == ["deal_us500"]
