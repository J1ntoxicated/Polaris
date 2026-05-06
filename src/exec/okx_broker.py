"""OKXBroker — OKX SPOT live REST execution (Phase 14.2).

Live activation (user sets env, no credential reads in code):
    export OKX_API_KEY=...
    export OKX_API_SECRET=...
    export OKX_API_PASSPHRASE=...
    export POLARIS_LIVE_MODE=1
    export POLARIS_OKX_DEMO=1     # demo trading mode (default — safer)

Demo mode (default) sends `x-simulated-trading: 1` header to OKX —
real API surface, no real money. Switch to live by `unset POLARIS_OKX_DEMO`.

OKX SPOT REST surface used:
- POST /api/v5/trade/order   — place order
- POST /api/v5/trade/cancel-order
- GET  /api/v5/account/balance
- GET  /api/v5/trade/order   — query

Auth: HMAC-SHA256 of (timestamp + method + path + body), base64 encoded.
Timestamp ISO 8601 with milliseconds, UTC.

Safety stack (every order):
1. Kill switch active → REJECTED
2. Size cap exceeded → REJECTED
3. Not _live_armed() → REJECTED (dry-run)
4. POLARIS_OKX_DEMO=1 (default) → demo header attached
5. Real REST call with timeout
6. Response parsed → OrderResult

Reference: https://www.okx.com/docs-v5/en/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from src.exec.broker import (
    Broker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.exec.kill_switch import is_kill_switch_active

logger = logging.getLogger(__name__)

OKX_BASE_URL = "https://www.okx.com"
OKX_REST_TIMEOUT_S = 10.0


def _live_armed() -> bool:
    """All conditions for real order submission satisfied?"""
    return (
        os.environ.get("POLARIS_LIVE_MODE") == "1"
        and bool(os.environ.get("OKX_API_KEY"))
        and bool(os.environ.get("OKX_API_SECRET"))
        and bool(os.environ.get("OKX_API_PASSPHRASE"))
    )


def _is_demo() -> bool:
    """Default to demo (safe). Explicit unset → real money."""
    return os.environ.get("POLARIS_OKX_DEMO", "1") == "1"


def _iso_timestamp() -> str:
    """OKX timestamp format: 2024-12-31T23:59:59.999Z"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sign(secret: str, ts: str, method: str, path: str, body: str) -> str:
    """OKX signature: base64(hmac_sha256(secret, ts + method + path + body))"""
    msg = ts + method.upper() + path + body
    digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _auth_headers(
    api_key: str, api_secret: str, passphrase: str,
    method: str, path: str, body: str,
    demo: bool,
) -> dict:
    ts = _iso_timestamp()
    headers = {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": _sign(api_secret, ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
    }
    if demo:
        headers["x-simulated-trading"] = "1"
    return headers


