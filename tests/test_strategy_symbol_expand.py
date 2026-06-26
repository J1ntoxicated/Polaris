"""strategy symbol-gate expansion — the OKX-liquid top-N roster widen.

Spec source: Jin 2026-06-27 "진입이 너무 안돼 → 늘려". The four verified 1D OKX
strategies pinned their OKX leg to a hardcoded ~5-major frozenset → daily-horizon
entry opportunity ≈ 0 (live emit 0). This widens the strategy-side leg to the
OKX-liquid USDT-quote roster sliced to the env top-N (POLARIS_STRAT_SYMBOL_TOP_N,
default 60). DEMO/PAPER 가상자금 — aggressive bias preserved, flow_not_block (the
roster is a POSITIVE-MATCH set, never a universe block; the top-N slice keeps the
most-liquid names and drops the thin-alt tail → no illiquid-whipsaw).

Asserts:
  * okx_liquid_top_n() default returns 60 USDT-quote symbols, BTC/ETH/SOL/BNB/XRP
    (the old majors) all retained (non-regression).
  * env override resizes the set; the slice keeps the MOST-liquid (top-of-roster).
  * an out-of-roster thin alt is excluded (illiquid-whipsaw guard).
  * each of the 4 strategies' SUPPORTED_SYMBOLS now admits a previously-absent
    liquid major (expansion verified), still admits BTC-USDT (non-regression),
    excludes the thin-alt tail, and the 3 multi-asset legs keep their inert
    equity-ETF sleeve (SPY/QQQ/GLD).
"""

from __future__ import annotations

import importlib

import pytest

from polaris.strategies._okx_liquid_universe import (
    OKX_LIQUID_ROSTER,
    STRAT_SYMBOL_TOP_N_DEFAULT,
    STRAT_SYMBOL_TOP_N_ENV,
    okx_liquid_top_n,
)

# The four widened 1D OKX strategy modules (module path, has-equity-ETF-leg).
_OKX_STRAT_MODULES = (
    ("polaris.strategies.okx_donchian_55_breakout", False),
    ("polaris.strategies.tsmom_12_1_multiasset", True),
    ("polaris.strategies.macd_ema_trend_pullback", True),
    ("polaris.strategies.donchian_turtle_breakout", True),
)
_OLD_MAJORS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT")
_EQUITY_ETF_LEG = frozenset({"SPY", "QQQ", "GLD"})
# A previously-absent liquid major now admitted by the widen (in roster top-60,
# none was in any old pin) — proves the expansion is live.
_NEW_LIQUID_MAJOR = "ADA-USDT"
_THIN_ALT = "THINALT-USDT"  # not in the roster → below the liquid top-N


# ---------------------------------------------------------------------------
# okx_liquid_top_n helper
# ---------------------------------------------------------------------------


def test_default_top_n_is_60_usdt_quote() -> None:
    syms = okx_liquid_top_n()
    assert len(syms) == STRAT_SYMBOL_TOP_N_DEFAULT == 60
    assert all(s.endswith("-USDT") for s in syms)


def test_default_retains_old_majors_no_regression() -> None:
    syms = okx_liquid_top_n()
    for major in _OLD_MAJORS:
        assert major in syms


def test_explicit_n_keeps_most_liquid_prefix() -> None:
    syms = okx_liquid_top_n(10)
    assert len(syms) == 10
    # The slice is liquidity-ordered → the top-10 are exactly the roster prefix.
    assert syms == frozenset(OKX_LIQUID_ROSTER[:10])
    assert "BTC-USDT" in syms  # most-liquid retained


def test_thin_alt_excluded() -> None:
    # An out-of-roster thin alt is never admitted at any sane N (illiquid guard).
    assert _THIN_ALT not in okx_liquid_top_n()
    assert _THIN_ALT not in okx_liquid_top_n(len(OKX_LIQUID_ROSTER))


def test_n_clamped_to_roster_length() -> None:
    big = okx_liquid_top_n(10_000)
    assert len(big) == len(OKX_LIQUID_ROSTER)
    assert okx_liquid_top_n(-5) == frozenset()


def test_env_override_resizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STRAT_SYMBOL_TOP_N_ENV, "12")
    assert len(okx_liquid_top_n()) == 12
    monkeypatch.setenv(STRAT_SYMBOL_TOP_N_ENV, "not-an-int")
    assert len(okx_liquid_top_n()) == STRAT_SYMBOL_TOP_N_DEFAULT  # bad → default


# ---------------------------------------------------------------------------
# per-strategy SUPPORTED_SYMBOLS expansion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("mod_path", "has_etf"), _OKX_STRAT_MODULES)
def test_strategy_supported_symbols_widened(mod_path: str, has_etf: bool) -> None:
    mod = importlib.import_module(mod_path)
    supported: frozenset[str] = mod.SUPPORTED_SYMBOLS
    # Expansion: a previously-absent liquid major is now admitted.
    assert _NEW_LIQUID_MAJOR in supported
    # Non-regression: the canonical major still passes.
    assert "BTC-USDT" in supported
    # Illiquid guard: the thin-alt tail is excluded.
    assert _THIN_ALT not in supported
    # The crypto leg is the full default top-N roster.
    assert supported >= okx_liquid_top_n()
    # Multi-asset strategies keep their inert equity-ETF sleeve; the pure-crypto
    # d55 strategy has none.
    if has_etf:
        assert supported >= _EQUITY_ETF_LEG
    else:
        assert supported == okx_liquid_top_n()
