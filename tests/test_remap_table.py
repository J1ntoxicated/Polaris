"""Tests for remap_table (fee-split v1 FLIP item 2 — persisted per-track /
venue-pool threshold remap).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
vault/50_research/debates/fee_split_flip_r2_2026-07-12.md item 2 — per-track
(n>=30) individual remap, venue-pool (okx/capital/alpaca, NEVER a global
pool) fallback below that, hardcoded-legacy-default final fallback.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.remap_table import (
    TRACK_MIN_N,
    RemapEntry,
    compute_track_remap,
    compute_venue_pool_remap,
    pool_scope_key,
    resolve_thresholds,
    track_scope_key,
    upsert_remap_entry,
)
from polaris.core.classes.transition import TransitionThresholds
from polaris.storage.schema import init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_closed(
    conn: sqlite3.Connection, *, position_id: str, venue: str, strategy_id: str,
    symbol: str = "BTC-USDT", closed_ts: int, pnl_usd: float, size_usd: float = 1000.0,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, ?, ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, 1.0, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:{symbol}", strategy_id, size_usd,
         closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )
    conn.commit()


def _seed_track(conn: sqlite3.Connection, *, n: int, venue="okx", strategy_id="s1") -> None:
    for i in range(n):
        pnl = 10.0 if i % 2 == 0 else -5.0
        _mk_closed(
            conn, position_id=f"{strategy_id}_{i}", venue=venue, strategy_id=strategy_id,
            closed_ts=1_700_000_000 + i * 100, pnl_usd=pnl,
        )


# ---------------------------------------------------------------------------
# TRACK_MIN_N gating
# ---------------------------------------------------------------------------


def test_track_remap_none_below_min_n(conn):
    _seed_track(conn, n=TRACK_MIN_N - 1)
    assert compute_track_remap(conn, venue="okx", strategy_id="s1", now_ts=1_700_100_000) is None


def test_track_remap_built_at_min_n(conn):
    _seed_track(conn, n=TRACK_MIN_N)
    entry = compute_track_remap(conn, venue="okx", strategy_id="s1", now_ts=1_700_100_000)
    assert entry is not None
    assert entry.n_samples == TRACK_MIN_N
    assert entry.scope_type == "track"
    assert entry.scope_key == track_scope_key(venue="okx", strategy_id="s1")


def test_track_remap_isotonic_order_preserved(conn):
    """The 7 migrated thresholds stay in the SAME increasing order as their
    legacy source (-4,-3,-1,2,3,6,9) — the isotonic-enforcement contract."""
    _seed_track(conn, n=60)
    entry = compute_track_remap(conn, venue="okx", strategy_id="s1", now_ts=1_700_100_000)
    assert entry is not None
    t = entry.thresholds
    seq = [
        t.schmitt_bench_partial, t.schmitt_bench_full, t.schmitt_prove,
        t.prove_stagnation, t.ladder_step0, t.ladder_step1, t.ladder_step2,
    ]
    assert seq == sorted(seq)


def test_ladder_decay_mirrors_ladder_step0(conn):
    """ladder_decay's legacy value (3.0) is numerically identical to
    ladder_step0's (3.0) -> both migrate to the SAME new value."""
    _seed_track(conn, n=60)
    entry = compute_track_remap(conn, venue="okx", strategy_id="s1", now_ts=1_700_100_000)
    assert entry is not None
    assert entry.thresholds.ladder_decay == pytest.approx(entry.thresholds.ladder_step0)


def test_only_real_notional_rows_count_toward_min_n(conn):
    """A zero-notional row (no fills at all) never contributes a sample."""
    _mk_closed(conn, position_id="empty", venue="okx", strategy_id="s2",
               closed_ts=1_700_000_000, pnl_usd=0.0, size_usd=0.0)
    assert compute_track_remap(conn, venue="okx", strategy_id="s2", now_ts=1_700_100_000) is None


# ---------------------------------------------------------------------------
# Venue-pool fallback — aggregates ALL strategy_ids on one venue, never global
# ---------------------------------------------------------------------------


def test_venue_pool_aggregates_across_strategies(conn):
    _seed_track(conn, n=10, venue="okx", strategy_id="a")
    _seed_track(conn, n=15, venue="okx", strategy_id="b")
    entry = compute_venue_pool_remap(conn, venue="okx", now_ts=1_700_100_000)
    assert entry is not None
    assert entry.n_samples == 25
    assert entry.scope_type == "pool"
    assert entry.scope_key == pool_scope_key(venue="okx")


