"""Bar-path OKX settle-ability gate — close the trade-eligibility ENFORCEMENT gap.

DEMO/PAPER · AGGRESSIVE bias preserved · flow_not_block · 9-stack ban · -1.0R rail
untouched. No sizing multiplier is touched here — this is a CORRECTNESS overlay at
the bar-pipeline order seam, the twin of the tick engine's ``eligible_set`` gate.

Root cause this proves fixed: ``_okx_quote_trade_eligible`` (entrance.py) correctly
forces ``trade_eligible=0`` for an OKX pair whose quote ∉ {USDT, USDC} (a crypto
quote like ``SOL-ETH`` or a nominal-USD pair), but that flag was ONLY enforced on
the tick-engine entry path (``_production_tick_engine._run_entries`` eligible_set).
The BAR pipeline (``_production_tick._run_tick``) dispatched ``bar_breakout_run``
over the FULL watch set and never consulted the flag, so crypto-quote names reached
the OKX order path and 100% rejected at the venue (51201 sz mis-unit / 51008 no
quote-ccy balance on the USDT-only demo wallet).

flow_not_block: the gate REDIRECTS flow to settleable USDT pairs (live fills were
0); the crypto-quote name stays WATCHED/SIGNALED/streamed — only its ENTRY is
deferred. A settleable USDT pair is NEVER deferred on a thin score (the bar path
uses ONLY the structural quote-ccy guard, not the opportunity-score floor — so the
99 USDT breakout signals reach orders).
"""

from __future__ import annotations

import sqlite3

from polaris.scripts._production_tick import okx_quote_settleable, okx_unsettleable_set
from polaris.storage.schema import init_db


def _seed_universe(conn: sqlite3.Connection) -> None:
    rows = [
        # (venue, symbol, quote_ccy)  — OKX SPOT demo USDT-only wallet.
        ("okx", "BTC-USDT", "USDT"),   # settleable
        ("okx", "ETH-USDT", "USDT"),   # settleable
        ("okx", "AAVE-USDC", "USDC"),  # settleable (USDC also a USD-stablecoin)
        ("okx", "SOL-ETH", "ETH"),     # CRYPTO-QUOTE → unsettleable (51008/51201)
        ("okx", "DOGE-BTC", "BTC"),    # CRYPTO-QUOTE → unsettleable
        ("okx", "ETH-USD", "USD"),     # nominal-USD → unsettleable (#44 51000)
        ("alpaca", "AAPL", "USD"),     # non-OKX venue → always settleable
        ("capital", "GOLD", "USD"),    # non-OKX venue → always settleable
    ]
    for venue, symbol, quote in rows:
        conn.execute(
            "INSERT INTO universe "
            "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
            " quote_ccy, state, last_seen_ts, is_active) "
            "VALUES (?,?,?,?,?,?,'live',1000,1)",
            (venue, symbol, f"{venue}:{symbol}", f"crypto:{symbol}", "crypto", quote),
        )


def test_okx_quote_settleable_predicate() -> None:
    # OKX: only USDT / USDC quotes are settleable on the demo SPOT wallet.
    assert okx_quote_settleable("okx", "USDT") is True
    assert okx_quote_settleable("okx", "USDC") is True
    # Crypto quote + nominal-USD quote → NOT settleable (would 51201/51008/51000).
    assert okx_quote_settleable("okx", "ETH") is False
    assert okx_quote_settleable("okx", "BTC") is False
    assert okx_quote_settleable("okx", "USD") is False
    # Non-OKX venues price venue-side in USD → always settleable (flow_not_block).
    assert okx_quote_settleable("alpaca", "USD") is True
    assert okx_quote_settleable("capital", "USD") is True


def test_unsettleable_set_excludes_only_okx_crypto_quote() -> None:
    conn = init_db(":memory:")
    _seed_universe(conn)
    unsettleable = okx_unsettleable_set(conn)

    # The crypto-quote + nominal-USD OKX pairs are the ONLY deferred entries.
    assert ("okx", "SOL-ETH") in unsettleable
    assert ("okx", "DOGE-BTC") in unsettleable
    assert ("okx", "ETH-USD") in unsettleable

    # Settleable USDT/USDC OKX pairs must REACH the order path (task #3: the 99
    # breakout signals settle).
    assert ("okx", "BTC-USDT") not in unsettleable
    assert ("okx", "ETH-USDT") not in unsettleable
    assert ("okx", "AAVE-USDC") not in unsettleable

    # Non-OKX venues are NEVER in the set (they are not OKX-quote-gated).
    assert ("alpaca", "AAPL") not in unsettleable
    assert ("capital", "GOLD") not in unsettleable
    conn.close()


def test_unsettleable_set_is_quote_guard_not_score_floor() -> None:
    """A USDT pair is NEVER deferred regardless of its opportunity score.

    The bar gate uses ONLY the structural quote-ccy guard (settle-ability), NOT the
    entrance score floor — so a low-score USDT breakout still reaches the order
    path (aggressive bias / flow_not_block). Only the structurally-unsettleable
    crypto-quote pair is deferred.
    """
    conn = init_db(":memory:")
    # A USDT pair with NO focus row / no score at all is still settleable.
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts, is_active) VALUES "
        "('okx','THIN-USDT','okx:THIN-USDT','crypto:THIN','crypto','USDT',"
        "'live',1000,1)"
    )
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts, is_active) VALUES "
        "('okx','THIN-ETH','okx:THIN-ETH','crypto:THIN2','crypto','ETH',"
        "'live',1000,1)"
    )
    unsettleable = okx_unsettleable_set(conn)
    assert ("okx", "THIN-USDT") not in unsettleable  # thin USDT still flows
    assert ("okx", "THIN-ETH") in unsettleable        # crypto-quote deferred
    conn.close()
