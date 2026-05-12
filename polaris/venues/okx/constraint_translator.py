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
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

import httpx

__all__ = [
    "OKX_INSTRUMENTS_PATH",
    "InstrumentConstraint",
    "fetch_instruments",
    "round_down_to_step",
    "round_price_to_tick",
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


def round_price_to_tick(price: float, tick_sz: float) -> float:
    """Round price to nearest tick (away from zero on positive prices)."""
    return round_down_to_step(price, tick_sz)


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
