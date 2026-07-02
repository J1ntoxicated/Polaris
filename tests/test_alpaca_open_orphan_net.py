"""Alpaca OPEN-side orphan net — the confirmed leak fix (Track C, DEMO/PAPER).

ROOT CAUSE: ``real_alpaca_open_fill`` POSTed a (whole-share-retry) market BUY then
did ONE 0.5s sleep + ONE fetch_order. An order still at ``new``/``pending_new``
at that single poll returned ``OpenAttempt(fill=None, reject_code='no_fill')`` —
the order was NOT cancelled, had NO confirm-poll loop, NO pending handoff, and
``_handle_open_reject`` only released the reservation. The released BUY stayed
LIVE at Alpaca, filled seconds later, and became a PERMANENT untracked orphan
(ENTX venue held 2445 shares = the 3 released no_fill retries).

FIX (flow_not_block — ENABLE tracking, never throttle):
 (1) mirror the CLOSE path's confirm-poll loop (ALPACA_CLOSE_CONFIRM_POLLS) so a
     late fill IS captured + returned as a Fill (more confirmed entries).
 (2) if STILL unfilled after the loop AND a real venue_order_id exists, hand the
     still-live order to record_venue_orphan (idempotent) instead of dropping it.

AGGRESSIVE bias intact: zero size cut / block / entry gate is added — this only
makes already-placed orders TRACKABLE. All adapters are mocked; no real network.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polaris.scripts._alpaca_open import real_alpaca_open_fill
from polaris.scripts._production_reject import _handle_open_reject
from polaris.scripts._smoke_roundtrip_shared import record_venue_orphan
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal


class _Resp:
    def __init__(
        self, ok: bool, venue_order_id: str | None = None,
        code: str = "", msg: str = "",
    ) -> None:
        self.ok = ok
        self.venue_order_id = venue_order_id
        self.client_order_id = None
        self.code = code
        self.msg = msg
        self.raw: dict[str, Any] = {}


class _PollAdapter:
    """Accepts a whole-share BUY; fetch_order returns ``status`` from a scripted
    sequence (one entry per poll). The retry path POSTs a qty order so a not-
    fractionable reject mirrors the live leak shape."""

    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self._i = 0
        self.calls: list[dict[str, Any]] = []

    async def place_market_order(
        self, *, symbol: str, side: str, notional_usd: float = 0.0,
        qty: float | None = None, client_order_id: str = "",
    ) -> _Resp:
        self.calls.append({"notional_usd": notional_usd, "qty": qty})
        if qty is None:
            # $-notional → not-fractionable reject → caller retries by whole qty.
            return _Resp(False, code="insufficient_buying_power",
                         msg='asset "ENTX" is not fractionable')
        return _Resp(True, venue_order_id="entx_ord_1")

    async def fetch_order(self, *, order_id: str) -> dict[str, Any]:
        status = self.statuses[min(self._i, len(self.statuses) - 1)]
        self._i += 1
        row: dict[str, Any] = {"status": status, "symbol": "ENTX", "side": "buy"}
        if status in ("filled", "partially_filled"):
            row["filled_qty"] = "797"
            row["filled_avg_price"] = "5.00"
        return row


@pytest.mark.asyncio
async def test_open_fills_after_poll_loop_returns_fill() -> None:
    """status=new at poll 1, still new at poll 2, filled at poll 3 → a Fill IS
    returned (today this was dropped as no_fill at the single poll)."""
    adapter = _PollAdapter(["new", "new", "filled"])
    res = await real_alpaca_open_fill(
        adapter, symbol="ENTX", notional_usd=4000.0, strategy_id="s",
        side="buy", last_price=5.0, poll_delay_sec=0.0,
    )
    assert res.fill is not None
    assert res.reject_code is None
    assert res.fill.base_qty == pytest.approx(797.0)


@pytest.mark.asyncio
async def test_open_partial_fill_keeps_polling_and_captures_full_fill() -> None:
    """status=new, then partially_filled, then filled at the LAST poll — the
    prior bug broke on the FIRST partially_filled observation and would have
    returned only the partial snapshot as final; the fix must keep polling and
    capture the eventual FULL fill (base_qty from the terminal 'filled' row)."""

    class _GrowingAdapter:
        def __init__(self) -> None:
            self.statuses = ["new", "partially_filled", "filled"]
            self._i = 0

        async def place_market_order(self, **kwargs: Any) -> _Resp:
            return _Resp(True, venue_order_id="entx_ord_1")

        async def fetch_order(self, *, order_id: str) -> dict[str, Any]:
            status = self.statuses[min(self._i, len(self.statuses) - 1)]
            self._i += 1
            row: dict[str, Any] = {"status": status, "symbol": "ENTX", "side": "buy"}
            if status in ("filled", "partially_filled"):
                qty = "400" if status == "partially_filled" else "797"
                row["filled_qty"] = qty
                row["filled_avg_price"] = "5.00"
            return row

    res = await real_alpaca_open_fill(
        _GrowingAdapter(), symbol="ENTX", notional_usd=4000.0, strategy_id="s",
        side="buy", last_price=5.0, poll_delay_sec=0.0,
    )
    assert res.fill is not None
    assert res.fill.base_qty == pytest.approx(797.0)  # the FINAL fill, not 400
    assert res.partial_trueup is False


@pytest.mark.asyncio
async def test_open_partial_fill_budget_exhausted_returns_snapshot_flagged() -> None:
    """Every poll across the WHOLE budget reports partially_filled (never
    reaches 'filled') — the snapshot fill IS returned (never dropped) but
    flagged ``partial_trueup=True`` + carries the venue ref so the caller can
    persist it as the entry and true-up the remainder next tick."""

    class _StuckPartialAdapter:
        async def place_market_order(self, **kwargs: Any) -> _Resp:
            return _Resp(True, venue_order_id="entx_ord_1")

        async def fetch_order(self, *, order_id: str) -> dict[str, Any]:
            return {
                "status": "partially_filled", "symbol": "ENTX", "side": "buy",
                "filled_qty": "400", "filled_avg_price": "5.00",
            }

    res = await real_alpaca_open_fill(
        _StuckPartialAdapter(), symbol="ENTX", notional_usd=4000.0,
        strategy_id="s", side="buy", last_price=5.0, poll_delay_sec=0.0,
    )
    assert res.fill is not None
    assert res.fill.base_qty == pytest.approx(400.0)
    assert res.partial_trueup is True
    assert res.venue_order_id == "entx_ord_1"


@pytest.mark.asyncio
async def test_open_never_fills_carries_venue_order_id_for_orphan() -> None:
    """Order accepted but never fills within the whole budget → no_fill BUT the
    OpenAttempt now carries the venue_order_id + the submitted qty so the caller
    can record an orphan instead of silently leaking the live order."""
    adapter = _PollAdapter(["new"])  # never leaves 'new'
    res = await real_alpaca_open_fill(
        adapter, symbol="ENTX", notional_usd=4000.0, strategy_id="s",
        side="buy", last_price=5.0, poll_delay_sec=0.0,
    )
    assert res.fill is None
    assert res.reject_code == "no_fill"
    assert res.venue_order_id == "entx_ord_1"
    assert res.unfilled_qty == pytest.approx(800.0)  # floor(4000/5)


@pytest.mark.asyncio
async def test_open_genuine_reject_has_no_venue_order_id() -> None:
    """A genuine reject (whole_qty<1 → no retry order placed) must NOT carry a
    venue_order_id — there is no accepted order to orphan (regression guard)."""

    class _RejAdapter:
        async def place_market_order(self, **kwargs: Any) -> _Resp:
            # $-notional → not-fractionable; price > notional so whole_qty<1 →
            # the retry never fires → no accepted order exists.
            return _Resp(False, code="insufficient_buying_power",
                         msg='asset "X" is not fractionable')

        async def fetch_order(self, **kwargs: Any) -> dict[str, Any]:
            return {}

    res = await real_alpaca_open_fill(
        _RejAdapter(), symbol="X", notional_usd=2.0, strategy_id="s",
        side="buy", last_price=150.0, poll_delay_sec=0.0,
    )
    assert res.fill is None
    assert res.venue_order_id is None  # nothing accepted → nothing to orphan


# ---------------------------------------------------------------------------
# _handle_open_reject — records the orphan once (idempotent), still releases,
# never trips the circuit breaker.
# ---------------------------------------------------------------------------


def _eq_sig() -> RawSignal:
    return RawSignal(
        signal_id="entx_sig", strategy_id="equity_tsmom", symbol="ENTX",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )


@pytest.mark.asyncio
async def test_handle_open_reject_records_orphan_when_venue_order_id(
    memdb: sqlite3.Connection,
) -> None:
    """no_fill branch + a real venue_order_id → record_venue_orphan is called,
    the reservation is still released, and NO strategy fault is recorded."""
    state = ProdLoopState()
    fence = AsyncMock()
    now = int(time.time())
    await _handle_open_reject(
        memdb, fence=fence, state=state, sig=_eq_sig(), venue="alpaca",
        symbol="ENTX", reservation_id="res1", reject_code="no_fill",
        reject_msg="state=new", now_ts=now,
        venue_order_id="entx_ord_1", unfilled_qty=797.0,
    )
    fence.release_reservation.assert_awaited_once()
    assert state.fault_events == 0
    rows = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='venue_orphan'"
    ).fetchone()[0]
    assert int(rows) == 1


@pytest.mark.asyncio
async def test_handle_open_reject_no_orphan_without_venue_order_id(
    memdb: sqlite3.Connection,
) -> None:
    """A genuine reject (no venue_order_id) on the no_fill branch must NOT record
    an orphan — there is no live accepted order (regression guard)."""
    state = ProdLoopState()
    fence = AsyncMock()
    await _handle_open_reject(
        memdb, fence=fence, state=state, sig=_eq_sig(), venue="alpaca",
        symbol="ENTX", reservation_id="res1", reject_code="no_fill",
        reject_msg="no order", now_ts=int(time.time()),
        venue_order_id=None, unfilled_qty=0.0,
    )
    fence.release_reservation.assert_awaited_once()
    rows = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='venue_orphan'"
    ).fetchone()[0]
    assert int(rows) == 0


# ---------------------------------------------------------------------------
# record_venue_orphan idempotency — duplicate same-order_id calls insert ONCE
# (concurrency: 2 strategies, same symbol, same tick can inject duplicates).
# ---------------------------------------------------------------------------


def test_record_venue_orphan_idempotent_on_venue_order_id(
    memdb: sqlite3.Connection,
) -> None:
    now = int(time.time())
    for _ in range(3):
        record_venue_orphan(
            memdb, strategy_id="equity_tsmom", venue="alpaca", symbol="ENTX",
            side="long", phase="real_open_no_fill", venue_order_id="entx_ord_1",
            deal_id=None, base_qty=797.0, now_ts=now,
        )
    rows = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='venue_orphan'"
    ).fetchone()[0]
    assert int(rows) == 1


def test_record_venue_orphan_distinct_order_ids_each_recorded(
    memdb: sqlite3.Connection,
) -> None:
    """Idempotency keys on venue_order_id — different live orders each record."""
    now = int(time.time())
    for oid, qty in (("entx_a", 797.0), ("entx_b", 858.0)):
        record_venue_orphan(
            memdb, strategy_id="equity_tsmom", venue="alpaca", symbol="ENTX",
            side="long", phase="real_open_no_fill", venue_order_id=oid,
            deal_id=None, base_qty=qty, now_ts=now,
        )
    rows = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='venue_orphan'"
    ).fetchone()[0]
    assert int(rows) == 2
