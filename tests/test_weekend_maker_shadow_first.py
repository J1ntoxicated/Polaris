"""② Weekend-maker shadow-first — signal flows, real order is SUPPRESSED, would-be
P&L is logged (FIX, [[weekend_maker_honest_rerun_2026-06-28]]).

DEMO/PAPER only — virtual funds. flow_not_block: the SIGNAL still FLOWS through the
full pipeline (G1-G5 + sizing) and the would-be entry is RECORDED for live edge
measurement; ONLY the real venue order is not submitted. The two weekend OKX makers
carry a THIN single-period sample (flush 20wk / funding 96d) so they ship behind a
shadow-first gate: zero capital at risk while the live would-be P&L accrues. The
``shadow_first`` flag is ORTHOGONAL to ``dispatch_eligible`` (the strategy STILL
dispatches + emits + is sized — only the order leg is suppressed).
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from polaris.core.economics.shadow_order import log_shadow_order
from polaris.scripts._run_signal_helpers import _is_shadow_first
from polaris.strategies import RawSignal
from polaris.strategies.weekend_funding_capitulation_maker import (
    WeekendFundingCapitulationMakerStrategy,
)
from polaris.strategies.weekend_thin_book_flush_maker import (
    WeekendThinBookFlushMakerStrategy,
)

THIN_BOOK_ID = "weekend_thin_book_flush_maker"
FUNDING_ID = "weekend_funding_capitulation_maker"


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


# ── both weekend makers carry the shadow_first metadata flag ──────────────────


def test_both_weekend_makers_are_shadow_first() -> None:
    assert WeekendThinBookFlushMakerStrategy.metadata.shadow_first is True
    assert WeekendFundingCapitulationMakerStrategy.metadata.shadow_first is True


def test_shadow_first_is_orthogonal_to_dispatch_eligible() -> None:
    # The makers STILL dispatch (generate_raw_signal IS called + emits) — shadow
    # only suppresses the order leg, never the emit (flow_not_block).
    assert WeekendThinBookFlushMakerStrategy.metadata.dispatch_eligible is True
    assert WeekendFundingCapitulationMakerStrategy.metadata.dispatch_eligible is True


def test_default_strategy_is_not_shadow_first() -> None:
    # Every other strategy keeps the live order path — byte-identical.
    assert _is_shadow_first("spot_donchian_breakout") is False
    assert _is_shadow_first("session_breakout") is False
    assert _is_shadow_first("__not_a_strategy__") is False
    assert _is_shadow_first(THIN_BOOK_ID) is True
    assert _is_shadow_first(FUNDING_ID) is True


# ── would-be order persists (entry mark + sized notional + target), no real fill ─


def test_log_shadow_order_persists_would_be_row() -> None:
    conn = _conn()
    log_shadow_order(
        conn,
        run_id="run1",
        strategy_id=THIN_BOOK_ID,
        venue="okx",
        symbol="BTC-USDT",
        side="long",
        entry_mark=100.0,
        notional_usd=50.0,
        atr_pct=0.02,
        profit_target_r=0.30,
        now_ts=1_700_000_000,
    )
    row = conn.execute(
        "SELECT strategy_id, venue, symbol, side, entry_mark, notional_usd, "
        "atr_pct, profit_target_r FROM weekend_shadow_orders"
    ).fetchone()
    assert row is not None
    assert row[0] == THIN_BOOK_ID
    assert row[1] == "okx"
    assert row[3] == "long"
    assert row[4] == 100.0
    assert row[5] == 50.0
    assert row[7] == 0.30


def test_log_shadow_order_none_conn_is_noop() -> None:
    # degrade-never-crash: a None conn must not raise (the signal already flowed).
    log_shadow_order(
        None, run_id="r", strategy_id="s", venue="okx", symbol="X",
        side="long", entry_mark=1.0, notional_usd=10.0, atr_pct=0.01,
        profit_target_r=None, now_ts=0,
    )


def test_shadow_order_does_not_touch_positions_or_fills() -> None:
    # 🚨 real order SUPPRESSED: a shadow order writes ONLY the would-be row — no
    # positions / fills row (zero capital at risk, no real venue submit).
    conn = _conn()
    log_shadow_order(
        conn, run_id="r", strategy_id=FUNDING_ID, venue="okx", symbol="ETH-USDT",
        side="long", entry_mark=2000.0, notional_usd=80.0, atr_pct=0.02,
        profit_target_r=1.0, now_ts=1,
    )
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM weekend_shadow_orders"
    ).fetchone()[0] == 1


# ── the dispatch seam: signal flows, reserve_and_submit is NOT called ─────────


@pytest.mark.asyncio
async def test_pipeline_suppresses_order_for_shadow_first_strategy() -> None:
    # The shadow_first weekend maker flows the FULL pipeline but the real-order
    # seam (reserve_and_submit) is NEVER reached — the order is SUPPRESSED and the
    # would-be entry is logged instead (flow_not_block: signal flows, order defers).
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import ProdLoopState, _all_strategies

    reset_process_fence()
    conn = _conn()
    now = int(time.time())
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        " depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts, "
        " is_active, active_reason) "
        "VALUES ('okx', 'BTC-USDT', 'okx:BTC-USDT', 'crypto:BTC', 'crypto', "
        "        'USDT', 'live', 1e9, 2.0, 3.0, 1e6, 0.0, NULL, ?, 1, NULL)",
        (now,),
    )
    called: dict[str, bool] = {"reserve": False}

    async def _spy_reserve(**_kwargs: Any) -> None:
        called["reserve"] = True  # must NEVER fire for a shadow_first strategy
        return None

    state = ProdLoopState()
    flush = next(
        s for s in _all_strategies() if s.metadata.strategy_id == THIN_BOOK_ID
    )
    sig = RawSignal(
        signal_id="shadow_flush", strategy_id=THIN_BOOK_ID, symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=3,
        thesis_tag="weekend_flush", correlation_group=flush.metadata.correlation_group_id,
    )
    await run_pipeline_for_signal(
        conn=conn, haiku=StubGPTClient(), state=state, strategy=flush, sig=sig,
        venue="okx", symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", regime="bull_trend",
        bars_atr_pct=0.02, last_price=60_000.0,
        universe_rows=[{"venue": "okx", "symbol": "BTC-USDT", "vol_24h_usd": 2e9}],
        now_ts=now, reserve_and_submit=_spy_reserve, real_roundtrip=True,
    )
    # 🚨 order SUPPRESSED: reserve_and_submit never called, no position/fill written.
    assert called["reserve"] is False
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    # would-be entry logged + counter bumped (the signal DID flow + was sized).
    assert state.shadow_orders_logged == 1
    row = conn.execute(
        "SELECT strategy_id, venue, symbol, side, notional_usd "
        "FROM weekend_shadow_orders"
    ).fetchone()
    assert row is not None
    assert row[0] == THIN_BOOK_ID
    assert row[4] > 0.0  # a real sized would-be notional was recorded
