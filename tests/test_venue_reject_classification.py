"""Venue-reject classification — external rejects must NOT halt the strategy.

DEMO/PAPER only. Aggressive bias: venue compliance/balance rejects are EXTERNAL
events, not strategy faults — they must not trip the per-strategy circuit
breaker (flow_not_block). A genuinely anomalous reject code (possible internal
bug) DOES still fault so real anomalies can eventually halt.

All adapters are mocked; no real venue network call ever happens.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polaris.core.data.fill_normalizer import FillNormalizationError
from polaris.core.isolation.circuit_breaker import ACTIVE, current_strategy_mode
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._smoke_real_roundtrip import real_okx_open_fill
from polaris.scripts._smoke_roundtrip_shared import OpenAttempt
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal


def _sig(signal_id: str = "rt_sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="tsmom", symbol="GAS-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _okx_reject_resp(code: str, msg: str = "rej") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=False, venue_order_id=None, client_order_id="cl1",
        code=code, msg=msg, raw={},
    )


def _okx_balance(avail_usdt: float = 1_000_000.0) -> dict[str, Any]:
    """OKX /account/balance payload with a USDT availBal detail row.

    Default is a large balance so the FIX-2 clamp is a no-op and the reject
    classification under test is exercised unchanged. Tests that want the clamp
    to engage pass a small ``avail_usdt``.
    """
    return {"data": [{"details": [{"ccy": "USDT", "availBal": str(avail_usdt)}]}]}


def _make_okx_reject_adapter(code: str, *, avail_usdt: float = 1_000_000.0) -> AsyncMock:
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_okx_reject_resp(code))
    adapter.fetch_order = AsyncMock(return_value={"data": []})
    # No bid → #7 maker path falls back to the market leg (which carries the
    # reject), so the reject-code assertions below still hold.
    adapter.fetch_ticker = AsyncMock(return_value={})
    # FIX-2 balance clamp reads available USDT before submit; a large balance
    # keeps the clamp a no-op so the reject path under test is unchanged.
    adapter.fetch_balance = AsyncMock(return_value=_okx_balance(avail_usdt))
    return adapter


# ---------------------------------------------------------------------------
# Part A — real_okx_open_fill propagates the venue reject code via OpenAttempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_okx_open_fill_propagates_reject_code() -> None:
    adapter = _make_okx_reject_adapter("51155")
    attempt = await real_okx_open_fill(
        adapter, inst_id="GAS-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=10.0,
    )
    assert isinstance(attempt, OpenAttempt)
    assert attempt.fill is None
    assert attempt.reject_code == "51155"


@pytest.mark.asyncio
async def test_real_okx_open_fill_no_fill_when_no_rows() -> None:
    """Order accepted but the fill query returns no rows → reject_code=no_fill."""
    adapter = AsyncMock()

    from polaris.venues.okx.adapter import OKXOrderResponse

    adapter.place_market_order = AsyncMock(
        return_value=OKXOrderResponse(
            ok=True, venue_order_id="o1", client_order_id="cl1",
            code="0", msg="", raw={},
        )
    )
    adapter.fetch_order = AsyncMock(return_value={"data": []})
    adapter.fetch_ticker = AsyncMock(return_value={})  # no bid → market leg
    attempt = await real_okx_open_fill(
        adapter, inst_id="BTC-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=60_000.0,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "no_fill"


# ---------------------------------------------------------------------------
# Part B — external reject does NOT record_fault; anomalous code DOES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_reject_does_not_fault_and_blocklists(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.isolation.blocklist import is_blocklisted

    reset_process_fence()
    state = ProdLoopState()
    adapter = _make_okx_reject_adapter("51155")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("comp"), venue="okx",
        symbol="GAS-USDT", asset_class="crypto",
        underlying_group_id="crypto:GAS", notional_usd=100.0,
        last_price=10.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    # No strategy fault recorded.
    assert state.fault_events == 0
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='tsmom'"
    ).fetchone()[0]
    assert int(fault) == 0
    # Telemetry counter incremented.
    assert state.venue_rejects_by_code.get("51155") == 1
    # 51155 → permanent blocklist.
    assert is_blocklisted(memdb, "okx", "GAS-USDT") is True


@pytest.mark.asyncio
async def test_balance_reject_does_not_fault_or_blocklist(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.isolation.blocklist import is_blocklisted

    reset_process_fence()
    state = ProdLoopState()
    adapter = _make_okx_reject_adapter("51008")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("bal"), venue="okx",
        symbol="ETH-USDT", asset_class="crypto",
        underlying_group_id="crypto:ETH", notional_usd=100.0,
        last_price=3_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    assert state.fault_events == 0
    assert state.venue_rejects_by_code.get("51008") == 1
    # 51008 is transient — NOT blocklisted.
    assert is_blocklisted(memdb, "okx", "ETH-USDT") is False


@pytest.mark.asyncio
async def test_three_external_rejects_keep_circuit_breaker_active(
    memdb: sqlite3.Connection,
) -> None:
    """3 consecutive external rejects must leave the breaker ACTIVE (the bug
    being fixed: it used to SOFT_HALT after 3 reject faults)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())
    for i, code in enumerate(("51155", "51008", "51008")):
        adapter = _make_okx_reject_adapter(code)
        await reserve_and_submit(
            conn=memdb, state=state, sig=_sig(f"ext{i}"), venue="okx",
            symbol=f"X{i}-USDT", asset_class="crypto",
            underlying_group_id=f"crypto:X{i}", notional_usd=100.0,
            last_price=10.0, now_ts=now + i, real_roundtrip=True,
            okx_adapter=adapter,
        )
    assert state.fault_events == 0
    assert current_strategy_mode(memdb, "tsmom", now_ts=now + 10) == ACTIVE


