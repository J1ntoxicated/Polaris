"""TTM Squeeze release-hit-rate offline probe (frontgate-scan item #9, stage 2).

DEMO/PAPER virtual capital only. Read-only digest — pure ``SELECT`` join of
``gate_shadow_events`` (squeeze-release rows) against 1H ``bars``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from polaris.scripts.ttm_squeeze_hit_rate import compute_release_hit_rate
from polaris.storage.schema import init_db

HOUR = 3600


def _seed_shadow(conn, *, event_id: str, venue: str, symbol: str, flags: str, created_ts: int) -> None:
    conn.execute(
        "INSERT INTO gate_shadow_events (event_id, run_id, gate_id, venue, symbol, "
        "regime, technical_decision, technical_flags, gpt_decision, mismatch, "
        "cell_warm, created_ts) VALUES (?, ?, 4, ?, ?, '', 'PROCEED', ?, '', 0, 0, ?)",
        (event_id, uuid.uuid4().hex, venue, symbol, flags, created_ts),
    )


def _seed_bar(conn, *, venue: str, symbol: str, ts: int, close: float) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) VALUES "
        "(?, ?, ?, ?, '1H', ?, ?, ?, ?, ?, 10.0)",
        (f"{venue}:{symbol}", f"{venue}.{symbol}", venue, symbol, ts, close, close, close, close),
    )


def test_bullish_release_that_moved_up_is_a_hit(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "hit.sqlite")
    try:
        _seed_shadow(
            conn, event_id="e1", venue="okx", symbol="BTC-USDT",
            flags="squeeze_release_bullish", created_ts=0,
        )
        for i in range(6):  # entry (i=0) + 5 forward bars
            _seed_bar(conn, venue="okx", symbol="BTC-USDT", ts=i * HOUR, close=100.0 + i)
        conn.commit()
        out = compute_release_hit_rate(conn, n=5)
        assert len(out) == 1
        assert out[0].direction == "bullish"
        assert out[0].hit is True
        assert out[0].entry_price == 100.0
        assert out[0].forward_price == 105.0
    finally:
        conn.close()


def test_bearish_release_that_moved_down_is_a_hit(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "hit_bear.sqlite")
    try:
        _seed_shadow(
            conn, event_id="e2", venue="okx", symbol="ETH-USDT",
            flags="squeeze_release_bearish", created_ts=0,
        )
        for i in range(6):
            _seed_bar(conn, venue="okx", symbol="ETH-USDT", ts=i * HOUR, close=100.0 - i)
        conn.commit()
        out = compute_release_hit_rate(conn, n=5)
        assert out[0].hit is True
    finally:
        conn.close()


def test_bullish_release_that_moved_down_is_a_miss(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "miss.sqlite")
    try:
        _seed_shadow(
            conn, event_id="e3", venue="okx", symbol="BTC-USDT",
            flags="squeeze_release_bullish", created_ts=0,
        )
        for i in range(6):
            _seed_bar(conn, venue="okx", symbol="BTC-USDT", ts=i * HOUR, close=100.0 - i)
        conn.commit()
        out = compute_release_hit_rate(conn, n=5)
        assert out[0].hit is False
    finally:
        conn.close()


def test_insufficient_forward_bars_excluded_not_a_miss(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "pending.sqlite")
    try:
        _seed_shadow(
            conn, event_id="e4", venue="okx", symbol="BTC-USDT",
            flags="squeeze_release_bullish", created_ts=0,
        )
        _seed_bar(conn, venue="okx", symbol="BTC-USDT", ts=0, close=100.0)  # only 1 bar
        conn.commit()
        out = compute_release_hit_rate(conn, n=5)
        assert out == []
    finally:
        conn.close()


def test_non_release_rows_are_ignored(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "ignore.sqlite")
    try:
        _seed_shadow(
            conn, event_id="e5", venue="okx", symbol="BTC-USDT",
            flags="squeeze_on", created_ts=0,
        )
        for i in range(6):
            _seed_bar(conn, venue="okx", symbol="BTC-USDT", ts=i * HOUR, close=100.0 + i)
        conn.commit()
        out = compute_release_hit_rate(conn, n=5)
        assert out == []
    finally:
        conn.close()


def test_no_rows_returns_empty(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "empty.sqlite")
    try:
        assert compute_release_hit_rate(conn) == []
    finally:
        conn.close()


def test_missing_table_graceful() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        assert compute_release_hit_rate(conn) == []
    finally:
        conn.close()
