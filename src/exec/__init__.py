"""Polaris execution layer — broker abstraction.

Phase 14 (2026-05-05): unified Broker interface so paper engine and live
OKX live API share the same API. Enables risk-controlled paper-to-live
transition.

Modules:
    broker: abstract Broker base + OrderRequest/OrderResult dataclasses (P6 pure).
    paper_broker: simulates fills via slippage_model (Phase 6+) — wraps existing engine.
    okx_broker: OKX SPOT live API skeleton (POST /api/v5/trade/order).
    kill_switch: global flag + file-based emergency halt.

Usage:
    from src.exec.paper_broker import PaperBroker
    from src.exec.okx_broker import OKXBroker

    broker: Broker = PaperBroker(...) if PAPER_MODE else OKXBroker(...)
    result = broker.place_order(OrderRequest(side='buy', ticker='BTC-USDT', size_usd=100))
"""
from src.exec.broker import (
    Broker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
)
from src.exec.kill_switch import is_kill_switch_active, set_kill_switch

__all__ = [
    "Broker",
    "OrderRequest",
    "OrderResult",
    "OrderSide",
    "OrderType",
    "is_kill_switch_active",
    "set_kill_switch",
]
