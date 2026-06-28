"""Shadow-first would-be order recorder — SUPPRESSED order, logged would-be P&L.

DEMO/PAPER only — virtual funds. flow_not_block: the SIGNAL still FLOWS the full
pipeline (G1-G5 + sizing); this records the WOULD-BE entry (the sized notional + the
decision-time mark + the strategy's exit target) so the live edge of a shadow-first
strategy can be measured WITHOUT putting capital at risk. The two weekend OKX makers
ship this way — their edge rests on a THIN single market period (flush ~20wk /
funding 96d, [[weekend_maker_honest_rerun_2026-06-28]]) so the durability is
INCONCLUSIVE; the would-be P&L accrues live before any real fill.

Instrumentation ONLY — it never opens a position, never writes a fill, never
influences any decision. A None conn or a missing table is a no-op
(degrade-never-crash): the signal already flowed and must never be undone by a
shadow-log failure. The forward would-be P&L is resolved offline from the
already-ingested bars at ``entry_mark`` + ``atr_pct`` (the SAME ATR-R basis the
counterfactual sweep uses), so this row needs no live network resolution.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

logger = logging.getLogger(__name__)

__all__ = ["log_shadow_order"]


def log_shadow_order(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    strategy_id: str,
    venue: str,
    symbol: str,
    side: str,
    entry_mark: float,
    notional_usd: float,
    atr_pct: float,
    profit_target_r: float | None,
    now_ts: int,
) -> None:
    """Append one ``weekend_shadow_orders`` would-be-entry row (no real order).

    No-op when ``conn`` is None. A sqlite error (e.g. a legacy DB without the
    table) is logged and swallowed — the signal already flowed and must never be
    crashed by a shadow-log failure (degrade-never-crash). NOTHING here opens a
    position / writes a fill / submits a venue order.
    """
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO weekend_shadow_orders
                (event_id, run_id, strategy_id, venue, symbol, side,
                 entry_mark, notional_usd, atr_pct, profit_target_r, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                run_id,
                strategy_id,
                venue,
                symbol,
                side,
                float(entry_mark),
                float(notional_usd),
                float(atr_pct),
                None if profit_target_r is None else float(profit_target_r),
                int(now_ts),
            ),
        )
    except sqlite3.Error as exc:
        logger.error("[shadow-order] durable record failed: %r", exc)
