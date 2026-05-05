"""TradeLedger — SQLite SSOT for Polaris trade lifecycle (shell P6).

Single-writer model: realtime_runner is the sole writer (asyncio loop),
backtest/cron/daily run in separate processes with their own connection.
WAL mode allows concurrent dashboard reads without blocking writers.

API:
    with TradeLedger(db_path) as ledger:
        ledger.insert_position_open(pos, hypo_id, signal_meta)
        ledger.update_position_close(position_id, exit_data)
        balance = ledger.get_balance(hypo_id, ticker)
        ledger.upsert_balance(hypo_id, ticker, balance)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from src.paper.state import PaperBalance, Position, PositionStatus
from src.persist.schema import ALL_DDL, INDEXES, PRAGMAS, SCHEMA_VERSION

logger = logging.getLogger(__name__)


class TradeLedger:
    """SQLite trade ledger — connection lifecycle + CRUD operations."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "TradeLedger":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        """Open connection + apply pragmas + create schema if missing."""
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        for p in PRAGMAS:
            self._conn.execute(p)
        self._init_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        assert self._conn is not None
        for ddl in ALL_DDL:
            self._conn.execute(ddl)
        for idx in INDEXES:
            self._conn.execute(idx)
        # Stamp schema version (idempotent)
        cur = self._conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Ledger not opened — use `with TradeLedger(...)` or .open()")
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN ... COMMIT (rollback on exception)."""
        c = self.conn
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ── Position lifecycle ──────────────────────────────────────────────────

    def insert_position_open(
        self,
        pos: Position,
        hypo_id: str,
        strategy_name: str,
        signal_meta: Optional[dict] = None,
    ) -> None:
        """Insert a newly opened position. Idempotent on position_id (REPLACE)."""
        meta = signal_meta or {}
        now_ms = int(time.time() * 1000)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO positions (
                position_id, hypo_id, strategy_name, ticker, direction, status,
                entry_price, entry_size_usd, open_ts_ms, entry_slip_bps,
                fee_round_trip,
                signal_confidence, signal_reason, regime,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pos.position_id, hypo_id, strategy_name, pos.ticker, pos.direction, "open",
                pos.entry_price, pos.size_usd, pos.open_ts_ms,
                float(meta.get("entry_slip_bps", 0.0)),
                pos.fee_round_trip,
                meta.get("signal_confidence"),
                meta.get("signal_reason"),
                meta.get("regime"),
                now_ms, now_ms,
            ),
        )

    def update_position_close(
        self,
        position_id: str,
        exit_price: float,
        close_ts_ms: int,
        exit_reason: str,
        exit_slip_bps: float = 0.0,
    ) -> None:
        """Close an existing position (compute gross/fee/net automatically)."""
        now_ms = int(time.time() * 1000)
        # Fetch entry data to compute net
        cur = self.conn.execute(
            "SELECT direction, entry_price, entry_size_usd, fee_round_trip "
            "FROM positions WHERE position_id = ?",
            (position_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        direction = row["direction"]
        entry_price = row["entry_price"]
        size_usd = row["entry_size_usd"]
        fee_rt = row["fee_round_trip"]

        gross_pct = direction * (exit_price - entry_price) / entry_price
        gross_usd = size_usd * gross_pct
        fee_usd = size_usd * fee_rt
        net_usd = gross_usd - fee_usd

        self.conn.execute(
            """
            UPDATE positions SET
                status = 'closed',
                exit_price = ?,
                close_ts_ms = ?,
                exit_slip_bps = ?,
                exit_reason = ?,
                gross_usd = ?,
                fee_usd = ?,
                net_usd = ?,
                updated_at_ms = ?
            WHERE position_id = ?
            """,
            (
                exit_price, close_ts_ms, exit_slip_bps, exit_reason,
                gross_usd, fee_usd, net_usd, now_ms, position_id,
            ),
        )

    def get_open_positions(
        self,
        hypo_id: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> list[Position]:
        """Return all open positions (optionally filtered)."""
        sql = "SELECT * FROM positions WHERE status = 'open'"
        params: list = []
        if hypo_id is not None:
            sql += " AND hypo_id = ?"
            params.append(hypo_id)
        if ticker is not None:
            sql += " AND ticker = ?"
            params.append(ticker)
        sql += " ORDER BY open_ts_ms ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_position(r) for r in rows]

    def get_closed_positions(
        self,
        hypo_id: Optional[str] = None,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Position]:
        """Return recent closed positions ordered by close_ts_ms desc."""
        sql = "SELECT * FROM positions WHERE status = 'closed'"
        params: list = []
        if hypo_id is not None:
            sql += " AND hypo_id = ?"
            params.append(hypo_id)
        if ticker is not None:
            sql += " AND ticker = ?"
            params.append(ticker)
        sql += " ORDER BY close_ts_ms DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_position(r) for r in rows]

    # ── Balance lifecycle ────────────────────────────────────────────────────

    def upsert_balance(
        self,
        hypo_id: str,
        ticker: str,
        balance: PaperBalance,
    ) -> None:
        """Insert or update balance row for (hypo_id, ticker)."""
        now_ms = int(time.time() * 1000)
        n_closed = balance.n_closed
        wins = sum(1 for p in balance.closed_positions if p.net_usd > 0)
        realized = balance.realized_pnl_usd
        self.conn.execute(
            """
            INSERT INTO balances (
                hypo_id, ticker, starting_usd, cash_usd,
                total_realized_usd, total_n_trades, total_wins, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hypo_id, ticker) DO UPDATE SET
                cash_usd = excluded.cash_usd,
                total_realized_usd = excluded.total_realized_usd,
                total_n_trades = excluded.total_n_trades,
                total_wins = excluded.total_wins,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                hypo_id, ticker, balance.starting_usd, balance.cash_usd,
                realized, n_closed, wins, now_ms,
            ),
        )

    def get_balance_summary(self, hypo_id: str, ticker: str) -> Optional[dict]:
        """Return balance summary row (cash, realized, n_trades) — not full PaperBalance.

        Full PaperBalance reconstruction requires positions JOIN; use
        get_paper_balance() for that.
        """
        row = self.conn.execute(
            "SELECT * FROM balances WHERE hypo_id = ? AND ticker = ?",
            (hypo_id, ticker),
        ).fetchone()
        return dict(row) if row else None

    def get_paper_balance(self, hypo_id: str, ticker: str) -> Optional[PaperBalance]:
        """Reconstruct full PaperBalance object from positions + balances."""
        bal_row = self.conn.execute(
            "SELECT starting_usd, cash_usd FROM balances "
            "WHERE hypo_id = ? AND ticker = ?",
            (hypo_id, ticker),
        ).fetchone()
        if bal_row is None:
            return None
        opens = self.get_open_positions(hypo_id, ticker)
        closes = self.get_closed_positions(hypo_id, ticker, limit=10000)
        return PaperBalance(
            starting_usd=bal_row["starting_usd"],
            cash_usd=bal_row["cash_usd"],
            open_positions=tuple(opens),
            closed_positions=tuple(reversed(closes)),  # chronological asc
        )

    # ── Aggregations ─────────────────────────────────────────────────────────

    def total_realized_usd(self, since_ts_ms: int = 0) -> float:
        cur = self.conn.execute(
            "SELECT COALESCE(SUM(net_usd), 0) AS s FROM positions "
            "WHERE status = 'closed' AND close_ts_ms >= ?",
            (since_ts_ms,),
        )
        row = cur.fetchone()
        return float(row["s"] or 0)

    def total_open_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS c FROM positions WHERE status='open'")
        return int(cur.fetchone()["c"])

    def insert_portfolio_snapshot(
        self,
        ts_ms: int,
        total_equity_usd: float,
        total_open: int,
        total_realized: float,
        drawdown_pct: float,
        active_hypos: list[str] | None = None,
    ) -> None:
        import json
        self.conn.execute(
            """
            INSERT OR REPLACE INTO portfolio_snapshots
                (ts_ms, total_equity_usd, total_open_count, total_realized_usd,
                 drawdown_pct, active_hypos)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts_ms, total_equity_usd, total_open, total_realized,
                drawdown_pct, json.dumps(active_hypos or []),
            ),
        )

    def latest_portfolio_snapshot(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def _row_to_position(row: sqlite3.Row) -> Position:
    """sqlite3.Row → Position dataclass."""
    return Position(
        position_id=row["position_id"],
        ticker=row["ticker"],
        direction=row["direction"],
        entry_price=row["entry_price"],
        size_usd=row["entry_size_usd"],
        open_ts_ms=row["open_ts_ms"],
        close_ts_ms=row["close_ts_ms"] or 0,
        exit_price=row["exit_price"] or 0.0,
        fee_round_trip=row["fee_round_trip"],
        status=PositionStatus(row["status"]),
    )
