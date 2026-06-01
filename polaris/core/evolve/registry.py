"""P0a honest-N variant registry — persistent OFFLINE trial store.

OFFLINE ONLY. This module persists one row per ``(variant × cell × split)``
replay outcome to a SEPARATE ``data/p0a_registry.sqlite`` (NEW db). It touches
NO live trading / sizing / T4 chain / gate thresholds and NEVER writes to
``data/polaris_live.sqlite`` (which stays a read-only bar source). The registry
is a pure read-model: the live loop never consults it, so persistence here is
behavior-0 by construction.

Each row is the gate verdict for one variant on one cell partition
(``exchange|strategy|ticker|regime``) under one replay split. ``oos_pass`` is the
honest verdict (gate.passed on the HELD-OUT slice); ``n_trades`` is the OOS trade
count (thin-N stays visible); ``trials_searched`` is the grid breadth behind the
winner (multiple-testing honesty, feeds the DSR search-breadth reader).

DDL is additive + idempotent (``CREATE TABLE IF NOT EXISTS``), and follows the
project connect convention (WAL, ``isolation_level=None``, ``busy_timeout``) so
the file is single-writer safe across reconnects.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CellAggregate",
    "PassRateAggregate",
    "TrialRow",
    "aggregate_pass_rate",
    "cumulative_trials_per_cell",
    "open_registry",
    "record_trial",
]

# ---------------------------------------------------------------------------
# DDL (design.registry_ddl) — written ONLY to the NEW p0a_registry.sqlite.
# ---------------------------------------------------------------------------

DDL_VARIANT_TRIALS = """
CREATE TABLE IF NOT EXISTS variant_trials (
    -- cell partition (mirrors CellKeyP0: exchange/strategy/ticker/regime)
    cell_key        TEXT    NOT NULL,   -- canonical "exchange|strategy|ticker|regime"
    variant_run_id  TEXT    NOT NULL,   -- uuid4 hex per (variant, split) replay run
    exchange        TEXT    NOT NULL,   -- venue: okx|capital|alpaca
    strategy        TEXT    NOT NULL,   -- strategy_id
    ticker          TEXT    NOT NULL,   -- symbol
    regime          TEXT    NOT NULL,   -- entry-bar confirmed regime label
    variant_id      TEXT    NOT NULL,   -- stable hash of param_json (dedup across runs)
    param_json      TEXT    NOT NULL,   -- {"VOL_Z_THRESHOLD":2.0,...} the varied knobs only
    is_pass         INTEGER NOT NULL DEFAULT 0,  -- gate.passed on the IS slice (0/1)
    oos_pass        INTEGER NOT NULL DEFAULT 0,  -- gate.passed on the held-out OOS slice (0/1)
    n_trades        INTEGER NOT NULL DEFAULT 0,  -- OOS trade count (honest N; thin-N visible)
    pnl_r_mean      REAL    NOT NULL DEFAULT 0.0, -- OOS per-trade net-R mean
    lcb             REAL    NOT NULL DEFAULT 0.0, -- NIG LCB on net-R mean
    ucb             REAL    NOT NULL DEFAULT 0.0, -- NIG UCB
    dsr             REAL    NOT NULL DEFAULT 0.0, -- deflated Sharpe
    trials_searched INTEGER NOT NULL DEFAULT 1,  -- grid size searched producing this winner
    is_oos_spread   REAL    NOT NULL DEFAULT 0.0, -- overfit flag (IS Sharpe - OOS Sharpe)
    created_ts      INTEGER NOT NULL,            -- unix seconds
    PRIMARY KEY (cell_key, variant_run_id)
)
"""

DDL_VARIANT_TRIALS_OOS_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_variant_trials_oos ON variant_trials (strategy, oos_pass, lcb)"
)

DDL_VARIANT_TRIALS_CELL_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_variant_trials_cell ON variant_trials (cell_key, oos_pass)"
)

_ALL_DDL: tuple[str, ...] = (
    DDL_VARIANT_TRIALS,
    DDL_VARIANT_TRIALS_OOS_INDEX,
    DDL_VARIANT_TRIALS_CELL_INDEX,
)


# ---------------------------------------------------------------------------
# Row + aggregate types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrialRow:
    """One variant × cell × split gate outcome (mirrors variant_trials columns)."""

    cell_key: str
    variant_run_id: str
    exchange: str
    strategy: str
    ticker: str
    regime: str
    variant_id: str
    param_json: str
    is_pass: int
    oos_pass: int
    n_trades: int
    pnl_r_mean: float
    lcb: float
    ucb: float
    dsr: float
    trials_searched: int
    is_oos_spread: float
    created_ts: int


@dataclass(frozen=True, slots=True)
class CellAggregate:
    """Pass-rate roll-up for one grouping key (a cell_key, strategy, or 'ALL')."""

    key: str
    n_trials: int
    is_pass: int
    oos_pass: int
    trials_searched: int

    @property
    def is_pass_rate(self) -> float:
        return self.is_pass / self.n_trials if self.n_trials else 0.0

    @property
    def oos_pass_rate(self) -> float:
        return self.oos_pass / self.n_trials if self.n_trials else 0.0


@dataclass(frozen=True, slots=True)
class PassRateAggregate:
    """Per-cell + per-strategy + overall IS/OOS pass-rate roll-up."""

    overall: CellAggregate
    per_cell: tuple[CellAggregate, ...]
    per_strategy: tuple[CellAggregate, ...]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def open_registry(path: str | Path) -> sqlite3.Connection:
    """Open (creating if absent) the p0a_registry DB and ensure the schema.

    Follows the project connect convention: WAL journal, ``isolation_level=None``
    (autocommit; ``record_trial`` is a single atomic statement), ``busy_timeout``
    for single-writer safety. The schema DDL is idempotent so reopening an
    existing file is a no-op. NEVER pass a live/production DB path here.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    for stmt in _ALL_DDL:
        conn.execute(stmt)
    return conn


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

