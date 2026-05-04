"""TDD: fmt_price() — ticker-aware price formatting, no sci notation.

Pure function tests: no I/O, deterministic.
P6: pure core.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# Import fmt_price from dashboard cli (pure function, no side effects)
from src.dashboard.cli import fmt_price


class TestFmtPriceBTC:
    """BTC/ETH price range: >= 1000 → comma + 2 dp."""

    def test_btc_79k(self):
        result = fmt_price(79134.50)
        assert result == "79,134.50"

    def test_btc_round_number(self):
        result = fmt_price(100000.0)
        assert result == "100,000.00"

    def test_eth_4k(self):
        result = fmt_price(4200.75)
        assert result == "4,200.75"

    def test_no_sci_notation(self):
        result = fmt_price(79000.0)
        assert "e" not in result.lower()
        assert "E" not in result


class TestFmtPriceORDI:
    """ORDI/SOL mid range: >= 10 and < 1000 → 3 dp."""

    def test_ordi_5(self):
        # 5.382 < 10 but >= 0.1 → 4 dp (DOGE range)
        result = fmt_price(5.382)
        assert result == "5.3820"

    def test_sol_150(self):
        # 150.123 >= 10 → 3 dp
        result = fmt_price(150.123)
        assert result == "150.123"

    def test_no_sci_notation(self):
        result = fmt_price(15.5)
        assert "e" not in result.lower()


class TestFmtPriceDOGE:
    """DOGE range: >= 0.1 and < 10 → 4 dp."""

    def test_doge_0108(self):
        result = fmt_price(0.1083)
        assert result == "0.1083"

    def test_doge_near_ten(self):
        result = fmt_price(9.9999)
        assert result == "9.9999"

    def test_no_sci_notation(self):
        result = fmt_price(0.25)
        assert "e" not in result.lower()


class TestFmtPriceSUI:
    """SUI range: >= 0.001 and < 0.1 → 5 dp."""

    def test_sui_small(self):
        result = fmt_price(0.00920)
        assert result == "0.00920"

    def test_sui_typical(self):
        # 0.92614 — wait, 0.92614 >= 0.1 so hits DOGE range
        # SUI is actually in 0.001-0.1 range at current prices ~$1-4
        # Let's test a price that's actually in the 0.001–0.1 range
        result = fmt_price(0.05123)
        assert result == "0.05123"

    def test_no_sci_notation(self):
        result = fmt_price(0.01)
        assert "e" not in result.lower()


class TestFmtPricePEPE:
    """PEPE range: < 0.001 → 8 dp."""

    def test_pepe_typical(self):
        result = fmt_price(0.00000392)
        assert result == "0.00000392"

    def test_pepe_leading_zeros(self):
        result = fmt_price(0.000001234)
        # 8 dp: 0.00000123
        assert result == "0.00000123"

    def test_no_sci_notation(self):
        result = fmt_price(0.000005)
        assert "e" not in result.lower()
        assert result == "0.00000500"


class TestFmtPriceBoundary:
    """Boundary exact values."""

    def test_exactly_1000(self):
        result = fmt_price(1000.0)
        assert result == "1,000.00"

    def test_just_below_1000(self):
        result = fmt_price(999.99)
        assert result == "999.990"

    def test_exactly_10(self):
        result = fmt_price(10.0)
        assert result == "10.000"

    def test_just_below_10(self):
        result = fmt_price(9.9999)
        assert result == "9.9999"

    def test_exactly_01(self):
        result = fmt_price(0.1)
        assert result == "0.1000"

    def test_exactly_0001(self):
        result = fmt_price(0.001)
        assert result == "0.00100"


class TestFmtPriceNeverSciNotation:
    """Property-based: fmt_price never produces 'e' or 'E' for valid positive prices."""

    @given(st.floats(min_value=1e-10, max_value=1e7, allow_nan=False, allow_infinity=False))
    @settings(max_examples=500)
    def test_no_sci_notation_property(self, price: float):
        result = fmt_price(price)
        assert "e" not in result.lower(), f"sci notation in {result!r} for price={price}"
        assert "E" not in result, f"sci notation in {result!r} for price={price}"

    @given(st.floats(min_value=1e-10, max_value=1e7, allow_nan=False, allow_infinity=False))
    @settings(max_examples=500)
    def test_always_returns_string(self, price: float):
        result = fmt_price(price)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(st.floats(min_value=1e-10, max_value=1e7, allow_nan=False, allow_infinity=False))
    @settings(max_examples=500)
    def test_contains_decimal_point(self, price: float):
        result = fmt_price(price)
        # All formatted prices should have a decimal point
        # (commas for large numbers but also decimal point)
        assert "." in result, f"No decimal point in {result!r} for price={price}"
