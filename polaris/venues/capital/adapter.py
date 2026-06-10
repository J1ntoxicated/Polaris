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

import asyncio
import contextlib
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

# D-3 — bounded submit backoff for the deal endpoints (open/close legs only).
# Total budget = 1 + len(delays) attempts, ≤1.5 s extra latency (tick pace 5 s
# — do NOT grow this). Read at call time so tests can monkeypatch to (0, 0).
_DEAL_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0)
# open POST /positions is NOT idempotent (Capital accepts no client-side deal
# reference) — only a 429 (rejected by the rate limiter BEFORE processing) is
# provably-unprocessed and safe to retry on the open leg.
_OPEN_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429})
# close DELETE /positions/{id} is naturally idempotent (an already-closed deal
# resolves via the absent→CloseOrphan reconcile path) — retry 5xx too.
_CLOSE_RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
# A venue Retry-After is honored but capped so the backoff budget stays fixed.
_RETRY_AFTER_CAP_SEC: Final[float] = 1.0

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
    status: str  # "OPEN" / "CLOSED" / "REJECTED" / "PENDING" / "HTTP_<code>"
    reason: str
    raw: dict[str, Any]
    # D-1: the actual HTTP status of the deal response. 0 = no HTTP response
    # existed (transport-level failure synthesized into HTTP_TIMEOUT/_TRANSPORT).
    http_status: int = 0


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
        parsed = await self._deal_request_with_retry(
            "POST", CAPITAL_POSITIONS_PATH, json_body=body,
            retry_statuses=_OPEN_RETRY_STATUSES, retry_ambiguous=False,
            log_ctx=f"epic={epic} direction={dir_u} size={size:.4f}",
        )
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[capital] open RESP ok=%s status=%s http=%d dealRef=%s dealId=%s reason=%s",
            parsed.ok,
            parsed.status,
            parsed.http_status,
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
        parsed = await self._deal_request_with_retry(
            "DELETE", f"{CAPITAL_POSITIONS_PATH}/{deal_id}",
            retry_statuses=_CLOSE_RETRY_STATUSES, retry_ambiguous=True,
            log_ctx=f"dealId={deal_id}",
        )
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[capital] close RESP ok=%s status=%s http=%d dealRef=%s reason=%s",
            parsed.ok,
            parsed.status,
            parsed.http_status,
            parsed.deal_reference,
            parsed.reason[:160] if parsed.reason else "-",
        )
        return parsed

    async def _deal_request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        retry_statuses: frozenset[int],
        retry_ambiguous: bool,
        log_ctx: str,
    ) -> CapitalDealResponse:
        """Submit a deal request with a bounded backoff (D-3, flow_not_block).

        Retry matrix (idempotency first — Capital POST /positions accepts no
        client deal reference, so a blind retry can DOUBLE an order):

        * HTTP status in ``retry_statuses`` — retry (429 = the rate limiter
          rejected it BEFORE processing; close-leg 5xx = DELETE is idempotent).
        * ``ConnectError``/``ConnectTimeout`` — connect-phase, the request
          provably never reached the venue → safe retry on BOTH legs.
        * Other ``TimeoutException``/transport errors AFTER send — AMBIGUOUS
          (the venue may have executed): the open leg NEVER retries
          (``retry_ambiguous=False``) and returns the synthetic
          ``HTTP_TIMEOUT``/``HTTP_TRANSPORT`` response instead of raising, so
          the reject classifier routes it EXTERNAL (no FAULT_EXCEPTION →
          HARD_HALT for a venue/transport event). ``PoolTimeout`` is also
          pre-send but lands here by type — conservatively non-retried on the
          open leg (only ConnectTimeout is provably pre-send).

        Non-httpx exceptions propagate — a genuine code bug must keep hitting
        the FAULT_EXCEPTION integrity backstop.
        """
        attempt = 0
        while True:
            delays = _DEAL_RETRY_DELAYS  # read at call time (tests monkeypatch)
            final = attempt >= len(delays)
            delay = 0.0 if final else delays[attempt]
            try:
                resp = await self.session.request(method, path, json_body=json_body)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = _transport_deal_response("HTTP_TRANSPORT", exc)
                retryable = not final
            except httpx.TimeoutException as exc:
                last = _transport_deal_response("HTTP_TIMEOUT", exc)
                retryable = retry_ambiguous and not final
            except httpx.HTTPError as exc:
                last = _transport_deal_response("HTTP_TRANSPORT", exc)
                retryable = retry_ambiguous and not final
            else:
                last = _parse_deal_response(resp)
                retryable = last.http_status in retry_statuses and not final
                if retryable:
                    ra_raw = resp.headers.get("Retry-After", "")
                    with contextlib.suppress(ValueError):
                        delay = min(max(delay, float(ra_raw)), _RETRY_AFTER_CAP_SEC)
            if not retryable:
                if last.status.startswith("HTTP_"):
                    # Exhausted / ambiguous failure — forensic record for the
                    # ghost-position reconcile (epic/direction/size in log_ctx).
                    logger.error(
                        "[capital] %s %s failed status=%s after %d attempt(s) "
                        "%s reason=%.200s",
                        method, path, last.status, attempt + 1, log_ctx, last.reason,
                    )
                return last
            logger.warning(
                "[capital] %s %s retry %d/%d in %.2fs status=%s %s",
                method, path, attempt + 1, len(delays), delay, last.status, log_ctx,
            )
            await asyncio.sleep(delay)
            attempt += 1

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
    http_status = resp.status_code
    if http_status == 200:
        deal_status = str(raw.get("dealStatus") or raw.get("status") or "PENDING").upper()
        reason = str(raw.get("reason") or "")
    else:
        # D-1: an HTTP error is NOT a deal status — the old "PENDING" default
        # mislabelled venue 429/5xx as a strategy-attributed reject (55 unjust
        # SOFT_HALTs live). Label is unconditionally HTTP_<code> — status-like
        # keys in an ERROR body are preserved in raw, never trusted for the
        # label (they would dodge the startswith("HTTP_") external gate).
        deal_status = f"HTTP_{http_status}"
        reason = str(raw.get("errorCode") or raw.get("reason") or "")
        msg = str(raw.get("errorMessage") or "")
        if msg:
            reason = f"{reason}: {msg}" if reason else msg
        logger.warning(
            "[capital] deal endpoint HTTP %d errorCode=%s body=%.200s",
            http_status, raw.get("errorCode") or "-", body,
        )
    ok = http_status == 200 and deal_status not in {"REJECTED", "ERROR"}
    return CapitalDealResponse(
        ok=ok,
        deal_reference=str(deal_ref) if deal_ref else None,
        deal_id=str(deal_id) if deal_id else None,
        status=deal_status,
        reason=reason,
        raw=raw,
        http_status=http_status,
    )


def _transport_deal_response(kind: str, exc: httpx.HTTPError) -> CapitalDealResponse:
    """Synthesize a deal response for a transport-level failure (no HTTP resp).

    RETURNED (never raised) so the submit path classifies it as an EXTERNAL
    venue/transport event (flow_not_block) instead of escaping into the
    FAULT_EXCEPTION backstop. ``http_status=0`` = no HTTP response existed.
    """
    return CapitalDealResponse(
        ok=False,
        deal_reference=None,
        deal_id=None,
        status=kind,
        reason=repr(exc)[:200],
        raw={"transport_error": repr(exc)[:200]},
        http_status=0,
    )
