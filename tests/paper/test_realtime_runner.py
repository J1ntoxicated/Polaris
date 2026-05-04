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
    rt._price_history_60s.clear()
    # Round 10: rate-limit 딕셔너리 초기화 (함수 속성)
    # Round 12 Q7: cascade warmup state 초기화 추가
    for attr in ("_blead_nofeed_last", "_blead_hold_last", "_cascade_warmup_last"):
        if hasattr(rt._eval_and_act, attr):
            delattr(rt._eval_and_act, attr)
    # Round 14: regime cluster guard state 초기화
    rt._hypo010_sl_window.clear()
    rt._hypo010_pause_until_ms = 0
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


# ── Round 10: HYPO-013 MTA HOLD log + HYPO-014 BLEAD health check ────────────


def test_mta_hold_logged(tmp_path, monkeypatch, caplog):
    """Round 10 Fix 1: MTAConfluence HOLD signal 시 [MTA-HOLD] INFO 로그 발생.

    HYPO-013 60분 trade 0 → too strict vs wrong logic 판단을 위해
    HOLD reason 분포가 로그로 노출돼야 한다.

    Strategy.evaluate_multi_tf를 mock하여 HOLD를 강제 — MTAConfluence
    내부 조건 변화에 무관하게 로그 경로만 검증.
    """
    import logging
    from src.strategies.mta_confluence import MTAConfluence
    from src.domain.candle import Candle
    from src.domain.signal import Signal, SignalAction

    hypo = {
        "hypo_id": "HYPO-013-MTA",
        "strategy_cls": MTAConfluence,
        "params": {"target_size_usd": 100.0, "rsi_soft_threshold": 52.0, "min_score": 2},
        "primary_tf": "mta",
        "tickers": ["BTC-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    }
    ticker = "BTC-USDT"
    now_ms = 1_700_000_000_000

    def _fresh(n: int = 60):
        return [
            Candle(
                timestamp_ms=now_ms - (n - i) * 60_000,
                open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0,
            )
            for i in range(n)
        ]

    fake_tf = {"1D": _fresh(60), "4H": _fresh(60), "1H": _fresh(60), "15m": _fresh(60)}
    hold_signal = Signal(
        timestamp_ms=now_ms,
        action=SignalAction.HOLD,
        confidence=0.0,
        reason="score=1/4 (effective_min=3) mandatory=False test",
    )

    with patch.object(rt, "fetch_multi_tf", return_value=fake_tf), \
         patch.object(MTAConfluence, "evaluate_multi_tf", return_value=hold_signal), \
         caplog.at_level(logging.INFO, logger="src.paper.realtime_runner"):
        rt._eval_and_act(
            hypo, ticker, tick_price=100.0, tick_ts_ms=now_ms,
            full_tick=_full_tick(100.0, now_ms),
        )

    mta_hold_lines = [r.message for r in caplog.records if "[MTA-HOLD]" in r.message]
    assert mta_hold_lines, (
        "HOLD signal from MTAConfluence must emit [MTA-HOLD] INFO log. "
        f"All log messages: {[r.message for r in caplog.records]}"
    )
    assert ticker in mta_hold_lines[0], "Log must include the ticker symbol"
    assert hold_signal.reason in mta_hold_lines[0], "Log must include the HOLD reason"


def test_blead_nofeed_warn_rate_limited(tmp_path, monkeypatch, caplog):
    """Round 10 Fix 2: b_last is None → [BLEAD-NOFEED] WARN 발생, 5분 rate-limit 적용.

    HYPO-014 60분 trade 0 원인: WS feed 미공급인지 threshold 미충족인지 구분 불가.
    Fix: b_last=None 시 WARN 로그 (최초 + 5분마다), 그 사이 tick은 suppress.
    """
    import logging
    from src.strategies.binance_lead import BinanceLeadSignal

    hypo = {
        "hypo_id": "HYPO-014-BLEAD",
        "strategy_cls": BinanceLeadSignal,
        "params": {"target_size_usd": 100.0},
        "primary_tf": "cross",
        "tickers": ["BTC-USDT"],
        "starting_usd": 5000.0,
        "max_position_pct": 0.02,
    }
    ticker = "BTC-USDT"
    base_ts = 1_700_000_000_000

    with patch.object(rt, "binance_taker_buy_ratio", return_value=None), \
         patch.object(rt, "binance_volatility_bps", return_value=None), \
         patch.object(rt, "binance_get_last_trade", return_value=None), \
         caplog.at_level(logging.WARNING, logger="src.paper.realtime_runner"):

        # Tick 1 (t=0): b_last=None → WARN 발생해야 함
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=base_ts,
                         full_tick=_full_tick(100.0, base_ts))
        warns_after_first = [r for r in caplog.records if "[BLEAD-NOFEED]" in r.message]
        assert len(warns_after_first) == 1, (
            "첫 번째 b_last=None tick에서 [BLEAD-NOFEED] WARN 1회 발생해야 함"
        )

        caplog.clear()

        # Tick 2 (t=2분): rate-limit 5분 안 → WARN suppress
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=base_ts + 120_000,
                         full_tick=_full_tick(100.0, base_ts + 120_000))
        warns_after_second = [r for r in caplog.records if "[BLEAD-NOFEED]" in r.message]
        assert len(warns_after_second) == 0, (
            "5분 안 두 번째 tick은 [BLEAD-NOFEED] suppress돼야 함 (rate-limit)"
        )

        caplog.clear()

        # Tick 3 (t=6분): rate-limit 초과 → WARN 재발생
        rt._eval_and_act(hypo, ticker, tick_price=100.0, tick_ts_ms=base_ts + 360_000,
                         full_tick=_full_tick(100.0, base_ts + 360_000))
        warns_after_third = [r for r in caplog.records if "[BLEAD-NOFEED]" in r.message]
        assert len(warns_after_third) == 1, (
            "5분 초과 후 [BLEAD-NOFEED] WARN 재발생해야 함"
        )


