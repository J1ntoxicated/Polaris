"""Real-fee cost model — pure functions (Component A, deliverable 1).

The OKX demo charges a flat 0.7% (70 bps) taker=maker, confirmed via
``/api/v5/account/trade-fee`` (Lv1). Real OKX SPOT Lv1 is 0.10% taker /
0.08% maker. These tests pin the schedules + the 7x demo-vs-real penalty.
"""

from __future__ import annotations

import math

import pytest

from polaris.core.economics.fees import (
    ALPACA_REAL_BPS,
    CAPITAL_REAL_BPS,
    DEMO_VS_REAL_OKX_PENALTY,
    OKX_DEMO_FEE_BPS,
    OKX_REAL_MAKER_BPS,
    OKX_REAL_TAKER_BPS,
    demo_fee_usd,
    real_fee_usd,
)


def test_constants_match_authoritative_schedule() -> None:
    assert OKX_REAL_TAKER_BPS == 10.0
    assert OKX_REAL_MAKER_BPS == 8.0
    assert OKX_DEMO_FEE_BPS == 70.0
    # COST_BPS_CAPITAL (=3.0) is the per-leg real proxy for Capital CFD.
    assert CAPITAL_REAL_BPS == 3.0
    assert ALPACA_REAL_BPS == 0.0


def test_okx_real_taker_fee() -> None:
    # 0.10% of $1000 = $1.00
    assert real_fee_usd("okx", 1000.0) == pytest.approx(1.0)


def test_okx_real_maker_fee() -> None:
    # 0.08% of $1000 = $0.80
    assert real_fee_usd("okx", 1000.0, is_maker=True) == pytest.approx(0.80)


def test_okx_demo_fee_is_7x_real_taker() -> None:
    # 0.70% of $1000 = $7.00 → 7x the $1.00 real taker leg.
    assert demo_fee_usd("okx", 1000.0) == pytest.approx(7.0)
    assert demo_fee_usd("okx", 1000.0) / real_fee_usd("okx", 1000.0) == pytest.approx(
        DEMO_VS_REAL_OKX_PENALTY
    )
    assert pytest.approx(7.0) == DEMO_VS_REAL_OKX_PENALTY


def test_capital_real_proxy() -> None:
    # 3 bps of $1000 = $0.30 per leg; maker flag is a no-op for Capital.
    assert real_fee_usd("capital", 1000.0) == pytest.approx(0.30)
    assert real_fee_usd("capital", 1000.0, is_maker=True) == pytest.approx(0.30)


def test_alpaca_commission_free() -> None:
    assert real_fee_usd("alpaca", 1000.0) == 0.0


def test_capital_demo_fee_uses_real_proxy() -> None:
    # Capital demo reports fee=0; the proxy stands in for both real + demo.
    assert demo_fee_usd("capital", 1000.0) == pytest.approx(0.30)


def test_alpaca_demo_fee_zero() -> None:
    assert demo_fee_usd("alpaca", 1000.0) == 0.0


def test_unknown_venue_falls_back_to_okx_real() -> None:
    # An unfamiliar venue is never silently free — it falls back to the OKX real
    # taker schedule (documented conservative default).
    assert real_fee_usd("kraken", 1000.0) == pytest.approx(1.0)
    assert demo_fee_usd("kraken", 1000.0) == pytest.approx(7.0)


def test_venue_case_insensitive() -> None:
    assert real_fee_usd("OKX", 1000.0) == pytest.approx(1.0)
    assert demo_fee_usd("Capital", 1000.0) == pytest.approx(0.30)


def test_negative_notional_uses_absolute() -> None:
    # A short/sell notional may arrive signed; fee is on |notional|.
    assert real_fee_usd("okx", -1000.0) == pytest.approx(1.0)
    assert demo_fee_usd("okx", -1000.0) == pytest.approx(7.0)


def test_zero_notional_zero_fee() -> None:
    assert real_fee_usd("okx", 0.0) == 0.0
    assert demo_fee_usd("okx", 0.0) == 0.0


def test_non_finite_notional_zero_fee() -> None:
    assert real_fee_usd("okx", math.nan) == 0.0
    assert real_fee_usd("okx", math.inf) == 0.0
    assert demo_fee_usd("okx", math.nan) == 0.0
