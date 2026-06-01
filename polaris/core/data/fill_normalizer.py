"""Layer 1 — venue fill → unified ``Fill`` dataclass.

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (canonical schemas)
- vault/10_decisions/ADR-003-8-layer-architecture.md (unified SQLite per-venue
  ``fills`` row).

Pure functions — no I/O. Smoke / paper-loop scripts feed venue-specific
payloads (OKX ``/trade/order`` GET response or Capital ``/confirms`` payload)
and persist the resulting ``Fill`` rows.

OKX
---
- ``fillSz`` is in base ccy when ``tgtCcy=base_ccy``; in quote ccy when
  ``tgtCcy=quote_ccy`` (our default for SPOT). We normalize using ``avgPx``.
- ``fee`` is signed (negative = paid). We flip to absolute USD.
- ``state`` valid values: ``filled``, ``partially_filled``, ``cancelled``,
  ``live``. Only ``filled``/``partially_filled`` produce a Fill row.

Capital
-------
- ``GET /confirms/{ref}`` returns ``status`` (OPEN/CLOSED/REJECTED), ``level``
  (entry price), ``size``, ``profit``, ``affectedDeals``.
- ``size_usd`` = ``size × pip_value_usd × leverage`` (we expose
  ``pip_value_usd`` as a known per-symbol constant from constraint_translator).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final, Literal

from polaris.core.economics.fees import real_fee_usd

__all__ = [
    "Fill",
    "FillNormalizationError",
    "normalize_alpaca_fill",
    "normalize_capital_confirm",
    "normalize_okx_fill",
]


class FillNormalizationError(ValueError):
    """Raised when a venue payload cannot be normalized to a Fill."""


@dataclass(frozen=True, slots=True)
class Fill:
    """Unified fill row (matches ``fills`` table — ADR-003 §Unified SQLite Schema)."""

    venue: str
    instrument_id: str
    strategy_id: str
    side: Literal["buy", "sell"]
    size_usd: float
    fill_price: float
    fee_usd: float
    slippage_bps: float
    ts_ms: int
    order_id: str
    client_order_id: str | None = None
    base_qty: float = 0.0
    quote_qty: float = 0.0
    state: str = "filled"


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------

OKX_FILLED_STATES: Final[frozenset[str]] = frozenset({"filled", "partially_filled"})


def normalize_okx_fill(
    payload: dict[str, Any],
    *,
    strategy_id: str,
    expected_price: float | None = None,
) -> Fill:
    """Convert a single OKX ``/trade/order`` GET response row into a Fill.

    ``payload`` is the inner row from ``data[0]`` (caller has unwrapped). We
    raise ``FillNormalizationError`` for non-fill states or missing fields so
    callers can skip without hiding errors.
    """
    state = str(payload.get("state") or "").lower()
    if state not in OKX_FILLED_STATES:
        raise FillNormalizationError(f"OKX order not in filled state: {state!r}")
    inst_id = str(payload.get("instId") or "")
    if not inst_id:
        raise FillNormalizationError("OKX fill missing instId")
    side_raw = str(payload.get("side") or "").lower()
    if side_raw not in ("buy", "sell"):
        raise FillNormalizationError(f"OKX fill invalid side {side_raw!r}")
    avg_px = _safe_float(payload.get("avgPx"))
    fill_sz = _safe_float(payload.get("accFillSz"))
    if fill_sz <= 0.0:
        fill_sz = _safe_float(payload.get("fillSz"))
    # OKX SPOT accFillSz/fillSz is ALWAYS the filled BASE-ccy quantity — even
    # when the *request* sz was quote (tgtCcy=quote_ccy, a $-notional market
    # buy). Only the request sz is quote; the fill qty is base. The prior
    # tgtCcy=quote_ccy "flip" mis-treated base accFillSz as quote and corrupted
    # size_usd/base_qty (verified 2026-05-29: a real 0.618-ETH market buy was
    # recorded as $0.62, under-tracking the position and orphaning the rest).
    base_qty = fill_sz
    quote_qty = fill_sz * avg_px
    # fee_usd is the REAL OKX taker fee (10 bps of notional) — NOT the raw demo
    # 'fee' the venue payload reports. OKX demo bills a punitive 70 bps; storing
    # that drained edge-validation (NIG posterior in cost_adjusted_pnl_r) on 7x
    # cost. The whole system measures REAL-venue viability via real_fee_usd
    # (replay gate, dashboard real_fee_total), so the persisted per-fill fee is
    # made consistent here. The demo charge is recomputable from notional via
    # fees.demo_fee_usd(venue, size_usd) where the demo-drain curve is shown.
    fee_usd = real_fee_usd("okx", notional_usd=quote_qty)
    slippage_bps = 0.0
    if expected_price is not None and expected_price > 0.0 and avg_px > 0.0:
        slippage_bps = abs(avg_px - expected_price) / expected_price * 10_000.0
    ts_ms = int(_safe_float(payload.get("uTime") or payload.get("cTime") or time.time() * 1000))
    return Fill(
        venue="okx",
        instrument_id=f"okx:{inst_id}",
        strategy_id=strategy_id,
        side=side_raw,  # type: ignore[arg-type]
        size_usd=quote_qty,
        fill_price=avg_px,
        fee_usd=fee_usd,
        slippage_bps=slippage_bps,
        ts_ms=ts_ms,
        order_id=str(payload.get("ordId") or ""),
        client_order_id=str(payload.get("clOrdId") or "") or None,
        base_qty=base_qty,
        quote_qty=quote_qty,
        state=state,
    )


# ---------------------------------------------------------------------------
# Capital
# ---------------------------------------------------------------------------


def normalize_capital_confirm(
    payload: dict[str, Any],
    *,
    strategy_id: str,
    pip_value_usd: float,
    expected_price: float | None = None,
    fee_usd: float | None = None,
    leverage: float = 1.0,
) -> Fill:
    """Convert a Capital ``/confirms/{ref}`` payload into a Fill.

    ``size_usd`` represents the gross notional (controlled exposure), which
    Capital prices as ``size × pip_value_usd × leverage`` for CFD positions.
    Pass ``leverage=1.0`` to record margin-equivalent (default kept at 1.0
    for backwards compatibility with the smoke loop's deterministic stub —
    real-venue paths should pass the per-epic leverage from
    :class:`CapitalMarketConstraint`).

    ``fee_usd`` defaults to the REAL Capital cost proxy (3 bps of the gross
    notional via :func:`fees.real_fee_usd`) — Capital demo reports ``fee=0``, so
    the stored per-fill fee mirrors the same real-venue cost the rest of the
    system measures. Pass an explicit value to override.
    """
    status = str(payload.get("status") or payload.get("dealStatus") or "").upper()
    if status not in ("OPEN", "CLOSED", "ACCEPTED"):
        raise FillNormalizationError(f"Capital confirm not filled: {status!r}")
    epic = str(payload.get("epic") or "")
    if not epic:
        raise FillNormalizationError("Capital confirm missing epic")
    direction = str(payload.get("direction") or "").upper()
    side: Literal["buy", "sell"] = "buy" if direction == "BUY" else "sell"
    level = _safe_float(payload.get("level"))
    size = _safe_float(payload.get("size"))
    if level <= 0.0 or size <= 0.0:
        raise FillNormalizationError("Capital confirm missing level/size")
    pip = pip_value_usd if pip_value_usd > 0.0 else 1.0
    lev = leverage if leverage > 0.0 else 1.0
    size_usd = size * pip * lev
    # Capital demo reports fee=0 → store the REAL 3 bps proxy on the gross
    # notional (consistent with OKX storing the real taker fee). Explicit
    # override honored.
    if fee_usd is None:
        fee_usd = real_fee_usd("capital", notional_usd=size_usd)
    slippage_bps = 0.0
    if expected_price is not None and expected_price > 0.0:
        slippage_bps = abs(level - expected_price) / expected_price * 10_000.0
    ts_ms = _capital_ts_ms(payload.get("date") or payload.get("dealDate"))
    deal_id = str(payload.get("dealId") or payload.get("dealReference") or "")
    return Fill(
        venue="capital",
        instrument_id=f"capital:{epic}",
        strategy_id=strategy_id,
        side=side,
        size_usd=size_usd,
        fill_price=level,
        fee_usd=fee_usd,
        slippage_bps=slippage_bps,
        ts_ms=ts_ms,
        order_id=deal_id,
        client_order_id=str(payload.get("dealReference") or "") or None,
        base_qty=size,
        quote_qty=size_usd,
        state="filled" if status in ("OPEN", "ACCEPTED") else "closed",
    )


def _capital_ts_ms(value: Any) -> int:
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, (int, float)):
        # Capital sometimes sends ms ts already.
        return int(value if value > 1e12 else value * 1000)
    if isinstance(value, str):
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return int(time.time() * 1000)
    return int(time.time() * 1000)


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


# ---------------------------------------------------------------------------
# Alpaca (US equity — Track C)
# ---------------------------------------------------------------------------

ALPACA_FILLED_STATES: Final[frozenset[str]] = frozenset({"filled", "partially_filled"})


def normalize_alpaca_fill(
    payload: dict[str, Any],
    *,
    strategy_id: str,
    expected_price: float | None = None,
) -> Fill:
    """Convert a single Alpaca ``GET /v2/orders/{id}`` object into a Fill.

    US equity is unlevered cash (1:1): ``size_usd`` is the filled notional
    (``filled_qty × filled_avg_price``) and ``base_qty`` is the filled share
    count. Raises :class:`FillNormalizationError` for non-fill states / missing
    fields so callers can skip without hiding the error (mirror OKX/Capital).
    """
    status = str(payload.get("status") or "").lower()
    if status not in ALPACA_FILLED_STATES:
        raise FillNormalizationError(f"Alpaca order not in filled state: {status!r}")
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        raise FillNormalizationError("Alpaca fill missing symbol")
    side_raw = str(payload.get("side") or "").lower()
    if side_raw not in ("buy", "sell"):
        raise FillNormalizationError(f"Alpaca fill invalid side {side_raw!r}")
    avg_px = _safe_float(payload.get("filled_avg_price"))
    filled_qty = _safe_float(payload.get("filled_qty"))
    if avg_px <= 0.0 or filled_qty <= 0.0:
        raise FillNormalizationError("Alpaca fill missing filled_avg_price/filled_qty")
    quote_qty = filled_qty * avg_px
    slippage_bps = 0.0
    if expected_price is not None and expected_price > 0.0:
        slippage_bps = abs(avg_px - expected_price) / expected_price * 10_000.0
    ts_ms = _capital_ts_ms(payload.get("filled_at") or payload.get("updated_at"))
    return Fill(
        venue="alpaca",
        instrument_id=f"alpaca:{symbol}",
        strategy_id=strategy_id,
        side=side_raw,  # type: ignore[arg-type]
        size_usd=quote_qty,
        fill_price=avg_px,
        fee_usd=0.0,  # Alpaca US equity = commission-free.
        slippage_bps=slippage_bps,
        ts_ms=ts_ms,
        order_id=str(payload.get("id") or ""),
        client_order_id=str(payload.get("client_order_id") or "") or None,
        base_qty=filled_qty,
        quote_qty=quote_qty,
        state="filled" if status == "filled" else "partially_filled",
    )
