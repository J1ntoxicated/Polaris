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

from polaris.storage.schema_ddl_altdata import (
    DDL_ALTDATA_SNAPSHOT,
    DDL_ALTDATA_SNAPSHOT_INDEX,
    DDL_TICKER_GROUND,
    DDL_TICKER_GROUND_INDEX,
)
from polaris.storage.schema_ddl_core import (
    DDL_ALLOCATOR_RESERVATIONS,
    DDL_ALLOCATOR_RESERVATIONS_KEY_INDEX,
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_BARS_INSTRUMENT_TS_INDEX,
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
    DDL_TICK_INFLOW,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_SAMPLES,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_TECHNICALS,
    DDL_UNIVERSE,
    DDL_UNIVERSE_INDEX_ACTIVE,
    DDL_UNIVERSE_INDEX_GROUP,
    DDL_WATCHLIST_FOCUS,
)
from polaris.storage.schema_ddl_ext import (
    DDL_AI_LESSONS,
    DDL_AI_LESSONS_INDEX,
    DDL_BENCHMARK_RESULTS,
    DDL_BENCHMARK_RESULTS_INDEX,
    DDL_ENTRY_ADMISSION_SHADOW,
    DDL_ENTRY_ADMISSION_SHADOW_INDEX,
    DDL_FILLS,
    DDL_FILLS_INDEX_ORDER,
    DDL_FILLS_INDEX_TS,
    DDL_FILLS_INDEX_VENUE_SYMBOL,
    DDL_GATE_EVENTS,
    DDL_GATE_EVENTS_DASH_INDEX,
    DDL_GATE_EVENTS_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS,
    DDL_GATE_KILL_COUNTERFACTUALS_GATE_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS_PENDING_INDEX,
    DDL_GATE_SHADOW_EVENTS,
    DDL_GATE_SHADOW_EVENTS_INDEX,
    DDL_LEARNER_BLOCKS,
    DDL_LEARNER_BLOCKS_INDEX,
    DDL_LEARNER_POSTERIOR,
    DDL_LEARNER_SNAPSHOT,
    DDL_LEARNER_STATE,
    DDL_LOOP_ROTATION_EVENTS,
    DDL_LOOP_ROTATION_EVENTS_INDEX,
    DDL_LOOP_SESSION_EXIT_EVENTS,
    DDL_LOOP_SESSION_EXIT_EVENTS_INDEX,
    DDL_MAKER_FILL_SHADOW,
    DDL_MAKER_FILL_SHADOW_INDEX,
    DDL_MEASUREMENT_RESETS,
    DDL_MEASUREMENT_RESETS_INDEX,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_POSITION_CONVICTION_LAYERS,
    DDL_POSITION_CONVICTION_LAYERS_INDEX,
    DDL_POSITION_LIVE_RECALC_STATE,
    DDL_POSITION_RISK_STATE,
    DDL_POSITION_STRATEGY_SEGMENTS,
    DDL_POSITION_STRATEGY_SEGMENTS_CELL_INDEX,
    DDL_POSITION_STRATEGY_SEGMENTS_INDEX,
    DDL_REENTRY_ANCHOR,
    DDL_REGIME_STATE,
    DDL_REPLAY_RUNS,
    DDL_REPLAY_RUNS_INDEX,
    DDL_STRATEGY_REGIME_PRIOR,
    DDL_STRATEGY_RISK_STATE,
    DDL_V_G34_COHORT_OUTCOMES,
    DDL_VENUE_BLOCKLIST,
    DDL_WEEKEND_SHADOW_ORDERS,
    DDL_WEEKEND_SHADOW_ORDERS_INDEX,
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
    DDL_BARS_INSTRUMENT_TS_INDEX,
    DDL_QUOTE_TICKS,
    DDL_TICK_INFLOW,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_SAMPLES,
    # ④ #12 technical store — write-after-compute LWW indicator persistence.
    DDL_TICKER_TECHNICALS,
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
    # Reject-anchor anti-churn (audit1 P0-4 ①) — persistent cooldown anchor that
    # survives a venue reject/clamp (no positions row) and a process restart.
    DDL_REENTRY_ANCHOR,
    # Layer 2 — Pipeline
    DDL_GATE_EVENTS,
    DDL_GATE_EVENTS_INDEX,
    # DDL_GATE_EVENTS_DASH_INDEX is created in _apply_post_migrations (after the
    # input_tokens/output_tokens ALTERs it depends on), not here.
    # AI-conductor P0 SHADOW — technical-vs-GPT shadow decision log
    DDL_GATE_SHADOW_EVENTS,
    DDL_GATE_SHADOW_EVENTS_INDEX,
    # Gate→outcome instrumentation — G3/G4 KILL/PASS cohort counterfactuals.
    # The cohort VIEW (DDL_V_G34_COHORT_OUTCOMES) is created in
    # _apply_post_migrations (after the positions.pnl_r ALTER it reads), not here.
    DDL_GATE_KILL_COUNTERFACTUALS,
    DDL_GATE_KILL_COUNTERFACTUALS_PENDING_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS_GATE_INDEX,
    # Component C (SHADOW) — edge-first entry admission shadow log
    DDL_ENTRY_ADMISSION_SHADOW,
    DDL_ENTRY_ADMISSION_SHADOW_INDEX,
    # Real-fee maker-fill shadow (#77) — entry-BASIS + real-maker net (the only
    # place the weekend maker edge is visible; OKX demo's flat 70 bps hides it).
    DDL_MAKER_FILL_SHADOW,
    DDL_MAKER_FILL_SHADOW_INDEX,
    # Shadow-first would-be orders (#94) — SUPPRESSED order on a shadow_first
    # strategy (the two weekend OKX makers); the signal flowed + the would-be P&L
    # is logged (zero capital at risk; durability of the thin sample accrues live).
    DDL_WEEKEND_SHADOW_ORDERS,
    DDL_WEEKEND_SHADOW_ORDERS_INDEX,
    DDL_AI_LESSONS,
    DDL_AI_LESSONS_INDEX,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_POSITION_STRATEGY_SEGMENTS,
    DDL_POSITION_STRATEGY_SEGMENTS_INDEX,
    # DDL_POSITION_STRATEGY_SEGMENTS_CELL_INDEX is created in _apply_post_migrations
    # (after the cell_key ALTER) — it spans cell_key, which legacy DBs only add
    # post-ALL_DDL; running it here would crash startup on existing DBs.
    # Layer 3 — Sizing risk state
    DDL_STRATEGY_RISK_STATE,
    DDL_POSITION_RISK_STATE,
    # Layer 5 — Learner Network
    DDL_LEARNER_STATE,
    DDL_LEARNER_BLOCKS,
    DDL_LEARNER_BLOCKS_INDEX,
    DDL_LEARNER_SNAPSHOT,
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
    # Layer 6 — alt-data EVIDENCE snapshot (#6)
    DDL_ALTDATA_SNAPSHOT,
    DDL_ALTDATA_SNAPSHOT_INDEX,
    # STEP① static-ground — per-active-ticker sentiment/event ground (②후보 input)
    DDL_TICKER_GROUND,
    DDL_TICKER_GROUND_INDEX,
    # Dashboard telemetry — rotation + session-forced-exit (display-only)
    DDL_LOOP_ROTATION_EVENTS,
    DDL_LOOP_ROTATION_EVENTS_INDEX,
    DDL_LOOP_SESSION_EXIT_EVENTS,
    DDL_LOOP_SESSION_EXIT_EVENTS_INDEX,
    # P1 replay / benchmark READ-MODEL (display-only; never read by trading)
    DDL_REPLAY_RUNS,
    DDL_REPLAY_RUNS_INDEX,
    DDL_BENCHMARK_RESULTS,
    DDL_BENCHMARK_RESULTS_INDEX,
    # Measurement-reset baseline (display-only; forward edge window key)
    DDL_MEASUREMENT_RESETS,
    DDL_MEASUREMENT_RESETS_INDEX,
)