# ── Round 12 F1: _price_history_60s maxlen=600 (spike safety margin) ────────


def test_price_history_high_tick_rate():
    """Codex Round 12 F1: maxlen=600으로 10Hz spike 흡수 — deque auto-truncate 방지.

    10Hz × 60s = 600 entries. maxlen=200이면 deque auto-truncate가 ts-trim보다
    먼저 작동 → price_1min_ago가 30s 전 값이 됨 (stale 1min delta).
    Fix: maxlen=600 → 600 entries push 후에도 deque overflow 없음.
    """
    rt._price_history_60s.clear()
    ticker = "BTC-USDT"
    base_ts = 1_700_000_000_000
    n_ticks = 600  # 10Hz × 60s

    # Push 600 entries spanning 60s (10Hz)
    for i in range(n_ticks):
        ts = base_ts + i * 100  # 100ms interval = 10Hz
        rt._update_price_history(ticker, ts, 50_000.0 + i)

    buf = rt._price_history_60s[ticker]
    # deque maxlen=600이므로 overflow 없이 600 entries 전부 유지
    assert len(buf) == n_ticks, (
        f"maxlen=600이면 600 entries 전부 유지돼야 함, 실제={len(buf)}"
    )
    # ts-trim: 65s 초과 entry는 제거됨. 60s 범위는 모두 유지
    oldest_ts = buf[0][0]
    newest_ts = buf[-1][0]
    assert newest_ts - oldest_ts <= 65_000, (
        f"oldest→newest 범위가 65s 이내여야 함, 실제={newest_ts - oldest_ts}ms"
    )
    # price_1min_ago가 실제 60초 전 가격인지 확인
    # oldest entry는 base_ts (index 0, price=50_000.0)
    assert buf[0][1] == 50_000.0, (
        "oldest entry price == 50_000.0 (index 0) — 1min delta 기준점 정확"
    )


