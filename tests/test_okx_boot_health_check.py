"""Boot-time OKX signed-credential health check (#3 — auth-regression detect).

DEMO/PAPER only — virtual funds. The single live blocker behind a 0-order
weekend was a SILENT OKX demo credential regression: every signed call (orders
AND /account/balance) returned 50105 / HTTP 401, but boot only exercised the
PUBLIC (unsigned) market-data path, so the bot looked "healthy" while every real
order rejected for 13h. This pins the boot signed health check: a single
``fetch_balance`` probe that, on an auth failure, emits a loud explicit
``OKX AUTH FAIL`` banner so the operator refreshes the credentials.

This is a pure DIAGNOSTIC — it NEVER blocks boot, NEVER throttles, NEVER cancels
an order (flow_not_block): a failed probe only LOGS. The order path, the maker→
taker fallback, and the -1.0R rail are all untouched.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from polaris.scripts.production_paper_loop import _okx_signed_health_check


def _ok_balance() -> dict[str, Any]:
    return {"data": [{"details": [{"ccy": "USDT", "availBal": "78000.0"}]}]}


def _auth_error() -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://us.okx.com/api/v5/account/balance")
    resp = httpx.Response(
        401, request=req,
        json={"code": "50105", "msg": "Request header OK-ACCESS-PASSPHRASE incorrect."},
    )
    return httpx.HTTPStatusError("401", request=req, response=resp)


@pytest.mark.asyncio
async def test_health_check_ok_when_balance_returns() -> None:
    """A valid signed balance call → healthy True, no AUTH-FAIL banner."""
    adapter = AsyncMock()
    adapter.fetch_balance = AsyncMock(return_value=_ok_balance())
    ok = await _okx_signed_health_check(adapter)
    assert ok is True
    adapter.fetch_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_fails_loudly_on_auth_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An auth failure (HTTP 401 / 50105) → healthy False + explicit ERROR banner
    naming the credential root cause (so the operator refreshes the demo key)."""
    adapter = AsyncMock()
    adapter.fetch_balance = AsyncMock(side_effect=_auth_error())
    with caplog.at_level(logging.ERROR):
        ok = await _okx_signed_health_check(adapter)
    assert ok is False
    banner = "\n".join(r.getMessage() for r in caplog.records)
    assert "OKX AUTH FAIL" in banner
    # The banner must name the credential remedy, not blame strategy/code.
    assert "50105" in banner or "credential" in banner.lower()


@pytest.mark.asyncio
async def test_health_check_never_raises_on_any_error() -> None:
    """flow_not_block: the probe is best-effort — a transport/unexpected error
    returns False (degraded) but NEVER propagates an exception into boot."""
    adapter = AsyncMock()
    adapter.fetch_balance = AsyncMock(side_effect=RuntimeError("boom"))
    ok = await _okx_signed_health_check(adapter)
    assert ok is False