# -wal autocheckpoint page threshold. SQLite checks this at the end of each write
# txn and folds the -wal back into the main DB when it crosses the bound, so the
# -wal cannot grow without limit between the loop's PASSIVE checkpoints (live: the
# -wal had ballooned to 1.4 GB). 1000 pages × the default 4 KB page = ~4 MB — tight
# but never 0 (0 DISABLES autocheckpoint, the unbounded-growth state). PASSIVE
# semantics: it never blocks on a reader and never takes the exclusive lock, so it
# does NOT contend the 1 Hz writer ([[feedback_db_lock_is_architecture_signal]]).
WAL_AUTOCHECKPOINT_PAGES: int = 1000


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with foreign keys enabled."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT_PAGES};")
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
    # position_strategy_segments lineage columns (P3 self-evolve read-model) —
    # ticker↔strategy↔exit lineage. ADDITIVE only: trade_id / venue / ticker /
    # cell_key default '' and pnl_usd defaults 0.0 so legacy swap-seeded rows
    # backfill cleanly and existing reads are unaffected. Live trading NEVER
    # reads this table; recording is INSERT/UPDATE only (behaviour 0). Pragma
    # guard makes each ALTER idempotent (SQLite has no ADD COLUMN IF NOT EXISTS).
    if seg_cols and "trade_id" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN trade_id TEXT NOT NULL DEFAULT ''"
        )
    if seg_cols and "venue" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN venue TEXT NOT NULL DEFAULT ''"
        )
    if seg_cols and "ticker" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN ticker TEXT NOT NULL DEFAULT ''"
        )
    if seg_cols and "pnl_usd" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN pnl_usd REAL NOT NULL DEFAULT 0.0"
        )
    if seg_cols and "cell_key" not in seg_cols:
        conn.execute(
            "ALTER TABLE position_strategy_segments "
            "ADD COLUMN cell_key TEXT NOT NULL DEFAULT ''"
        )
    # cell_key index created HERE (not in ALL_DDL) — legacy DBs add cell_key only
    # via the ALTER above (post-ALL_DDL), so the index must run after it. IF NOT
    # EXISTS keeps it idempotent for fresh DBs that already have the column.
    conn.execute(DDL_POSITION_STRATEGY_SEGMENTS_CELL_INDEX)
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
    # universe.last_price — Alpaca last close for the min_price eligibility floor
    # (B2 floor-persistence). Without the column read_active_universe left it 0.0,
    # so the min_price floor was inert on the DB-read path compute_dynamic_focus
    # consumes — pennies re-entered focus after persistence. ADDITIVE only:
    # backfilled to 0.0 (== unknown → min_price axis skipped, flow_not_block).
    if uni_cols and "last_price" not in uni_cols:
        conn.execute(
            "ALTER TABLE universe ADD COLUMN last_price REAL NOT NULL DEFAULT 0.0"
        )
    # universe.name — human-readable instrument name for the dashboard (display
    # only). Captured at discovery from the venue meta when present (Alpaca
    # /v2/assets ``name``, Capital market ``instrumentName``; OKX has none → '').
    # ADDITIVE only: backfilled to '' for legacy rows (graceful — UI falls back to
    # the symbol). Never read by sizing/gating/exit. Pragma guard = idempotent.
    if uni_cols and "name" not in uni_cols:
        conn.execute(
            "ALTER TABLE universe ADD COLUMN name TEXT NOT NULL DEFAULT ''"
        )
    # watchlist_focus.opportunity_score / trade_eligible — Increment 1 EntranceJudge
    # persistence (entrance-judge build 2026-06-24). ADDITIVE only: the score is
    # nullable (legacy/un-judged rows = NULL) and ``trade_eligible`` DEFAULT 1 keeps
    # every pre-existing row trade-eligible (flow-preserving — no row is retro-
    # demoted out of the trade set). Pragma guard = idempotent (no ADD COLUMN IF
    # NOT EXISTS in SQLite). Neither column feeds sizing (9-stack untouched).
    wf_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(watchlist_focus)").fetchall()
    }
    if wf_cols and "opportunity_score" not in wf_cols:
        conn.execute("ALTER TABLE watchlist_focus ADD COLUMN opportunity_score REAL")
    if wf_cols and "trade_eligible" not in wf_cols:
        conn.execute(
            "ALTER TABLE watchlist_focus "
            "ADD COLUMN trade_eligible INTEGER NOT NULL DEFAULT 1"
        )
    # watchlist_focus.tier — STAGE 1 rank-attention gradient (2026-06-24). ADDITIVE
    # only: DEFAULT 'T' keeps every pre-existing row at the tail cadence band (no
    # row retro-promoted). The {S,A,B,T} band governs poll cadence, not membership
    # (flow_not_block); not a sizing input (9-stack untouched).
    if wf_cols and "tier" not in wf_cols:
        conn.execute(
            "ALTER TABLE watchlist_focus ADD COLUMN tier TEXT NOT NULL DEFAULT 'T'"
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
    # positions.deal_id — Capital (CFD) close key SSOT. The close path routes
    # Capital closes by deal_id (captured at open → affectedDeals[0].dealId).
    # Pre-this-column DBs rebuilt state.open_trades on restart WITHOUT deal_id
    # (it only lived on the in-memory trade + the entry fill's order_id stash),
    # so a restarted bot could never close a live Capital position. ADDITIVE:
    # nullable TEXT DEFAULT NULL — OKX closes by base_qty and leaves it NULL.
    # Pragma guard = idempotent (SQLite has no ADD COLUMN IF NOT EXISTS).
    if "deal_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN deal_id TEXT")
    # positions.entry_atr_pct + entry_atr_timeframe — entry-time ATR anchor
    # (timeframe-aligned exit ruler fix). The R-unit denominator (pnl_r /
    # mfe_r / mae_r) is anchored at entry so a volatility contraction can no
    # longer shrink the denominator mid-life and inflate excursions 4-8x.
    # ``entry_atr_timeframe`` records WHICH timeframe produced the anchor
    # (provenance + legacy/new discriminator + correction-script idempotency
    # key). ADDITIVE: nullable, NULL = legacy row (graceful current-ATR
    # fallback). Pragma guard = idempotent.
    if "entry_atr_pct" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN entry_atr_pct REAL")
    if "entry_atr_timeframe" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN entry_atr_timeframe TEXT")
    # positions.pnl_r — gate→outcome instrumentation (BUILD): the final-close
    # realised R stamped by ``_close_trade_with_real_pnl`` so the PASS cohort
    # (gate_events.position_id → positions) reads its outcome in ONE join.
    # NULL = still open / reconciled-zombie / partial-only / legacy row.
    # MEASUREMENT ONLY — never read by sizing/gating. Pragma guard = idempotent.
    if "pnl_r" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN pnl_r REAL")
    # positions.risk_usd — the trade's intended 1R in dollars, stamped at entry
    # (Step M, 2026-06-22). risk_usd = entry_price * clamp(entry_atr_pct) *
    # STOP_ATR_MULT * base_qty. Realised R is then the dollar truth rescaled by
    # this unit (pnl_usd / risk_usd) so the R ledger and the fills.pnl_usd dollar
    # ledger AGREE (same sign, same bleeders) and the SAME trade shows the SAME R
    # on every panel. NULL = legacy row (the close path re-derives risk_usd from
    # the persisted entry_atr_pct anchor; consistent fallback, never a flat $10/
    # $50 proxy). MEASUREMENT ONLY — never read by sizing/gating. Idempotent.
    if "risk_usd" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN risk_usd REAL")
    # positions.entry_regime — the regime stamped at OPEN (fetch_regime at fill),
    # the entry-thesis anchor the adaptive thesis re-map ([[adaptive_thesis_remap_
    # 2026-06-23]]) compares the LIVE regime against to detect a flip-against-the-
    # position. ADDITIVE: nullable TEXT, NULL = legacy/unstamped row (the re-map
    # degrades safe — a missing entry_regime never invalidates the position).
    # MEASUREMENT / EXIT-TIMING only — never read by sizing/gating. Idempotent.
    if "entry_regime" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN entry_regime TEXT")
    # positions.exit_cadence — hardening #7 (2026-06-23): the exit PASS that fired
    # this close — 'bar' (the ~5s bar recalc) or 'tick' (the sub-second tick exit
    # pass). MEASUREMENT-FIRST: lets the since-reset rollup split close_reason ×
    # cadence so the bar-vs-tick thesis-cut asymmetry ([[structure_hardening_
    # 2026-06-23]]) is measured before any streak threading. ADDITIVE: nullable
    # TEXT, NULL = legacy/un-stamped row. MEASUREMENT ONLY — never read by
    # sizing/gating/exit-timing. Pragma guard = idempotent.
    if "exit_cadence" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN exit_cadence TEXT")
    # positions.stop_atr_mult — [P1-8] observability: the resolved R-unit ATR
    # multiplier (``_stop_atr_mult_for_strategy``) THIS position's exit ruler was
    # bound to, stamped at first precise-exit tick. Lets a floor-bound / wide-ruler
    # position be read directly off the row instead of re-derived — the exact gap
    # ([[trade_mess_full_audit_2026-07-02]]) that let the BEP-arm/trail desync go
    # unnoticed. ADDITIVE: nullable REAL, NULL = legacy/un-stamped row. MEASUREMENT
    # ONLY — never read by sizing/gating/exit-timing. Pragma guard = idempotent.
    if "stop_atr_mult" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN stop_atr_mult REAL")
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
    # Dashboard covering index — created HERE (not in ALL_DDL) because it spans
    # input_tokens/output_tokens, which the ALTERs above add to legacy DBs only
    # after ALL_DDL has run. Fresh DBs already have the columns; IF NOT EXISTS
    # makes this idempotent for both paths.
    conn.execute(DDL_GATE_EVENTS_DASH_INDEX)
    # Cohort view — created HERE (not in ALL_DDL, idx_gate_events_dash
    # precedent) because its SELECT reads positions.pnl_r, which legacy DBs
    # only gain via the ALTER above. IF NOT EXISTS = idempotent.
    conn.execute(DDL_V_G34_COHORT_OUTCOMES)
    # regime_state.last_advanced_bar_id — bar-close confirm dedup (flip-flop
    # fix). The 2-consecutive-close confirm gate advanced once per TICK call;
    # this column records the closed 5m bar that last advanced the count so a
    # candidate advances AT MOST ONCE per bar (24h had 1233 tick-driven flips).
    # ADDITIVE: NULL default → first post-migration advance starts clean.
    rs_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(regime_state)").fetchall()
    }
    if rs_cols and "last_advanced_bar_id" not in rs_cols:
        conn.execute(
            "ALTER TABLE regime_state ADD COLUMN last_advanced_bar_id INTEGER"
        )
    _migrate_quote_ticks_to_lww(conn)