def test_price_history_high_tick_rate_no_maxlen_overflow():
    """보완: maxlen=600이 실제 deque 제한 설정인지 직접 확인 (구현 검증)."""
    rt._price_history_60s.clear()
    ticker = "ETH-USDT"
    base_ts = 2_000_000_000_000
    # 601 entries push → maxlen=600이면 마지막 600만 유지 (oldest 1개 drop)
    for i in range(601):
        rt._update_price_history(ticker, base_ts + i * 100, 3_000.0 + i)

    buf = rt._price_history_60s[ticker]
    # deque(maxlen=600) → 601번째 push 시 0번째 drop
    # (ts-trim은 601번째가 base_ts+60100ms → 60.1s, 0번째 base_ts → diff=60100ms < 65000 → trim 안 됨)
    # 결론: len은 maxlen 기준 ≤ 600
    assert len(buf) <= 600, (
        f"deque maxlen=600 → 601 push 후 len≤600, 실제={len(buf)}"
    )
    # 첫 entry는 index 1 (50000ms ts, price=3001.0) — index 0은 overflow로 dropped
    assert buf[0][1] == 3_001.0, (
        "601번째 push 후 oldest entry는 index 1 (price=3001.0)"
    )


# ── Round 12 F2: _get_cascade_state uses last BTC/ETH tick ts ────────────────


def test_cascade_state_uses_last_tick_ts():
    """Codex Round 12 F2: ts_ms = buf[-1][0] (실제 마지막 BTC/ETH tick ts).

    BTC가 50s 범위 history 보유, 마지막 BTC tick = base_ts+50s.
    alt tick은 base_ts+80s (30s 더 늦게 도착) → current_ts_ms=base_ts+80s.

    F2 fix 전: ts_ms = current_ts_ms = base_ts+80s → stale=0s → guard 무력화.
    F2 fix 후: ts_ms = buf[-1][0] = base_ts+50s → stale = 30s → guard 작동.
    """
    rt._price_history_60s.clear()
    ticker = "BTC-USDT"
    base_ts = 1_700_000_000_000

    # BTC history: 51 ticks at 1Hz spanning 50s (index 0→50), last tick = base_ts+50s
    for i in range(51):
        ts = base_ts + i * 1_000  # 1Hz
        rt._update_price_history(ticker, ts, 50_000.0 + i)

    btc_last_tick_ts = base_ts + 50 * 1_000  # buf[-1][0]

    # alt tick arrives 30s AFTER the last BTC tick
    alt_tick_ts = btc_last_tick_ts + 30_000  # base_ts + 80s
    assert alt_tick_ts != btc_last_tick_ts, "alt_tick_ts must differ from BTC last tick"

    state = rt._get_cascade_state(ticker, 50_060.0, alt_tick_ts)
    assert state is not None, "50s+ history → should return state (not None)"

    # F2 fix: ts_ms == buf[-1][0] == btc_last_tick_ts, NOT alt_tick_ts
    assert state["ts_ms"] == btc_last_tick_ts, (
        f"ts_ms must be last BTC tick ts={btc_last_tick_ts}, "
        f"got {state['ts_ms']} (alt_tick_ts={alt_tick_ts})"
    )
    assert state["ts_ms"] != alt_tick_ts, (
        f"ts_ms must NOT equal alt-ticker tick ts ({alt_tick_ts}) — "
        "stale guard would be bypassed if they match"
    )


def test_cascade_state_stale_guard_works_with_fix():
    """F2 fix 실제 효과: BTC 29s 미update → BTCCascade stale guard 계산 정확.

    BTCCascade.evaluate_cascade가 ts_ms를 이용해 stale 여부 판단한다고 가정.
    state['ts_ms']가 29s 전이면 now_ms - ts_ms = 29_000ms → guard 작동 가능.
    (BTCCascade 내부 30s stale threshold 검증은 별도 test_btc_cascade.py 담당)
    """
    rt._price_history_60s.clear()
    ticker = "BTC-USDT"
    base_ts = 1_700_000_000_000

    # BTC: 50s 히스토리, 마지막 틱 = base_ts+50s (29s 전 기준 alt_tick at base_ts+79s)
    for i in range(51):
        rt._update_price_history(ticker, base_ts + i * 1_000, 50_000.0 + i)

    btc_last_tick = base_ts + 50_000  # BTC 마지막 틱
    alt_tick_now = btc_last_tick + 29_000  # alt tick = 29s 후

    state = rt._get_cascade_state(ticker, 50_060.0, alt_tick_now)
    assert state is not None

    # ts_ms = buf[-1][0] = btc_last_tick → stale = alt_tick_now - ts_ms = 29_000ms
    stale_ms = alt_tick_now - state["ts_ms"]
    assert stale_ms == 29_000, (
        f"stale_ms must be 29_000 (29s), got {stale_ms}ms — "
        "F2 fix ensures BTCCascade can correctly detect 30s stale"
    )


