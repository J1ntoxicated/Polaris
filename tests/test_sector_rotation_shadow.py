"""Sector/dual-momentum rotation context SHADOW (frontgate-scan item #8, G1).

DEMO/PAPER virtual capital only. behavior-0: ROUTING/CONTEXT recording only.
Covers:
- ``_sector_rotation_shadow`` pure functions (rebalance-boundary detection,
  ROC composite, cross-sectional rank).
- The ``sector_rank_shadow`` migration (fresh DB — additive new table).
- ``_production_layers.refresh_sector_rotation_shadow`` — the monthly
  rebalance-boundary gate (a no-op every non-boundary L0 cycle), the
  look-ahead guard, and ``delta_from_prior`` across two boundary cycles.

Spec source: vault/50_research/frontgate-scan/{integration-blueprint,
experiment-roadmap}.md item #8 (G1), Stage 1 only (ETF-only ranking; Stage 2
per-stock candidate mapping is a /debate-pending item, not built here).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from polaris.core.universe._sector_rotation_shadow import (
    SECTOR_ETF_SYMBOLS,
    SECTOR_ROC_LOOKBACK_DAYS,
    is_rebalance_boundary,
    rank_sector_etfs,
    sector_momentum_raw,
)
from polaris.scripts._production_layers import refresh_sector_rotation_shadow
from polaris.storage.schema import init_db

DAY = 86_400


def _epoch_day(y: int, m: int, d: int) -> int:
    """Independent oracle: days since 1970-01-01 for a UTC calendar date."""
    return (date(y, m, d) - date(1970, 1, 1)).days


# ---------------------------------------------------------------------------
# SECTOR_ETF_SYMBOLS
# ---------------------------------------------------------------------------


def test_sector_etf_symbols_has_11() -> None:
    assert len(SECTOR_ETF_SYMBOLS) == 11
    assert {"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"} == (
        SECTOR_ETF_SYMBOLS
    )


# ---------------------------------------------------------------------------
# is_rebalance_boundary — first 1D bar of a new UTC calendar month
# ---------------------------------------------------------------------------


def test_is_rebalance_boundary_fewer_than_2_bars_false() -> None:
    assert is_rebalance_boundary([]) is False
    assert is_rebalance_boundary([(0, 1.0, 1.0, 1.0)]) is False


def test_is_rebalance_boundary_same_month_false() -> None:
    d0 = _epoch_day(2024, 1, 30) * DAY
    d1 = _epoch_day(2024, 1, 31) * DAY
    bars = [(d0, 1.0, 1.0, 1.0), (d1, 1.0, 1.0, 1.0)]
    assert is_rebalance_boundary(bars) is False


def test_is_rebalance_boundary_month_crossed_true() -> None:
    d0 = _epoch_day(2024, 1, 31) * DAY
    d1 = _epoch_day(2024, 2, 1) * DAY
    bars = [(d0, 1.0, 1.0, 1.0), (d1, 1.0, 1.0, 1.0)]
    assert is_rebalance_boundary(bars) is True


def test_is_rebalance_boundary_year_crossed_true() -> None:
    d0 = _epoch_day(2023, 12, 31) * DAY
    d1 = _epoch_day(2024, 1, 1) * DAY
    bars = [(d0, 1.0, 1.0, 1.0), (d1, 1.0, 1.0, 1.0)]
    assert is_rebalance_boundary(bars) is True


def test_civil_month_matches_datetime_oracle_across_range() -> None:
    """Cross-check the datetime-free civil_from_days algorithm against the
    stdlib for a wide day-index range (leap years, month/year boundaries)."""
    from polaris.core.universe._sector_rotation_shadow import _civil_month

    start = _epoch_day(1999, 1, 1)
    end = _epoch_day(2031, 1, 1)
    for day in range(start, end, 37):  # sampled, not exhaustive (speed)
        expected_dt = datetime.fromtimestamp(day * DAY, tz=UTC)
        assert _civil_month(day) == (expected_dt.year, expected_dt.month)


# ---------------------------------------------------------------------------
# sector_momentum_raw / rank_sector_etfs
# ---------------------------------------------------------------------------


def test_sector_momentum_raw_insufficient_history_is_zero() -> None:
    bars = [(i * DAY, 100.0 + i, 101.0, 99.0) for i in range(10)]
    assert sector_momentum_raw(bars) == 0.0


def _trend_bars(base: float, slope: float, n: int) -> list[tuple[int, float, float, float]]:
    return [(i * DAY, base + slope * i, base + slope * i + 0.5, base + slope * i - 0.5) for i in range(n)]


def test_rank_sector_etfs_orders_by_momentum_descending() -> None:
    n = SECTOR_ROC_LOOKBACK_DAYS + 1
    bars_by_symbol = {
        "XLK": _trend_bars(100.0, 5.0, n),  # strongest up
        "XLE": _trend_bars(100.0, 0.5, n),  # mild up
        "XLU": _trend_bars(100.0, -5.0, n),  # strongest down
    }
    out = rank_sector_etfs(bars_by_symbol)
    by_symbol = {sym: (z, rank) for sym, z, rank in out}
    assert by_symbol["XLK"][1] == 1
    assert by_symbol["XLU"][1] == 3
    assert by_symbol["XLK"][0] > by_symbol["XLE"][0] > by_symbol["XLU"][0]


def test_rank_sector_etfs_deterministic_tie_break_alphabetical() -> None:
    bars_by_symbol = {"XLY": [], "XLB": [], "XLF": []}  # all 0.0 raw → tied
    out = rank_sector_etfs(bars_by_symbol)
    ranks = {sym: rank for sym, _, rank in out}
    assert ranks == {"XLB": 1, "XLF": 2, "XLY": 3}  # sorted-symbol tie order


# ---------------------------------------------------------------------------
# Migration — sector_rank_shadow table (additive, boot-time auto-create)
# ---------------------------------------------------------------------------


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_sector_rank_shadow_table_exists_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = _cols(conn, "sector_rank_shadow")
        assert {
            "cycle_ts", "sector_etf_symbol", "momentum_rank", "momentum_z",
            "candidate_symbols", "delta_from_prior", "created_ts",
        } <= cols
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# refresh_sector_rotation_shadow — DB glue
# ---------------------------------------------------------------------------


def _seed_1d(
    conn: sqlite3.Connection, *, symbol: str, ts: int, close: float, high: float, low: float
) -> None:
    # INSERT OR REPLACE: two seeded trend windows may share a UTC day (same
    # PK) when a test advances to a later boundary — the later write wins,
    # same as a real re-ingest of an already-seen day.
    conn.execute(
        "INSERT OR REPLACE INTO bars (instrument_id, underlying_group_id, venue, "
        "symbol, bar_interval, ts, open, high, low, close, volume) VALUES "
        "(?, ?, 'alpaca', ?, '1D', ?, ?, ?, ?, ?, 0)",
        (f"alpaca:{symbol}", f"alpaca.{symbol}", symbol, ts, close, high, low, close),
    )


def test_refresh_sector_rotation_shadow_noop_when_not_boundary(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "noboundary.sqlite")
    try:
        d0 = _epoch_day(2024, 1, 29) * DAY
        d1 = _epoch_day(2024, 1, 30) * DAY
        now_ts = _epoch_day(2024, 1, 31) * DAY  # "today" — both bars are lagged
        _seed_1d(conn, symbol="XLK", ts=d0, close=100.0, high=100.5, low=99.5)
        _seed_1d(conn, symbol="XLK", ts=d1, close=101.0, high=101.5, low=100.5)
        conn.commit()
        written = refresh_sector_rotation_shadow(conn, now_ts=now_ts)
        assert written == 0
        assert conn.execute("SELECT COUNT(*) FROM sector_rank_shadow").fetchone()[0] == 0
    finally:
        conn.close()


def test_refresh_sector_rotation_shadow_writes_on_boundary(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "boundary.sqlite")
    try:
        n = SECTOR_ROC_LOOKBACK_DAYS + 1
        start_day = _epoch_day(2023, 9, 1)  # far enough back for a Jan31->Feb1 cross
        # XLK: strong uptrend, ending on the month-boundary pair (Jan31, Feb1).
        for i in range(n - 1):
            ts = (start_day + i) * DAY
            _seed_1d(conn, symbol="XLK", ts=ts, close=100.0 + i, high=100.5 + i, low=99.5 + i)
        last_day = _epoch_day(2024, 2, 1)
        _seed_1d(
            conn, symbol="XLK", ts=last_day * DAY,
            close=100.0 + n - 1, high=100.5 + n - 1, low=99.5 + n - 1,
        )
        # XLE: mild uptrend, same window.
        for i in range(n - 1):
            ts = (start_day + i) * DAY
            _seed_1d(conn, symbol="XLE", ts=ts, close=100.0 + 0.1 * i, high=100.6, low=99.4)
        _seed_1d(
            conn, symbol="XLE", ts=last_day * DAY,
            close=100.0 + 0.1 * (n - 1), high=100.6, low=99.4,
        )
        conn.commit()
        now_ts = (last_day + 1) * DAY  # "today" is AFTER the boundary bar (lagged)
        written = refresh_sector_rotation_shadow(conn, now_ts=now_ts)
        assert written == 11  # all 11 ETFs get a row (9 with 0-history neutral)
        rows = conn.execute(
            "SELECT sector_etf_symbol, momentum_rank, momentum_z, candidate_symbols, "
            "delta_from_prior FROM sector_rank_shadow ORDER BY momentum_rank"
        ).fetchall()
        assert rows[0][0] == "XLK"  # strongest momentum → rank 1
        assert rows[0][3] == ""  # candidate_symbols stays '' (Stage 2 unbuilt)
        assert rows[0][4] is None  # first-ever row → no prior to diff against
    finally:
        conn.close()


def _seed_trend_ending_at(
    conn: sqlite3.Connection, *, symbol: str, end_day: int, n: int, base: float, slope: float
) -> None:
    """Seed ``n`` consecutive daily bars ending EXACTLY at ``end_day`` (epoch days)."""
    for i in range(n):
        day = end_day - (n - 1 - i)
        close = base + slope * i
        _seed_1d(conn, symbol=symbol, ts=day * DAY, close=close, high=close + 0.5, low=close - 0.5)


def test_refresh_sector_rotation_shadow_delta_from_prior_second_boundary(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "delta.sqlite")
    try:
        n = SECTOR_ROC_LOOKBACK_DAYS + 1
        # Boundary 1: Feb 1, 2024 (day before = Jan 31, 2024 — different month,
        # guaranteed rebalance boundary regardless of the preceding 120 days).
        boundary_1 = _epoch_day(2024, 2, 1)
        _seed_trend_ending_at(conn, symbol="XLK", end_day=boundary_1, n=n, base=100.0, slope=1.0)
        conn.commit()
        first = refresh_sector_rotation_shadow(conn, now_ts=(boundary_1 + 1) * DAY)
        assert first == 11
        z1 = conn.execute(
            "SELECT momentum_z FROM sector_rank_shadow WHERE sector_etf_symbol='XLK'"
        ).fetchone()[0]

        # Boundary 2: Mar 1, 2024 (day before = Feb 29, 2024, leap year — different
        # month). A steeper trend → a different momentum_z → a real delta.
        boundary_2 = _epoch_day(2024, 3, 1)
        _seed_trend_ending_at(conn, symbol="XLK", end_day=boundary_2, n=n, base=100.0, slope=3.0)
        conn.commit()
        second = refresh_sector_rotation_shadow(conn, now_ts=(boundary_2 + 1) * DAY)
        assert second == 11
        row2 = conn.execute(
            "SELECT momentum_z, delta_from_prior FROM sector_rank_shadow "
            "WHERE sector_etf_symbol='XLK' ORDER BY cycle_ts DESC LIMIT 1"
        ).fetchone()
        z2, delta = row2
        assert delta is not None
        assert delta == z2 - z1
    finally:
        conn.close()


def test_refresh_sector_rotation_shadow_excludes_same_utc_day_row(tmp_path: Path) -> None:
    """Look-ahead guard: a same-UTC-day row must never enter the composite."""
    conn = init_db(tmp_path / "lookahead.sqlite")
    try:
        d0 = _epoch_day(2024, 1, 30) * DAY
        d1 = _epoch_day(2024, 1, 31) * DAY
        today = _epoch_day(2024, 1, 31)  # "now" is the SAME UTC day as d1
        _seed_1d(conn, symbol="XLK", ts=d0, close=100.0, high=100.5, low=99.5)
        _seed_1d(conn, symbol="XLK", ts=d1, close=999.0, high=999.0, low=999.0)  # excluded
        conn.commit()
        # today's UTC midnight == d1's day → d1 is NOT lagged → excluded → only
        # d0 remains → 1 deduped bar → not a boundary (< 2 bars) → 0 written.
        written = refresh_sector_rotation_shadow(conn, now_ts=today * DAY + 100)
        assert written == 0
    finally:
        conn.close()


def test_refresh_sector_rotation_shadow_no_bars_table_graceful() -> None:
    """A DB with no ``bars``/``sector_rank_shadow`` tables must not raise."""
    conn = sqlite3.connect(":memory:")
    try:
        assert refresh_sector_rotation_shadow(conn, now_ts=1_780_000_000) == 0
    finally:
        conn.close()
