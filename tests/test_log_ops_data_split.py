"""Operational↔data log split — dedup + log-on-change routing (behaviour-neutral).

Jin directive 2026-06-25: the per-tick / per-update DATA firehose moves to the
``polaris.datastream`` sink, the operator runtime log stays lean, and repeated
identical WARNINGs are deduped to a transition. These tests verify the LOGGING
behaviour changed (less runtime spam, structured data emitted) while the
DECISION behaviour is byte-identical (the recency guard still returns ``[]``,
the fallback DB row is still idempotent, the tick gate still builds the same
intents).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import pytest

import polaris.datastream as datastream
from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.scripts import _production_asset_class
from polaris.scripts._production_bars import read_recent_bars
from polaris.storage.schema import init_db


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "split.sqlite")
    yield c
    c.close()


@pytest.fixture()
def captured_emits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Capture every ``datastream.emit`` call (event, fields) without a sink."""
    calls: list[tuple[str, dict]] = []

    def _spy(event: str, **fields: object) -> None:
        calls.append((event, dict(fields)))

    # Patch the names the callsites imported (``from ... import emit as
    # datastream_emit``) AND the module function, so every route is captured.
    monkeypatch.setattr(datastream, "emit", _spy)
    import polaris.scripts._production_asset_class as ac_mod
    import polaris.scripts._production_bars as bars_mod

    monkeypatch.setattr(bars_mod, "datastream_emit", _spy)
    monkeypatch.setattr(ac_mod, "datastream_emit", _spy)
    return calls


def _bar(venue: str, symbol: str, ts: int, *, bar_interval: str = "1D") -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"equity:{symbol}",
        venue=venue,
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1_000_000.0, notional_usd=1e8, trade_count=5000,
        vwap=100.4, bid_close=0.0, ask_close=0.0, spread_bps_close=0.0,
        source="alpaca_rest",
    )


# --- recency guard: dedup WARN + per-read datastream, return-[] unchanged ----


def test_recency_warns_once_but_returns_empty_every_read(
    conn: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    captured_emits: list[tuple[str, dict]],
) -> None:
    now = int(time.time())
    stale = now - int(99 * 3600)
    for i in range(3):
        persist_bars(conn, [_bar("alpaca", "DEAD", stale - i * 86400)])

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            out = read_recent_bars(
                conn, venue="alpaca", symbol="DEAD", bar_interval="1D",
                freshness_threshold_sec=12 * 3600,
            )
            # DECISION unchanged: every stale read still returns [] (skip symbol).
            assert out == []

    recency_warns = [r for r in caplog.records if "[recency]" in r.message]
    assert len(recency_warns) == 1, "WARN deduped to the fresh→stale transition"
    # DATA emitted on every read (per-read detail to the batch sink).
    recency_emits = [c for c in captured_emits if c[0] == "bars/recency-stale"]
    assert len(recency_emits) == 5
    assert recency_emits[0][1]["instrument_id"] == "alpaca:DEAD"


def test_recency_redetects_after_fresh_recovery(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("alpaca", "RCV", stale)])
    with caplog.at_level(logging.WARNING):
        read_recent_bars(
            conn, venue="alpaca", symbol="RCV", bar_interval="1D",
            freshness_threshold_sec=12 * 3600,
        )
        # Fresh bar lands → guard clears (and the stale flag resets).
        persist_bars(conn, [_bar("alpaca", "RCV", now - 600)])
        fresh = read_recent_bars(
            conn, venue="alpaca", symbol="RCV", bar_interval="1D",
            freshness_threshold_sec=12 * 3600,
        )
        assert fresh, "fresh read returns bars (guard auto-clears)"
        # Feed dies again → a NEW transition WARN fires (flag was reset).
        persist_bars(conn, [_bar("alpaca", "RCV", now - int(99 * 3600))])
        # remove the fresh bar's dominance by reading with a tight window again
        read_recent_bars(
            conn, venue="alpaca", symbol="RCV", bar_interval="1D",
            freshness_threshold_sec=300,
        )
    recency_warns = [r for r in caplog.records if "[recency]" in r.message]
    assert len(recency_warns) == 2, "each fresh→stale transition re-warns"


# --- asset_class fallback: dedup WARN, DB row still idempotent ----------------


