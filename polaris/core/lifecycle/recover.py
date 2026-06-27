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

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from polaris.core.lifecycle.trade import SimulatedTrade

logger = logging.getLogger(__name__)

__all__ = ["hydrate_open_positions", "reconcile_venue_positions"]

RECONCILE_STRATEGY_ID = "_reconcile_import"


class _AlpacaLike(Protocol):
    async def fetch_positions(self) -> list[dict[str, Any]]: ...


class _CapitalLike(Protocol):
    async def list_positions(self) -> dict[str, Any]: ...


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
    # BUG B: ``closed_qty`` subtracts the persisted PARTIAL-close fills
    # (instrument-id scoped — a foreign instrument's close fill sharing the
    # contribution_id is historical cross-match pollution and must not shrink
    # this position) and ``pos_qty`` carries positions.qty (the partial-close
    # decrement SSOT). Hydrating the raw entry-fills sum re-sold the ORIGINAL
    # full qty after every restart (SPCE: 552.29 shares sold across 3 sessions
    # for a 221.29-share entry).
    rows = conn.execute(
        """
        SELECT p.position_id, p.venue, p.symbol, p.strategy_id, p.side,
               p.opened_ts, p.underlying_group_id,
               SUM(f.fill_price * f.size_usd) / SUM(f.size_usd) AS entry_price,
               SUM(f.size_usd) AS notional_usd,
               MAX(f.order_id) AS venue_order_id,
               SUM(f.base_qty) AS base_qty,
               p.deal_id AS deal_id,
               p.qty AS pos_qty,
               (SELECT COALESCE(SUM(c.base_qty), 0.0) FROM fills c
                 WHERE c.contribution_id = p.position_id
                   AND c.instrument_id = p.venue || ':' || p.symbol
                   AND c.is_close = 1) AS closed_qty
        FROM positions p
        JOIN fills f
          ON f.contribution_id = p.position_id
         AND f.is_close = 0
        WHERE p.status = 'open'
        -- allow-list 'open' only → 'closed'/'cancelled'/'reconciled' (FIX 2
        -- venue-side state-drift recovery) are all excluded by construction, so
        -- a reconciled orphan is never re-hydrated + retried on restart.
        GROUP BY p.position_id, p.venue, p.symbol, p.strategy_id, p.side,
                 p.opened_ts, p.underlying_group_id, p.deal_id, p.qty
        ORDER BY p.opened_ts ASC
        """
    ).fetchall()
    out: list[SimulatedTrade] = []
    for r in rows:
        position_id = str(r[0])
        venue = str(r[1])
        entry_qty = float(r[10] or 0.0)
        pos_qty = float(r[12] or 0.0)
        closed_qty = float(r[13] or 0.0)
        # Remaining = min of BOTH remainders so over-restoring (= re-selling)
        # is blocked from either side: a legacy row whose qty was never
        # decremented is caught by the fills subtraction; a row whose close
        # fills were lost is caught by positions.qty.
        remaining = max(0.0, min(pos_qty, entry_qty - closed_qty))
        if remaining <= 1e-12:
            # Fully consumed by persisted closes — never resurrect it as a
            # sellable trade (the restart re-sell loop). The stale 'open'
            # status row itself is the correction script's job to flip.
            logger.warning(
                "[hydrate] %s %s:%s skipped — entry qty %.10f already consumed "
                "(closed %.10f, positions.qty %.10f); status row left for the "
                "correction pass",
                position_id, venue, str(r[2]), entry_qty, closed_qty, pos_qty,
            )
            continue
        venue_order_id = str(r[9]) if r[9] else None
        # positions.deal_id is the SSOT for the Capital close key (persisted at
        # open + reconcile-import). Fall back to the entry fill's order_id stash
        # (``venue_order_id``) for legacy rows written before the column existed.
        persisted_deal_id = str(r[11]) if r[11] else None
        deal_id = persisted_deal_id or (
            venue_order_id if venue == "capital" else None
        )
        trade = SimulatedTrade(
            signal_id=position_id,
            venue=venue,
            symbol=str(r[2]),
            strategy_id=str(r[3]),
            side=str(r[4]),
            entry_price=float(r[7]),
            # Scale the rotation/risk-view notional to the REMAINING fraction
            # so it matches the actual residual venue exposure.
            notional_usd=float(r[8]) * (remaining / entry_qty)
            if entry_qty > 0.0 else float(r[8]),
            open_ts=int(r[5]),
            position_id=position_id,
            underlying_group_id=str(r[6] or ""),
            # P0-5 venue-wire: restore the close-relevant venue refs so a
            # real-roundtrip restart can still close the position. Capital closes
            # by ``deal_id`` (positions.deal_id SSOT, fill order_id fallback);
            # OKX closes by ``base_qty`` and needs no deal_id.
            venue_order_id=venue_order_id,
            deal_id=deal_id,
            base_qty=remaining,
        )
        out.append(trade)
    return out


