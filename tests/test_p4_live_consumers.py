"""P4 — LIVE WS-price consumer wiring (builder 3).

Three consumers read real-time WS ticks with a bar-close fallback:

#1 dashboard (``snapshot_queries._last_prices``): prefer a FRESH ``quote_ticks``
   mid (real-time px-flash); a stale/absent tick → last bar close. Behavior 0
   (display only; ``positions.last_price`` reads the same dict downstream).
#2 exit (``_production_recalc.load_active_position_rows``): ``last_price`` is a
   FRESH WS ``live_px`` mid (M6 monotonic age check) so the precise-exit engine +
   G6 react to real-time price; stale/absent → bar close (graceful degrade).
#3 G4 (``_production_run_signal``): the watcher's ``tick_window`` is the writer's
   last ~30 ticks; no WS history → [] (freshness unknown, never a stale KILL).

Plus the foundation ring buffer (``QuoteTickWriter.recent_ticks``) that #3 reads.

DEMO/PAPER only; virtual funds. WS failure → bar fallback (never halts —
AGGRESSIVE invariant). No throttle, no size dampen, no chain multiplier.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from polaris.core.data.quote_writer import RING_BUFFER_DEPTH, QuoteTickWriter
from polaris.core.data.schema import QuoteTick
from polaris.core.pipeline.agents._shadow_rules import (
    GateDecision,
    g4_shadow_inputs_from_payload,
    technical_watch_decision,
)
from polaris.core.pipeline.payload_builder import build_watcher_payload
from polaris.scripts._production_recalc import (
    WS_EXIT_MARK_FRESH_SEC,
    load_active_position_rows,
)
from polaris.scripts.dashboard.snapshot_queries import _last_prices

NOW = int(time.time())


def _qt(inst: str, *, ts: int, mid: float) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst,
        venue="okx",
        symbol=inst.split(":", 1)[-1],
        ts=ts,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=10.0,
        source="okx_ws",
    )


# ---------------------------------------------------------------------------
# Foundation — ring buffer feeding #3
# ---------------------------------------------------------------------------


def test_recent_ticks_returns_g4_window_shape() -> None:
    w = QuoteTickWriter(":memory:")
    assert w.recent_ticks("okx:BTC-USDT") == []  # no history yet
    w.on_quote(_qt("okx:BTC-USDT", ts=NOW - 1, mid=100.0))
    w.on_quote(_qt("okx:BTC-USDT", ts=NOW, mid=101.0))
    window = w.recent_ticks("okx:BTC-USDT")
    assert len(window) == 2  # newest-last
    assert window[-1] == {"ts": NOW, "bid": 100.5, "ask": 101.5, "mid": 101.0}
    # keys are exactly what g4_shadow_inputs_from_payload reads.
    assert set(window[-1]) == {"ts", "bid", "ask", "mid"}


def test_recent_ticks_caps_at_ring_depth() -> None:
    w = QuoteTickWriter(":memory:")
    for i in range(RING_BUFFER_DEPTH + 15):
        w.on_quote(_qt("okx:BTC-USDT", ts=NOW + i, mid=100.0 + i))
    window = w.recent_ticks("okx:BTC-USDT")
    assert len(window) == RING_BUFFER_DEPTH  # bounded
    # newest-last: the final tick is the most recent one pushed.
    assert window[-1]["ts"] == NOW + RING_BUFFER_DEPTH + 14


# ---------------------------------------------------------------------------
# #1 dashboard — _last_prices prefers fresh quote_ticks mid, else bar close
# ---------------------------------------------------------------------------


def _seed_bar(conn: sqlite3.Connection, inst: str, *, close: float, ts: int) -> None:
    venue, symbol = inst.split(":", 1)
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, ts, "
        " open, high, low, close, volume, notional_usd, trade_count, vwap, "
        " bid_close, ask_close, spread_bps_close, source) "
        "VALUES (?, '', ?, ?, '1m', ?, ?, ?, ?, ?, 1.0, 1.0, 1, ?, ?, ?, 1.0, 'rest')",
        (inst, venue, symbol, ts, close, close, close, close, close, close, close),
    )


def _seed_tick(conn: sqlite3.Connection, inst: str, *, mid: float, ts: int) -> None:
    venue, symbol = inst.split(":", 1)
    conn.execute(
        "INSERT OR REPLACE INTO quote_ticks "
        "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 10.0, 'okx_ws')",
        (inst, venue, symbol, ts, mid - 0.5, mid + 0.5, mid),
    )


def test_last_prices_prefers_fresh_quote_tick(memdb: sqlite3.Connection) -> None:
    inst = "okx:BTC-USDT"
    # _last_prices judges freshness against live ``int(time.time())`` (_now_s),
    # so seed the tick relative to NOW-AT-CALL-TIME (not module-load NOW, which
    # drifts stale during a long suite run).
    now = int(time.time())
    _seed_bar(memdb, inst, close=100.0, ts=now - 30)
    _seed_tick(memdb, inst, mid=105.0, ts=now - 2)  # fresh WS tick
    prices = _last_prices(memdb)
    assert prices[inst] == pytest.approx(105.0)  # real-time mid wins


def test_last_prices_falls_back_to_bar_when_tick_stale(
    memdb: sqlite3.Connection,
) -> None:
    inst = "okx:ETH-USDT"
    _seed_bar(memdb, inst, close=50.0, ts=NOW - 30)
    _seed_tick(memdb, inst, mid=55.0, ts=NOW - 600)  # >60s old → stale
    prices = _last_prices(memdb)
    assert prices[inst] == pytest.approx(50.0)  # bar close fallback


def test_last_prices_bar_only_when_no_tick(memdb: sqlite3.Connection) -> None:
    inst = "okx:SOL-USDT"
    _seed_bar(memdb, inst, close=20.0, ts=NOW - 30)
    prices = _last_prices(memdb)
    assert prices[inst] == pytest.approx(20.0)


def test_last_prices_excludes_future_bar_prefers_fresh_tick(
    memdb: sqlite3.Connection,
) -> None:
    # Capital REST bars can be +10h future-dated (AEST-naive parse bug). A
    # future-dated bar (ts > now) must NOT be the MAX(ts) price source — the
    # fresh WS quote_tick is the unambiguous current price.
    inst = "capital:EURUSD"
    now = int(time.time())
    _seed_bar(memdb, inst, close=1.10, ts=now + 36000)  # +10h FUTURE bar
    _seed_tick(memdb, inst, mid=1.20, ts=now - 2)  # fresh WS tick
    prices = _last_prices(memdb)
    assert prices[inst] == pytest.approx(1.20)  # fresh quote wins, future bar excluded


def test_last_prices_normal_bar_fallback_when_no_tick(
    memdb: sqlite3.Connection,
) -> None:
    # A normal (past) bar is still the fallback when there is no fresh quote.
    inst = "capital:GBPUSD"
    now = int(time.time())
    _seed_bar(memdb, inst, close=1.30, ts=now - 30)  # normal past bar
    prices = _last_prices(memdb)
    assert prices[inst] == pytest.approx(1.30)


def test_last_prices_future_bar_excluded_no_tick(
    memdb: sqlite3.Connection,
) -> None:
    # A future-dated bar with NO fresh quote → excluded entirely (no price), so
    # the dashboard never shows a +10h-stamped future bar as "current".
    inst = "capital:USDJPY"
    now = int(time.time())
    _seed_bar(memdb, inst, close=150.0, ts=now + 36000)  # +10h FUTURE bar only
    prices = _last_prices(memdb)
    assert inst not in prices


# ---------------------------------------------------------------------------
# #2 exit — load_active_position_rows uses fresh live_px else bar close
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Minimal QuoteTickWriter stand-in: returns a canned (mid, monotonic)."""

    def __init__(self, px: dict[str, tuple[float, float]]) -> None:
        self._px = px

    def live_px(self, instrument_id: str) -> tuple[float, float] | None:
        return self._px.get(instrument_id)

    def recent_ticks(self, instrument_id: str) -> list[dict[str, object]]:
        return []