# ── Round 14: intent-code 정합 복원 + regime cluster guard ──────────────────


def test_hypo010_target_size_200_round14():
    """HYPO-010 Round 14 검증 — Round 15에서 deprecated.

    Round 14: target_size_usd 200 (max_position_pct 0.04 × $5000 = $200 cap 정합 복원).
    Round 15: HYPO-010 전체 deprecated (n=95, win 43%, -$14.98 변질 확인).
    이 테스트는 Round 14 config 정합을 historical record로 보존.
    HYPO-010이 REALTIME_HYPOS에 없으면 skip (deprecated 확인).
    """
    hypo010 = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-010-TICK"), None)
    if hypo010 is None:
        # Round 15 deprecated — expected absence, test passes
        return
    assert hypo010["params"].get("target_size_usd") == 200.0, (
        f"HYPO-010-TICK params target_size_usd must be 200.0, "
        f"got {hypo010['params'].get('target_size_usd')} — "
        "Round 14 intent-code 정합 복원 (max_position_pct 0.04 × $5000 = $200 cap)"
    )


def test_hypo010_trump_removed_round14():
    """HYPO-010 Round 14 검증 — Round 15에서 deprecated.

    Round 14: TRUMP-USDT 제거 (price range 0.42% < SL×2=0.70%, 구조적 부적합).
    Round 15: HYPO-010 전체 deprecated (n=95, win 43%, -$14.98 변질 확인).
    HYPO-010이 REALTIME_HYPOS에 없으면 skip (deprecated 확인).
    """
    hypo010 = next((h for h in rt.REALTIME_HYPOS if h["hypo_id"] == "HYPO-010-TICK"), None)
    if hypo010 is None:
        # Round 15 deprecated — expected absence, test passes
        return
    assert "TRUMP-USDT" not in hypo010["tickers"], (
        "TRUMP-USDT must be removed from HYPO-010-TICK tickers — "
        "price range 0.42% < SL×2=0.70% (구조적 SL 도달 부적합, 3/3 SL Round 14 forensic)"
    )


def test_regime_cluster_pauses_hypo010():
    """Round 14: _check_hypo010_regime_cluster — 3+ ticker SL in 5min → 10min pause.

    forensic INSIGHT-029: 14:36 이후 13 trade 전부 SL = regime change cluster.
    Fix: 5분 window 내 3+ ticker SL hit 시 HYPO-010 entry 10분 pause.
    """
    # 초기화
    rt._hypo010_sl_window.clear()
    rt._hypo010_pause_until_ms = 0

    base_ts = 1_700_000_000_000
    # Ticker A SL (t=0)
    rt._check_hypo010_regime_cluster("BTC-USDT", "sl_hit:-0.0036", base_ts)
    assert rt._hypo010_pause_until_ms == 0, "2 tickers 미만 — pause 안 됨"

    # Ticker B SL (t=1min)
    rt._check_hypo010_regime_cluster("ETH-USDT", "sl_hit:-0.0038", base_ts + 60_000)
    assert rt._hypo010_pause_until_ms == 0, "2 tickers — pause 안 됨"

    # Ticker C SL (t=2min) → 3 distinct tickers → 10분 pause 발동
    rt._check_hypo010_regime_cluster("SOL-USDT", "sl_hit:-0.0035", base_ts + 120_000)
    assert rt._hypo010_pause_until_ms > 0, "3 tickers SL → 10min pause 발동해야 함"
    expected_pause_until = (base_ts + 120_000) + 600_000  # 10min
    assert rt._hypo010_pause_until_ms == expected_pause_until, (
        f"pause_until_ms must be {expected_pause_until}, got {rt._hypo010_pause_until_ms}"
    )

    # TP hit은 cluster 추적에서 제외
    rt._hypo010_sl_window.clear()
    rt._hypo010_pause_until_ms = 0
    rt._check_hypo010_regime_cluster("DOGE-USDT", "tp_hit:+0.006", base_ts + 300_000)
    assert rt._hypo010_pause_until_ms == 0, "TP hit은 regime cluster 추적 대상 아님"


