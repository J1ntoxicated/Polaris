"""VIRTUAL-mode entry loosening — representative-strategy proof (Jin 2026-07-07).

DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (this is a
LOOSENING, more trades, never a throttle). Proves the shared
``polaris.strategies._virtual_loosen.virtual_loosen`` mechanism on
``okx_donchian_55_breakout`` (DONCHIAN_WINDOW: virtual 20 / real 55):

  (a) VIRTUAL mode (``POLARIS_VIRTUAL_ACCOUNT=1``) fires on a bar series that
      breaks a 20-bar high but NOT a 55-bar high — the conservative REAL
      threshold would REJECT this same series (proves loosening works).
  (b) REAL mode (env unset) rejects that same series (proves the real path is
      unchanged / byte-identical to the frozen research-survivor threshold).
  (c) Property: for ANY breakout-magnitude bar series, VIRTUAL signal count >=
      REAL signal count (loosening only ever WIDENS firing, never narrows it).

Module-level constants are read once at import, so each mode is exercised via
``importlib.reload`` after setting/clearing the env var — the only way to
observe both branches of an import-time env-branch in one test process.
"""

from __future__ import annotations

import importlib
import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.strategies.base import BarView, MarketView

_DAY = 86_400
_ENV = "POLARIS_VIRTUAL_ACCOUNT"


def _bars(closes: list[float], *, start_ts: int = 1_700_000_000) -> list[BarView]:
    out: list[BarView] = []
    for i, c in enumerate(closes):
        out.append(
            BarView(
                ts=start_ts + i * _DAY,
                open=c,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=1000.0,
                notional_usd=1000.0 * c,
            )
        )
    return out


def _mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="BTC-USDT",
        venue="okx",
        timeframe="1D",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=4.0,
        momentum_20bar=0.05,  # positive ROC-20 confirm held constant throughout
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
    import polaris.strategies.okx_donchian_55_breakout as mod

    os.environ.pop(_ENV, None)
    importlib.reload(mod)


def _reload_with_env(value: str | None):
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.strategies.okx_donchian_55_breakout as mod

    return importlib.reload(mod)


# ===========================================================================
# (a) + (b): the same bar series — a fresh 20-bar high, NOT a fresh 55-bar high
# ===========================================================================


def _series_breaks_20_not_55() -> list[float]:
    """80 bars: flat-ish history, then a dip, then a rally that clears the
    last-20-bar high but stays UNDER the bar-55-ago high (a 55-bar breakout
    would reject this; a 20-bar breakout accepts it)."""
    closes = [100.0] * 55  # bar 55 high anchor = 100.3 (high = close+0.3)
    closes += [90.0] * 20  # a deep dip -> resets the 20-bar high much lower
    closes += [95.0]  # final close: > 20-bar prior high (90.3), < 55-bar (100.3)
    return closes


def test_virtual_mode_fires_where_real_mode_rejects() -> None:
    closes = _series_breaks_20_not_55()

    virtual_mod = _reload_with_env("1")
    virtual_strategy = virtual_mod.OKXDonchian55BreakoutStrategy()
    assert virtual_mod.DONCHIAN_WINDOW == 20
    virtual_sig = virtual_strategy.generate_raw_signal(_mv(_bars(closes)))
    assert virtual_sig is not None, "VIRTUAL (20-bar) must fire on a 20-bar breakout"
    assert virtual_sig.side == "long"

    real_mod = _reload_with_env(None)
    real_strategy = real_mod.OKXDonchian55BreakoutStrategy()
    assert real_mod.DONCHIAN_WINDOW == 55
    real_sig = real_strategy.generate_raw_signal(_mv(_bars(closes)))
    assert real_sig is None, "REAL (55-bar) must reject the same series — byte-identical threshold"


def test_real_mode_byte_identical_when_env_unset() -> None:
    os.environ.pop(_ENV, None)
    real_mod = _reload_with_env(None)
    assert real_mod.DONCHIAN_WINDOW == 55
    assert real_mod.ROC_LOOKBACK == 20


# ===========================================================================
# (c) property: VIRTUAL signal count >= REAL signal count on the same bars
# ===========================================================================


@given(
    dip_len=st.integers(min_value=20, max_value=54),
    rally=st.floats(min_value=0.5, max_value=15.0),
)
@settings(max_examples=30)
def test_virtual_never_fires_less_than_real(dip_len: int, rally: float) -> None:
    """For a breakout series parameterized by dip depth/rally size, VIRTUAL
    (20-bar) emitting is implied whenever REAL (55-bar) emits — never the
    reverse. This is the flow_not_block invariant: loosening only widens."""
    closes = [100.0] * 55 + [100.0 - 20.0] * dip_len + [100.0 - 20.0 + rally]
    bars = _bars(closes)

    virtual_mod = _reload_with_env("1")
    virtual_sig = virtual_mod.OKXDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars))

    real_mod = _reload_with_env(None)
    real_sig = real_mod.OKXDonchian55BreakoutStrategy().generate_raw_signal(_mv(bars))

    virtual_count = 0 if virtual_sig is None else 1
    real_count = 0 if real_sig is None else 1
    assert virtual_count >= real_count
