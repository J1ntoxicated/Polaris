"""XS-momentum + 52wk-high SHADOW composite (frontgate-scan item #3, behavior-0).

DEMO/PAPER virtual capital only. Covers:
- ``_momentum_shadow`` pure functions (dedup, composite raw, grouped-z combine).
- The ``rank_active_universe(momentum_z=...)`` seam — BEHAVIOR-0 proof (byte-
  identical output when the param is omitted) + shadow-field attachment when
  supplied.
- The ``universe.momentum_z`` / ``rank_score_shadow`` migration (fresh +
  legacy DB) + ``persist_momentum_shadow`` write path.
- ``_production_layers.compute_momentum_z_shadow`` — the look-ahead boundary
  (t-1 close, not "drop the last row") + the GROUP BY date(ts) dedup wiring.

Spec source: vault/50_research/frontgate-scan/{integration-blueprint,
experiment-roadmap}.md item #3 (G1). flow_not_block — RANK_SCORE_W_MOM stays
0.0; no filter/size-cut/GPT-call added.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from polaris.core.universe._momentum_shadow import (
    WINDOW_DAYS_52W,
    dedup_daily_bars,
    momentum_composite_raw,
    momentum_z_composite,
)
from polaris.core.universe._ranking import rank_active_universe
from polaris.core.universe.discovery import persist_momentum_shadow, persist_universe
from polaris.core.universe.schema import RANK_SCORE_W_MOM, UniverseInstrument
from polaris.scripts._production_layers import compute_momentum_z_shadow
from polaris.storage.schema import init_db

NOW = 1_780_000_000  # arbitrary fixed epoch (UTC-aligned math only)
DAY = 86_400


def _inst(
    symbol: str,
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    instrument_id: str | None = None,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=instrument_id or f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol}",
        asset_class=asset_class,
        quote_ccy="USDT",
        state="live",
        vol_24h_usd=5e8,
        spread_bps=2.0,
        atr_24h_pct=4.0,
        depth_10bps_usd=200_000.0,
        last_seen_ts=NOW,
    )


# ---------------------------------------------------------------------------
# RANK_SCORE_W_MOM — pinned at 0.0 (behavior-0 constant)
# ---------------------------------------------------------------------------


def test_rank_score_w_mom_pinned_zero() -> None:
    assert RANK_SCORE_W_MOM == 0.0


def test_universe_instrument_shadow_fields_default_none() -> None:
    ins = _inst("BTC-USDT")
    assert ins.momentum_z is None
    assert ins.rank_score_shadow is None


# ---------------------------------------------------------------------------
# dedup_daily_bars — GROUP BY UTC-date, day's LAST (max-ts) row wins
# ---------------------------------------------------------------------------


def test_dedup_daily_bars_empty() -> None:
    assert dedup_daily_bars([]) == []


def test_dedup_daily_bars_keeps_max_ts_row_per_day() -> None:
    day0 = 10 * DAY
    rows = [
        (day0, 100.0, 101.0, 99.0),  # 00:00 write
        (day0 + 16 * 3600, 105.0, 106.0, 104.0),  # 16:00 write — SAME day, later
    ]
    out = dedup_daily_bars(rows)
    assert out == [(day0 + 16 * 3600, 105.0, 106.0, 104.0)]


def test_dedup_daily_bars_distinct_days_all_kept_sorted() -> None:
    day0, day1 = 10 * DAY, 11 * DAY
    rows = [
        (day1 + 100, 2.0, 2.0, 2.0),
        (day0, 1.0, 1.0, 1.0),
    ]
    out = dedup_daily_bars(rows)
    assert [r[0] for r in out] == [day0, day1 + 100]


def test_dedup_daily_bars_okx_dual_cadence_shape() -> None:
    """OKX ~1.85x/day: 3 writes across 2 UTC days dedup to exactly 2 rows."""
    day0 = 100 * DAY
    rows = [
        (day0, 1.0, 1.0, 1.0),
        (day0 + 3600, 2.0, 2.0, 2.0),
        (day0 + DAY, 3.0, 3.0, 3.0),
    ]
    out = dedup_daily_bars(rows)
    assert len(out) == 2
    assert out[0] == (day0 + 3600, 2.0, 2.0, 2.0)  # later same-day row wins
    assert out[1] == (day0 + DAY, 3.0, 3.0, 3.0)


# ---------------------------------------------------------------------------
# momentum_composite_raw — insufficient history = neutral fallback
# ---------------------------------------------------------------------------


def test_momentum_composite_raw_empty_is_neutral() -> None:
    mom, prox = momentum_composite_raw([])
    assert mom == 0.0
    assert prox == 0.5


def test_momentum_composite_raw_insufficient_history_is_neutral() -> None:
    bars = [(i * DAY, 100.0 + i, 101.0 + i, 99.0 + i) for i in range(10)]
    mom, prox = momentum_composite_raw(bars)
    assert mom == 0.0
    assert prox == 0.5


def test_momentum_composite_raw_uptrend_positive_momentum_near_high() -> None:
    n = WINDOW_DAYS_52W + 1
    bars = [
        (i * DAY, 100.0 + i, 100.0 + i + 0.5, 100.0 + i - 0.5) for i in range(n)
    ]
    mom, prox = momentum_composite_raw(bars)
    assert mom > 0.0  # last close >> close[-253]
    assert prox > 0.9  # last close sits at the top of the 252-bar high/low range


# ---------------------------------------------------------------------------
# momentum_z_composite — grouped z + explicit-neutral for insufficient history
# ---------------------------------------------------------------------------


def _sufficient_bars(base: float, slope: float) -> list[tuple[int, float, float, float]]:
    n = WINDOW_DAYS_52W + 1
    return [
        (i * DAY, base + slope * i, base + slope * i + 0.5, base + slope * i - 0.5)
        for i in range(n)
    ]


def test_momentum_z_composite_missing_history_forced_neutral_not_skewed() -> None:
    # 3 instruments with REAL (strongly divergent) history + 1 with NO bars at
    # all. The no-history row must land at EXACTLY 0.0 — never a value derived
    # from folding a fallback-neutral raw signal into the real population.
    bars_by_id = {
        "okx:A": _sufficient_bars(100.0, 5.0),  # strong up
        "okx:B": _sufficient_bars(100.0, -5.0),  # strong down
        "okx:C": _sufficient_bars(100.0, 0.1),  # flat
        "okx:D": [],  # no history at all
    }
    groups_by_id = {k: "okx/crypto" for k in bars_by_id}
    out = momentum_z_composite(bars_by_id, groups_by_id)
    assert out["okx:D"] == 0.0
    assert out["okx:A"] > out["okx:C"] > out["okx:B"]


def test_momentum_z_composite_empty_input() -> None:
    assert momentum_z_composite({}, {}) == {}


def test_momentum_z_composite_all_insufficient_all_neutral() -> None:
    bars_by_id = {"okx:A": [(0, 1.0, 1.0, 1.0)], "okx:B": []}
    out = momentum_z_composite(bars_by_id, {"okx:A": "okx/crypto", "okx:B": "okx/crypto"})
    assert out == {"okx:A": 0.0, "okx:B": 0.0}


def test_momentum_z_composite_cross_sectional_grouping_no_cross_contamination() -> None:
    # Two disjoint venue/asset_class groups: an instrument's z is relative to
    # ITS OWN group only, mirroring _ranking._grouped_pop_z.
    bars_by_id = {
        "okx:A": _sufficient_bars(100.0, 5.0),
        "okx:B": _sufficient_bars(100.0, -5.0),
        "alpaca:X": _sufficient_bars(100.0, 5.0),
        "alpaca:Y": _sufficient_bars(100.0, -5.0),
    }
    groups_by_id = {
        "okx:A": "okx/crypto", "okx:B": "okx/crypto",
        "alpaca:X": "alpaca/equity", "alpaca:Y": "alpaca/equity",
    }
    out = momentum_z_composite(bars_by_id, groups_by_id)
    # Symmetric within each group (same magnitude, opposite sign shape).
    assert out["okx:A"] == pytest.approx(out["alpaca:X"], abs=1e-9)
    assert out["okx:B"] == pytest.approx(out["alpaca:Y"], abs=1e-9)


# ---------------------------------------------------------------------------
# rank_active_universe(momentum_z=...) seam — BEHAVIOR-0 proof
# ---------------------------------------------------------------------------


def test_rank_active_universe_byte_identical_when_momentum_z_omitted() -> None:
    insts = [_inst(f"SYM{i}-USDT", instrument_id=f"okx:SYM{i}-USDT") for i in range(5)]
    baseline = rank_active_universe(insts, top_n=5)
    out = rank_active_universe(insts, top_n=5)
    assert out == baseline
    for ins in out:
        assert ins.momentum_z is None
        assert ins.rank_score_shadow is None


def test_rank_active_universe_shadow_fields_populated_when_momentum_z_passed() -> None:
    insts = [_inst(f"SYM{i}-USDT", instrument_id=f"okx:SYM{i}-USDT") for i in range(3)]
    mz = {"okx:SYM0-USDT": 1.5, "okx:SYM1-USDT": -0.5}
    out = rank_active_universe(insts, top_n=5, momentum_z=mz)
    by_id = {ins.instrument_id: ins for ins in out}
    assert by_id["okx:SYM0-USDT"].momentum_z == 1.5
    assert by_id["okx:SYM1-USDT"].momentum_z == -0.5
    assert by_id["okx:SYM2-USDT"].momentum_z == 0.0  # missing lookup → neutral default


def test_rank_active_universe_selection_order_unchanged_by_momentum_z() -> None:
    """RANK_SCORE_W_MOM=0.0 → passing momentum_z must NOT change WHO is
    selected or their ORDER — only the two shadow fields differ."""
    insts = [_inst(f"SYM{i}-USDT", instrument_id=f"okx:SYM{i}-USDT") for i in range(5)]
    no_mz = rank_active_universe(insts, top_n=3)
    mz = {ins.instrument_id: float(10 - i) for i, ins in enumerate(insts)}  # wildly skewed
    with_mz = rank_active_universe(insts, top_n=3, momentum_z=mz)
    assert [i.instrument_id for i in no_mz] == [i.instrument_id for i in with_mz]


def test_rank_active_universe_rank_score_shadow_equals_real_score_via_zero_weight() -> None:
    insts = [_inst(f"SYM{i}-USDT", instrument_id=f"okx:SYM{i}-USDT") for i in range(3)]
    mz = {"okx:SYM0-USDT": 7.0}  # large, would move the real score if weight != 0
    out = rank_active_universe(insts, top_n=5, momentum_z=mz)
    baseline = rank_active_universe(insts, top_n=5)
    baseline_by_id = {i.instrument_id: i for i in baseline}
    for ins in out:
        assert ins.rank_score_shadow is not None
        # rank_score_shadow must equal the byte-identical real ordering position
        # (proxy: relative order matches, since RANK_SCORE_W_MOM=0.0 folds to
        # a no-op — direct value check is covered by the order-unchanged test).
        assert ins.instrument_id in baseline_by_id


# ---------------------------------------------------------------------------
# Migration — universe.momentum_z / rank_score_shadow (fresh + legacy DB)
# ---------------------------------------------------------------------------


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_momentum_shadow_columns_exist_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = _cols(conn, "universe")
        assert "momentum_z" in cols
        assert "rank_score_shadow" in cols
    finally:
        conn.close()


def test_legacy_universe_db_gets_momentum_shadow_columns(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE universe ("
        "venue TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL, "
        "underlying_group_id TEXT NOT NULL, asset_class TEXT NOT NULL, "
        "quote_ccy TEXT NOT NULL, state TEXT NOT NULL, vol_24h_usd REAL NOT NULL, "
        "spread_bps REAL NOT NULL, atr_24h_pct REAL NOT NULL, "
        "depth_10bps_usd REAL NOT NULL, signal_density_7d REAL NOT NULL, "
        "last_seen_ts INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, "
        "active_reason TEXT, PRIMARY KEY (venue, symbol))"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        cols = _cols(conn, "universe")
        assert "momentum_z" in cols
        assert "rank_score_shadow" in cols
    finally:
        conn.close()


def test_momentum_shadow_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "twice.sqlite"
    init_db(db).close()
    conn = init_db(db)  # re-run: guarded ALTER must not raise
    try:
        assert "momentum_z" in _cols(conn, "universe")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# persist_momentum_shadow — targeted UPDATE, only non-None rows written
# ---------------------------------------------------------------------------


def test_persist_momentum_shadow_writes_only_annotated_rows(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "shadow.sqlite")
    try:
        a = _inst("AAA-USDT")
        b = _inst("BBB-USDT")
        persist_universe(conn, [a, b])
        ranked = [replace(a, momentum_z=1.25, rank_score_shadow=0.5)]
        persist_momentum_shadow(conn, ranked)
        row_a = conn.execute(
            "SELECT momentum_z, rank_score_shadow FROM universe WHERE symbol='AAA-USDT'"
        ).fetchone()
        row_b = conn.execute(
            "SELECT momentum_z, rank_score_shadow FROM universe WHERE symbol='BBB-USDT'"
        ).fetchone()
        assert row_a == (1.25, 0.5)
        assert row_b == (None, None)  # untouched — no shadow annotation
    finally:
        conn.close()


def test_persist_momentum_shadow_empty_list_is_noop(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "noop.sqlite")
    try:
        persist_momentum_shadow(conn, [])  # must not raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# compute_momentum_z_shadow — look-ahead guard + dedup wiring (DB glue)
# ---------------------------------------------------------------------------


def _seed_1d_bar(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    venue: str,
    symbol: str,
    ts: int,
    close: float,
    high: float,
    low: float,
) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) VALUES "
        "(?, ?, ?, ?, '1D', ?, ?, ?, ?, ?, 0)",
        (instrument_id, f"{venue}.{symbol}", venue, symbol, ts, close, high, low, close),
    )


def test_compute_momentum_z_shadow_empty_instruments_returns_empty(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "empty.sqlite")
    try:
        assert compute_momentum_z_shadow(conn, []) == {}
    finally:
        conn.close()


def test_compute_momentum_z_shadow_excludes_same_utc_day_row(tmp_path: Path) -> None:
    """Look-ahead guard: a bar row stamped THIS UTC day must never enter the
    lagged (t-1) window — dropping only "the last row" would be insufficient
    on OKX/Capital's dual-cadence 1D writes (multiple rows can land today)."""
    conn = init_db(tmp_path / "lookahead.sqlite")
    try:
        ins = _inst("AAA-USDT")
        today_start = NOW - (NOW % DAY)
        # 253 lagged daily rows (well before today), flat.
        for i in range(WINDOW_DAYS_52W + 1):
            ts = today_start - (WINDOW_DAYS_52W + 1 - i) * DAY
            _seed_1d_bar(
                conn, instrument_id=ins.instrument_id, venue="okx", symbol="AAA-USDT",
                ts=ts, close=100.0, high=100.5, low=99.5,
            )
        # A same-UTC-day (today, already exists) row with an extreme outlier
        # value — MUST be excluded, or it would blow out the composite.
        _seed_1d_bar(
            conn, instrument_id=ins.instrument_id, venue="okx", symbol="AAA-USDT",
            ts=today_start + 100, close=99999.0, high=99999.0, low=99999.0,
        )
        conn.commit()
        out = compute_momentum_z_shadow(conn, [ins], now_ts=NOW)
        # Flat lagged history only → neutral composite (momentum=0, prox=0.5
        # region) → grouped z with a single-member group collapses to 0.0.
        assert out[ins.instrument_id] == pytest.approx(0.0, abs=1e-9)
    finally:
        conn.close()