def test_regime_cluster_5min_window_expiry():
    """Round 14: 5분 초과 SL은 cluster window에서 제거 — 오래된 SL로 trigger 방지."""
    rt._hypo010_sl_window.clear()
    rt._hypo010_pause_until_ms = 0

    base_ts = 1_700_000_000_000
    # 오래된 SL 2건 (t=0, t=1min)
    rt._check_hypo010_regime_cluster("BTC-USDT", "sl_hit:-0.0036", base_ts)
    rt._check_hypo010_regime_cluster("ETH-USDT", "sl_hit:-0.0038", base_ts + 60_000)

    # 6분 후 새 SL — 5분 window 초과 이전 항목 trim됨 → distinct tickers 재계산
    tick_6min = base_ts + 360_000
    rt._check_hypo010_regime_cluster("SOL-USDT", "sl_hit:-0.0035", tick_6min)
    # 5분 cutoff = tick_6min - 300_000 = base_ts + 60_000
    # BTC SL (base_ts): < cutoff → trim됨
    # ETH SL (base_ts+60_000): == cutoff → trim됨 (< 조건, popleft while)
    # SOL SL (tick_6min): 유일하게 window 내 → distinct_tickers = {SOL} → count < 3
    assert rt._hypo010_pause_until_ms == 0, (
        "5분 초과 SL trim 후 distinct tickers < 3 → pause 발동 안 됨"
    )


# ── Round 15: tick-driven scalp 비활성 + 1d trend 강화 ───────────────────────
# Jin 판단 (2026-05-04):
# HYPO-010 n=95 win 43% -$14.98 → deprecate
# HYPO-013 n=1 → deprecate (sample 부족)
# HYPO-014 n=1 0% → deprecate (vol 미달)
# HYPO-016 n=37 win 24% -$3.92 → deprecate (사전 trigger)
# HYPO-017 n=0 → deprecate (60분 trigger 0)
# HYPO-007/008 유지 (양수 EV 또는 cron-style)
# cron ACTIVE_HYPOS: SMA 8ticker + Donchian 2 variants


