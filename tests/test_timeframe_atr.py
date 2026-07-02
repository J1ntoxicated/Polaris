"""Timeframe-aligned ATR helper (`_production_atr`) — unit + cache + schema.

DEMO/PAPER. Bug-fix coverage for the timeframe-blind exit ruler: every
strategy's R denominator / trail width was computed off the latest 20×1m bars
regardless of `StrategyMetadata.timeframe` (a 1H tsmom was trailed on 1m noise
and cut in ~11 minutes). `timeframe_atr_pct` reads the strategy's OWN
timeframe bars (tf → 1m fallback → None) with a degenerate-bar guard so a
flat/stale window can never collapse the R unit. Measurement/exit precision
only — no sizing change, no entry block (flow_not_block intact).
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from polaris.scripts._production_atr import (
    DEGENERATE_ATR_PCT,
    MIN_TF_BARS,
    bars_atr_pct,
    strategy_timeframe,
    timeframe_anchor_atr_pct,
    timeframe_atr_pct,
    timeframe_bar_rows,
)

NOW = 1_780_000_000
IID = "okx:BTC-USDT"


def _seed_bars(
    conn: sqlite3.Connection,
    *,
    interval: str,
    n: int,
    band: float,
    close: float = 100.0,
    end_ts: int = NOW,
    step: int | None = None,
    instrument_id: str = IID,
) -> None:
    """Insert ``n`` bars of ``interval`` ending at ``end_ts`` with (high-low)
    spread ``2*band`` so mean((high-low)/close) == 2*band/close exactly."""
    venue, _, symbol = instrument_id.partition(":")
    sec = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "1D": 86400}[interval]
    step = step or sec
    for i in range(n):
        ts = end_ts - (n - 1 - i) * step
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', ?, ?, ?, ?, ?, ?, ?, ?, 100.0, 1e4, 1, "
            " ?, ?, ?, 1.0, 'rest')",
            (
                instrument_id, venue, symbol, interval, ts,
                close, close + band, close - band, close, close, close, close,
            ),
        )


# --- strategy_timeframe ------------------------------------------------------


def test_strategy_timeframe_resolves_registry() -> None:
    # ema_crossover = the live OKX 1H registry strategy (spot_donchian, the prior
    # 1H vehicle, was un-registered 2026-06-27 #56 stop-bleeders KILL → it now
    # falls back to "1m"; ema_crossover keeps the registry-resolve coverage).
    assert strategy_timeframe("ema_crossover") == "1H"
    assert strategy_timeframe("connors_rsi2") == "1D"
    assert strategy_timeframe("session_breakout") == "5m"


def test_strategy_timeframe_unregistered_falls_back_to_1m() -> None:
    # tick-engine signals (micro_reversion / flow_pressure / burst_rider) are
    # NOT in STRATEGY_REGISTRY → "1m" = the pre-fix behaviour, byte-identical.
    assert strategy_timeframe("micro_reversion") == "1m"
    assert strategy_timeframe("flow_pressure") == "1m"
    assert strategy_timeframe("") == "1m"
    assert strategy_timeframe("no_such_strategy") == "1m"


# --- bars_atr_pct / timeframe_atr_pct ---------------------------------------


def test_tf_bars_present_returns_tf_atr(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.05)   # 1m atr = 0.001
    _seed_bars(memdb, interval="1H", n=20, band=2.0)    # 1H atr = 0.04
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert atr == pytest.approx(0.04)


def test_1d_with_exactly_20_bars_uses_1d_atr(memdb: sqlite3.Connection) -> None:
    """Review BLOCKER guard: the query LIMIT is 20, so any '< 21 bars →
    fallback' threshold would PERMANENTLY silence the 1D track. 20 bars of 1D
    must yield the 1D ATR, never the 1m fallback."""
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    _seed_bars(memdb, interval="1D", n=20, band=3.0)    # 1D atr = 0.06
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1D", now_ts=NOW)
    assert atr == pytest.approx(0.06)


def test_thin_tf_bars_fall_back_to_1m(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    _seed_bars(memdb, interval="1H", n=MIN_TF_BARS - 1, band=2.0)  # too thin
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert atr == pytest.approx(0.001)  # 1m fallback


def test_no_bars_at_all_returns_none(memdb: sqlite3.Connection) -> None:
    assert timeframe_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW
    ) is None


def test_degenerate_tf_bars_fall_back_to_1m(memdb: sqlite3.Connection) -> None:
    """Review BLOCKER guard: flat weekend bars (high==low → mean ~0) must NOT
    yield a ~0 anchor; the degenerate window falls through to 1m."""
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    _seed_bars(memdb, interval="1H", n=20, band=0.0)  # flat → degenerate
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert atr == pytest.approx(0.001)


def test_degenerate_everywhere_returns_none(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.0)
    _seed_bars(memdb, interval="1H", n=20, band=0.0)
    assert timeframe_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW
    ) is None
    assert DEGENERATE_ATR_PCT == 1e-5  # mirrors _atr_pct_from_bars guard


def test_future_ghost_bars_excluded(memdb: sqlite3.Connection) -> None:
    # All 1H bars +10h in the future (the stale Capital ghost; step compressed
    # so EVERY bar sits past now+skew) → excluded → falls back to the 1m window.
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, end_ts=NOW + 36_000, step=60)
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert atr == pytest.approx(0.001)


def test_1m_timeframe_matches_recalc_estimator(memdb: sqlite3.Connection) -> None:
    # Same estimator as the existing recalc 1m computation: mean((h-l)/c).
    _seed_bars(memdb, interval="1m", n=20, band=0.25)
    atr = timeframe_atr_pct(memdb, instrument_id=IID, timeframe="1m", now_ts=NOW)
    assert atr == pytest.approx(0.005)


def test_bars_atr_pct_min_bars_contract(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1H", n=3, band=2.0)
    assert bars_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW, min_bars=5,
    ) is None
    assert bars_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW, min_bars=3,
    ) == pytest.approx(0.04)


# --- timeframe_bar_rows -------------------------------------------------------
# [[1d_exit_horizon_fix_2026-07-02]] follow-up (review BLOCKER): the drift
# FLOOR was timeframe-scaled but the drift SIGNAL (momentum_drift, sourced from
# recalc's ``bar_row``) was still always read off the 1m window for EVERY
# strategy — an 11.2% 1D floor compared against a ~10-minute 1m drift never
# fires. ``timeframe_bar_rows`` is the missing counterpart to
# ``timeframe_atr_pct`` that scopes the drift SIGNAL itself to the strategy's
# own timeframe.


def _seed_drift_bars(
    conn: sqlite3.Connection,
    *,
    interval: str,
    closes: list[float],
    end_ts: int = NOW,
    step: int | None = None,
    instrument_id: str = IID,
) -> None:
    """Insert one bar per entry in ``closes`` (oldest first), ending at
    ``end_ts`` — lets a test construct a KNOWN (last-first)/first drift."""
    venue, _, symbol = instrument_id.partition(":")
    sec = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "1D": 86400}[interval]
    step = step or sec
    n = len(closes)
    for i, c in enumerate(closes):
        ts = end_ts - (n - 1 - i) * step
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', ?, ?, ?, ?, ?, ?, ?, ?, 100.0, 1e4, 1, "
            " ?, ?, ?, 1.0, 'rest')",
            (instrument_id, venue, symbol, interval, ts, c, c, c, c, c, c, c),
        )


def test_tf_bar_rows_returns_own_timeframe_window(memdb: sqlite3.Connection) -> None:
    # 1m closes drift +1% over the window; 1D closes drift +15% — the two
    # windows must resolve to DIFFERENT, non-conflated rows.
    _seed_drift_bars(memdb, interval="1m", closes=[100.0] * 9 + [101.0])
    _seed_drift_bars(memdb, interval="1D", closes=[100.0] * 9 + [115.0])
    rows = timeframe_bar_rows(memdb, instrument_id=IID, timeframe="1D", now_ts=NOW)
    assert rows is not None
    newest_close = float(rows[0][1])
    oldest_close = float(rows[-1][1])
    assert newest_close == pytest.approx(115.0)
    assert oldest_close == pytest.approx(100.0)


def test_tf_bar_rows_thin_window_returns_none(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1H", n=MIN_TF_BARS - 1, band=2.0)
    assert timeframe_bar_rows(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW, min_bars=MIN_TF_BARS,
    ) is None


def test_tf_bar_rows_no_bars_returns_none(memdb: sqlite3.Connection) -> None:
    assert timeframe_bar_rows(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW
    ) is None


def test_tf_bar_rows_excludes_future_ghost_bars(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1H", n=20, band=2.0, end_ts=NOW + 36_000, step=60)
    assert timeframe_bar_rows(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW, min_bars=MIN_TF_BARS,
    ) is None


# --- anchor variant (provenance) ---------------------------------------------


def test_anchor_returns_timeframe_used(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    _seed_bars(memdb, interval="1H", n=20, band=2.0)
    got = timeframe_anchor_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert got is not None
    atr, tf_used = got
    assert atr == pytest.approx(0.04)
    assert tf_used == "1H"


def test_anchor_fallback_provenance_is_1m(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.05)
    got = timeframe_anchor_atr_pct(memdb, instrument_id=IID, timeframe="1H", now_ts=NOW)
    assert got is not None
    atr, tf_used = got
    assert atr == pytest.approx(0.001)
    assert tf_used == "1m"


def test_anchor_none_when_unobtainable(memdb: sqlite3.Connection) -> None:
    assert timeframe_anchor_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW
    ) is None


# --- TTL cache ---------------------------------------------------------------


class _CountingConn:
    """Wrap a connection, counting ``execute`` calls that hit the bars table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.bars_queries = 0

    def execute(self, sql: str, *args: Any) -> Any:
        if "FROM bars" in sql:
            self.bars_queries += 1
        return self._conn.execute(sql, *args)


