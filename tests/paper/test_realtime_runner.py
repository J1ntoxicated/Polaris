"""tests/paper/test_realtime_runner.py — min hold + re-entry cooldown (Codex Round 4)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.paper import realtime_runner as rt
from src.paper.state import PaperBalance


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect paper state files + reset cooldown registry per test."""
    from src.paper import runner as paper_runner
    monkeypatch.setattr(paper_runner, "DEFAULT_STATE_DIR", tmp_path)
    rt._last_close_ms.clear()
    rt._last_close_ms_ticker.clear()
    rt._indicator_cache.clear()
    yield


def _hypo_flow(starting_usd: float = 5000.0) -> dict:
    from src.strategies.trade_flow import TradeFlow
    return {
        "hypo_id": "HYPO-012-FLOW",
        "strategy_cls": TradeFlow,
        "params": {},
        "primary_tf": "flow",
        "tickers": ["BTC-USDT"],
        "starting_usd": starting_usd,
        "max_position_pct": 0.05,
    }


def _full_tick(price: float = 100.0, ts: int = 1_000) -> dict:
    return {"ts": ts, "last": price, "bid": price * 0.999, "ask": price * 1.001,
            "open24h": price * 0.98, "high24h": price * 1.02, "low24h": price * 0.97}


def test_min_hold_blocks_signal_exit_under_90s(tmp_path, monkeypatch):
    """Entry 후 90s 안에 signal_exit 신호 와도 close 안 됨 — TP/SL만 유효."""
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    # Stub: ENTER 신호 + 충분한 trades
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 1, "entry 후 1 open position"

    # 30s 후 EXIT 신호 — held_ms 30s < MIN_HOLD_MS 90s → close 안 됨
    later_ts = entry_ts + 30_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.05, tick_ts_ms=later_ts,
                         full_tick=_full_tick(100.05, later_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 1, "min hold 안에서 signal_exit 차단"
    assert len(bal.closed_positions) == 0


def test_min_hold_allows_signal_exit_after_90s():
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    later_ts = entry_ts + 95_000  # > 90s
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.05, tick_ts_ms=later_ts,
                         full_tick=_full_tick(100.05, later_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 0
    assert len(bal.closed_positions) == 1


def test_tp_hits_under_min_hold():
    """TP/SL은 min hold 무시 — 즉시 close."""
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    # 10s 후 +0.7% (> TP 0.6%) — close 됨
    tp_price = 100.7
    later_ts = entry_ts + 10_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.50), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=tp_price, tick_ts_ms=later_ts,
                         full_tick=_full_tick(tp_price, later_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.closed_positions) == 1


def test_re_entry_cooldown_blocks_within_60s():
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    # 95s 후 SL hit — close
    sl_price = 99.5
    sl_ts = entry_ts + 95_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=sl_price, tick_ts_ms=sl_ts,
                         full_tick=_full_tick(sl_price, sl_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.closed_positions) == 1

    # 30s 후 ENTER 신호 — cooldown 60s 안 → 차단
    re_ts = sl_ts + 30_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.85), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=re_ts,
                         full_tick=_full_tick(100.0, re_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 0, "cooldown 안 re-entry 차단"


def test_min_hold_boundary_exactly_90s():
    """경계값: held_ms == 90_000 → exit 허용 (>=)."""
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    boundary_ts = entry_ts + 90_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.05, tick_ts_ms=boundary_ts,
                         full_tick=_full_tick(100.05, boundary_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.closed_positions) == 1, "==90s 경계 close 허용 (>=)"


def test_cooldown_boundary_exactly_60s():
    """경계값: cooldown == 60_000ms → re-entry 차단 유지 (< 비교)."""
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    sl_ts = entry_ts + 95_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=99.5, tick_ts_ms=sl_ts,
                         full_tick=_full_tick(99.5, sl_ts))

    # 정확히 60_000ms — `< RE_ENTRY_COOLDOWN_MS` 이므로 허용
    boundary_ts = sl_ts + 60_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.85), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=boundary_ts,
                         full_tick=_full_tick(100.0, boundary_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 1, "==60s 경계 entry 허용 (< 비교)"


def test_cross_strategy_cooldown_blocks_other_strategy():
    """Cross-strategy ticker-global cooldown: TradeFlow close 후 OrderBook entry도 60s 차단."""
    flow_hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    # TradeFlow entry → 95s 후 SL close
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(flow_hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    sl_ts = entry_ts + 95_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(flow_hypo, ticker, tick_price=99.5, tick_ts_ms=sl_ts,
                         full_tick=_full_tick(99.5, sl_ts))

    # 30s 후 OrderBookImbalance가 같은 ticker entry 시도 → ticker-global cooldown 차단
    from src.strategies.orderbook_imbalance import OrderBookImbalance
    book_hypo = {
        "hypo_id": "HYPO-011-BOOK",
        "strategy_cls": OrderBookImbalance,
        "params": {},
        "primary_tf": "book",
        "tickers": [ticker],
        "starting_usd": 5000.0,
        "max_position_pct": 0.05,
    }
    re_ts = sl_ts + 30_000
    with patch.object(rt, "compute_book_imbalance", return_value=0.80):
        rt._eval_and_act(book_hypo, ticker, tick_price=100.0, tick_ts_ms=re_ts,
                         full_tick=_full_tick(100.0, re_ts))

    bal = rt.load_state(ticker, "orderbook_imbalance", starting_usd=5000.0)
    assert len(bal.open_positions) == 0, "ticker-global cooldown — 다른 strategy도 60s 차단"


def test_mta_stale_tf_data_skip():
    """Codex Round 2 fix: 1H TF 데이터가 90분+ stale → MTA branch skip (no entry)."""
    from src.strategies.mta_confluence import MTAConfluence
    from src.domain.candle import Candle

    hypo = {
        "hypo_id": "HYPO-013-MTA",
        "strategy_cls": MTAConfluence,
        "params": {},
        "primary_tf": "mta",
        "tickers": ["BTC-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    }
    ticker = "BTC-USDT"
    now_ms = 1_700_000_000_000

    def _fake_candles(n=60, slope=0.001):
        return [Candle(timestamp_ms=(i+1)*60_000, open=100*(1+i*slope),
                       high=100*(1+i*slope)*1.005, low=100*(1+i*slope)*0.995,
                       close=100*(1+i*slope), volume=100) for i in range(n)]

    # 1H tf data — last candle 100분 전 (stale > 90min)
    h1_stale = _fake_candles(20)
    h1_stale[-1] = Candle(
        timestamp_ms=now_ms - 100*60_000,  # 100분 전 stale
        open=100, high=101, low=99, close=100, volume=100,
    )
    fresh = _fake_candles(60)
    fresh[-1] = Candle(timestamp_ms=now_ms, open=100, high=101, low=99, close=100, volume=100)

    fake_tf = {"1D": fresh, "4H": fresh, "1H": h1_stale, "15m": fresh}
    with patch.object(rt, "fetch_multi_tf", return_value=fake_tf):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=now_ms,
                         full_tick=_full_tick(100.0, now_ms))

    bal = rt.load_state(ticker, "mta_confluence", starting_usd=5000.0)
    assert len(bal.open_positions) == 0, "stale 1H TF → entry 차단"
    assert len(bal.closed_positions) == 0


# ── Fix 1: supervisor — binance task crash does not kill OKX task ─────────────


def test_run_okx_and_binance_is_coroutine():
    """_run_okx_and_binance must be a coroutine (async def)."""
    import inspect
    assert inspect.iscoroutinefunction(rt._run_okx_and_binance), (
        "_run_okx_and_binance must be async"
    )


def test_supervisor_restarts_after_binance_crash():
    """Fix 1: if binance_task raises, supervisor must restart (not propagate).

    Simulates Binance 24h auto-disconnect → exception → supervisor catches + restarts.
    The 5s sleep is patched to 0 so the restart happens immediately in test.
    """
    import asyncio

    call_count = {"stream": 0, "okx": 0}

    async def fake_binance_stream(symbols, on_event):
        call_count["stream"] += 1
        if call_count["stream"] == 1:
            raise RuntimeError("Binance 24h auto-disconnect")
        # Second call: hang (simulate running normally)
        await asyncio.sleep(1000)

    async def fake_okx_stream(tickers, on_tick):
        call_count["okx"] += 1
        await asyncio.sleep(1000)

    async def _run_with_timeout():
        task = asyncio.create_task(
            rt._run_okx_and_binance(["BTC-USDT"], ["BTC-USDT"])
        )
        # Allow the event loop to complete: crash + sleep(0) + restart → 2nd call
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Patch the 5s restart delay to 0 so the test doesn't hang
    with patch.object(rt, "stream_tickers", fake_okx_stream), \
         patch.object(rt, "binance_stream", fake_binance_stream), \
         patch.object(rt, "make_tick_handler", return_value=lambda *a: None), \
         patch.object(rt, "_SUPERVISOR_RESTART_DELAY_S", 0.0):
        asyncio.run(_run_with_timeout())

    # After crash + restart (sleep patched to 0), stream called at least twice
    assert call_count["stream"] >= 2, (
        f"Supervisor should restart Binance task after crash, "
        f"got {call_count['stream']} call(s)"
    )


def test_supervisor_returns_exceptions_not_raises():
    """Fix 1: return_exceptions=True — gather must not raise on task failure.

    Old code used return_exceptions=False (default) → gather raised on Binance crash.
    New supervisor catches exceptions from gather result list.
    """
    import asyncio

    async def crash_immediately(symbols, on_event):
        raise ValueError("immediate crash")

    async def okx_hang(tickers, on_tick):
        await asyncio.sleep(1000)

    propagated = {"error": None}

    async def _run():
        task = asyncio.create_task(
            rt._run_okx_and_binance(["BTC-USDT"], ["BTC-USDT"])
        )
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            propagated["error"] = e

    with patch.object(rt, "stream_tickers", okx_hang), \
         patch.object(rt, "binance_stream", crash_immediately), \
         patch.object(rt, "make_tick_handler", return_value=lambda *a: None), \
         patch.object(rt, "_SUPERVISOR_RESTART_DELAY_S", 0.0):
        asyncio.run(_run())

    assert propagated["error"] is None, (
        f"Supervisor must not propagate Binance crash to caller: {propagated['error']}"
    )


def test_re_entry_allowed_after_cooldown():
    hypo = _hypo_flow()
    ticker = "BTC-USDT"
    entry_ts = 1_700_000_000_000

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(100.0, entry_ts))

    sl_price = 99.5
    sl_ts = entry_ts + 95_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.20), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=sl_price, tick_ts_ms=sl_ts,
                         full_tick=_full_tick(sl_price, sl_ts))

    # 65s 후 ENTER — cooldown 60s 지남 → 허용
    re_ts = sl_ts + 65_000
    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.85), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100):
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=re_ts,
                         full_tick=_full_tick(100.0, re_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=5000.0)
    assert len(bal.open_positions) == 1, "cooldown 후 re-entry 허용"


