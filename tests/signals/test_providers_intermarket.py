from __future__ import annotations

import pytest

from invasion.signals.providers_intermarket import (
    IntermarketStressProvider,
    clear_intermarket_cache,
)


def _series_from_returns(start: float, returns: list[float]) -> list[float]:
    out = [start]
    for ret in returns:
        out.append(out[-1] * (1.0 + ret))
    return out


def _context(group: str) -> dict:
    return {"group": group, "market_data": {"group": group}}


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_intermarket_cache()
    yield
    clear_intermarket_cache()


@pytest.fixture
def _preg(monkeypatch):
    vals = {
        "intermarket_stress_enabled": 1,
        "intermarket_stress_weight": 1.0,
    }
    monkeypatch.setattr(
        "invasion.signals.providers_intermarket.preg",
        lambda name: vals.get(name),
    )
    return vals


def test_happy_path_dxy_eurusd_short_forex(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    ref_data = {
        "dxy": _series_from_returns(100.0, [0.0] * 11 + [0.05]),
        "eurusd": _series_from_returns(1.10, [0.0] * 11 + [-0.05]),
        "vix": _series_from_returns(20.0, [0.0] * 12),
        "spx": _series_from_returns(5000.0, [0.0] * 12),
        "gold": _series_from_returns(2300.0, [0.0] * 12),
        "oil": _series_from_returns(75.0, [0.0] * 12),
    }
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: ref_data)

    result = provider.score("EUR/USD", _context("forex"))
    assert result is not None
    assert result.direction == "short"
    assert result.score < 0
    assert result.metadata["reason"].startswith("DXY+EUR- divergence")


def test_happy_path_vix_spx_short_us_equity(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    ref_data = {
        "dxy": _series_from_returns(100.0, [0.0] * 12),
        "eurusd": _series_from_returns(1.10, [0.0] * 12),
        "vix": _series_from_returns(20.0, [0.0] * 11 + [0.10]),
        "spx": _series_from_returns(5000.0, [0.0] * 11 + [0.03]),
        "gold": _series_from_returns(2300.0, [0.0] * 12),
        "oil": _series_from_returns(75.0, [0.0] * 11 + [0.03]),
    }
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: ref_data)

    result = provider.score("SPY", _context("stock"))
    assert result is not None
    assert result.direction == "short"
    assert result.score < 0
    assert result.metadata["reason"].startswith("VIX+SPX+ divergence")


def test_happy_path_gold_usd_short_commodity(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    ref_data = {
        "dxy": _series_from_returns(100.0, [0.0] * 11 + [0.04]),
        "eurusd": _series_from_returns(1.10, [0.0] * 11 + [0.04]),
        "vix": _series_from_returns(20.0, [0.0] * 12),
        "spx": _series_from_returns(5000.0, [0.0] * 12),
        "gold": _series_from_returns(2300.0, [0.0] * 11 + [0.03]),
        "oil": _series_from_returns(75.0, [0.0] * 12),
    }
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: ref_data)

    result = provider.score("Gold", _context("commodity"))
    assert result is not None
    assert result.direction == "short"
    assert result.score < 0
    assert result.metadata["reason"].startswith("GOLD+DXY+ divergence")


def test_happy_path_oil_spx_short_etf(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    ref_data = {
        "dxy": _series_from_returns(100.0, [0.0] * 12),
        "eurusd": _series_from_returns(1.10, [0.0] * 12),
        "vix": _series_from_returns(20.0, [0.0] * 11 + [0.04]),
        "spx": _series_from_returns(5000.0, [0.0] * 11 + [-0.04]),
        "gold": _series_from_returns(2300.0, [0.0] * 12),
        "oil": _series_from_returns(75.0, [0.0] * 11 + [0.05]),
    }
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: ref_data)

    result = provider.score("SPY", _context("etf"))
    assert result is not None
    assert result.direction == "short"
    assert result.score < 0
    assert result.metadata["reason"].startswith("OIL+SPX- divergence")


def test_cache_hit_second_call_skips_refetch(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    calls = {"tick": 0}
    now = {"ts": 1000.0}

    monkeypatch.setattr("invasion.signals.providers_intermarket.time.time", lambda: now["ts"])
    monkeypatch.setattr(provider, "_load_from_yahoo", lambda ref: None)

    def _tick_loader(ref_key, context):
        calls["tick"] += 1
        return _series_from_returns(100.0, [0.0] * 12)

    monkeypatch.setattr(provider, "_load_from_tick_history", _tick_loader)

    first = provider._fetch_reference_prices(_context("stock"))
    second = provider._fetch_reference_prices(_context("stock"))

    assert first.keys() == second.keys()
    assert calls["tick"] == len(first)


def test_fail_silent_missing_data_returns_none(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: {})
    assert provider.score("SPY", _context("stock")) is None


def test_zscore_edge_below_threshold_returns_none(monkeypatch, _preg):
    provider = IntermarketStressProvider()
    ref_data = {
        "dxy": _series_from_returns(100.0, [0.001] * 12),
        "eurusd": _series_from_returns(1.10, [0.0] * 12),
        "vix": _series_from_returns(20.0, [0.0] * 12),
        "spx": _series_from_returns(5000.0, [0.0] * 12),
        "gold": _series_from_returns(2300.0, [0.0] * 12),
        "oil": _series_from_returns(75.0, [0.0] * 12),
    }
    monkeypatch.setattr(provider, "_fetch_reference_prices", lambda ctx: ref_data)
    assert provider.score("EUR/USD", _context("forex")) is None
