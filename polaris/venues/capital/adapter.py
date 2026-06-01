"""Capital CFD adapter — REST bar fetch + canonical Bar conversion + lifecycle.

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Q1 bars table)
- vault/10_decisions/ADR-003-8-layer-architecture.md (REST polling P0)

Endpoints
---------
Public (uses CST + X-SECURITY-TOKEN once logged in):
- ``GET  /api/v1/prices/{epic}`` — historical candles
- ``POST /api/v1/positions``     — open market position (with optional SL/TP)
- ``POST /api/v1/workingorders`` — pending LIMIT/STOP order
- ``DELETE /api/v1/positions/{dealId}`` — close
- ``GET  /api/v1/positions``     — list open
- ``GET  /api/v1/confirms/{dealReference}`` — fill confirmation
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.data.schema import BAR_INTERVALS, Bar
from polaris.venues.capital.session import CapitalSession

logger = logging.getLogger(__name__)

__all__ = [
    "CAPITAL_BASE_DEMO",
    "CAPITAL_CONFIRMS_PATH",
    "CAPITAL_POSITIONS_PATH",
    "CAPITAL_PRICES_PATH",
    "CAPITAL_WORKINGORDERS_PATH",
    "CapitalAdapter",
    "CapitalDealResponse",
    "capital_price_row_to_bar",
    "fetch_capital_bars",
]

CAPITAL_BASE_DEMO: Final[str] = "https://demo-api-capital.backend-capital.com"
CAPITAL_PRICES_PATH: Final[str] = "/api/v1/prices"
CAPITAL_POSITIONS_PATH: Final[str] = "/api/v1/positions"
CAPITAL_WORKINGORDERS_PATH: Final[str] = "/api/v1/workingorders"
CAPITAL_CONFIRMS_PATH: Final[str] = "/api/v1/confirms"

REST_TIMEOUT_SEC: Final[float] = 15.0

# Capital resolution token → canonical bar_interval.
RESOLUTION_TO_INTERVAL: Final[dict[str, str]] = {
    "MINUTE": "1m",
    "MINUTE_5": "5m",
    "MINUTE_15": "15m",
    "HOUR": "1H",
}


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return f


def _mid(price_block: Any) -> float:
    """Capital prices are usually ``{"bid": x, "ask": y}`` — return mid."""
    if not isinstance(price_block, dict):
        return _to_float(price_block)
    bid = _to_float(price_block.get("bid"))
    ask = _to_float(price_block.get("ask"))
    if bid > 0 and ask > 0:
        return 0.5 * (bid + ask)
    return bid if bid > 0 else ask


def _parse_capital_ts(value: Any) -> int:
    """Capital bar ts is ISO-8601 string. Return seconds-epoch (UTC).

    The caller passes ``snapshotTimeUTC`` (the genuinely-UTC field), which is
    tz-naive in the payload. We force UTC explicitly so ``timestamp()`` does
    not reinterpret it in the host tz (verified 2026-06-01: account tz = AEST
    / UTC+10; the sibling ``snapshotTime`` field is account-local and would
    shift every bar +10h — see ``capital_price_row_to_bar``).
    """
    from datetime import datetime

    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return 0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def capital_price_row_to_bar(
    row: dict[str, Any],
    *,
    epic: str,
    bar_interval: str,
    underlying_group_id: str,
) -> Bar | None:
    """Convert one Capital `/prices` row to canonical Bar (or None if invalid)."""
    if bar_interval not in BAR_INTERVALS:
        return None
    # Prefer ``snapshotTimeUTC`` (genuine UTC). The sibling ``snapshotTime`` is
    # account-local (AEST/UTC+10 on this account) and, parsed as UTC, dates every
    # bar +10h into the FUTURE — which polluted bars MAX(ts) and made the
    # dashboard look stale. Verified live 2026-06-01: wall-clock 15:00 UTC →
    # snapshotTime "…T01:00:00" (+10h), snapshotTimeUTC "…T15:00:00" (correct).
    ts = _parse_capital_ts(row.get("snapshotTimeUTC") or row.get("snapshotTime"))
    if ts <= 0:
        return None
    o = _mid(row.get("openPrice"))
    h = _mid(row.get("highPrice"))
    low = _mid(row.get("lowPrice"))
    c = _mid(row.get("closePrice"))
    if o <= 0.0 or c <= 0.0:
        return None
    vol = _to_float(row.get("lastTradedVolume"))
    return Bar(
        instrument_id=f"capital:{epic}",
        underlying_group_id=underlying_group_id,
        venue="capital",
        symbol=epic,
        bar_interval=bar_interval,
        ts=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=vol,
        notional_usd=c * vol if vol > 0 else 0.0,
        trade_count=0,
        vwap=0.0,
        bid_close=_to_float(row.get("closePrice", {}).get("bid") if isinstance(row.get("closePrice"), dict) else 0.0),
        ask_close=_to_float(row.get("closePrice", {}).get("ask") if isinstance(row.get("closePrice"), dict) else 0.0),
        spread_bps_close=0.0,
        source="capital_rest",
    )


async def fetch_capital_bars(
    epic: str,
    *,
    cst: str,
    security_token: str,
    bar_interval: str = "1m",
    resolution: str = "MINUTE",
    limit: int = 300,
    base_url: str = CAPITAL_BASE_DEMO,
    asset_class: str = "forex",
    client: httpx.AsyncClient | None = None,
) -> list[Bar]:
    """Fetch up to ``limit`` candles for ``epic`` from Capital demo REST.

    Demo session creds (CST + X-SECURITY-TOKEN) must be obtained via /session
    by the caller. The function emits canonical Bars.
    """
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            f"{CAPITAL_PRICES_PATH}/{epic}",
            params={"resolution": resolution, "max": str(limit)},
            headers={"CST": cst, "X-SECURITY-TOKEN": security_token},
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await cli.aclose()
    rows = list(body.get("prices", []))
    underlying = compute_underlying_group_id("capital", epic, asset_class=asset_class)
    out: list[Bar] = []
    for row in rows:
        bar = capital_price_row_to_bar(
            row, epic=epic, bar_interval=bar_interval, underlying_group_id=underlying
        )
        if bar is not None:
            out.append(bar)
    return out


# ---------------------------------------------------------------------------
# Authenticated CFD lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapitalDealResponse:
    """Normalized response for ``POST /positions`` and ``DELETE /positions/{id}``."""

    ok: bool
    deal_reference: str | None
    deal_id: str | None
    status: str  # "OPEN" / "CLOSED" / "REJECTED" / "PENDING"
    reason: str
    raw: dict[str, Any]


class CapitalAdapter:
    """Authenticated Capital CFD wrapper bound to a ``CapitalSession``."""

    def __init__(self, session: CapitalSession) -> None:
        self.session = session

    @property
    def base_url(self) -> str:
        return self.session.base_url

    async def open_position(
        self,
        *,
        epic: str,
        direction: str,
        size: float,
        stop_distance: float | None = None,
        limit_distance: float | None = None,
        guaranteed_stop: bool = False,
    ) -> CapitalDealResponse:
        dir_u = direction.upper()
        if dir_u not in ("BUY", "SELL"):
            raise ValueError(f"direction must be BUY or SELL, got {direction!r}")
        body: dict[str, Any] = {
            "epic": epic,
            "direction": dir_u,
            "size": size,
            "guaranteedStop": guaranteed_stop,
            "forceOpen": True,
        }
        if stop_distance is not None:
            body["stopDistance"] = stop_distance
        if limit_distance is not None:
            body["limitDistance"] = limit_distance
        # Security: tokens (CST / X-SECURITY-TOKEN) are NEVER logged.
        logger.info(
            "[capital] open_position epic=%s direction=%s size=%.4f stop=%s limit=%s gtd_stop=%s",
            epic,
            dir_u,
            size,
            stop_distance if stop_distance is not None else "-",
            limit_distance if limit_distance is not None else "-",
            guaranteed_stop,
        )
        resp = await self.session.request("POST", CAPITAL_POSITIONS_PATH, json_body=body)
        parsed = _parse_deal_response(resp)
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[capital] open RESP ok=%s status=%s dealRef=%s dealId=%s reason=%s",
            parsed.ok,
            parsed.status,
            parsed.deal_reference,
            parsed.deal_id,
            parsed.reason[:160] if parsed.reason else "-",
        )
        return parsed

    async def place_working_order(
        self,
        *,
        epic: str,
        direction: str,
        size: float,
        level: float,
        order_type: str = "LIMIT",
        time_in_force: str = "GOOD_TILL_CANCELLED",
    ) -> CapitalDealResponse:
        body = {
            "epic": epic,
            "direction": direction.upper(),
            "size": size,
            "level": level,
            "type": order_type.upper(),
            "timeInForce": time_in_force,
        }
        resp = await self.session.request("POST", CAPITAL_WORKINGORDERS_PATH, json_body=body)
        return _parse_deal_response(resp)

    async def close_position(self, deal_id: str) -> CapitalDealResponse:
        logger.info("[capital] close_position dealId=%s", deal_id)
        resp = await self.session.request(
            "DELETE", f"{CAPITAL_POSITIONS_PATH}/{deal_id}"
        )
        parsed = _parse_deal_response(resp)
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[capital] close RESP ok=%s status=%s dealRef=%s reason=%s",
            parsed.ok,
            parsed.status,
            parsed.deal_reference,
            parsed.reason[:160] if parsed.reason else "-",
        )
        return parsed

    async def list_positions(self) -> dict[str, Any]:
        resp = await self.session.request("GET", CAPITAL_POSITIONS_PATH)
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def confirm(self, deal_reference: str) -> dict[str, Any]:
        resp = await self.session.request(
            "GET", f"{CAPITAL_CONFIRMS_PATH}/{deal_reference}"
        )
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, dict) else {}


def _parse_deal_response(resp: httpx.Response) -> CapitalDealResponse:
    """Parse Capital deal endpoint response into a normalized struct.

    Capital responds 200 even for REJECTED deals; the actual outcome lives in
    the confirm payload (or ``dealStatus`` on the position payload).
    """
    raw: dict[str, Any] = {}
    body = resp.text
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            raw = parsed
    except (ValueError, TypeError):
        raw = {"raw_text": body}
    deal_ref = raw.get("dealReference")
    deal_id = raw.get("dealId")
    deal_status = str(raw.get("dealStatus") or raw.get("status") or "PENDING").upper()
    reason = str(raw.get("reason") or "")
    ok = resp.status_code == 200 and deal_status not in {"REJECTED", "ERROR"}
    return CapitalDealResponse(
        ok=ok,
        deal_reference=str(deal_ref) if deal_ref else None,
        deal_id=str(deal_id) if deal_id else None,
        status=deal_status,
        reason=reason,
        raw=raw,
    )
