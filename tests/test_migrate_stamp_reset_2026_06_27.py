"""audit2 P0-2 ③ — one-shot migration: stamp the 2026-06-27 21:13 (Sydney) live
DB reset (forensic log: "live DB reset 2026-06-27 21:13" — earliest post-reset
fill 2026-06-27 11:13:50 UTC) into ``measurement_resets`` so the since-reset
dashboard window has a real anchor instead of silently falling back to
all-time. Idempotent (never double-stamps); DEMO/PAPER; observability only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polaris.storage.measurement_reset import latest_reset
from polaris.storage.schema import init_db
from tools.migrate_stamp_reset_2026_06_27 import RESET_LABEL, RESET_TS, run


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "polaris_live.sqlite"
    conn = init_db(path)
    conn.close()
    return path


def test_stamps_the_2026_06_27_reset(db_path: Path) -> None:
    assert run(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        reset = latest_reset(conn)
    finally:
        conn.close()
    assert reset is not None
    assert reset.reset_ts == RESET_TS
    assert reset.label == RESET_LABEL
    assert reset.equity_baseline_usd > 0.0


def test_idempotent_never_double_stamps(db_path: Path) -> None:
    assert run(db_path) == 0
    assert run(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM measurement_resets").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_never_touches_positions_or_fills(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status) "
        "VALUES ('p1', 'okx', 'BTC-USDT', 's', 's', 's', 'long', 1.0, 'open')"
    )
    conn.commit()
    conn.close()
    assert run(db_path) == 0
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
