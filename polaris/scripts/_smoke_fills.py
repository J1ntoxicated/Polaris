"""Day 5 paper-loop smoke — simulated fill helpers (split out for line budget).

Builds venue-shaped payloads from internal signals/trades and runs them
through ``polaris.core.data.fill_normalizer``. Pure functions; the smoke
loop owns all state.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Final

from polaris.core.data.fill_normalizer import (
    Fill,
    normalize_alpaca_fill,
    normalize_capital_confirm,
    normalize_okx_fill,
)

# SimulatedTrade moved to ``polaris.core.lifecycle.trade`` (it is a pure core-layer
# dataclass; ``core.lifecycle.recover`` consumes it, so importing it UP from scripts
# was a layer inversion). Re-exported here so every existing
# ``from polaris.scripts._smoke_fills import SimulatedTrade`` import is byte-identical.
from polaris.core.lifecycle.trade import SimulatedTrade
from polaris.strategies.base import RawSignal

__all__ = [
    "SimulatedTrade",
    "simulate_close",
    "simulate_open_fill",
]

# Capital-sim legacy pip-value placeholder — kept ONLY as the degenerate
# (notional<=0 / price<=0) fallback now (flow_not_block). It used to ALSO
# drive the "real" path via ``size_usd = size x pip_value_usd x leverage``
# (Capital $10-flat collapse fix, live 2026-07-07+), but that formula ties
# ``size`` to ``notional/10`` with NO relationship to price — every OTHER
# consumer of ``Fill.base_qty`` ("quantity of underlying", the SAME contract
# OKX's ``accFillSz`` and Alpaca's ``filled_qty`` already honour, e.g.
# ``risk_usd_at_entry``) then computed a unit ~750x too large on a
# ~$6,300-priced instrument (US500: notional/10=5000 vs the true
# notional/price ~7.9 -> risk_usd stamped ~$248,535 vs the true ~$330) and
# ~10x too SMALL on a ~$1-priced FX pair. ``_capital_sim_units`` below fixes
# this: ``size`` is derived from price (matching OKX/Alpaca) and paired with
# ``contract_factor_usd=1.0`` (the Bug C real-exposure path already in
# ``normalize_capital_confirm``) so ``size_usd = size * level * 1.0`` still
# equals the requested notional exactly — ``size_usd`` AND ``base_qty`` are
# now both correct from the same expression, instead of only ``size_usd``.
_CAPITAL_SIM_PIP_VALUE_USD: Final[float] = 10.0


def _capital_sim_units(notional_usd: float, last_price: float) -> tuple[float, float | None]:
    """T4 notional + fill price -> simulated Capital ``(size, contract_factor_usd)``.

    Returns ``size = notional_usd / last_price`` (the SAME "quantity of
    underlying per 1x price" OKX/Alpaca's sim branches already use) paired
    with ``contract_factor_usd=1.0`` so ``normalize_capital_confirm``'s Bug C
    real-exposure path (``size_usd = size * level * contract_factor_usd``)
    reproduces ``size_usd == notional_usd`` exactly while ``base_qty == size``
    is now real units, not a price-blind ``notional/10``.

    A non-positive ``notional_usd`` or ``last_price`` degrades to the old
    1-lot/$10 legacy placeholder (``contract_factor_usd=None`` routes
    ``normalize_capital_confirm`` back to its ``pip_value_usd`` formula)
    rather than raising or dividing by zero (flow_not_block)."""
    if notional_usd <= 0.0 or last_price <= 0.0:
        return 1.0, None
    return notional_usd / last_price, 1.0


def _okx_fill_payload(
    *,
    instrument: str,
    side: str,  # "long" / "short"
    notional_usd: float,
    avg_price: float,
    is_close: bool,
) -> dict[str, Any]:
    flipped = ("sell" if side == "long" else "buy") if is_close else (
        "buy" if side == "long" else "sell"
    )
    return {
        "ordId": uuid.uuid4().hex[:16],
        "clOrdId": uuid.uuid4().hex[:12],
        "instId": instrument,
        "tdMode": "cash",
        "side": flipped,
        "ordType": "ioc",
        "tgtCcy": "quote_ccy",
        "sz": str(notional_usd),  # request sz is quote ($) for tgtCcy=quote_ccy
        # accFillSz is the filled BASE-ccy qty (notional / price) — OKX reports
        # fills in base ccy regardless of the quote request sz.
        "accFillSz": str(notional_usd / avg_price if avg_price > 0.0 else 0.0),
        "avgPx": str(avg_price),
        "fee": str(-notional_usd * 0.001),
        "feeCcy": "USDT",
        "state": "filled",
        "uTime": str(int(time.time() * 1000)),
    }


def _alpaca_fill_payload(
    *,
    symbol: str,
    side: str,  # "long" / "short"
    notional_usd: float,
    avg_price: float,
    is_close: bool,
) -> dict[str, Any]:
    flipped = ("sell" if side == "long" else "buy") if is_close else (
        "buy" if side == "long" else "sell"
    )
    return {
        "id": uuid.uuid4().hex[:16],
        "client_order_id": uuid.uuid4().hex[:12],
        "symbol": symbol,
        "side": flipped,
        "status": "filled",
        # Alpaca US equity is unlevered cash — qty is derived from notional/price
        # (mirrors the OKX quote_ccy accFillSz derivation above).
        "filled_qty": str(notional_usd / avg_price if avg_price > 0.0 else 0.0),
        "filled_avg_price": str(avg_price),
        "filled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _capital_fill_payload(
    *,
    epic: str,
    side: str,  # "long" / "short"
    level: float,
    is_close: bool,
    size: float = 1.0,
) -> dict[str, Any]:
    direction = (
        ("SELL" if side == "long" else "BUY")
        if is_close
        else ("BUY" if side == "long" else "SELL")
    )
    return {
        "dealReference": uuid.uuid4().hex[:16],
        "dealId": uuid.uuid4().hex[:16],
        "epic": epic,
        "direction": direction,
        "level": level,
        "size": size,
        "status": "CLOSED" if is_close else "OPEN",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def simulate_open_fill(
    *,
    signal: RawSignal,
    venue: str,
    last_price: float,
    notional_usd: float,
) -> tuple[Fill, SimulatedTrade]:
    """Create a deterministic open-side Fill via fill_normalizer."""
    if venue == "okx":
        fill = normalize_okx_fill(
            _okx_fill_payload(
                instrument=signal.symbol,
                side=signal.side,
                notional_usd=notional_usd,
                avg_price=last_price,
                is_close=False,
            ),
            strategy_id=signal.strategy_id,
            expected_price=last_price,
        )
    elif venue == "alpaca":
        fill = normalize_alpaca_fill(
            _alpaca_fill_payload(
                symbol=signal.symbol,
                side=signal.side,
                notional_usd=notional_usd,
                avg_price=last_price,
                is_close=False,
            ),
            strategy_id=signal.strategy_id,
            expected_price=last_price,
        )
    else:
        size, contract_factor_usd = _capital_sim_units(notional_usd, last_price)
        fill = normalize_capital_confirm(
            _capital_fill_payload(
                epic=signal.symbol,
                side=signal.side,
                level=last_price,
                is_close=False,
                size=size,
            ),
            strategy_id=signal.strategy_id,
            pip_value_usd=_CAPITAL_SIM_PIP_VALUE_USD,
            contract_factor_usd=contract_factor_usd,
            expected_price=last_price,
        )
    trade = SimulatedTrade(
        signal_id=signal.signal_id,
        venue=venue,
        symbol=signal.symbol,
        strategy_id=signal.strategy_id,
        side=signal.side,
        entry_price=last_price,
        notional_usd=fill.size_usd,
        open_ts=int(time.time()),
    )
    return fill, trade


def simulate_close(trade: SimulatedTrade, *, exit_price: float) -> Fill:
    """Build the close-side Fill (PnL applied via exit_price drift)."""
    if trade.venue == "okx":
        return normalize_okx_fill(
            _okx_fill_payload(
                instrument=trade.symbol,
                side=trade.side,
                notional_usd=trade.notional_usd,
                avg_price=exit_price,
                is_close=True,
            ),
            strategy_id=trade.strategy_id,
            expected_price=exit_price,
        )
    if trade.venue == "alpaca":
        return normalize_alpaca_fill(
            _alpaca_fill_payload(
                symbol=trade.symbol,
                side=trade.side,
                notional_usd=trade.notional_usd,
                avg_price=exit_price,
                is_close=True,
            ),
            strategy_id=trade.strategy_id,
            expected_price=exit_price,
        )
    size, contract_factor_usd = _capital_sim_units(trade.notional_usd, exit_price)
    return normalize_capital_confirm(
        _capital_fill_payload(
            epic=trade.symbol,
            side=trade.side,
            level=exit_price,
            is_close=True,
            size=size,
        ),
        strategy_id=trade.strategy_id,
        pip_value_usd=_CAPITAL_SIM_PIP_VALUE_USD,
        contract_factor_usd=contract_factor_usd,
        expected_price=exit_price,
    )
