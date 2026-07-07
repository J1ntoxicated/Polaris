"""Reset-only-on-ruin — TDD spec (Jin 2026-07-07). DEMO/PAPER, VIRTUAL ACCOUNT.

Measurement hygiene, NOT a defensive throttle: these tests pin

  1. SCHEMA: ``virtual_ruin_events`` table exists after DDL bootstrap.
  2. ABOVE FLOOR: no event, no re-seed (result carries current_equity through).
  3. BELOW FLOOR: WARNING logged + one ``virtual_ruin_events`` row recorded +
     re-seed signal == the exchange's $100k seed.
  4. ENV OVERRIDE: ``POLARIS_RUIN_FLOOR_FRAC`` changes the floor fraction.
  5. NEVER BLOCKS A TRADE: the function is a pure flag/re-seed signal — it
     never raises, never returns anything resembling a block/skip verdict.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from polaris.storage.virtual_ruin import check_and_reseed_ruin, ruin_floor_frac
from polaris.storage.weekly_equity_trace import week_start_ts


def test_schema_table_exists(memdb: sqlite3.Connection) -> None:
    row = memdb.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='virtual_ruin_events'"
    ).fetchone()
    assert row is not None
    cols = {r[1] for r in memdb.execute("PRAGMA table_info(virtual_ruin_events)")}
    assert cols == {
        "ruin_id", "exchange", "ruined_ts", "low_equity", "week_start_ts",
        "reseeded_to",
    }


def test_default_ruin_floor_frac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_RUIN_FLOOR_FRAC", raising=False)
    assert ruin_floor_frac() == 0.5


def test_ruin_floor_frac_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_RUIN_FLOOR_FRAC", "0.3")
    assert ruin_floor_frac() == 0.3


def test_above_floor_no_event_no_reseed(memdb: sqlite3.Connection) -> None:
    result = check_and_reseed_ruin(
        memdb, exchange="okx", current_equity=60_000.0, seed_equity=100_000.0,
        now_ts=1_000_000,
    )
    assert result.ruined is False
    assert result.reseed_equity == 60_000.0
    n = memdb.execute("SELECT COUNT(*) FROM virtual_ruin_events").fetchone()[0]
    assert n == 0


def test_below_floor_flags_records_and_reseeds(
    memdb: sqlite3.Connection, caplog: pytest.LogCaptureFixture,
) -> None:
    now_ts = 1_752_000_000  # arbitrary fixed ts
    with caplog.at_level(logging.WARNING):
        result = check_and_reseed_ruin(
            memdb, exchange="okx", current_equity=45_000.0,
            seed_equity=100_000.0, now_ts=now_ts,
        )
    assert result.ruined is True
    assert result.reseed_equity == 100_000.0
    assert any("virtual-ruin" in rec.message for rec in caplog.records)
    row = memdb.execute(
        "SELECT exchange, ruined_ts, low_equity, week_start_ts, reseeded_to "
        "FROM virtual_ruin_events"
    ).fetchone()
    assert row is not None
    assert row[0] == "okx"
    assert row[1] == now_ts
    assert row[2] == 45_000.0
    assert row[3] == week_start_ts(now_ts)
    assert row[4] == 100_000.0


def test_exactly_at_floor_is_not_ruined(memdb: sqlite3.Connection) -> None:
    result = check_and_reseed_ruin(
        memdb, exchange="okx", current_equity=50_000.0, seed_equity=100_000.0,
        now_ts=1_000_000,
    )
    assert result.ruined is False


def test_env_override_changes_floor_behavior(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_RUIN_FLOOR_FRAC", "0.3")
    # 40k is above a 30%-of-100k=30k floor -> not ruined (would have been
    # ruined under the default 50% floor).
    result = check_and_reseed_ruin(
        memdb, exchange="capital", current_equity=40_000.0, seed_equity=100_000.0,
        now_ts=1_000_000,
    )
    assert result.ruined is False


def test_never_raises_multiple_exchanges_independent(memdb: sqlite3.Connection) -> None:
    r_okx = check_and_reseed_ruin(
        memdb, exchange="okx", current_equity=10_000.0, seed_equity=100_000.0,
        now_ts=1_000_000,
    )
    r_cap = check_and_reseed_ruin(
        memdb, exchange="capital", current_equity=90_000.0, seed_equity=100_000.0,
        now_ts=1_000_000,
    )
    assert r_okx.ruined is True
    assert r_cap.ruined is False
    n = memdb.execute("SELECT COUNT(*) FROM virtual_ruin_events").fetchone()[0]
    assert n == 1
