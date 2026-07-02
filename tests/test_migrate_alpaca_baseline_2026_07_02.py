"""P0-2 — migration test: fold Alpaca display-baseline into the stamped reset.

DEMO/PAPER; observability only. Verifies the UPDATE-in-place semantics (same
reset_ts, no new row) and idempotency.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.storage.measurement_reset import latest_reset, stamp_measurement_reset
from polaris.storage.schema import init_db
from tools.migrate_alpaca_baseline_2026_07_02 import (
    NEW_BASELINE_USD,
    OLD_BASELINE_USD,
    RESET_TS,
    run,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "polaris_live.sqlite"
    conn = init_db(path)
    stamp_measurement_reset(
        conn,
        label="2026-06-27 21:13 live DB reset (audit2 P0-2 backfill)",
        git_sha="178dbc0",
        reset_ts=RESET_TS,
        equity_baseline_usd=OLD_BASELINE_USD,
    )
    conn.commit()
    conn.close()
    return path


def test_updates_baseline_in_place(db_path: Path) -> None:
    assert run(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        reset = latest_reset(conn)
        n = conn.execute("SELECT COUNT(*) FROM measurement_resets").fetchone()[0]
    finally:
        conn.close()
    assert reset is not None
    assert reset.reset_ts == RESET_TS  # same reset instant — UPDATE, not INSERT
    assert reset.equity_baseline_usd == NEW_BASELINE_USD
    assert n == 1  # no new row


def test_idempotent(db_path: Path) -> None:
    assert run(db_path) == 0
    assert run(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM measurement_resets").fetchone()[0]
        reset = latest_reset(conn)
    finally:
        conn.close()
    assert n == 1
    assert reset is not None
    assert reset.equity_baseline_usd == NEW_BASELINE_USD


def test_noop_when_no_reset_row(tmp_path: Path) -> None:
    """A DB with no stamped reset yet is a graceful no-op (nothing to update)."""
    path = tmp_path / "polaris_live.sqlite"
    conn = init_db(path)
    conn.close()
    assert run(path) == 0
    conn = sqlite3.connect(path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM measurement_resets").fetchone()[0]
    finally:
        conn.close()
    assert n == 0
