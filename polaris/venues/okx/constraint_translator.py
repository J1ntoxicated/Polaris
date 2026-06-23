"""OKX SPOT instrument constraint translator.

Spec source:
- OKX `/api/v5/public/instruments` (instType=SPOT) — returns lotSz / minSz /
  tickSz / quoteCcy.
- vault/30_components/layer-3-sizing-risk.md (size USD → base ccy via
  per-symbol tickSz / lotSz).

For SPOT with ``tdMode=cash`` and ``tgtCcy=quote_ccy`` we send notional in
USDT directly, so size translation is mostly used to:
- Round prices to tickSz (px field on IOC orders)
- Validate that the chosen notional is at least minSz × last_price
- Round quantities (sz field) to lotSz when ``tgtCcy=base_ccy``

Pure functions, no I/O. The adapter (``adapter.py``) calls
``fetch_instruments`` once at startup and caches the dict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any, Final

import httpx

__all__ = [
    "OKX_INSTRUMENTS_PATH",
    "InstrumentConstraint",
    "clamp_up_to_min",
    "fetch_instruments",
    "round_down_to_step",
    "round_price_to_tick",
    "round_up_to_step",
    "validate_min_notional",
]

OKX_INSTRUMENTS_PATH: Final[str] = "/api/v5/public/instruments"
REST_TIMEOUT_SEC: Final[float] = 15.0


@dataclass(frozen=True, slots=True)
class InstrumentConstraint:
    """Trading constraints for a single OKX SPOT instrument."""

    inst_id: str
    base_ccy: str
    quote_ccy: str
    lot_sz: float  # base ccy step (sz field)
    min_sz: float  # minimum order size in base ccy
    tick_sz: float  # price step (px field)
    state: str  # "live" / "suspend" / "preopen"


def round_down_to_step(value: float, step: float) -> float:
    """Round ``value`` down to the nearest multiple of ``step``.

    Uses Decimal to avoid float drift on small step sizes (e.g. 0.00000001).
    """
    if step <= 0.0:
        return value
    if not math.isfinite(value) or not math.isfinite(step):
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(quanta * d_step)


def round_up_to_step(value: float, step: float) -> float:
    """Round ``value`` UP to the nearest multiple of ``step`` (Decimal-safe).

    The ceil counterpart of ``round_down_to_step`` — used by the min-size
    clamp-up so the floored-to-min qty is a valid lot multiple that is still
    ``>= min`` (never rounds the min DOWN below the venue minimum).
    """
    if step <= 0.0:
        return value
    if not math.isfinite(value) or not math.isfinite(step):
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_UP)
    return float(quanta * d_step)


def round_price_to_tick(price: float, tick_sz: float) -> float:
    """Round price to nearest tick (away from zero on positive prices)."""
    return round_down_to_step(price, tick_sz)


def clamp_up_to_min(base_qty: float, *, min_sz: float, lot_sz: float) -> float:
    """Bump a sub-minimum order size UP to the venue minimum so the order FLOWS.

    Jin 2026-06-23 (flow_not_block, AGGRESSIVE): when the sized base qty is below
    the instrument minimum (``min_sz``), an OKX order is rejected 51020 below-min
    and cannot trade. Instead of skipping/cooling-down the symbol, raise the qty
    UP to ``min_sz`` (rounded UP to the next ``lot_sz`` multiple so it is a valid
    lot AND still ``>= min``). Slightly larger, never smaller, never blocked.

    This is a venue-constraint FLOOR applied at the LAST step before submit — it
    is NOT a sizing multiplier and never enters the T4 chain (the 9-stack
    invariant is intact; a floor is not a ``<=1`` multiplier).

    A qty already ``>= min_sz`` is returned UNCHANGED (no-op above min →
    byte-identical). ``min_sz <= 0`` (no constraint) is also a no-op.
    """
    if min_sz <= 0.0 or base_qty >= min_sz:
        return base_qty
    if lot_sz > 0.0:
        return round_up_to_step(min_sz, lot_sz)
    return min_sz


def validate_min_notional(
    *,
    constraint: InstrumentConstraint,
    notional_usd: float,
    last_price: float,
) -> tuple[bool, str]:
    """Return (ok, reason). Reason empty when ok."""
    if last_price <= 0.0:
        return False, "last_price_non_positive"
    base_qty = notional_usd / last_price
    if base_qty < constraint.min_sz:
        return (
            False,
            f"below_min_sz qty={base_qty:.8f} min={constraint.min_sz}",
        )
    return True, ""


async def fetch_instruments(
    *,
    base_url: str,
    inst_type: str = "SPOT",
    client: httpx.AsyncClient | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, InstrumentConstraint]:
    """Fetch all live SPOT instruments and return a map keyed by inst_id."""
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        headers = dict(extra_headers or {})
        resp = await cli.get(
            OKX_INSTRUMENTS_PATH,
            params={"instType": inst_type},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await cli.aclose()
    if str(body.get("code", "0")) != "0":
        raise RuntimeError(f"OKX instruments error: code={body.get('code')} msg={body.get('msg')}")
    out: dict[str, InstrumentConstraint] = {}
    for row in body.get("data", []) or []:
        c = _row_to_constraint(row)
        if c is not None:
            out[c.inst_id] = c
    return out


def _row_to_constraint(row: dict[str, Any]) -> InstrumentConstraint | None:
    inst_id = str(row.get("instId") or "")
    if not inst_id:
        return None
    try:
        return InstrumentConstraint(
            inst_id=inst_id,
            base_ccy=str(row.get("baseCcy") or ""),
            quote_ccy=str(row.get("quoteCcy") or ""),
            lot_sz=float(row.get("lotSz") or 0.0),
            min_sz=float(row.get("minSz") or 0.0),
            tick_sz=float(row.get("tickSz") or 0.0),
            state=str(row.get("state") or "live"),
        )
    except (TypeError, ValueError):
        return None