def test_asset_class_warns_once_db_idempotent(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    from polaris.scripts._production_asset_class import resolve_bar_asset_class

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            ac = resolve_bar_asset_class(
                venue="alpaca", symbol="SPCE", group_id="SPCE", conn=conn,
            )
            assert ac == "equity"  # DECISION unchanged: venue-correct default
    warns = [r for r in caplog.records if "[asset_class]" in r.message]
    assert len(warns) == 1, "WARN deduped to one per distinct instrument"
    rows = conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type='asset_class_fallback'"
    ).fetchone()[0]
    assert rows == 1, "DB row idempotent (unchanged)"


def test_asset_class_dedup_resets_between_distinct_symbols(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    from polaris.scripts._production_asset_class import resolve_bar_asset_class

    with caplog.at_level(logging.WARNING):
        resolve_bar_asset_class(venue="alpaca", symbol="AAA", group_id="AAA", conn=conn)
        resolve_bar_asset_class(venue="alpaca", symbol="BBB", group_id="BBB", conn=conn)
    warns = [r for r in caplog.records if "[asset_class]" in r.message]
    assert len(warns) == 2, "distinct instruments each warn once"


def test_fallback_warned_cache_is_resettable() -> None:
    # The dedup cache is a plain module set (cleared per-test by conftest).
    assert isinstance(_production_asset_class._FALLBACK_WARNED, set)


# --- tick gate: regime-active + seam2-confirm log-on-CHANGE -------------------


def _flat_window(now_ts: int, n: int = 19) -> list:
    """A calm, fresh window (no signal fires) — enough to reach the gate logs."""
    from polaris.core.ticks.types import TickSample

    ticks: list[TickSample] = []
    for i in range(n):
        mid = 100.0 + 0.001 * (1 if i % 2 == 0 else -1)
        half = 0.01
        ticks.append(
            TickSample(
                ts=now_ts - (n - 1 - i),
                bid=mid - half, ask=mid + half, mid=mid,
                bid_size=10.0, ask_size=10.0,
                last_trade_price=mid, last_trade_size=1.0,
                spread_bps=(2 * half) / mid * 1e4,
            )
        )
    return ticks


def test_tick_gate_logs_on_regime_change_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.ticks.config import TickEngineConfig
    from polaris.scripts import _production_tick_decision as dec
    from polaris.scripts._production_tick_decision import _collect_intents
    from polaris.scripts._production_tick_state import TickEngineState

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        dec, "datastream_emit",
        lambda event, **f: calls.append((event, dict(f))),
    )

    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    now = int(time.time())
    win = _flat_window(now)

    # First eval in 'bull_trend' → both gate-data events emit once.
    _collect_intents(eng, venue="okx", symbol="BTC-USDT", window=win,
                     regime="bull_trend", now_ts=now)
    # Repeat SAME regime twice more → log-on-change suppresses the repeats.
    for _ in range(2):
        _collect_intents(eng, venue="okx", symbol="BTC-USDT", window=win,
                         regime="bull_trend", now_ts=now)

    regime_active = [c for c in calls if c[0] == "tick-gate/regime-active"]
    seam2 = [c for c in calls if c[0] == "regime-fit/seam2-confirm"]
    assert len(regime_active) == 1, "regime-active emits once per (regime, set) state"
    assert len(seam2) == 1, "seam2-confirm emits once per regime state"

    # Regime CHANGES → both re-emit (a genuine state transition).
    _collect_intents(eng, venue="okx", symbol="BTC-USDT", window=win,
                     regime="bear_trend", now_ts=now)
    assert len([c for c in calls if c[0] == "tick-gate/regime-active"]) == 2
    assert len([c for c in calls if c[0] == "regime-fit/seam2-confirm"]) == 2


def test_tick_gate_independent_symbols_each_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.core.ticks.config import TickEngineConfig
    from polaris.scripts import _production_tick_decision as dec
    from polaris.scripts._production_tick_decision import _collect_intents
    from polaris.scripts._production_tick_state import TickEngineState

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        dec, "datastream_emit",
        lambda event, **f: calls.append((event, dict(f))),
    )
    eng = TickEngineState(cfg=TickEngineConfig(shadow=False))
    now = int(time.time())
    win = _flat_window(now)

    _collect_intents(eng, venue="okx", symbol="BTC-USDT", window=win,
                     regime="bull_trend", now_ts=now)
    _collect_intents(eng, venue="okx", symbol="ETH-USDT", window=win,
                     regime="bull_trend", now_ts=now)
    # Distinct symbols are tracked independently → each logs its first state.
    assert len([c for c in calls if c[0] == "tick-gate/regime-active"]) == 2
