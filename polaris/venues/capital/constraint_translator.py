"""Capital.com market constraint translator.

Spec source:
- ``GET /api/v1/markets/{epic}`` — returns ``dealingRules`` (minDealSize,
  minSizeIncrement = the SIZE increment; minStepDistance is a stop/limit
  PRICE distance, kept only as a fallback), instrument (lotSize,
  valueOfOnePip, currencies, type), and snapshot (decimalPlacesFactor).
- tests/fixtures/capital_markets/*.json — REAL demo payload captures (RO GET
  probe 2026-06-11) that calibrate the size semantics: ``size`` is UNDERLYING
  UNITS (``lotSize`` = 1, ``valueOfOnePip`` absent on every probed epic), so
  one size unit's USD exposure = ``price × lotSize × quote→USD``. Leverage
  scales MARGIN only — it NEVER enters the lot conversion or the exposure.

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
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Any, Final

import httpx

from polaris.core.streams import fallback_leverage_for_asset_class
from polaris.venues.capital.opening_hours import OpeningHoursWeek, parse_opening_hours

__all__ = [
    "CAPITAL_MARKET_DETAIL_PATH",
    "CapitalMarketConstraint",
    "OpeningHoursWeek",
    "ceil_size_to_step",
    "fetch_market_detail",
    "payload_to_constraint",
    "round_size_to_step",
    "size_usd_to_lots",
    "unit_notional_usd",
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
    # Bug C fix (defaults keep every existing constructor byte-identical):
    # ``lot_size`` = underlying units per 1.0 size (live payloads: always 1);
    # ``snapshot_mid`` = (bid+offer)/2 at fetch time — the quote→USD rate
    # fallback when no bars exist for the conversion pair (0.0 = missing).
    lot_size: float = 1.0
    snapshot_mid: float = 0.0
    # Per-epic UTC session windows (``instrument.openingHours`` parsed) — the
    # source for the per-epic EOD close probe. None = absent/unparseable/non-UTC
    # → the EOD path uses the legacy asset-class fallback. Defaulted so every
    # existing constructor stays byte-identical (same pattern as ``lot_size``).
    opening_hours: OpeningHoursWeek | None = None


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


def ceil_size_to_step(value: float, step: float) -> float:
    """Round ``value`` UP to the nearest multiple of ``step``."""
    if step <= 0.0:
        return value
    if not math.isfinite(value) or not math.isfinite(step):
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quanta = (d_val / d_step).to_integral_value(rounding=ROUND_CEILING)
    return float(quanta * d_step)


def unit_notional_usd(
    *,
    constraint: CapitalMarketConstraint,
    last_price: float,
    quote_usd_rate: float = 1.0,
) -> float:
    """USD gross exposure of ONE size unit: ``price × lotSize × quote→USD``.

    Calibrated on the live demo payloads (tests/fixtures/capital_markets):
    1.0 size of GOLD@4164 ≈ $4,164; 0.1 size of J225@64,637 JPY ≈ $42.
    ``leverage`` NEVER enters — it scales margin, not exposure.
    """
    if last_price <= 0.0 or quote_usd_rate <= 0.0:
        return 0.0
    factor = constraint.lot_size if constraint.lot_size > 0.0 else 1.0
    return last_price * factor * quote_usd_rate


def size_usd_to_lots(
    *,
    constraint: CapitalMarketConstraint,
    notional_usd: float,
    last_price: float,
    quote_usd_rate: float = 1.0,
) -> float:
    """Convert a T4 USD notional into a venue size (lots).

    ``raw = notional / unit_notional_usd`` — dimensionally exact (the prior
    ``notional / pip_value_usd`` was a unit mismatch; live payloads carry no
    ``valueOfOnePip`` at all). Rounding policy (Bug C design §min_deal):

    * raw below ``min_deal_size`` → ceil-to-step(min): the venue floor is
      EXPRESSED, never skipped (flow_not_block);
    * otherwise floor-to-step(raw) so the submit never exceeds the T4 target;
      a floor that lands below min re-surfaces at ceil-to-step(min);
    * payload-defect edges: min=0 → floor only (a zero floor takes one step);
      step=0 → raw passes through unquantized.
    """
    unit = unit_notional_usd(
        constraint=constraint, last_price=last_price, quote_usd_rate=quote_usd_rate
    )
    if unit <= 0.0 or notional_usd <= 0.0:
        return 0.0
    raw = notional_usd / unit
    min_deal = constraint.min_deal_size
    step = constraint.step_size
    if min_deal > 0.0 and raw < min_deal:
        return ceil_size_to_step(min_deal, step)
    lots = round_size_to_step(raw, step)
    if min_deal > 0.0 and lots < min_deal:
        return ceil_size_to_step(min_deal, step)
    if lots <= 0.0:
        return step if step > 0.0 else 0.0
    return lots


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
    if not quote_ccy:
        # Live payloads carry the quote ccy in ``instrument.currency`` (the
        # ``currencies`` list is absent) — J225 is JPY-quoted, most are USD.
        quote_ccy = str(instrument.get("currency") or "")
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
    lot_size = _safe_float(instrument.get("lotSize", 1.0))
    bid = _safe_float(snapshot.get("bid"))
    offer = _safe_float(snapshot.get("offer"))
    snapshot_mid = (bid + offer) / 2.0 if bid > 0.0 and offer > 0.0 else 0.0
    return CapitalMarketConstraint(
        epic=epic,
        instrument_type=instrument_type,
        min_deal_size=_safe_float(_rule_value(rules, "minDealSize")),
        # ``minSizeIncrement`` is the SIZE increment; ``minStepDistance`` is a
        # stop/limit PRICE distance — they diverge on 5/6 live captures
        # (EURUSD 100 vs 1e-05). The old chain is kept as fallback only.
        step_size=_safe_float(_rule_value(rules, "minSizeIncrement"))
        or _safe_float(_rule_value(rules, "minStepDistance"))
        or _safe_float(instrument.get("lotSize", 1.0)),
        decimal_places=int(_safe_float(snapshot.get("decimalPlacesFactor", 0))),
        leverage=leverage,
        pip_value_usd=_safe_float(instrument.get("valueOfOnePip", 0.0)),
        base_ccy=base_ccy,
        quote_ccy=quote_ccy,
        lot_size=lot_size if lot_size > 0.0 else 1.0,
        snapshot_mid=snapshot_mid,
        opening_hours=parse_opening_hours(instrument.get("openingHours") or {}),
    )


# Public alias (Bug C): the production constraint cache parses payloads it
# fetched through the loop-owned ``CapitalSession``. The private name is KEPT
# (existing imports in tests/test_stream_config.py target it directly).
payload_to_constraint = _payload_to_constraint


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
