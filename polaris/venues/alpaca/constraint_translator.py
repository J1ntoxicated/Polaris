"""Alpaca US-equity asset constraint translator.

Spec source:
- Alpaca ``GET /v2/assets`` (and ``/v2/assets/{symbol}``) — returns
  ``class`` / ``tradable`` / ``fractionable`` / ``shortable`` /
  ``easy_to_borrow`` / ``status`` / (optional) ``min_order_size`` /
  ``price_increment``.
- vault/30_components/layer-3-sizing-risk.md (size USD → shares; notional path
  avoids share-rounding, so the constraint is used mainly for tradability /
  short / fractionability gating and price-increment rounding).

For US equities we submit ``notional`` (USD) on the order, so size translation
is mostly used to gate (tradable / fractionable / shortable) and to round a
reference price to ``price_increment``. Mirrors
``polaris/venues/okx/constraint_translator.py`` (``InstrumentConstraint`` shape):
a frozen-slots dataclass, a ``fetch_assets`` startup cache, and pure helpers.

``fetch_assets`` is given the adapter (which owns the authed trade client) so
the asset list goes through the same header-auth path; no I/O lives in the
pure helpers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any, Final

__all__ = [
    "ALPACA_ASSETS_PATH",
    "AssetConstraint",
    "asset_row_to_constraint",
    "fetch_assets",
    "round_price_to_increment",
]

if TYPE_CHECKING:
    from polaris.venues.alpaca.adapter import AlpacaAdapter

ALPACA_ASSETS_PATH: Final[str] = "/v2/assets"

# Defaults when the asset payload omits the field (the live probe showed AAPL
# returns ``min_order_size``/``price_increment`` as ``null``). A fractionable
# equity trades down to a small notional; a non-fractionable one is whole-share
# (1.0). Penny tick is the default US-equity price increment.
_FRACTIONABLE_MIN_ORDER_SIZE: Final[float] = 0.0001
_WHOLE_SHARE_MIN_ORDER_SIZE: Final[float] = 1.0
_DEFAULT_PRICE_INCREMENT: Final[float] = 0.01


@dataclass(frozen=True, slots=True)
class AssetConstraint:
    """Trading constraints for a single Alpaca US-equity asset."""

    symbol: str
    tradable: bool
    fractionable: bool
    shortable: bool
    easy_to_borrow: bool
    min_order_size: float  # shares (fractionable → tiny; whole-share → 1.0)
    price_increment: float  # price step (penny default)


def round_price_to_increment(price: float, increment: float) -> float:
    """Round ``price`` down to the nearest ``increment`` (Decimal — no drift)."""
    if increment <= 0.0:
        return price
    if not math.isfinite(price) or not math.isfinite(increment):
        return 0.0
    d_val = Decimal(str(price))
    d_step = Decimal(str(increment))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(quanta * d_step)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _as_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) and f > 0.0 else default


def asset_row_to_constraint(row: dict[str, Any]) -> AssetConstraint | None:
    """Convert one ``/v2/assets`` row to an :class:`AssetConstraint` (or None)."""
    symbol = str(row.get("symbol") or "")
    if not symbol:
        return None
    fractionable = _as_bool(row.get("fractionable"))
    default_min = (
        _FRACTIONABLE_MIN_ORDER_SIZE if fractionable else _WHOLE_SHARE_MIN_ORDER_SIZE
    )
    return AssetConstraint(
        symbol=symbol,
        tradable=_as_bool(row.get("tradable")),
        fractionable=fractionable,
        shortable=_as_bool(row.get("shortable")),
        easy_to_borrow=_as_bool(row.get("easy_to_borrow")),
        min_order_size=_as_float(row.get("min_order_size"), default_min),
        price_increment=_as_float(row.get("price_increment"), _DEFAULT_PRICE_INCREMENT),
    )


async def fetch_assets(
    adapter: AlpacaAdapter,
    *,
    asset_class: str = "us_equity",
    status: str = "active",
) -> dict[str, AssetConstraint]:
    """Fetch active tradable assets → map keyed by symbol (startup cache).

    Routes through the adapter's authed trade client (same header auth as
    orders). Invalid rows (no symbol) are skipped, mirroring the OKX
    translator's row filter.
    """
    body = await adapter.request_json(
        "GET",
        ALPACA_ASSETS_PATH,
        params={"asset_class": asset_class, "status": status},
    )
    rows = body if isinstance(body, list) else []
    out: dict[str, AssetConstraint] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        c = asset_row_to_constraint(row)
        if c is not None:
            out[c.symbol] = c
    return out
