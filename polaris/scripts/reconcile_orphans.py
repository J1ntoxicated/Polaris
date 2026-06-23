"""Unified cross-venue orphan reconcile + EOD flatten (OKX + Capital + Alpaca).

Two complementary position-lifecycle passes, both DEMO/PAPER bot hygiene Jin
directed ('리콘은 다 만들어 … 청산이 기본 베이스' — build ALL reconciliation, and
LIQUIDATION IS THE DEFAULT before market close). flow_not_block: neither blocks an
entry nor cuts size — they reconcile orphans and flatten before close.

(1) ``reconcile_venue_orphans`` — the orphan-LIQUIDATION sibling of the import-side
    ``reconcile_venue_positions`` (recover.py). Import = pull untracked venue
    holdings INTO management; reconcile-orphans = CLOSE untracked venue holdings the
    bot will never manage. Per venue it runs the SAME proven 3-step guard shipped in
    ``liquidate_alpaca_orphans``: read the venue book → compute the tracked-open set
    (``positions WHERE venue=? AND status IN ('open','active')``, RE-READ every sweep,
    never cached) → close ONLY positions whose key is NOT in that set, idempotently.
      * ALPACA — lift ``liquidate_alpaca_orphans`` (RTH-gated, idempotent, audited).
      * OKX — sell untracked altcoin SPOT wallet bags to USDT, ADDING the tracked
        guard the one-off script lacked (a tracked ``{ccy}-USDT`` long is never sold).
      * CAPITAL — NEW: close an untracked orphan via ``close_position(dealId)``
        (Capital closes by dealId, the natural idempotent key).

(2) ``flatten_venue_eod`` — before each venue's market close, flatten the TRACKED
    open positions for that venue (the INVERSE set from #1) EXCEPT those flagged
    hold-overnight. DEFAULT = flatten. OKX is HARD-EXEMPT (24/7 crypto, no close) via
    ``EOD_EXEMPT_VENUES``. Reuses the exit-engine close legs so the R-ledger stays
    honest (``exit_reason='eod_flatten'`` is just a regularly-triggered exit).

GUARDS (load-bearing): tracked-set guard (never close a live position); idempotent
keys / status-flip (never double-submit); never close/flatten blind on a venue read
error (skip + log); market-closed → no blind queue (defer); OKX EOD-exempt assertion;
hold defaults to flatten (unknown strategy → flatten).
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Final

from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.strategies import STRATEGY_REGISTRY
from polaris.venues.capital.opening_hours import seconds_to_next_close
from polaris.venues.capital.session_calendar import capital_seconds_to_close

# Capital ``instrument_type`` that means 24/5 FX — the per-epic probe is exempt
# (its Friday-weekly close stays owned by the legacy ``capital_seconds_to_close``
# FX branch). The discriminator is TYPE, not the intraday gap length.
_CAPITAL_FX_INSTRUMENT_TYPE: Final[str] = "CURRENCIES"

logger = logging.getLogger(__name__)

__all__ = [
    "EOD_EXEMPT_VENUES",
    "flatten_venue_eod",
    "reconcile_venue_orphans",
]

# OKX crypto SPOT is 24/7 — there is NO market close, so it is HARD-EXEMPT from
# EOD flatten. Asserted as a constant the flatten path checks so a future edit
# cannot accidentally flatten 24/7 crypto at a fabricated "close". OKX
# participates ONLY in orphan reconcile (#1), never in EOD flatten (#2).
EOD_EXEMPT_VENUES: Final[frozenset[str]] = frozenset({"okx"})

# OKX dust floor (USD): a wallet bag worth less than this is left untouched.
_OKX_DUST_MIN_USD: Final[float] = 5.0
# Stable quote/cash currencies on OKX that are never "orphan bags" to sell.
_OKX_QUOTE_CCYS: Final[frozenset[str]] = frozenset({"USDT", "USD", "USDC"})


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _tracked_open_symbols(conn: sqlite3.Connection, venue: str) -> set[str]:
    """The venue's live (open/active) symbols — NEVER liquidated/closed out of turn.

    Re-read at RUN TIME every sweep (never cached): a closed/reconciled row does
    NOT protect a same-symbol orphan (load-bearing invariant).
    """
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM positions "
        "WHERE venue = ? AND status IN ('open', 'active')",
        (venue,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _audit(conn: sqlite3.Connection, *, event_type: str, strategy_id: str,
           now_ts: int, payload: dict[str, Any]) -> None:
    """Best-effort risk_events audit row (a write failure never aborts a sweep)."""
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, strategy_id, event_type, now_ts,
             json.dumps(payload, separators=(",", ":"))),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# (1) ORPHAN RECONCILE — per-venue branches
# ---------------------------------------------------------------------------


async def _reconcile_alpaca(
    conn: sqlite3.Connection, adapter: Any, *, dry_run: bool, now_ts: int,
) -> int:
    """Close every UNTRACKED Alpaca venue position (lifted guard contract)."""
    try:
        positions = await adapter.fetch_positions()
    except Exception as exc:  # noqa: BLE001 — read failed → skip, never blind
        logger.warning("[reconcile/alpaca] fetch_positions failed %r — skip", exc)
        return 0
    if not positions:
        return 0
    tracked = _tracked_open_symbols(conn, "alpaca")
    orphans: list[tuple[str, float, bool]] = []
    for pos in positions:
        symbol = str(pos.get("symbol") or "")
        qty = _f(pos.get("qty"))
        if not symbol or qty == 0.0:
            continue
        if symbol in tracked:
            logger.info("[reconcile/alpaca] %s TRACKED — not closed", symbol)
            continue
        is_crypto = str(pos.get("asset_class") or "").lower() == "crypto"
        orphans.append((symbol, qty, is_crypto))
    if not orphans:
        return 0
    if dry_run:
        for symbol, qty, _c in orphans:
            logger.info("[reconcile/alpaca] would-close %s qty=%.9f", symbol, qty)
        return len(orphans)

    equity_open = await _alpaca_market_open(adapter)
    closed = 0
    for symbol, qty, is_crypto in orphans:
        if not is_crypto and not equity_open:
            logger.info("[reconcile/alpaca] %s equity closed — defer", symbol)
            continue
        side = "sell" if qty > 0.0 else "buy"
        close_qty = abs(qty)
        tif = "gtc" if is_crypto else "day"
        cl = f"polAliq{uuid.uuid4().hex[:10]}"
        try:
            resp = await adapter.place_market_order(
                symbol=symbol, side=side, qty=close_qty,
                client_order_id=cl, time_in_force=tif,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per symbol
            logger.warning("[reconcile/alpaca] %s close EXC %r — skip", symbol, exc)
            continue
        if not getattr(resp, "ok", False) or not getattr(resp, "venue_order_id", None):
            logger.warning("[reconcile/alpaca] %s close rejected — skip", symbol)
            continue
        closed += 1
        _audit(conn, event_type="alpaca_orphan_liquidated", strategy_id="equity",
               now_ts=now_ts, payload={
                   "venue": "alpaca", "symbol": symbol, "side": side,
                   "qty": close_qty,
                   "venue_order_id": str(getattr(resp, "venue_order_id", "")),
                   "reason": "untracked venue orphan liquidated"})
        logger.warning("[reconcile/alpaca] %s CLOSED %s qty=%.9f (orphan)",
                       symbol, side, close_qty)
    return closed


async def _alpaca_market_open(adapter: Any) -> bool:
    """True iff the US equity session is open; on any clock error → CLOSED."""
    try:
        clock = await adapter.fetch_clock()
    except Exception as exc:  # noqa: BLE001 — closed-default on error
        logger.warning("[reconcile/alpaca] clock fetch failed %r — closed", exc)
        return False
    return bool(getattr(clock, "is_open", False))


async def _reconcile_okx(
    conn: sqlite3.Connection, adapter: Any, *, dry_run: bool, now_ts: int,
) -> int:
    """Sell every UNTRACKED non-USDT SPOT wallet bag to USDT (tracked guard ADDED).

    OKX SPOT holds no discrete positions — orphans are altcoin balances in
    ``fetch_balance().details``. The NEW guard: skip any ccy whose ``{ccy}-USDT``
    symbol is in the OKX tracked-open set (a live tracked SPOT long is never sold
    from under the exit engine). Dust (< $5) is left as-is.
    """
    try:
        bal = await adapter.fetch_balance()
    except Exception as exc:  # noqa: BLE001 — read failed → skip, never blind
        logger.warning("[reconcile/okx] fetch_balance failed %r — skip", exc)
        return 0
    try:
        details = bal.get("data", [{}])[0].get("details", []) or []
    except (AttributeError, IndexError, TypeError):
        details = []
    if not details:
        return 0
    tracked = _tracked_open_symbols(conn, "okx")
    targets: list[tuple[str, str, float, float]] = []
    for c in details:
        ccy = str(c.get("ccy") or "")
        if not ccy or ccy in _OKX_QUOTE_CCYS:
            continue
        avail = _f(c.get("availBal"))
        eq_usd = _f(c.get("eqUsd"))
        if avail <= 0.0 or eq_usd < _OKX_DUST_MIN_USD:
            continue
        inst = f"{ccy}-USDT"
        if inst in tracked:
            # TRACKED guard (the one the one-off script lacked).
            logger.info("[reconcile/okx] %s TRACKED — not sold", inst)
            continue
        targets.append((ccy, inst, avail, eq_usd))
    if not targets:
        return 0
    if dry_run:
        for _ccy, inst, avail, _eq in targets:
            logger.info("[reconcile/okx] would-sell %s avail=%.8f", inst, avail)
        return len(targets)

    closed = 0
    for ccy, inst, avail, eq_usd in targets:
        try:
            resp = await adapter.place_market_order(
                inst_id=inst, side="sell", notional_usd=avail,
                client_order_id=f"polOliq{ccy}{uuid.uuid4().hex[:8]}",
                ord_type="market", tgt_ccy="base_ccy",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per coin
            logger.warning("[reconcile/okx] %s sell EXC %r — skip", inst, exc)
            continue
        if not getattr(resp, "ok", False):
            logger.warning("[reconcile/okx] %s sell rejected code=%s — skip",
                          inst, getattr(resp, "code", "?"))
            continue
        closed += 1
        _audit(conn, event_type="okx_orphan_liquidated", strategy_id="spot",
               now_ts=now_ts, payload={
                   "venue": "okx", "symbol": inst, "side": "sell",
                   "qty": avail, "eq_usd": eq_usd,
                   "venue_order_id": str(getattr(resp, "venue_order_id", "")),
                   "reason": "untracked spot wallet bag sold to USDT"})
        logger.warning("[reconcile/okx] %s SOLD avail=%.8f (~$%.2f orphan)",
                       inst, avail, eq_usd)
    return closed


async def _reconcile_capital(
    conn: sqlite3.Connection, adapter: Any, *, dry_run: bool, now_ts: int,
) -> int:
    """Close every UNTRACKED Capital orphan by dealId (NEW branch).

    tracked-open set keyed by EPIC (``positions.symbol`` stores the epic). Close
    via ``close_position(dealId)`` — Capital closes by dealId, not qty/side; the
    dealId is the natural idempotent key (a closed dealId returns a benign reject).
    """
    try:
        body = await adapter.list_positions()
    except Exception as exc:  # noqa: BLE001 — read failed → skip, never blind
        logger.warning("[reconcile/capital] list_positions failed %r — skip", exc)
        return 0
    rows = body.get("positions", []) or [] if isinstance(body, dict) else []
    if not rows:
        return 0
    tracked = _tracked_open_symbols(conn, "capital")
    orphans: list[tuple[str, str]] = []  # (epic, deal_id)
    for entry in rows:
        pos = entry.get("position", {}) or {}
        market = entry.get("market", {}) or {}
        epic = str(market.get("epic") or "")
        deal_id = str(pos.get("dealId") or "")
        if not epic or not deal_id:
            continue
        if epic in tracked:
            logger.info("[reconcile/capital] %s TRACKED — not closed", epic)
            continue
        orphans.append((epic, deal_id))
    if not orphans:
        return 0
    if dry_run:
        for epic, deal_id in orphans:
            logger.info("[reconcile/capital] would-close %s deal=%s", epic, deal_id)
        return len(orphans)

    closed = 0
    for epic, deal_id in orphans:
        try:
            resp = await adapter.close_position(deal_id)
        except Exception as exc:  # noqa: BLE001 — best-effort per deal
            logger.warning("[reconcile/capital] %s close EXC %r — skip", epic, exc)
            continue
        if not getattr(resp, "ok", False):
            logger.warning("[reconcile/capital] %s close rejected status=%s — skip",
                          epic, getattr(resp, "status", "?"))
            continue
        closed += 1
        _audit(conn, event_type="capital_orphan_liquidated", strategy_id="cfd",
               now_ts=now_ts, payload={
                   "venue": "capital", "symbol": epic, "deal_id": deal_id,
                   "reason": "untracked venue orphan closed by dealId"})
        logger.warning("[reconcile/capital] %s CLOSED deal=%s (orphan)", epic, deal_id)
    return closed


async def reconcile_venue_orphans(
    conn: sqlite3.Connection,
    *,
    okx_adapter: Any | None,
    capital_adapter: Any | None,
    alpaca_adapter: Any | None,
    dry_run: bool = False,
    now_ts: int | None = None,
) -> dict[str, int]:
    """Close every UNTRACKED venue holding across all 3 venues; return per-venue counts.

    Per-venue try/except isolation: a failure on one venue (read error / session
    expiry) skips ONLY that venue (count 0) and never blind-closes; the other
    venues still sweep. A ``None`` adapter is skipped (count 0). ``dry_run`` logs
    would-close without submitting. Best-effort + idempotent: a per-item reject is
    logged + skipped, the sweep never aborts (flow_not_block).
    """
    now = now_ts if now_ts is not None else int(time.time())
    counts: dict[str, int] = {"okx": 0, "capital": 0, "alpaca": 0}
    if alpaca_adapter is not None:
        try:
            counts["alpaca"] = await _reconcile_alpaca(
                conn, alpaca_adapter, dry_run=dry_run, now_ts=now)
        except Exception:
            logger.exception("[reconcile/alpaca] sweep failed — venue skipped")
    if okx_adapter is not None:
        try:
            counts["okx"] = await _reconcile_okx(
                conn, okx_adapter, dry_run=dry_run, now_ts=now)
        except Exception:
            logger.exception("[reconcile/okx] sweep failed — venue skipped")
    if capital_adapter is not None:
        try:
            counts["capital"] = await _reconcile_capital(
                conn, capital_adapter, dry_run=dry_run, now_ts=now)
        except Exception:
            logger.exception("[reconcile/capital] sweep failed — venue skipped")
    total = sum(counts.values())
    if total:
        logger.warning(
            "[reconcile] cross-venue orphan sweep closed %d "
            "(okx=%d capital=%d alpaca=%d)%s",
            total, counts["okx"], counts["capital"], counts["alpaca"],
            " [DRY-RUN]" if dry_run else "",
        )
    return counts


# ---------------------------------------------------------------------------
# (2) EOD FLATTEN
# ---------------------------------------------------------------------------


def _is_held_overnight(trade: SimulatedTrade) -> bool:
    """Resolve a tracked position's hold-overnight flag (DEFAULT = flatten).

    True ONLY if its strategy's ``metadata.hold_overnight`` is True. An unknown /
    ``_reconcile_import`` strategy_id → False (flatten — the directive's base case).
    """
    cls = STRATEGY_REGISTRY.get(trade.strategy_id)
    if cls is None:
        return False
    return bool(getattr(getattr(cls, "metadata", None), "hold_overnight", False))


def _strategy_asset_class(strategy_id: str) -> str:
    """The strategy's asset_class (forex/index/commodity/equity/spot), or '' if
    unknown — used to route a Capital position to its session-close calendar."""
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return ""
    return str(getattr(getattr(cls, "metadata", None), "asset_class", "") or "")


def _capital_seconds_to_close(
    state: ProdLoopState, trade: SimulatedTrade, now: int
) -> tuple[int | None, str]:
    """Seconds to this Capital epic's next close + the source label.

    PRIMARY = the per-epic ``openingHours`` probe (recovers the real per-epic
    anchor — US500 21:00 vs GOLD 20:59 — instead of the lumped asset-class time).
    FALLBACK = the legacy ``capital_seconds_to_close`` asset-class table, used
    EXACTLY when the constraint is not yet cached (lazy cold-miss) OR carries no
    parsed ``opening_hours`` (absent / non-UTC / unparseable). Both return the
    SAME int≥0|None defer contract, so the caller's lead/None branch is untouched
    and the cold-miss path is byte-identical to today.
    """
    constraint = state.capital_constraints.peek(trade.symbol)  # zero-I/O
    if constraint is not None and constraint.opening_hours is not None:
        secs = seconds_to_next_close(
            constraint.opening_hours, now,
            is_fx=(constraint.instrument_type == _CAPITAL_FX_INSTRUMENT_TYPE),
        )
        return secs, "opening_hours"
    asset_class = _strategy_asset_class(trade.strategy_id)
    return capital_seconds_to_close(asset_class, now), "asset_class_fallback"


async def _seconds_to_close_alpaca(adapter: Any, now_ts: int) -> int | None:
    """Seconds to the Alpaca next_close, or None if the session is closed / the
    clock errored (→ never flatten blind)."""
    try:
        clock = await adapter.fetch_clock()
    except Exception as exc:  # noqa: BLE001 — outage → skip, never blind
        logger.warning("[eod/alpaca] clock fetch failed %r — skip", exc)
        return None
    if not bool(getattr(clock, "is_open", False)):
        # Already closed → a market order would only queue; defer to next open.
        return None
    next_close = str(getattr(clock, "next_close", "") or "")
    if not next_close:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(next_close.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp()) - now_ts


async def flatten_venue_eod(
    conn: sqlite3.Connection,
    state: ProdLoopState,
    *,
    alpaca_adapter: Any | None,
    capital_adapter: Any | None,
    lead_sec: int,
    dry_run: bool = False,
    now_ts: int | None = None,
) -> int:
    """Flatten TRACKED open positions for venues inside their EOD close window.

    DEFAULT = flatten. Operates on ``state.open_trades`` (the inverse set from the
    orphan reconcile) filtered to venues in {alpaca, capital}. OKX is HARD-EXEMPT
    (``EOD_EXEMPT_VENUES`` — 24/7 crypto). Per position: (1) skip if held_overnight;
    (2) skip if its venue is not within ``lead_sec`` of close; (3) otherwise submit a
    real venue CLOSE (Alpaca opposite-side market at base_qty; Capital
    ``close_position(dealId)``), flip the trade to closed so a re-tick is a no-op.

    GUARDS: a clock error / closed session → submit nothing (never flatten blind);
    OKX never enters this path; hold defaults to flatten. Returns the count flattened.
    """
    now = now_ts if now_ts is not None else int(time.time())
    # Cache the Alpaca window decision once per call (the clock is cached on the
    # adapter, but this also avoids re-fetching per position).
    alpaca_secs: int | None = None
    alpaca_checked = False

    flattened = 0
    for trade in list(state.open_trades):
        venue = trade.venue
        if venue in EOD_EXEMPT_VENUES:
            continue  # OKX 24/7 — never EOD-flattened.
        if venue not in ("alpaca", "capital"):
            continue
        if getattr(trade, "closed", False):
            continue
        if _is_held_overnight(trade):
            logger.info("[eod] %s/%s held_overnight — not flattened",
                       venue, trade.symbol)
            continue

        if venue == "alpaca":
            if alpaca_adapter is None:
                continue
            if not alpaca_checked:
                alpaca_secs = await _seconds_to_close_alpaca(alpaca_adapter, now)
                alpaca_checked = True
            if alpaca_secs is None or alpaca_secs > lead_sec:
                continue
            ok = await _flatten_one_alpaca(
                conn, alpaca_adapter, trade, dry_run=dry_run, now_ts=now)
        else:  # capital
            if capital_adapter is None:
                continue
            # PRIMARY = per-epic openingHours probe; asset-class table fallback.
            secs, close_source = _capital_seconds_to_close(state, trade, now)
            if secs is None or secs > lead_sec:
                continue
            ok = await _flatten_one_capital(
                conn, capital_adapter, trade, dry_run=dry_run, now_ts=now,
                close_source=close_source)
        if ok:
            flattened += 1
            if not dry_run:
                trade.closed = True  # idempotent: a re-tick skips this position.
    if flattened:
        logger.warning("[eod] flattened %d tracked position(s) before close%s",
                       flattened, " [DRY-RUN]" if dry_run else "")
    return flattened


async def _flatten_one_alpaca(
    conn: sqlite3.Connection, adapter: Any, trade: SimulatedTrade,
    *, dry_run: bool, now_ts: int,
) -> bool:
    """Flatten one tracked Alpaca position via an opposite-side market close."""
    qty = abs(_f(trade.base_qty))
    if qty <= 0.0:
        return False
    side = "sell" if trade.side == "long" else "buy"
    if dry_run:
        logger.info("[eod/alpaca] would-flatten %s %s qty=%.9f",
                   trade.symbol, side, qty)
        return True
    try:
        resp = await adapter.place_market_order(
            symbol=trade.symbol, side=side, qty=qty,
            client_order_id=f"polAeod{uuid.uuid4().hex[:10]}", time_in_force="day",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort per position
        logger.warning("[eod/alpaca] %s flatten EXC %r — skip", trade.symbol, exc)
        return False
    if not getattr(resp, "ok", False) or not getattr(resp, "venue_order_id", None):
        logger.warning("[eod/alpaca] %s flatten rejected — skip", trade.symbol)
        return False
    _audit(conn, event_type="eod_flatten", strategy_id=trade.strategy_id,
           now_ts=now_ts, payload={
               "venue": "alpaca", "symbol": trade.symbol, "side": side, "qty": qty,
               "venue_order_id": str(getattr(resp, "venue_order_id", "")),
               "exit_reason": "eod_flatten"})
    logger.warning("[eod/alpaca] %s FLATTENED %s qty=%.9f (eod, default)",
                   trade.symbol, side, qty)
    return True


async def _flatten_one_capital(
    conn: sqlite3.Connection, adapter: Any, trade: SimulatedTrade,
    *, dry_run: bool, now_ts: int, close_source: str = "asset_class_fallback",
) -> bool:
    """Flatten one tracked Capital position via close_position(dealId).

    ``close_source`` records which close-time signal fired (``opening_hours`` =
    per-epic PRIMARY, ``asset_class_fallback`` = legacy table) for observability.
    """
    deal_id = trade.deal_id
    if not deal_id:
        logger.warning("[eod/capital] %s has no deal_id — cannot flatten", trade.symbol)
        return False
    if dry_run:
        logger.info("[eod/capital] would-flatten %s deal=%s", trade.symbol, deal_id)
        return True
    try:
        resp = await adapter.close_position(deal_id)
    except Exception as exc:  # noqa: BLE001 — best-effort per position
        logger.warning("[eod/capital] %s flatten EXC %r — skip", trade.symbol, exc)
        return False
    if not getattr(resp, "ok", False):
        logger.warning("[eod/capital] %s flatten rejected status=%s — skip",
                      trade.symbol, getattr(resp, "status", "?"))
        return False
    _audit(conn, event_type="eod_flatten", strategy_id=trade.strategy_id,
           now_ts=now_ts, payload={
               "venue": "capital", "symbol": trade.symbol, "deal_id": deal_id,
               "exit_reason": "eod_flatten", "close_source": close_source})
    logger.warning("[eod/capital] %s FLATTENED deal=%s (eod, default)",
                   trade.symbol, deal_id)
    return True
