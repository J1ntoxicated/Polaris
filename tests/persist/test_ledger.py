"""Tests for src/persist/ledger.py — TradeLedger SQLite SSOT.

Uses :memory: SQLite for fast, isolated tests.
"""
from __future__ import annotations

import time

import pytest

from src.paper.state import PaperBalance, Position
from src.persist.ledger import TradeLedger
from src.persist.schema import SCHEMA_VERSION


@pytest.fixture
def ledger():
    """In-memory TradeLedger fixture."""
    led = TradeLedger(":memory:")
    led.open()
    yield led
    led.close()


@pytest.fixture
def sample_position():
    """A sample open Position for tests."""
    return Position(
        position_id="BTC-USDT-1700000000000",
        ticker="BTC-USDT",
        direction=1,
        entry_price=80000.0,
        size_usd=300.0,
        open_ts_ms=1_700_000_000_000,
        fee_round_trip=0.002,
    )


# ─── Schema initialization ───────────────────────────────────────────────────


class TestSchemaInit:
    def test_schema_version_stamped(self, ledger):
        cur = ledger.conn.execute("SELECT version FROM schema_version")
        assert cur.fetchone()["version"] == SCHEMA_VERSION

    def test_schema_idempotent_on_reopen(self, tmp_path):
        db = tmp_path / "test.sqlite"
        with TradeLedger(db) as l1:
            n1 = l1.conn.execute("SELECT COUNT(*) c FROM schema_version").fetchone()["c"]
        with TradeLedger(db) as l2:
            n2 = l2.conn.execute("SELECT COUNT(*) c FROM schema_version").fetchone()["c"]
        assert n1 == n2 == 1

    def test_wal_pragma_active(self, tmp_path):
        # :memory: doesn't support WAL — use file
        db = tmp_path / "test.sqlite"
        with TradeLedger(db) as l:
            mode = l.conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


# ─── Position lifecycle ──────────────────────────────────────────────────────


class TestPositionLifecycle:
    def test_insert_open_position(self, ledger, sample_position):
        ledger.insert_position_open(
            sample_position,
            hypo_id="HYPO-008-RT",
            strategy_name="volume_burst",
            signal_meta={
                "entry_slip_bps": 2.5,
                "signal_confidence": 0.75,
                "signal_reason": "vol_2x_bullish",
                "regime": "normal",
            },
        )
        opens = ledger.get_open_positions(hypo_id="HYPO-008-RT")
        assert len(opens) == 1
        assert opens[0].position_id == sample_position.position_id

    def test_insert_idempotent(self, ledger, sample_position):
        # Re-inserting same position_id replaces, doesn't duplicate
        for _ in range(3):
            ledger.insert_position_open(
                sample_position, "HYPO-008-RT", "volume_burst",
            )
        opens = ledger.get_open_positions()
        assert len(opens) == 1

    def test_close_position_computes_pnl(self, ledger, sample_position):
        ledger.insert_position_open(sample_position, "HYPO-008-RT", "volume_burst")
        # Close at +1% gross → net = +1% - 0.2% fee = +0.8%
        ledger.update_position_close(
            position_id=sample_position.position_id,
            exit_price=80800.0,  # +1%
            close_ts_ms=1_700_000_000_000 + 60_000,
            exit_reason="tp_hit:+0.0100",
            exit_slip_bps=3.0,
        )
        row = ledger.conn.execute(
            "SELECT gross_usd, fee_usd, net_usd, status FROM positions WHERE position_id=?",
            (sample_position.position_id,),
        ).fetchone()
        assert row["status"] == "closed"
        # gross = (80800-80000)/80000 * 300 = 3.0
        assert abs(row["gross_usd"] - 3.0) < 0.01
        # fee = 0.002 * 300 = 0.6
        assert abs(row["fee_usd"] - 0.6) < 0.01
        # net = 3.0 - 0.6 = 2.4
        assert abs(row["net_usd"] - 2.4) < 0.01

    def test_close_nonexistent_raises(self, ledger):
        with pytest.raises(ValueError, match="position not found"):
            ledger.update_position_close(
                position_id="NONEXISTENT",
                exit_price=100.0,
                close_ts_ms=1,
                exit_reason="test",
            )

    def test_get_open_filtered_by_ticker(self, ledger):
        for tk in ("BTC-USDT", "ETH-USDT", "BTC-USDT"):
            p = Position(
                position_id=f"{tk}-{int(time.time()*1e6)}-{tk}",
                ticker=tk, direction=1, entry_price=100.0, size_usd=100.0,
                open_ts_ms=int(time.time() * 1000),
            )
            ledger.insert_position_open(p, "HYPO-X", "test_strategy")
        btc = ledger.get_open_positions(ticker="BTC-USDT")
        eth = ledger.get_open_positions(ticker="ETH-USDT")
        assert len(btc) == 2
        assert len(eth) == 1


