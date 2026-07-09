"""VIRTUAL-mode timeframe downgrade — full-pipeline FIRING smoke (Jin 2026-07-09).

DEMO/PAPER only — virtual funds, aggressive/flow_not_block. Guards the exact
trap ``test_dispatch_ssot.py`` documents: "registered != dispatched != fires".
A strategy can be registered, ``dispatch_eligible=True``, AND still be a
silent no-emit (a warmup_bars miscalculation, a stale pre-fed indicator field
that only applied at the OLD timeframe/period, etc.) — only calling
``generate_raw_signal`` and getting back a real ``RawSignal`` proves the bar
cadence downgrade did not starve the entry trigger.

Runs the check in a FRESH subprocess with ``POLARIS_VIRTUAL_ACCOUNT`` set
BEFORE any import happens (never via ``importlib.reload`` mid-process): the 8
edited modules + ``polaris.strategies.__init__`` (which builds
``STRATEGY_REGISTRY`` and each ``dispatch_eligible`` flag from a same-process
env read at import time) have real cross-module dependencies (e.g.
``cci_reversion`` imports ``fx_breakout_basket.BASKET_SYMBOLS``); reloading
only a subset in-process risks an inconsistent half-VIRTUAL/half-REAL import
graph. A dedicated interpreter is the only way to observe the SAME import
ordering the live paper loop uses.

Of the 8 downgraded files, 6 are live in ``STRATEGY_REGISTRY`` (dispatched via
``polaris.scripts._production_tick._all_strategies`` — the G1-equivalent
universe/dispatch-eligible filter — then evaluated by
``generate_raw_signal`` — G2). The other 2 (``equity_rsi_bb_pullback`` /
``fx_range_fade``) were KILLed + UN-registered in earlier waves for reasons
unrelated to timeframe (gross-negative entry expectancy / strategy-wave1
restructure) and stay that way — this smoke does not re-register them, only
confirms their signal-generation CODE PATH still fires post-edit (module
preserved read-only, same as their existing dedicated unit tests already do).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

_REGISTERED_TARGETS = (
    "connors_rsi2",
    "supertrend",
    "ema_crossover",
    "cci_reversion",
    "fx_breakout_basket",
    "macd_ema_trend_pullback",
)
_UNREGISTERED_TARGETS = ("equity_rsi_bb_pullback", "fx_range_fade")

_VIRTUAL_EXPECTED_TF = {
    "connors_rsi2": "1H",
    "supertrend": "15m",
    "ema_crossover": "15m",
    "cci_reversion": "15m",
    "fx_breakout_basket": "15m",
    "macd_ema_trend_pullback": "1H",
    "equity_rsi_bb_pullback": "1H",
    "fx_range_fade": "15m",
}

# The script fires each of the 8 strategies with a tailored trigger-set bar
# series (same fixture idioms as the existing per-strategy unit tests:
# test_connors_rsi2.py / test_supertrend.py / test_strategies_signal_gen.py /
# test_cci_reversion.py / test_fx_range_fade.py) and prints one JSON line per
# result so the parent test can assert on structured data, not string-grep.
_SCRIPT = r"""
import json, sys
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.base import BarView, MarketView
from polaris.scripts._production_tick import _all_strategies

results = {}

def _bar(ts, o, h, low_, c, vol=1000.0):
    return BarView(ts=ts, open=o, high=h, low=low_, close=c, volume=vol)

dispatch_ids = {s.metadata.strategy_id for s in _all_strategies()}
results["_dispatch_ids"] = sorted(dispatch_ids)

REGISTERED_TARGETS = [
    "connors_rsi2", "supertrend", "ema_crossover",
    "cci_reversion", "fx_breakout_basket", "macd_ema_trend_pullback",
]

# ---- connors_rsi2 ----
def _bars_uptrend(n, start=50.0, step=0.2):
    base = 1_700_000_000
    out = []
    for i in range(n):
        c = start + step * i
        out.append(_bar(base + i * 86400, c, c + 0.1, c - 0.1, c))
    return out

