"""OKX adapter — REST bar fetch + signed trade endpoints.

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Q1 bars table)
- vault/10_decisions/ADR-003-8-layer-architecture.md (REST polling P0, WS P1)

Endpoints
---------
Public (no auth, demo header for parity):
- ``GET /api/v5/market/candles``
- ``GET /api/v5/market/ticker``

Authenticated (HMAC-SHA256, ``signing.build_signed_headers``):
- ``POST /api/v5/trade/order`` — entry. ``tdMode=cash`` always.
  Path semantics differ by ``ordType``:
    * ``market`` (or IOC fallback when ticker unavailable): ``sz=notional_usd`` (USDT) and ``tgtCcy=quote_ccy`` is sent.
    * ``ioc`` (default with reference price): ``sz`` is in base ccy (computed
      from ``notional_usd / clamped_px``), ``px`` is sent, ``tgtCcy`` is omitted
      (OKX ignores it for IOC/limit/post_only).
- ``POST /api/v5/trade/cancel-order``
- ``GET  /api/v5/account/balance``
- ``GET  /api/v5/account/positions`` (SPOT → base ccy holdings)
- ``GET  /api/v5/trade/orders-pending``
- ``GET  /api/v5/trade/order`` — fill query by ``ordId``

Aggressive default for SPOT (R2): IOC with opposite-side px clamp at
``slippage_bps`` (buy anchors on ``askPx`` and pays up by ``+bps``; sell
anchors on ``bidPx`` and gives up ``-bps``). Falls back to ``ordType=market``
+ ``tgtCcy=quote_ccy`` (sz in USDT) when no ticker reference is available.
``clOrdId`` follows OKX rule (alphanumeric, ≤ 32 chars, must start with a
letter — hyphens and underscores forbidden).

Returns canonical ``Bar`` objects ready to write to the ``bars`` table via
:func:`polaris.core.data.ingest.persist_bars`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from polaris.core.data.canonical import compute_underlying_group_id, okx_candle_to_bar
from polaris.core.data.schema import Bar
from polaris.venues.okx.signing import build_signed_headers

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SLIPPAGE_BPS",
    "OKX_BASE_DEMO",
    "OKX_CANCEL_ORDER_PATH",
    "OKX_CANDLES_PATH",
    "OKX_PLACE_ORDER_PATH",
    "OKXAdapter",
    "OKXOrderResponse",
    "fetch_okx_bars",
    "sanitize_clordid",
]

OKX_BASE_DEMO: Final[str] = "https://us.okx.com"
OKX_CANDLES_PATH: Final[str] = "/api/v5/market/candles"
OKX_TICKER_PATH: Final[str] = "/api/v5/market/ticker"
OKX_PLACE_ORDER_PATH: Final[str] = "/api/v5/trade/order"
OKX_CANCEL_ORDER_PATH: Final[str] = "/api/v5/trade/cancel-order"
OKX_ACCOUNT_BALANCE_PATH: Final[str] = "/api/v5/account/balance"
OKX_ACCOUNT_POSITIONS_PATH: Final[str] = "/api/v5/account/positions"
OKX_TRADE_ORDERS_PENDING_PATH: Final[str] = "/api/v5/trade/orders-pending"
OKX_TRADE_ORDER_PATH: Final[str] = "/api/v5/trade/order"

DEMO_HEADERS: Final[dict[str, str]] = {"x-simulated-trading": "1"}
REST_TIMEOUT_SEC: Final[float] = 15.0
DEFAULT_SLIPPAGE_BPS: Final[float] = 5.0  # 0.05 %, R2 aggressive cap

_CLORDID_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9]")
_CLORDID_MAX: Final[int] = 32


def sanitize_clordid(raw: str) -> str:
    """Strip non-alphanumeric chars, ensure leading letter, clamp ≤ 32 chars.

    OKX rejects ``clOrdId`` with hyphens / underscores / non-ASCII or starting
    with a digit. We replace forbidden chars with empty and prefix ``p`` if
    the result starts with a digit or is empty.
    """
    cleaned = _CLORDID_RE.sub("", raw)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "p" + cleaned
    return cleaned[:_CLORDID_MAX]


# ---------------------------------------------------------------------------
# Public market data (existing behaviour preserved)
# ---------------------------------------------------------------------------


async def fetch_okx_bars(
    inst_id: str,
    *,
    bar_interval: str = "1m",
    limit: int = 300,
    base_url: str = OKX_BASE_DEMO,
    asset_class: str = "crypto",
    client: httpx.AsyncClient | None = None,
) -> list[Bar]:
    """Fetch up to ``limit`` ``bar_interval`` candles from OKX SPOT (no auth)."""
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            OKX_CANDLES_PATH,
            params={"instId": inst_id, "bar": bar_interval, "limit": str(limit)},
            headers=DEMO_HEADERS,
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await cli.aclose()
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(f"OKX candles error: code={body.get('code')} msg={body.get('msg')}")
    data = body.get("data", []) or []
    underlying = compute_underlying_group_id("okx", inst_id, asset_class=asset_class)
    out: list[Bar] = []
    for row in data:
        try:
            out.append(
                okx_candle_to_bar(
                    row,
                    inst_id=inst_id,
                    bar_interval=bar_interval,
                    underlying_group_id=underlying,
                )
            )
        except (ValueError, IndexError):
            continue
    logger.debug(
        "[okx] bars fetched %s/%s requested=%d got=%d",
        inst_id,
        bar_interval,
        limit,
        len(out),
    )
    return out


# ---------------------------------------------------------------------------
# Authenticated trade adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OKXOrderResponse:
    """Normalized response for ``POST /trade/order``."""

    ok: bool
    venue_order_id: str | None
    client_order_id: str | None
    code: str
    msg: str
    raw: dict[str, Any]


class OKXAdapter:
    """Authenticated wrapper over OKX REST trade endpoints (demo by default)."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        base_url: str = OKX_BASE_DEMO,
        demo: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not secret or not passphrase:
            raise ValueError("api_key/secret/passphrase required")
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.base_url = base_url
        self.demo = demo
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> OKXAdapter:
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

    async def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            request_path = f"{path}?{qs}"
        else:
            request_path = path
        body = "" if json_body is None else json.dumps(json_body, separators=(",", ":"))
        headers = build_signed_headers(
            api_key=self.api_key,
            secret=self.secret,
            passphrase=self.passphrase,
            method=method,
            request_path=request_path,
            body=body,
            demo=self.demo,
        )
        resp = await self.client.request(
            method,
            path,
            params=params,
            content=body if body else None,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"OKX unexpected payload type: {type(data).__name__}")
        return data

    # ------------------------------------------------------------------
    # Public sign-required endpoints
    # ------------------------------------------------------------------

    async def fetch_ticker(self, inst_id: str) -> dict[str, Any]:
        """Lightweight last-price + best-bid/ask snapshot (no auth, demo header)."""
        resp = await self.client.get(
            OKX_TICKER_PATH,
            params={"instId": inst_id},
            headers=DEMO_HEADERS if self.demo else {},
        )
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("data") or []
        return rows[0] if rows else {}

    async def place_market_order(
        self,
        *,
        inst_id: str,
        side: str,
        notional_usd: float,
        client_order_id: str,
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
        td_mode: str = "cash",
        tgt_ccy: str = "quote_ccy",
        ord_type: str = "ioc",
        last_price_hint: float | None = None,
    ) -> OKXOrderResponse:
        """Place an IOC (or market fallback) order with px clamp at ``slippage_bps``.

        - ``side`` must be 'buy' or 'sell'.
        - ``client_order_id`` is sanitized (no hyphens, alphanumeric only).
        - **``ord_type='market'`` (or IOC fallback when ticker is unavailable)**
          uses notional semantics: ``sz`` carries ``notional_usd`` in USDT and
          ``tgtCcy=quote_ccy`` is sent so OKX interprets size as quote ccy.
        - **``ord_type='ioc'`` (default, with reference price)** uses
          base-quantity semantics: we resolve a reference price (opposite-side
          anchor — ``askPx`` for buys, ``bidPx`` for sells), apply the
          ``slippage_bps`` clamp (``+bps`` for buys, ``-bps`` for sells),
          divide ``notional_usd`` by the clamped px to get ``sz`` in base ccy,
          and send ``px`` alongside (``tgtCcy`` is omitted because OKX ignores
          it for IOC/limit/post_only).
        - **``ord_type='limit'`` / ``'post_only'``** require ``last_price_hint``
          and follow the same base-qty path.
        """
        side_l = side.lower()
        if side_l not in ("buy", "sell"):
            raise ValueError(f"invalid side: {side!r}")
        cl_ord_id = sanitize_clordid(client_order_id)
        # OKX SPOT semantics:
        # - ordType=market + tgtCcy=quote_ccy → sz is in USDT (notional).
        # - ordType=limit/ioc/post_only → sz is in base ccy; tgtCcy is ignored.
        # Compute base_qty from notional_usd via reference price for
        # limit/ioc paths, send sz accordingly.
        ref_price = last_price_hint
        if ord_type != "market" and ref_price is None:
            tk = await self.fetch_ticker(inst_id)
            ref_price = _safe_float(tk.get("bidPx") if side_l == "sell" else tk.get("askPx"))
        order_body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side_l,
            "ordType": ord_type,
            "clOrdId": cl_ord_id,
        }
        if ord_type == "market":
            # Notional USDT path (tgtCcy=quote_ccy honoured by OKX).
            order_body["sz"] = _format_decimal(notional_usd)
            order_body["tgtCcy"] = tgt_ccy
        elif ord_type == "ioc":
            if ref_price is None or ref_price <= 0.0:
                # No reference price — fall back to market notional.
                order_body["ordType"] = "market"
                order_body["sz"] = _format_decimal(notional_usd)
                order_body["tgtCcy"] = tgt_ccy
            else:
                bps_mult = 1.0 + (slippage_bps / 10_000.0) * (1.0 if side_l == "buy" else -1.0)
                px = ref_price * bps_mult
                base_qty = notional_usd / max(px, 1e-12)
                order_body["sz"] = _format_decimal(base_qty)
                order_body["px"] = _format_decimal(px)
        else:
            # limit / post_only — caller is responsible for px via last_price_hint.
            if ref_price is None or ref_price <= 0.0:
                raise ValueError(f"{ord_type} requires last_price_hint")
            base_qty = notional_usd / max(ref_price, 1e-12)
            order_body["sz"] = _format_decimal(base_qty)
            order_body["px"] = _format_decimal(ref_price)
        # Security: only log non-secret order params (clOrdId / sz / px / side).
        # API key, secret, passphrase are NEVER logged.
        logger.info(
            "[okx] order POST %s side=%s notional_usd=%.4f sz=%s px=%s ordType=%s clOrdId=%s",
            inst_id,
            side_l,
            notional_usd,
            order_body.get("sz"),
            order_body.get("px", "-"),
            order_body.get("ordType"),
            cl_ord_id,
        )
        body = await self._signed_request("POST", OKX_PLACE_ORDER_PATH, json_body=order_body)
        parsed = _parse_order_response(body)
        log_level = logging.INFO if parsed.ok else logging.WARNING
        logger.log(
            log_level,
            "[okx] order RESP ok=%s ordId=%s clOrdId=%s code=%s msg=%s",
            parsed.ok,
            parsed.venue_order_id,
            parsed.client_order_id,
            parsed.code,
            parsed.msg[:160] if parsed.msg else "-",
        )
        return parsed

    async def cancel_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        logger.info("[okx] cancel_order inst=%s ordId=%s", inst_id, ord_id)
        return await self._signed_request(
            "POST",
            OKX_CANCEL_ORDER_PATH,
            json_body={"instId": inst_id, "ordId": ord_id},
        )

    async def fetch_balance(self, ccy: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        return await self._signed_request("GET", OKX_ACCOUNT_BALANCE_PATH, params=params or None)

    async def fetch_positions(self, inst_type: str = "SPOT") -> dict[str, Any]:
        return await self._signed_request(
            "GET",
            OKX_ACCOUNT_POSITIONS_PATH,
            params={"instType": inst_type},
        )

    async def fetch_pending_orders(self, inst_type: str = "SPOT") -> dict[str, Any]:
        return await self._signed_request(
            "GET",
            OKX_TRADE_ORDERS_PENDING_PATH,
            params={"instType": inst_type},
        )

    async def fetch_order(self, *, inst_id: str, ord_id: str) -> dict[str, Any]:
        return await self._signed_request(
            "GET",
            OKX_TRADE_ORDER_PATH,
            params={"instId": inst_id, "ordId": ord_id},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_decimal(value: float) -> str:
    """Render with 8 decimal places (OKX accepts strings; trims trailing 0)."""
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


def _parse_order_response(body: dict[str, Any]) -> OKXOrderResponse:
    code = str(body.get("code", "1"))
    msg = str(body.get("msg", ""))
    rows = body.get("data") or []
    row0 = rows[0] if rows else {}
    inner_code = str(row0.get("sCode", code))
    ok = code == "0" and inner_code == "0"
    return OKXOrderResponse(
        ok=ok,
        venue_order_id=str(row0.get("ordId")) if row0.get("ordId") else None,
        client_order_id=str(row0.get("clOrdId")) if row0.get("clOrdId") else None,
        code=inner_code if inner_code != "0" else code,
        msg=str(row0.get("sMsg") or msg),
        raw=body,
    )


# Touch unused import time to silence linters if pruned later — used by signing.
_ = time
