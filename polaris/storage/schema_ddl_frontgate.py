"""Polaris SQLite DDL — Frontgate-scan feed tables (SEC EDGAR / DefiLlama
stablecoins / Finnhub earnings calendar) + their G3/G4 proximity SHADOW tags.

Additive only. Every table here is a pure EVIDENCE/provenance/shadow store:
NONE is read by any live gate/sizing/exit/halt decision path (frontgate-scan
behavior-0 invariant — collector/storage/shadow only, zero gating/sizing
touchpoints this wave). Split out of ``schema_ddl_ext.py`` (already 1000+ LOC)
to keep this feature's DDL in its own <=500-LOC module — mirrors
``schema_ddl_virtual.py`` / ``schema_ddl_reranker.py`` / ``schema_ddl_classes.py``.

Timestamp provenance (frontgate-scan invariant #2): every raw-event table keeps
the ORIGINAL SOURCE publication time and Polaris's OWN ingestion time in
separate columns — never a single blended timestamp — so no downstream reader
can look ahead of when Polaris actually observed the event.

Spec source: vault/50_research/frontgate-scan/data-sourcing-plan.md items #1-3.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# #1 SEC EDGAR — 8-K/10-Q/10-K filing events (per active-Alpaca-universe symbol)
# acceptance_ts = SEC's own acceptanceDateTime (A-grade original publish time,
# epoch seconds); ingestion_ts = when Polaris pulled the submissions feed.
# PRIMARY KEY on (symbol, accession_number) — NOT accession_number alone:
# dual-class tickers (GOOG/GOOGL, BRK-A/BRK-B) share one CIK and thus one
# accession-number set, so a bare accession_number PK would silently drop
# the second share class's row via INSERT OR IGNORE. The composite key keeps
# this table per-symbol (matching its per-symbol index) while still making
# the ~15min re-fetch of the same "recent" window a natural no-op, so real
# growth is bounded by actual new filings, not fetch cadence.
# ---------------------------------------------------------------------------

DDL_EDGAR_FILINGS = """
CREATE TABLE IF NOT EXISTS edgar_filings (
    symbol TEXT NOT NULL,
    cik TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL DEFAULT '',
    acceptance_ts INTEGER,
    ingestion_ts INTEGER NOT NULL,
    primary_document TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (symbol, accession_number)
);
"""

DDL_EDGAR_FILINGS_SYMBOL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_edgar_filings_symbol
    ON edgar_filings(symbol, acceptance_ts DESC);
"""

# G4 filing-proximity SHADOW (behavior-0, TAG-ONLY): one row per (symbol,
# collection cycle) recording how far the collector's own "now" sits from the
# latest filing's acceptance_ts. Never read by any live gate — a future,
# separate, non-shadow wire-up would be required to consume this.
DDL_FILING_PROXIMITY_SHADOW = """
CREATE TABLE IF NOT EXISTS filing_proximity_shadow (
    symbol TEXT NOT NULL,
    cycle_ts INTEGER NOT NULL,
    form_type TEXT NOT NULL DEFAULT '',
    days_since_filing REAL,
    accession_number TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (symbol, cycle_ts)
);
"""

# ---------------------------------------------------------------------------
# #2 DefiLlama stablecoins — total + per-symbol (USDT/USDC/DAI) circulating
# snapshot + derived 7d net-mint. Daily-resolution state snapshot (B-grade —
# no pub-vs-ingest lag to audit, ``ts`` IS the observation time).
# ---------------------------------------------------------------------------

DDL_STABLECOIN_LIQUIDITY = """
CREATE TABLE IF NOT EXISTS stablecoin_liquidity (
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    mcap_usd REAL,
    mcap_prev_day_usd REAL,
    mcap_prev_week_usd REAL,
    net_mint_7d_usd REAL,
    net_mint_7d_pct REAL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (ts, symbol)
);
"""

DDL_STABLECOIN_LIQUIDITY_SYMBOL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stablecoin_liquidity_symbol
    ON stablecoin_liquidity(symbol, ts DESC);
"""

# ---------------------------------------------------------------------------
# #3 Finnhub earnings calendar — forward 14d + trailing 7d, active-Alpaca-
# universe intersection only. surprise_pct is a plain (actual-estimate)/|est|
# proxy, NOT a real SUE (no consensus-revision/std-dev — see
# vault/50_research/frontgate-scan/scan-event-models.md R1). UPSERT-keyed on
# (symbol, earnings_date) so the eps_actual/surprise_pct columns fill in once
# the print lands, without duplicating the row.
# ---------------------------------------------------------------------------

DDL_EARNINGS_CALENDAR = """
CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol TEXT NOT NULL,
    earnings_date TEXT NOT NULL,
    hour TEXT NOT NULL DEFAULT '',
    eps_estimate REAL,
    eps_actual REAL,
    revenue_estimate REAL,
    revenue_actual REAL,
    surprise_pct REAL,
    ingestion_ts INTEGER NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (symbol, earnings_date)
);
"""

DDL_EARNINGS_CALENDAR_DATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_earnings_calendar_date
    ON earnings_calendar(earnings_date);
"""

# G3 earnings-proximity SHADOW (behavior-0, TAG-ONLY): one row per (symbol,
# collection cycle) recording the signed day-distance to the nearest earnings
# print + the SUE-proxy surprise (when a recent actual is already known).
DDL_EARNINGS_PROXIMITY_SHADOW = """
CREATE TABLE IF NOT EXISTS earnings_proximity_shadow (
    symbol TEXT NOT NULL,
    cycle_ts INTEGER NOT NULL,
    days_to_earnings REAL,
    hour TEXT NOT NULL DEFAULT '',
    surprise_pct REAL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY (symbol, cycle_ts)
);
"""