def _migrate_quote_ticks_to_lww(conn: sqlite3.Connection) -> None:
    """Collapse a legacy append-per-tick quote_ticks to single-row LWW.

    Tick-stream decouple (vault/50_research/debates/tick_stream_decouple_2026-06-24.md):
    the table moves from PK=(instrument_id, ts) (unbounded append → 645k rows /
    215MB + a 15s retention DELETE that lock-contends the 1Hz writer) to a single
    LWW row per instrument (PK=instrument_id). ``CREATE TABLE IF NOT EXISTS`` in
    ALL_DDL cannot alter an existing PK, so an EXISTING DB needs this rebuild.

    Additive-first / never blind-DROP (Gemini D4): the latest-per-instrument rows
    are copied into the new shape and committed in ONE transaction BEFORE the old
    table is dropped — a crash mid-migration leaves the original table intact.
    Idempotent: a DB already in single-row shape (``ts`` not in the PK) is a
    no-op, so re-running init_db at every boot is safe. The kept row per
    instrument is the MAX(ts) tick — exactly what the latest-per-instrument
    consumers (Dashboard / Sentinel-S1) already read.
    """
    pk_cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(quote_ticks)").fetchall()
        if row[5]  # pk index > 0
    ]
    if pk_cols == ["instrument_id"]:
        return  # already single-row LWW (fresh DB or migrated) — no-op
    if "ts" not in pk_cols:
        return  # unexpected shape — leave untouched (fail-safe, never destroy)
    # Rebuild: new single-row table ← latest (max ts) row per instrument, then
    # swap. One transaction so the copy is durable before the old table is gone.
    # Flush any open implicit txn first so the explicit BEGIN cannot raise
    # "cannot start a transaction within a transaction" (the only caller is the
    # autocommit init_db connection, but this keeps the contract explicit).
    if conn.in_transaction:
        conn.execute("COMMIT")
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS quote_ticks_lww_new")
        conn.execute(
            DDL_QUOTE_TICKS.replace(
                "CREATE TABLE IF NOT EXISTS quote_ticks",
                "CREATE TABLE quote_ticks_lww_new",
            )
        )
        # Keep the greatest-ts row per instrument (the LWW survivor). INSERT OR
        # REPLACE + ORDER BY ts ASC processes oldest→newest so the highest-ts row
        # wins on the instrument_id PK. Robust to duplicate (instrument_id, ts)
        # rows in a legacy table — the prior MAX(ts) JOIN raised
        # "UNIQUE constraint failed: instrument_id" whenever such a tie existed.
        conn.execute(
            """INSERT OR REPLACE INTO quote_ticks_lww_new
               SELECT instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps,
                      bid_size, ask_size, last_trade_price, last_trade_size, source
               FROM quote_ticks ORDER BY ts ASC"""
        )
        conn.execute("DROP TABLE quote_ticks")
        conn.execute("ALTER TABLE quote_ticks_lww_new RENAME TO quote_ticks")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
