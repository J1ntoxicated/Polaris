"""FIX-2 — G6 position monitor must see REAL market inputs, not zeros.

Root cause: ``_production_recalc._evaluate_position`` hardcoded
``monitor_payload["market_view"]["volume_now"] = 0.0`` and
``monitor_payload["recent_ticks"] = []`` for EVERY active position. So the
G6 monitor (and the P1 GPT prompt) always saw volume_now=0 and no recent
price action — it was blind to the very data it needs to decide HOLD vs
EXIT_NOW vs ADJUST_EXIT.

Fix: ``load_active_position_rows`` now captures the real recent bar series
(latest volume, volume z-score, last-N closes, ATR slope) and
``_evaluate_position`` forwards them into the G6 ``market_view`` +
``recent_ticks``.

These tests pin the fix:
1. ``load_active_position_rows`` exposes real volume + recent-close series.
2. ``_evaluate_position`` puts a non-zero ``volume_now`` into the G6
   market_view and a non-empty ``recent_ticks`` into the payload — verified
   by capturing the actual P1 GPT prompt (which embeds both).

DEMO/PAPER only; virtual funds. This is a data-correctness fix (the monitor
sees what actually traded). No throttle, no size dampen, no chain multiplier.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import (
    load_active_position_rows,
    recalc_active_positions,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _PromptCapturingGPTClient:
    """Captures each call's user prompt; returns HOLD."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)

                class _Block:
                    text = '{"decision":"HOLD","reason":"x"}'

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()

    @property
    def last_user_prompt(self) -> str:
        msgs = self.calls[-1].get("messages", [])
        for m in msgs:
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(
                        str(blk.get("text", "")) for blk in content
                        if isinstance(blk, dict)
                    )
        return ""


def _seed_position_with_volume_ramp(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    entry_price: float = 80_000.0,
) -> None:
    """Seed a position whose bars carry a clear volume ramp + price drift.

    The last bar's volume is much larger than the trailing window so a real
    ``volume_now`` (and volume z-score) is unmistakably non-zero — proving the
    monitor reads live data rather than the hardcoded 0.0.
    """
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 0.001, 'open', ?, 0)",
        (
            position_id, venue, symbol, "crypto:BTC", "vb", "vb", "vb", NOW,
        ),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, 'long', 0.001, ?, 80.0, 0.05, 1.0, 0.0, "
        "        0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, "vb", f"{venue}:{symbol}", venue,
            entry_price, position_id, uuid.uuid4().hex,
        ),
    )
    instrument_id = f"{venue}:{symbol}"
    for i in range(20):
        ts = NOW - (20 - i) * 60
        # Price drifts up slightly each bar; last bar volume spikes.
        close = entry_price + i * 20.0
        volume = 100.0 if i < 19 else 5_000.0
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rest')",
            (
                instrument_id, "crypto:BTC", venue, symbol, ts,
                close, close + 60.0, close - 60.0, close, volume,
                volume * close, 1, close, close, close, 1.0,
            ),
        )


def _trade_for(position_id: str) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        side="long",
        entry_price=80_000.0,
        notional_usd=80.0,
        open_ts=NOW,
        position_id=position_id,
        correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _lookup_regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


# ---------------------------------------------------------------------------
# 1. load_active_position_rows exposes real volume + recent-close series
# ---------------------------------------------------------------------------


def test_load_active_position_rows_exposes_real_volume(
    memdb: sqlite3.Connection,
) -> None:
    _seed_position_with_volume_ramp(memdb, position_id="pos-vol")
    rows = load_active_position_rows(memdb)
    assert len(rows) == 1
    pos = rows[0]
    # The last bar's volume (5_000) must surface as a real volume_now.
    assert float(pos["volume_now"]) == pytest.approx(5_000.0)
    # Recent closes captured (non-empty series of last-N closes, newest last).
    recent = pos["recent_ticks"]
    assert isinstance(recent, list)
    assert len(recent) >= 2
    # Last recent close matches the live last_price.
    last_close = float(recent[-1]["close"])
    assert last_close == pytest.approx(float(pos["last_price"]))


# ---------------------------------------------------------------------------
# 2. _evaluate_position forwards real volume_now + recent_ticks into G6 prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g6_prompt_carries_real_volume_and_ticks(
    memdb: sqlite3.Connection,
) -> None:
    """The P1 G6 GPT prompt must embed a non-zero volume_now + real ticks.

    Before the fix the prompt always showed ``"volume_now":0.0`` and an empty
    ``Recent ticks`` list. After the fix the monitor sees the live 5_000-unit
    volume spike and the recent close series.
    """
    _seed_position_with_volume_ramp(memdb, position_id="pos-vol")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-vol")]
    gpt = _PromptCapturingGPTClient()

    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=gpt, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )

    assert len(gpt.calls) == 1
    prompt = gpt.last_user_prompt
    # Real volume reached the prompt — NOT the hardcoded 0.0.
    assert '"volume_now":0.0' not in prompt
    assert '"volume_now":0' not in prompt
    assert "5000" in prompt
    # Recent ticks are no longer an empty list in the prompt.
    assert "# Recent ticks (last 0)" not in prompt
