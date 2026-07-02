"""Alpaca OPEN-side pending-confirm carryover — the open-confirm fix (Track C).

DEMO/PAPER only — every adapter here is mocked; no real network call happens.
Aggressive bias intact: this NEVER blocks/dampens a genuine new signal — it
only stops a SECOND order from being placed while a prior tick's accepted
order at THIS venue+symbol+strategy+side is still unconfirmed, and confirms
that exact order first so a late fill is captured (more confirmed entries,
not fewer).

Live root cause (2026-07-02 13:31-33 UTC open): a market BUY POSTs
``ok=True code=pending_new`` (open-auction delay); the confirm-poll budget
expired before a fill landed, the order was released as a passive orphan
record, and the still-live BUY filled seconds later — untracked until the
next boot's adopt-import sweep. Meanwhile a later signal on the same key
could submit a DUPLICATE buy. This suite covers the mission's (a)-(e) plan.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.scripts._alpaca_pending_open import read_pending_open
from polaris.scripts._production_pipeline import reserve_and_submit as _reserve_and_submit
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal


def _eq_sig(signal_id: str = "eq_sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="equity_tsmom", symbol="ENTX",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )


class _RespOk:
    def __init__(self, venue_order_id: str = "entx_ord_1") -> None:
        self.ok = True
        self.venue_order_id = venue_order_id
        self.client_order_id = "clA"
        self.code = "accepted"
        self.msg = "accepted"
        self.raw: dict[str, Any] = {}


def _row(status: str, qty: str = "797", px: str = "5.00") -> dict[str, Any]:
    r: dict[str, Any] = {"id": "entx_ord_1", "symbol": "ENTX", "side": "buy",
                          "status": status}
    if status in ("filled", "partially_filled"):
        r["filled_qty"] = qty
        r["filled_avg_price"] = px
    return r


def _adapter_never_fills() -> AsyncMock:
    """Every poll across the WHOLE budget reports ``new`` (no fill)."""
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_RespOk())
    adapter.fetch_order = AsyncMock(return_value=_row("new"))
    adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    return adapter


def _adapter_fills_on_pending_check() -> AsyncMock:
    """Any fetch_order call (the pending-resolve check) reports filled — used
    for the tick-2 confirm-first scenario; place_market_order must NEVER be
    called (would be the duplicate-submit bug)."""
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_RespOk())
    adapter.fetch_order = AsyncMock(return_value=_row("filled"))
    adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    return adapter


# ---------------------------------------------------------------------------
# (a) pending_new → next tick filled: no give-up, a positions row IS created.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_open_confirms_and_persists_on_next_tick(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())

    # Tick 1: accepted but never confirms within the poll budget — no give-up
    # (a fault-free skip), the pending ref is persisted for next tick.
    tick1_adapter = _adapter_never_fills()
    trade1 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig1"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now,
        real_roundtrip=True, alpaca_adapter=tick1_adapter,
    )
    assert trade1 is None
    assert state.fault_events == 0
    pending = read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    )
    assert pending is not None
    assert pending.venue_order_id == "entx_ord_1"
    # Sized intent (mission ①: venue order id + clOrdId + sized intent) is
    # persisted, not just the bare venue ref.
    assert pending.client_order_id is not None and pending.client_order_id.startswith("polA")
    assert pending.notional_usd == pytest.approx(4000.0)
    assert pending.last_price == pytest.approx(5.0)

    # Tick 2: a NEW signal fires on the same key; the pending ref confirms
    # FIRST (fills) — the caller must NOT give up; a positions row is created.
    tick2_adapter = _adapter_fills_on_pending_check()
    trade2 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig2"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now + 5,
        real_roundtrip=True, alpaca_adapter=tick2_adapter,
    )
    assert trade2 is not None
    # No duplicate order placed — the pending order's OWN fill was used.
    tick2_adapter.place_market_order.assert_not_awaited()
    pos_row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?",
        (trade2.position_id,),
    ).fetchone()
    assert pos_row is not None and pos_row[0] == "open"
    fill_row = memdb.execute(
        "SELECT order_id FROM fills WHERE contribution_id = ? AND is_close = 0",
        (trade2.position_id,),
    ).fetchone()
    assert fill_row is not None and fill_row[0] == "entx_ord_1"
    # Pending ref cleared once confirmed.
    assert read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    ) is None


# ---------------------------------------------------------------------------
# (b) still pending next tick too → ref kept, ZERO duplicate submit.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_open_still_live_next_tick_no_duplicate_submit(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())

    tick1_adapter = _adapter_never_fills()
    trade1 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig1"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now,
        real_roundtrip=True, alpaca_adapter=tick1_adapter,
    )
    assert trade1 is None

    # Tick 2: STILL live (never fills) — must NOT submit a second order.
    tick2_adapter = _adapter_never_fills()
    trade2 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig2"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now + 5,
        real_roundtrip=True, alpaca_adapter=tick2_adapter,
    )
    assert trade2 is None
    tick2_adapter.place_market_order.assert_not_awaited()
    pending = read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    )
    assert pending is not None and pending.venue_order_id == "entx_ord_1"
    # Zero positions rows created across both ticks.
    count = memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert int(count) == 0


# ---------------------------------------------------------------------------
# (c) final canceled/rejected → ref cleared + reject-anchor stamped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_open_terminal_reject_clears_ref_and_stamps_anchor(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())

    tick1_adapter = _adapter_never_fills()
    trade1 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig1"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now,
        real_roundtrip=True, alpaca_adapter=tick1_adapter,
    )
    assert trade1 is None
    assert read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    ) is not None

    # Tick 2: the venue reports the pending order CANCELED (terminal, zero
    # fill) — the ref clears and (since a fresh submit is then attempted and
    # ALSO rejects outright here) no duplicate live order results.
    adapter = AsyncMock()
    adapter.fetch_order = AsyncMock(return_value=_row("canceled"))
    adapter.place_market_order = AsyncMock(
        return_value=type(
            "R", (), {"ok": False, "venue_order_id": None,
                      "client_order_id": None, "code": "rejected",
                      "msg": "final reject", "raw": {}},
        )()
    )
    adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    trade2 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig2"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now + 5,
        real_roundtrip=True, alpaca_adapter=adapter,
    )
    assert trade2 is None
    assert read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    ) is None
    anchor = memdb.execute(
        "SELECT COUNT(*) FROM reentry_anchor WHERE venue='alpaca' AND symbol='ENTX'"
    ).fetchone()[0]
    assert int(anchor) == 1


# ---------------------------------------------------------------------------
# (d) an immediate-fill entry (no pending carryover) is byte-identical.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_fill_open_unaffected_by_pending_wiring(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_RespOk())
    adapter.fetch_order = AsyncMock(return_value=_row("filled"))
    adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig(), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=int(time.time()),
        real_roundtrip=True, alpaca_adapter=adapter,
    )
    assert trade is not None
    adapter.place_market_order.assert_awaited_once()
    assert read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    ) is None


# ---------------------------------------------------------------------------
# (f) partial-fill true-up — the confirmed RCA fix (spec A'): a poll budget
# expiring on ``partially_filled`` must persist the snapshot as the position
# entry AND true-up the final venue qty on the NEXT tick (no permanent
# truncation, e.g. live GVH 206/250 / AAL 35/82.60 sh).
# ---------------------------------------------------------------------------


def _partial_row(qty: str = "400", px: str = "5.00") -> dict[str, Any]:
    return {"id": "entx_ord_1", "symbol": "ENTX", "side": "buy",
            "status": "partially_filled", "filled_qty": qty,
            "filled_avg_price": px}


def _adapter_stuck_partial() -> AsyncMock:
    """Every poll (submit + pending-resolve) reports the SAME partial qty —
    the order never progresses past 400/797 within this test's ticks."""
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_RespOk())
    adapter.fetch_order = AsyncMock(return_value=_partial_row("400"))
    adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    return adapter