@pytest.mark.asyncio
async def test_51201_market_cap_reject_does_not_fault(
    memdb: sqlite3.Connection,
) -> None:
    """51201 (OKX SPOT 1000-USDT market cap) is a deterministic VENUE RULE, not
    a strategy fault — FIX 1 splits >1000 USDT entries so it stops occurring,
    but a residual 51201 must classify external (no FAULT_REJECT)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.isolation.blocklist import is_blocklisted

    reset_process_fence()
    state = ProdLoopState()
    adapter = _make_okx_reject_adapter("51201")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("cap"), venue="okx",
        symbol="ALGO-USDT", asset_class="crypto",
        underlying_group_id="crypto:ALGO", notional_usd=100.0,
        last_price=1.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    assert state.fault_events == 0
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='tsmom'"
    ).fetchone()[0]
    assert int(fault) == 0
    assert state.venue_rejects_by_code.get("51201") == 1
    # Venue rule, not compliance → NOT blocklisted (transient/deterministic).
    assert is_blocklisted(memdb, "okx", "ALGO-USDT") is False


@pytest.mark.asyncio
async def test_fix2_low_balance_clamp_skips_without_fault(
    memdb: sqlite3.Connection,
) -> None:
    """FIX 2 end-to-end: an OKX available USDT below the min notional makes the
    entry skip cleanly (reject_code=insufficient_balance) with NO order POSTed
    and NO strategy fault (flow_not_block: no oversized 51008 churn)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    # availBal=2 USDT (below MIN_OKX_NOTIONAL_USD=10) → clamp returns None → skip.
    adapter = _make_okx_reject_adapter("51008", avail_usdt=2.0)
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("lowbal"), venue="okx",
        symbol="INJ-USDT", asset_class="crypto",
        underlying_group_id="crypto:INJ", notional_usd=1_468.0,
        last_price=10.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    assert state.fault_events == 0
    # Skipped BEFORE submit → no market order ever placed.
    adapter.place_market_order.assert_not_awaited()
    assert state.venue_rejects_by_code.get("insufficient_balance") == 1