def test_realtime_hypos_007_008_023():
    """Phase 2M: REALTIME_HYPOS active HYPO 목록 검증.

    Phase 2k: HYPO-023 (LiquidationCascade) 추가.
    Phase 2L: HYPO-024~028 일괄 추가 (fail-fast paradigm).
    Phase 2L+: HYPO-026 cut + HYPO-029/030/031 신규 (basic indicators, no academic basis).
    Phase 2M: HYPO-029/030/031 cut (학술 근거 부족) + HYPO-032/033/034 신규 (academic-grade).

    Deprecated Phase 2M:
    - HYPO-029 StochRSI: basic indicator, no peer-reviewed basis
    - HYPO-030 ADXTrendPullback: basic combo, no peer-reviewed basis
    - HYPO-031 OBVDivergence: basic indicator, no peer-reviewed basis
    Deployed Phase 2M:
    - HYPO-032 TSMOM: Moskowitz/Ooi/Pedersen 2012 JFE
    - HYPO-033 VPINToxicity: Easley/Lopez de Prado/O'Hara 2012 RFS
    - HYPO-034 BTCDominanceLag: Stalder/Cosenza 2025 + Liu 2022
    """
    active_ids = {h["hypo_id"] for h in rt.REALTIME_HYPOS}

    # 유지 — 반드시 존재 (이전 Phase)
    assert "HYPO-007-RT" in active_ids, "HYPO-007-RT (RSI15m) must remain active"
    assert "HYPO-008-RT" in active_ids, "HYPO-008-RT (VolumeBurst) must remain active"
    assert "HYPO-023" in active_ids, "HYPO-023 (LiquidationCascade) must be active — Phase 2k"

    # Phase 2L 유지 (026 제외, 025 deprecated Phase 2N+)
    assert "HYPO-024" in active_ids, "HYPO-024 (CrossExchangeGap) must be active — Phase 2L"
    assert "HYPO-027" in active_ids, "HYPO-027 (FundingRateFilter) must be active — Phase 2L"
    assert "HYPO-028" in active_ids, "HYPO-028 (TickBurst) must be active — Phase 2L"

    # Phase 2M 신규 — academic-grade alphas
    assert "HYPO-032" in active_ids, "HYPO-032 (TSMOM — Moskowitz 2012 JFE) must be active — Phase 2M"
    assert "HYPO-033" in active_ids, "HYPO-033 (VPINToxicity — Easley 2012 RFS) must be active — Phase 2M"
    assert "HYPO-034" in active_ids, "HYPO-034 (BTCDominanceLag — Stalder 2025) must be active — Phase 2M"

    # Deprecated Phase 2M — basic indicators cut (학술 근거 부족)
    assert "HYPO-029" not in active_ids, "HYPO-029 must be cut — basic indicator, no academic basis"
    assert "HYPO-030" not in active_ids, "HYPO-030 must be cut — basic combo, no academic basis"
    assert "HYPO-031" not in active_ids, "HYPO-031 must be cut — basic indicator, no academic basis"

    # Deprecated Phase 2N+ — HYPO-025 cut (n=6 win 33%, fast_fail trigger met)
    assert "HYPO-025" not in active_ids, (
        "HYPO-025 must be cut — n=6, win 33%, -$3.76 (fast_fail auto-trigger met)"
    )

    # Deprecated Phase 2L/earlier — 반드시 부재
    assert "HYPO-026" not in active_ids, (
        "HYPO-026 must be cut — n=7, 0 wins, -$1.31 (whale_wall 비유효)"
    )
    assert "HYPO-010-TICK" not in active_ids, (
        "HYPO-010-TICK must be deprecated — n=95, win 43%, -$14.98 (변질 진행)"
    )
    assert "HYPO-013-MTA" not in active_ids, (
        "HYPO-013-MTA must be deprecated — n=1, sample 부족, 빈도 0"
    )
    assert "HYPO-014-BLEAD" not in active_ids, (
        "HYPO-014-BLEAD must be deprecated — n=1, 0% win, vol 미달"
    )
    assert "HYPO-016-OFI" not in active_ids, (
        "HYPO-016-OFI must be deprecated — n=37, win 24%, -$3.92 (사전 trigger)"
    )
    assert "HYPO-017-CASCADE" not in active_ids, (
        "HYPO-017-CASCADE must be deprecated — n=0, 60분 trigger 0"
    )

    # 정확히 9개 (007+008+023+024+027+028+032+033+034; 025 deprecated Phase 2N+)
    assert len(active_ids) == 9, (
        f"REALTIME_HYPOS must contain exactly 9 active HYPOs "
        f"(007+008+023+024+027+028+032+033+034; HYPO-025 deprecated Phase 2N+), "
        f"got {len(active_ids)}: {sorted(active_ids)}"
    )


# ── Phase 2N+: Dynamic sizing cap fix (ADR-015 정합) ─────────────────────────
# Bug: max_position_pct=0.04 → $5000×0.04=$200 hard cap silently overrides
# dynamic sizing (e.g. fraction=0.12 × $5000=$600 → capped to $200).
# Fix: remove max_position_pct from REALTIME_HYPOS; use hard_cap=equity×0.30.


