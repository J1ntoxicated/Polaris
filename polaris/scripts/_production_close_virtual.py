"""Close-path hook — virtual weekly trace + reset-only-on-ruin (Jin 2026-07-07).

Fault-isolated post-close side effect (mirrors the ``_safe_*`` helpers in
``_production_close_effects.py``): on every close, derive this exchange's
current virtual equity from the internal ledger (zero venue calls), upsert
the Monday-anchored weekly trace row (TRACE, never RESET — the account
compounds continuously), and check the ruin floor. A failure here must never
abort an already-committed close; flow_not_block — this never blocks/skips a
trade, it only measures + flags.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from polaris.core.sizing.constants import equity_usd_for_venue
from polaris.storage.virtual_account_equity import virtual_equity_now
from polaris.storage.virtual_ruin import check_and_reseed_ruin
from polaris.storage.weekly_equity_trace import upsert_weekly_row

if TYPE_CHECKING:
    from polaris.scripts._smoke_fills import SimulatedTrade

logger = logging.getLogger(__name__)


def safe_update_virtual_trace(
    conn: sqlite3.Connection,
    *,
    trade: SimulatedTrade,
    pnl_usd_net: float,
    now_ts: int,
) -> None:
    """Post-close: derive equity, trace this week, check ruin. Fail-open.

    ``pnl_usd_net`` is the SAME real-fee-net close delta the cell matrix /
    learners / posterior fold (one number, one source) — folded as this
    close's ``realized_pnl_delta_usd`` into the weekly row.
    """
    exchange = trade.venue
    try:
        seed = equity_usd_for_venue(exchange)
        equity = virtual_equity_now(conn, exchange=exchange, seed_equity=seed)
        upsert_weekly_row(
            conn, exchange=exchange, now_ts=now_ts,
            account_equity=equity.equity,
            realized_pnl_delta_usd=pnl_usd_net,
            unrealized_pnl_usd=equity.unrealized_pnl_usd,
            trade_delta=1,
        )
        check_and_reseed_ruin(
            conn, exchange=exchange, current_equity=equity.equity,
            seed_equity=seed, now_ts=now_ts,
        )
    except Exception as exc:  # noqa: BLE001 — measurement/observability only
        logger.warning(
            "[virtual-trace] weekly trace / ruin check failed %s:%s: %r",
            trade.venue, trade.symbol, exc,
        )
