"""Day 1 P2 fix — ``update_baseline_from_bars`` 2-pass iterator regression.

Before the fix, callers passing a generator (or any single-shot Iterable)
caused the second loop in ``update_baseline_from_bars`` to see an empty
sequence — so per-instrument baselines were never recomputed despite
samples being persisted.

This test reproduces the original bug by passing a generator, then
asserts that both ``ticker_baseline_samples`` AND
``ticker_baseline_state`` rows are populated.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from polaris.core.data.ingest import update_baseline_from_bars
from polaris.core.data.schema import Bar
from polaris.storage.schema import init_db


def _make_bars(n: int = 6) -> list[Bar]:
    """6 bars (above the per-metric warmup floor) with deterministic ATR."""
    out: list[Bar] = []
    for i in range(n):
        out.append(
            Bar(
                instrument_id="okx:BTC-USDT",
                underlying_group_id="BTC",
                venue="okx",
                symbol="BTC-USDT",
                bar_interval="1m",
                ts=1_700_000_000 + i * 60,
                open=80_000.0 + i,
                high=80_010.0 + i,
                low=79_990.0 + i,
                close=80_005.0 + i,
                volume=10.0 + i,
                notional_usd=80_000.0 * (10.0 + i),
                trade_count=100,
                vwap=80_000.0 + i,
                bid_close=0.0,
                ask_close=0.0,
                spread_bps_close=0.0,
                source="rest",
            )
        )
    return out


def _bars_generator(bars: list[Bar]) -> Iterator[Bar]:
    yield from bars


def test_update_baseline_from_bars_with_generator(tmp_path: Path) -> None:
    """Generator input must produce both samples + baseline-state rows."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        bars = _make_bars(6)
        n = update_baseline_from_bars(conn, _bars_generator(bars), asset_class="crypto")
        assert n == 18  # 6 bars × 3 metrics
        # Samples written.
        rows = conn.execute(
            "SELECT COUNT(*) FROM ticker_baseline_samples"
        ).fetchone()
        assert rows[0] == 18
        # State rows: one per (instrument, metric) — Day 1 P2 bug skipped this.
        states = conn.execute(
            "SELECT COUNT(*) FROM ticker_baseline_state"
        ).fetchone()
        assert states[0] == 3  # atr / size / volume
    finally:
        conn.close()


def test_update_baseline_from_bars_with_list(tmp_path: Path) -> None:
    """List input still works (regression guard for the pre-fix path)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        bars = _make_bars(4)
        n = update_baseline_from_bars(conn, bars, asset_class="crypto")
        assert n == 12
        states = conn.execute(
            "SELECT COUNT(*) FROM ticker_baseline_state"
        ).fetchone()
        assert states[0] == 3
    finally:
        conn.close()


def test_update_baseline_from_bars_empty_iterator(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        n = update_baseline_from_bars(conn, iter(()), asset_class="crypto")
        assert n == 0
    finally:
        conn.close()
