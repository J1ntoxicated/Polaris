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

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from polaris.core.data.canonical import compute_underlying_group_id, okx_candle_to_bar
from polaris.core.data.schema import Bar
from polaris.venues.okx.constraint_translator import (
    InstrumentConstraint,
    clamp_up_to_min,
    round_down_to_step,
    round_price_to_tick,
)
from polaris.venues.okx.signing import build_signed_headers

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SLIPPAGE_BPS",
    "OKX_BASE_DEMO",
    "OKX_CANCEL_ORDER_PATH",
    "OKX_CANDLES_PATH",
    "OKX_PLACE_ORDER_PATH",
    "OKX_PLACE_ALGO_ORDER_PATH",
    "OKXAdapter",
    "OKXAlgoOrderResponse",
    "OKXOrderResponse",
    "fetch_okx_bars",
    "sanitize_clordid",
]

OKX_BASE_DEMO: Final[str] = "https://us.okx.com"
OKX_CANDLES_PATH: Final[str] = "/api/v5/market/candles"
OKX_TICKER_PATH: Final[str] = "/api/v5/market/ticker"
OKX_PLACE_ORDER_PATH: Final[str] = "/api/v5/trade/order"
OKX_CANCEL_ORDER_PATH: Final[str] = "/api/v5/trade/cancel-order"
OKX_PLACE_ALGO_ORDER_PATH: Final[str] = "/api/v5/trade/order-algo"
OKX_CANCEL_ALGO_ORDER_PATH: Final[str] = "/api/v5/trade/cancel-algos"
OKX_ACCOUNT_BALANCE_PATH: Final[str] = "/api/v5/account/balance"
OKX_ACCOUNT_POSITIONS_PATH: Final[str] = "/api/v5/account/positions"
OKX_TRADE_ORDER_PATH: Final[str] = "/api/v5/trade/order"

DEMO_HEADERS: Final[dict[str, str]] = {"x-simulated-trading": "1"}
REST_TIMEOUT_SEC: Final[float] = 15.0
DEFAULT_SLIPPAGE_BPS: Final[float] = 5.0  # 0.05 %, R2 aggressive cap

# #12 — absorb transient OKX order-placement outages (5xx / timeout). AGGRESSIVE:
# a momentary venue/network blip should not drop a trade. Retries are bounded
# and idempotent (clOrdId state-check before each retry → never double-submit).
ORDER_RETRY_MAX: Final[int] = 2  # extra attempts after the initial POST
ORDER_RETRY_BASE_DELAY_SEC: Final[float] = 0.5  # exponential: 0.5s, 1.0s


async def _async_sleep(delay: float) -> None:
    """Indirection so tests can monkeypatch the backoff sleep to a no-op."""
    await asyncio.sleep(delay)

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