@pytest.mark.asyncio
async def test_anomalous_reject_code_does_record_fault(
    memdb: sqlite3.Connection,
) -> None:
    """A reject code NOT in the external set (possible internal/client bug) must
    still record a fault so real anomalies can eventually halt."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    adapter = _make_okx_reject_adapter("99999")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("anom"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    assert state.fault_events == 1
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='tsmom'"
    ).fetchone()[0]
    assert int(fault) == 1


# ---------------------------------------------------------------------------
# Part C — OKX entry uses market order + non-filled state → no_fill (not fault)
# ---------------------------------------------------------------------------


class _RecordingOKXAdapter:
    """Fake adapter recording place_market_order kwargs; scriptable fetch row."""

    def __init__(self, fetch_row: dict[str, Any] | None) -> None:
        self.place_kwargs: dict[str, Any] = {}
        self._fetch_row = fetch_row

    async def place_market_order(self, **kwargs: Any) -> Any:
        from polaris.venues.okx.adapter import OKXOrderResponse

        self.place_kwargs = kwargs
        return OKXOrderResponse(
            ok=True, venue_order_id="o1", client_order_id="cl1",
            code="0", msg="", raw={},
        )

    async def fetch_order(self, **_kwargs: Any) -> dict[str, Any]:
        rows = [self._fetch_row] if self._fetch_row is not None else []
        return {"data": rows}

    async def fetch_balance(self, _ccy: str | None = None) -> dict[str, Any]:
        # Large balance → FIX-2 clamp is a no-op for these market-path tests.
        return {"data": [{"details": [{"ccy": "USDT", "availBal": "1000000"}]}]}


@pytest.mark.asyncio
async def test_okx_open_uses_market_order() -> None:
    """Entry leg must place a market order with quote-ccy notional semantics so
    OKX fills at best available (aggressive bias: getting filled > px clamp)."""
    adapter = _RecordingOKXAdapter(
        fetch_row={
            "instId": "GAS-USDT", "side": "buy", "state": "filled",
            "avgPx": "10.0", "accFillSz": "10.0", "ordId": "o1",
            "tgtCcy": "quote_ccy",
        }
    )
    await real_okx_open_fill(
        adapter, inst_id="GAS-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=10.0, poll_delay_sec=0.0,
    )
    assert adapter.place_kwargs.get("ord_type") == "market"
    assert adapter.place_kwargs.get("tgt_ccy") == "quote_ccy"


@pytest.mark.asyncio
async def test_okx_open_canceled_state_is_no_fill_not_exception() -> None:
    """A non-filled venue state (e.g. 'canceled') must route to no_fill, NOT
    raise FillNormalizationError → keeps the no-fill on the external-nonfault
    path instead of a FAULT_EXCEPTION."""
    adapter = _RecordingOKXAdapter(
        fetch_row={"instId": "GAS-USDT", "side": "buy", "state": "canceled"}
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="GAS-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(attempt, OpenAttempt)
    assert attempt.fill is None
    assert attempt.reject_code == "no_fill"


@pytest.mark.asyncio
async def test_okx_open_canceled_with_partial_fill_is_tracked() -> None:
    """A 'canceled' order that left a partial fill (accFillSz>0) is a REAL
    position — it must be normalized + tracked, never dropped to no_fill (which
    would leave an untracked orphan). codex review 2026-05-29."""
    adapter = _RecordingOKXAdapter(
        fetch_row={
            "instId": "GAS-USDT", "side": "buy", "state": "canceled",
            "avgPx": "10.0", "accFillSz": "3.0", "ordId": "o1",
            "tgtCcy": "quote_ccy",
        }
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="GAS-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=10.0, poll_delay_sec=0.0,
    )
    assert isinstance(attempt, OpenAttempt)
    assert attempt.fill is not None
    assert attempt.reject_code is None


@pytest.mark.asyncio
async def test_okx_open_filled_state_returns_fill() -> None:
    """A filled row still normalizes to a Fill (success path preserved)."""
    adapter = _RecordingOKXAdapter(
        fetch_row={
            "instId": "GAS-USDT", "side": "buy", "state": "filled",
            "avgPx": "10.0", "accFillSz": "10.0", "ordId": "o1",
            "tgtCcy": "quote_ccy",
        }
    )
    attempt = await real_okx_open_fill(
        adapter, inst_id="GAS-USDT", notional_usd=100.0,
        strategy_id="tsmom", last_price=10.0, poll_delay_sec=0.0,
    )
    assert attempt.fill is not None
    assert attempt.reject_code is None
    _ = FillNormalizationError  # imported for negative-path documentation


# ---------------------------------------------------------------------------
# Part D — Alpaca (Track C) external reject classification (H2 + H5).
#   external (market-closed / PDT / buying-power / forbidden) → NO fault, the
#   reservation is released and a telemetry counter bumps. A validation reject
#   (anomalous, normalized to a non-external token) DOES fault. The alpaca
#   adapter is MOCKED end-to-end through reserve_and_submit — NO real order.
# ---------------------------------------------------------------------------


def _eq_sig(signal_id: str = "eq_sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="equity_tsmom", symbol="AAPL",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )


def _alpaca_reject_resp(code: str, msg: str = "rej") -> Any:
    from polaris.venues.alpaca.adapter import AlpacaOrderResponse

    return AlpacaOrderResponse(
        ok=False, venue_order_id=None, client_order_id="clA",
        code=code, msg=msg, raw={},
    )


def _make_alpaca_reject_adapter(code: str) -> AsyncMock:
    """Mock AlpacaAdapter whose order POST returns a normalized reject code."""
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_alpaca_reject_resp(code))
    # If the open path ever polled, return no rows (no fill) — defensive.
    adapter.fetch_order = AsyncMock(return_value={})
    return adapter


@pytest.mark.parametrize(
    "code",
    ["insufficient_buying_power", "pdt_block", "market_closed", "forbidden"],
)
@pytest.mark.asyncio
async def test_alpaca_external_reject_does_not_fault(
    memdb: sqlite3.Connection, code: str
) -> None:
    """Each genuine external Alpaca reject classifies non-fault (no HARD_HALT)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    adapter = _make_alpaca_reject_adapter(code)
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig(f"ext_{code}"), venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", notional_usd=200.0,
        last_price=190.0, now_ts=int(time.time()),
        real_roundtrip=True, alpaca_adapter=adapter,
    )
    assert trade is None
    # External reject → NO strategy fault.
    assert state.fault_events == 0
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='equity_tsmom'"
    ).fetchone()[0]
    assert int(fault) == 0
    # Telemetry counter incremented for the external code.
    assert state.venue_rejects_by_code.get(code) == 1


