"""Alpaca US-equity adapter — header-auth REST trade + market data (PAPER only).

Mirrors ``polaris/venues/okx/adapter.py`` but simpler: Alpaca uses static
header auth (``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY``), so there is NO
request signing and NO ``session.py`` (no token expiry to manage).

Trade host (``paper-api.alpaca.markets``): POST/GET/DELETE ``/v2/orders``,
``/v2/account``, ``/v2/positions``, ``/v2/clock``, ``/v2/assets``. Data host
(``data.alpaca.markets``): ``/v2/stocks/{symbol}/bars`` + ``/quotes/latest``.

Aggressive default: orders are **market** with ``notional`` (USD) so there is
no share-rounding (``flow_not_block`` — a $-notional always fills at best
available). Idempotency MIRROR of OKX #12: a ``client_order_id`` is attached to
every order; before each retry we look the order up by that id, so a lost
response never produces a duplicate submission (Alpaca also rejects a re-POST
of a used id, so the lookup makes the outcome a single correct order).

Security: the API key/secret are sent ONLY as request headers and are NEVER
logged. Live keys are REFUSED (DEMO/PAPER ONLY, double safety).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Final

import httpx

from polaris.core.ratelimit import AsyncTokenBucket
from polaris.venues.alpaca.reject_codes import classify_reject_code

logger = logging.getLogger(__name__)

__all__ = [
    "ALPACA_ACCOUNT_PATH",
    "ALPACA_DATA_BASE",
    "ALPACA_ORDERS_PATH",
    "ALPACA_PAPER_BASE",
    "ALPACA_POSITIONS_PATH",
    "AlpacaAdapter",
    "AlpacaOrderResponse",
    "resolve_alpaca_credentials",
]

# HARDCODED paper hosts — never point the trade client at a live endpoint.
ALPACA_PAPER_BASE: Final[str] = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE: Final[str] = "https://data.alpaca.markets"
# Live trade host — refused by the constructor (double safety).
ALPACA_LIVE_BASE: Final[str] = "https://api.alpaca.markets"

ALPACA_ORDERS_PATH: Final[str] = "/v2/orders"
ALPACA_ACCOUNT_PATH: Final[str] = "/v2/account"
ALPACA_POSITIONS_PATH: Final[str] = "/v2/positions"

_KEY_HEADER: Final[str] = "APCA-API-KEY-ID"
_SECRET_HEADER: Final[str] = "APCA-API-SECRET-KEY"

REST_TIMEOUT_SEC: Final[float] = 15.0

# #12 idempotent transient-fault absorption (mirror OKX): a momentary 5xx /
# timeout must not drop a trade. Bounded + idempotent (client_order_id lookup
# before each retry → never double-submit). NOT a throttle.
ORDER_RETRY_MAX: Final[int] = 2  # extra attempts after the initial POST
ORDER_RETRY_BASE_DELAY_SEC: Final[float] = 0.5  # exponential: 0.5s, 1.0s

# Data-host 429 backoff (the bar-ingest half of the universe-swell incident). A
# momentary "Too Many Requests" during the bar fan-out must not drop the bar.
# Bounded exponential retry (honours Retry-After), data host only. NOT a throttle.
DATA_RETRY_MAX: Final[int] = 3
DATA_RETRY_BASE_DELAY_SEC: Final[float] = 0.5  # 0.5s, 1.0s, 2.0s
_DATA_RETRY_MAX_DELAY_SEC: Final[float] = 30.0

# Data-host PACING (the storm root-cause fix). Backoff alone retried INTO the
# same overage: the per-symbol /bars fan-out fires ~6× the basic-plan cap, so
# 17k× 429 piled up. A per-adapter token bucket paces every data GET to stay
# UNDER the cap (200 req / 60 s, basic plan), so the burst never overshoots in
# the first place (flow_not_block: the call WAITS for a token, never rejected;
# the order/trade host is NEVER paced — aggressive bias intact). The bounded
# in-bucket wait lets a fully drained bucket defer a non-urgent bar re-fetch to
# the next cadence rather than stall the ingest coroutine.
DATA_RATE: Final[int] = 200  # tokens per DATA_PER_SEC window (basic-plan cap)
DATA_PER_SEC: Final[float] = 60.0
DATA_ACQUIRE_TIMEOUT_SEC: Final[float] = 5.0


def _retry_after_sec(resp: httpx.Response, fallback: float) -> float:
    """Backoff seconds for a 429 — honour ``Retry-After`` if present and sane."""
    raw = resp.headers.get("Retry-After", "")
    if raw:
        try:
            return min(_DATA_RETRY_MAX_DELAY_SEC, max(0.0, float(raw)))
        except ValueError:
            pass
    return min(_DATA_RETRY_MAX_DELAY_SEC, fallback)

# Alpaca order states that mean the order is live/working/done (a real order
# exists at the venue) — used to classify a lookup hit as ``ok``.
_ALPACA_LIVE_STATES: Final[frozenset[str]] = frozenset(
    {
        "new",
        "accepted",
        "pending_new",
        "partially_filled",
        "filled",
        "done_for_day",
        "calculated",
        "accepted_for_bidding",
    }
)


async def _async_sleep(delay: float) -> None:
    """Indirection so tests can monkeypatch the backoff sleep to a no-op."""
    await asyncio.sleep(delay)


def resolve_alpaca_credentials() -> tuple[str, str]:
    """Resolve PAPER creds: ``ALPACA_PAPER_*`` first, else ``ARCHIVE_ALPACA_PAPER_*``.

    An empty/absent active value falls back to the archive value (the active
    keys in ``.env`` are currently blank). LIVE keys are never read here —
    DEMO/PAPER ONLY.
    """
    key = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_API_KEY", ""
    )
    secret = os.environ.get("ALPACA_PAPER_SECRET") or os.environ.get(
        "ARCHIVE_ALPACA_PAPER_SECRET", ""
    )
    return key, secret


@dataclass(frozen=True, slots=True)
class AlpacaOrderResponse:
    """Normalized response for ``POST /v2/orders`` (mirror OKXOrderResponse)."""

    ok: bool
    venue_order_id: str | None
    client_order_id: str | None
    code: str
    msg: str
    raw: dict[str, Any]


class AlpacaAdapter:
    """Header-auth wrapper over Alpaca paper REST (trade + data)."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        base_url: str = ALPACA_PAPER_BASE,
        data_base_url: str = ALPACA_DATA_BASE,
        client: httpx.AsyncClient | None = None,
        data_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not secret:
            raise ValueError("api_key/secret required")
        # DEMO/PAPER ONLY — refuse any live trade host (double safety).
        if base_url.rstrip("/") == ALPACA_LIVE_BASE:
            raise ValueError("AlpacaAdapter refuses the live trade host (paper only)")
        self.api_key = api_key
        self.secret = secret
        self.base_url = base_url
        self.data_base_url = data_base_url
        self._client = client
        self._owns_client = client is None
        self._data_client = data_client
        self._owns_data_client = data_client is None
        # Data-host pacing bucket (200 req / 60 s, basic plan). Per-adapter so
        # every data GET on this instance paces through the SAME limiter — the
        # concurrent /bars fan-out cannot burst past the cap. Trade host bypasses.
        self._data_bucket: AsyncTokenBucket = AsyncTokenBucket(
            rate=DATA_RATE, per_sec=DATA_PER_SEC
        )
        # ~1 min clock cache (calendar): (monotonic_deadline, AlpacaClock).
        self._clock_cache: tuple[float, Any] | None = None

    async def __aenter__(self) -> AlpacaAdapter:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._owns_data_client and self._data_client is not None:
            await self._data_client.aclose()
            self._data_client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def _auth_headers(self) -> dict[str, str]:
        # Sent ONLY as headers; never logged.
        return {_KEY_HEADER: self.api_key, _SECRET_HEADER: self.secret}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=REST_TIMEOUT_SEC)
            self._owns_client = True
        return self._client

    @property
    def data_client(self) -> httpx.AsyncClient:
        if self._data_client is None:
            self._data_client = httpx.AsyncClient(
                base_url=self.data_base_url, timeout=REST_TIMEOUT_SEC
            )
            self._owns_data_client = True
        return self._data_client

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        on_data_host: bool = False,
    ) -> Any:
        """Authed request → parsed JSON. Raises on transport / 4xx-5xx.

        Data-host reads (``on_data_host``, e.g. ``fetch_bars``) retry a 429 Too
        Many Requests with exponential backoff (honouring ``Retry-After``), so a
        momentary throttle during the bar-ingest fan-out does not silently drop the
        bar (the 429-storm half of the universe-swell incident). The trade host is
        unchanged (orders have their own idempotent retry path). A non-429 4xx/5xx
        still raises immediately (caller's existing handling).
        """
        cli = self.data_client if on_data_host else self.client
        for attempt in range(DATA_RETRY_MAX + 1):
            if on_data_host:
                # Pace BEFORE the GET so the fan-out stays under the cap (this is
                # what prevents the 429 rather than retrying into it). The bounded
                # acquire is a SOFT cap: if the wait elapses the GET still proceeds
                # token-less (flow_not_block — the bar is never dropped; the 429
                # backoff below is the safety net). The trade host is never paced —
                # orders flow at full speed (aggressive bias intact).
                await self._data_bucket.acquire(timeout=DATA_ACQUIRE_TIMEOUT_SEC)
            resp = await cli.request(
                method, path, params=params, json=json_body, headers=self._auth_headers
            )
            if not (on_data_host and resp.status_code == 429 and attempt < DATA_RETRY_MAX):
                resp.raise_for_status()
                return resp.json()
            delay = _retry_after_sec(resp, DATA_RETRY_BASE_DELAY_SEC * (2 ** attempt))
            logger.info(
                "[alpaca] data 429 %s — backoff %.2fs (retry %d/%d)",
                path, delay, attempt + 1, DATA_RETRY_MAX,
            )
            await _async_sleep(delay)
        # Unreachable (loop returns on the final attempt) — satisfies the type checker.
        raise RuntimeError("unreachable")

    async def _place_order_with_retry(
        self,
        *,
        order_body: dict[str, Any],
        client_order_id: str,
    ) -> AlpacaOrderResponse:
        """POST the order, retrying transient 5xx / timeouts with backoff.

        Idempotency MIRROR of OKX #12: before every retry we query Alpaca by
        ``client_order_id``. If the prior attempt landed (response lost, not the
        order), we return that existing order instead of submitting a duplicate.

        A business 4xx (e.g. 422 insufficient buying power / invalid symbol) is
        NOT transient — it is parsed into an ``ok=False`` response (no retry).
        A 5xx / timeout is retried.
        """
        last_exc: Exception | None = None
        for attempt in range(ORDER_RETRY_MAX + 1):
            if attempt > 0:
                existing = await self._lookup_existing_order(client_order_id)
                if existing is not None:
                    logger.warning(
                        "[alpaca] order retry short-circuit — clOrdId=%s already "
                        "landed (id=%s); not re-submitting (idempotent)",
                        client_order_id,
                        existing.venue_order_id,
                    )
                    return existing
                delay = ORDER_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "[alpaca] order POST retry %d/%d clOrdId=%s after %.2fs backoff",
                    attempt,
                    ORDER_RETRY_MAX,
                    client_order_id,
                    delay,
                )
                await _async_sleep(delay)
            try:
                resp = await self.client.request(
                    "POST",
                    ALPACA_ORDERS_PATH,
                    json=order_body,
                    headers=self._auth_headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                continue
            if resp.status_code < 400:
                return _parse_order_response(resp)
            if resp.status_code < 500:
                # Business reject (4xx) — not transient; parse + return ok=False.
                return _parse_order_response(resp)
            # 5xx → transient; retry.
            last_exc = httpx.HTTPStatusError(
                f"alpaca order 5xx {resp.status_code}", request=resp.request, response=resp
            )
        assert last_exc is not None
        logger.error(
            "[alpaca] order POST failed after %d attempts clOrdId=%s: %s",
            ORDER_RETRY_MAX + 1,
            client_order_id,
            last_exc,
        )
        raise last_exc

    async def _lookup_existing_order(
        self, client_order_id: str
    ) -> AlpacaOrderResponse | None:
        """Return the order if ``client_order_id`` already exists, else ``None``.

        Used between retries to avoid double-submission when a prior POST
        response was lost. Any error in the lookup returns ``None`` (treat as
        'not found' → allow the retry).
        """
        try:
            body = await self.request_json(
                "GET",
                ALPACA_ORDERS_PATH,
                params={"client_order_id": client_order_id, "status": "all"},
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "[alpaca] idempotency lookup failed clOrdId=%s: %s", client_order_id, exc
            )
            return None
        row = _first_order_row(body)
        if row is None or not row.get("id"):
            return None
        return _parse_order_row(row)

    # ------------------------------------------------------------------
    # Market data (data host)
    # ------------------------------------------------------------------

    async def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Min",
        limit: int = 300,
        start: str | None = None,
        feed: str | None = None,
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch up to ``limit`` ``timeframe`` bars for ``symbol`` (data host).

        ``start`` (ISO date / RFC3339) bounds the lower edge of the window. It
        is REQUIRED for the v2 bars endpoint to return anything: without a
        ``start`` the endpoint responds with an empty ``bars`` list (verified
        live — daily/AAPL returns 0 bars sans start).

        FRESHNESS FIX (2026-06-22, RTH 0-trade incident). Two params land the
        FRESHEST bars during US RTH for liquid names; without them the newest
        returned 1m bar was 1-2.6h STALE (TSLA 2.3h / AMD 1.0h live) so the
        recency guard rejected every symbol → 0 signals → 0 trades:
        - ``sort='desc'`` — Alpaca returns the window NEWEST-first, so ``limit``
          keeps the MOST-RECENT bars. The default (asc) returned the OLDEST
          ``limit`` bars of a window wider than ``limit`` → provably stale.
        - ``feed`` — defaults to ``iex`` (#43). Bars do NOT need the paid SIP
          consolidated tape; IEX 1m prints are real-time and adequate for the bar
          pipeline. A SIP-pinned bar URL on a key WITHOUT the entitlement 429s (the
          IEX 200/min ceiling), so the WS SIP→IEX downgrade left bars stranded on
          ``feed=sip`` → 429 storm. Decoupling here ends that at the source; an
          explicit ``feed='sip'`` still overrides (a SIP-confirmed caller).
        The caller re-sorts to the canonical newest-LAST contract.
        """
        if feed is None:
            feed = "iex"
        params: dict[str, str] = {
            "timeframe": timeframe,
            "limit": str(limit),
            "feed": feed,
            "sort": sort,
        }
        if start is not None:
            params["start"] = start
        body = await self.request_json(
            "GET",
            f"/v2/stocks/{symbol}/bars",
            params=params,
            on_data_host=True,
        )
        if isinstance(body, dict):
            bars = body.get("bars")
            return list(bars) if isinstance(bars, list) else []
        return []

    async def fetch_quote(self, symbol: str) -> dict[str, Any]:
        """Latest quote (best bid/ask) for ``symbol`` (data host)."""
        body = await self.request_json(
            "GET",
            f"/v2/stocks/{symbol}/quotes/latest",
            on_data_host=True,
        )
        if isinstance(body, dict):
            q = body.get("quote")
            return q if isinstance(q, dict) else {}
        return {}

    # ------------------------------------------------------------------
    # Trade host
    # ------------------------------------------------------------------

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        notional_usd: float = 0.0,
        qty: float | None = None,
        client_order_id: str,
        time_in_force: str = "day",
    ) -> AlpacaOrderResponse:
        """Place a market order sized by ``notional`` (USD) or exact ``qty`` shares.

        - ``side`` must be 'buy' or 'sell'.
        - ``notional`` (USD, the open path) is used by default so fractional
          sizing is exact (AGGRESSIVE: a $-notional always fills at best
          available). ``qty`` (share count, the SELL-to-CLOSE path) takes
          precedence when given so a close sells EXACTLY the tracked shares — no
          dust left, no oversell. Alpaca accepts exactly one of notional / qty.
        - ``client_order_id`` is the idempotency key (mirrors OKX clOrdId).
        """
        side_l = side.lower()
        if side_l not in ("buy", "sell"):
            raise ValueError(f"invalid side: {side!r}")
        order_body: dict[str, Any] = {
            "symbol": symbol,
            "side": side_l,
            "type": "market",
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }
        if qty is not None:
            order_body["qty"] = _format_qty(qty)
        else:
            order_body["notional"] = _format_decimal(notional_usd)
        # Security: only non-secret order params are logged (never key/secret).
        logger.info(
            "[alpaca] order POST %s side=%s notional_usd=%.4f qty=%s clOrdId=%s",
            symbol,
            side_l,
            notional_usd,
            qty,
            client_order_id,
        )
        parsed = await self._place_order_with_retry(
            order_body=order_body, client_order_id=client_order_id
        )
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[alpaca] order RESP ok=%s id=%s clOrdId=%s code=%s msg=%s",
            parsed.ok,
            parsed.venue_order_id,
            parsed.client_order_id,
            parsed.code,
            parsed.msg[:160] if parsed.msg else "-",
        )
        return parsed

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        logger.info("[alpaca] cancel_order id=%s", order_id)
        resp = await self.client.request(
            "DELETE",
            f"{ALPACA_ORDERS_PATH}/{order_id}",
            headers=self._auth_headers,
        )
        # Alpaca returns 204 (no body) on a successful cancel.
        if resp.status_code in (200, 204):
            return {"ok": True, "order_id": order_id, "status_code": resp.status_code}
        return {"ok": False, "order_id": order_id, "status_code": resp.status_code}

    async def fetch_account(self) -> dict[str, Any]:
        body = await self.request_json("GET", ALPACA_ACCOUNT_PATH)
        return body if isinstance(body, dict) else {}

    async def fetch_positions(self) -> list[dict[str, Any]]:
        body = await self.request_json("GET", ALPACA_POSITIONS_PATH)
        return list(body) if isinstance(body, list) else []

    async def fetch_order(
        self,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Query an order by venue ``order_id`` or ``client_order_id``."""
        if not order_id and not client_order_id:
            raise ValueError("fetch_order requires order_id or client_order_id")
        if order_id:
            body = await self.request_json("GET", f"{ALPACA_ORDERS_PATH}/{order_id}")
            return body if isinstance(body, dict) else {}
        body = await self.request_json(
            "GET",
            ALPACA_ORDERS_PATH,
            params={"client_order_id": client_order_id, "status": "all"},
        )
        row = _first_order_row(body)
        return row if row is not None else {}

    async def fetch_orders(
        self, *, symbol: str, status: str = "closed", limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List recent orders for one symbol (coid-ownership lookup, sweep guard).

        ``status='closed'`` (Alpaca's filled/canceled/rejected/expired
        superset) by default — the sweep guard uses this to test whether an
        UNTRACKED venue holding traces back to OUR OWN ``polA*``-prefixed
        client_order_id (adopt) vs a genuinely foreign order (liquidate
        candidate). Read-only; a malformed/empty body degrades to ``[]``.
        """
        body = await self.request_json(
            "GET", ALPACA_ORDERS_PATH,
            params={"symbols": symbol, "status": status, "limit": limit},
        )
        return list(body) if isinstance(body, list) else []

    async def fetch_clock(self) -> Any:
        """Session state (``/v2/clock``), cached ~1 min on the adapter."""
        # Imported here to avoid a module-level cycle (calendar is data-only).
        from polaris.venues.alpaca.calendar import (
            ALPACA_CLOCK_PATH,
            CLOCK_CACHE_TTL_SEC,
            parse_clock,
        )

        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._clock_cache is not None and now < self._clock_cache[0]:
            return self._clock_cache[1]
        body = await self.request_json("GET", ALPACA_CLOCK_PATH)
        clock = parse_clock(body if isinstance(body, dict) else {})
        self._clock_cache = (now + CLOCK_CACHE_TTL_SEC, clock)
        return clock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_decimal(value: float) -> str:
    """Render with 2 decimals (USD notional); trims trailing zeros."""
    if value <= 0.0:
        return "0"
    txt = f"{value:.2f}".rstrip("0").rstrip(".")
    return txt or "0"


def _format_qty(value: float) -> str:
    """Render a share quantity (sell-to-close) at fractional precision.

    9 decimals covers Alpaca fractional-share granularity without scientific
    notation; trailing zeros are trimmed so a whole-share qty renders cleanly.
    """
    if value <= 0.0:
        return "0"
    txt = f"{value:.9f}".rstrip("0").rstrip(".")
    return txt or "0"


def _first_order_row(body: Any) -> dict[str, Any] | None:
    """Unwrap an order lookup body: a list (query) or a single dict."""
    if isinstance(body, list):
        return body[0] if body and isinstance(body[0], dict) else None
    if isinstance(body, dict):
        return body
    return None


def _parse_order_row(row: dict[str, Any]) -> AlpacaOrderResponse:
    """Parse a single Alpaca order object into the normalized response."""
    order_id = str(row.get("id")) if row.get("id") else None
    cl_id = str(row.get("client_order_id")) if row.get("client_order_id") else None
    status = str(row.get("status") or "").lower()
    ok = order_id is not None and status in _ALPACA_LIVE_STATES
    return AlpacaOrderResponse(
        ok=ok,
        venue_order_id=order_id,
        client_order_id=cl_id,
        code=status or "unknown",
        msg=status,
        raw=row,
    )


def _parse_order_response(resp: httpx.Response) -> AlpacaOrderResponse:
    """Parse a ``POST /v2/orders`` response (success row or business reject)."""
    raw: dict[str, Any]
    try:
        parsed = resp.json()
    except (ValueError, TypeError):
        parsed = None
    if resp.status_code < 400 and isinstance(parsed, dict):
        return _parse_order_row(parsed)
    # Business reject — Alpaca returns ``{"code": <int>, "message": "..."}``.
    raw = parsed if isinstance(parsed, dict) else {"raw_text": resp.text}
    msg = str(raw.get("message") or raw.get("msg") or resp.text or "")
    code = classify_reject_code(  # H2: numeric+status → SSOT semantic token
        numeric_code=raw.get("code"), status_code=resp.status_code, message=msg
    )
    return AlpacaOrderResponse(
        ok=False,
        venue_order_id=None,
        client_order_id=str(raw.get("client_order_id")) if raw.get("client_order_id") else None,
        code=code,
        msg=msg,
        raw=raw,
    )