class OKXBroker(Broker):
    """OKX SPOT live broker — Phase 14.2 REST implementation.

    Defaults to demo trading (POLARIS_OKX_DEMO=1). Set to 0 for real money.

    Order placement: SPOT MARKET via tgtCcy=quote_ccy (size_usd directly,
    no manual base-quantity conversion needed).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        max_size_usd: float = 500.0,
        base_url: str = OKX_BASE_URL,
    ) -> None:
        self._api_key = api_key or os.environ.get("OKX_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("OKX_API_SECRET", "")
        self._passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
        self._max_size_usd = max_size_usd
        self._base_url = base_url

    @property
    def is_live(self) -> bool:
        return True

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _request(
        self, method: str, path: str, body_dict: Optional[dict] = None,
    ) -> dict:
        """Sign + send REST request. Returns parsed JSON.

        Raises requests.RequestException on network failure.
        """
        body_str = json.dumps(body_dict) if body_dict else ""
        headers = _auth_headers(
            self._api_key, self._api_secret, self._passphrase,
            method, path, body_str, demo=_is_demo(),
        )
        url = self._base_url + path
        if method.upper() == "POST":
            resp = requests.post(
                url, headers=headers, data=body_str, timeout=OKX_REST_TIMEOUT_S,
            )
        else:
            resp = requests.get(url, headers=headers, timeout=OKX_REST_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()

    # ── Public Broker API ───────────────────────────────────────────────────

    def place_order(self, request: OrderRequest) -> OrderResult:
        ts_ms = int(time.time() * 1000)

        if is_kill_switch_active():
            return _rejected("KILL-SWITCH-BLOCK", "kill_switch_active", ts_ms)
        if request.size_usd > self._max_size_usd:
            return _rejected(
                "SIZE-CAP-BLOCK",
                f"size_usd {request.size_usd:.2f} > max {self._max_size_usd:.2f}",
                ts_ms,
            )
        if not _live_armed():
            return _rejected("DRY-RUN", "live_disabled_dry_run", ts_ms)

        # Build OKX order request
        # SPOT MARKET via tgtCcy=quote_ccy: sz = USD amount (no base conversion)
        body: dict[str, Any] = {
            "instId": request.ticker,                      # "BTC-USDT"
            "tdMode": "cash",                              # SPOT
            "side": request.side.value,                    # "buy" / "sell"
            "ordType": "market",                           # market order
            "sz": f"{request.size_usd:.4f}",
            "tgtCcy": "quote_ccy",                         # interpret sz as USDT
        }
        if request.client_order_id:
            # OKX clOrdId: alphanumeric only, max 32 chars
            cid = "".join(c for c in request.client_order_id if c.isalnum())[:32]
            if cid:
                body["clOrdId"] = cid
        if request.order_type == OrderType.LIMIT_POST_ONLY:
            if request.limit_price is None or request.limit_price <= 0:
                return _rejected("BAD-LIMIT-PRICE", "limit_price required", ts_ms)
            body["ordType"] = "post_only"
            body["px"] = f"{request.limit_price}"
            # Limit orders need base_ccy size; user must convert before passing.
            # For now reject post-only on this fast path (Phase 14.3 work).
            return _rejected(
                "POST-ONLY-NOT-IMPL",
                "post_only requires base_ccy size — Phase 14.3",
                ts_ms,
            )

        try:
            resp = self._request("POST", "/api/v5/trade/order", body)
        except requests.RequestException as e:
            return _rejected("NET-ERROR", f"request_error: {e!r}", ts_ms)

        if str(resp.get("code", "")) != "0":
            return _rejected(
                "OKX-ERROR",
                f"code={resp.get('code')} msg={resp.get('msg')} detail={resp.get('data')}",
                ts_ms,
            )

        # data = [{"clOrdId":..., "ordId":..., "tag":..., "sCode":"0", "sMsg":""}]
        order_data_list = resp.get("data") or []
        if not order_data_list:
            return _rejected("EMPTY-RESPONSE", "no order data in response", ts_ms)
        order_data = order_data_list[0]
        s_code = str(order_data.get("sCode", ""))
        if s_code != "0":
            return _rejected(
                "OKX-REJECTED",
                f"sCode={s_code} sMsg={order_data.get('sMsg')}",
                ts_ms,
            )

        ord_id = order_data.get("ordId", "")
        # OKX market orders return immediately with ordId; fill details via
        # /api/v5/trade/order GET. Best-effort fill query (skip on failure).
        fill_price, fill_size, fee_usd = self._query_fill(request.ticker, ord_id)

        return OrderResult(
            status=OrderStatus.FILLED if fill_size > 0 else OrderStatus.PENDING,
            order_id=ord_id,
            filled_size_usd=fill_size,
            avg_fill_price=fill_price,
            fee_usd=fee_usd,
            slippage_bps=0.0,  # OKX doesn't report directly — caller computes vs last
            ts_ms=ts_ms,
            raw_response=resp,
        )

    def _query_fill(self, inst_id: str, ord_id: str) -> tuple[float, float, float]:
        """Query order fill details. Returns (avg_fill_price, filled_usd, fee_usd).

        Best-effort: returns zeros on error so caller treats as PENDING.
        """
        if not ord_id:
            return 0.0, 0.0, 0.0
        path = f"/api/v5/trade/order?instId={inst_id}&ordId={ord_id}"
        try:
            resp = self._request("GET", path)
        except requests.RequestException:
            return 0.0, 0.0, 0.0
        if str(resp.get("code", "")) != "0":
            return 0.0, 0.0, 0.0
        data_list = resp.get("data") or []
        if not data_list:
            return 0.0, 0.0, 0.0
        d = data_list[0]
        try:
            avg_px = float(d.get("avgPx") or 0)
            fill_sz = float(d.get("fillSz") or 0)  # base ccy
            fee = abs(float(d.get("fee") or 0))    # OKX fee is signed (negative for paid)
            # filled_usd = base * price
            filled_usd = avg_px * fill_sz
            return avg_px, filled_usd, fee
        except (ValueError, TypeError):
            return 0.0, 0.0, 0.0

    def cancel_order(self, order_id: str) -> bool:
        if not _live_armed():
            return False
        try:
            resp = self._request(
                "POST", "/api/v5/trade/cancel-order",
                {"ordId": order_id},
            )
            return str(resp.get("code", "")) == "0"
        except requests.RequestException:
            return False

    def get_balance(self) -> dict[str, float]:
        """GET /api/v5/account/balance — returns {ccy: available_amount}."""
        if not _live_armed():
            return {}
        try:
            resp = self._request("GET", "/api/v5/account/balance")
        except requests.RequestException:
            return {}
        if str(resp.get("code", "")) != "0":
            return {}
        out: dict[str, float] = {}
        for entry in resp.get("data") or []:
            for d in entry.get("details", []) or []:
                ccy = d.get("ccy")
                avail = d.get("availBal") or d.get("cashBal") or "0"
                try:
                    out[ccy] = float(avail)
                except (ValueError, TypeError):
                    continue
        return out

    def get_positions(self) -> list[dict]:
        """GET /api/v5/account/positions — returns list of position dicts.

        For SPOT, OKX returns each non-zero base ccy as a "position" via
        /asset/balances. For SPOT positions on demo, prefer
        /asset/balances (more accurate for SPOT base qty). Returns:
            [{ccy: "BTC", qty: 0.05, ...}, ...]
        """
        if not _live_armed():
            return []
        try:
            resp = self._request("GET", "/api/v5/asset/balances")
        except requests.RequestException:
            return []
        if str(resp.get("code", "")) != "0":
            return []
        out: list[dict] = []
        for d in resp.get("data") or []:
            ccy = d.get("ccy")
            try:
                bal = float(d.get("bal") or 0)
                avail = float(d.get("availBal") or 0)
            except (ValueError, TypeError):
                continue
            if ccy and bal > 0:
                out.append({"ccy": ccy, "bal": bal, "avail": avail})
        return out


def _rejected(order_id: str, error_msg: str, ts_ms: int) -> OrderResult:
    """Helper — build a uniform REJECTED OrderResult."""
    return OrderResult(
        status=OrderStatus.REJECTED, order_id=order_id,
        filled_size_usd=0.0, avg_fill_price=0.0,
        fee_usd=0.0, slippage_bps=0.0, ts_ms=ts_ms,
        error_msg=error_msg,
    )
