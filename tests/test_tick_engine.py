"""P5 tick-decision engine — integration TDD.

Replays a synthetic live tick stream into a fake ``quote_writer`` and asserts
the engine's decision → risk-gate → executor + per-tick exit behaviour:

  (a) a clear burst produces an entry intent → sized order (non-shadow),
  (b) shadow mode logs the decision but places NO order,
  (c) the freshness gate skips a stale symbol,
  (d) a 'short' intent on OKX (long-only) is dropped,
  (e) no double-open when a position already exists on the symbol,
  (f) a reversion position exits on flow reversal.

Runs against the in-memory ``memdb`` fixture + a fake in-mem quote_writer, so no
WS / demo-venue network happens. Entries use the SIM fill path
(``real_roundtrip=False``) — the same ``reserve_and_submit`` the bar pipeline
uses, exercised without a live adapter.

Spec SSOT: .claude/plans/p5_tick_decision_engine_2026-06-03.md.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.ticks.config import TickEngineConfig
from polaris.core.ticks.types import TickSample
from polaris.scripts import _production_tick_engine as eng_mod
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_engine import (
    TickEngineState,
    _run_entries,
    _run_exits,
    _scalp_exit_decision,
)
from polaris.scripts._smoke_fills import SimulatedTrade

VENUE = "okx"
SYMBOL = "BTC-USDT"
GROUP = "crypto:BTC"
INSTRUMENT = f"{VENUE}:{SYMBOL}"


# ---------------------------------------------------------------------------
# Fake quote writer — in-mem live_px + feature_window (mirrors the real API).
# ---------------------------------------------------------------------------


class FakeQuoteWriter:
    """In-mem stand-in for ``QuoteTickWriter`` (live_px + feature_window only)."""

    def __init__(self) -> None:
        self._window: dict[str, list[TickSample]] = {}
        self._px: dict[str, tuple[float, float]] = {}

    def set_stream(
        self, instrument_id: str, window: list[TickSample], last_mono: float
    ) -> None:
        self._window[instrument_id] = window
        self._px[instrument_id] = (window[-1].mid if window else 0.0, last_mono)

    def live_px(self, instrument_id: str) -> tuple[float, float] | None:
        return self._px.get(instrument_id)

    def feature_window(self, instrument_id: str) -> list[TickSample]:
        return self._window.get(instrument_id, [])


def _tick(
    ts_ms: int,
    mid: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    last_trade_price: float | None = None,
    last_trade_size: float = 1.0,
    spread: float = 0.02,
) -> TickSample:
    half = spread / 2.0
    bid = mid - half
    ask = mid + half
    spread_bps = (ask - bid) / mid * 1e4 if mid > 0 else 0.0
    return TickSample(
        ts=ts_ms,
        bid=bid,
        ask=ask,
        mid=mid,
        bid_size=bid_size,
        ask_size=ask_size,
        last_trade_price=last_trade_price if last_trade_price is not None else mid,
        last_trade_size=last_trade_size,
        spread_bps=spread_bps,
    )


def _burst_window(now_mono: float, direction: int) -> list[TickSample]:
    """16 near-flat ticks then a sharp directional jump with aggressor flow.

    ``direction`` +1 = up burst (buyer-aggressor) → burst_rider long.
    Ticks are stamped ~1s before ``now_mono`` (fresh). The jump trade prints on
    the aggressor side so aggr_flow agrees with velocity.
    """
    base_ms = int((now_mono - 1.0) * 1000)
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(16):
        mid += 0.001 * (1 if i % 2 == 0 else -1)  # tiny noise baseline
        ticks.append(_tick(base_ms + i * 50, mid))
    # Sharp jump on the last 3 ticks, trades printing on the aggressor side.
    for j in range(3):
        mid += direction * 0.5
        ltp = mid + direction * 0.05  # buyer lifts offer (up) / seller hits bid
        ticks.append(
            _tick(
                base_ms + (16 + j) * 50, mid,
                last_trade_price=ltp, last_trade_size=20.0,
                bid_size=30.0 if direction > 0 else 10.0,
                ask_size=10.0 if direction > 0 else 30.0,
            )
        )
    return ticks


def _overshoot_window(now_mono: float, direction: int) -> list[TickSample]:
    """A stretched mid with EXHAUSTING flow → micro_reversion fade.

    ``direction`` +1 = stretched UP with selling aggressor (flow opposes) →
    micro_reversion SHORT. Used for the reversion-exit test setup.
    """
    base_ms = int((now_mono - 1.0) * 1000)
    ticks: list[TickSample] = []
    mid = 100.0
    for i in range(14):
        mid += 0.001 * (1 if i % 2 == 0 else -1)
        ticks.append(_tick(base_ms + i * 50, mid))
    # Stretch the mid, but trades print AGAINST the move (exhaustion).
    for j in range(4):
        mid += direction * 0.4
        ltp = mid - direction * 0.1  # opposing aggressor (push exhausting)
        ticks.append(
            _tick(
                base_ms + (14 + j) * 50, mid,
                last_trade_price=ltp, last_trade_size=15.0,
            )
        )
    return ticks


# ---------------------------------------------------------------------------
# DB seeding.
# ---------------------------------------------------------------------------


def _seed(conn: sqlite3.Connection, *, regime: str, now_ts: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " product_class, stream_id, quote_ccy, state, vol_24h_usd, spread_bps, "
        " atr_24h_pct, depth_10bps_usd, signal_density_7d, last_seen_ts, "
        " is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            VENUE, SYMBOL, INSTRUMENT, GROUP, "crypto", "spot", "A_okx_crypto",
            "USDT", "live", 5e8, 2.0, 1.0, 1e7, 1.0, now_ts, 1,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO watchlist_focus "
        "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket) "
        "VALUES (?,?,?,?,?,?)",
        (now_ts, VENUE, SYMBOL, 1.0, 1, "focus"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO regime_state "
        "(venue, underlying_group_id, regime, confidence, updated_ts) "
        "VALUES (?,?,?,?,?)",
        (VENUE, GROUP, regime, 0.9, now_ts),
    )


def _focus() -> list[tuple[str, str, str, str]]:
    return [(VENUE, SYMBOL, "crypto", GROUP)]


# ---------------------------------------------------------------------------
# (a) clear burst → entry intent → sized order (non-shadow).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_produces_sized_order(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_mono, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.orders == 1, "a clear up-burst should open exactly one position"
    assert len(state.open_trades) == 1
    trade = state.open_trades[0]
    assert trade.side == "long"
    assert trade.venue == VENUE and trade.symbol == SYMBOL
    assert trade.notional_usd > 0.0, "the order must be sized via compute_size"
    # The opened position is tagged with the tick signal_family (momentum here).
    assert eng.family_by_position[trade.position_id] == "momentum"
    # A persisted positions row exists (entry path wrote it, not the hot path).
    row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", (trade.position_id,)
    ).fetchone()
    assert row is not None and row[0] == "open"


# ---------------------------------------------------------------------------
# (b) shadow mode logs the decision but places NO order.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_logs_no_order(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_mono, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=True))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.shadow_logs >= 1, "shadow mode must LOG the decision"
    assert eng.orders == 0, "shadow mode must NOT place an order"
    assert state.open_trades == []
    assert memdb.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# (c) freshness gate skips a stale symbol.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_symbol_skipped(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    # Same burst window, but the live_px monotonic stamp is far in the past →
    # stale past cfg.fresh_sec → the symbol is skipped.
    stale_mono = now_mono - 999.0
    writer.set_stream(INSTRUMENT, _burst_window(now_mono, +1), stale_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.skips_stale == 1
    assert eng.orders == 0
    assert state.open_trades == []


# ---------------------------------------------------------------------------
# (d) a 'short' intent on OKX (long-only) is dropped at the risk gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_intent_dropped_on_okx(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    # bear_trend keeps burst_rider active; a DOWN burst yields a SHORT intent.
    _seed(memdb, regime="bear_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_mono, -1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.drops_short >= 1, "a short on long-only OKX must be dropped"
    assert eng.orders == 0
    assert state.open_trades == []


# ---------------------------------------------------------------------------
# (e) no double-open when a position already exists on the symbol.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_double_open_when_already_held(memdb: sqlite3.Connection) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="bull_trend", now_ts=now_ts)
    writer = FakeQuoteWriter()
    writer.set_stream(INSTRUMENT, _burst_window(now_mono, +1), now_mono)
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    # A position already open on (okx, BTC-USDT) — the dedup must skip it.
    state.open_trades.append(
        SimulatedTrade(
            signal_id="prior", venue=VENUE, symbol=SYMBOL,
            strategy_id="prior", side="long", entry_price=100.0,
            notional_usd=50.0, open_ts=now_ts, position_id="pos_prior",
        )
    )
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    await _run_entries(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono,
        okx_adapter=None, capital_session=None, alpaca_adapter=None,
        real_roundtrip=False, focus=_focus(), regime_cache={},
    )

    assert eng.skips_dedup == 1, "an already-held symbol must be deduped"
    assert eng.orders == 0
    assert len(state.open_trades) == 1, "no second position stacked on the symbol"


# ---------------------------------------------------------------------------
# (f) a reversion position exits on flow reversal.
# ---------------------------------------------------------------------------


def test_scalp_exit_decision_flow_reversal() -> None:
    # A long reversion bet (faded a down-overshoot) with flow now firmly selling
    # again (ofi < 0) and pnl in the dead band → flow_reversal exit.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.0, ofi=-0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )
    # Symmetric short reversion with flow firmly buying again.
    assert (
        _scalp_exit_decision(
            side="short", entry_price=100.0, last_mid=100.0, ofi=0.5, pnl_r=0.0
        )
        == "flow_reversal"
    )
    # Flow aligned + pnl in band → hold.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.0, ofi=0.3, pnl_r=0.0
        )
        is None
    )
    # Micro-stop fires regardless of flow.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.0, ofi=0.3, pnl_r=-1.0
        )
        == "scalp_stop"
    )
    # Small-R target banks the snap-back.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=101.0, ofi=0.3, pnl_r=1.0
        )
        == "scalp_target"
    )


@pytest.mark.asyncio
async def test_reversion_position_exits_on_flow_reversal(
    memdb: sqlite3.Connection,
) -> None:
    now_ts = int(time.time())
    now_mono = time.monotonic()
    _seed(memdb, regime="chop", now_ts=now_ts)
    writer = FakeQuoteWriter()
    state = ProdLoopState()
    state.quote_writer = writer  # type: ignore[assignment]
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))

    # An open reversion (long) position on the symbol, tracked by the engine.
    trade = SimulatedTrade(
        signal_id="tick_micro_reversion_x", venue=VENUE, symbol=SYMBOL,
        strategy_id="micro_reversion", side="long", entry_price=100.0,
        notional_usd=50.0, open_ts=now_ts, position_id="pos_rev",
    )
    state.open_trades.append(trade)
    eng.family_by_position["pos_rev"] = "reversion"
    eng.entry_ref_by_position["pos_rev"] = 100.0

    # A flow window that is firmly SELLING (bid_size << ask_size → ofi < 0):
    # the long fade is failing → flow_reversal exit.
    base_ms = int((now_mono - 1.0) * 1000)
    sell_window = [
        _tick(base_ms + i * 50, 100.0, bid_size=2.0, ask_size=40.0)
        for i in range(20)
    ]
    writer.set_stream(INSTRUMENT, sell_window, now_mono)

    await _run_exits(
        memdb, state, eng, now_ts=now_ts, now_mono=now_mono, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
        lookup_regime=eng_mod._lookup_regime_str,
    )

    assert eng.scalp_exits == 1, "the reversion position must exit on flow reversal"
    assert "pos_rev" not in eng.family_by_position
    # The position is no longer open in the tracked book.
    assert all(t.position_id != "pos_rev" or t.closed for t in state.open_trades)