@pytest.mark.asyncio
async def test_partial_fill_persists_snapshot_and_keeps_trueup_ref(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())

    tick1_adapter = _adapter_stuck_partial()
    trade1 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig1"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now,
        real_roundtrip=True, alpaca_adapter=tick1_adapter,
    )
    # The snapshot fill (400 shares) IS persisted as the position entry — never
    # dropped (flow_not_block: some confirmed shares beat zero tracked shares).
    assert trade1 is not None
    pos_row = memdb.execute(
        "SELECT qty, risk_usd FROM positions WHERE position_id = ?",
        (trade1.position_id,),
    ).fetchone()
    assert pos_row is not None
    assert float(pos_row[0]) == pytest.approx(400.0)
    pending = read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    )
    assert pending is not None
    assert pending.state == "partial_trueup"
    assert pending.position_id == trade1.position_id


@pytest.mark.asyncio
async def test_partial_fill_trueup_folds_final_qty_next_tick(
    memdb: sqlite3.Connection,
) -> None:
    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())

    tick1_adapter = _adapter_stuck_partial()
    trade1 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig1"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now,
        real_roundtrip=True, alpaca_adapter=tick1_adapter,
    )
    assert trade1 is not None
    old_risk_usd = memdb.execute(
        "SELECT risk_usd FROM positions WHERE position_id = ?",
        (trade1.position_id,),
    ).fetchone()[0]

    # Tick 2: the SAME order now reports the FULL 797 filled — a fresh signal
    # fires on the same key; the true-up runs FIRST (position row folded to
    # 797) and does NOT block the fresh signal's own flow.
    tick2_adapter = AsyncMock()
    tick2_adapter.fetch_order = AsyncMock(return_value=_partial_row("797"))
    with_final = dict(_partial_row("797"))
    with_final["status"] = "filled"
    tick2_adapter.fetch_order = AsyncMock(return_value=with_final)
    tick2_adapter.place_market_order = AsyncMock(return_value=_RespOk("entx_ord_2"))
    tick2_adapter.fetch_account = AsyncMock(return_value={"buying_power": "100000"})
    trade2 = await _reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("sig2"), venue="alpaca",
        symbol="ENTX", asset_class="equity", underlying_group_id="equity:ENTX",
        notional_usd=4000.0, last_price=5.0, now_ts=now + 5,
        real_roundtrip=True, alpaca_adapter=tick2_adapter,
    )
    folded_qty = memdb.execute(
        "SELECT qty, risk_usd FROM positions WHERE position_id = ?",
        (trade1.position_id,),
    ).fetchone()
    assert float(folded_qty[0]) == pytest.approx(797.0)
    # risk_usd rescaled proportionally to the qty growth (entry anchor kept).
    if old_risk_usd is not None:
        assert float(folded_qty[1]) == pytest.approx(
            float(old_risk_usd) * (797.0 / 400.0)
        )
    fill_row = memdb.execute(
        "SELECT base_qty, size_usd, quote_qty FROM fills "
        "WHERE order_id = 'entx_ord_1' AND is_close = 0"
    ).fetchone()
    assert fill_row is not None
    assert float(fill_row[0]) == pytest.approx(797.0)
    assert float(fill_row[1]) == pytest.approx(797.0 * 5.0)
    # Pending ref cleared — nothing left to true up.
    assert read_pending_open(
        memdb, venue="alpaca", symbol="ENTX", strategy_id="equity_tsmom",
        side="long",
    ) is None
    # The fresh tick-2 signal was NOT blocked by the true-up (flow_not_block).
    assert trade2 is not None
    assert trade2.position_id != trade1.position_id


# ---------------------------------------------------------------------------
# (e) OKX / Capital open paths never touch pending_opens.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_okx_open_path_never_writes_pending_opens(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.venues.okx.adapter import OKXOrderResponse

    reset_process_fence()
    state = ProdLoopState()
    okx_sig = RawSignal(
        signal_id="okx_sig", strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )
    okx_adapter = AsyncMock()
    okx_adapter.place_market_order = AsyncMock(
        return_value=OKXOrderResponse(
            ok=True, venue_order_id="ord_live_1", client_order_id="cl1",
            code="0", msg="", raw={},
        )
    )
    okx_adapter.fetch_order = AsyncMock(return_value={"data": [{
        "ordId": "ord_live_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": "100.0",
        "avgPx": "60000.0", "fee": "-0.1", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }]})
    okx_adapter.fetch_ticker = AsyncMock(return_value={})
    okx_adapter.fetch_balance = AsyncMock(return_value={})
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=okx_sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is not None
    count = memdb.execute("SELECT COUNT(*) FROM pending_opens").fetchone()[0]
    assert int(count) == 0
