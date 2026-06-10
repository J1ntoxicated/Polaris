"""BUG E — close re-fire on a still-live venue order (stale-order double sell).

Live incidents this pins:
* GOLD close reject #1..#17 — the Capital close deal_reference was never
  confirmed before re-firing ``close_position`` every tick (1.1h reject loop).
* SELX — the Alpaca close confirm was a single 0.5s poll; a late venue fill was
  missed, the position re-fired a full sell next tick → double sell + wallet
  drain → orphan reconcile.

FIX (PendingClose sentinel): a close order that is LIVE at the venue but whose
fill is unconfirmed returns ``PendingClose(ref)`` — a third semantic next to
``Fill`` (settled) and ``None`` (no order remains → safe to re-fire). The
caller stores the ref on the trade and CONFIRMS it first next tick instead of
firing a duplicate close. OKX market sells never survive to the next tick — a
still-``live`` order is cancelled + re-checked in-tick (no sentinel needed).

DEMO/PAPER only; virtual funds. AGGRESSIVE bias intact — the close is the
position's own exit leg (flow_not_block), never a throttle / size dampen.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

import polaris.scripts._production_close as pc
from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._production_close import _close_trade_with_real_pnl, _real_close_fill
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts._smoke_real_roundtrip import _okx_sell_child
from polaris.scripts._smoke_roundtrip_alpaca import (
    ALPACA_CLOSE_CONFIRM_POLLS,
    real_alpaca_close_fill,
)
from polaris.scripts._smoke_roundtrip_capital import (
    _CONFIRM_POLLS,
    real_capital_close_fill,
)
from polaris.scripts._smoke_roundtrip_shared import CloseOrphan, PendingClose
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


# ---------------------------------------------------------------------------
# Alpaca mocks
# ---------------------------------------------------------------------------


class _Clock:
    is_open = True


def _alpaca_row(order_id: str, status: str, *, filled_qty: float = 2.0) -> dict[str, Any]:
    return {
        "id": order_id,
        "symbol": "SELX",
        "side": "sell",
        "status": status,
        "filled_qty": str(filled_qty),
        "filled_avg_price": "10.5",
        "filled_at": "2026-06-09T19:00:00Z",
        "client_order_id": "cl",
    }


class _AlpacaSeqMock:
    """fetch_order returns the per-order-id status SEQUENCE (last repeats)."""

    def __init__(self, *, sequences: dict[str, list[dict[str, Any] | None]]) -> None:
        self._seq = {k: list(v) for k, v in sequences.items()}
        self.fetch_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.sell_orders: list[dict[str, Any]] = []

    async def fetch_clock(self) -> _Clock:
        return _Clock()

    async def fetch_positions(self) -> list[dict[str, Any]]:
        return [{"symbol": "SELX", "qty": "1000000"}]

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.cancel_calls.append(order_id)
        return {}

    async def place_market_order(self, **kwargs: Any) -> Any:
        self.sell_orders.append(dict(kwargs))

        class _Resp:
            ok = True
            venue_order_id = "new_order"
            code = "accepted"
            msg = ""

        return _Resp()

    async def fetch_order(
        self, *, order_id: str | None = None, client_order_id: str | None = None
    ) -> dict[str, Any] | None:
        oid = order_id or ""
        self.fetch_calls.append(oid)
        seq = self._seq.get(oid, [])
        row = seq.pop(0) if len(seq) > 1 else (seq[0] if seq else None)
        return row


@pytest.mark.asyncio
async def test_alpaca_unfilled_after_polls_returns_pending_close() -> None:
    """A fresh SELL that never confirms within the poll budget hands the LIVE
    order to the next tick as PendingClose — never a bare None (re-fire)."""
    adapter = _AlpacaSeqMock(
        sequences={"new_order": [_alpaca_row("new_order", "accepted")]},
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=2.0, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert isinstance(out, PendingClose)
    assert out.ref == "new_order"
    # Polled the order the full budget before handing off.
    assert len(adapter.fetch_calls) == ALPACA_CLOSE_CONFIRM_POLLS


@pytest.mark.asyncio
async def test_alpaca_pending_order_filled_returns_fill_no_refire() -> None:
    """SELX double-sell fix: a pending ref that ALREADY filled returns the
    existing order's Fill and never places a new sell."""
    adapter = _AlpacaSeqMock(
        sequences={"po1": [_alpaca_row("po1", "filled", filled_qty=2.0)]},
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=2.0, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert isinstance(out, Fill)
    assert out.order_id == "po1"
    assert out.base_qty == pytest.approx(2.0)
    assert adapter.sell_orders == []  # no duplicate sell fired


@pytest.mark.asyncio
async def test_alpaca_pending_live_cancelled_then_fresh_sell() -> None:
    """A still-LIVE pending order is cancelled first (so it cannot fill after
    we re-fire), re-checked once, and only then is a fresh sell placed."""
    adapter = _AlpacaSeqMock(
        sequences={
            # live → (after cancel) canceled with 0 filled → fresh sell path
            "po1": [
                _alpaca_row("po1", "accepted"),
                _alpaca_row("po1", "canceled", filled_qty=0.0),
            ],
            "new_order": [_alpaca_row("new_order", "filled", filled_qty=2.0)],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=2.0, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert adapter.cancel_calls == ["po1"]
    assert isinstance(out, Fill)
    assert out.order_id == "new_order"
    assert len(adapter.sell_orders) == 1
    assert adapter.sell_orders[0]["qty"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_alpaca_pending_terminal_falls_through_to_fresh_sell() -> None:
    """A terminal (canceled, ZERO filled) pending ref can never fill — fall
    through to a fresh sell WITHOUT a cancel round-trip."""
    adapter = _AlpacaSeqMock(
        sequences={
            "po1": [_alpaca_row("po1", "canceled", filled_qty=0.0)],
            "new_order": [_alpaca_row("new_order", "filled", filled_qty=2.0)],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=2.0, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert adapter.cancel_calls == []
    assert isinstance(out, Fill)
    assert out.order_id == "new_order"


@pytest.mark.asyncio
async def test_alpaca_poll_loop_breaks_on_third_poll_fill() -> None:
    """E-c: the confirm loop polls past the first miss — a 3rd-poll fill is a
    Fill (the prior single 0.5s poll returned None → next-tick re-fire)."""
    adapter = _AlpacaSeqMock(
        sequences={
            "new_order": [
                _alpaca_row("new_order", "accepted"),
                _alpaca_row("new_order", "accepted"),
                _alpaca_row("new_order", "filled", filled_qty=2.0),
            ],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=2.0, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert isinstance(out, Fill)
    assert len(adapter.fetch_calls) == 3  # broke out as soon as it filled


# ---------------------------------------------------------------------------
# BUG E-d — partially_filled is NOT settled: its remainder is still LIVE at
# the venue. Settling the slice + clearing the ref re-fires a fresh sell next
# tick NEXT TO the live remainder = two live close orders (the double sell).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alpaca_poll_partial_keeps_polling_until_full_fill() -> None:
    """E-d: ``partially_filled`` must NOT break the confirm loop — the
    remainder is still LIVE and may complete. Keep polling → full Fill."""
    adapter = _AlpacaSeqMock(
        sequences={
            "new_order": [
                _alpaca_row("new_order", "partially_filled", filled_qty=1.0),
                _alpaca_row("new_order", "partially_filled", filled_qty=1.0),
                _alpaca_row("new_order", "filled", filled_qty=5.8169),
            ],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert isinstance(out, Fill)
    assert out.base_qty == pytest.approx(5.8169)
    assert adapter.cancel_calls == []
    assert len(adapter.fetch_calls) == 3


@pytest.mark.asyncio
async def test_alpaca_poll_partial_after_budget_cancels_then_settles_slice() -> None:
    """SOXL 'filled 1.0 of 5.8169': part-filled at budget exhaustion → cancel
    the LIVE remainder + ONE re-query (mirror ``_okx_sell_child``), book only
    the TERMINAL slice. The remainder re-fires fresh next tick with nothing
    live left at the venue."""
    partial = _alpaca_row("new_order", "partially_filled", filled_qty=1.0)
    adapter = _AlpacaSeqMock(
        sequences={
            "new_order": [dict(partial) for _ in range(ALPACA_CLOSE_CONFIRM_POLLS)]
            + [_alpaca_row("new_order", "canceled", filled_qty=1.0)],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert adapter.cancel_calls == ["new_order"]
    assert isinstance(out, Fill)
    assert out.base_qty == pytest.approx(1.0)
    # full poll budget + the single post-cancel re-query
    assert len(adapter.fetch_calls) == ALPACA_CLOSE_CONFIRM_POLLS + 1


@pytest.mark.asyncio
async def test_alpaca_poll_partial_cancel_race_full_fill_returns_full() -> None:
    """The remainder fills DURING the cancel round-trip → the re-query shows
    'filled' → the FULL settled Fill is returned (no slice under-booking)."""
    partial = _alpaca_row("new_order", "partially_filled", filled_qty=1.0)
    adapter = _AlpacaSeqMock(
        sequences={
            "new_order": [dict(partial) for _ in range(ALPACA_CLOSE_CONFIRM_POLLS)]
            + [_alpaca_row("new_order", "filled", filled_qty=5.8169)],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert adapter.cancel_calls == ["new_order"]
    assert isinstance(out, Fill)
    assert out.base_qty == pytest.approx(5.8169)


@pytest.mark.asyncio
async def test_alpaca_poll_partial_cancel_not_landed_returns_pending() -> None:
    """The cancel has not landed by the re-query (still partially_filled) —
    the remainder can STILL fill: PendingClose, never a settled slice."""
    adapter = _AlpacaSeqMock(
        sequences={
            "new_order": [
                _alpaca_row("new_order", "partially_filled", filled_qty=1.0),
            ],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert adapter.cancel_calls == ["new_order"]
    assert isinstance(out, PendingClose)
    assert out.ref == "new_order"


@pytest.mark.asyncio
async def test_alpaca_pending_partial_cancels_remainder_then_settles_slice() -> None:
    """E-d on the confirm-first path: a pending ref observed partially_filled
    has a LIVE remainder — cancel + re-query, book the terminal slice, and
    NEVER fire a fresh sell in the same tick (two live orders otherwise)."""
    adapter = _AlpacaSeqMock(
        sequences={
            "po1": [
                _alpaca_row("po1", "partially_filled", filled_qty=1.0),
                _alpaca_row("po1", "canceled", filled_qty=1.0),
            ],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert adapter.cancel_calls == ["po1"]
    assert isinstance(out, Fill)
    assert out.order_id == "po1"
    assert out.base_qty == pytest.approx(1.0)
    assert adapter.sell_orders == []  # no fresh sell next to a live remainder


@pytest.mark.asyncio
async def test_alpaca_pending_partial_cancel_not_landed_keeps_pending() -> None:
    adapter = _AlpacaSeqMock(
        sequences={
            "po1": [_alpaca_row("po1", "partially_filled", filled_qty=1.0)],
        },
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert adapter.cancel_calls == ["po1"]
    assert isinstance(out, PendingClose)
    assert out.ref == "po1"
    assert adapter.sell_orders == []


@pytest.mark.asyncio
async def test_alpaca_pending_terminal_with_partial_fill_returns_slice() -> None:
    """A pending ref that died part-filled (canceled w/ filled_qty>0): the
    slice is FINAL — book it. Ignoring it kept the sold slice tracked and
    re-sold it on the next tick's fresh sell."""
    adapter = _AlpacaSeqMock(
        sequences={"po1": [_alpaca_row("po1", "canceled", filled_qty=1.0)]},
    )
    out = await real_alpaca_close_fill(
        adapter, symbol="SELX", base_qty=5.8169, strategy_id="s",
        poll_delay_sec=0.0, pending_order_id="po1",
    )
    assert adapter.cancel_calls == []
    assert isinstance(out, Fill)
    assert out.order_id == "po1"
    assert out.base_qty == pytest.approx(1.0)
    assert adapter.sell_orders == []


# ---------------------------------------------------------------------------
# Capital mocks
# ---------------------------------------------------------------------------


def _cap_confirm(status: str) -> dict[str, Any]:
    base: dict[str, Any] = {"dealStatus": status}
    if status in ("ACCEPTED", "OPEN", "CLOSED"):
        base.update(
            {
                "status": "CLOSED", "epic": "GOLD", "direction": "SELL",
                "level": 2300.0, "size": 1.0, "dealId": "deal-1",
                "dealReference": "ref-1",
                "date": "2026-06-09T10:00:00Z",
            }
        )
    return base


class _CapResp:
    def __init__(self, *, ok: bool, deal_reference: str | None) -> None:
        self.ok = ok
        self.deal_reference = deal_reference
        self.deal_id = None
        self.status = "REJECTED" if not ok else "OPEN"
        self.reason = "" if ok else "error.invalid.dealId"
        self.raw: dict[str, Any] = {}


class _CapitalSeqMock:
    """confirm() returns the per-ref dealStatus SEQUENCE (last repeats)."""

    def __init__(
        self,
        *,
        confirms: dict[str, list[str]],
        close_ok: bool = True,
        close_ref: str | None = "new_ref",
        held_deal_ids: list[str] | None = None,
        list_raises: bool = False,
    ) -> None:
        self._confirms = {k: list(v) for k, v in confirms.items()}
        self._close_ok = close_ok
        self._close_ref = close_ref
        self._held = held_deal_ids if held_deal_ids is not None else []
        self._list_raises = list_raises
        self.close_calls: list[str] = []
        self.confirm_calls: list[str] = []

    async def close_position(self, deal_id: str) -> _CapResp:
        self.close_calls.append(deal_id)
        return _CapResp(ok=self._close_ok, deal_reference=self._close_ref)

    async def confirm(self, deal_reference: str) -> dict[str, Any]:
        self.confirm_calls.append(deal_reference)
        seq = self._confirms.get(deal_reference, ["PENDING"])
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        return _cap_confirm(status)

    async def list_positions(self) -> dict[str, Any]:
        if self._list_raises:
            raise RuntimeError("listing down")
        return {
            "positions": [
                {"position": {"dealId": d}, "market": {}} for d in self._held
            ]
        }


@pytest.mark.asyncio
async def test_capital_pending_ref_settled_returns_fill_no_refire() -> None:
    """GOLD loop fix: a pending close deal_reference that CONFIRMS settled
    returns the Fill — close_position is never re-fired."""
    adapter = _CapitalSeqMock(confirms={"pref": ["ACCEPTED"]})
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
        pending_ref="pref",
    )
    assert isinstance(out, Fill)
    assert adapter.close_calls == []


@pytest.mark.asyncio
async def test_capital_pending_ref_still_pending_returns_same_ref() -> None:
    adapter = _CapitalSeqMock(confirms={"pref": ["PENDING"]})
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
        pending_ref="pref",
    )
    assert isinstance(out, PendingClose)
    assert out.ref == "pref"
    assert adapter.close_calls == []


@pytest.mark.asyncio
async def test_capital_pending_ref_rejected_refires_close() -> None:
    """A REJECTED pending close never executed — a fresh close_position is
    fired and its confirm drives the result."""
    adapter = _CapitalSeqMock(
        confirms={"pref": ["REJECTED"], "new_ref": ["ACCEPTED"]},
    )
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
        pending_ref="pref",
    )
    assert adapter.close_calls == ["deal-1"]
    assert isinstance(out, Fill)


@pytest.mark.asyncio
async def test_capital_close_reject_deal_absent_returns_close_orphan() -> None:
    """GOLD reject #1..#17 terminal in ONE tick: close_position rejects AND
    list_positions no longer lists the deal → the deal is GONE at the venue →
    CloseOrphan (reconcile), not an endless transient None."""
    adapter = _CapitalSeqMock(
        confirms={}, close_ok=False, close_ref=None, held_deal_ids=[],
    )
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
    )
    assert isinstance(out, CloseOrphan)
    assert out.available == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_capital_close_reject_deal_still_listed_stays_transient() -> None:
    adapter = _CapitalSeqMock(
        confirms={}, close_ok=False, close_ref=None, held_deal_ids=["deal-1"],
    )
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_capital_close_reject_listing_failure_stays_transient() -> None:
    """A listing failure must NOT fabricate an orphan — keep the transient
    retry semantics (flow_not_block)."""
    adapter = _CapitalSeqMock(
        confirms={}, close_ok=False, close_ref=None, list_raises=True,
    )
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_capital_new_close_confirm_never_finalizes_returns_pending() -> None:
    """A placed close whose confirm stays PENDING through the poll budget hands
    the deal_reference to the next tick as PendingClose (confirm-first)."""
    adapter = _CapitalSeqMock(confirms={"new_ref": ["PENDING"]})
    out = await real_capital_close_fill(
        adapter, deal_id="deal-1", strategy_id="s", poll_delay_sec=0.0,
    )
    assert isinstance(out, PendingClose)
    assert out.ref == "new_ref"
    assert adapter.confirm_calls.count("new_ref") == _CONFIRM_POLLS


# ---------------------------------------------------------------------------
# OKX — a still-live market sell is cancelled + re-checked in-tick
# ---------------------------------------------------------------------------


class _OkxLiveMock:
    def __init__(self, *, requery_state: str, requery_fill: float) -> None:
        self._requery_state = requery_state
        self._requery_fill = requery_fill
        self._fetches = 0
        self.cancel_calls: list[str] = []

    async def place_market_order(self, **kwargs: Any) -> Any:
        class _R:
            ok = True
            venue_order_id = "ord1"
            code = "0"
            msg = ""

        return _R()

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        self.cancel_calls.append(ord_id)
        return {}

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        self._fetches += 1
        if self._fetches == 1:
            state, fill = "live", 0.0
        else:
            state, fill = self._requery_state, self._requery_fill
        return {
            "data": [{
                "ordId": ord_id, "clOrdId": "cl", "instId": inst_id,
                "side": "sell", "tgtCcy": "base_ccy",
                "accFillSz": f"{fill:.10f}", "avgPx": "130.0",
                "fee": "-0.02", "feeCcy": "USDT", "state": state,
                "uTime": str(NOW * 1000),
            }]
        }


@pytest.mark.asyncio
async def test_okx_sell_child_live_is_cancelled_then_partial_fill() -> None:
    """state='live' after the poll → cancel (so it cannot fill after a next-tick
    re-fire) → one re-check; a race partial fill during the cancel is returned."""
    adapter = _OkxLiveMock(requery_state="partially_filled", requery_fill=0.5)
    out = await _okx_sell_child(
        adapter, inst_id="SOL-USDT", base_qty=1.0, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert adapter.cancel_calls == ["ord1"]
    assert isinstance(out, Fill)
    assert out.base_qty == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_okx_sell_child_live_cancelled_unfilled_returns_none() -> None:
    adapter = _OkxLiveMock(requery_state="canceled", requery_fill=0.0)
    out = await _okx_sell_child(
        adapter, inst_id="SOL-USDT", base_qty=1.0, strategy_id="s",
        poll_delay_sec=0.0,
    )
    assert adapter.cancel_calls == ["ord1"]
    assert out is None


# ---------------------------------------------------------------------------
# _close_trade_with_real_pnl — PendingClose bookkeeping
# ---------------------------------------------------------------------------


def _seed_position(conn: sqlite3.Connection, *, position_id: str, base_qty: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', ?, 'open', ?, 0)",
        (position_id, base_qty, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', 'long', ?, 100.0, ?, "
        " 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, base_qty, base_qty * 100.0,
         position_id, uuid.uuid4().hex),
    )


def _trade(position_id: str, base_qty: float) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=base_qty * 100.0, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


@pytest.mark.asyncio
async def test_close_trade_pending_preserves_position_and_stores_ref(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PendingClose from the venue leg: the position is preserved (no fill
    persisted, status stays open, not popped), the ref is stored on the trade
    for the next tick's confirm-first path, and the zombie reject tally keeps
    its backstop count. A later real Fill clears the ref and closes."""
    _seed_position(memdb, position_id="pos-pend", base_qty=2.0)
    state = ProdLoopState()
    trade = _trade("pos-pend", 2.0)
    state.open_trades = [trade]

    async def _pending(**kwargs: Any) -> PendingClose:
        return PendingClose(ref="ref-77")

    monkeypatch.setattr(pc, "_real_close_fill", _pending)
    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True,
    )
    assert ok is False
    assert trade.pending_close_ref == "ref-77"
    assert state.open_trades == [trade]
    assert trade.closed is False
    n_close = memdb.execute(
        "SELECT COUNT(*) FROM fills WHERE contribution_id='pos-pend' "
        "AND is_close=1"
    ).fetchone()[0]
    assert n_close == 0
    # Backstop kept: a forever-pending close still counts toward the zombie drain.
    assert sum(state.close_reject_counts.values()) == 1

    async def _filled(**kwargs: Any) -> Fill:
        return Fill(
            venue="okx", instrument_id="okx:SOL-USDT", strategy_id="tsmom",
            side="sell", size_usd=260.0, fill_price=130.0, fee_usd=0.02,
            slippage_bps=0.0, ts_ms=NOW * 1000 + 1000,
            order_id=uuid.uuid4().hex, base_qty=2.0, quote_qty=260.0,
        )

    monkeypatch.setattr(pc, "_real_close_fill", _filled)
    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=NOW + 60,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True,
    )
    assert ok is True
    assert trade.pending_close_ref is None
    assert trade.closed is True


@pytest.mark.asyncio
async def test_real_close_fill_threads_pending_ref_to_alpaca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _spy(adapter: Any, **kwargs: Any) -> None:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(pc, "real_alpaca_close_fill", _spy)
    trade = SimulatedTrade(
        signal_id="s1", venue="alpaca", symbol="SELX", strategy_id="s",
        side="long", entry_price=10.0, notional_usd=20.0, open_ts=NOW,
        position_id="p1", base_qty=2.0, pending_close_ref="po-9",
    )
    await _real_close_fill(trade=trade, alpaca_adapter=object())
    assert seen.get("pending_order_id") == "po-9"


@pytest.mark.asyncio
async def test_real_close_fill_threads_pending_ref_to_capital(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _spy(adapter: Any, **kwargs: Any) -> None:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(pc, "real_capital_close_fill", _spy)
    trade = SimulatedTrade(
        signal_id="s2", venue="capital", symbol="GOLD", strategy_id="s",
        side="long", entry_price=2300.0, notional_usd=100.0, open_ts=NOW,
        position_id="p2", base_qty=1.0, deal_id="deal-1",
        pending_close_ref="ref-3",
    )
    await _real_close_fill(trade=trade, capital_session=object())
    assert seen.get("deal_id") == "deal-1"
    assert seen.get("pending_ref") == "ref-3"
