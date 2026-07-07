"""P0 venue wire — real open/close fill TDD surface.

All tests inject a mock venue adapter (or use the ``dry_run`` synthetic-fill
path); **no real venue network call ever happens**. The real-roundtrip smoke
(actual demo orders) is driven manually outside pytest.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._production_pipeline import (
    _real_open_fill,
)
from polaris.scripts._production_pipeline import (
    reserve_and_submit as _reserve_and_submit,
)
from polaris.scripts._smoke_fills import SimulatedTrade, simulate_open_fill
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal


def _sig(signal_id: str = "rt_sig") -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _okx_order_resp(ok: bool = True, ord_id: str = "ord_live_1") -> Any:
    from polaris.venues.okx.adapter import OKXOrderResponse

    return OKXOrderResponse(
        ok=ok, venue_order_id=ord_id if ok else None,
        client_order_id="cl1", code="0", msg="", raw={},
    )


def _okx_filled_row(price: float = 60_000.0) -> dict[str, Any]:
    return {
        "ordId": "ord_live_1", "clOrdId": "cl1", "instId": "BTC-USDT",
        "side": "buy", "tgtCcy": "quote_ccy", "accFillSz": "100.0",
        "avgPx": str(price), "fee": "-0.1", "feeCcy": "USDT",
        "state": "filled", "uTime": str(int(time.time() * 1000)),
    }


def _make_okx_adapter(*, ok: bool = True, filled: bool = True) -> AsyncMock:
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(return_value=_okx_order_resp(ok=ok))
    adapter.fetch_order = AsyncMock(
        return_value={"data": [_okx_filled_row()] if filled else []}
    )
    # No bid → #7 maker path falls back to the single market leg, so the
    # place_market_order / fetch_order awaited-once assertions still hold.
    adapter.fetch_ticker = AsyncMock(return_value={})
    return adapter


# ---------------------------------------------------------------------------
# (a) reserve_and_submit(real_roundtrip=True) drives the adapter + persists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_real_calls_adapter_and_persists(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter(ok=True, filled=True)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig(), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is not None
    # Adapter was actually driven (the bug was: it never was).
    okx_adapter.place_market_order.assert_awaited_once()
    okx_adapter.fetch_order.assert_awaited_once()
    # Entry fill persisted + position row created + reservation confirmed.
    fill_row = memdb.execute(
        "SELECT order_id FROM fills WHERE contribution_id = ? AND is_close = 0",
        (trade.position_id,),
    ).fetchone()
    assert fill_row is not None
    assert fill_row[0] == "ord_live_1"
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is not None and res[0] == "confirmed"
    assert trade.venue_order_id == "ord_live_1"
    # P5 gap-b: the open now populates position_risk_state so the sizer's caps
    # bind on the next entry (was never written → caps never bound).
    prs = memdb.execute(
        "SELECT venue, symbol, strategy, open_risk_pct FROM position_risk_state"
    ).fetchone()
    assert prs is not None
    assert prs[0] == "okx" and prs[1] == "BTC-USDT" and prs[2] == "volume_burst"
    assert prs[3] > 0.0


# ---------------------------------------------------------------------------
# (b) adapter ok=False → release + record_fault + None + no positions row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_real_reject_releases_and_faults(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter(ok=False)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("rej"), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is None
    # No positions row, no fills row.
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert memdb.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    # Reservation released (not confirmed) + fault recorded.
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is None or res[0] != "confirmed"
    assert state.fault_events == 1
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE strategy_id='volume_burst'"
    ).fetchone()[0]
    assert int(fault) >= 1


@pytest.mark.asyncio
async def test_reserve_and_submit_real_no_fill_row_releases(
    memdb: sqlite3.Connection,
) -> None:
    """Order accepted but the fill query returns no rows → external no-fill:
    released WITHOUT a strategy fault (flow_not_block), telemetry incremented."""
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter(ok=True, filled=False)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("nofill"), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is None
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    # no_fill is EXTERNAL — no strategy fault; telemetry counter bumped instead.
    assert state.fault_events == 0
    assert state.venue_rejects_by_code.get("no_fill") == 1
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is None or res[0] != "confirmed"


# ---------------------------------------------------------------------------
# (c2) Capital open persists the position deal_id into positions.deal_id (SSOT)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_capital_persists_deal_id(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Capital (cfd) open writes the position deal_id into positions.deal_id
    and tags the in-memory trade — so a restart can still close by deal_id.

    ``_real_open_fill`` (the network leg) is stubbed to return an OpenAttempt
    carrying a deal_id; no venue call happens.
    """
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe
    from polaris.scripts._smoke_roundtrip_shared import OpenAttempt

    reset_process_fence()
    deal_id = "DIAAAAAA-CAFE-1234"
    cap_fill = Fill(
        venue="capital", instrument_id="capital:GOLD", strategy_id="volume_burst",
        side="buy", size_usd=100.0, fill_price=2000.0, fee_usd=0.1,
        slippage_bps=0.0, ts_ms=int(time.time() * 1000),
        order_id=deal_id, base_qty=0.05, quote_qty=100.0, state="filled",
    )

    async def _stub_open(**_kwargs: Any) -> OpenAttempt:
        return OpenAttempt(fill=cap_fill, deal_id=deal_id)

    monkeypatch.setattr(pipe, "_real_open_fill", _stub_open)
    state = ProdLoopState()
    cap_sig = RawSignal(
        signal_id="cap_sig", strategy_id="volume_burst", symbol="GOLD",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="cfd_intraday",
    )
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=cap_sig, venue="capital", symbol="GOLD",
        asset_class="commodity", underlying_group_id="commodity:GOLD",
        notional_usd=100.0, last_price=2000.0, now_ts=int(time.time()),
        real_roundtrip=True, capital_session=object(),
    )
    assert trade is not None
    assert trade.deal_id == deal_id
    row = memdb.execute(
        "SELECT deal_id FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == deal_id


# ---------------------------------------------------------------------------
# (c3) quote-ccy entry converts risk_usd to USD (audit rank 4: J225/USDJPY
# stamped raw-quote-ccy risk_usd, ~40-150x inflated).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_capital_jpy_quote_converts_risk_usd(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JPY-quoted Capital entry (USDJPY) must stamp risk_usd in USD terms,
    not the raw JPY notional. Without the quote->USD conversion, entry_price
    ~150 (JPY) inflates risk_usd ~150x vs the true USD risk unit.
    """
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts import _production_pipeline as pipe
    from polaris.scripts._smoke_roundtrip_shared import OpenAttempt
    from polaris.venues.capital.constraint_translator import CapitalMarketConstraint

    reset_process_fence()
    # Seed the USDJPY conversion pair bar (bars-first, no-network rate source
    # shared with the trading path's _peek_quote_usd_rate).
    now = int(time.time())
    memdb.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) VALUES "
        " ('capital:USDJPY', 'fx:USDJPY', 'capital', 'USDJPY', '1m', ?, "
        " 150.0, 150.0, 150.0, 150.0, 0.0)",
        (now,),
    )
    memdb.commit()
    fill_price = 150.0  # JPY per USD — raw price is NOT in USD
    jpy_fill = Fill(
        venue="capital", instrument_id="capital:USDJPY",
        strategy_id="volume_burst", side="buy", size_usd=1000.0,
        fill_price=fill_price, fee_usd=0.1, slippage_bps=0.0,
        ts_ms=int(time.time() * 1000), order_id="deal_jpy_1",
        base_qty=1000.0, quote_qty=150_000.0, state="filled",
    )

    async def _stub_open(**_kwargs: Any) -> OpenAttempt:
        return OpenAttempt(fill=jpy_fill, deal_id="deal_jpy_1")

    monkeypatch.setattr(pipe, "_real_open_fill", _stub_open)
    state = ProdLoopState()
    # Warm the constraint cache the way a successful translate_capital_order
    # fetch would (real production: the constraint is cached before this
    # stamping site runs) — no network in this unit test.
    import time as _time_mod

    state.capital_constraints._entries["USDJPY"] = (
        CapitalMarketConstraint(
            epic="USDJPY", instrument_type="CURRENCIES", min_deal_size=1000.0,
            step_size=1000.0, decimal_places=0, leverage=30.0,
            pip_value_usd=0.1, base_ccy="USD", quote_ccy="JPY",
        ),
        _time_mod.monotonic(),
    )
    jpy_sig = RawSignal(
        signal_id="jpy_sig", strategy_id="volume_burst", symbol="USDJPY",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="cfd_intraday",
    )
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=jpy_sig, venue="capital", symbol="USDJPY",
        asset_class="fx", underlying_group_id="fx:USDJPY",
        notional_usd=1000.0, last_price=fill_price, now_ts=now,
        real_roundtrip=True, capital_session=object(),
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT risk_usd FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert row is not None and row[0] is not None
    risk_usd = float(row[0])
    # Raw (unconverted) would be >= 150 * 0.005 (notional floor pct) * 1000 =
    # 750+ (JPY-scale). USD-converted (rate ~1/150) must be well under $50 —
    # the notional is $1000, and 0.5% of that is $5, comfortably below the
    # raw-JPY-scale value.
    assert risk_usd < 50.0
    assert risk_usd > 0.0


# ---------------------------------------------------------------------------
# (d) real_roundtrip=False regression — simulate path only, adapter untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_sim_does_not_call_adapter(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter()
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("sim"), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=False, okx_adapter=okx_adapter,
    )
    assert trade is not None
    okx_adapter.place_market_order.assert_not_awaited()
    okx_adapter.fetch_order.assert_not_awaited()


# ---------------------------------------------------------------------------
# _real_open_fill helper — OKX + Capital dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_open_fill_okx_dispatch() -> None:
    okx_adapter = _make_okx_adapter()
    attempt = await _real_open_fill(
        venue="okx", symbol="BTC-USDT", side="long", notional_usd=100.0,
        last_price=60_000.0, strategy_id="volume_burst",
        okx_adapter=okx_adapter, capital_session=None,
    )
    assert attempt.fill is not None
    assert isinstance(attempt.fill, Fill)
    assert attempt.fill.order_id == "ord_live_1"
    assert attempt.deal_id is None  # OKX closes by base_qty, not deal_id


@pytest.mark.asyncio
async def test_real_open_fill_okx_reject_returns_attempt_with_code() -> None:
    okx_adapter = _make_okx_adapter(ok=False)
    attempt = await _real_open_fill(
        venue="okx", symbol="BTC-USDT", side="long", notional_usd=100.0,
        last_price=60_000.0, strategy_id="volume_burst",
        okx_adapter=okx_adapter, capital_session=None,
    )
    assert attempt.fill is None
    # _okx_order_resp(ok=False) carries code="0"; reject_code is propagated.
    assert attempt.reject_code == "0"


# ---------------------------------------------------------------------------
# H5 — Alpaca (equity) dispatch through _real_open_fill (adapter MOCKED).
# ---------------------------------------------------------------------------


def _make_alpaca_adapter(*, filled: bool = True) -> AsyncMock:
    from polaris.venues.alpaca.adapter import AlpacaOrderResponse

    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock(
        return_value=AlpacaOrderResponse(
            ok=True, venue_order_id="alp-1", client_order_id="clA",
            code="accepted", msg="accepted", raw={},
        )
    )
    row = {
        "id": "alp-1", "symbol": "AAPL", "side": "buy",
        "status": "filled" if filled else "new",
        "filled_qty": "1.05" if filled else "0",
        "filled_avg_price": "190.0" if filled else None,
    }
    adapter.fetch_order = AsyncMock(return_value=row)
    return adapter


@pytest.mark.asyncio
async def test_real_open_fill_alpaca_equity_dispatch() -> None:
    """An equity venue routes to the (mock) Alpaca open fill; long → buy."""
    adapter = _make_alpaca_adapter(filled=True)
    attempt = await _real_open_fill(
        venue="alpaca", symbol="AAPL", side="long", notional_usd=200.0,
        last_price=190.0, strategy_id="equity_tsmom", asset_class="equity",
        alpaca_adapter=adapter,
    )
    assert attempt.fill is not None
    assert isinstance(attempt.fill, Fill)
    adapter.place_market_order.assert_awaited_once()
    assert adapter.place_market_order.await_args.kwargs["side"] == "buy"
    # Equity closes by base_qty (cash, no deal_id) — like OKX spot.
    assert attempt.deal_id is None


@pytest.mark.asyncio
async def test_pipeline_threads_alpaca_adapter_to_reserve(
    memdb: sqlite3.Connection,
) -> None:
    """run_pipeline_for_signal must forward the injected ``alpaca_adapter`` to
    the ``reserve_and_submit`` closure (mirror of the okx_adapter threading)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import _all_strategies

    reset_process_fence()
    sentinel = _make_alpaca_adapter()
    captured: dict[str, Any] = {}
    # Live-feed precondition: the Alpaca dead-feed entry HALT (Jin 2026-06-22)
    # holds NEW Alpaca entries when the feed is stale. Seed a FRESH Alpaca bar so
    # the data-health gate passes and the adapter-threading path under test runs.
    from polaris.core.data.ingest import persist_bars
    from polaris.core.data.schema import Bar
    persist_bars(memdb, [Bar(
        instrument_id="alpaca:AAPL", underlying_group_id="equity:AAPL",
        venue="alpaca", symbol="AAPL", bar_interval="1D", ts=int(time.time()) - 600,
        open=189.0, high=191.0, low=188.0, close=190.0, volume=1e6,
        notional_usd=1.9e8, trade_count=5000, vwap=190.0, bid_close=0.0,
        ask_close=0.0, spread_bps_close=0.0, source="alpaca_rest",
    )])

    async def _spy_reserve(**kwargs: Any) -> SimulatedTrade | None:
        captured["alpaca_adapter"] = kwargs.get("alpaca_adapter")
        fill, trade = simulate_open_fill(
            signal=kwargs["sig"], venue=kwargs["venue"],
            last_price=kwargs["last_price"], notional_usd=kwargs["notional_usd"],
        )
        trade.position_id = "pos_eq_spy"
        return trade

    # The Alpaca adapter is threaded by VENUE, not by the strategy object — the
    # equity_* strategies were KILLed, so any preserved strategy carries the
    # alpaca/equity signal through the pipeline for this adapter-threading check.
    carrier_strat = _all_strategies()[0]
    eq_sig = RawSignal(
        signal_id="eq_thread", strategy_id="equity_tsmom", symbol="AAPL",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )
    await run_pipeline_for_signal(
        conn=memdb, haiku=StubGPTClient(), state=ProdLoopState(),
        strategy=carrier_strat, sig=eq_sig, venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", regime="bull_trend",
        bars_atr_pct=0.02, last_price=190.0,
        universe_rows=[{"venue": "alpaca", "symbol": "AAPL", "vol_24h_usd": 2e9}],
        now_ts=int(time.time()), reserve_and_submit=_spy_reserve,
        real_roundtrip=True, alpaca_adapter=sentinel,
    )
    assert captured.get("alpaca_adapter") is sentinel


# ---------------------------------------------------------------------------
# (c) flag threading e2e — run_pipeline_for_signal forwards real_roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_threads_real_roundtrip_to_reserve(
    memdb: sqlite3.Connection,
) -> None:
    """run_pipeline_for_signal must forward ``real_roundtrip`` to the
    injected ``reserve_and_submit`` closure (adapter boundary mocked)."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import _all_strategies

    reset_process_fence()
    memdb.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        " depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts, "
        " is_active, active_reason) "
        "VALUES ('okx', 'BTC-USDT', 'okx:BTC-USDT', 'crypto:BTC', 'crypto', "
        "        'USDT', 'live', 1e9, 2.0, 3.0, 1e6, 0.0, NULL, ?, 1, NULL)",
        (int(time.time()),),
    )
    captured: dict[str, Any] = {}

    async def _spy_reserve(**kwargs: Any) -> SimulatedTrade | None:
        captured["real_roundtrip"] = kwargs.get("real_roundtrip")
        fill, trade = simulate_open_fill(
            signal=kwargs["sig"], venue=kwargs["venue"],
            last_price=kwargs["last_price"], notional_usd=kwargs["notional_usd"],
        )
        trade.position_id = "pos_spy"
        return trade

    await run_pipeline_for_signal(
        conn=memdb, haiku=StubGPTClient(), state=ProdLoopState(),
        strategy=_all_strategies()[0], sig=_sig("thread"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.02, last_price=60_000.0,
        universe_rows=[{"venue": "okx", "symbol": "BTC-USDT", "vol_24h_usd": 2e9}],
        now_ts=int(time.time()), reserve_and_submit=_spy_reserve,
        real_roundtrip=True,
    )
    assert captured.get("real_roundtrip") is True


# ---------------------------------------------------------------------------
# Close path — real_roundtrip OKX sells the entry base_qty
# ---------------------------------------------------------------------------


def _seed_pos_and_entry(
    memdb: sqlite3.Connection, pos_id: str, *, risk_usd: float = 100.0,
) -> None:
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'volume_burst', "
        "        'volume_burst', 'volume_burst', 'long', 0.001, 'open', ?, 0, ?)",
        (pos_id, int(time.time()), risk_usd),
    )
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f_rt', 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o_rt', ?, 0.0, 0, "
        "        0.001666, 100.0, 'filled')",
        (int(time.time() * 1000), pos_id),
    )


@pytest.mark.asyncio
async def test_close_real_roundtrip_okx_sells_base_qty(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.scripts._production_close import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_rt_close"
    _seed_pos_and_entry(memdb, pos_id)
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_rt", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=60_000.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
        venue_order_id="o_rt", base_qty=0.001666,
    )
    state.open_trades.append(trade)

    okx_adapter = AsyncMock()
    okx_adapter.place_market_order = AsyncMock(return_value=_okx_order_resp(ok=True, ord_id="sell_1"))
    sell_row = _okx_filled_row(price=60_600.0)
    sell_row["side"] = "sell"
    sell_row["ordId"] = "sell_1"
    okx_adapter.fetch_order = AsyncMock(return_value={"data": [sell_row]})
    # Balance lookup returns no base-ccy detail → close-clamp skipped (the real
    # adapter returns a plain dict; AsyncMock's auto-coroutine would otherwise
    # leak an un-awaited coroutine warning).
    okx_adapter.fetch_balance = AsyncMock(return_value={"data": []})

    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime, real_roundtrip=True,
        okx_adapter=okx_adapter,
    )
    # Adapter sell leg was driven.
    okx_adapter.place_market_order.assert_awaited_once()
    assert okx_adapter.place_market_order.await_args.kwargs["side"] == "sell"
    # Close fill persisted from the real exit price (60_600).
    close_row = memdb.execute(
        "SELECT fill_price, is_close FROM fills "
        "WHERE contribution_id = ? AND is_close = 1",
        (pos_id,),
    ).fetchone()
    assert close_row is not None
    assert abs(float(close_row[0]) - 60_600.0) < 1e-6
    assert len(state.closed_trades) == 1


@pytest.mark.asyncio
async def test_close_sim_does_not_call_adapter(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.scripts._production_close import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_sim_close"
    _seed_pos_and_entry(memdb, pos_id)
    for ts_off in range(20):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            "        60000.0, 60100.0, 59900.0, ?, 1000.0, 6e7, 0, 0, 0, 0, 0, 'test')",
            (1_700_000_000 + ts_off * 60, 60_000.0 + ts_off * 10),
        )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_sim", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=60_000.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
    )
    state.open_trades.append(trade)
    okx_adapter = AsyncMock()
    okx_adapter.place_market_order = AsyncMock()
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime, real_roundtrip=False,
        okx_adapter=okx_adapter,
    )
    okx_adapter.place_market_order.assert_not_awaited()
    assert len(state.closed_trades) == 1


# ===========================================================================
# codex review round 2 — real-order safety defects (P0-1..5, P1-6, P2-7)
# ===========================================================================


# ---------------------------------------------------------------------------
# P0-1 — real_roundtrip must refuse the shared sim DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_roundtrip_refuses_sim_db(tmp_path: Any) -> None:
    """real_roundtrip=True against data/polaris.sqlite must raise (no co-mingle)."""
    from pathlib import Path

    from polaris.scripts.production_paper_loop import run_production_paper_loop

    with pytest.raises(ValueError, match="real_roundtrip"):
        await run_production_paper_loop(
            duration_sec=0.0, tick_sec=0.0,
            db_path=Path("data/polaris.sqlite"),
            haiku=object(), real_roundtrip=True,
        )


@pytest.mark.asyncio
async def test_real_roundtrip_refuses_default_db(tmp_path: Any) -> None:
    """db_path=None defaults to the sim DB → must also raise under real."""
    from polaris.scripts.production_paper_loop import run_production_paper_loop

    with pytest.raises(ValueError, match="real_roundtrip"):
        await run_production_paper_loop(
            duration_sec=0.0, tick_sec=0.0, db_path=None,
            haiku=object(), real_roundtrip=True,
        )


# ---------------------------------------------------------------------------
# P0-2 — adapter EXCEPTION during real open must release + fault (not escape)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_and_submit_real_open_exception_releases(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = AsyncMock()
    okx_adapter.place_market_order = AsyncMock(
        side_effect=RuntimeError("venue timeout")
    )
    okx_adapter.fetch_order = AsyncMock()
    okx_adapter.fetch_ticker = AsyncMock(return_value={})  # no bid → market leg
    # Must NOT propagate the exception; must release + fault + None.
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("exc"), venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=100.0, last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is None
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is None or res[0] != "confirmed"
    assert state.fault_events == 1


# ---------------------------------------------------------------------------
# P0-3 — confirm_reservation failure after a real fill must record an orphan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_failure_after_real_fill_records_orphan(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.isolation.allocator_fence import (
        ReservationConflictError,
        get_process_fence,
        reset_process_fence,
    )

    reset_process_fence()
    fence = get_process_fence(memdb)

    async def _boom_confirm(*args: Any, **kwargs: Any) -> Any:
        raise ReservationConflictError("confirm lost the race")

    monkeypatch.setattr(fence, "confirm_reservation", _boom_confirm)
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter(ok=True, filled=True)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("orphan_c"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is None
    # Durable orphan record carrying the venue ref (ord_live_1) so a
    # reconciliation pass can find + close the untracked venue position.
    orphan = memdb.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'venue_orphan'"
    ).fetchone()
    assert orphan is not None
    assert "ord_live_1" in orphan[0]
    assert state.fault_events == 1


# ---------------------------------------------------------------------------
# P0-4 — persist failure after a real fill must record an orphan + release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_failure_after_real_fill_records_orphan(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    okx_adapter = _make_okx_adapter(ok=True, filled=True)

    import polaris.scripts._production_pipeline as pipe_mod

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.Error("persist failed")

    monkeypatch.setattr(pipe_mod, "persist_fill", _boom)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("orphan_p"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=okx_adapter,
    )
    assert trade is None
    orphan = memdb.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'venue_orphan'"
    ).fetchone()
    assert orphan is not None
    assert "ord_live_1" in orphan[0]
    # Reservation released so the order_key is not leaked.
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='volume_burst'"
    ).fetchone()
    assert res is None or res[0] != "confirmed"


# ---------------------------------------------------------------------------
# P0-4b — sim persist failure must NOT record a venue_orphan (no real position)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_persist_failure_no_orphan(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence

    reset_process_fence()
    state = ProdLoopState()
    import polaris.scripts._production_pipeline as pipe_mod

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.Error("persist failed")

    monkeypatch.setattr(pipe_mod, "persist_fill", _boom)
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=_sig("sim_pf"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=int(time.time()),
        real_roundtrip=False,
    )
    assert trade is None
    orphan = memdb.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = 'venue_orphan'"
    ).fetchone()[0]
    assert int(orphan) == 0


# ---------------------------------------------------------------------------
# P0-5 — open persists deal_id/venue_order_id; hydration restores them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_open_persists_deal_id_and_hydrate_restores(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.lifecycle.recover import hydrate_open_positions

    reset_process_fence()
    state = ProdLoopState()

    # Mock Capital session + adapter via the open path: open → confirm.
    cap_session = AsyncMock()

    class _OpenResp:
        ok = True
        deal_reference = "ref_1"
        deal_id = "deal_top"

    class _CloseResp:
        ok = True
        deal_reference = "cref"

    confirm_open = {
        "epic": "EURUSD", "direction": "BUY", "level": 1.105, "size": 1.0,
        "dealStatus": "ACCEPTED", "status": "OPEN", "dealId": "deal_top",
        "affectedDeals": [{"dealId": "deal_position_99", "status": "OPENED"}],
        "date": "2026-05-28T00:00:00Z",
    }
    sig = RawSignal(
        signal_id="cap_open", strategy_id="fx_breakout", symbol="EURUSD",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="fx_intraday",
    )

    import polaris.scripts._production_pipeline as pipe_mod

    # Patch CapitalAdapter so the real-open path drives our mock.
    class _FakeCapAdapter:
        def __init__(self, _session: Any) -> None: ...
        async def open_position(self, **kwargs: Any) -> Any:
            return _OpenResp()
        async def confirm(self, _ref: str) -> dict[str, Any]:
            return confirm_open

    monkeypatch_target = pipe_mod.CapitalAdapter
    pipe_mod.CapitalAdapter = _FakeCapAdapter  # type: ignore[misc,assignment]
    try:
        trade = await _reserve_and_submit(
            conn=memdb, state=state, sig=sig, venue="capital", symbol="EURUSD",
            asset_class="forex", underlying_group_id="forex:EURUSD",
            notional_usd=100.0, last_price=1.105, now_ts=int(time.time()),
            real_roundtrip=True, capital_session=cap_session,
        )
    finally:
        pipe_mod.CapitalAdapter = monkeypatch_target  # type: ignore[misc]

    assert trade is not None
    assert trade.deal_id == "deal_position_99"
    # Restart: hydrate must restore deal_id (+ venue_order_id) from persisted state.
    restored = hydrate_open_positions(memdb)
    assert len(restored) == 1
    assert restored[0].deal_id == "deal_position_99"


# ---------------------------------------------------------------------------
# P1-6 — real close recomputes pnl_r from the real exit price
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_close_recomputes_pnl_r_from_exit(
    memdb: sqlite3.Connection,
) -> None:
    """A loss exit (sell below entry) must produce pnl_r < 0 even though the
    seeded bars trended *up* (pre-close bar-based pnl_r would be positive)."""
    from polaris.scripts._production_close import close_oldest_with_real_pnl
    from polaris.scripts.production_paper_loop import _lookup_regime

    pos_id = "pos_pnl_r"
    _seed_pos_and_entry(memdb, pos_id)
    # Seed bars trending UP (bar-based pnl_r would be positive).
    for ts_off in range(20):
        memdb.execute(
            "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, "
            " venue, symbol, bar_interval, ts, open, high, low, close, volume, "
            " notional_usd, trade_count, vwap, bid_close, ask_close, "
            " spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            "        60000.0, 61000.0, 59000.0, ?, 1000.0, 6e7, 0, 0, 0, 0, 0, 'test')",
            (1_700_000_000 + ts_off * 60, 61_000.0 + ts_off * 50),
        )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id="t_pnlr", venue="okx", symbol="BTC-USDT",
        strategy_id="volume_burst", side="long", entry_price=60_000.0,
        notional_usd=100.0, open_ts=int(time.time()), position_id=pos_id,
        venue_order_id="o_rt", base_qty=0.001666,
    )
    state.open_trades.append(trade)

    okx_adapter = AsyncMock()
    okx_adapter.place_market_order = AsyncMock(
        return_value=_okx_order_resp(ok=True, ord_id="sell_loss")
    )
    # Real exit at 55_000 — a LOSS for the long entered at 60_000.
    sell_row = _okx_filled_row(price=55_000.0)
    sell_row["side"] = "sell"
    sell_row["ordId"] = "sell_loss"
    okx_adapter.fetch_order = AsyncMock(return_value={"data": [sell_row]})
    okx_adapter.fetch_balance = AsyncMock(return_value={"data": []})

    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=int(time.time()),
        lookup_regime=_lookup_regime, real_roundtrip=True,
        okx_adapter=okx_adapter,
    )
    assert len(state.closed_trades) == 1
    # pnl_r must reflect the REAL loss exit, not the up-trending bars.
    assert state.closed_trades[0].pnl_r < 0.0
    close_row = memdb.execute(
        "SELECT pnl_usd FROM fills WHERE contribution_id = ? AND is_close = 1",
        (pos_id,),
    ).fetchone()
    assert float(close_row[0]) < 0.0


# ---------------------------------------------------------------------------
# P2-7 — shared okx_adapter threads through run_pipeline_for_signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_threads_shared_okx_adapter(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import _all_strategies

    reset_process_fence()
    memdb.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        " depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts, "
        " is_active, active_reason) "
        "VALUES ('okx', 'BTC-USDT', 'okx:BTC-USDT', 'crypto:BTC', 'crypto', "
        "        'USDT', 'live', 1e9, 2.0, 3.0, 1e6, 0.0, NULL, ?, 1, NULL)",
        (int(time.time()),),
    )
    captured: dict[str, Any] = {}

    async def _spy_reserve(**kwargs: Any) -> SimulatedTrade | None:
        captured["okx_adapter"] = kwargs.get("okx_adapter")
        fill, trade = simulate_open_fill(
            signal=kwargs["sig"], venue=kwargs["venue"],
            last_price=kwargs["last_price"], notional_usd=kwargs["notional_usd"],
        )
        trade.position_id = "pos_spy2"
        return trade

    sentinel = object()
    await run_pipeline_for_signal(
        conn=memdb, haiku=StubGPTClient(), state=ProdLoopState(),
        strategy=_all_strategies()[0], sig=_sig("shared"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.02, last_price=60_000.0,
        universe_rows=[{"venue": "okx", "symbol": "BTC-USDT", "vol_24h_usd": 2e9}],
        now_ts=int(time.time()), reserve_and_submit=_spy_reserve,
        real_roundtrip=True, okx_adapter=sentinel,
    )
    assert captured.get("okx_adapter") is sentinel


# ===========================================================================
# Capital open leg — D-1 honest propagation + B1 confirm-poll exception guard
# + B2 confirm-stall honest label. Adapters are FAKES; no venue network.
# ===========================================================================


def _cap_deal_resp(
    *, ok: bool, status: str, deal_ref: str | None = None,
    reason: str = "", http_status: int = 200,
) -> Any:
    from polaris.venues.capital.adapter import CapitalDealResponse

    return CapitalDealResponse(
        ok=ok, deal_reference=deal_ref, deal_id=None, status=status,
        reason=reason, raw={}, http_status=http_status,
    )


_CAP_CONFIRM_ACCEPTED: dict[str, Any] = {
    "epic": "EURUSD", "direction": "BUY", "level": 1.105, "size": 1.0,
    "dealStatus": "ACCEPTED", "status": "OPEN", "dealId": "deal_top",
    "affectedDeals": [{"dealId": "deal_pos_1", "status": "OPENED"}],
    "date": "2026-05-28T00:00:00Z",
}


class _ScriptedCapAdapter:
    """Fake CapitalAdapter: scripted open response + per-poll confirm script
    (a dict is returned; an Exception instance is raised). The last script
    entry repeats once the script is exhausted."""

    def __init__(self, open_resp: Any, confirms: list[Any]) -> None:
        self._open_resp = open_resp
        self._confirms = list(confirms)
        self.confirm_calls = 0

    async def open_position(self, **_kwargs: Any) -> Any:
        return self._open_resp

    async def confirm(self, _ref: str) -> dict[str, Any]:
        self.confirm_calls += 1
        item = self._confirms.pop(0) if len(self._confirms) > 1 else self._confirms[0]
        if isinstance(item, Exception):
            raise item
        return dict(item)


@pytest.mark.asyncio
async def test_real_capital_open_fill_http_error_reject_code() -> None:
    """D-1 propagation: an adapter HTTP error surfaces its honest label as the
    OpenAttempt.reject_code — never the fake 'PENDING'."""
    from polaris.scripts._smoke_roundtrip_capital import real_capital_open_fill

    adapter = _ScriptedCapAdapter(
        _cap_deal_resp(
            ok=False, status="HTTP_429",
            reason="error.too-many.requests", http_status=429,
        ),
        confirms=[{}],
    )
    attempt = await real_capital_open_fill(
        adapter, epic="EURUSD", direction="BUY", size=1.0,
        strategy_id="fx_breakout_basket", poll_delay_sec=0.0,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "HTTP_429"
    assert attempt.reject_code != "PENDING"
    assert adapter.confirm_calls == 0  # no confirm poll on an open reject


@pytest.mark.asyncio
async def test_capital_confirm_stall_pending_honest_label() -> None:
    """B2: a confirm that stays PENDING for the whole poll budget is a venue
    stall — distinct honest label CONFIRM_STALL_PENDING (external), never the
    extinct 'PENDING' mislabel."""
    from polaris.scripts._smoke_roundtrip_capital import real_capital_open_fill

    adapter = _ScriptedCapAdapter(
        _cap_deal_resp(ok=True, status="PENDING", deal_ref="REF1"),
        confirms=[{"dealStatus": "PENDING"}],
    )
    attempt = await real_capital_open_fill(
        adapter, epic="EURUSD", direction="BUY", size=1.0,
        strategy_id="fx_breakout_basket", poll_delay_sec=0.0,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "CONFIRM_STALL_PENDING"


@pytest.mark.asyncio
async def test_capital_open_confirm_httpx_error_never_raises() -> None:
    """B1: an open-leg confirm poll that fails at the HTTP layer (429 storm /
    timeout) must NOT escape to the FAULT_EXCEPTION backstop (3/300s →
    HARD_HALT) — it polls within budget and returns the honest HTTP_CONFIRM."""
    import httpx

    from polaris.scripts._smoke_roundtrip_capital import real_capital_open_fill

    adapter = _ScriptedCapAdapter(
        _cap_deal_resp(ok=True, status="PENDING", deal_ref="REF1"),
        confirms=[httpx.ReadTimeout("slow confirm")],
    )
    attempt = await real_capital_open_fill(
        adapter, epic="EURUSD", direction="BUY", size=1.0,
        strategy_id="fx_breakout_basket", poll_delay_sec=0.0,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "HTTP_CONFIRM"


@pytest.mark.asyncio
async def test_capital_open_confirm_recovers_after_transient_error() -> None:
    """A transient confirm failure followed by ACCEPTED still fills — the
    B1 guard keeps polling instead of aborting on the first error."""
    import httpx

    from polaris.scripts._smoke_roundtrip_capital import real_capital_open_fill

    adapter = _ScriptedCapAdapter(
        _cap_deal_resp(ok=True, status="PENDING", deal_ref="REF1"),
        confirms=[httpx.ReadTimeout("blip"), _CAP_CONFIRM_ACCEPTED],
    )
    attempt = await real_capital_open_fill(
        adapter, epic="EURUSD", direction="BUY", size=1.0,
        strategy_id="fx_breakout_basket", poll_delay_sec=0.0,
    )
    assert attempt.fill is not None
    assert attempt.deal_id == "deal_pos_1"
    assert attempt.reject_code is None


@pytest.mark.asyncio
async def test_capital_confirm_timeout_no_fault_e2e(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1 e2e: a confirm-leg timeout through reserve_and_submit must release
    the reservation with NO fault (was: FAULT_EXCEPTION x40 live, 39 with the
    empty-str timeout signature) and leave the breaker ACTIVE."""
    import httpx

    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.core.isolation.circuit_breaker import ACTIVE, current_strategy_mode
    from polaris.scripts import _production_pipeline as pipe_mod

    reset_process_fence()
    monkeypatch.setattr(
        "polaris.scripts._smoke_roundtrip_capital._CONFIRM_POLLS", 1
    )

    class _FakeCapAdapter:
        def __init__(self, _session: Any) -> None: ...

        async def open_position(self, **_kwargs: Any) -> Any:
            return _cap_deal_resp(ok=True, status="PENDING", deal_ref="REF_T")

        async def confirm(self, _ref: str) -> dict[str, Any]:
            raise httpx.ReadTimeout("confirm timeout")

    monkeypatch.setattr(pipe_mod, "CapitalAdapter", _FakeCapAdapter)
    state = ProdLoopState()
    sig = RawSignal(
        signal_id="cap_to", strategy_id="fx_breakout_basket", symbol="EURUSD",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="fx_intraday",
    )
    now = int(time.time())
    trade = await _reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="capital", symbol="EURUSD",
        asset_class="forex", underlying_group_id="forex:EURUSD",
        notional_usd=100.0, last_price=1.105, now_ts=now,
        real_roundtrip=True, capital_session=AsyncMock(),
    )
    assert trade is None
    # NO fault of any kind — venue/transport event, flow preserved.
    assert state.fault_events == 0
    fault = memdb.execute(
        "SELECT COUNT(*) FROM strategy_fault_events"
    ).fetchone()[0]
    assert int(fault) == 0
    assert state.venue_rejects_by_code.get("HTTP_CONFIRM") == 1
    assert current_strategy_mode(memdb, "fx_breakout_basket", now_ts=now + 10) == ACTIVE
    # Reservation released (not confirmed, not leaked).
    res = memdb.execute(
        "SELECT status FROM allocator_reservations WHERE strategy_id='fx_breakout_basket'"
    ).fetchone()
    assert res is None or res[0] != "confirmed"