@dataclass(frozen=True, slots=True)
class OKXAlgoOrderResponse:
    """Normalized response for ``POST /trade/order-algo`` (conditional stop).

    ``ok=False`` (algo endpoint unavailable for the symbol / venue reject /
    transport blip) is NOT an error — the caller keeps the existing software
    stop as the backstop (flow_not_block: a resting-stop miss never blocks the
    trade, it just degrades to the prior polled exit).
    """

    ok: bool
    algo_id: str | None
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
        # Per-symbol dealing rules (lotSz / tickSz) for sz/px rounding. Empty
        # until ``set_instrument_constraints`` is called from the boot path
        # (fetch_instruments). When empty the order path is byte-identical to
        # the prior un-rounded behaviour (no regression on the cache-miss path).
        self._constraints: dict[str, InstrumentConstraint] = {}

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

    def set_instrument_constraints(
        self, constraints: dict[str, InstrumentConstraint]
    ) -> None:
        """Install the per-symbol dealing rules used for sz/px rounding.

        Called once from the boot path after ``fetch_instruments``. Rounding
        ``sz`` down to ``lotSz`` and ``px`` to ``tickSz`` BEFORE submit is the
        precision root-cause fix for the OKX 400 on the limit/ioc entry leg.
        """
        self._constraints = dict(constraints)

    def _round_px_sz(
        self, inst_id: str, *, px: float, base_qty: float
    ) -> tuple[float, float]:
        """Round ``px`` to tickSz and ``base_qty`` to lotSz, then CLAMP UP to min.

        Returns ``(px, base_qty)`` unchanged on a cache miss (prior behaviour —
        flow_not_block: a missing constraint never blocks the entry). ``sz`` is
        floored to the nearest lot multiple.

        Min-size CLAMP-UP (Jin 2026-06-23, replaces the param-reject per-symbol
        cooldown): when the lot-rounded qty is below the instrument minimum
        (``min_sz``), BUMP it UP to ``min_sz`` (rounded up to lotSz) so the order
        FLOWS at the venue minimum instead of being rejected 51020 below-min.
        This is the LAST step before submit — a venue-constraint FLOOR applied
        AFTER the T4 sizing chain, NOT a sizing multiplier (the 9-stack invariant
        is intact). A qty already ``>= min_sz`` is byte-identical (no-op above
        min). The clamp only raises TOWARD min, never past the per-order hard-MAX
        headroom (which binds upstream at T4 sizing); a degenerate instrument
        whose min itself exceeds the headroom is still submitted at min (below-min
        cannot trade) and flagged.
        """
        c = self._constraints.get(inst_id)
        if c is None:
            return px, base_qty
        rounded_px = round_price_to_tick(px, c.tick_sz) if c.tick_sz > 0.0 else px
        if rounded_px <= 0.0:
            rounded_px = px
        rounded_qty = round_down_to_step(base_qty, c.lot_sz) if c.lot_sz > 0.0 else base_qty
        if rounded_qty <= 0.0:
            rounded_qty = base_qty
        clamped_qty = clamp_up_to_min(rounded_qty, min_sz=c.min_sz, lot_sz=c.lot_sz)
        if clamped_qty > rounded_qty:
            logger.info(
                "[okx/min-clamp] %s sized qty %s < min %s -> clamped UP to %s (flows)",
                inst_id, rounded_qty, c.min_sz, clamped_qty,
            )
        return rounded_px, clamped_qty

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
        json_body: dict[str, Any] | list[Any] | None = None,
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

    async def _place_order_with_retry(
        self,
        *,
        order_body: dict[str, Any],
        inst_id: str,
        cl_ord_id: str,
    ) -> OKXOrderResponse:
        """POST the order, retrying transient 5xx / timeouts with backoff.

        #12 — AGGRESSIVE transient-fault absorption (NOT a throttle): a momentary
        OKX 5xx or a lost response should not silently drop a trade. Bounded to
        ``ORDER_RETRY_MAX`` extra attempts with exponential backoff.

        Idempotency: before every retry we query OKX by ``clOrdId``. If the prior
        attempt actually landed (response was lost, not the order), we return that
        existing order instead of submitting a duplicate. ``clOrdId`` is OKX's
        idempotency key, so a re-POST with the same id would also be rejected —
        the lookup turns that ambiguity into a correct, single-order outcome.
        """
        last_exc: Exception | None = None
        for attempt in range(ORDER_RETRY_MAX + 1):
            if attempt > 0:
                # Before re-submitting, check whether the failed attempt landed.
                existing = await self._lookup_existing_order(
                    inst_id=inst_id, cl_ord_id=cl_ord_id
                )
                if existing is not None:
                    logger.warning(
                        "[okx] order retry short-circuit — clOrdId=%s already landed "
                        "(ordId=%s); not re-submitting (idempotent)",
                        cl_ord_id,
                        existing.venue_order_id,
                    )
                    return existing
                delay = ORDER_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "[okx] order POST retry %d/%d clOrdId=%s after %.2fs backoff",
                    attempt,
                    ORDER_RETRY_MAX,
                    cl_ord_id,
                    delay,
                )
                await _async_sleep(delay)
            try:
                body = await self._signed_request(
                    "POST", OKX_PLACE_ORDER_PATH, json_body=order_body
                )
                return _parse_order_response(body)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status < 500:
                    # Client error (4xx). OKX returns its business body (code +
                    # data[].sCode/sMsg) even on a 400 for a param/precision
                    # reject (bad lotSz/tickSz, sz format). ROOT-CAUSE of the
                    # entry stall: this used to re-raise → FAULT_EXCEPTION →
                    # permanent HARD_HALT. Parse the body into a venue REJECT so
                    # it flows as an EXTERNAL no-fill (flow_not_block), logging
                    # the exact 400 body + request for diagnosis. A 4xx WITHOUT
                    # an OKX body (WAF/HTML/opaque) is a real anomaly → re-raise.
                    parsed = _parse_4xx_reject(exc, cl_ord_id=cl_ord_id, order_body=order_body)
                    if parsed is not None:
                        return parsed
                    raise
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
        assert last_exc is not None  # loop always sets last_exc before exhausting
        logger.error(
            "[okx] order POST failed after %d attempts clOrdId=%s: %s",
            ORDER_RETRY_MAX + 1,
            cl_ord_id,
            last_exc,
        )
        raise last_exc

    async def _lookup_existing_order(
        self, *, inst_id: str, cl_ord_id: str
    ) -> OKXOrderResponse | None:
        """Return the order if ``cl_ord_id`` already exists on OKX, else ``None``.

        Used between retries to avoid double-submission when a prior POST response
        was lost. Any error in the lookup itself returns ``None`` (treat as 'not
        found' → allow the retry to proceed).
        """
        try:
            body = await self._signed_request(
                "GET",
                OKX_TRADE_ORDER_PATH,
                params={"instId": inst_id, "clOrdId": cl_ord_id},
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning("[okx] idempotency lookup failed clOrdId=%s: %s", cl_ord_id, exc)
            return None
        rows = body.get("data") or []
        if not rows or not rows[0].get("ordId"):
            return None
        return _parse_order_response(body)

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
                px, base_qty = self._round_px_sz(inst_id, px=px, base_qty=base_qty)
                order_body["sz"] = _format_decimal(base_qty)
                order_body["px"] = _format_decimal(px)
        else:
            # limit / post_only — caller is responsible for px via last_price_hint.
            if ref_price is None or ref_price <= 0.0:
                raise ValueError(f"{ord_type} requires last_price_hint")
            base_qty = notional_usd / max(ref_price, 1e-12)
            px, base_qty = self._round_px_sz(inst_id, px=ref_price, base_qty=base_qty)
            order_body["sz"] = _format_decimal(base_qty)
            order_body["px"] = _format_decimal(px)
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
        parsed = await self._place_order_with_retry(
            order_body=order_body, inst_id=inst_id, cl_ord_id=cl_ord_id
        )
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

    async def place_conditional_stop(
        self,
        *,
        inst_id: str,
        side: str,
        base_qty: float,
        trigger_px: float,
        client_order_id: str,
        td_mode: str = "cash",
    ) -> OKXAlgoOrderResponse:
        """Place a VENUE-RESTING conditional (stop) order that triggers on OKX's
        side BEFORE the next software poll tick.

        This is the precise-exit loss-defence for the inter-tick gap: a SPOT alt
        can gap through the software stop in the ~5s between recalc passes, and
        the polled market close then fills far below the stop (the -34..-100R
        orphan). A resting ``ordType=conditional`` with ``slTriggerPx`` lets OKX
        trigger the close the instant the trigger is crossed — no poll latency.

        ``trigger_px`` is rounded to the instrument ``tickSz`` and ``base_qty``
        floored to ``lotSz`` (reusing the entry-leg precision helper) so the
        algo submit is not rejected for a bad px/sz format. ``slOrdPx="-1"`` =
        fill at market on trigger (don't leave a resting limit that can miss).

        Graceful (flow_not_block): ANY failure — a 4xx OKX reject, an opaque
        body, a transport blip — returns ``ok=False`` instead of raising. The
        caller keeps the existing software stop, so the trade is never blocked
        and the worst case is simply the prior polled-exit behaviour.
        """
        side_l = side.lower()
        if side_l not in ("buy", "sell"):
            raise ValueError(f"invalid side: {side!r}")
        cl_ord_id = sanitize_clordid(client_order_id)
        px, qty = self._round_px_sz(inst_id, px=trigger_px, base_qty=base_qty)
        # NB: NO ``tgtCcy``. OKX rejects it on an algo SELL with HTTP 400
        # ``51000 Parameter tgtCcy error`` (tgtCcy is only valid for a spot
        # market BUY). Sending it was the bug that made EVERY resting stop fall
        # back to the software poll. Confirmed live (us.okx.com demo): with
        # tgtCcy → 400; without → accepted (sCode 0 / balance-bound reject only).
        order_body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side_l,
            "ordType": "conditional",
            "sz": _format_decimal(qty),
            "slTriggerPx": _format_decimal(px),
            "slTriggerPxType": "last",
            "slOrdPx": "-1",  # market on trigger — never a resting limit that misses
            "algoClOrdId": cl_ord_id,
        }
        logger.info(
            "[okx] algo-stop POST %s side=%s sz=%s slTriggerPx=%s algoClOrdId=%s",
            inst_id, side_l, order_body["sz"], order_body["slTriggerPx"], cl_ord_id,
        )
        try:
            body = await self._signed_request(
                "POST", OKX_PLACE_ALGO_ORDER_PATH, json_body=order_body
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            # flow_not_block: a resting-stop failure degrades to the software stop,
            # never raises. Captures 4xx (HTTPStatusError), transport blips, and a
            # non-dict payload (RuntimeError from _signed_request).
            #
            # OKX returns business rejects (bad param, etc.) as HTTP 400 with the
            # real {code, msg, data:[{sCode, sMsg}]} in the body — so a 4xx is NOT
            # opaque. Parse it and SURFACE the true OKX code/msg instead of the
            # generic ``algo_unavailable`` label (the swallow that hid the 51000
            # tgtCcy reject for weeks). Only a genuinely unparseable body / pure
            # transport blip falls through to ``algo_unavailable``.
            if isinstance(exc, httpx.HTTPStatusError):
                body_txt = exc.response.text[:200]
                try:
                    err_body = exc.response.json()
                except (ValueError, json.JSONDecodeError):
                    err_body = None
                if isinstance(err_body, dict):
                    parsed = _parse_algo_response(err_body)
                    logger.warning(
                        "[okx] algo-stop reject inst=%s code=%s msg=%s — keeping "
                        "software stop",
                        inst_id, parsed.code, parsed.msg[:160] if parsed.msg else "-",
                    )
                    return parsed
            else:
                body_txt = ""
            logger.warning(
                "[okx] algo-stop unavailable inst=%s — keeping software stop: %s %s",
                inst_id, exc, body_txt,
            )
            return OKXAlgoOrderResponse(
                ok=False, algo_id=None, client_order_id=cl_ord_id,
                code="algo_unavailable", msg=str(exc), raw={},
            )
        parsed = _parse_algo_response(body)
        logger.log(
            logging.INFO if parsed.ok else logging.WARNING,
            "[okx] algo-stop RESP ok=%s algoId=%s code=%s msg=%s",
            parsed.ok, parsed.algo_id, parsed.code,
            parsed.msg[:160] if parsed.msg else "-",
        )
        return parsed

    async def cancel_algo_order(self, *, inst_id: str, algo_id: str) -> dict[str, Any]:
        """Cancel a resting conditional stop by ``algoId`` (best-effort).

        Used when the trailing stop tightens (cancel-then-replace) or when the
        software path already closed the position (so the resting stop cannot
        fire a duplicate sell). ``cancel-algos`` takes a LIST of orders.
        """
        logger.info("[okx] cancel_algo inst=%s algoId=%s", inst_id, algo_id)
        return await self._signed_request(
            "POST",
            OKX_CANCEL_ALGO_ORDER_PATH,
            json_body=[{"instId": inst_id, "algoId": algo_id}],
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

    async def fetch_order(
        self,
        *,
        inst_id: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> dict[str, Any]:
        """Query an order by venue ``ordId`` or client ``clOrdId``.

        OKX ``GET /trade/order`` accepts either key; ``clOrdId`` lets us look up
        an order whose POST response we never received (#12 idempotency check).
        """
        if not ord_id and not cl_ord_id:
            raise ValueError("fetch_order requires ord_id or cl_ord_id")
        params: dict[str, Any] = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        else:
            params["clOrdId"] = cl_ord_id
        return await self._signed_request("GET", OKX_TRADE_ORDER_PATH, params=params)


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


def _parse_4xx_reject(
    exc: httpx.HTTPStatusError, *, cl_ord_id: str, order_body: dict[str, Any]
) -> OKXOrderResponse | None:
    """Turn an OKX-bodied HTTP 4xx into a venue reject, or ``None`` if opaque.

    OKX delivers ``{code, msg, data:[{sCode, sMsg}]}`` even on a 400 for a
    param/precision reject. Parsing it into an ``OKXOrderResponse`` reject lets
    the entry leg treat it as an EXTERNAL no-fill instead of a strategy crash
    (the entry-stall root-cause). Logs the exact 400 body + the (non-secret)
    request so the precise failing field is diagnosable. Returns ``None`` when
    the body is not OKX JSON (WAF/HTML/opaque) — the caller re-raises.
    """
    try:
        body = exc.response.json()
    except (ValueError, json.JSONDecodeError):
        body = None
    if not isinstance(body, dict) or "code" not in body:
        logger.error(
            "[okx] order POST %d clOrdId=%s — opaque body (non-OKX), re-raising: %s",
            exc.response.status_code,
            cl_ord_id,
            exc.response.text[:200],
        )
        return None
    # Non-secret request fields only (instId / sz / px / ordType / side).
    logger.error(
        "[okx] order POST %d REJECT clOrdId=%s instId=%s sz=%s px=%s ordType=%s "
        "side=%s body=%s",
        exc.response.status_code,
        cl_ord_id,
        order_body.get("instId"),
        order_body.get("sz"),
        order_body.get("px", "-"),
        order_body.get("ordType"),
        order_body.get("side"),
        json.dumps(body, sort_keys=True)[:300],
    )
    return _parse_order_response(body)


def _parse_algo_response(body: dict[str, Any]) -> OKXAlgoOrderResponse:
    """Parse ``POST /trade/order-algo`` → ``OKXAlgoOrderResponse``.

    OKX returns ``{code, msg, data:[{algoId, algoClOrdId, sCode, sMsg}]}``. A
    non-zero ``sCode`` (algo unavailable / param reject) maps to ``ok=False`` —
    the caller keeps the software stop (flow_not_block).
    """
    code = str(body.get("code", "1"))
    msg = str(body.get("msg", ""))
    rows = body.get("data") or []
    row0 = rows[0] if rows else {}
    inner_code = str(row0.get("sCode", code))
    ok = code == "0" and inner_code == "0"
    return OKXAlgoOrderResponse(
        ok=ok,
        algo_id=str(row0.get("algoId")) if row0.get("algoId") else None,
        client_order_id=str(row0.get("algoClOrdId")) if row0.get("algoClOrdId") else None,
        code=inner_code if inner_code != "0" else code,
        msg=str(row0.get("sMsg") or msg),
        raw=body,
    )


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
