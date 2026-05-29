"""SQLite schema bootstrap for Polaris (`data/polaris.sqlite`).

Layer 0 + Layer 1 P0 schema. Idempotent: `init_db()` runs `CREATE TABLE IF NOT EXISTS`.

Spec sources:
- vault/30_components/layer-0-universe-discovery.md (Schema)
- vault/30_components/layer-1-canonical-baseline.md (Schema)
- vault/10_decisions/ADR-003-8-layer-architecture.md (Unified SQLite Schema)

DDL string constants live in ``schema_ddl_core`` (Layer 0/1/4/7) and
``schema_ddl_ext`` (Layer 2/3/5/6 + fill ledger); this module assembles them
into ``ALL_DDL`` and owns the connect / init / migration logic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema_ddl_core import (
    DDL_ALLOCATOR_RESERVATIONS,
    DDL_ALLOCATOR_RESERVATIONS_KEY_INDEX,
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_CELL_MATRIX_P0,
    DDL_CELL_MATRIX_PARENT2,
    DDL_CELL_MATRIX_PARENT3,
    DDL_CELL_MATRIX_SHADOW,
    DDL_MARKET_EVENTS,
    DDL_ORDER_INTENTS,
    DDL_ORDER_INTENTS_INDEX,
    DDL_ORDERS,
    DDL_ORDERS_INDEX,
    DDL_POSITIONS,
    DDL_POSITIONS_INDEX,
    DDL_QUOTE_TICKS,
    DDL_RISK_EVENTS,
    DDL_RISK_EVENTS_INDEX,
    DDL_SIGNALS,
    DDL_STRATEGY_FAULT_EVENTS,
    DDL_STRATEGY_FAULT_EVENTS_INDEX,
    DDL_STRATEGY_HALTS,
    DDL_STRATEGY_HALTS_INDEX,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_SAMPLES,
    DDL_TICKER_BASELINE_STATE,
    DDL_UNIVERSE,
    DDL_UNIVERSE_INDEX_ACTIVE,
    DDL_UNIVERSE_INDEX_GROUP,
    DDL_WATCHLIST_FOCUS,
)
from polaris.storage.schema_ddl_ext import (
    DDL_AI_LESSONS,
    DDL_AI_LESSONS_INDEX,
    DDL_FILLS,
    DDL_FILLS_INDEX_ORDER,
    DDL_FILLS_INDEX_TS,
    DDL_FILLS_INDEX_VENUE_SYMBOL,
    DDL_GATE_EVENTS,
    DDL_GATE_EVENTS_INDEX,
    DDL_LEARNER_BLOCKS,
    DDL_LEARNER_BLOCKS_INDEX,
    DDL_LEARNER_POSTERIOR,
    DDL_LEARNER_SNAPSHOT,
    DDL_LEARNER_STATE,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_POSITION_CONVICTION_LAYERS,
    DDL_POSITION_CONVICTION_LAYERS_INDEX,
    DDL_POSITION_LIVE_RECALC_STATE,
    DDL_POSITION_RISK_STATE,
    DDL_POSITION_STRATEGY_SEGMENTS,
    DDL_POSITION_STRATEGY_SEGMENTS_INDEX,
    DDL_REGIME_STATE,
    DDL_ROLLBACK_CANDIDATES,
    DDL_STRATEGY_REGIME_PRIOR,
    DDL_STRATEGY_RISK_STATE,
    DDL_VENUE_BLOCKLIST,
)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path("data/polaris.sqlite")


ALL_DDL: tuple[str, ...] = (
    DDL_UNIVERSE,
    DDL_UNIVERSE_INDEX_ACTIVE,
    DDL_UNIVERSE_INDEX_GROUP,
    DDL_WATCHLIST_FOCUS,
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_QUOTE_TICKS,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_SAMPLES,
    DDL_MARKET_EVENTS,
    DDL_SIGNALS,
    # Layer 4 — Cell Matrix
    DDL_CELL_MATRIX_P0,
    DDL_CELL_MATRIX_PARENT3,
    DDL_CELL_MATRIX_PARENT2,
    DDL_CELL_MATRIX_SHADOW,
    # Layer 7 — Isolation
    DDL_STRATEGY_HALTS,
    DDL_STRATEGY_HALTS_INDEX,
    DDL_STRATEGY_FAULT_EVENTS,
    DDL_STRATEGY_FAULT_EVENTS_INDEX,
    DDL_ALLOCATOR_RESERVATIONS,
    DDL_ALLOCATOR_RESERVATIONS_KEY_INDEX,
    DDL_ORDER_INTENTS,
    DDL_ORDER_INTENTS_INDEX,
    DDL_POSITIONS,
    DDL_POSITIONS_INDEX,
    DDL_ORDERS,
    DDL_ORDERS_INDEX,
    DDL_RISK_EVENTS,
    DDL_RISK_EVENTS_INDEX,
    DDL_VENUE_BLOCKLIST,
    # Layer 2 — Pipeline
    DDL_GATE_EVENTS,
    DDL_GATE_EVENTS_INDEX,
    DDL_AI_LESSONS,
    DDL_AI_LESSONS_INDEX,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_POSITION_STRATEGY_SEGMENTS,
    DDL_POSITION_STRATEGY_SEGMENTS_INDEX,
    # Layer 3 — Sizing risk state
    DDL_STRATEGY_RISK_STATE,
    DDL_POSITION_RISK_STATE,
    # Layer 5 — Learner Network
    DDL_LEARNER_STATE,
    DDL_LEARNER_BLOCKS,
    DDL_LEARNER_BLOCKS_INDEX,
    DDL_LEARNER_SNAPSHOT,
    DDL_ROLLBACK_CANDIDATES,
    # Edge-validation Phase 1 — Bayesian posterior (measure-only, no sizing wire)
    DDL_LEARNER_POSTERIOR,
    DDL_STRATEGY_REGIME_PRIOR,
    # Layer 6 — Live Recalc
    DDL_POSITION_LIVE_RECALC_STATE,
    DDL_REGIME_STATE,
    DDL_POSITION_CONVICTION_LAYERS,
    DDL_POSITION_CONVICTION_LAYERS_INDEX,
    # Layer 1 — Fill ledger (Day 6)
    DDL_FILLS,
    DDL_FILLS_INDEX_TS,
    DDL_FILLS_INDEX_VENUE_SYMBOL,
    DDL_FILLS_INDEX_ORDER,
)


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with foreign keys enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create all tables/indexes if not already present and return a live connection.

    Idempotent ALTER TABLE migrations are applied for columns added after the
    initial CREATE — SQLite does not honour additions inside ``CREATE TABLE IF
    NOT EXISTS`` once the table exists.
    """
    conn = connect(db_path)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    _apply_post_migrations(conn)
    return conn