def test_cache_reuses_within_ttl_and_recomputes_after(
    memdb: sqlite3.Connection,
) -> None:
    _seed_bars(memdb, interval="1H", n=20, band=2.0)
    counting = _CountingConn(memdb)
    cache: dict[tuple[str, str], tuple[float, int]] = {}
    a1 = timeframe_atr_pct(
        counting, instrument_id=IID, timeframe="1H", now_ts=NOW, cache=cache,
    )
    assert counting.bars_queries == 1
    # Same tick + within the 1H cadence (300s) → served from cache, no query.
    a2 = timeframe_atr_pct(
        counting, instrument_id=IID, timeframe="1H", now_ts=NOW + 299, cache=cache,
    )
    assert counting.bars_queries == 1
    assert a1 == a2 == pytest.approx(0.04)
    # Past the cadence TTL → recomputed.
    timeframe_atr_pct(
        counting, instrument_id=IID, timeframe="1H", now_ts=NOW + 301, cache=cache,
    )
    assert counting.bars_queries == 2


def test_cache_keyed_per_instrument_and_timeframe(
    memdb: sqlite3.Connection,
) -> None:
    _seed_bars(memdb, interval="1H", n=20, band=2.0)
    _seed_bars(memdb, interval="1H", n=20, band=1.0, instrument_id="okx:ETH-USDT")
    cache: dict[tuple[str, str], tuple[float, int]] = {}
    a_btc = timeframe_atr_pct(
        memdb, instrument_id=IID, timeframe="1H", now_ts=NOW, cache=cache,
    )
    a_eth = timeframe_atr_pct(
        memdb, instrument_id="okx:ETH-USDT", timeframe="1H", now_ts=NOW, cache=cache,
    )
    assert a_btc == pytest.approx(0.04)
    assert a_eth == pytest.approx(0.02)
    assert len(cache) == 2


