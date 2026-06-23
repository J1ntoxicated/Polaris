"""CLI wiring guard: ``run_replay.main`` writes a ``replay_runs`` row to the
read-model DB that actually backs the dashboard.

Why this exists: the harness was *present but unwired* — ``main`` defaulted its
read-model write to ``data/polaris.sqlite`` (a DB that has no ``replay_runs``
table and that nothing reads), while the dashboard + bars live in
``data/polaris_live.sqlite``. So every run either crashed on the missing table
or wrote rows nobody could see, and ``replay_runs`` stayed at 0 rows. These
tests pin the wiring: (1) the default read-model DB IS the live DB, and (2) a
full ``main`` run over a tiny seeded bar slice persists exactly one row.

Offline / behaviour 0 — a self-contained on-disk SQLite seeded with synthetic
bars; never touches a live trading path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.run_replay import build_config
from polaris.storage.schema import init_db

BASE_TS = 1_700_000_000
HOUR = 3600


def _seed_db(path: str) -> None:
    """Create an on-disk DB with the full schema + a small uptrend bar slice."""
    conn = init_db(path)
    try:
        price = 100.0
        rows = []
        for i in range(60):
            nxt = price * 1.01
            rows.append(
                (
                    "okx:BTC-USDT", "crypto:BTC", "okx", "BTC-USDT", "1H",
                    BASE_TS + i * HOUR, price, max(price, nxt), min(price, nxt), nxt,
                    1000.0 + i, price * 1000.0, 10, nxt, nxt, nxt, 4.0, "test",
                )
            )
            price = nxt
        conn.executemany(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
            "bar_interval, ts, open, high, low, close, volume, notional_usd, "
            "trade_count, vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_default_read_model_is_the_live_db() -> None:
    """The default read-model write target must be the DB the dashboard reads.

    Pins the wiring root cause: if this default drifts back to an unread DB,
    ``replay_runs`` silently goes empty again.
    """
    bundle = build_config(["--instruments", "okx:BTC-USDT"])
    assert bundle.read_model_db == "data/polaris_live.sqlite"
    assert bundle.config.live_db_path == "data/polaris_live.sqlite"
    assert bundle.persist is True


def test_main_persists_replay_row(tmp_path: Path) -> None:
    """A full ``main`` run over a seeded slice writes one ``replay_runs`` row."""
    from polaris.scripts import run_replay

    db = str(tmp_path / "live.sqlite")
    _seed_db(db)
    rc = run_replay.main(
        [
            "--db", db,
            "--read-model-db", db,
            "--interval", "1H",
            "--instruments", "okx:BTC-USDT",
            "--trials", "7",
        ]
    )
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM replay_runs").fetchone()[0]
        assert n == 1, f"expected exactly 1 replay_runs row, got {n}"
        verdict, n_trades = conn.execute(
            "SELECT verdict, n_trades FROM replay_runs"
        ).fetchone()
        assert verdict in {"PASS", "EDGE_WIDE_CI", "FAIL"}
        assert n_trades > 0  # the slice actually traded
        # benchmark_results carries the per-tier rows for the same run.
        tiers = {
            r[0]
            for r in conn.execute("SELECT DISTINCT tier FROM benchmark_results")
        }
        assert tiers == {"relative", "risk_adjusted", "statistical"}
    finally:
        conn.close()


def test_no_persist_writes_nothing(tmp_path: Path) -> None:
    """``--no-persist`` prints the verdict but writes no read-model row."""
    from polaris.scripts import run_replay

    db = str(tmp_path / "live.sqlite")
    _seed_db(db)
    rc = run_replay.main(
        ["--db", db, "--read-model-db", db, "--instruments", "okx:BTC-USDT", "--no-persist"]
    )
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM replay_runs").fetchone()[0] == 0
    finally:
        conn.close()