def _seed_position(
    conn: sqlite3.Connection, *, inst: str = "okx:BTC-USDT", bar_close: float = 100.0
) -> str:
    venue, symbol = inst.split(":", 1)
    position_id = uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count) VALUES (?, ?, ?, 'crypto:BTC', 'vb', 'vb', 'vb', 'long', "
        " 0.001, 'open', ?, 0)",
        (position_id, venue, symbol, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'vb', ?, ?, 'long', 0.001, 90.0, 80.0, 0.05, 1.0, 0.0, 0, "
        " ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, inst, venue, position_id, uuid.uuid4().hex),
    )
    # [P0-5] ts=NOW (>= opened_ts) — load_active_position_rows now excludes
    # pre-entry bars, so a bar older than the position's own open would fall
    # back to the entry-price fallback instead of the intended bar_close.
    _seed_bar(conn, inst, close=bar_close, ts=NOW)
    return position_id


def test_exit_uses_fresh_live_px(memdb: sqlite3.Connection) -> None:
    inst = "okx:BTC-USDT"
    _seed_position(memdb, inst=inst, bar_close=100.0)
    # Fresh tick (monotonic age ~1s) at a different price than the bar close.
    writer = _FakeWriter({inst: (123.0, time.monotonic() - 1.0)})
    rows = load_active_position_rows(memdb, quote_writer=writer)
    assert len(rows) == 1
    assert float(rows[0]["last_price"]) == pytest.approx(123.0)  # WS mid wins


def test_exit_falls_back_to_bar_when_tick_stale(memdb: sqlite3.Connection) -> None:
    inst = "okx:BTC-USDT"
    _seed_position(memdb, inst=inst, bar_close=100.0)
    # Stale tick: monotonic age beyond the reconnect-proof threshold.
    stale = time.monotonic() - (WS_EXIT_MARK_FRESH_SEC + 5.0)
    writer = _FakeWriter({inst: (123.0, stale)})
    rows = load_active_position_rows(memdb, quote_writer=writer)
    assert float(rows[0]["last_price"]) == pytest.approx(100.0)  # bar close


def test_exit_falls_back_to_bar_when_no_writer(memdb: sqlite3.Connection) -> None:
    inst = "okx:BTC-USDT"
    _seed_position(memdb, inst=inst, bar_close=100.0)
    rows = load_active_position_rows(memdb)  # no writer (smoke/replay path)
    assert float(rows[0]["last_price"]) == pytest.approx(100.0)


def test_exit_ignores_nonpositive_live_px(memdb: sqlite3.Connection) -> None:
    inst = "okx:BTC-USDT"
    _seed_position(memdb, inst=inst, bar_close=100.0)
    writer = _FakeWriter({inst: (0.0, time.monotonic())})  # degenerate mid
    rows = load_active_position_rows(memdb, quote_writer=writer)
    assert float(rows[0]["last_price"]) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# DIA/CNC 7-day frozen-mark P1 fix — mark_source stamp (2 cases)
# ---------------------------------------------------------------------------
#
# The Alpaca-held incident: quote_ticks mid stayed null for days, and the
# mark computation had no distinct label for "a genuine bar close substituted
# for the missing mid" — case 1 below. When even the bar is unavailable, the
# existing entry_price degrade must stay (no fabricated bar_fallback claim) —
# case 2.


def test_exit_mark_source_stamped_bar_fallback_when_mid_missing(
    memdb: sqlite3.Connection,
) -> None:
    inst = "alpaca:DIA"
    _seed_position(memdb, inst=inst, bar_close=100.0)
    # No writer at all (mirrors the Alpaca-held null-mid scenario) but a real
    # bar exists — the bar close substitutes for the missing mid.
    rows = load_active_position_rows(memdb)
    assert float(rows[0]["last_price"]) == pytest.approx(100.0)
    assert rows[0]["mark_source"] == "bar_fallback"


def test_exit_mark_source_entry_price_degrade_when_no_bar(
    memdb: sqlite3.Connection,
) -> None:
    inst = "alpaca:CNC"
    # Seed the position WITHOUT any bar row (bar_row query returns empty) —
    # last_price degrades to entry_price, the pre-existing behaviour. No
    # bar_fallback stamp is fabricated since no bar was actually used.
    position_id = uuid.uuid4().hex
    memdb.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count) VALUES (?, 'alpaca', 'CNC', 'equity:CNC', 'vb', 'vb', "
        " 'vb', 'long', 1.0, 'open', ?, 0)",
        (position_id, NOW),
    )
    memdb.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'vb', ?, 'alpaca', 'long', 1.0, 90.0, 80.0, 0.05, 1.0, "
        " 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, inst, position_id, uuid.uuid4().hex),
    )
    rows = load_active_position_rows(memdb)
    assert float(rows[0]["last_price"]) == pytest.approx(90.0)  # entry_price
    # review LOW (2026-07-16): the deepest freeze is now distinctly labeled —
    # never hides as plain 'bar' (which now means a healthy live/bar mark).
    assert rows[0]["mark_source"] == "entry_price_degrade"