def _run_coro[T](coro: Awaitable[T]) -> T:
    """Drive an adapter coroutine to completion from sync code.

    ``reconcile_venue_positions`` is sync (mirrors ``hydrate_open_positions``)
    but the venue adapters are async. When called from inside a running event
    loop (production paper loop) we cannot ``asyncio.run`` directly, so the
    coroutine is run in a dedicated worker thread with its own loop. With no
    running loop (tests) the same path works.
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()  # type: ignore[arg-type]


def _existing_keys(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Set of ``(venue, symbol)`` already tracked as OPEN in the DB."""
    rows = conn.execute(
        "SELECT venue, symbol FROM positions WHERE status = 'open'"
    ).fetchall()
    return {(str(r[0]), str(r[1])) for r in rows}


def _import_one(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    side: str,
    mark: float,
    base_qty: float,
    now_ts: int,
    deal_id: str | None,
) -> SimulatedTrade | None:
    """Persist one reconcile-import position + synthetic entry fill at mark.

    entry_price == current mark, fee_usd == 0, NO fabricated cost basis ⇒
    unrealized PnL starts ~0. Returns the ``SimulatedTrade`` (hydrate shape)
    or ``None`` if the venue payload is unusable (non-positive mark / qty).
    """
    if mark <= 0.0 or base_qty <= 0.0:
        logger.warning(
            "[reconcile] skip %s:%s — non-positive mark=%s qty=%s",
            venue, symbol, mark, base_qty,
        )
        return None
    notional_usd = base_qty * mark
    position_id = f"reconcile_{venue}_{symbol}_{now_ts}"
    fill_id = f"{position_id}:open"
    instrument_id = f"{venue}:{symbol}"
    fill_side = "buy" if side == "long" else "sell"
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO positions "
            "(position_id, venue, symbol, underlying_group_id, signal_id, "
            " strategy_id, entry_strategy_id, active_strategy_id, side, qty, "
            " status, opened_ts, swap_count, deal_id) "
            "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, 'open', ?, 0, ?)",
            (
                position_id, venue, symbol, position_id,
                RECONCILE_STRATEGY_ID, RECONCILE_STRATEGY_ID,
                RECONCILE_STRATEGY_ID, side, base_qty, now_ts, deal_id,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fills "
            "(fill_id, venue, instrument_id, strategy_id, side, size_usd, "
            " fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
            " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, 0.0, 0, ?, ?, "
            "'filled')",
            (
                fill_id, venue, instrument_id, RECONCILE_STRATEGY_ID, fill_side,
                notional_usd, mark, now_ts * 1000, deal_id or "",
                position_id, base_qty, notional_usd,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.exception(
            "[reconcile] import failed for %s:%s — left untracked", venue, symbol
        )
        return None
    logger.info(
        "[reconcile] imported %s:%s side=%s qty=%.6f mark=%.6f notional=%.2f "
        "(entry=mark, PnL~0)",
        venue, symbol, side, base_qty, mark, notional_usd,
    )
    return SimulatedTrade(
        signal_id=position_id,
        venue=venue,
        symbol=symbol,
        strategy_id=RECONCILE_STRATEGY_ID,
        side=side,
        entry_price=mark,
        notional_usd=notional_usd,
        open_ts=now_ts,
        position_id=position_id,
        venue_order_id=deal_id,
        deal_id=deal_id if venue == "capital" else None,
        base_qty=base_qty,
    )


def _reconcile_alpaca(
    conn: sqlite3.Connection,
    adapter: _AlpacaLike,
    existing: set[tuple[str, str]],
    now_ts: int,
) -> list[SimulatedTrade]:
    out: list[SimulatedTrade] = []
    positions = _run_coro(adapter.fetch_positions())
    for pos in positions or []:
        symbol = str(pos.get("symbol") or "")
        if not symbol or ("alpaca", symbol) in existing:
            continue
        # current mark: prefer current_price, else market_value/qty.
        qty = abs(_f(pos.get("qty")))
        mark = _f(pos.get("current_price"))
        if mark <= 0.0:
            mv = _f(pos.get("market_value"))
            mark = (mv / qty) if qty > 0.0 else 0.0
        side = "short" if str(pos.get("side") or "long").lower() == "short" else "long"
        trade = _import_one(
            conn, venue="alpaca", symbol=symbol, side=side, mark=mark,
            base_qty=qty, now_ts=now_ts, deal_id=None,
        )
        if trade is not None:
            existing.add(("alpaca", symbol))
            out.append(trade)
    return out


def _reconcile_capital(
    conn: sqlite3.Connection,
    adapter: _CapitalLike,
    existing: set[tuple[str, str]],
    now_ts: int,
) -> list[SimulatedTrade]:
    out: list[SimulatedTrade] = []
    body = _run_coro(adapter.list_positions())
    for entry in body.get("positions", []) or []:
        pos = entry.get("position", {}) or {}
        market = entry.get("market", {}) or {}
        symbol = str(market.get("epic") or "")
        if not symbol or ("capital", symbol) in existing:
            continue
        direction = str(pos.get("direction") or "BUY").upper()
        side = "short" if direction == "SELL" else "long"
        base_qty = abs(_f(pos.get("size")))
        # current mark = mid(bid, offer); fall back to position level.
        bid = _f(market.get("bid"))
        offer = _f(market.get("offer"))
        mark = (bid + offer) / 2.0 if bid > 0.0 and offer > 0.0 else _f(pos.get("level"))
        deal_id = str(pos.get("dealId")) if pos.get("dealId") else None
        trade = _import_one(
            conn, venue="capital", symbol=symbol, side=side, mark=mark,
            base_qty=base_qty, now_ts=now_ts, deal_id=deal_id,
        )
        if trade is not None:
            existing.add(("capital", symbol))
            out.append(trade)
    return out


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def reconcile_venue_positions(
    conn: sqlite3.Connection,
    *,
    okx_adapter: Any | None,
    capital_adapter: _CapitalLike | None,
    alpaca_adapter: _AlpacaLike | None,
    now_ts: int,
    import_okx_spot: bool = False,
) -> list[SimulatedTrade]:
    """Import live venue positions NOT yet tracked in the DB (startup blind-spot).

    After a fresh-DB reset the bot starts FLAT while the real venues still hold
    positions (e.g. Alpaca AAVEUSD/INTC/ORCL/SPCE ~$44k). ``hydrate`` only reads
    the DB, so those live holdings are invisible to the exit engine. This pass
    fetches live positions and, for any ``(venue, symbol)`` not already OPEN in
    the DB, INSERTs a ``status='open'`` position row + a synthetic ENTRY fill at
    the **current mark** (``entry_price = current price`` ⇒ unrealized PnL starts
    ~0; ``fee_usd = 0``; NO fabricated cost basis). ``strategy_id`` is
    ``_reconcile_import`` so the position is managed by the exit engine normally.

    OKX SPOT dust is SKIPPED by default (``import_okx_spot=False``) — a SPOT
    wallet balance is fungible cash, not a discrete entry/exit-managed position.

    Returns the imported ``SimulatedTrade`` list (same shape as ``hydrate``) so
    the caller can ``state.open_trades.extend(...)``. ``None`` adapters are
    no-ops; a venue fetch failure is logged and skipped (flow_not_block) without
    aborting the other venues.
    """
    existing = _existing_keys(conn)
    out: list[SimulatedTrade] = []
    if alpaca_adapter is not None:
        try:
            out.extend(_reconcile_alpaca(conn, alpaca_adapter, existing, now_ts))
        except Exception:  # noqa: BLE001
            logger.exception("[reconcile] Alpaca fetch/import failed — skipped")
    if capital_adapter is not None:
        try:
            out.extend(_reconcile_capital(conn, capital_adapter, existing, now_ts))
        except Exception:  # noqa: BLE001
            logger.exception("[reconcile] Capital fetch/import failed — skipped")
    if import_okx_spot and okx_adapter is not None:
        logger.info("[reconcile] import_okx_spot=True is not implemented — SPOT "
                    "dust is fungible wallet balance, skipped")
    return out
