"""Binance USDⓈ-M Futures instrument constraint translator.

Spec source:
- Binance ``GET /fapi/v1/exchangeInfo`` — returns per-symbol ``filters``:
    * ``LOT_SIZE``    → ``stepSize`` / ``minQty`` (base-qty rounding)
    * ``PRICE_FILTER``→ ``tickSize`` (price rounding)
    * ``MIN_NOTIONAL``→ ``notional`` (minimum order notional in quote ccy)
- vault/30_components/layer-3-sizing-risk.md (size USD → base ccy / contract).

Perp semantics: symbol has no dash (``BTCUSDT``); ``quantity`` is the base
contract qty rounded to ``stepSize``; ``price`` rounded to ``tickSize``. USD
notional → qty is ``notional_usd / price`` floored to ``stepSize``, then
validated against ``minQty`` and ``MIN_NOTIONAL.notional``.

Pure functions, no I/O except :func:`fetch_exchange_info`. The adapter calls
it once at startup and caches the dict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

import httpx

__all__ = [
    "BINANCE_EXCHANGE_INFO_PATH",
    "FuturesConstraint",
    "fetch_exchange_info",
    "round_down_to_step",
    "round_price_to_tick",
    "translate_notional_to_qty",
    "validate_min_notional",
]

BINANCE_EXCHANGE_INFO_PATH: Final[str] = "/fapi/v1/exchangeInfo"
REST_TIMEOUT_SEC: Final[float] = 15.0


@dataclass(frozen=True, slots=True)
class FuturesConstraint:
    """Trading constraints for a single Binance USDⓈ-M perp symbol."""

    symbol: str
    base_ccy: str
    quote_ccy: str
    step_size: float  # base-qty step (quantity field)
    min_qty: float  # minimum order qty in base ccy
    tick_size: float  # price step (price field)
    min_notional: float  # minimum order notional in quote ccy (USDT)
    status: str  # "TRADING" / "BREAK" / ...


def round_down_to_step(value: float, step: float) -> float:
    """Round ``value`` down to the nearest multiple of ``step``.

    Uses Decimal to avoid float drift on small step sizes (e.g. 0.001).
    """
    if step <= 0.0:
        return value
    if not math.isfinite(value) or not math.isfinite(step):
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(quanta * d_step)


def round_price_to_tick(price: float, tick_size: float) -> float:
    """Round price down to the nearest tick."""
    return round_down_to_step(price, tick_size)


def translate_notional_to_qty(
    *, constraint: FuturesConstraint, notional_usd: float, price: float
) -> float:
    """Convert USD notional → base contract qty, floored to ``stepSize``.

    Returns ``0.0`` when ``price`` is non-positive. Caller validates the result
    against :func:`validate_min_notional` before submitting.
    """
    if price <= 0.0:
        return 0.0
    raw_qty = notional_usd / price
    return round_down_to_step(raw_qty, constraint.step_size)


def validate_min_notional(
    *, constraint: FuturesConstraint, qty: float, price: float
) -> tuple[bool, str]:
    """Return (ok, reason). Reason empty when ok."""
    if price <= 0.0:
        return False, "price_non_positive"
    if qty < constraint.min_qty:
        return (
            False,
            f"below_min_qty qty={qty:.8f} min={constraint.min_qty}",
        )
    notional = qty * price
    if notional < constraint.min_notional:
        return (
            False,
            f"below_min_notional notional={notional:.4f} min={constraint.min_notional}",
        )
    return True, ""


async def fetch_exchange_info(
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, FuturesConstraint]:
    """Fetch all symbols' filters and return a map keyed by symbol."""
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(BINANCE_EXCHANGE_INFO_PATH)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await cli.aclose()
    if not isinstance(body, dict) or "symbols" not in body:
        raise RuntimeError(f"Binance exchangeInfo unexpected payload: {type(body).__name__}")
    out: dict[str, FuturesConstraint] = {}
    for row in body.get("symbols", []) or []:
        c = _row_to_constraint(row)
        if c is not None:
            out[c.symbol] = c
    return out


def _row_to_constraint(row: dict[str, Any]) -> FuturesConstraint | None:
    symbol = str(row.get("symbol") or "")
    if not symbol:
        return None
    filters = {f.get("filterType"): f for f in row.get("filters", []) or []}
    lot = filters.get("LOT_SIZE", {})
    price_f = filters.get("PRICE_FILTER", {})
    # MIN_NOTIONAL key varies (notional / minNotional) across symbol versions.
    notional_f = filters.get("MIN_NOTIONAL", {})
    min_notional_raw = notional_f.get("notional") or notional_f.get("minNotional") or 0.0
    try:
        return FuturesConstraint(
            symbol=symbol,
            base_ccy=str(row.get("baseAsset") or ""),
            quote_ccy=str(row.get("quoteAsset") or ""),
            step_size=float(lot.get("stepSize") or 0.0),
            min_qty=float(lot.get("minQty") or 0.0),
            tick_size=float(price_f.get("tickSize") or 0.0),
            min_notional=float(min_notional_raw or 0.0),
            status=str(row.get("status") or "TRADING"),
        )
    except (TypeError, ValueError):
        return None