# ---------------------------------------------------------------------------
# #3 G4 — writer ring → tick_window → live stale/crossed-book detection
# ---------------------------------------------------------------------------


def test_g4_consumes_live_tick_window_crossed_book() -> None:
    """A crossed-book WS tick (bid >= ask) flows ring → payload → G4 KILL.

    Mirrors the exact wiring in run_pipeline_for_signal:
    ``tick_window = writer.recent_ticks(instrument_id)`` →
    ``build_watcher_payload(tick_window=...)`` → G4 technical rule.
    """
    w = QuoteTickWriter(":memory:")
    inst = "okx:BTC-USDT"
    # A crossed book: bid above ask (microstructure broken).
    w.on_quote(
        QuoteTick(
            instrument_id=inst, venue="okx", symbol="BTC-USDT", ts=NOW,
            bid=101.0, ask=100.0, mid=100.5, spread_bps=10.0, source="okx_ws",
        )
    )
    payload = build_watcher_payload(
        spread_bps=10.0, baseline_p50_spread_bps=10.0, listing_age_hours=99.0,
        tick_window=w.recent_ticks(inst),
    )
    inputs = g4_shadow_inputs_from_payload(payload, now_ts=NOW)
    decision = technical_watch_decision(inputs)
    assert decision.decision is GateDecision.KILL
    assert decision.reason == "crossed_book"


def test_g4_empty_window_no_stale_kill_when_no_writer_history() -> None:
    """No WS history (empty ring) → PROCEED (freshness unknown, not stale)."""
    w = QuoteTickWriter(":memory:")
    payload = build_watcher_payload(
        spread_bps=10.0, baseline_p50_spread_bps=10.0, listing_age_hours=99.0,
        tick_window=w.recent_ticks("okx:ETH-USDT"),  # empty
    )
    inputs = g4_shadow_inputs_from_payload(payload, now_ts=NOW)
    decision = technical_watch_decision(inputs)
    assert decision.decision is GateDecision.PROCEED