# --- schema migration --------------------------------------------------------


def test_positions_anchor_columns_in_fresh_ddl(memdb: sqlite3.Connection) -> None:
    cols = {r[1] for r in memdb.execute("PRAGMA table_info(positions)").fetchall()}
    assert "entry_atr_pct" in cols
    assert "entry_atr_timeframe" in cols


def test_post_migrations_add_anchor_columns_to_legacy_db(tmp_path: Any) -> None:
    from polaris.storage.schema import init_db

    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db, isolation_level=None)
    # Legacy positions table WITHOUT the anchor columns.
    conn.execute(
        "CREATE TABLE positions ("
        " position_id TEXT PRIMARY KEY, venue TEXT NOT NULL,"
        " symbol TEXT NOT NULL, strategy_id TEXT NOT NULL,"
        " entry_strategy_id TEXT NOT NULL, active_strategy_id TEXT NOT NULL,"
        " side TEXT NOT NULL, qty REAL NOT NULL, status TEXT NOT NULL,"
        " opened_ts INTEGER NOT NULL DEFAULT 0, closed_ts INTEGER,"
        " swap_count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.close()
    conn = init_db(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    assert "entry_atr_pct" in cols
    assert "entry_atr_timeframe" in cols
    # Idempotent: a second init_db run must not raise (duplicate column).
    conn.close()
    init_db(db).close()