def _apply_post_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after their parent table existed (idempotent)."""
    # positions.swap_count — added Day 4 (Layer 6 strategy swap).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "swap_count" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN swap_count INTEGER NOT NULL DEFAULT 0"
        )
    # positions.underlying_group_id — added Day 4 R2 (Layer 6 regime SSOT key
    # is (venue, underlying_group_id); without this column swap segments lose
    # attribution context).
    if "underlying_group_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "underlying_group_id TEXT NOT NULL DEFAULT ''"
        )
    # positions.entry_strategy_id + active_strategy_id — added Day 4
    # (Layer 6 §Q3 strategy-swap invariants). Pre-Day-4 DBs that
    # bootstrapped the table prior to these additions need an idempotent
    # ALTER so the swap apply path does not fail with no-such-column.
    if "entry_strategy_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "entry_strategy_id TEXT NOT NULL DEFAULT ''"
        )
    if "active_strategy_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN "
            "active_strategy_id TEXT NOT NULL DEFAULT ''"
        )
    # Backfill runs unconditionally — partially-migrated DBs that already
    # had the columns but kept the legacy ``''`` defaults must also be
    # repaired (codex Day 4 R5 fix). Layer 6 §Q3 invariant requires both
    # ``entry_strategy_id`` and ``active_strategy_id`` to be populated for
    # any open position. Idempotent.
    conn.execute(
        "UPDATE positions SET entry_strategy_id = strategy_id "
        "WHERE entry_strategy_id = '' AND strategy_id != ''"
    )
    conn.execute(
        "UPDATE positions SET active_strategy_id = strategy_id "
        "WHERE active_strategy_id = '' AND strategy_id != ''"
    )
    # position_strategy_segments.regime_at_start + attribution_weight —
    # added Day 4 R2 (Layer 6 §Q3 attribution + segment-per-regime spec).
    seg_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(position_strategy_segments)"
        ).fetchall()
    }
    if seg_cols and "regime_at_start" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN regime_at_start TEXT NOT NULL DEFAULT ''"
        )
    if seg_cols and "attribution_weight" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN attribution_weight REAL NOT NULL DEFAULT 0.0"
        )
    # universe / positions: product_class + stream_id — added 3-stream
    # architecture P0-1 (stream_architecture_redesign §2.3). ADDITIVE only:
    # all rows backfilled to '' default; venue→product_class/stream_id mapping
    # repaired below. SQLite has no ``ADD COLUMN IF NOT EXISTS`` so a
    # pragma table_info guard makes the ALTER idempotent.
    uni_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(universe)").fetchall()
    }
    if uni_cols and "product_class" not in uni_cols:
        conn.execute(
            "ALTER TABLE universe ADD COLUMN product_class TEXT NOT NULL DEFAULT ''"
        )
    if uni_cols and "stream_id" not in uni_cols:
        conn.execute(
            "ALTER TABLE universe ADD COLUMN stream_id TEXT NOT NULL DEFAULT ''"
        )
    if "product_class" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN product_class TEXT NOT NULL DEFAULT ''"
        )
    if "stream_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN stream_id TEXT NOT NULL DEFAULT ''"
        )
    # positions.signal_id — AI-effect instrumentation (BUILD_SCHEMA): the
    # originating signal_id is the join key that ties a PASSED signal_id
    # (gate_events) to its resulting position/outcome. position_id truncates
    # signal_id to 16 chars so it is not a reliable reverse-join. ADDITIVE
    # only: backfilled to '' for existing rows. Pragma guard = idempotent.
    if "signal_id" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN signal_id TEXT NOT NULL DEFAULT ''"
        )
    # positions excursion / precise-exit columns (BUILD_SCHEMA, #26 precise
    # exits prerequisite). ADDITIVE only — every column is nullable DEFAULT NULL
    # (exit_state DEFAULT 'open') so existing reads are unaffected and legacy
    # rows backfill to NULL/'open'. These persist the position's tracked
    # stop / price extremes (peak/trough) and the close-time MFE/MAE in R units;
    # measurement only — never gates sizing or blocks entry. Pragma guard makes
    # each ALTER idempotent (SQLite has no ADD COLUMN IF NOT EXISTS). exit_state
    # carries a non-constant-incompatible default ('open') so the ALTER is legal.
    if "stop_price" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN stop_price REAL")
    if "peak_price" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN peak_price REAL")
    if "trough_price" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN trough_price REAL")
    if "mfe_r" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN mfe_r REAL")
    if "mae_r" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN mae_r REAL")
    if "exit_state" not in cols:
        # No column DEFAULT on the ALTER: SQLite backfills EVERY existing row
        # with an ALTER default (open + closed alike), which would stamp a
        # meaningless 'open' onto already-closed legacy positions. Adding it
        # without a default leaves legacy rows NULL, then the targeted backfill
        # below sets ONLY open rows to 'open'. The fresh-DB DDL still carries
        # ``DEFAULT 'open'`` so new inserts get the lifecycle marker.
        conn.execute("ALTER TABLE positions ADD COLUMN exit_state TEXT")
    # Backfill legacy open positions left at NULL exit_state to 'open' so the
    # tick loop / precise-exit FSM reads a consistent lifecycle marker. Only
    # touches still-NULL rows (idempotent). Closed rows keep NULL → they are
    # not driven by the exit FSM.
    conn.execute(
        "UPDATE positions SET exit_state = 'open' "
        "WHERE exit_state IS NULL AND status = 'open'"
    )
    # Backfill venue→product_class/stream_id for legacy rows left at ''.
    # okx→spot/A_okx_crypto, capital→cfd/B_capital_cfd. Runs unconditionally
    # (idempotent: WHERE clause only touches still-blank rows).
    for table in ("universe", "positions"):
        conn.execute(
            f"UPDATE {table} SET product_class = 'spot' "
            f"WHERE product_class = '' AND venue = 'okx'"
        )
        conn.execute(
            f"UPDATE {table} SET product_class = 'cfd' "
            f"WHERE product_class = '' AND venue = 'capital'"
        )
        conn.execute(
            f"UPDATE {table} SET stream_id = 'A_okx_crypto' "
            f"WHERE stream_id = '' AND venue = 'okx'"
        )
        conn.execute(
            f"UPDATE {table} SET stream_id = 'B_capital_cfd' "
            f"WHERE stream_id = '' AND venue = 'capital'"
        )
    # learner_posterior.running_mean — edge-validation P0-1 fix: persist the
    # Welford running mean so an existing cell folds the next observation into
    # its own accumulated NIG state without re-mixing the parent prior. DBs
    # that bootstrapped the table before this column need an idempotent ALTER.
    post_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(learner_posterior)").fetchall()
    }
    if post_cols and "running_mean" not in post_cols:
        conn.execute(
            "ALTER TABLE learner_posterior "
            "ADD COLUMN running_mean REAL NOT NULL DEFAULT 0.0"
        )
    # gate_events.input_tokens + output_tokens — AI-effect instrumentation
    # (BUILD_SCHEMA): GPTCallResult captures per-call token usage but the
    # gate_events INSERT dropped it. ADDITIVE: backfilled to 0 for existing
    # rows. Pragma guard = idempotent.
    ge_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(gate_events)").fetchall()
    }
    if ge_cols and "input_tokens" not in ge_cols:
        conn.execute(
            "ALTER TABLE gate_events ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if ge_cols and "output_tokens" not in ge_cols:
        conn.execute(
            "ALTER TABLE gate_events ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0"
        )
