"""xau_indices_trend — VIRTUAL-loosening pre-fed reuse gate (Jin 2026-07-07 fix).

DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (this proves a
LOOSENING fires, never a throttle).

Reviewer-proven gap: the live production loop
(``polaris.core.indicators.production``) populates ``market_view.momentum_20bar``
and ``market_view.donchian_high_30``/``donchian_low_30`` UNCONDITIONALLY at
FIXED windows (20-bar momentum, 30-bar Donchian), regardless of VIRTUAL/REAL
mode. If ``generate_raw_signal`` reused those pre-fed fields whenever finite
(the pre-fix shape), the module's own loosened ``MOMENTUM_LOOKBACK``
(20->10) and ``DONCHIAN_WINDOW`` (30->15) in VIRTUAL mode would never
actually run on the live loop — a silent no-op for BOTH levers.

Fix: gate each pre-fed reuse on the loosened window still matching the
pre-fed field's fixed window (``MOMENTUM_LOOKBACK == 20`` /
``DONCHIAN_WINDOW == 30``). In VIRTUAL mode the in-module recompute always
runs even when the pre-fed fields are finite (the live-production shape). In
REAL mode (env unset) the byte-identical pre-fed reuse still runs.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

from polaris.strategies.base import BarView, MarketView

_DAY = 3_600  # xau_indices_trend runs on 1H bars
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


def _series_breaks_15_not_30_with_10bar_momentum() -> list[float]:
    """50 bars: a flat 30-bar anchor, then a deep 15-bar dip, then a rally that
    clears the last-15-bar high (and shows positive 10-bar momentum) but
    stays under the bar-30-ago high and fails a 20-bar momentum read (the
    20-bar-ago close is still ABOVE the final close, so the fixed-window
    momentum_20bar would be negative)."""
    closes = [100.0] * 30  # bar-30-ago high anchor = 100.3
    closes += [80.0] * 15  # deep dip -> resets the 15-bar high + 10-bar-ago close
    closes += [85.0]  # final: >15-bar prior high (80.3), <30-bar (100.3);
    # 10-bar-ago close = 80.0 -> momentum_10 = (85-80)/80 > 0 (positive);
    # 20-bar-ago close = 80.0 (still inside the dip run) -> momentum_20 same
    # sign here, so we widen the dip below to force a genuine sign split.
    return closes


def _series_for_momentum_gate() -> list[float]:
    """Engineered so the FIXED 20-bar momentum is <=0 (rejects) while the
    loosened 10-bar momentum is >0 (fires), isolating the momentum lever. The
    dip run is long enough (40 bars) that it also fully vacates the 15-bar
    Donchian window used by VIRTUAL mode, so the final close cleanly breaks
    the (all-dip) 15-bar prior high regardless of the momentum window."""
    closes = [100.0] * 20  # bar-20-ago anchor close = 100.0
    closes += [70.0] * 40  # deep, long dip -> vacates both 10-bar and 15-bar windows
    closes += [75.0]  # final close: 10-bar momentum = (75-70)/70 > 0 (fires);
    # 20-bar-ago close (from this same dip run) = 70.0 too, so a plain
    # recompute would NOT go negative — the FIXED pre-fed field is passed
    # in directly as negative to isolate the gate (see call sites below).
    return closes


def _mv_with_live_prefed(
    bars: list[BarView],
    *,
    prefed_momentum_20bar: float,
    prefed_donchian_high_30: float,
    prefed_donchian_low_30: float,
) -> MarketView:
    """Mirrors the LIVE production shape: momentum_20bar / donchian_high_30 /
    donchian_low_30 are ALWAYS finite (production.py:638,641,642 feed them
    unconditionally at fixed 20/30-bar windows)."""
    return MarketView(
        symbol="XAUUSD",
        venue="capital",
        timeframe="1H",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=4.0,
        momentum_20bar=prefed_momentum_20bar,
        donchian_high_30=prefed_donchian_high_30,
        donchian_low_30=prefed_donchian_low_30,
    )


def _reload_with_env(value: str | None) -> ModuleType:
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.strategies.xau_indices_trend as mod

    return importlib.reload(mod)


def teardown_module() -> None:
    os.environ.pop(_ENV, None)
    import polaris.strategies.xau_indices_trend as mod

    importlib.reload(mod)


def test_virtual_mode_fires_donchian_with_prefed_30bar_finite() -> None:
    """Live-production shape: pre-fed donchian_high_30/low_30 are FINITE
    (fixed 30-bar), yet VIRTUAL mode must fire off the loosened 15-bar window —
    proving the in-module recompute runs, not the stale pre-fed reuse. Hold
    momentum fixed-positive (both windows agree) to isolate the Donchian gate."""
    closes = [100.0] * 30 + [80.0] * 15 + [85.0]
    bars = _bars(closes)
    prefed_high_30 = max(b.high for b in bars[-31:-1])
    prefed_low_30 = min(b.low for b in bars[-31:-1])

    virtual_mod = _reload_with_env("1")
    assert virtual_mod.DONCHIAN_WINDOW == 15
    strategy = virtual_mod.XAUIndicesTrendStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(
            bars,
            prefed_momentum_20bar=0.05,  # positive, held constant (not under test)
            prefed_donchian_high_30=prefed_high_30,
            prefed_donchian_low_30=prefed_low_30,
        )
    )
    assert sig is not None, (
        "VIRTUAL (15-bar loosened) must fire even when the pre-fed "
        "donchian_high_30 (fixed 30-bar) is finite — else the loosening is a "
        "silent no-op on the live loop"
    )
    assert sig.side == "long"


def test_real_mode_byte_identical_donchian_reuse_with_prefed_finite() -> None:
    """REAL mode (env unset): the SAME series + SAME finite pre-fed 30-bar
    fields must NOT fire (30-bar breakout fails) — proving REAL still reuses
    the pre-fed value byte-identically."""
    closes = [100.0] * 30 + [80.0] * 15 + [85.0]
    bars = _bars(closes)
    prefed_high_30 = max(b.high for b in bars[-31:-1])
    prefed_low_30 = min(b.low for b in bars[-31:-1])

    real_mod = _reload_with_env(None)
    assert real_mod.DONCHIAN_WINDOW == 30
    strategy = real_mod.XAUIndicesTrendStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(
            bars,
            prefed_momentum_20bar=0.05,
            prefed_donchian_high_30=prefed_high_30,
            prefed_donchian_low_30=prefed_low_30,
        )
    )
    assert sig is None, (
        "REAL (30-bar) must reject the same series using the pre-fed "
        "30-bar value — byte-identical to pre-loosening behavior"
    )


def test_virtual_mode_fires_momentum_with_prefed_20bar_finite() -> None:
    """Live-production shape: pre-fed momentum_20bar is FINITE (a fixed-window
    read, forced NEGATIVE here to model a stale/mismatched production feed),
    yet VIRTUAL mode must still fire off the loosened 10-bar in-module
    recompute (positive on this series) — proving the recompute runs, not
    the stale pre-fed reuse. Donchian high/low held permissive (finite, far
    outside price) so only the momentum gate is under test."""
    closes = _series_for_momentum_gate()
    bars = _bars(closes)
    prefed_momentum_20 = -0.05  # forced negative pre-fed value (see docstring)

    virtual_mod = _reload_with_env("1")
    assert virtual_mod.MOMENTUM_LOOKBACK == 10
    strategy = virtual_mod.XAUIndicesTrendStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(
            bars,
            prefed_momentum_20bar=prefed_momentum_20,
            prefed_donchian_high_30=bars[-1].close - 100.0,  # permissive: already broken
            prefed_donchian_low_30=bars[-1].close - 100.0,  # irrelevant to long branch
        )
    )
    assert sig is not None, (
        "VIRTUAL (10-bar loosened momentum) must fire even when the pre-fed "
        "momentum_20bar (fixed 20-bar, negative here) is finite — else the "
        "loosening is a silent no-op on the live loop"
    )
    assert sig.side == "long"


def test_real_mode_byte_identical_momentum_reuse_with_prefed_finite() -> None:
    """REAL mode (env unset): the SAME series + SAME finite pre-fed
    (negative) 20-bar momentum must NOT fire — proving REAL still reuses the
    pre-fed value byte-identically."""
    closes = _series_for_momentum_gate()
    bars = _bars(closes)
    prefed_momentum_20 = -0.05  # forced negative pre-fed value (see docstring above)

    real_mod = _reload_with_env(None)
    assert real_mod.MOMENTUM_LOOKBACK == 20
    strategy = real_mod.XAUIndicesTrendStrategy()
    sig = strategy.generate_raw_signal(
        _mv_with_live_prefed(
            bars,
            prefed_momentum_20bar=prefed_momentum_20,
            prefed_donchian_high_30=bars[-1].close - 100.0,
            prefed_donchian_low_30=bars[-1].close - 100.0,
        )
    )
    assert sig is None, (
        "REAL (20-bar) must reject the same series using the pre-fed "
        "(negative) momentum_20bar — byte-identical to pre-loosening behavior"
    )
