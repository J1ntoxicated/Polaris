"""Binance USDⓈ-M Futures (perp) **testnet** adapter — REST klines + signed trade.

Mirrors the public surface of :class:`polaris.venues.okx.adapter.OKXAdapter`
so it can slot into the production pipeline with compatible method signatures:
``place_market_order`` / ``place_order`` (limit) / ``cancel_order`` /
``fetch_balance`` / ``fetch_positions`` / ``fetch_bars`` / ``aclose``.

TESTNET ONLY. ``base_url`` defaults to the testnet host; mainnet must never be
wired here. Binance has no demo header — isolation is via the testnet base URL
and testnet-only API keys.

Endpoints
---------
Public (no auth):
- ``GET /fapi/v1/klines`` — bars.
- ``GET /fapi/v1/ticker/bookTicker`` — best bid/ask.

Signed (HMAC-SHA256 query string, ``X-MBX-APIKEY`` header):
- ``POST   /fapi/v1/order``        — entry/exit (MARKET or LIMIT).
- ``DELETE /fapi/v1/order``        — cancel.
- ``POST   /fapi/v1/leverage``     — set leverage.
- ``GET    /fapi/v2/account``      — balances.
- ``GET    /fapi/v2/positionRisk`` — positions.
- ``GET    /fapi/v1/order``        — order/fill query (idempotency check).

Perp semantics: ``symbol`` has no dash (``BTCUSDT``). Long/short via
``side=BUY/SELL`` (one-way mode default; ``positionSide`` only used in hedge
mode). MARKET: ``type=MARKET``; LIMIT: ``type=LIMIT,timeInForce=GTC`` (or GTX
for post-only). ``quantity`` is base contract qty (caller rounds to stepSize).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

from polaris.core.data.schema import Bar
from polaris.venues.binance.market_data import (
    BINANCE_BOOK_TICKER_PATH,
    BINANCE_TESTNET_FUTURES_BASE,
    fetch_binance_bars,
)
from polaris.venues.binance.signing import (
    BINANCE_APIKEY_HEADER,
    build_signed_query,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BINANCE_CANCEL_ORDER_PATH",
    "BINANCE_PLACE_ORDER_PATH",
    "BINANCE_TESTNET_FUTURES_BASE",
    "BinanceFuturesAdapter",
    "BinanceOrderResponse",
]

BINANCE_PLACE_ORDER_PATH: Final[str] = "/fapi/v1/order"
BINANCE_CANCEL_ORDER_PATH: Final[str] = "/fapi/v1/order"
BINANCE_ORDER_QUERY_PATH: Final[str] = "/fapi/v1/order"
BINANCE_LEVERAGE_PATH: Final[str] = "/fapi/v1/leverage"
BINANCE_ACCOUNT_PATH: Final[str] = "/fapi/v2/account"
BINANCE_POSITION_RISK_PATH: Final[str] = "/fapi/v2/positionRisk"

REST_TIMEOUT_SEC: Final[float] = 15.0

# Transient order-POST outage absorption (mirror OKX #12). AGGRESSIVE: a
# momentary venue/network blip must not drop a trade. Bounded + idempotent
# (newClientOrderId lookup before each retry → never double-submit).
ORDER_RETRY_MAX: Final[int] = 2
ORDER_RETRY_BASE_DELAY_SEC: Final[float] = 0.5


async def _async_sleep(delay: float) -> None:
    """Indirection so tests can monkeypatch the backoff sleep to a no-op."""
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Authenticated trade adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BinanceOrderResponse:
    """Normalized response for ``POST /fapi/v1/order`` (mirrors OKXOrderResponse)."""

    ok: bool
    venue_order_id: str | None
    client_order_id: str | None
    code: str
    msg: str
    raw: dict[str, Any]


class BinanceFuturesAdapter:
    """Authenticated wrapper over Binance USDⓈ-M futures **testnet** REST."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        base_url: str = BINANCE_TESTNET_FUTURES_BASE,
        recv_window: int = 5_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not secret:
            raise ValueError("api_key/secret required")
        self.api_key = api_key
        self.secret = secret
        self.base_url = base_url
        self.recv_window = recv_window
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> BinanceFuturesAdapter:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=REST_TIMEOUT_SEC)
            self._owns_client = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=REST_TIMEOUT_SEC)
            self._owns_client = True
        return self._client

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {BINANCE_APIKEY_HEADER: self.api_key}

    async def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | float] | None = None,
    ) -> Any:
        """Send a SIGNED request. Signed params go on the query string for all
        verbs (Binance accepts query-string signing for POST/DELETE too)."""
        query = build_signed_query(
            secret=self.secret,
            params=params or {},
            recv_window=self.recv_window,
        )
        url = f"{path}?{query}"
        resp = await self.client.request(method, url, headers=self._auth_headers)
        resp.raise_for_status()
        return resp.json()

    async def _place_order_with_retry(
        self,
        *,
        order_params: dict[str, str | int | float],
        symbol: str,
        cl_ord_id: str,
    ) -> BinanceOrderResponse:
        """POST the order, retrying transient 5xx / timeouts with backoff.

        AGGRESSIVE transient-fault absorption (NOT a throttle). Idempotent:
        before each retry we query by ``newClientOrderId``; if the prior attempt
        landed (response lost, not the order), we return it instead of
        double-submitting. ``newClientOrderId`` is Binance's idempotency key.
        """
        last_exc: Exception | None = None
        for attempt in range(ORDER_RETRY_MAX + 1):
            if attempt > 0:
                existing = await self._lookup_existing_order(
                    symbol=symbol, cl_ord_id=cl_ord_id
                )
                if existing is not None:
                    logger.warning(
                        "[binance] order retry short-circuit — clOrdId=%s already "
                        "landed (orderId=%s); not re-submitting (idempotent)",
                        cl_ord_id,
                        existing.venue_order_id,
                    )
                    return existing
                delay = ORDER_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "[binance] order POST retry %d/%d clOrdId=%s after %.2fs backoff",
                    attempt,
                    ORDER_RETRY_MAX,
                    cl_ord_id,
                    delay,
                )
                await _async_sleep(delay)
            try:
                body = await self._signed_request(
                    "POST", BINANCE_PLACE_ORDER_PATH, params=order_params
                )
                return _parse_order_response(body)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status < 500:
                    raise  # client error (4xx) — not transient, fail fast.
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
        assert last_exc is not None
        logger.error(
            "[binance] order POST failed after %d attempts clOrdId=%s: %s",
            ORDER_RETRY_MAX + 1,
            cl_ord_id,
            last_exc,
        )
        raise last_exc

    async def _lookup_existing_order(
        self, *, symbol: str, cl_ord_id: str
    ) -> BinanceOrderResponse | None:
        """Return the order if ``cl_ord_id`` already exists, else ``None``.

        Any error in the lookup returns ``None`` (treat as 'not found' → allow
        the retry to proceed).
        """
        try:
            body = await self._signed_request(
                "GET",
                BINANCE_ORDER_QUERY_PATH,
                params={"symbol": symbol, "origClientOrderId": cl_ord_id},
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning("[binance] idempotency lookup failed clOrdId=%s: %s", cl_ord_id, exc)
            return None
        if not isinstance(body, dict) or not body.get("orderId"):
            return None
        return _parse_order_response(body)

    # ------------------------------------------------------------------
    # Public market data
    # ------------------------------------------------------------------

    async def fetch_bars(
        self, symbol: str, *, bar_interval: str = "1m", limit: int = 300
    ) -> list[Bar]:
        """Fetch klines via the adapter's client (mirrors OKX ``fetch_bars`` use)."""
        return await fetch_binance_bars(
            symbol,
            bar_interval=bar_interval,
            limit=limit,
            base_url=self.base_url,
            client=self.client,
        )

    async def fetch_book_ticker(self, symbol: str) -> dict[str, Any]:
        """Best bid/ask snapshot (no auth)."""
        resp = await self.client.get(
            BINANCE_BOOK_TICKER_PATH, params={"symbol": symbol}
        )
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, dict) else {}

    # ------------------------------------------------------------------
    # Signed trade endpoints
    # ------------------------------------------------------------------

    async def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: str,
        position_side: str | None = None,
    ) -> BinanceOrderResponse:
        """Place a MARKET order. ``side`` is 'buy'/'sell' (→ BUY/SELL).

        ``quantity`` is base contract qty (caller rounds to stepSize via the
        constraint translator). ``position_side`` is only set in hedge mode.
        """
        order_params = self._build_order_params(
            symbol=symbol,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
            ord_type="MARKET",
            position_side=position_side,
        )
        return await self._submit(order_params, symbol, client_order_id)

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        client_order_id: str,
        time_in_force: str = "GTC",
        position_side: str | None = None,
    ) -> BinanceOrderResponse:
        """Place a LIMIT order. ``time_in_force`` GTC, or GTX for post-only."""
        if price <= 0.0:
            raise ValueError("limit order requires positive price")
        order_params = self._build_order_params(
            symbol=symbol,
            side=side,
            quantity=quantity,
            client_order_id=client_order_id,
            ord_type="LIMIT",
            price=price,
            time_in_force=time_in_force,
            position_side=position_side,
        )
        return await self._submit(order_params, symbol, client_order_id)

    def _build_order_params(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: str,
        ord_type: str,
        price: float | None = None,
        time_in_force: str | None = None,
        position_side: str | None = None,
    ) -> dict[str, str | int | float]:
        side_u = side.upper()
        if side_u not in ("BUY", "SELL"):
            raise ValueError(f"invalid side: {side!r}")
        params: dict[str, str | int | float] = {
            "symbol": symbol,
            "side": side_u,
            "type": ord_type,
            "quantity": _format_decimal(quantity),
            "newClientOrderId": client_order_id,
        }
        if ord_type == "LIMIT":
            assert price is not None
            params["price"] = _format_decimal(price)
            params["timeInForce"] = time_in_force or "GTC"
        if position_side is not None:
            params["positionSide"] = position_side.upper()
        return params

    async def _submit(
        self,
        order_params: dict[str, str | int | float],
        symbol: str,
        client_order_id: str,
    ) -> BinanceOrderResponse:
        logger.info(
            "[binance] order POST %s side=%s qty=%s price=%s type=%s clOrdId=%s",
            symbol,
            order_params.get("side"),
            order_params.get("quantity"),
            order_params.get("price", "-"),
            order_params.get("type"),
            client_order_id,
        )
        parsed = await self._place_order_with_retry(
            order_params=order_params, symbol=symbol, cl_ord_id=client_order_id
        )
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[binance] order RESP ok=%s orderId=%s clOrdId=%s code=%s msg=%s",
            parsed.ok,
            parsed.venue_order_id,
            parsed.client_order_id,
            parsed.code,
            parsed.msg[:160] if parsed.msg else "-",
        )
        return parsed

    async def cancel_order(
        self,
        *,
        symbol: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> dict[str, Any]:
        if not ord_id and not cl_ord_id:
            raise ValueError("cancel_order requires ord_id or cl_ord_id")
        params: dict[str, str | int | float] = {"symbol": symbol}
        if ord_id:
            params["orderId"] = ord_id
        else:
            assert cl_ord_id is not None
            params["origClientOrderId"] = cl_ord_id
        logger.info("[binance] cancel_order symbol=%s ordId=%s clOrdId=%s",
                    symbol, ord_id, cl_ord_id)
        body = await self._signed_request("DELETE", BINANCE_CANCEL_ORDER_PATH, params=params)
        return body if isinstance(body, dict) else {"raw": body}

    async def set_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        body = await self._signed_request(
            "POST", BINANCE_LEVERAGE_PATH, params={"symbol": symbol, "leverage": leverage}
        )
        return body if isinstance(body, dict) else {"raw": body}

    async def fetch_balance(self) -> dict[str, Any]:
        """Account balances + assets (``GET /fapi/v2/account``)."""
        body = await self._signed_request("GET", BINANCE_ACCOUNT_PATH)
        return body if isinstance(body, dict) else {"raw": body}

    async def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Open positions (``GET /fapi/v2/positionRisk``)."""
        params: dict[str, str | int | float] = {}
        if symbol:
            params["symbol"] = symbol
        body = await self._signed_request(
            "GET", BINANCE_POSITION_RISK_PATH, params=params or None
        )
        if isinstance(body, list):
            return [r for r in body if isinstance(r, dict)]
        return []

    async def fetch_order(
        self,
        *,
        symbol: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> dict[str, Any]:
        """Query an order by ``orderId`` or ``origClientOrderId``."""
        if not ord_id and not cl_ord_id:
            raise ValueError("fetch_order requires ord_id or cl_ord_id")
        params: dict[str, str | int | float] = {"symbol": symbol}
        if ord_id:
            params["orderId"] = ord_id
        else:
            assert cl_ord_id is not None
            params["origClientOrderId"] = cl_ord_id
        body = await self._signed_request("GET", BINANCE_ORDER_QUERY_PATH, params=params)
        return body if isinstance(body, dict) else {"raw": body}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_decimal(value: float) -> str:
    """Render with up to 8 decimal places (Binance accepts strings; trims 0s)."""
    if value <= 0.0:
        return "0"
    txt = f"{value:.8f}".rstrip("0").rstrip(".")
    return txt or "0"


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_order_response(body: dict[str, Any]) -> BinanceOrderResponse:
    """Parse a Binance order POST/GET response.

    Success: a dict with ``orderId``. Error: ``{"code": -2010, "msg": "..."}``
    (Binance returns negative numeric codes; 4xx HTTP carries the JSON body).
    """
    if body.get("orderId"):
        return BinanceOrderResponse(
            ok=True,
            venue_order_id=str(body["orderId"]),
            client_order_id=str(body.get("clientOrderId")) if body.get("clientOrderId") else None,
            code="0",
            msg=str(body.get("status") or ""),
            raw=body,
        )
    return BinanceOrderResponse(
        ok=False,
        venue_order_id=None,
        client_order_id=None,
        code=str(body.get("code", "-1")),
        msg=str(body.get("msg", "")),
        raw=body,
    )