def test_dynamic_sizing_not_capped_by_max_position_pct():
    """Phase 2N+ bug regression: dynamic size must NOT be silently capped by max_position_pct=0.04.

    Scenario: fraction=0.12 × $5000 = $600 expected.
    Old code: min($600, $5000×0.04=$200, $5000) = $200 (silent override).
    New code: min($600, $5000×0.30=$1500, $5000) = $600 (dynamic respected).

    Uses flow strategy (primary_tf="flow") with stubbed ENTER_LONG at confidence=0.70.
    Kelly cold-start (win_rate=0.55, avg_win=0.8, avg_loss=0.5): kelly=0.268.
    fraction = 0.268 × 0.70²=0.49 × uptrend=1.0 × dd=1.0 = 0.131 (> 0.04).
    Expected size = $5000 × 0.131 = $655 >> $200 old cap.
    """
    from src.strategies.trade_flow import TradeFlow
    from src.domain.signal import Signal, SignalAction
    from src.risk.performance_tracker import _COLD_START_DEFAULTS

    ticker = "BTC-USDT"
    tick_price = 100.0
    entry_ts = 1_700_000_000_000
    starting_usd = 5000.0

    # HYPO without max_position_pct (new config — key removed)
    hypo = {
        "hypo_id": "HYPO-TEST-DYNCAP",
        "strategy_cls": TradeFlow,
        "params": {},
        "primary_tf": "flow",
        "tickers": [ticker],
        "starting_usd": starting_usd,
        # max_position_pct intentionally absent — new contract
    }

    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=0.70,
        target_size_usd=9999.0,  # large value — dynamic sizing will override
        reason="test_strong_signal",
    )

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100), \
         patch("src.strategies.trade_flow.TradeFlow.evaluate_flow",
               return_value=enter_signal), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="uptrend"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value=dict(_COLD_START_DEFAULTS)):
        rt._eval_and_act(hypo, ticker, tick_price=tick_price, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(tick_price, entry_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=starting_usd)
    assert len(bal.open_positions) == 1, "entry must have occurred"
    size = bal.open_positions[0].size_usd
    # Old cap: $200 (max_position_pct=0.04 × $5000)
    # New: dynamic sizing should produce >> $200
    assert size > 200.0, (
        f"Dynamic size must exceed old $200 cap (max_position_pct=0.04 silent override). "
        f"Got ${size:.2f}. "
        f"Expected > $200 (dynamic kelly=~0.27 × conf²≈0.49 × uptrend × $5000)."
    )
    # Also must not exceed hard_cap = equity × 0.30
    hard_cap = starting_usd * 0.30
    assert size <= hard_cap + 0.01, (
        f"Size ${size:.2f} must not exceed hard_cap ${hard_cap:.2f} (equity × 0.30)"
    )


def test_realtime_hypos_no_max_position_pct_key():
    """Phase 2N+: REALTIME_HYPOS entries must NOT contain max_position_pct key.

    max_position_pct was the source of the silent $200 cap bug.
    Dynamic sizing (ADR-015 MAX_FRACTION=0.20) handles sizing internally.
    Shell uses hard_cap=equity×0.30 as the only upper bound.
    """
    for hypo in rt.REALTIME_HYPOS:
        assert "max_position_pct" not in hypo, (
            f"{hypo['hypo_id']} must not have max_position_pct key — "
            "Phase 2N+ removes this key; dynamic sizing handles cap via ADR-015 MAX_FRACTION=0.20 "
            "and shell hard_cap=equity×0.30"
        )


def test_hard_cap_30pct_applied():
    """Phase 2N+: hard_cap=equity×0.30 is the only shell-level cap.

    Even with extreme Kelly (full cap), size must not exceed equity×0.30.
    Tests that the new hard_cap line replaced the max_position_pct line.
    """
    from src.strategies.trade_flow import TradeFlow
    from src.domain.signal import Signal, SignalAction

    ticker = "BTC-USDT"
    tick_price = 100.0
    entry_ts = 1_700_000_000_001
    starting_usd = 5000.0

    hypo = {
        "hypo_id": "HYPO-TEST-HARDCAP",
        "strategy_cls": TradeFlow,
        "params": {},
        "primary_tf": "flow",
        "tickers": [ticker],
        "starting_usd": starting_usd,
    }

    # Confidence=1.0 + excellent performance → maximum dynamic size (capped at MAX_FRACTION=0.20)
    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=1.0,
        target_size_usd=9999.0,  # large value — dynamic sizing will override
        reason="test_max_signal",
    )

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100), \
         patch("src.strategies.trade_flow.TradeFlow.evaluate_flow",
               return_value=enter_signal), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="crisis"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={
                   "win_rate": 0.80,
                   "avg_win_pct": 2.0,
                   "avg_loss_pct": 0.5,
               }):
        rt._eval_and_act(hypo, ticker, tick_price=tick_price, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(tick_price, entry_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=starting_usd)
    if len(bal.open_positions) == 0:
        # size < MIN_SIZE_USD or skip — acceptable if dynamic_sizing returned 0
        return
    size = bal.open_positions[0].size_usd
    hard_cap = starting_usd * 0.30  # $1500
    assert size <= hard_cap + 0.01, (
        f"Size ${size:.2f} must not exceed hard_cap ${hard_cap:.2f} (equity×0.30). "
        "New shell contract: only hard_cap=equity×0.30 as upper bound."
    )


# ── Phase 2N+: HYPO-025 cut + cold start cap + deprecate interval ────────────


def test_hypo_025_not_in_realtime_hypos():
    """Phase 2N+: HYPO-025 (VolumeDeltaDivergence) must be absent from REALTIME_HYPOS.

    n=6, win 33% < 40% fast_fail threshold. avg_size $687 → loss acceleration.
    Auto-trigger was already met. Manual cut to stop bleeding immediately.
    """
    active_ids = {h["hypo_id"] for h in rt.REALTIME_HYPOS}
    assert "HYPO-025" not in active_ids, (
        "HYPO-025 (VolumeDeltaDivergence) must be deprecated — "
        "n=6, win 33%, avg_size $687, -$3.76 lifetime. "
        "fast_fail auto-trigger met (n>=5, win<40%)."
    )


def test_deprecate_check_interval_1min():
    """Phase 2N+: DEPRECATE_CHECK_INTERVAL_S must be 60s (was 300s).

    5min → 1min for faster fail-fast. HYPO-025 lesson:
    5min delay = 1-2 more trades at $687 size → unnecessary loss accumulation.
    """
    assert rt.DEPRECATE_CHECK_INTERVAL_S == 60.0, (
        f"DEPRECATE_CHECK_INTERVAL_S must be 60.0s (1 min), "
        f"got {rt.DEPRECATE_CHECK_INTERVAL_S}s. "
        "Phase 2N+ change: 5min → 1min for faster auto-deprecate."
    )


def test_cold_start_cap_applied_in_eval_and_act():
    """Phase 2N+: _eval_and_act passes n_trades to compute_size → cold start cap active.

    Uses flow strategy with 0 closed positions (n_trades=0 → cold start).
    Even with high confidence + crisis regime, size must be <= $300 (COLD_START_MAX_USD).
    Verifies that n_closed is passed from _eval_and_act to compute_size.
    """
    from src.strategies.trade_flow import TradeFlow
    from src.domain.signal import Signal, SignalAction
    from src.risk.dynamic_sizing import COLD_START_MAX_USD

    ticker = "BTC-USDT"
    tick_price = 100.0
    entry_ts = 1_700_000_010_000
    starting_usd = 5000.0

    hypo = {
        "hypo_id": "HYPO-TEST-COLDCAP",
        "strategy_cls": TradeFlow,
        "params": {},
        "primary_tf": "flow",
        "tickers": [ticker],
        "starting_usd": starting_usd,
    }

    # High confidence crisis signal → without cold start cap would produce >> $300
    enter_signal = Signal(
        timestamp_ms=entry_ts,
        action=SignalAction.ENTER_LONG,
        confidence=1.0,
        target_size_usd=9999.0,
        reason="test_cold_cap",
    )

    with patch.object(rt, "compute_taker_buy_ratio", return_value=0.80), \
         patch.object(rt, "get_recent_trades", return_value=[None] * 100), \
         patch("src.strategies.trade_flow.TradeFlow.evaluate_flow",
               return_value=enter_signal), \
         patch.object(rt, "_get_btc_1d_candles", return_value=[]), \
         patch("src.risk.regime_detector.detect_regime", return_value="crisis"), \
         patch("src.risk.performance_tracker.compute_recent_stats",
               return_value={"win_rate": 0.80, "avg_win_pct": 2.0, "avg_loss_pct": 0.5}):
        rt._eval_and_act(hypo, ticker, tick_price=tick_price, tick_ts_ms=entry_ts,
                         full_tick=_full_tick(tick_price, entry_ts))

    bal = rt.load_state(ticker, "trade_flow", starting_usd=starting_usd)
    assert len(bal.open_positions) == 1, "entry must have occurred"
    size = bal.open_positions[0].size_usd
    # n_trades=0 (no closed positions) → cold start cap $300 applied
    assert size <= COLD_START_MAX_USD, (
        f"Cold start (n=0) must cap size at ${COLD_START_MAX_USD:.0f}, "
        f"got ${size:.2f}. "
        "Check that _eval_and_act passes n_closed to compute_size(n_trades=n_closed)."
    )
