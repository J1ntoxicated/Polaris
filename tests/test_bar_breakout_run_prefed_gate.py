"""bar_breakout_run — VIRTUAL-loosening pre-fed reuse gate (Jin 2026-07-07 fix).

DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (this proves a
LOOSENING fires, never a throttle).

Reviewer-proven gap: the live production loop
(``polaris.core.indicators.production``) populates ``market_view.donchian_high_40``
UNCONDITIONALLY at a FIXED 40-bar window, regardless of VIRTUAL/REAL mode. If
``generate_raw_signal`` reused that pre-fed field whenever finite (the
pre-fix shape), the module's own loosened ``DONCHIAN_WINDOW`` (40->10 in
VIRTUAL) would never actually run on the live loop — a silent no-op.

Fix: gate the pre-fed reuse on ``DONCHIAN_WINDOW == 40`` (the pre-fed field's
fixed window). In VIRTUAL mode (window=10) the in-module recompute always
runs, even when the pre-fed 40-bar field is finite (the live-production
shape). In REAL mode (window=40, env unset) the byte-identical pre-fed reuse
still runs.

These tests reproduce the exact live shape: pre-fed ``donchian_high_40`` is
ALWAYS finite (as production.py feeds it), and assert:
  (a) VIRTUAL mode fires on a series that breaks the last-10-bar high but NOT
      the last-40-bar high — proving the loosened in-module compute is used.
  (b) REAL mode (env unset) rejects that same series — proving the pre-fed
      40-bar reuse path is unchanged / byte-identical.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

from polaris.strategies.base import BarView, MarketView

_DAY = 86_400
_ENV = "POLARIS_VIRTUAL_ACCOUNT"


def _bars(closes: list[float]) -> list[BarView]:
    out: list[BarView] = []
    for i, c in enumerate(closes):
        out.append(
            BarView(
                ts=1_700_000_000 + i * _DAY,
                open=c,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=1000.0,
                notional_usd=1000.0 * c,
            )
        )
    return out


def _series_breaks_10_not_40() -> list[float]:
    """65 bars: a flat 40-bar anchor, then a deep 20-bar dip, then a rally that
    clears the last-10-bar high (entirely inside the dip) but stays under the
    bar-40-ago high."""
    closes = [100.0] * 40  # bar-40-ago high anchor = 100.3 (high = close+0.3)
    closes += [85.0] * 20  # deep dip -> resets the 10-bar high much lower
    closes += [90.0]  # final close: > 10-bar prior high (85.3), < 40-bar (100.3)
    return closes


def _mv_with_live_prefed(bars: list[BarView], *, prefed_donchian_high_40: float) -> MarketView:
    """Mirrors the LIVE production shape: donchian_high_40 is ALWAYS finite
    (production.py:639 feeds it unconditionally at a fixed 40-bar window)."""
    return MarketView(
        symbol="BTC-USDT",
        venue="okx",
        timeframe="1D",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=4.0,
        donchian_high_40=prefed_donchian_high_40,
    )


def _reload_with_env(value: str | None) -> ModuleType:
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.strategies.bar_breakout_run as mod

    return importlib.reload(mod)


def teardown_module() -> None:
    os.environ.pop(_ENV, None)
    import polaris.strategies.bar_breakout_run as mod

    importlib.reload(mod)


def test_virtual_mode_fires_with_prefed_40bar_finite() -> None:
    """The live-production shape: pre-fed donchian_high_40 is FINITE (fixed
    40-bar), yet VIRTUAL mode must still fire off the loosened 10-bar window —
    proving the in-module recompute runs, not the stale pre-fed reuse."""
    closes = _series_breaks_10_not_40()
    bars = _bars(closes)
    # Fixed 40-bar pre-fed high, computed exactly as production.py would.
    prefed_high_40 = max(b.high for b in bars[-41:-1])

    virtual_mod = _reload_with_env("1")
    assert virtual_mod.DONCHIAN_WINDOW == 10
    strategy = virtual_mod.BarBreakoutRunStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(bars, prefed_donchian_high_40=prefed_high_40)
    )
    assert sig is not None, (
        "VIRTUAL (10-bar loosened) must fire even when the pre-fed "
        "donchian_high_40 (fixed 40-bar) is finite — else the loosening is a "
        "silent no-op on the live loop"
    )
    assert sig.side == "long"


def test_real_mode_byte_identical_reuse_with_prefed_finite() -> None:
    """REAL mode (env unset): the SAME series + the SAME finite pre-fed
    40-bar field must NOT fire (40-bar breakout fails) — proving REAL still
    reuses the pre-fed value byte-identically."""
    closes = _series_breaks_10_not_40()
    bars = _bars(closes)
    prefed_high_40 = max(b.high for b in bars[-41:-1])

    real_mod = _reload_with_env(None)
    assert real_mod.DONCHIAN_WINDOW == 40
    strategy = real_mod.BarBreakoutRunStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(bars, prefed_donchian_high_40=prefed_high_40)
    )
    assert sig is None, (
        "REAL (40-bar) must reject the same series using the pre-fed "
        "40-bar value — byte-identical to pre-loosening behavior"
    )
