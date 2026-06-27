"""③ anti-churn — a TRANSIENT external reject stamps the novelty key.

DEMO/PAPER. Aggressive bias / flow_not_block intact.

ROOT CAUSE (42x re-fire churn): the novelty key
``state.last_entry_by_key[(venue, symbol, strategy_id)]`` was written ONLY on a
SUCCESSFUL fill (``_production_run_signal`` after ``reserve_and_submit`` returns
a trade). A buying-power / market-closed reject makes ``reserve_and_submit``
return ``None`` BEFORE that stamp, so the key stayed ``None`` → every following
tick saw ``is_novel_reentry`` True (last_entry_bar is None → always novel) →
the re-entry cooldown was exempted → the SAME signal re-fired indefinitely. The
positions table guards never caught it either: a reject INSERTs no positions row.

FIX (surgical, flow_not_block): when ``_handle_open_reject`` classifies a reject
as a TRANSIENT external event (insufficient_buying_power / market_closed /
no_fill …), stamp the novelty key with the signal's ``(created_at_bar, side)``.
A same-bar same-side re-fire is then NOT novel → the cooldown applies → churn
stops. A NEW bar or a side flip still flows (novelty restored) — never a
permanent block. A PERMANENT-blocklist compliance reject (51155) does NOT stamp
(the blocklist is its mechanism; the symbol is skipped before novelty anyway).

All adapters mocked; no real venue network. The fence is an AsyncMock.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from polaris.scripts._production_reject import _handle_open_reject
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal

KEY = ("alpaca", "ENTX", "equity_tsmom")


def _eq_sig(
    *, created_at_bar: int = 7000, side: Literal["long", "short"] = "long",
) -> RawSignal:
    return RawSignal(
        signal_id="entx_sig", strategy_id="equity_tsmom", symbol="ENTX",
        side=side, strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
        created_at_bar=created_at_bar,
    )


@pytest.mark.asyncio
async def test_buying_power_reject_stamps_novelty_key(
    memdb: sqlite3.Connection,
) -> None:
    """🔴 The churn-stopping stamp: an insufficient_buying_power reject records
    the signal's (created_at_bar, side) so the next same-bar tick is NOT novel."""
    state = ProdLoopState()
    fence = AsyncMock()
    await _handle_open_reject(
        memdb, fence=fence, state=state, sig=_eq_sig(created_at_bar=7000),
        venue="alpaca", symbol="ENTX", reservation_id="res1",
        reject_code="insufficient_buying_power", reject_msg="bp",
        now_ts=int(time.time()),
    )
    assert state.last_entry_by_key[KEY] == (7000, "long")
    # Still released, still no strategy fault (external, flow_not_block).
    fence.release_reservation.assert_awaited_once()
    assert state.fault_events == 0


@pytest.mark.asyncio
async def test_market_closed_reject_stamps_novelty_key(
    memdb: sqlite3.Connection,
) -> None:
    """A market_closed reject (weekday holiday residual) is transient external →
    stamps the key too, so a same-bar re-fire is suppressed."""
    state = ProdLoopState()
    await _handle_open_reject(
        memdb, fence=AsyncMock(), state=state, sig=_eq_sig(created_at_bar=7100),
        venue="alpaca", symbol="ENTX", reservation_id="res1",
        reject_code="market_closed", reject_msg="closed",
        now_ts=int(time.time()),
    )
    assert state.last_entry_by_key[KEY] == (7100, "long")


@pytest.mark.asyncio
async def test_no_fill_reject_stamps_novelty_key(
    memdb: sqlite3.Connection,
) -> None:
    """A plain no_fill (None reject_code → 'no_fill') is external transient and
    stamps the key (the residual BP-reserve no-fill safety net)."""
    state = ProdLoopState()
    await _handle_open_reject(
        memdb, fence=AsyncMock(), state=state, sig=_eq_sig(created_at_bar=7200),
        venue="alpaca", symbol="ENTX", reservation_id="res1",
        reject_code=None, reject_msg="state=new", now_ts=int(time.time()),
    )
    assert state.last_entry_by_key[KEY] == (7200, "long")


@pytest.mark.asyncio
async def test_compliance_reject_does_not_stamp_novelty_key(
    memdb: sqlite3.Connection,
) -> None:
    """A PERMANENT-blocklist compliance reject (OKX 51155) must NOT stamp the
    novelty key — the blocklist is its mechanism (the symbol is skipped before
    novelty), and stamping would be misleading. It still blocklists + releases."""
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="gas_sig", strategy_id="tsmom", symbol="GAS-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
        created_at_bar=7300,
    )
    await _handle_open_reject(
        memdb, fence=AsyncMock(), state=state, sig=sig, venue="okx",
        symbol="GAS-USDT", reservation_id="res1", reject_code="51155",
        reject_msg="region", now_ts=int(time.time()),
    )
    assert ("okx", "GAS-USDT", "tsmom") not in state.last_entry_by_key


@pytest.mark.asyncio
async def test_anomalous_reject_does_not_stamp_novelty_key(
    memdb: sqlite3.Connection,
) -> None:
    """An ANOMALOUS reject (possible internal bug → FAULT_REJECT) takes the
    fault branch, NOT the external branch, so it never stamps the novelty key."""
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="entx_sig", strategy_id="equity_tsmom", symbol="ENTX",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
        created_at_bar=7400,
    )
    await _handle_open_reject(
        memdb, fence=AsyncMock(), state=state, sig=sig, venue="alpaca",
        symbol="ENTX", reservation_id="res1",
        reject_code="validation_rejected", reject_msg="bad", now_ts=int(time.time()),
    )
    assert KEY not in state.last_entry_by_key
    assert state.fault_events == 1  # the anomalous path still faults


@pytest.mark.asyncio
async def test_stamp_lets_new_bar_refire_but_blocks_same_bar(
    memdb: sqlite3.Connection,
) -> None:
    """flow_not_block end-to-end: after a BP-reject stamps bar=7000, the SAME bar
    is not novel (churn blocked) but a NEW bar (7001) and a side flip are novel
    again (entry resumes). Uses is_novel_reentry against the stamped key."""
    from polaris.core.isolation.reentry import is_novel_reentry

    state = ProdLoopState()
    await _handle_open_reject(
        memdb, fence=AsyncMock(), state=state, sig=_eq_sig(created_at_bar=7000),
        venue="alpaca", symbol="ENTX", reservation_id="res1",
        reject_code="insufficient_buying_power", reject_msg="bp",
        now_ts=int(time.time()),
    )
    last_bar, last_side = state.last_entry_by_key[KEY]
    # Same bar, same side → NOT novel (the churn re-fire is suppressed).
    assert is_novel_reentry(
        created_at_bar=7000, side="long",
        last_entry_bar=last_bar, last_entry_side=last_side,
    ) is False
    # New bar → novel again (a genuine new opportunity still flows).
    assert is_novel_reentry(
        created_at_bar=7001, side="long",
        last_entry_bar=last_bar, last_entry_side=last_side,
    ) is True
    # Side flip → novel again (the thesis reversed).
    assert is_novel_reentry(
        created_at_bar=7000, side="short",
        last_entry_bar=last_bar, last_entry_side=last_side,
    ) is True
