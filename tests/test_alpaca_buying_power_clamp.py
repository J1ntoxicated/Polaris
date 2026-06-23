"""Alpaca entry buying-power clamp — insufficient_buying_power reject fix.

Live incident (2026-06-22 RTH): the Alpaca open path (unlike OKX, which clamps
to live available USDT) sized ``notional_usd`` with NO reference to account
buying power, so oversized orders were rejected ``insufficient_buying_power`` on
repeat (AMD churn in the runtime log). The clamp caps the entry at live buying
power (minus a thin buffer) so SOME order fills — flow_not_block, a REAL account
constraint, not a defensive size cut.

DEMO/PAPER only.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.scripts._alpaca_open import (
    _clamp_to_buying_power,
    fetch_alpaca_buying_power,
    real_alpaca_open_fill,
)


def test_clamp_none_buying_power_is_passthrough() -> None:
    assert _clamp_to_buying_power(5000.0, None) == 5000.0


def test_clamp_caps_to_buying_power_minus_buffer() -> None:
    # buying_power 1000 → fundable 980 (2% buffer) < requested 5000 → capped.
    clamped = _clamp_to_buying_power(5000.0, 1000.0)
    assert clamped is not None
    assert clamped == pytest.approx(980.0)


def test_clamp_below_dust_floor_returns_none() -> None:
    assert _clamp_to_buying_power(5000.0, 0.5) is None


def test_clamp_does_not_inflate_a_small_order() -> None:
    # requested below fundable → unchanged (never sizes UP).
    assert _clamp_to_buying_power(100.0, 1000.0) == 100.0


class _AcctAdapter:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    async def fetch_account(self) -> dict[str, Any]:
        return self._body


@pytest.mark.asyncio
async def test_fetch_buying_power_parses_field() -> None:
    bp = await fetch_alpaca_buying_power(_AcctAdapter({"buying_power": "1234.56"}))
    assert bp == pytest.approx(1234.56)


@pytest.mark.asyncio
async def test_fetch_buying_power_missing_field_is_none() -> None:
    assert await fetch_alpaca_buying_power(_AcctAdapter({})) is None


@pytest.mark.asyncio
async def test_fetch_buying_power_failure_is_none() -> None:
    class _Boom:
        async def fetch_account(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    assert await fetch_alpaca_buying_power(_Boom()) is None


class _OrderAdapter:
    """Captures the notional the open path actually submits."""

    def __init__(self) -> None:
        self.submitted_notional: float | None = None

    async def place_market_order(
        self, *, symbol: str, side: str, notional_usd: float = 0.0,
        qty: float | None = None, client_order_id: str,
        time_in_force: str = "day",
    ) -> Any:
        self.submitted_notional = notional_usd

        class _R:
            ok = False
            venue_order_id = None
            code = "rejected"
            msg = ""
        return _R()


@pytest.mark.asyncio
async def test_open_submits_clamped_notional() -> None:
    adapter = _OrderAdapter()
    await real_alpaca_open_fill(
        adapter, symbol="AMD", notional_usd=5000.0, strategy_id="s",
        buying_power=1000.0,
    )
    # submitted = min(5000, 1000*0.98) = 980.
    assert adapter.submitted_notional == pytest.approx(980.0)


@pytest.mark.asyncio
async def test_open_skips_when_unfundable() -> None:
    adapter = _OrderAdapter()
    attempt = await real_alpaca_open_fill(
        adapter, symbol="AMD", notional_usd=5000.0, strategy_id="s",
        buying_power=0.5,
    )
    assert attempt.fill is None
    assert attempt.reject_code == "insufficient_buying_power"
    assert adapter.submitted_notional is None, "no unfundable order submitted"
