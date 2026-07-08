"""cci_reversion Capital universe widen — decoupled from xau_indices_trend
(Jin 2026-07-09).

``cci_reversion`` no longer imports ``SUPPORTED_SYMBOLS`` from
``xau_indices_trend`` — it now owns its own set. REAL stays byte-identical
(gold + index majors only). VIRTUAL additionally unions in the FX majors,
reused SSOT from ``fx_breakout_basket.BASKET_SYMBOLS`` (all venue=capital). No
crypto is added (Layer 0 / G1's domain). DEMO/PAPER only — flow_not_block:
this is a widen-only loosening, REAL is never touched.

Module-level ``SUPPORTED_SYMBOLS`` is read once at import (the shared
``_virtual_loosen`` pattern), so each mode is exercised via
``importlib.reload`` after setting/clearing the env var — mirrors
``test_virtual_loosen_okx_donchian55.py``.
"""

from __future__ import annotations

import importlib
import os

import pytest

from polaris.strategies.base import BarView, MarketView

_ENV = "POLARIS_VIRTUAL_ACCOUNT"

# The exact gold+index set formerly imported from xau_indices_trend.SUPPORTED_
# SYMBOLS — pinned here as the byte-identical REAL-mode expectation.
_REAL_EXPECTED: frozenset[str] = frozenset(
    {"XAUUSD", "GOLD", "US500", "US100", "DE40", "UK100", "EU50", "US30"}
)
_FX_EXPECTED: frozenset[str] = frozenset(
    {"EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD"}
)


@pytest.fixture(autouse=True)
def _restore_env_and_module():
    """Isolate the env var + force a fresh import per test (no cross-test leak)."""
    prior = os.environ.get(_ENV)
    yield
    if prior is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = prior
    import polaris.strategies.cci_reversion as mod

    os.environ.pop(_ENV, None)
    importlib.reload(mod)


def _reload_with_env(value: str | None):
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.strategies.cci_reversion as mod

    return importlib.reload(mod)


# ===========================================================================
# REAL / VIRTUAL SUPPORTED_SYMBOLS split
# ===========================================================================


def test_real_supported_symbols_byte_identical_gold_index_only() -> None:
    """REAL (env unset) SUPPORTED_SYMBOLS == exactly the gold+index majors
    formerly imported from xau_indices_trend — no FX, no drift."""
    mod = _reload_with_env(None)
    assert mod.SUPPORTED_SYMBOLS == _REAL_EXPECTED


def test_virtual_supported_symbols_adds_fx_majors() -> None:
    """VIRTUAL (env=1) SUPPORTED_SYMBOLS == REAL union the 5 FX majors, reused
    SSOT from fx_breakout_basket.BASKET_SYMBOLS."""
    from polaris.strategies.fx_breakout_basket import BASKET_SYMBOLS

    assert BASKET_SYMBOLS == _FX_EXPECTED
    mod = _reload_with_env("1")
    assert mod.SUPPORTED_SYMBOLS == _REAL_EXPECTED | _FX_EXPECTED
    assert mod.SUPPORTED_SYMBOLS >= BASKET_SYMBOLS


def test_virtual_never_narrower_than_real() -> None:
    """flow_not_block invariant: VIRTUAL is always a superset of REAL."""
    real_mod = _reload_with_env(None)
    real_syms = real_mod.SUPPORTED_SYMBOLS
    virtual_mod = _reload_with_env("1")
    virtual_syms = virtual_mod.SUPPORTED_SYMBOLS
    assert virtual_syms >= real_syms


def test_no_crypto_symbols_added() -> None:
    """Layer 0 / G1's domain — this strategy adds no crypto symbols in either
    mode."""
    virtual_mod = _reload_with_env("1")
    for crypto in ("BTC-USDT", "ETH-USDT", "BTCUSDT", "ETHUSDT"):
        assert crypto not in virtual_mod.SUPPORTED_SYMBOLS


