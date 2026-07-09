"""``--top-n-active`` — DB-driven dynamic-universe instrument selection.

Root cause this closes: ``scripts/run_replay_nightly.sh`` used to fall back to
a HARDCODED default (``okx:BTC-USDT okx:ETH-USDT okx:ADA-USDT``) whenever
``POLARIS_REPLAY_INSTRUMENTS`` was unset — a static ticker pin, the exact
anti-pattern Layer 0's dynamic universe exists to avoid. This module now asks
the live DB's own ``universe`` table (``is_active=1``, ranked by
``vol_24h_usd``) instead, so the nightly replay always tracks whatever the bot
is actually trading THAT night. Offline / behaviour 0 — self-contained on-disk
SQLite, never touches a live trading path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.run_replay import build_config, resolve_top_n_active_instruments
from polaris.storage.schema import init_db

BASE_TS = 1_700_000_000
HOUR = 3600


def _seed_universe(db: str, rows: list[tuple[str, str, str, float, int]]) -> None:
    """rows: (venue, symbol, instrument_id, vol_24h_usd, is_active)."""
    conn = init_db(db)
    try:
        for venue, symbol, instrument_id, vol, active in rows:
            conn.execute(
                "INSERT INTO universe (venue, symbol, instrument_id, "
                "underlying_group_id, asset_class, quote_ccy, state, "
                "vol_24h_usd, last_seen_ts, is_active) "
                "VALUES (?, ?, ?, 'g', 'crypto', 'USDT', 'live', ?, ?, ?)",
                (venue, symbol, instrument_id, vol, BASE_TS, active),
            )
        conn.commit()
    finally:
        conn.close()


def test_resolve_top_n_ranks_by_volume_desc_active_only(tmp_path: Path) -> None:
    db = str(tmp_path / "live.sqlite")
    _seed_universe(
        db,
        [
            ("okx", "BTC-USDT", "okx:BTC-USDT", 500_000_000.0, 1),
            ("okx", "ETH-USDT", "okx:ETH-USDT", 300_000_000.0, 1),
            ("okx", "SOL-USDT", "okx:SOL-USDT", 900_000_000.0, 0),  # inactive
            ("okx", "ADA-USDT", "okx:ADA-USDT", 40_000_000.0, 1),
        ],
    )
    top2 = resolve_top_n_active_instruments(db, 2)
    assert top2 == ["okx:BTC-USDT", "okx:ETH-USDT"]  # SOL excluded (inactive)


def test_resolve_top_n_missing_db_fails_open_to_empty(tmp_path: Path) -> None:
    assert resolve_top_n_active_instruments(str(tmp_path / "nope.sqlite"), 3) == []


def test_resolve_top_n_missing_table_fails_open_to_empty(tmp_path: Path) -> None:
    db = str(tmp_path / "bare.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    assert resolve_top_n_active_instruments(db, 3) == []


def test_build_config_top_n_active_resolves_when_instruments_empty(
    tmp_path: Path,
) -> None:
    db = str(tmp_path / "live.sqlite")
    _seed_universe(
        db,
        [
            ("okx", "BTC-USDT", "okx:BTC-USDT", 500_000_000.0, 1),
            ("okx", "ETH-USDT", "okx:ETH-USDT", 10_000_000.0, 1),
        ],
    )
    bundle = build_config(["--db", db, "--top-n-active", "1"])
    assert bundle.config.instrument_ids == ("okx:BTC-USDT",)


def test_build_config_explicit_instruments_wins_over_top_n_active(
    tmp_path: Path,
) -> None:
    """An explicit --instruments list is never overridden by --top-n-active."""
    db = str(tmp_path / "live.sqlite")
    _seed_universe(db, [("okx", "BTC-USDT", "okx:BTC-USDT", 1.0, 1)])
    bundle = build_config(
        ["--db", db, "--instruments", "okx:ETH-USDT", "--top-n-active", "5"]
    )
    assert bundle.config.instrument_ids == ("okx:ETH-USDT",)


def test_build_config_top_n_disabled_by_default_leaves_all_instruments(
    tmp_path: Path,
) -> None:
    db = str(tmp_path / "live.sqlite")
    _seed_universe(db, [("okx", "BTC-USDT", "okx:BTC-USDT", 1.0, 1)])
    bundle = build_config(["--db", db])
    assert bundle.config.instrument_ids == ()  # empty = replay ALL, no DB read


def _seed_bars_and_universe(db: str) -> None:
    conn = init_db(db)
    try:
        price = 100.0
        rows = []
        for i in range(60):
            nxt = price * 1.01
            rows.append(
                (
                    "okx:BTC-USDT", "crypto:BTC", "okx", "BTC-USDT", "1H",
                    BASE_TS + i * HOUR, price, max(price, nxt), min(price, nxt),
                    nxt, 1000.0 + i, price * 1000.0, 10, nxt, nxt, nxt, 4.0, "test",
                )
            )
            price = nxt
        conn.executemany(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
            "symbol, bar_interval, ts, open, high, low, close, volume, "
            "notional_usd, trade_count, vwap, bid_close, ask_close, "
            "spread_bps_close, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute(
            "INSERT INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, quote_ccy, state, "
            "vol_24h_usd, last_seen_ts, is_active) "
            "VALUES ('okx', 'BTC-USDT', 'okx:BTC-USDT', 'crypto:BTC', "
            "'crypto', 'USDT', 'live', 999.0, ?, 1)",
            (BASE_TS,),
        )
        conn.commit()
    finally:
        conn.close()


def test_main_persists_replay_row_via_top_n_active(tmp_path: Path) -> None:
    """Smoke: a full run with --top-n-active (no pinned --instruments) still
    persists exactly one replay_runs row + a benchmark_results tier row."""
    from polaris.scripts import run_replay

    db = str(tmp_path / "live.sqlite")
    _seed_bars_and_universe(db)
    rc = run_replay.main(
        [
            "--db", db,
            "--read-model-db", db,
            "--interval", "1H",
            "--top-n-active", "3",
            "--trials", "7",
        ]
    )
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM replay_runs").fetchone()[0]
        assert n == 1
        instrument_ids = conn.execute(
            "SELECT instrument_ids FROM replay_runs"
        ).fetchone()[0]
        assert instrument_ids == "okx:BTC-USDT"  # resolved from universe, not pinned
        tiers = {
            r[0]
            for r in conn.execute("SELECT DISTINCT tier FROM benchmark_results")
        }
        assert tiers == {"relative", "risk_adjusted", "statistical"}
    finally:
        conn.close()