_INSERT_TRIAL = """
INSERT INTO variant_trials (
    cell_key, variant_run_id, exchange, strategy, ticker, regime,
    variant_id, param_json, is_pass, oos_pass, n_trades, pnl_r_mean,
    lcb, ucb, dsr, trials_searched, is_oos_spread, created_ts
) VALUES (
    :cell_key, :variant_run_id, :exchange, :strategy, :ticker, :regime,
    :variant_id, :param_json, :is_pass, :oos_pass, :n_trades, :pnl_r_mean,
    :lcb, :ucb, :dsr, :trials_searched, :is_oos_spread, :created_ts
)
ON CONFLICT (cell_key, variant_run_id) DO UPDATE SET
    exchange        = excluded.exchange,
    strategy        = excluded.strategy,
    ticker          = excluded.ticker,
    regime          = excluded.regime,
    variant_id      = excluded.variant_id,
    param_json      = excluded.param_json,
    is_pass         = excluded.is_pass,
    oos_pass        = excluded.oos_pass,
    n_trades        = excluded.n_trades,
    pnl_r_mean      = excluded.pnl_r_mean,
    lcb             = excluded.lcb,
    ucb             = excluded.ucb,
    dsr             = excluded.dsr,
    trials_searched = excluded.trials_searched,
    is_oos_spread   = excluded.is_oos_spread,
    created_ts      = excluded.created_ts
"""