@pytest.mark.asyncio
async def test_alpaca_external_rejects_keep_breaker_active(
    memdb: sqlite3.Connection,
) -> None:
    """Repeated external Alpaca rejects leave the strategy breaker ACTIVE."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    now = int(time.time())
    for i, code in enumerate(
        ("insufficient_buying_power", "market_closed", "pdt_block")
    ):
        adapter = _make_alpaca_reject_adapter(code)
        await reserve_and_submit(
            conn=memdb, state=state, sig=_eq_sig(f"eqext{i}"), venue="alpaca",
            symbol=f"SYM{i}", asset_class="equity",
            underlying_group_id=f"equity:SYM{i}", notional_usd=200.0,
            last_price=100.0, now_ts=now + i, real_roundtrip=True,
            alpaca_adapter=adapter,
        )
    assert state.fault_events == 0
    assert current_strategy_mode(memdb, "equity_tsmom", now_ts=now + 10) == ACTIVE


@pytest.mark.asyncio
async def test_alpaca_anomalous_reject_records_fault(
    memdb: sqlite3.Connection,
) -> None:
    """A reject code OUTSIDE the Alpaca external set (e.g. a normalized 422
    validation token / unknown code) must still record a fault."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    # ``validation_rejected`` is the adapter's normalized token for a 422
    # business-validation reject — NOT in C_alpaca_equity.external_reject_codes.
    adapter = _make_alpaca_reject_adapter("validation_rejected")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("eqanom"), venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", notional_usd=200.0,
        last_price=190.0, now_ts=int(time.time()),
        real_roundtrip=True, alpaca_adapter=adapter,
    )
    assert trade is None
    assert state.fault_events == 1
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='equity_tsmom'"
    ).fetchone()[0]
    assert int(fault) == 1


@pytest.mark.asyncio
async def test_alpaca_equity_signal_flows_through_reserve_and_submit_to_mock_fill(
    memdb: sqlite3.Connection,
) -> None:
    """H5 wiring: an equity signal reaches the (MOCK) Alpaca open fill via
    reserve_and_submit → _real_open_fill, producing a tracked trade. NO real
    order — the adapter is a mock that returns a filled order."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()

    class _MockAlpacaAdapter:
        """Records the order POST + returns a filled order on lookup."""

        def __init__(self) -> None:
            self.place_kwargs: dict[str, Any] = {}

        async def place_market_order(self, **kwargs: Any) -> Any:
            from polaris.venues.alpaca.adapter import AlpacaOrderResponse

            self.place_kwargs = kwargs
            return AlpacaOrderResponse(
                ok=True, venue_order_id="alp-1",
                client_order_id=kwargs.get("client_order_id"),
                code="accepted", msg="accepted", raw={},
            )

        async def fetch_order(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "id": "alp-1", "symbol": "AAPL", "side": "buy",
                "status": "filled", "filled_qty": "1.05",
                "filled_avg_price": "190.0",
            }

    adapter = _MockAlpacaAdapter()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_eq_sig("eqflow"), venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", notional_usd=200.0,
        last_price=190.0, now_ts=int(time.time()),
        real_roundtrip=True, alpaca_adapter=adapter,
    )
    # The equity signal flowed end-to-end to the mock fill → a tracked trade.
    assert trade is not None
    assert trade.venue == "alpaca"
    assert trade.symbol == "AAPL"
    assert state.fills_open == 1
    assert state.fault_events == 0
    # The adapter actually received the order (the long side maps to "buy").
    assert adapter.place_kwargs.get("side") == "buy"
    assert adapter.place_kwargs.get("symbol") == "AAPL"
    # A positions row was persisted for the equity trade.
    pos = memdb.execute(
        "SELECT COUNT(*) FROM positions WHERE venue='alpaca' AND symbol='AAPL'"
    ).fetchone()[0]
    assert int(pos) == 1
