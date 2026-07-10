"""Layer 3 — ``position_risk_state`` persistence (write on open / delete on close).

The read path (``_read_portfolio_state`` in
``polaris.core.pipeline._sizer_payload``) hydrates ``PortfolioState.open_positions``
from this table so ``compute_size`` can bind the per-symbol / underlying /
cluster / track caps. Until this module existed the table was never written on
a real open, so live open risk read as zero and the caps never bound — letting
the sizer over-deploy / churn the same names.

This is a CAPITAL-EFFICIENCY wire (opportunity-cost ceilings), not a defensive
throttle: the caps are headroom ceilings the aggressive sizer already honours;
populating them simply makes the existing ceilings reflect reality.

Idempotency
-----------
``persist_position_risk_state`` uses ``INSERT OR REPLACE`` keyed on the table PK
``(venue, symbol, strategy, opened_ts)`` so a re-driven open does not double-count
the same position. ``delete_position_risk_state`` removes that exact row on close
so ``PortfolioState`` reflects only live open risk.
"""

from __future__ import annotations

import sqlite3

from polaris.core.sizing.cluster_cap import resolve_cluster_id

__all__ = [
    "delete_position_risk_state",
    "persist_position_risk_state",
]


def persist_position_risk_state(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    instrument_id: str,
    underlying_group_id: str,
    strategy: str,
    track: str,
    asset_class: str,
    signal_strength: float,
    notional_usd: float,
    equity_usd: float,
    opened_ts: int,
) -> None:
    """Insert (idempotently) the open-position risk row the sizer's caps read.

    ``open_risk_pct`` is the notional as a fraction of equity — this is the
    field every cap (per-symbol / underlying / cluster / track) sums over. The
    ``cluster_id`` is resolved from the same deterministic map the sizer uses
    (``strategy_id=strategy`` included — see the sizer's own ``engine.py:878``
    call — so an equity meanrev-sleeve position persists as
    ``equity:meanrev``, not the ``equity:beta_trend`` default) so a position
    contributes to its cluster cap exactly as the headroom check expects.
    """
    cluster_id = resolve_cluster_id(
        underlying_group_id=underlying_group_id,
        asset_class=asset_class,
        symbol=symbol,
        strategy_id=strategy,
    )
    open_risk_pct = (notional_usd / equity_usd) if equity_usd > 0.0 else 0.0
    conn.execute(
        """
        INSERT OR REPLACE INTO position_risk_state (
            venue, symbol, instrument_id, underlying_group_id, cluster_id,
            strategy, track, signal_strength, open_risk_pct, notional_usd,
            opened_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            venue,
            symbol,
            instrument_id,
            underlying_group_id,
            cluster_id,
            strategy,
            track,
            float(signal_strength),
            float(open_risk_pct),
            float(notional_usd),
            int(opened_ts),
        ),
    )


def delete_position_risk_state(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy: str,
    opened_ts: int,
) -> None:
    """Remove the open-position risk row on close (PK match).

    Keyed on the same PK the open write used so only the closed position's
    contribution is dropped — concurrent opens of the same name at a different
    ``opened_ts`` are untouched.
    """
    conn.execute(
        """
        DELETE FROM position_risk_state
        WHERE venue = ? AND symbol = ? AND strategy = ? AND opened_ts = ?
        """,
        (venue, symbol, strategy, int(opened_ts)),
    )
