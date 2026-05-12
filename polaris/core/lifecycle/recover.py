"""Session hydrate — restore ``state.open_trades`` from persisted SQLite.

A paper-loop restart leaves ``state.open_trades`` empty in memory, but the
``positions`` table still carries OPEN rows from prior sessions. Without
hydrate the close path (``trade.position_id`` lookup) cannot match those
rows, so they accumulate as stale OPEN forever (incident 2026-05-10).

``hydrate_open_positions`` rebuilds a ``SimulatedTrade`` for each OPEN row
that has a matching entry fill (``fills.contribution_id == position_id``,
``is_close = 0``). OPEN rows without a corresponding entry fill are
skipped — close path needs ``entry_price`` which only the entry fill
carries. Migration / dedup of legacy duplicate-logical-key rows is a
separate concern (A-PR2); hydrate is transparent about persisted state.

Spec: ``vault/50_research/debates/2026-05-10_topic_a_lifecycle_fix.md``
(Round 3 §R3-d).
"""
from __future__ import annotations

import sqlite3

from polaris.scripts._smoke_fills import SimulatedTrade

__all__ = ["hydrate_open_positions"]


def hydrate_open_positions(conn: sqlite3.Connection) -> list[SimulatedTrade]:
    """Return ``SimulatedTrade`` for every OPEN position with a matching entry fill.

    Ordered by ``opened_ts`` ascending so callers see the same chronology
    that produced the persisted state. ``notional_usd`` is reconstructed
    from the entry fill's ``size_usd``; ``entry_price`` from
    ``fill_price``. ``correlation_group`` / ``underlying_group_id`` are
    populated from the position row when present.
    """
    # GROUP BY position_id so a scale-in (multiple entry fills sharing a
    # position) collapses to one ``SimulatedTrade``. ``entry_price`` is the
    # size-weighted average so the close path's PnL denominator
    # (``_production_close.py``) matches the true cost basis;
    # ``notional_usd`` is the sum of entry size_usd.
    rows = conn.execute(
        """
        SELECT p.position_id, p.venue, p.symbol, p.strategy_id, p.side,
               p.opened_ts, p.underlying_group_id,
               SUM(f.fill_price * f.size_usd) / SUM(f.size_usd) AS entry_price,
               SUM(f.size_usd) AS notional_usd
        FROM positions p
        JOIN fills f
          ON f.contribution_id = p.position_id
         AND f.is_close = 0
        WHERE p.status = 'open'
        GROUP BY p.position_id, p.venue, p.symbol, p.strategy_id, p.side,
                 p.opened_ts, p.underlying_group_id
        ORDER BY p.opened_ts ASC
        """
    ).fetchall()
    out: list[SimulatedTrade] = []
    for r in rows:
        position_id = str(r[0])
        trade = SimulatedTrade(
            signal_id=position_id,
            venue=str(r[1]),
            symbol=str(r[2]),
            strategy_id=str(r[3]),
            side=str(r[4]),
            entry_price=float(r[7]),
            notional_usd=float(r[8]),
            open_ts=int(r[5]),
            position_id=position_id,
            underlying_group_id=str(r[6] or ""),
        )
        out.append(trade)
    return out