# ===========================================================================
# Suffix-strip normalization unit tests
# ===========================================================================


def test_normalize_symbol_strips_capital_fx_suffix() -> None:
    mod = _reload_with_env(None)
    assert mod._normalize_symbol("AUDUSD_ZERO") == "AUDUSD"
    assert mod._normalize_symbol("EURUSD_W") == "EURUSD"


def test_normalize_symbol_strips_slash_and_dot() -> None:
    mod = _reload_with_env(None)
    assert mod._normalize_symbol("XAU/USD") == "XAUUSD"
    assert mod._normalize_symbol("us100.cash") == "US100CASH"


def test_normalize_symbol_no_suffix_passthrough() -> None:
    mod = _reload_with_env(None)
    assert mod._normalize_symbol("GOLD") == "GOLD"
    assert mod._normalize_symbol("US500") == "US500"


# ===========================================================================
# Signal-generation smoke test — proves the widened FX universe actually fires
# ===========================================================================


def _bar(ts: int, price: float, *, spread: float) -> BarView:
    return BarView(
        ts=ts, open=price, high=price + spread, low=price - spread,
        close=price, volume=1000.0,
    )


def _mv(symbol: str, bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol=symbol, venue="capital", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=1.0, atr_pct=0.01,
    )


def _oversold_then_revert_bars(base_price: float, spread: float) -> list[BarView]:
    """A deep dip (CCI << both the REAL -70 and VIRTUAL -40 oversold floors)
    then a strong recovery bar that clears CCI back ABOVE BOTH thresholds
    (cci_now ~= -4.4, comfortably > -40), uniformly scaled to ``base_price`` —
    CCI is scale-invariant under a uniform price*spread scaling (mean/
    mean-deviation both scale linearly), so this reproduces an identical CCI
    sequence at any price level, e.g. an FX pair around 1.20 instead of
    gold's 2000. (A shallower revert, e.g. to 1990, clears -70 but NOT -40 —
    the looser VIRTUAL threshold requires a bigger recovery bounce to clear,
    since -40 sits above -70; this fixture's 1995 revert clears both.)
    """
    scale = base_price / 2000.0
    base = 1_700_000_000
    bars: list[BarView] = []
    for i in range(25):
        bars.append(_bar(base + i * 3600, base_price, spread=spread))
    for j, px in enumerate((1960.0, 1955.0, 1995.0)):
        bars.append(_bar(base + (25 + j) * 3600, px * scale, spread=spread))
    return bars


def test_virtual_fires_on_fx_major_real_rejects_same_series() -> None:
    """AUDUSD_ZERO (Capital's real FX epic spelling) — VIRTUAL accepts it
    (suffix-stripped to AUDUSD, in the widened universe) and fires on the
    oversold cross-up; REAL rejects it outright (gold/index-only universe,
    byte-identical to before this change)."""
    bars = _oversold_then_revert_bars(1.2000, spread=0.0006)

    virtual_mod = _reload_with_env("1")
    virtual_sig = virtual_mod.CCIReversionStrategy().generate_raw_signal(
        _mv("AUDUSD_ZERO", bars)
    )
    assert virtual_sig is not None
    assert virtual_sig.side == "long"
    assert virtual_sig.strategy_id == "cci_reversion"

    real_mod = _reload_with_env(None)
    real_sig = real_mod.CCIReversionStrategy().generate_raw_signal(
        _mv("AUDUSD_ZERO", bars)
    )
    assert real_sig is None


def test_virtual_still_fires_on_gold_unaffected_by_widen() -> None:
    """The widen is additive — gold/index still fires under VIRTUAL exactly
    as it did before (no regression from adding FX)."""
    bars = _oversold_then_revert_bars(2000.0, spread=1.0)
    virtual_mod = _reload_with_env("1")
    sig = virtual_mod.CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars))
    assert sig is not None
    assert sig.side == "long"
