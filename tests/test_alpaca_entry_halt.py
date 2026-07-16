"""Alpaca equity NEW-entry halt — dead-feed data-health gate (not a throttle).

Jin 2026-06-22 (coherence audit): the Alpaca feed went 98.7h stale. With no
live price an equity entry would size/exit against a dead quote and become an
unexitable zombie. ``alpaca_equity_entries_halted`` HALTS new Alpaca entries
while the feed is dead, and AUTO-CLEARS the instant the feed recovers.

Two halt sources, OR-combined:
- DATA-HEALTH (primary): the newest Alpaca bar is older than the freshness
  window -> feed dead -> halt. Pure function of the stored data, so it clears
  automatically when fresh bars land (no manual reset).
- OPERATOR OVERRIDE: ``POLARIS_ALPACA_ENTRIES_HALT`` truthy forces the halt on
  regardless of feed state (an explicit kill-switch).

flow_not_block preserved: this is a "no live price = cannot trade" gate, NOT a
defensive throttle. It touches ZERO sizing — a healthy feed sizes/enters
exactly as before. It applies ONLY to Alpaca (OKX/Capital untouched) and ONLY
to NEW entries (existing positions still managed/exited).

DEMO/PAPER only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.core.streams.alpaca_health import alpaca_equity_entries_halted
from polaris.storage.schema import ALL_DDL, init_db
from polaris.strategies.base import RawSignal


def _alpaca_bar(symbol: str, ts: int) -> Bar:
    return Bar(
        instrument_id=f"alpaca:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        venue="alpaca",
        symbol=symbol,
        bar_interval="1D",
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1_000_000.0,
        notional_usd=100_500_000.0,
        trade_count=5_000,
        vwap=100.4,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="alpaca_rest",
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "halt.sqlite")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_ALPACA_ENTRIES_HALT", raising=False)


def test_fresh_alpaca_feed_does_not_halt(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("AAPL", now - 3600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False


def test_dead_alpaca_feed_halts_entries(conn: sqlite3.Connection) -> None:
    """The 98.7h incident: newest Alpaca bar 99h old -> halt new entries."""
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("CAST", now - int(99 * 3600))])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_halt_auto_clears_on_feed_recovery(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("GPUS", now - int(99 * 3600))])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True
    # Feed recovers.
    persist_bars(conn, [_alpaca_bar("GPUS", now - 600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False


def test_no_alpaca_bars_at_all_halts(conn: sqlite3.Connection) -> None:
    """No Alpaca bars in the DB == no live price == cannot trade -> halt."""
    now = int(time.time())
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_operator_override_forces_halt_even_when_fresh(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("MSFT", now - 600)])
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False
    monkeypatch.setenv("POLARIS_ALPACA_ENTRIES_HALT", "1")
    assert alpaca_equity_entries_halted(conn, now_ts=now) is True


def test_operator_override_falsey_does_not_force(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time())
    persist_bars(conn, [_alpaca_bar("MSFT", now - 600)])
    monkeypatch.setenv("POLARIS_ALPACA_ENTRIES_HALT", "0")
    assert alpaca_equity_entries_halted(conn, now_ts=now) is False


# ---------------------------------------------------------------------------
# Pipeline call-site — storage-split round 4 CRITICAL fix
# (_production_run_signal.run_pipeline_for_signal:158): bars is
# marketdata-domain, so a trading-conn-only read sees a permanently-empty
# table post-split -> alpaca_equity_entries_halted always returns True ->
# every Alpaca entry HALTED forever (fail-closed, flow_not_block violation).
# ---------------------------------------------------------------------------


def _memdb_all_ddl() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


@pytest.mark.asyncio
async def test_pipeline_alpaca_entry_not_halted_by_permanently_empty_trading_bars() -> None:
    """A fresh Alpaca bar landing ONLY in state.md_conn (post-split reality —
    the trading conn's bars table stays empty) must NOT halt the entry."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import ProdLoopState, _all_strategies

    reset_process_fence()
    conn = _memdb_all_ddl()  # trading conn — bars stays EMPTY
    md_conn = _memdb_all_ddl()
    persist_bars(md_conn, [_alpaca_bar("AAPL", int(time.time()) - 600)])

    captured: dict[str, object] = {}

    async def _spy_reserve(**_kwargs: object) -> None:
        captured["called"] = True
        return None

    eq_sig = RawSignal(
        signal_id="halt_fix_1", strategy_id="equity_tsmom", symbol="AAPL",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )
    await run_pipeline_for_signal(
        conn=conn, haiku=StubGPTClient(), state=ProdLoopState(md_conn=md_conn),
        strategy=_all_strategies()[0], sig=eq_sig, venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", regime="bull_trend",
        bars_atr_pct=0.02, last_price=190.0,
        universe_rows=[{"venue": "alpaca", "symbol": "AAPL", "vol_24h_usd": 2e9}],
        now_ts=int(time.time()), reserve_and_submit=_spy_reserve,
    )
    assert captured.get("called") is True  # NOT halted -> pipeline reached G5
    conn.close()
    md_conn.close()


@pytest.mark.asyncio
async def test_pipeline_alpaca_entry_halt_falls_back_to_conn_when_md_conn_unwired() -> None:
    """state.md_conn=None (legacy/smoke/replay callers) must fall back to the
    conn it is given — byte-identical to the pre-fix single-conn behaviour."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import run_pipeline_for_signal
    from polaris.scripts._smoke_gpt_stub import StubGPTClient
    from polaris.scripts.production_paper_loop import ProdLoopState, _all_strategies

    reset_process_fence()
    conn = _memdb_all_ddl()
    persist_bars(conn, [_alpaca_bar("AAPL", int(time.time()) - int(99 * 3600))])

    captured: dict[str, object] = {}

    async def _spy_reserve(**_kwargs: object) -> None:
        captured["called"] = True
        return None

    eq_sig = RawSignal(
        signal_id="halt_fix_2", strategy_id="equity_tsmom", symbol="AAPL",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="equity_intraday",
    )
    await run_pipeline_for_signal(
        conn=conn, haiku=StubGPTClient(), state=ProdLoopState(),
        strategy=_all_strategies()[0], sig=eq_sig, venue="alpaca",
        symbol="AAPL", asset_class="equity",
        underlying_group_id="equity:AAPL", regime="bull_trend",
        bars_atr_pct=0.02, last_price=190.0,
        universe_rows=[{"venue": "alpaca", "symbol": "AAPL", "vol_24h_usd": 2e9}],
        now_ts=int(time.time()), reserve_and_submit=_spy_reserve,
    )
    assert "called" not in captured  # stale bar on the fallback conn -> HALTED
    conn.close()
