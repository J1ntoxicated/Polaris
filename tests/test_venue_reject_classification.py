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


def _make_okx_reject_adapter(code: str) -> AsyncMock:
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_okx_reject_resp(code))
    adapter.fetch_order = AsyncMock(return_value={"data": []})
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