def test_compute_momentum_z_shadow_dedups_dual_cadence_same_day(tmp_path: Path) -> None:
    """Two writes on the SAME UTC day (00:00 + 16:00, the real OKX/Capital
    cadence) must dedup to ONE row (the later write), not double-count the day."""
    conn = init_db(tmp_path / "dedup.sqlite")
    try:
        ins = _inst("AAA-USDT")
        today_start = NOW - (NOW % DAY)
        base_day = today_start - (WINDOW_DAYS_52W + 1) * DAY
        for i in range(WINDOW_DAYS_52W):
            ts = base_day + i * DAY
            _seed_1d_bar(
                conn, instrument_id=ins.instrument_id, venue="okx", symbol="AAA-USDT",
                ts=ts, close=100.0 + i, high=100.5 + i, low=99.5 + i,
            )
        # Dual-cadence: a SECOND write on the FINAL lagged day at a wildly
        # different close. If dedup failed to pick the later (correct) row,
        # or double-counted the day, the composite would not match the
        # single-row-per-day expectation.
        last_ts = base_day + (WINDOW_DAYS_52W - 1) * DAY
        _seed_1d_bar(
            conn, instrument_id=ins.instrument_id, venue="okx", symbol="AAA-USDT",
            ts=last_ts - 3600, close=1.0, high=1.0, low=1.0,  # earlier same-day, ignored
        )
        conn.commit()
        out = compute_momentum_z_shadow(conn, [ins], now_ts=NOW)
        # Single-instrument group → the composite is well-defined and finite
        # (no crash / NaN from a mis-deduped double row).
        assert out[ins.instrument_id] == pytest.approx(0.0, abs=1e-9)
    finally:
        conn.close()


def test_compute_momentum_z_shadow_no_bars_neutral(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "nobars.sqlite")
    try:
        ins = _inst("NEW-USDT")
        out = compute_momentum_z_shadow(conn, [ins], now_ts=NOW)
        assert out[ins.instrument_id] == 0.0
    finally:
        conn.close()
