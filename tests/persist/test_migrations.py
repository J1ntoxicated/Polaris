"""Tests for src/persist/migrations.py — JSON → SQLite backfill."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.persist.ledger import TradeLedger
from src.persist.migrations import (
    _parse_filename,
    _restore_position,
    migrate_paper_state,
)


class TestParseFilename:
    def test_basic_parse(self):
        assert _parse_filename("/x/paper_state_btc-usdt_volume_burst.json") == (
            "BTC-USDT", "volume_burst",
        )

    def test_strategy_with_underscores(self):
        assert _parse_filename("paper_state_eth-usdt_funding_carry.json") == (
            "ETH-USDT", "funding_carry",
        )

    def test_non_paper_state_returns_none(self):
        assert _parse_filename("garbage.json") is None
        assert _parse_filename("paper_log_btc.md") is None


class TestRestorePosition:
    def test_open_position(self):
        d = {
            "position_id": "BTC-USDT-1",
            "ticker": "BTC-USDT", "direction": 1,
            "entry_price": 80000.0, "size_usd": 300.0,
            "open_ts_ms": 1_700_000_000_000, "status": "open",
        }
        pos = _restore_position(d)
        assert pos is not None
        assert pos.is_open

    def test_closed_position(self):
        d = {
            "position_id": "BTC-USDT-2",
            "ticker": "BTC-USDT", "direction": 1,
            "entry_price": 80000.0, "size_usd": 300.0,
            "open_ts_ms": 1_700_000_000_000,
            "exit_price": 80800.0, "close_ts_ms": 1_700_000_001_000,
            "status": "closed",
        }
        pos = _restore_position(d)
        assert pos is not None
        assert not pos.is_open

    def test_malformed_returns_none(self):
        assert _restore_position({"position_id": "X"}) is None  # missing fields
        assert _restore_position({}) is None

    def test_zero_entry_price_skipped(self):
        d = {
            "position_id": "X", "ticker": "X", "direction": 1,
            "entry_price": 0.0, "size_usd": 100.0,
            "open_ts_ms": 1, "status": "open",
        }
        # Position __post_init__ raises on entry_price <= 0 → caught & skipped
        assert _restore_position(d) is None


class TestMigratePaperState:
    @pytest.fixture
    def state_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "state"
        d.mkdir()
        # Sample paper_state file with one open + one closed
        sample = {
            "starting_usd": 5000.0,
            "cash_usd": 4700.0,
            "open_positions": [
                {
                    "position_id": "BTC-USDT-1",
                    "ticker": "BTC-USDT", "direction": 1,
                    "entry_price": 80000.0, "size_usd": 300.0,
                    "open_ts_ms": 1_700_000_000_000,
                    "fee_round_trip": 0.002, "status": "open",
                },
            ],
            "closed_positions": [
                {
                    "position_id": "BTC-USDT-2",
                    "ticker": "BTC-USDT", "direction": 1,
                    "entry_price": 80000.0, "size_usd": 300.0,
                    "open_ts_ms": 1_699_000_000_000,
                    "exit_price": 80800.0, "close_ts_ms": 1_699_000_060_000,
                    "fee_round_trip": 0.002, "status": "closed",
                },
            ],
        }
        (d / "paper_state_btc-usdt_volume_burst.json").write_text(json.dumps(sample))
        return d

    def test_migration_creates_balance_and_positions(self, state_dir, tmp_path):
        db = tmp_path / "polaris.sqlite"
        stats = migrate_paper_state(state_dir, db)
        assert stats["files"] == 1
        assert stats["balances"] == 1
        assert stats["opens"] == 1
        assert stats["closes"] == 1

        with TradeLedger(db) as ledger:
            opens = ledger.get_open_positions(ticker="BTC-USDT")
            closes = ledger.get_closed_positions(ticker="BTC-USDT")
            # hypo_id resolved via REALTIME_HYPOS (volume_burst → HYPO-008-RT)
            # OR LEGACY-volume_burst fallback if cls instantiation fails
            row = ledger.conn.execute(
                "SELECT hypo_id FROM balances WHERE ticker = 'BTC-USDT'"
            ).fetchone()

        assert len(opens) == 1
        assert len(closes) == 1
        assert row is not None
        assert row["hypo_id"] in ("HYPO-008-RT", "LEGACY-volume_burst")

    def test_migration_idempotent(self, state_dir, tmp_path):
        db = tmp_path / "polaris.sqlite"
        # Run twice
        migrate_paper_state(state_dir, db)
        migrate_paper_state(state_dir, db)
        with TradeLedger(db) as ledger:
            opens = ledger.get_open_positions()
            closes = ledger.get_closed_positions()
        # No duplicates
        assert len(opens) == 1
        assert len(closes) == 1

    def test_migration_skips_malformed_files(self, tmp_path):
        d = tmp_path / "state"
        d.mkdir()
        (d / "paper_state_btc-usdt_test.json").write_text("not json {[")
        db = tmp_path / "polaris.sqlite"
        stats = migrate_paper_state(d, db)
        assert stats["errors"] == 1
        assert stats["files"] == 0