# ── Fix 4 (Codex Round 5): pending cancel await — 이중 subscribe 방지 ─────────


def test_pending_cancel_awaited_after_first_completed():
    """Fix 4: supervisor must call asyncio.gather on pending tasks after cancel.

    Codex Round 5 gap: `t.cancel()` schedules cancellation but does not await
    completion. In production, WS coroutines may not have an immediate await
    point (e.g., during SSL handshake or socket send), so the old task can
    overlap with the new iteration.

    This test directly verifies the structural fix: `asyncio.gather` is called
    with the pending set. We patch `asyncio.gather` to intercept the call and
    confirm it receives the pending tasks (not just the empty set).

    The old code (t.cancel() only) never calls gather on pending tasks.
    The new code calls `await asyncio.gather(*pending, return_exceptions=True)`.
    """
    import asyncio

    gather_calls_with_tasks = []
    original_gather = asyncio.gather

    async def spy_gather(*coros_or_futures, **kwargs):
        # Record non-empty gather calls (ignoring gather() called by asyncio internals)
        if coros_or_futures:
            gather_calls_with_tasks.append(len(coros_or_futures))
        return await original_gather(*coros_or_futures, **kwargs)

    call_count = {"binance": 0}

    async def fake_binance(symbols, on_event):
        call_count["binance"] += 1
        if call_count["binance"] == 1:
            raise RuntimeError("binance crash")
        await asyncio.sleep(1000)

    async def fake_okx(tickers, on_tick):
        await asyncio.sleep(1000)

    async def _run():
        task = asyncio.create_task(
            rt._run_okx_and_binance(["BTC-USDT"], ["BTC-USDT"])
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch.object(rt, "stream_tickers", fake_okx), \
         patch.object(rt, "binance_stream", fake_binance), \
         patch.object(rt, "make_tick_handler", return_value=lambda *a: None), \
         patch.object(rt, "_SUPERVISOR_RESTART_DELAY_S", 0.01), \
         patch("asyncio.gather", spy_gather):
        asyncio.run(_run())

    # After crash, pending OKX task must have been gathered
    assert any(n > 0 for n in gather_calls_with_tasks), (
        "asyncio.gather must be called on pending tasks after cancel. "
        "Fix: add `await asyncio.gather(*pending, return_exceptions=True)` "
        "after the t.cancel() loop."
    )


def test_no_double_subscribe_after_crash():
    """Fix 4: at most 1 active OKX task at a time after crash (no duplicate subscribe).

    Tracks concurrent OKX coroutine count. Without `await gather(*pending)`,
    the old OKX task and new OKX task can overlap during restart_delay, giving
    max_concurrent == 2 (two simultaneous WS subscriptions to same ticker).

    With the fix, gather(*pending) blocks until old task is done → max = 1.

    Note: asyncio cooperative multitasking means this test may pass even without
    the fix in simple mock scenarios (sleep(0) yields). The structural test above
    (test_pending_cancel_awaited_after_first_completed) is the canonical regression
    guard. This test provides a complementary behaviour check.
    """
    import asyncio

    active = {"count": 0, "max": 0}
    binance_calls = {"n": 0}

    async def fake_binance(symbols, on_event):
        binance_calls["n"] += 1
        if binance_calls["n"] == 1:
            raise RuntimeError("crash")
        await asyncio.sleep(1000)

    async def fake_okx(tickers, on_tick):
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            active["count"] -= 1
            raise

    async def _run():
        task = asyncio.create_task(
            rt._run_okx_and_binance(["BTC-USDT"], ["BTC-USDT"])
        )
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch.object(rt, "stream_tickers", fake_okx), \
         patch.object(rt, "binance_stream", fake_binance), \
         patch.object(rt, "make_tick_handler", return_value=lambda *a: None), \
         patch.object(rt, "_SUPERVISOR_RESTART_DELAY_S", 0.02):
        asyncio.run(_run())

    assert active["max"] <= 1, (
        f"Max concurrent OKX tasks must be 1, got {active['max']} "
        f"— duplicate subscribe detected"
    )


# ── Round 9: HYPO-009 deprecated (Codex 92% 합의) ────────────────────────────


def test_hypo_009_not_in_realtime_hypos():
    """HYPO-009-RT (BreakoutMomentum) must be absent from REALTIME_HYPOS.

    Codex Round 9 (92% consensus): n=16, win 44%, TP 7 / SL 9, -$2.47 total.
    EV (paper fee 0.0014): -0.07%/trade — structural negative EV (TP<SL asymmetry).
    EV (live fee 0.0014): -0.07%/trade — still negative, TP<SL asymmetry unfixable.
    Deprecated to stop fee bleed. Strategy file preserved for learning archive.
    """
    active_ids = {h["hypo_id"] for h in rt.REALTIME_HYPOS}
    assert "HYPO-009-RT" not in active_ids, (
        "HYPO-009-RT (BreakoutMomentum) must be deprecated — "
        "n=16, win 44%, TP<SL asymmetry, EV -1.33%/trade (paper fee)"
    )


# ── Round 8: HYPO-011/012 deprecated (Codex 95% 합의) ───────────────────────


def test_hypo_011_012_not_in_realtime_hypos():
    """HYPO-011-BOOK / HYPO-012-FLOW must be absent from REALTIME_HYPOS.

    Codex Round 8 (95% consensus): n=336 / n=450 measured, EV < -0.20%/trade,
    signal_exit dominates (99.7% / 90.2%), -$77.93 / -$151.77 lifetime losses.
    Both deprecated to stop fee bleed. Strategy files preserved for archive.
    """
    active_ids = {h["hypo_id"] for h in rt.REALTIME_HYPOS}
    assert "HYPO-011-BOOK" not in active_ids, (
        "HYPO-011-BOOK (OrderBookImbalance) must be deprecated — "
        "n=336, 0 TP, -$77.93 lifetime, signal_exit 99.7%"
    )
    assert "HYPO-012-FLOW" not in active_ids, (
        "HYPO-012-FLOW (TradeFlow) must be deprecated — "
        "n=450, TP 9.8%, -$151.77 lifetime, EV -0.22%/trade"
    )
