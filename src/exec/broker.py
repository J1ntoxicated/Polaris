"""Broker abstraction — paper / live unified interface (P6 pure dataclasses).

Phase 14: Both paper engine (PaperBroker) and live OKX (OKXBroker) implement
this interface. Lets the same trading logic produce paper or live orders
based on configuration only.

Order semantics:
- BUY = SPOT long entry (consume ask side)
- SELL = SPOT long exit (hit bid side)
- MARKET = taker (immediate, fee × 2)
- LIMIT_POST_ONLY = maker (queued, lower fee, may not fill)

Pure dataclasses. Implementations live in paper_broker.py / okx_broker.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT_POST_ONLY = "limit_post_only"  # maker — Layer 4 slippage saving


class OrderStatus(str, Enum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    PENDING = "pending"   # limit posted, not yet filled


@dataclass(frozen=True)
class OrderRequest:
    """Pre-execution order specification."""
    side: OrderSide
    ticker: str
    size_usd: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None  # required for LIMIT_POST_ONLY
    client_order_id: Optional[str] = None  # idempotency key

    def __post_init__(self) -> None:
        if self.size_usd <= 0:
            raise ValueError(f"size_usd must be > 0, got {self.size_usd}")
        if self.order_type == OrderType.LIMIT_POST_ONLY and self.limit_price is None:
            raise ValueError("limit_price required for LIMIT_POST_ONLY order")


@dataclass(frozen=True)
class OrderResult:
    """Post-execution order outcome."""
    status: OrderStatus
    order_id: str  # broker-assigned (paper: synthetic, live: exchange order id)
    filled_size_usd: float
    avg_fill_price: float
    fee_usd: float
    slippage_bps: float
    ts_ms: int
    raw_response: Optional[dict] = None  # broker-specific, audit trail
    error_msg: Optional[str] = None


class Broker(ABC):
    """Abstract broker — paper / live execution."""

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Submit an order. Returns filled / partial / rejected / pending."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending limit order. Returns True on success."""
        ...

    @abstractmethod
    def get_balance(self) -> dict[str, float]:
        """Return {currency: amount} balances (e.g. {'USDT': 5000.0, 'BTC': 0.05})."""
        ...

    @property
    @abstractmethod
    def is_live(self) -> bool:
        """True for live exchange brokers, False for paper."""
        ...
