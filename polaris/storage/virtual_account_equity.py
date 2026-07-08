"""Per-exchange VIRTUAL account equity — continuously compounding (Jin 2026-07-07).

Derives "current account equity" for one exchange from the internal fills
ledger (sim fills only — zero venue calls), anchored at its $100k seed UNLESS
a ``virtual_ruin_events`` re-seed has fired for that exchange, in which case
the anchor becomes the LATEST ruin's ``reseeded_to`` value at its
``ruined_ts`` (mirrors the ``measurement_resets`` forward-window pattern
already used for the dashboard's since-reset rollup — same idea, scoped
per-exchange). This keeps the account CONTINUOUSLY COMPOUNDING (no periodic
reset) while still being able to "re-seed" cleanly after a ruin event.

Realized PnL is NET of fees, excludes RECONCILED (tracking-failure) fills —
same convention as ``snapshot_q_equity._realised_pnl_since``. Unrealized PnL
is LIVE-DERIVED from open positions as ``(mark - entry) * qty * sign``
(best-effort; 0.0 when a position has no resolvable entry/mark) — see
``_unrealized_pnl_now``.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple


class VirtualEquity(NamedTuple):
    """One exchange's derived virtual equity at ``now``."""

    exchange: str
    seed_anchor: float  # $100k, or the latest ruin's reseeded_to
    anchor_ts: int  # 0 (epoch) unless a ruin re-anchored this exchange
    realized_pnl_usd: float  # net-of-fees, since anchor_ts
    unrealized_pnl_usd: float
    equity: float  # seed_anchor + realized_pnl_usd + unrealized_pnl_usd


def _latest_ruin_anchor(
    conn: sqlite3.Connection, *, exchange: str
) -> tuple[float, int] | None:
    """(reseeded_to, ruined_ts) of the most recent ruin for ``exchange``, else None."""
    try:
        row = conn.execute(
            "SELECT reseeded_to, ruined_ts FROM virtual_ruin_events "
            "WHERE exchange = ? ORDER BY ruined_ts DESC, ruin_id DESC LIMIT 1",
            (exchange,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return float(row[0] or 0.0), int(row[1] or 0)


def _realized_pnl_net_since(
    conn: sqlite3.Connection, *, exchange: str, since_ms: int
) -> float:
    """Net-of-fees realized PnL for ``exchange`` since ``since_ms`` (fills.ts_ms).

    Excludes RECONCILED (tracking-failure) fills — mirrors
    ``snapshot_q_equity._realised_pnl_since``.
    """
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd
                                         ELSE 0.0 END), 0.0)
                      - COALESCE(SUM(f.fee_usd), 0.0)
               FROM fills f
               LEFT JOIN positions p ON p.position_id = f.contribution_id
               WHERE f.venue = ? AND f.ts_ms >= ?
                 AND (p.status IS NULL OR p.status != 'reconciled')""",
            (exchange, since_ms),
        ).fetchone()
    except sqlite3.Error:
        return 0.0
    return float(row[0] or 0.0) if row else 0.0


def _unrealized_pnl_now(conn: sqlite3.Connection, *, exchange: str) -> float:
    """Sum of open positions' unrealized PnL for ``exchange``, LIVE-DERIVED.

    ``positions`` carries no ``upnl_usd`` column at all (schema_ddl_core) — the
    prior implementation's ``PRAGMA table_info`` guard was therefore always
    False, so this always read 0.0 (forward-fix, not a hardcode). Instead this
    derives ``(mark - entry) * qty * sign`` per open position, same as the
    dashboard's live positions board
    (``snapshot_q_positions._read_positions``): ``entry`` = the latest
    open-fill price (``fills.is_close = 0``), ``mark`` = the latest bar
    close/fresh quote-tick mid. Function-LOCAL import of those two lookups —
    a module-level import would deadlock on partial init: importing
    ``polaris.scripts.dashboard.snapshot_q_positions`` first initializes the
    ``polaris.scripts.dashboard`` package, whose ``__init__`` eagerly chains
    into ``snapshot_q_streams``, which imports THIS module (``virtual_equity_
    now``) at its own module level — a circular import.

    No quote-ccy→USD conversion (unlike the dashboard's ``upnl_usd``, #50):
    scope is the raw ``(mark - entry) * qty * sign`` in the instrument's quote
    ccy. FX/CFD instruments quoted in JPY/EUR (J225/EU50/…) are therefore NOT
    USD-normalized here — a known deviation from the dashboard's positions
    board, left for a follow-up if virtual-equity display needs USD parity.

    Best-effort: any sqlite error degrades to 0.0 — never raises, never a
    trading path (display-only, like ``virtual_equity_now`` itself).
    """
    from polaris.scripts.dashboard.snapshot_q_positions import (
        _entry_price_lookup,
        _last_prices,
    )

    try:
        rows = conn.execute(
            "SELECT symbol, strategy_id, side, qty FROM positions "
            "WHERE venue = ? AND status = 'open'",
            (exchange,),
        ).fetchall()
    except sqlite3.Error:
        return 0.0
    if not rows:
        return 0.0
    last_prices = _last_prices(conn)
    entry_lookup = _entry_price_lookup(conn)
    total = 0.0
    for symbol, strategy_id, side, qty in rows:
        inst = f"{exchange}:{symbol}"
        entry = entry_lookup.get((exchange, inst, str(strategy_id)))
        if entry is None or entry <= 0.0:
            entry = last_prices.get(inst, 0.0) or 0.0
        mark = last_prices.get(inst, entry)
        sign = 1.0 if str(side).lower() in {"long", "buy"} else -1.0
        total += (mark - entry) * float(qty or 0.0) * sign
    return total


def virtual_equity_now(
    conn: sqlite3.Connection, *, exchange: str, seed_equity: float
) -> VirtualEquity:
    """Derive ``exchange``'s current virtual equity from the internal ledger.

    Anchored at ``seed_equity`` from epoch (ts=0) UNLESS a ruin re-seed
    exists for this exchange, in which case the anchor becomes that ruin's
    ``reseeded_to`` at its ``ruined_ts`` — realized PnL is only summed
    FORWARD of the anchor, so a pre-ruin blowup is never double-counted
    after the re-seed.
    """
    anchor_ruin = _latest_ruin_anchor(conn, exchange=exchange)
    anchor_value = seed_equity if anchor_ruin is None else anchor_ruin[0]
    anchor_ts = 0 if anchor_ruin is None else anchor_ruin[1]
    realized = _realized_pnl_net_since(
        conn, exchange=exchange, since_ms=anchor_ts * 1000,
    )
    unrealized = _unrealized_pnl_now(conn, exchange=exchange)
    return VirtualEquity(
        exchange=exchange,
        seed_anchor=anchor_value,
        anchor_ts=anchor_ts,
        realized_pnl_usd=realized,
        unrealized_pnl_usd=unrealized,
        equity=anchor_value + realized + unrealized,
    )
