"""OKX entry-stall fix — bounded HARD_HALT auto-resume + 400 root-cause routing.

DEMO/PAPER only. Aggressive bias / flow_not_block: a strategy HARD_HALT must
NEVER be a permanent block — after a bounded cooldown it auto-resumes. An OKX
order POST that returns an HTTP 400 carrying an OKX business body (bad lot/tick
precision, etc.) must flow as a venue REJECT (OpenAttempt.fill=None), NOT escape
as a raw exception that trips the exception circuit breaker into a permanent
halt. No real venue network call ever happens (all I/O mocked).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.core.isolation.circuit_breaker import (
    ACTIVE,
    CB_HARD_HALT_AUTO_UNBLOCK_SEC,
    FAULT_EXCEPTION,
    HARD_HALT,
    RISK_ONLY,
    current_strategy_mode,
    record_fault,
    resume_stale_permanent_halts,
)
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal
from polaris.venues.okx.adapter import OKXAdapter, OKXOrderResponse
from polaris.venues.okx.constraint_translator import (
    InstrumentConstraint,
    round_down_to_step,
    round_price_to_tick,
)

NOW = 1_780_000_000


def _sig(signal_id: str, symbol: str) -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="tsmom", symbol=symbol,
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _okx_param_reject_adapter(code: str) -> AsyncMock:
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(
        return_value=OKXOrderResponse(
            ok=False, venue_order_id=None, client_order_id="cl1",
            code=code, msg="precision", raw={},
        )
    )
    adapter.fetch_order = AsyncMock(return_value={"data": []})
    adapter.fetch_ticker = AsyncMock(return_value={})  # no bid → market leg
    adapter.fetch_balance = AsyncMock(
        return_value={"data": [{"details": [{"ccy": "USDT", "availBal": "1000000"}]}]}
    )
    return adapter


# ---------------------------------------------------------------------------
# 1. Bounded HARD_HALT auto-resume
# ---------------------------------------------------------------------------


def test_hard_halt_sets_bounded_unblock_ts(memdb: sqlite3.Connection) -> None:
    """3 exceptions → HARD_HALT, but with a bounded unblock_ts (not None)."""
    for i in range(3):
        record_fault(memdb, strategy_id="tsmom", fault_type=FAULT_EXCEPTION, now_ts=NOW + i)
    assert current_strategy_mode(memdb, "tsmom", now_ts=NOW + 5) == HARD_HALT
    unblock = memdb.execute(
        "SELECT unblock_ts FROM strategy_halts "
        "WHERE strategy_id='tsmom' AND mode=? AND reset_ts IS NULL",
        (HARD_HALT,),
    ).fetchone()[0]
    assert unblock is not None
    assert int(unblock) == (NOW + 2) + CB_HARD_HALT_AUTO_UNBLOCK_SEC


def test_hard_halt_auto_resumes_after_cooldown(memdb: sqlite3.Connection) -> None:
    """Before cooldown → still HARD_HALT; after cooldown → ACTIVE (flow_not_block)."""
    for i in range(3):
        record_fault(memdb, strategy_id="tsmom", fault_type=FAULT_EXCEPTION, now_ts=NOW + i)
    # Just before the cooldown elapses → still halted.
    assert (
        current_strategy_mode(
            memdb, "tsmom", now_ts=(NOW + 2) + CB_HARD_HALT_AUTO_UNBLOCK_SEC - 1
        )
        == HARD_HALT
    )
    # After the cooldown → auto-resumed (the offending strategy flows again).
    assert (
        current_strategy_mode(
            memdb, "tsmom", now_ts=(NOW + 2) + CB_HARD_HALT_AUTO_UNBLOCK_SEC + 1
        )
        == ACTIVE
    )


def test_risk_only_stays_manual_reset(memdb: sqlite3.Connection) -> None:
    """RISK_ONLY (open positions) is exit-only and NOT auto-resumed by cooldown.

    A position is live; auto-resuming NEW entries while the feed/venue that
    faulted is unproven would re-arm the same fault. Exit path is unaffected.
    """
    record_fault(
        memdb, strategy_id="rsi_bb_pullback", fault_type=FAULT_EXCEPTION,
        now_ts=NOW, has_open_positions=True,
    )
    for i in range(1, 3):
        record_fault(
            memdb, strategy_id="rsi_bb_pullback", fault_type=FAULT_EXCEPTION,
            now_ts=NOW + i, has_open_positions=True,
        )
    assert current_strategy_mode(memdb, "rsi_bb_pullback", now_ts=NOW + 5) == RISK_ONLY
    far_future = NOW + CB_HARD_HALT_AUTO_UNBLOCK_SEC * 100
    assert current_strategy_mode(memdb, "rsi_bb_pullback", now_ts=far_future) == RISK_ONLY


# ---------------------------------------------------------------------------
# 2. One-time migration: convert the existing unblock_ts=None permanent halts
# ---------------------------------------------------------------------------


def _insert_permanent_hard_halt(
    conn: sqlite3.Connection, *, strategy_id: str, opened_ts: int, mode: str = HARD_HALT
) -> None:
    conn.execute(
        "INSERT INTO strategy_halts "
        "(halt_id, strategy_id, mode, reason_code, opened_ts, unblock_ts, "
        " reset_by, reset_ts, detail_json) "
        "VALUES (?, ?, ?, 'exception', ?, NULL, NULL, NULL, '{}')",
        (f"h_{strategy_id}", strategy_id, mode, opened_ts),
    )


def test_resume_stale_permanent_halts_migration(memdb: sqlite3.Connection) -> None:
    """The 2 legacy unblock_ts=None HARD_HALT rows become bounded/resumable."""
    _insert_permanent_hard_halt(memdb, strategy_id="tsmom", opened_ts=NOW)
    _insert_permanent_hard_halt(memdb, strategy_id="rsi_bb_pullback", opened_ts=NOW)
    # Permanent → blocks forever.
    assert current_strategy_mode(memdb, "tsmom", now_ts=NOW + 10**9) == HARD_HALT

    touched = resume_stale_permanent_halts(memdb, now_ts=NOW + 100)
    assert touched == 2
    # Now bounded off opened_ts → resumes after cooldown.
    after = NOW + CB_HARD_HALT_AUTO_UNBLOCK_SEC + 1
    assert current_strategy_mode(memdb, "tsmom", now_ts=after) == ACTIVE
    assert current_strategy_mode(memdb, "rsi_bb_pullback", now_ts=after) == ACTIVE


def test_migration_skips_risk_only_and_already_bounded(memdb: sqlite3.Connection) -> None:
    """RISK_ONLY rows and rows already carrying an unblock_ts are left untouched."""
    _insert_permanent_hard_halt(memdb, strategy_id="ro", opened_ts=NOW, mode=RISK_ONLY)
    # already-bounded HARD_HALT
    memdb.execute(
        "INSERT INTO strategy_halts "
        "(halt_id, strategy_id, mode, reason_code, opened_ts, unblock_ts, "
        " reset_by, reset_ts, detail_json) "
        "VALUES ('hb', 'bounded', ?, 'exception', ?, ?, NULL, NULL, '{}')",
        (HARD_HALT, NOW, NOW + 60),
    )
    touched = resume_stale_permanent_halts(memdb, now_ts=NOW + 100)
    assert touched == 0
    # RISK_ONLY still manual-only.
    assert current_strategy_mode(memdb, "ro", now_ts=NOW + 10**9) == RISK_ONLY


# ---------------------------------------------------------------------------
# 3. OKX 400 root-cause: param-reject body → OpenAttempt reject, NOT exception
# ---------------------------------------------------------------------------


def _okx_400_response(code: str = "51000", smsg: str = "Parameter sz error") -> httpx.Response:
    """An OKX-bodied HTTP 400 (param/precision reject) — JSON carries code+sMsg."""
    body = {
        "code": "1",
        "msg": "Operation failed.",
        "data": [{"sCode": code, "sMsg": smsg, "ordId": "", "clOrdId": "polLbuyabcd"}],
    }
    return httpx.Response(
        400, json=body, request=httpx.Request("POST", "https://us.okx.com/api/v5/trade/order")
    )


@pytest.mark.asyncio
async def test_okx_400_param_body_returns_reject_not_exception() -> None:
    """A 400 carrying an OKX business body returns ok=False — it must NOT raise.

    This is the root-cause of the entry stall: a precision/param 400 was
    re-raised as HTTPStatusError → FAULT_EXCEPTION → permanent HARD_HALT. It
    must instead flow as a venue reject (OpenAttempt.fill=None downstream).
    """
    transport = httpx.MockTransport(lambda req: _okx_400_response())
    client = httpx.AsyncClient(base_url="https://us.okx.com", transport=transport)
    adapter = OKXAdapter(
        api_key="k", secret="s", passphrase="p", client=client, demo=True,
    )
    resp = await adapter.place_market_order(
        inst_id="THETA-USDT", side="buy", notional_usd=20.0,
        client_order_id="polLbuyabcd", ord_type="market", tgt_ccy="quote_ccy",
    )
    assert resp.ok is False
    assert resp.code == "51000"
    assert resp.venue_order_id is None
    await client.aclose()


@pytest.mark.asyncio
async def test_okx_400_without_okx_body_still_raises() -> None:
    """A 400 that is NOT an OKX business body (e.g. WAF/HTML) still raises.

    Only genuine OKX param-rejects are absorbed; an opaque 400 remains an
    anomaly that should surface.
    """
    resp = httpx.Response(
        400, text="<html>blocked</html>",
        request=httpx.Request("POST", "https://us.okx.com/api/v5/trade/order"),
    )
    transport = httpx.MockTransport(lambda req: resp)
    client = httpx.AsyncClient(base_url="https://us.okx.com", transport=transport)
    adapter = OKXAdapter(api_key="k", secret="s", passphrase="p", client=client, demo=True)
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.place_market_order(
            inst_id="THETA-USDT", side="buy", notional_usd=20.0,
            client_order_id="polLbuyabcd", ord_type="market", tgt_ccy="quote_ccy",
        )
    await client.aclose()


# ---------------------------------------------------------------------------
# 4. Precision: sz→lotSz, px→tickSz rounding (prevents the precision 400)
# ---------------------------------------------------------------------------


def _constraint() -> InstrumentConstraint:
    return InstrumentConstraint(
        inst_id="THETA-USDT", base_ccy="THETA", quote_ccy="USDT",
        lot_sz=0.1, min_sz=0.1, tick_sz=0.001, state="live",
    )


@pytest.mark.asyncio
async def test_okx_ioc_rounds_sz_and_px_to_constraint() -> None:
    """When a constraint cache is present, IOC sz is floored to lotSz, px to tickSz."""
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(req.content.decode())
        captured.update({"sz": body.get("sz", ""), "px": body.get("px", "")})
        return httpx.Response(
            200,
            json={"code": "0", "msg": "", "data": [{"sCode": "0", "ordId": "1", "clOrdId": "c"}]},
            request=req,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://us.okx.com", transport=transport)
    adapter = OKXAdapter(api_key="k", secret="s", passphrase="p", client=client, demo=True)
    adapter.set_instrument_constraints({"THETA-USDT": _constraint()})
    await adapter.place_market_order(
        inst_id="THETA-USDT", side="buy", notional_usd=20.0,
        client_order_id="polLpoabcd", ord_type="ioc", last_price_hint=1.23456,
    )
    # px floored to tick 0.001 → 1.234 (after +slippage clamp, still a tick multiple).
    px = float(captured["px"])
    assert abs(round_price_to_tick(px, 0.001) - px) < 1e-9
    # sz floored to lot 0.1 → a multiple of 0.1.
    sz = float(captured["sz"])
    assert abs(round_down_to_step(sz, 0.1) - sz) < 1e-9
    await client.aclose()


# ---------------------------------------------------------------------------
# 5. Param reject is EXTERNAL non-fault with NO per-symbol cooldown skip (the
#    cooldown machinery is GONE — replaced by the submit-path min-size clamp-up).
#    A previously-"cooled-down" symbol now FLOWS on its next signal (regression
#    guard: the per-symbol skip path is removed).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_param_reject_is_external_nonfault_no_cooldown(
    memdb: sqlite3.Connection,
) -> None:
    """A 51121 (lot-size) reject on THETA-USDT records NO strategy fault and arms
    NO per-symbol cooldown — the cooldown map is gone from ProdLoopState. The
    strategy stays ACTIVE (flow_not_block); only telemetry is bumped."""
    reset_process_fence()
    state = ProdLoopState()
    adapter = _okx_param_reject_adapter("51121")
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("p1", "THETA-USDT"), venue="okx",
        symbol="THETA-USDT", asset_class="crypto",
        underlying_group_id="crypto:THETA", notional_usd=100.0,
        last_price=1.0, now_ts=NOW, real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    # NO strategy fault → strategy NOT halted (flow_not_block).
    assert state.fault_events == 0
    assert (
        memdb.execute(
            "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='tsmom'"
        ).fetchone()[0]
        == 0
    )
    # Cooldown machinery removed — no per-symbol skip map exists.
    assert not hasattr(state, "okx_param_reject_cooldowns")
    assert state.venue_rejects_by_code.get("51121") == 1


@pytest.mark.asyncio
async def test_previously_param_rejected_symbol_flows_next_signal(
    memdb: sqlite3.Connection,
) -> None:
    """REGRESSION guard: a symbol that just took a param reject is NOT skipped on
    its next signal (the per-symbol cooldown skip is removed). The next attempt
    reaches the venue (place_market_order is called again) — the symbol flows."""
    reset_process_fence()
    state = ProdLoopState()
    adapter = _okx_param_reject_adapter("51121")
    # First attempt takes a param reject (no cooldown armed).
    await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("p1", "THETA-USDT"), venue="okx",
        symbol="THETA-USDT", asset_class="crypto",
        underlying_group_id="crypto:THETA", notional_usd=100.0,
        last_price=1.0, now_ts=NOW, real_roundtrip=True, okx_adapter=adapter,
    )
    adapter.place_market_order.reset_mock()
    # Next signal on the SAME symbol immediately after → reaches the venue (NOT
    # skipped). Previously this was blocked for 1800s by the cooldown.
    await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("p2", "THETA-USDT"), venue="okx",
        symbol="THETA-USDT", asset_class="crypto",
        underlying_group_id="crypto:THETA", notional_usd=100.0,
        last_price=1.0, now_ts=NOW + 10, real_roundtrip=True, okx_adapter=adapter,
    )
    adapter.place_market_order.assert_called()  # symbol flows, no skip


@pytest.mark.asyncio
async def test_other_symbol_flows_after_param_reject(memdb: sqlite3.Connection) -> None:
    """After THETA-USDT takes a param reject, a DIFFERENT symbol still submits."""
    reset_process_fence()
    state = ProdLoopState()
    bad = _okx_param_reject_adapter("51121")
    await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("p1", "THETA-USDT"), venue="okx",
        symbol="THETA-USDT", asset_class="crypto",
        underlying_group_id="crypto:THETA", notional_usd=100.0,
        last_price=1.0, now_ts=NOW, real_roundtrip=True, okx_adapter=bad,
    )
    # A second symbol with a working fill must still flow (not blocked).
    good = AsyncMock()
    good.place_market_order = AsyncMock(
        return_value=OKXOrderResponse(
            ok=True, venue_order_id="o1", client_order_id="cl1", code="0", msg="", raw={},
        )
    )
    good.fetch_ticker = AsyncMock(return_value={})
    good.fetch_balance = AsyncMock(
        return_value={"data": [{"details": [{"ccy": "USDT", "availBal": "1000000"}]}]}
    )
    good.fetch_order = AsyncMock(
        return_value={
            "data": [{
                "instId": "ETH-USDT", "state": "filled", "side": "buy",
                "accFillSz": "0.03", "avgPx": "3000", "fillSz": "0.03",
                "ordId": "o1", "clOrdId": "cl1", "fee": "-0.01", "feeCcy": "USDT",
            }]
        }
    )
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("e1", "ETH-USDT"), venue="okx",
        symbol="ETH-USDT", asset_class="crypto",
        underlying_group_id="crypto:ETH", notional_usd=90.0,
        last_price=3000.0, now_ts=NOW + 5, real_roundtrip=True, okx_adapter=good,
    )
    assert trade is not None  # other symbol flowed through
    good.place_market_order.assert_called()
