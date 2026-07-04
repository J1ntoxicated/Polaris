"""seed_tag cohort wiring — RawSignal -> G3 payload -> positions column.

DEMO/PAPER only. seed_tag is a SEPARATE field from thesis_tag (strategy-owned;
e.g. equity_52wk_high_breakout overwrites thesis_tag with its own breakout
string). It is a read-only cohort/measurement tag for the cowork
watchlist-intel feed — additive only, never a sizing/gating input
(flow_not_block). These tests pin: (1) RawSignal carries seed_tag additively
without touching thesis_tag, (2) the G3 validator payload surfaces it,
(3) the positions table has the column via BOTH the fresh-DB DDL and the
legacy-DB ALTER migration path (2026-07-03 last_promotion_ts drift precedent),
and (4) a position insert stamps it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.pipeline.payload_builder import build_validator_payload
from polaris.storage.schema import DEFAULT_DB_PATH, init_db
from polaris.strategies.base import RawSignal


def _make_raw_signal(**overrides: object) -> RawSignal:
    base = dict(
        signal_id="sig_test_seedtag",
        strategy_id="equity_52wk_high_breakout",
        symbol="AAPL",
        side="long",
        strength=1.0,
        sizing_hint=0.05,
        ttl_bars=10,
        thesis_tag="equity_52wk_high+brk=0.0123",
        correlation_group="equity_52wk_high_breakout",
    )
    base.update(overrides)
    return RawSignal(**base)  # type: ignore[arg-type]


def test_raw_signal_seed_tag_defaults_empty() -> None:
    """The overwhelming default (unseeded symbol) — byte-identical dead weight."""
    sig = _make_raw_signal()
    assert sig.seed_tag == ""


def test_raw_signal_seed_tag_is_additive_never_touches_thesis_tag() -> None:
    """Setting seed_tag must not disturb the strategy-owned thesis_tag."""
    sig = _make_raw_signal(seed_tag="pead_high")
    assert sig.seed_tag == "pead_high"
    assert sig.thesis_tag == "equity_52wk_high+brk=0.0123"


def test_validator_payload_surfaces_seed_tag(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "polaris.sqlite")
    try:
        sig = _make_raw_signal(seed_tag="sector_rs")
        payload = build_validator_payload(
            raw_signal=sig,
            venue="alpaca",
            symbol="AAPL",
            instrument_id="alpaca:AAPL",
            regime="bull_trend",
            conn=conn,
        )
        assert payload["raw_signal"]["seed_tag"] == "sector_rs"
        assert payload["raw_signal"]["thesis_tag"] == sig.thesis_tag
    finally:
        conn.close()


def test_validator_payload_seed_tag_defaults_empty_when_unseeded(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "polaris.sqlite")
    try:
        sig = _make_raw_signal()
        payload = build_validator_payload(
            raw_signal=sig,
            venue="okx",
            symbol="BTC-USDT",
            instrument_id="okx:BTC-USDT",
            regime="bull_trend",
            conn=conn,
        )
        assert payload["raw_signal"]["seed_tag"] == ""
    finally:
        conn.close()


def test_positions_table_has_seed_tag_column_fresh_db(tmp_path: Path) -> None:
    """DDL_POSITIONS (fresh-DB path) carries the column with a '' default."""
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
        assert "seed_tag" in cols
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
            "VALUES ('p1', 'alpaca', 'AAPL', 's', 's', 's', 'long', 1.0, 'open', 0)"
        )
        row = conn.execute(
            "SELECT seed_tag FROM positions WHERE position_id = 'p1'"
        ).fetchone()
        assert row[0] == ""
    finally:
        conn.close()


def test_legacy_db_missing_column_gets_alter_migrated(tmp_path: Path) -> None:
    """A pre-existing DB file (built before seed_tag existed) must gain the
    column idempotently via _apply_post_migrations, mirroring the
    last_promotion_ts legacy-ALTER precedent (schema-drift regression guard)."""
    db_path = tmp_path / "legacy.sqlite"
    # Build a minimal legacy positions table WITHOUT seed_tag (pre-migration shape).
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute(
        "CREATE TABLE positions ("
        "position_id TEXT PRIMARY KEY, venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, entry_strategy_id TEXT NOT NULL, "
        "active_strategy_id TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, "
        "status TEXT NOT NULL, opened_ts INTEGER NOT NULL DEFAULT 0)"
    )
    legacy_conn.execute(
        "INSERT INTO positions VALUES ('p_legacy', 'alpaca', 'AAPL', 's', 's', "
        "'s', 'long', 1.0, 'open', 0)"
    )
    legacy_conn.commit()
    legacy_conn.close()

    # init_db on the SAME file must run ALL_DDL (IF NOT EXISTS, no-op on
    # positions) + _apply_post_migrations (ALTERs the missing column in).
    migrated = init_db(db_path)
    try:
        cols = {
            row[1] for row in migrated.execute("PRAGMA table_info(positions)").fetchall()
        }
        assert "seed_tag" in cols
        row = migrated.execute(
            "SELECT seed_tag FROM positions WHERE position_id = 'p_legacy'"
        ).fetchone()
        assert row[0] == ""  # pre-existing row backfilled to the default
    finally:
        migrated.close()


def test_default_db_path_constant_exists() -> None:
    """Sanity: schema module still exports its DB path constant (no import break)."""
    assert DEFAULT_DB_PATH is not None
