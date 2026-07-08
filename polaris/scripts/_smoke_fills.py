"""Day 5 paper-loop smoke — simulated fill helpers (split out for line budget).

Builds venue-shaped payloads from internal signals/trades and runs them
through ``polaris.core.data.fill_normalizer``. Pure functions; the smoke
loop owns all state.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

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
        "size": 1.0,
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
        fill = normalize_capital_confirm(
            _capital_fill_payload(
                epic=signal.symbol,
                side=signal.side,
                level=last_price,
                is_close=False,
            ),
            strategy_id=signal.strategy_id,
            pip_value_usd=10.0,
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
    return normalize_capital_confirm(
        _capital_fill_payload(
            epic=trade.symbol,
            side=trade.side,
            level=exit_price,
            is_close=True,
        ),
        strategy_id=trade.strategy_id,
        pip_value_usd=10.0,
        expected_price=exit_price,
    )