cls = STRATEGY_REGISTRY["connors_rsi2"]
bars = _bars_uptrend(220, start=50.0, step=0.3)
last_close = bars[-3].close
drop = last_close - 1.5
bars[-2] = _bar(bars[-2].ts, drop, drop + 0.1, drop - 0.1, drop)
bars[-1] = _bar(bars[-1].ts, drop - 1.5, drop, drop - 1.6, drop - 1.5)
mv = MarketView(symbol="AAPL", venue="alpaca", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01)
sig = cls().generate_raw_signal(mv)
results["connors_rsi2"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- supertrend ----
def _downtrend_then_flip():
    base = 1_700_000_000
    bars = []
    price = 1000.0
    for i in range(25):
        c = price
        bars.append(_bar(base + i * 3600, c + 4, c + 6, c - 6, c))
        price -= 8.0
    last_close = bars[-1].close
    flip_close = last_close + 80.0
    bars.append(_bar(base + 25 * 3600, last_close, flip_close + 5, last_close - 2, flip_close))
    return bars

cls = STRATEGY_REGISTRY["supertrend"]
bars = _downtrend_then_flip()
mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=bars[-1].close, spread_bps=1.0, atr_pct=0.02)
sig = cls().generate_raw_signal(mv)
results["supertrend"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- ema_crossover ----
def _ema_cross_bars():
    floor = 100.0
    closes = [floor] * 60 + [floor + 8.0]
    out = []
    for i, c in enumerate(closes):
        out.append(_bar(1_700_000_000 + i * 3600, c - 0.3, c + 0.5, c - 0.6, c, vol=1500.0))
    return out

cls = STRATEGY_REGISTRY["ema_crossover"]
bars = _ema_cross_bars()
mv = MarketView(symbol="BTC-USDT", venue="okx", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=bars[-1].close, spread_bps=3.0, ma_200=100.0, adx_14=30.0)
sig = cls().generate_raw_signal(mv)
results["ema_crossover"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- cci_reversion ----
def _oversold_then_revert():
    base = 1_700_000_000
    bars = []
    for i in range(25):
        bars.append(_bar(base + i * 3600, 2000.0, 2001.0, 1999.0, 2000.0))
    bars.append(_bar(base + 25 * 3600, 1960.0, 1961.0, 1959.0, 1960.0))
    bars.append(_bar(base + 26 * 3600, 1955.0, 1956.0, 1954.0, 1955.0))
    bars.append(_bar(base + 27 * 3600, 1995.0, 1996.0, 1994.0, 1995.0))
    return bars

cls = STRATEGY_REGISTRY["cci_reversion"]
bars = _oversold_then_revert()
mv = MarketView(symbol="GOLD", venue="capital", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=bars[-1].close, spread_bps=1.0, atr_pct=0.01)
sig = cls().generate_raw_signal(mv)
results["cci_reversion"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- fx_breakout_basket ----
def _fx_breakout_bars():
    base = 1_700_000_000
    out = []
    for i in range(30):
        out.append(_bar(base + i * 3600, 1.099, 1.101, 1.099, 1.10))
    out.append(_bar(base + 30 * 3600, 1.20, 1.25, 1.19, 1.24, vol=1500.0))
    return out

cls = STRATEGY_REGISTRY["fx_breakout_basket"]
bars = _fx_breakout_bars()
mv = MarketView(symbol="EURUSD", venue="capital", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=1.24, spread_bps=1.0,
                donchian_high_40=1.20, adx_14=28.0)
sig = cls().generate_raw_signal(mv)
results["fx_breakout_basket"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- macd_ema_trend_pullback ----
cls = STRATEGY_REGISTRY["macd_ema_trend_pullback"]
closes = [100.0] * 60 + [108.0]
bars = [
    _bar(1_700_000_000 + i * 3600, c - 0.3, c + 0.5, c - 0.6, c,
         vol=1000.0 if i < 60 else 5000.0)
    for i, c in enumerate(closes)
]
mv = MarketView(symbol="SPY", venue="okx", timeframe=cls.metadata.timeframe,
                bars=bars, last_price=bars[-1].close, spread_bps=3.0)
sig = cls().generate_raw_signal(mv)
results["macd_ema_trend_pullback"] = {
    "timeframe": cls.metadata.timeframe, "fired": sig is not None,
    "side": sig.side if sig else None,
}

# ---- equity_rsi_bb_pullback (pre-existing KILL, NOT in STRATEGY_REGISTRY) ----
from polaris.strategies.equity_rsi_bb_pullback import EquityRSIBBPullbackStrategy
bars = [_bar(1_700_000_000 + i * 86400, 95.0, 96.0, 88.0, 95.0) for i in range(205)]
mv = MarketView(symbol="AAPL", venue="alpaca",
                timeframe=EquityRSIBBPullbackStrategy.metadata.timeframe,
                bars=bars, last_price=95.0, spread_bps=1.0,
                rsi_14=20.0, bb_lower=90.0, bb_upper=110.0, bb_middle=100.0, ma_200=80.0)
sig = EquityRSIBBPullbackStrategy().generate_raw_signal(mv)
results["equity_rsi_bb_pullback"] = {
    "timeframe": EquityRSIBBPullbackStrategy.metadata.timeframe,
    "fired": sig is not None, "side": sig.side if sig else None,
    "in_registry": "equity_rsi_bb_pullback" in STRATEGY_REGISTRY,
}

# ---- fx_range_fade (pre-existing KILL, NOT in STRATEGY_REGISTRY) ----
from polaris.strategies.fx_range_fade import FXRangeFadeStrategy
bars = [_bar(1_700_000_000 + i * 3600, 1.1050, 1.1051, 1.1049, 1.1050) for i in range(31)]
mv = MarketView(symbol="EURUSD", venue="capital",
                timeframe=FXRangeFadeStrategy.metadata.timeframe,
                bars=bars, last_price=1.1050, spread_bps=1.0, atr_pct=0.001,
                adx_14=12.0, bb_upper=1.1040, bb_lower=1.0960, bb_middle=1.1000)
sig = FXRangeFadeStrategy().generate_raw_signal(mv)
results["fx_range_fade"] = {
    "timeframe": FXRangeFadeStrategy.metadata.timeframe,
    "fired": sig is not None, "side": sig.side if sig else None,
    "in_registry": "fx_range_fade" in STRATEGY_REGISTRY,
}

print(json.dumps(results))
"""


def _run_smoke(*, virtual: bool) -> dict[str, Any]:
    env = dict(os.environ)
    if virtual:
        env["POLARIS_VIRTUAL_ACCOUNT"] = "1"
    else:
        env.pop("POLARIS_VIRTUAL_ACCOUNT", None)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, dir=_REPO_ROOT
    ) as f:
        f.write(_SCRIPT)
        script_path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        os.unlink(script_path)
    assert proc.returncode == 0, (
        f"smoke subprocess (virtual={virtual}) failed:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    result: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


def test_all_8_strategies_fire_a_real_signal_under_virtual() -> None:
    """G1 (dispatch-eligible universe) -> G2 (generate_raw_signal) end to end,
    in a fresh VIRTUAL-mode process — the exact env the live paper loop uses.
    Every one of the 8 downgraded strategies must return a REAL RawSignal
    (not just be registered/eligible) on its tailored trigger-set bars."""
    results = _run_smoke(virtual=True)

    for sid in _REGISTERED_TARGETS:
        assert sid in results["_dispatch_ids"], (
            f"{sid} registered+eligible but NOT dispatched under VIRTUAL "
            "(registered != dispatched trap)"
        )
        assert results[sid]["fired"], (
            f"{sid} dispatched but generate_raw_signal returned None "
            "(dispatched != fires trap — the timeframe downgrade may have "
            "starved warmup/indicator supply)"
        )
        assert results[sid]["timeframe"] == _VIRTUAL_EXPECTED_TF[sid]

    for sid in _UNREGISTERED_TARGETS:
        assert not results[sid]["in_registry"], (
            f"{sid} unexpectedly present in STRATEGY_REGISTRY — this change "
            "must not resurrect a pre-existing KILL"
        )
        assert results[sid]["fired"], (
            f"{sid} (pre-existing KILL, module-only) no longer fires its raw "
            "signal after the timeframe edit — code path broken"
        )
        assert results[sid]["timeframe"] == _VIRTUAL_EXPECTED_TF[sid]


def test_the_4_conditionally_killed_strategies_stay_off_real_dispatch() -> None:
    """REAL mode (env unset) — connors_rsi2 / supertrend / ema_crossover /
    cci_reversion keep their PRE-EXISTING dispatch_eligible=False KILL (this
    change only touches ``timeframe``, never ``dispatch_eligible``); the same
    invariant ``test_dispatch_ssot.py`` pins, re-confirmed in a byte-identical
    fresh REAL-mode process."""
    results = _run_smoke(virtual=False)
    for sid in ("connors_rsi2", "supertrend", "ema_crossover", "cci_reversion"):
        assert sid not in results["_dispatch_ids"], (
            f"{sid} is dispatched under REAL mode — its pre-existing KILL "
            "(dispatch_eligible=False) must stay untouched by this timeframe-"
            "only change"
        )
    for sid in ("fx_breakout_basket", "macd_ema_trend_pullback"):
        assert sid in results["_dispatch_ids"], (
            f"{sid} is a live (always dispatch_eligible=True) strategy — it "
            "must still dispatch under REAL mode, unaffected by the "
            "VIRTUAL-only timeframe loosening"
        )