# ─── Balance lifecycle ───────────────────────────────────────────────────────


class TestBalance:
    def test_upsert_balance_inserts(self, ledger):
        bal = PaperBalance(starting_usd=5000.0, cash_usd=4700.0)
        ledger.upsert_balance("HYPO-008-RT", "BTC-USDT", bal)
        summary = ledger.get_balance_summary("HYPO-008-RT", "BTC-USDT")
        assert summary["cash_usd"] == 4700.0
        assert summary["total_n_trades"] == 0

    def test_upsert_balance_updates_existing(self, ledger):
        bal1 = PaperBalance(starting_usd=5000.0, cash_usd=4700.0)
        bal2 = PaperBalance(starting_usd=5000.0, cash_usd=5200.0)
        ledger.upsert_balance("HYPO-X", "BTC-USDT", bal1)
        ledger.upsert_balance("HYPO-X", "BTC-USDT", bal2)
        summary = ledger.get_balance_summary("HYPO-X", "BTC-USDT")
        assert summary["cash_usd"] == 5200.0

    def test_get_paper_balance_reconstructs(self, ledger, sample_position):
        bal = PaperBalance(starting_usd=5000.0, cash_usd=4700.0)
        ledger.upsert_balance("HYPO-Y", "BTC-USDT", bal)
        ledger.insert_position_open(sample_position, "HYPO-Y", "test")
        reconstructed = ledger.get_paper_balance("HYPO-Y", "BTC-USDT")
        assert reconstructed is not None
        assert reconstructed.cash_usd == 4700.0
        assert len(reconstructed.open_positions) == 1


# ─── Aggregations ────────────────────────────────────────────────────────────


class TestAggregations:
    def test_total_realized_usd_zero_initially(self, ledger):
        assert ledger.total_realized_usd() == 0.0

    def test_total_realized_after_close(self, ledger, sample_position):
        ledger.insert_position_open(sample_position, "HYPO-X", "test")
        ledger.update_position_close(
            sample_position.position_id,
            exit_price=80800.0,  # +1%
            close_ts_ms=1_700_000_000_001,
            exit_reason="tp",
        )
        # net = 3.0 - 0.6 = 2.4
        assert abs(ledger.total_realized_usd() - 2.4) < 0.01

    def test_total_open_count(self, ledger, sample_position):
        ledger.insert_position_open(sample_position, "HYPO-X", "test")
        assert ledger.total_open_count() == 1

    def test_portfolio_snapshot_round_trip(self, ledger):
        ledger.insert_portfolio_snapshot(
            ts_ms=1_700_000_000_000,
            total_equity_usd=50000.0,
            total_open=3,
            total_realized=120.5,
            drawdown_pct=0.012,
            active_hypos=["HYPO-008-RT", "HYPO-040"],
        )
        latest = ledger.latest_portfolio_snapshot()
        assert latest is not None
        assert latest["total_equity_usd"] == 50000.0
        assert latest["total_open_count"] == 3


# ─── Transaction safety ──────────────────────────────────────────────────────


class TestTransactionSafety:
    def test_rollback_on_exception(self, ledger, sample_position):
        with pytest.raises(RuntimeError):
            with ledger.transaction():
                ledger.insert_position_open(sample_position, "HYPO-X", "test")
                raise RuntimeError("boom")
        # Position should NOT exist (rollback happened)
        opens = ledger.get_open_positions()
        assert len(opens) == 0

    def test_commit_on_success(self, ledger, sample_position):
        with ledger.transaction():
            ledger.insert_position_open(sample_position, "HYPO-X", "test")
        opens = ledger.get_open_positions()
        assert len(opens) == 1