def test_venue_pool_never_crosses_venues(conn):
    """Capital's closes must NOT leak into the OKX pool (T3 fix — no global
    pooled fallback)."""
    _seed_track(conn, n=10, venue="okx", strategy_id="a")
    _seed_track(conn, n=10, venue="capital", strategy_id="c")
    okx_pool = compute_venue_pool_remap(conn, venue="okx", now_ts=1_700_100_000)
    assert okx_pool is not None
    assert okx_pool.n_samples == 10


def test_venue_pool_none_when_no_closes(conn):
    assert compute_venue_pool_remap(conn, venue="alpaca", now_ts=1_700_100_000) is None


# ---------------------------------------------------------------------------
# resolve_thresholds — track -> pool -> hardcoded-default fallback chain
# ---------------------------------------------------------------------------


def test_resolve_thresholds_defaults_when_table_empty(conn):
    t = resolve_thresholds(conn, venue="okx", strategy_id="brand_new")
    assert t == TransitionThresholds()


def test_resolve_thresholds_prefers_track_row_over_pool(conn):
    now_ts = 1_700_100_000
    track_entry = RemapEntry(
        scope_key=track_scope_key(venue="okx", strategy_id="s1"), scope_type="track",
        n_samples=30, computed_ts=now_ts,
        thresholds=TransitionThresholds(schmitt_prove=-99.0), ref_population=[1.0] * 30,
    )
    pool_entry = RemapEntry(
        scope_key=pool_scope_key(venue="okx"), scope_type="pool",
        n_samples=50, computed_ts=now_ts,
        thresholds=TransitionThresholds(schmitt_prove=-5.0), ref_population=[1.0] * 50,
    )
    upsert_remap_entry(conn, pool_entry)
    upsert_remap_entry(conn, track_entry)
    conn.commit()
    t = resolve_thresholds(conn, venue="okx", strategy_id="s1")
    assert t.schmitt_prove == pytest.approx(-99.0)


def test_resolve_thresholds_falls_back_to_pool_when_no_track_row(conn):
    now_ts = 1_700_100_000
    pool_entry = RemapEntry(
        scope_key=pool_scope_key(venue="okx"), scope_type="pool",
        n_samples=50, computed_ts=now_ts,
        thresholds=TransitionThresholds(schmitt_prove=-5.0), ref_population=[1.0] * 50,
    )
    upsert_remap_entry(conn, pool_entry)
    conn.commit()
    t = resolve_thresholds(conn, venue="okx", strategy_id="untracked_strategy")
    assert t.schmitt_prove == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# upsert staleness (item 4 — PSI/KS drift auto-flag, never blocks a write)
# ---------------------------------------------------------------------------


def test_upsert_first_write_never_stale(conn):
    entry = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=30, computed_ts=1_700_000_000,
        thresholds=TransitionThresholds(), ref_population=[1.0] * 30,
    )
    upsert_remap_entry(conn, entry)
    conn.commit()
    row = conn.execute(
        "SELECT stale, psi, ks FROM score_f_remap_table WHERE scope_key = 'okx:s1'"
    ).fetchone()
    assert row[0] == 0
    assert row[1] is None
    assert row[2] is None


def test_upsert_flags_stale_on_large_psi_drift(conn):
    stable_pop = [1.0] * 40
    entry1 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=40, computed_ts=1_700_000_000,
        thresholds=TransitionThresholds(), ref_population=stable_pop,
    )
    upsert_remap_entry(conn, entry1)
    conn.commit()

    # Wildly different distribution -> large PSI.
    drifted_pop = [100.0 + i for i in range(40)]
    entry2 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=40, computed_ts=1_700_100_000,
        thresholds=TransitionThresholds(), ref_population=drifted_pop,
    )
    upsert_remap_entry(conn, entry2)
    conn.commit()
    row = conn.execute(
        "SELECT stale, psi FROM score_f_remap_table WHERE scope_key = 'okx:s1'"
    ).fetchone()
    assert row[0] == 1
    assert row[1] > 0.20


def test_upsert_flags_stale_on_near_zero_threshold_drift(conn):
    entry1 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=30, computed_ts=1_700_000_000,
        thresholds=TransitionThresholds(schmitt_prove=-1.0), ref_population=[1.0] * 30,
    )
    upsert_remap_entry(conn, entry1)
    conn.commit()

    entry2 = RemapEntry(
        scope_key="okx:s1", scope_type="track", n_samples=30, computed_ts=1_700_100_000,
        thresholds=TransitionThresholds(schmitt_prove=-10.0), ref_population=[1.0] * 30,
    )
    upsert_remap_entry(conn, entry2)
    conn.commit()
    row = conn.execute(
        "SELECT stale FROM score_f_remap_table WHERE scope_key = 'okx:s1'"
    ).fetchone()
    assert row[0] == 1
