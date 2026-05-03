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
