"""Marketdata-domain SQLite bootstrap (`data/polaris_marketdata.sqlite`).

Storage split (vault/50_research/storage-split-blueprint.md, 2026-07-14 wipe
reset): the market-observation "firehose" (bars 12.4M rows + ticker_baseline_
samples 9.1M + watchlist_focus 149K, plus the lower-volume alt-data/shadow
EVIDENCE tables) shares ONE WAL write-lock with latency-critical trading
writes (positions/fills/regime) in the pre-split single-file DB — that
contention was the root cause of the 2026-07-13 10h main-tick freeze. This
module gives the firehose its OWN file + WAL lock.

``trading`` keeps the EXISTING ``schema.init_db`` / ``schema.ALL_DDL``
UNCHANGED (Jin 2026-07-14 mandate: "trading은 기존 init_db 그대로") — the
trading DB (``data/polaris_live.sqlite``) still nominally contains the full
superset schema (idempotent ``CREATE TABLE IF NOT EXISTS``), it simply is no
longer WRITTEN by production code for the tables this module owns. This
keeps every existing single-conn test fixture (``init_db(tmp_path)``) byte-
identical — only the PRODUCTION wiring (``production_paper_loop.py`` +
callees) is repointed to a second connection against this module's DB.

Domain boundary (SSOT — reproduced from the storage-split task spec, not
re-derived): bars, ticker_baseline_samples, watchlist_focus (핵심 3) +
ticker_ground, ticker_technicals, ticker_baseline_state, quote_ticks,
tick_inflow, altdata_snapshot, every ``*_shadow`` table EXCEPT
``gate_shadow_events`` (G2 GPT-vs-deterministic shadow — same-txn joined with
``signals``, stays trading), edgar_filings, earnings_calendar,
stablecoin_liquidity, benchmark_results, calibration_pairs,
gate_kill_counterfactuals, meta_labels. ``r_budget_shadow_state``
(``core/sizing/r_budget_sizer.py``) is a single-row (``id=1`` CHECK) ad hoc
table created lazily on whatever conn ``compute_size`` receives — moving it
would require threading a second conn through the sizing engine's public
signature (4+ call sites) for zero firehose benefit; left on the trading conn
(Phase 2 candidate, documented, not silently dropped).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from polaris.storage.schema import connect
from polaris.storage.schema_ddl_altdata import (
    DDL_ALTDATA_SNAPSHOT,
    DDL_ALTDATA_SNAPSHOT_INDEX,
    DDL_TICKER_GROUND,
    DDL_TICKER_GROUND_INDEX,
)
from polaris.storage.schema_ddl_core import (
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_BARS_INSTRUMENT_TS_INDEX,
    DDL_QUOTE_TICKS,
    DDL_TICK_INFLOW,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_SAMPLES,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_TECHNICALS,
    DDL_WATCHLIST_FOCUS,
)
from polaris.storage.schema_ddl_ext import (
    DDL_BENCHMARK_RESULTS,
    DDL_BENCHMARK_RESULTS_INDEX,
    DDL_CALIBRATION_PAIRS,
    DDL_CALIBRATION_PAIRS_INDEX,
    DDL_ENTRY_ADMISSION_SHADOW,
    DDL_ENTRY_ADMISSION_SHADOW_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS,
    DDL_GATE_KILL_COUNTERFACTUALS_GATE_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS_PENDING_INDEX,
    DDL_MAKER_FILL_SHADOW,
    DDL_MAKER_FILL_SHADOW_INDEX,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_NEWS_TIMING_SHADOW,
    DDL_NEWS_TIMING_SHADOW_DEDUP_INDEX,
    DDL_NEWS_TIMING_SHADOW_SYMBOL_INDEX,
    DDL_PRICE_THROUGH_SHADOW,
    DDL_PRICE_THROUGH_SHADOW_INDEX,
    DDL_SECTOR_RANK_SHADOW,
    DDL_SECTOR_RANK_SHADOW_INDEX,
    DDL_VWAP_TIMING_SHADOW,
    DDL_VWAP_TIMING_SHADOW_PENDING_INDEX,
    DDL_VWAP_TIMING_SHADOW_RUN_INDEX,
    DDL_WEEKEND_SHADOW_ORDERS,
    DDL_WEEKEND_SHADOW_ORDERS_INDEX,
)
from polaris.storage.schema_ddl_frontgate import (
    DDL_EARNINGS_CALENDAR,
    DDL_EARNINGS_CALENDAR_DATE_INDEX,
    DDL_EARNINGS_PROXIMITY_SHADOW,
    DDL_EDGAR_FILINGS,
    DDL_EDGAR_FILINGS_SYMBOL_INDEX,
    DDL_FILING_PROXIMITY_SHADOW,
    DDL_STABLECOIN_LIQUIDITY,
    DDL_STABLECOIN_LIQUIDITY_SYMBOL_INDEX,
)

__all__ = [
    "DEFAULT_MARKETDATA_DB_PATH",
    "MARKETDATA_DDL",
    "init_marketdata_db",
    "marketdata_db_path_for",
]

DEFAULT_MARKETDATA_DB_PATH = Path("data/polaris_marketdata.sqlite")

MARKETDATA_DDL: tuple[str, ...] = (
    # 핵심 3 — the actual firehose (2026-07-13 freeze root cause).
    DDL_WATCHLIST_FOCUS,
    DDL_BARS,
    DDL_BARS_INDEX,
    DDL_BARS_INSTRUMENT_TS_INDEX,
    DDL_TICKER_BASELINE_STATE,
    DDL_TICKER_BASELINE_INDEX_GROUP,
    DDL_TICKER_BASELINE_INDEX_CLASS,
    DDL_TICKER_BASELINE_SAMPLES,
    # Tick-stream + technical store.
    DDL_QUOTE_TICKS,
    DDL_TICK_INFLOW,
    DDL_TICKER_TECHNICALS,
    # Alt-data EVIDENCE.
    DDL_ALTDATA_SNAPSHOT,
    DDL_ALTDATA_SNAPSHOT_INDEX,
    DDL_TICKER_GROUND,
    DDL_TICKER_GROUND_INDEX,
    # Gate→outcome instrumentation (G3/G4 KILL/PASS counterfactuals).
    DDL_GATE_KILL_COUNTERFACTUALS,
    DDL_GATE_KILL_COUNTERFACTUALS_PENDING_INDEX,
    DDL_GATE_KILL_COUNTERFACTUALS_GATE_INDEX,
    # SHADOW measurement tables (gate_shadow_events excluded — stays trading).
    DDL_ENTRY_ADMISSION_SHADOW,
    DDL_ENTRY_ADMISSION_SHADOW_INDEX,
    DDL_MAKER_FILL_SHADOW,
    DDL_MAKER_FILL_SHADOW_INDEX,
    DDL_PRICE_THROUGH_SHADOW,
    DDL_PRICE_THROUGH_SHADOW_INDEX,
    DDL_WEEKEND_SHADOW_ORDERS,
    DDL_WEEKEND_SHADOW_ORDERS_INDEX,
    DDL_SECTOR_RANK_SHADOW,
    DDL_SECTOR_RANK_SHADOW_INDEX,
    DDL_VWAP_TIMING_SHADOW,
    DDL_VWAP_TIMING_SHADOW_RUN_INDEX,
    DDL_VWAP_TIMING_SHADOW_PENDING_INDEX,
    DDL_NEWS_TIMING_SHADOW,
    DDL_NEWS_TIMING_SHADOW_SYMBOL_INDEX,
    DDL_NEWS_TIMING_SHADOW_DEDUP_INDEX,
    DDL_META_LABELS,
    DDL_META_LABELS_INDEX,
    DDL_CALIBRATION_PAIRS,
    DDL_CALIBRATION_PAIRS_INDEX,
    # P1 replay/benchmark read-model + frontgate feeds.
    DDL_BENCHMARK_RESULTS,
    DDL_BENCHMARK_RESULTS_INDEX,
    DDL_EDGAR_FILINGS,
    DDL_EDGAR_FILINGS_SYMBOL_INDEX,
    DDL_FILING_PROXIMITY_SHADOW,
    DDL_STABLECOIN_LIQUIDITY,
    DDL_STABLECOIN_LIQUIDITY_SYMBOL_INDEX,
    DDL_EARNINGS_CALENDAR,
    DDL_EARNINGS_CALENDAR_DATE_INDEX,
    DDL_EARNINGS_PROXIMITY_SHADOW,
)


def marketdata_db_path_for(trading_db_path: Path | str) -> Path:
    """Resolve the marketdata DB path for a given trading DB path.

    ``POLARIS_MARKETDATA_DB`` env overrides unconditionally (ops escape
    hatch). Otherwise derives a sibling file in the SAME directory as the
    trading DB, named ``polaris_marketdata.sqlite`` — keeps both files next
    to each other under ``data/`` regardless of the trading DB's own name
    (``polaris_live.sqlite`` in production, a tmp path in tests).
    """
    override = os.environ.get("POLARIS_MARKETDATA_DB")
    if override:
        return Path(override)
    return Path(trading_db_path).parent / "polaris_marketdata.sqlite"


def init_marketdata_db(
    db_path: Path | str = DEFAULT_MARKETDATA_DB_PATH,
) -> sqlite3.Connection:
    """Create every marketdata table/index if missing; return a live connection.

    Fresh-init only (wipe-reset context, Jin 2026-07-14 — "마이그레이션 코드
    금지"): unlike ``schema.init_db``, there is no legacy DB to migrate, so
    this is just the DDL loop — no ``_apply_post_migrations`` twin. Reuses
    ``schema.connect`` for byte-identical WAL/busy-timeout/foreign-key pragmas.
    """
    conn = connect(db_path)
    for stmt in MARKETDATA_DDL:
        conn.execute(stmt)
    return conn
