"""Fill normalizer — venue payload → unified Fill dataclass."""

from __future__ import annotations

import pytest

from polaris.core.data.fill_normalizer import (
    Fill,
    FillNormalizationError,
    normalize_capital_confirm,
    normalize_okx_fill,
)


def test_okx_fill_to_unified_quote_ccy() -> None:
    payload = {
        "ordId": "999",
        "clOrdId": "polarisvb",
        "instId": "BTC-USDT",
        "tdMode": "cash",
        "side": "buy",
        "ordType": "ioc",
        "tgtCcy": "quote_ccy",
        "sz": "10",
        "accFillSz": "10",  # in quote ccy when tgtCcy=quote_ccy
        "avgPx": "60000",
        "fee": "-0.0035",
        "feeCcy": "USDT",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="volume_burst", expected_price=60_000.0)
    assert isinstance(f, Fill)
    assert f.venue == "okx"
    assert f.instrument_id == "okx:BTC-USDT"
    assert f.side == "buy"
    assert f.size_usd == 10.0  # quote ccy
    assert f.fill_price == 60_000.0
    assert f.fee_usd == pytest.approx(0.0035)
    assert f.slippage_bps == pytest.approx(0.0)
    assert f.ts_ms == 1_762_476_225_678
    assert f.order_id == "999"


def test_okx_fill_base_ccy_units_recovered() -> None:
    """When tgtCcy=base_ccy, accFillSz is base; quote_qty = base × avgPx."""
    payload = {
        "ordId": "1",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "base_ccy",
        "accFillSz": "0.001",
        "avgPx": "60000",
        "fee": "-0.000001",
        "feeCcy": "BTC",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="tsmom")
    assert f.base_qty == pytest.approx(0.001)
    assert f.quote_qty == pytest.approx(60.0)
    assert f.size_usd == pytest.approx(60.0)


def test_okx_fill_rejects_non_filled() -> None:
    payload = {"state": "live", "instId": "BTC-USDT", "side": "buy"}
    with pytest.raises(FillNormalizationError):
        normalize_okx_fill(payload, strategy_id="volume_burst")


def test_capital_fill_to_unified() -> None:
    payload = {
        "dealReference": "REF1",
        "dealId": "DEAL1",
        "epic": "EURUSD",
        "direction": "BUY",
        "level": 1.085,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T01:23:45.000",
    }
    f = normalize_capital_confirm(
        payload, strategy_id="fx_breakout_basket", pip_value_usd=10.0,
        expected_price=1.085,
    )
    assert f.venue == "capital"
    assert f.instrument_id == "capital:EURUSD"
    assert f.side == "buy"
    assert f.size_usd == 10.0  # 1 lot * pip 10
    assert f.fill_price == 1.085
    assert f.slippage_bps == pytest.approx(0.0)
    assert f.order_id == "DEAL1"


def test_capital_fill_slippage_bps() -> None:
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "GOLD",
        "direction": "SELL",
        "level": 2050.0,
        "size": 0.5,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f = normalize_capital_confirm(
        payload,
        strategy_id="xau_indices_trend",
        pip_value_usd=1.0,
        expected_price=2049.0,
    )
    # |2050 - 2049| / 2049 * 10000 ≈ 4.88
    assert f.slippage_bps == pytest.approx(4.88, abs=0.05)
    assert f.side == "sell"


def test_capital_rejects_non_open() -> None:
    payload = {
        "dealReference": "R", "epic": "EURUSD", "direction": "BUY",
        "level": 1.0, "size": 1.0, "status": "REJECTED",
    }
    with pytest.raises(FillNormalizationError):
        normalize_capital_confirm(payload, strategy_id="fx", pip_value_usd=10.0)


def test_capital_fill_includes_leverage_in_notional() -> None:
    """Codex Day 5 P1 — size_usd must include leverage for CFD gross notional."""
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "EURUSD",
        "direction": "BUY",
        "level": 1.10,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f30 = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, leverage=30.0
    )
    f1 = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, leverage=1.0
    )
    assert f30.size_usd == pytest.approx(300.0)  # 1 * 10 * 30
    assert f1.size_usd == pytest.approx(10.0)
    assert f30.size_usd == 30.0 * f1.size_usd


def test_fee_slippage_calc_okx_non_usdt_fee() -> None:
    """When fee is paid in BTC, we convert to USD via avgPx."""
    payload = {
        "ordId": "1",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "100",
        "avgPx": "60000",
        "fee": "-0.000001",  # BTC
        "feeCcy": "BTC",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="volume_burst")
    # 0.000001 BTC × 60000 USD/BTC = 0.06 USD.
    assert f.fee_usd == pytest.approx(0.06)
