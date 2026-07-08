"""VIRTUAL-mode entry loosening — rsi_bb_pullback silent-INERT revival, part A
(Jin 2026-07-09, vault/50_research/active-trading-max-plan_2026-07-09.md Group 1).

DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (this is a
LOOSENING, more trades, never a throttle). Proves two independent knobs on
``rsi_bb_pullback`` via the shared ``polaris.strategies._virtual_loosen.
virtual_loosen`` mechanism:

  - ``RSI_THRESHOLD``: virtual 55.0 / REAL 39.0 (byte-identical, unchanged).
  - ``BB_TOUCH_MULT``: virtual 1.004 (0.4% near-touch) / REAL 1.0 (exact pierce,
    byte-identical, a no-op multiplier).

Module-level constants are read once at import, so each mode is exercised via
``importlib.reload`` after setting/clearing the env var (mirrors
``test_virtual_loosen_okx_donchian55.py``).
"""

from __future__ import annotations

import importlib
import os

import pytest

from polaris.strategies.base import BarView, MarketView

_ENV = "POLARIS_VIRTUAL_ACCOUNT"
_BAR_STEP_SEC = 15 * 60


def _bars(n: int, *, base_close: float = 100.0) -> list[BarView]:
    out: list[BarView] = []
    for i in range(n):
        out.append(
            BarView(
                ts=1_700_000_000 + i * _BAR_STEP_SEC,
                open=base_close,
                high=base_close + 0.5,
                low=base_close - 0.5,
                close=base_close,
                volume=1000.0,
                notional_usd=1000.0 * base_close,
            )
        )
    return out


@pytest.fixture(autouse=True)
def _restore_env_and_module():
    """Isolate the env var + force a fresh import per test (no cross-test leak)."""
    prior = os.environ.get(_ENV)
    yield
    if prior is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = prior
    import polaris.strategies.rsi_bb_pullback as mod

    os.environ.pop(_ENV, None)
    importlib.reload(mod)


def _reload_with_env(value: str | None):
    if value is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = value
    import polaris.strategies.rsi_bb_pullback as mod

    return importlib.reload(mod)


# ===========================================================================
# Module-constant proof: REAL byte-identical, VIRTUAL loosened
# ===========================================================================


def test_real_mode_constants_byte_identical_when_env_unset() -> None:
    real_mod = _reload_with_env(None)
    assert real_mod.RSI_THRESHOLD == 39.0
    assert real_mod.BB_TOUCH_MULT == 1.0


def test_virtual_mode_constants_loosened() -> None:
    virtual_mod = _reload_with_env("1")
    assert virtual_mod.RSI_THRESHOLD == 55.0
    assert virtual_mod.BB_TOUCH_MULT == 1.004


# ===========================================================================
# RSI_THRESHOLD-driven firing delta (BB gate held identical both sides:
# a deep pierce so BB_TOUCH_MULT cannot be the deciding factor here).
# ===========================================================================


def _mv_rsi_delta(rsi: float) -> MarketView:
    bars = _bars(210, base_close=100.0)
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high,
        low=85.0, close=95.0, volume=last.volume,  # deep BB pierce, above ma_200
    )
    return MarketView(
        symbol="SOL-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=95.0, spread_bps=3.0,
        rsi_14=rsi, bb_lower=90.0, ma_200=80.0,
    )


def test_rsi_threshold_real_rejects_45_virtual_fires() -> None:
    # rsi=45 sits strictly between REAL(39) and VIRTUAL(55): REAL must reject,
    # VIRTUAL (which now admits the low-50s dip cluster) must fire.
    mv = _mv_rsi_delta(45.0)

    real_mod = _reload_with_env(None)
    assert real_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None, (
        "REAL (39.0) must reject rsi=45 — byte-identical to the pre-existing threshold"
    )

    virtual_mod = _reload_with_env("1")
    sig = virtual_mod.RSIBBPullbackStrategy().generate_raw_signal(mv)
    assert sig is not None, "VIRTUAL (55.0) must fire on rsi=45 (revival target)"
    assert sig.side == "long"


def test_rsi_threshold_both_reject_above_virtual_ceiling() -> None:
    # rsi=60 clears neither threshold — confirms the loosening is bounded, not
    # a bare "always fire" (the setup identity — BB touch + ma_200 uptrend —
    # still gates every RSI value, and 60 is outside even the loosened band).
    mv = _mv_rsi_delta(60.0)
    real_mod = _reload_with_env(None)
    assert real_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None
    virtual_mod = _reload_with_env("1")
    assert virtual_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None


# ===========================================================================
# BB_TOUCH_MULT-driven firing delta (RSI held identical both sides: deep
# oversold so RSI_THRESHOLD cannot be the deciding factor here).
# ===========================================================================


def _mv_bb_delta(low: float) -> MarketView:
    bars = _bars(210, base_close=100.0)
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=last.high,
        low=low, close=95.0, volume=last.volume,
    )
    return MarketView(
        symbol="CFX-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=95.0, spread_bps=3.0,
        rsi_14=20.0, bb_lower=100.0, ma_200=80.0,
    )


def test_bb_touch_mult_real_rejects_near_touch_virtual_fires() -> None:
    # low = 100.3 is 0.3% ABOVE bb_lower(100.0) — NOT a pierce (REAL exact-pierce
    # rule: last.low > bb_lo * 1.0 -> rejects), but WITHIN VIRTUAL's 0.4% near-
    # touch band (last.low > bb_lo * 1.004 = 100.4 -> False -> gate passes).
    mv = _mv_bb_delta(100.3)

    real_mod = _reload_with_env(None)
    assert real_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None, (
        "REAL (exact pierce, *1.0) must reject a low that never touched bb_lower"
    )

    virtual_mod = _reload_with_env("1")
    sig = virtual_mod.RSIBBPullbackStrategy().generate_raw_signal(mv)
    assert sig is not None, "VIRTUAL (0.4% near-touch) must fire on the same bar"
    assert sig.side == "long"


def test_bb_touch_mult_both_reject_far_from_band() -> None:
    # low = 102.0 (2% above bb_lower) is outside even the 0.4% near-touch band —
    # confirms the loosening is bounded, not an unconditional pass.
    mv = _mv_bb_delta(102.0)
    real_mod = _reload_with_env(None)
    assert real_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None
    virtual_mod = _reload_with_env("1")
    assert virtual_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is None


def test_exact_pierce_still_fires_both_modes() -> None:
    # A genuine pierce (low < bb_lower) must keep firing in BOTH modes — the
    # multiplier only WIDENS the gate, it never narrows the pre-existing pass case.
    mv = _mv_bb_delta(85.0)
    real_mod = _reload_with_env(None)
    assert real_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is not None
    virtual_mod = _reload_with_env("1")
    assert virtual_mod.RSIBBPullbackStrategy().generate_raw_signal(mv) is not None
