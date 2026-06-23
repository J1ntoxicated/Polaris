"""Day 6 — shared helpers for real demo fill round-trip (OKX + Capital).

``record_venue_orphan`` (reconciliation durable record), ``resolve_okx_base_url``
(US-region enforcement), and the per-venue minimum-size constants. Split out so
the OKX module (``_smoke_real_roundtrip``) and the Capital module
(``_smoke_roundtrip_capital``) share them without a circular import.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass

from polaris.core.data.fill_normalizer import Fill

logger = logging.getLogger(__name__)

MIN_OKX_NOTIONAL_USD: float = 10.0  # OKX SPOT minimum (BTC-USDT minSz × px ≥ 5 USDT)
MIN_CAPITAL_LOT: float = 1.0  # Capital min_deal_size for FX majors


@dataclass(frozen=True, slots=True)
class OpenAttempt:
    """Result of a single real venue open leg (P0 wire).

    ``fill`` is the normalized entry fill on success, ``None`` on reject /
    no-fill. ``reject_code`` carries the venue reason code (OKX ``sCode`` like
    ``"51155"``/``"51008"``, Capital status, or the sentinel ``"no_fill"`` when
    the order was accepted but never filled) so the caller can classify an
    EXTERNAL venue event apart from an internal/client fault.
    """

    fill: Fill | None
    deal_id: str | None = None
    reject_code: str | None = None
    reject_msg: str | None = None
    # Set on an ACCEPTED-but-unfilled order (the venue holds a LIVE order). Lets
    # the caller hand the live order to ``record_venue_orphan`` instead of
    # silently releasing it (the Alpaca open-side orphan leak). ``None`` on a
    # genuine reject (nothing accepted at the venue → nothing to reconcile).
    venue_order_id: str | None = None
    # Submitted share qty for that live unfilled order (whole-share-retry qty),
    # carried so the orphan record knows the exposure to reconcile.
    unfilled_qty: float = 0.0


@dataclass(frozen=True, slots=True)
class PendingClose:
    """BUG E — a close order is LIVE at the venue but its fill is unconfirmed.

    Third semantic next to a ``Fill`` (settled) and ``None`` (no order remains
    → safe to re-fire next tick): the order may still execute, so the caller
    must NOT fire a duplicate close. It stores ``ref`` (Alpaca order_id /
    Capital close deal_reference) on the trade and CONFIRMS that ref first on
    the next tick. Pins the GOLD close-reject re-fire loop and the SELX
    missed-late-fill double sell. OKX market sells never survive to the next
    tick (a still-live order is cancelled + re-checked in-tick), so OKX never
    returns this.
    """

    ref: str


@dataclass(frozen=True, slots=True)
class CloseOrphan:
    """FIX 2 — a TRUE-ORPHAN close signal (venue-side state drift).

    Returned by ``real_okx_close_fill`` when the wallet's available base ccy is
    ~0 while the internal book still tracks ``base_qty`` (an over-count from the
    close-chunk fix). DISTINCT from a bare ``None`` (transient reject / no-fill →
    retry next tick): the caller marks the position ``status='reconciled'`` so
    the exit engine + hydrate STOP retrying it — WITHOUT fabricating a fill or
    PnL. State-drift recovery (flow_not_block), NOT a throttle / size dampen /
    P&L halt. ``available`` is the wallet balance observed (~0) for the audit.
    """

    available: float


@dataclass(frozen=True, slots=True)
class SiblingDrainClose:
    """Pooled-wallet drain — NOT a tracking failure (do NOT reconcile-with-NULL).

    Returned by ``real_okx_close_fill`` when the wallet's available base ccy is
    sub-min/~0 BUT one or more OTHER same-ccy positions are still open and their
    tracked base accounts for the missing pool. The OKX SPOT demo holds ONE
    fungible base-ccy balance shared by every concurrent same-ccy position; the
    first sibling to close drains the shared pool, so a later sibling sees
    ``availBal~0 < base_qty`` even though ITS base WAS real and WAS sold into the
    pool (wallet USDT proceeds are conserved). DISTINCT from ``CloseOrphan`` (a
    genuine over-count with NO sibling to explain the drain → reconcile, PnL
    NULL): this signals the caller to book a normal MARK-to-market close (real
    ``pnl_r``, learner feed) so the position is NOT dropped from the R ledger —
    removing the survivorship bias that orphaned ~32% of flow_pressure positions
    (heavily the late-closers/losers). flow_not_block, NOT a throttle.
    ``available`` is the observed pool remainder (~0) for the audit/close fill.
    """

    available: float


def record_venue_orphan(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    venue: str,
    symbol: str,
    side: str,
    phase: str,
    venue_order_id: str | None,
    deal_id: str | None,
    base_qty: float,
    now_ts: int,
) -> None:
    """Durably record an untracked real venue position for reconciliation.

    Fired when a real fill landed at the venue but the local bookkeeping
    (confirm_reservation / persist) then failed — leaving a live position with
    no matching internal row. Reuses the existing ``risk_events`` table
    (``event_type='venue_orphan'``) so a reconciliation pass can find the
    venue ref (OKX ``ordId`` / Capital ``dealId`` + ``base_qty``) and close
    the orphaned exposure. Best-effort: a failure here is logged, never raised.

    IDEMPOTENT on ``venue_order_id``: the same live order can be handed in more
    than once (concurrency — two strategies, same symbol, same tick — or a
    re-fired no_fill), and a reconciler must see ONE row per live order, not a
    duplicate flood. A non-``None`` ``venue_order_id`` already recorded is a
    no-op. ``None`` (no venue ref) skips the dedup (nothing to key on).
    """
    payload = json.dumps(
        {
            "venue": venue, "symbol": symbol, "side": side, "phase": phase,
            "venue_order_id": venue_order_id, "deal_id": deal_id,
            "base_qty": base_qty,
        },
        separators=(",", ":"),
    )
    if venue_order_id is not None:
        try:
            existing = conn.execute(
                "SELECT 1 FROM risk_events WHERE event_type='venue_orphan' "
                "AND json_extract(payload_json, '$.venue_order_id') = ? LIMIT 1",
                (venue_order_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            existing = None
            logger.error("[venue-orphan] dedup lookup failed: %r", exc)
        if existing is not None:
            logger.info(
                "[venue-orphan] %s:%s venue_order_id=%s already recorded — "
                "skip duplicate", venue, symbol, venue_order_id,
            )
            return
    logger.error(
        "[venue-orphan] %s:%s %s phase=%s venue_order_id=%s deal_id=%s "
        "base_qty=%s — real position untracked, needs reconciliation",
        venue, symbol, side, phase, venue_order_id, deal_id, base_qty,
    )
    try:
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, 'venue_orphan', ?, ?)",
            (uuid.uuid4().hex, strategy_id, now_ts, payload),
        )
    except sqlite3.Error as exc:
        logger.error("[venue-orphan] durable record failed: %r", exc)


def resolve_okx_base_url(env_value: str | None) -> str:
    """Force the OKX base URL onto the US region for Jin's demo keys.

    Codex Day 5 fix ([[feedback_okx_region_endpoint]]): keys live on
    ``us.okx.com``; if `.env` ships ``www.okx.com`` (international) we
    must override before the adapter constructs its ``httpx.AsyncClient``.

    Codex Day 7 R3 nit: parse with ``urllib.parse`` and require the
    netloc to **equal** ``us.okx.com`` (or be a sub-domain ending in
    ``.us.okx.com``) so malformed values like ``https://us.okx.com.evil``
    or ``https://evil.example/us.okx.com`` cannot bypass the override.
    """
    from urllib.parse import urlparse

    if not env_value:
        return "https://us.okx.com"
    parsed = urlparse(env_value)
    host = (parsed.netloc or "").lower().split(":")[0]  # strip optional port
    if host == "us.okx.com" or host.endswith(".us.okx.com"):
        return env_value
    return "https://us.okx.com"
