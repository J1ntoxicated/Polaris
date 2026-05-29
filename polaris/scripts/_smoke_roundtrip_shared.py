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
    """
    payload = json.dumps(
        {
            "venue": venue, "symbol": symbol, "side": side, "phase": phase,
            "venue_order_id": venue_order_id, "deal_id": deal_id,
            "base_qty": base_qty,
        },
        separators=(",", ":"),
    )
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