def record_trial(conn: sqlite3.Connection, row: TrialRow) -> None:
    """Append (or upsert) one variant × cell × split trial outcome.

    The PK ``(cell_key, variant_run_id)`` makes a re-recorded run idempotent: the
    same ``variant_run_id`` overwrites in place (no duplicate row), while distinct
    runs accumulate — so ``cumulative_trials_per_cell`` grows across separate
    replay sessions. Single atomic statement under autocommit.
    """
    conn.execute(
        _INSERT_TRIAL,
        {
            "cell_key": row.cell_key,
            "variant_run_id": row.variant_run_id,
            "exchange": row.exchange,
            "strategy": row.strategy,
            "ticker": row.ticker,
            "regime": row.regime,
            "variant_id": row.variant_id,
            "param_json": row.param_json,
            "is_pass": int(row.is_pass),
            "oos_pass": int(row.oos_pass),
            "n_trades": int(row.n_trades),
            "pnl_r_mean": float(row.pnl_r_mean),
            "lcb": float(row.lcb),
            "ucb": float(row.ucb),
            "dsr": float(row.dsr),
            "trials_searched": int(row.trials_searched),
            "is_oos_spread": float(row.is_oos_spread),
            "created_ts": int(row.created_ts),
        },
    )


# ---------------------------------------------------------------------------
# Read / aggregate
# ---------------------------------------------------------------------------


def _group_aggregate(
    conn: sqlite3.Connection, group_col: str, run_id: str | None
) -> tuple[CellAggregate, ...]:
    where = "" if run_id is None else " WHERE variant_run_id = ?"
    params: tuple[str, ...] = () if run_id is None else (run_id,)
    sql = (
        f"SELECT {group_col} AS k, COUNT(*) AS n, "
        "COALESCE(SUM(is_pass), 0) AS isp, COALESCE(SUM(oos_pass), 0) AS oosp, "
        "COALESCE(SUM(trials_searched), 0) AS ts "
        f"FROM variant_trials{where} GROUP BY {group_col} ORDER BY {group_col}"
    )
    out: list[CellAggregate] = []
    for k, n, isp, oosp, ts in conn.execute(sql, params).fetchall():
        out.append(
            CellAggregate(
                key=str(k),
                n_trials=int(n),
                is_pass=int(isp),
                oos_pass=int(oosp),
                trials_searched=int(ts),
            )
        )
    return tuple(out)


def aggregate_pass_rate(conn: sqlite3.Connection, run_id: str | None = None) -> PassRateAggregate:
    """Per-cell / per-strategy / overall IS & OOS pass-rate + trials_searched.

    When ``run_id`` is given, the roll-up is scoped to that ``variant_run_id``;
    otherwise it spans every persisted trial (cumulative across runs). Pass-rates
    are ``passes / n_trials`` (0.0 when empty). ``trials_searched`` SUMS the grid
    breadth so the honest-N reader sees the search behind each pass count.
    """
    per_cell = _group_aggregate(conn, "cell_key", run_id)
    per_strategy = _group_aggregate(conn, "strategy", run_id)

    where = "" if run_id is None else " WHERE variant_run_id = ?"
    params: tuple[str, ...] = () if run_id is None else (run_id,)
    n, isp, oosp, ts = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(is_pass), 0), "
        "COALESCE(SUM(oos_pass), 0), COALESCE(SUM(trials_searched), 0) "
        f"FROM variant_trials{where}",
        params,
    ).fetchone()
    overall = CellAggregate(
        key="ALL",
        n_trials=int(n),
        is_pass=int(isp),
        oos_pass=int(oosp),
        trials_searched=int(ts),
    )
    return PassRateAggregate(overall=overall, per_cell=per_cell, per_strategy=per_strategy)


def cumulative_trials_per_cell(conn: sqlite3.Connection) -> dict[str, int]:
    """Cumulative DISTINCT trial count per cell_key (feeds DSR search-breadth).

    Counts persisted rows (one per ``variant_run_id``) per cell across ALL runs,
    so two separate replay sessions writing to the same cell sum together. This
    is the search breadth the deflated-Sharpe reader uses to deflate by the
    number of trials actually attempted in each cell.
    """
    return {
        str(cell_key): int(cnt)
        for cell_key, cnt in conn.execute(
            "SELECT cell_key, COUNT(*) FROM variant_trials GROUP BY cell_key"
        ).fetchall()
    }
