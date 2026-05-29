"""Capital.com market constraint translator.

Spec source:
- ``GET /api/v1/markets/{epic}`` — returns ``dealingRules`` (minDealSize,
  minStepDistance, minControlledRiskStopDistance), instrument
  (lotSize, valueOfOnePip, currencies, type), and snapshot (decimalPlacesFactor).
- vault/30_components/layer-3-sizing-risk.md (size USD → CFD lot via
  per-symbol pip value × leverage).

Pure functions, no I/O beyond ``fetch_market_detail``. P0 leverages:
- Forex majors: 30:1 (retail) / 100:1 (pro)
- Indices: 20:1
- Commodities (XAU/oil): 20:1
- Crypto CFD: 2:1

Capital returns ``leveragePremium`` per market; we read it dynamically rather
than hard-code per asset class.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Final

import httpx

from polaris.core.streams import fallback_leverage_for_asset_class

__all__ = [
    "CAPITAL_MARKET_DETAIL_PATH",
    "CapitalMarketConstraint",
    "fetch_market_detail",
    "round_size_to_step",
    "size_usd_to_lots",
]

CAPITAL_MARKET_DETAIL_PATH: Final[str] = "/api/v1/markets"
REST_TIMEOUT_SEC: Final[float] = 15.0

# Capital ``instrument.type`` → asset_class key for the per-market leverage
# fallback (T7). Used ONLY when the venue payload carries neither ``leverage``
# nor ``marginFactor`` — so CapitalMarketConstraint.leverage is NEVER 0.
_INSTRUMENT_TYPE_TO_ASSET_CLASS: Final[dict[str, str]] = {
    "CURRENCIES": "forex",
    "INDICES": "indices",
    "COMMODITIES": "commodity",
    "CRYPTOCURRENCIES": "crypto",
}


@dataclass(frozen=True, slots=True)
class CapitalMarketConstraint:
    """Trading constraints for a single Capital epic."""

    epic: str
    instrument_type: str  # CURRENCIES / INDICES / COMMODITIES / CRYPTOCURRENCIES
    min_deal_size: float
    step_size: float  # min increment for size
    decimal_places: int
    leverage: float  # margin factor (e.g. 30.0 = 30:1)
    pip_value_usd: float
    base_ccy: str
    quote_ccy: str


def round_size_to_step(value: float, step: float) -> float:
    """Round ``value`` down to nearest multiple of ``step``."""
    if step <= 0.0:
        return value
    if not math.isfinite(value) or not math.isfinite(step):
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(quanta * d_step)


def size_usd_to_lots(
    *,
    constraint: CapitalMarketConstraint,
    notional_usd: float,
    last_price: float,
) -> float:
    """Convert USD notional to lots respecting ``min_deal_size`` + ``step_size``.

    ``last_price`` may be in non-USD quote — we treat ``pip_value_usd`` as the
    pre-converted dollar value of one unit, so lot count = notional / price /
    pip_value_usd. Tests + smoke validate this against Capital's confirm
    endpoint.
    """
    if last_price <= 0.0:
        return 0.0
    raw_lots = notional_usd / max(last_price, 1e-9)
    if constraint.pip_value_usd > 0.0:
        raw_lots = notional_usd / constraint.pip_value_usd
    lots = max(raw_lots, constraint.min_deal_size)
    return round_size_to_step(lots, constraint.step_size)


async def fetch_market_detail(
    *,
    epic: str,
    cst: str,
    security_token: str,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> CapitalMarketConstraint:
    """Fetch the full ``/markets/{epic}`` payload + parse into a constraint."""
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            f"{CAPITAL_MARKET_DETAIL_PATH}/{epic}",
            headers={"CST": cst, "X-SECURITY-TOKEN": security_token},
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await cli.aclose()
    if not isinstance(body, dict):
        raise RuntimeError(f"Capital market detail unexpected payload {type(body).__name__}")
    return _payload_to_constraint(epic, body)


def _payload_to_constraint(epic: str, body: dict[str, Any]) -> CapitalMarketConstraint:
    instrument = body.get("instrument") or {}
    rules = body.get("dealingRules") or {}
    snapshot = body.get("snapshot") or {}
    instrument_type = str(instrument.get("type") or "")
    base_ccy = ""
    quote_ccy = ""
    ccys = instrument.get("currencies") or []
    if isinstance(ccys, list) and ccys:
        first = ccys[0]
        if isinstance(first, dict):
            base_ccy = str(first.get("baseExchangeRate", first.get("code", "")) or "")
            quote_ccy = str(first.get("code", "") or "")
    leverage = _safe_float(instrument.get("leverage", 0.0))
    if leverage <= 0.0:
        # Some endpoints return ``marginFactor`` instead of ``leverage``.
        margin_factor = _safe_float(instrument.get("marginFactor", 0.0))
        leverage = (1.0 / margin_factor) if margin_factor > 0.0 else 0.0
    if leverage <= 0.0:
        # T7: neither live ``leverage`` nor ``marginFactor`` present — apply the
        # /debate-CONFIRMED per-market fallback keyed on instrument_type so the
        # constraint leverage is NEVER 0 (a 0 here would zero out CFD notional).
        # The live venue value always wins above; this is a correctness floor,
        # not a defensive damper.
        asset_class = _INSTRUMENT_TYPE_TO_ASSET_CLASS.get(instrument_type, "")
        leverage = fallback_leverage_for_asset_class(asset_class)
    return CapitalMarketConstraint(
        epic=epic,
        instrument_type=instrument_type,
        min_deal_size=_safe_float(_rule_value(rules, "minDealSize")),
        step_size=_safe_float(_rule_value(rules, "minStepDistance"))
        or _safe_float(instrument.get("lotSize", 1.0)),
        decimal_places=int(_safe_float(snapshot.get("decimalPlacesFactor", 0))),
        leverage=leverage,
        pip_value_usd=_safe_float(instrument.get("valueOfOnePip", 0.0)),
        base_ccy=base_ccy,
        quote_ccy=quote_ccy,
    )


def _rule_value(rules: dict[str, Any], key: str) -> Any:
    block = rules.get(key)
    if isinstance(block, dict):
        return block.get("value", 0.0)
    return block or 0.0


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0
