"""OKXBroker — OKX SPOT live API skeleton (Phase 14.1).

WARNING: skeleton + dry-run only. Phase 14.2 (Jin authorization) wires
actual API keys + sends real orders. Until then, all calls return
OrderStatus.REJECTED with reason "live_disabled_dry_run".

OKX SPOT REST endpoints (from official docs):
- POST /api/v5/trade/order — place order
- POST /api/v5/trade/cancel-order — cancel
- GET  /api/v5/account/balance — balances
- GET  /api/v5/trade/order — query order

Auth headers required:
- OK-ACCESS-KEY
- OK-ACCESS-SIGN (HMAC-SHA256 of timestamp + method + path + body)
- OK-ACCESS-TIMESTAMP
- OK-ACCESS-PASSPHRASE

Live activation:
    export OKX_API_KEY=...
    export OKX_API_SECRET=...
    export OKX_API_PASSPHRASE=...
    export POLARIS_LIVE_MODE=1   # arm

This module never sends real orders unless POLARIS_LIVE_MODE=1 AND all
3 secrets are set. Dry-run by default — safe to import and instantiate.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from src.exec.broker import (
    Broker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.exec.kill_switch import is_kill_switch_active

logger = logging.getLogger(__name__)


def _live_armed() -> bool:
    """All conditions for real order submission satisfied?"""
    return (
        os.environ.get("POLARIS_LIVE_MODE") == "1"
        and bool(os.environ.get("OKX_API_KEY"))
        and bool(os.environ.get("OKX_API_SECRET"))
        and bool(os.environ.get("OKX_API_PASSPHRASE"))
    )


class OKXBroker(Broker):
    """OKX SPOT live broker — disabled by default, dry-run skeleton.

    Phase 14.2 will wire the actual REST + auth.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        max_size_usd: float = 500.0,  # Phase 14.2: cap small for first runs
    ) -> None:
        self._api_key = api_key or os.environ.get("OKX_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("OKX_API_SECRET", "")
        self._passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
        self._max_size_usd = max_size_usd

    @property
    def is_live(self) -> bool:
        return True

    def place_order(self, request: OrderRequest) -> OrderResult:
        ts_ms = int(time.time() * 1000)

        # Safety: kill switch always honored
        if is_kill_switch_active():
            return OrderResult(
                status=OrderStatus.REJECTED, order_id="KILL-SWITCH-BLOCK",
                filled_size_usd=0.0, avg_fill_price=0.0,
                fee_usd=0.0, slippage_bps=0.0, ts_ms=ts_ms,
                error_msg="kill_switch_active",
            )

        # Safety: hard cap on order size (Phase 14.2 sanity)
        if request.size_usd > self._max_size_usd:
            return OrderResult(
                status=OrderStatus.REJECTED, order_id="SIZE-CAP-BLOCK",
                filled_size_usd=0.0, avg_fill_price=0.0,
                fee_usd=0.0, slippage_bps=0.0, ts_ms=ts_ms,
                error_msg=f"size_usd {request.size_usd:.2f} > max {self._max_size_usd:.2f}",
            )

        # Phase 14.1: live not armed → dry run
        if not _live_armed():
            return OrderResult(
                status=OrderStatus.REJECTED, order_id="DRY-RUN",
                filled_size_usd=0.0, avg_fill_price=0.0,
                fee_usd=0.0, slippage_bps=0.0, ts_ms=ts_ms,
                error_msg="live_disabled_dry_run (Phase 14.1)",
            )

        # Phase 14.2 — actual REST submission goes here
        return OrderResult(
            status=OrderStatus.REJECTED, order_id="NOT-IMPLEMENTED",
            filled_size_usd=0.0, avg_fill_price=0.0,
            fee_usd=0.0, slippage_bps=0.0, ts_ms=ts_ms,
            error_msg="OKX REST not yet implemented (Phase 14.2 pending)",
        )

    def cancel_order(self, order_id: str) -> bool:
        return False  # Phase 14.2

    def get_balance(self) -> dict[str, float]:
        # Phase 14.2: GET /api/v5/account/balance
        return {}
