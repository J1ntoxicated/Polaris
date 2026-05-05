"""Polaris persistence layer — unified SQLite trade ledger.

Phase 9 (2026-05-05): SSOT for trades, balances, positions.
Replaces 270 JSON files (paper_state_*.json) with normalized SQL tables.

Modules:
    schema: DDL strings + version constant (P6 pure).
    ledger: TradeLedger class — connection + CRUD (shell I/O).
    queries: Analytical SELECTs — drawdown / correlation / attribution (P6 pure SQL).
    migrations: JSON → SQL backfill (one-shot script).

Usage:
    from src.persist.ledger import TradeLedger

    with TradeLedger("data/polaris.sqlite") as ledger:
        ledger.insert_position_open(pos, hypo_id="HYPO-008-RT", signal_meta={...})
        balance = ledger.get_balance("HYPO-008-RT", "BTC-USDT")
"""
from src.persist.ledger import TradeLedger
from src.persist.schema import SCHEMA_VERSION

__all__ = ["TradeLedger", "SCHEMA_VERSION"]
