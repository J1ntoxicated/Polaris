"""Wave 1 — Capital index/commodity bar-path routing carve-out.

DEMO/PAPER only. AGGRESSIVE / flow_not_block: these tests pin a PURELY ADDITIVE
reachability fix — the asset-class routing skip at ``_production_tick`` no longer
VACATES non-forex Capital index/commodity symbols that an ENABLED Capital bar
strategy supports, so the bar pipeline (donchian/momentum/session) and the tick
engine (flow micro-structure) cover COMPLEMENTARY edges on the same symbol. No
throttle / block / size-cut is introduced — the skip is NARROWED, not widened.

The cross-producer double-open backstop is the per-symbol risk cap (sizing
``per_symbol_remaining_pct``) which sums open_risk_pct over ``(venue, symbol)``
across BOTH producers — verified in ``test_per_symbol_cap_cross_producer``.
"""

from __future__ import annotations

from polaris.scripts._production_tick import (
    CAPITAL_BAR_STRATEGY_SYMBOLS,
    keep_on_bar_path,
)
from polaris.strategies import session_breakout, xau_indices_trend


def test_capital_index_symbol_reaches_bar_path() -> None:
    # US100 is in session_breakout + xau_indices_trend SUPPORTED_SYMBOLS — it
    # must NOT be vacated from the bar fan-out (previously skipped → 0% emit).
    assert keep_on_bar_path(asset_class="index", symbol="US100") is True


def test_gold_commodity_reaches_bar_path() -> None:
    # GOLD is the LIVE Capital commodity symbol for xau_indices_trend (whose
    # whole universe was vacated → structural 0% emit ceiling).
    assert keep_on_bar_path(asset_class="commodity", symbol="GOLD") is True


def test_forex_still_reaches_bar_path() -> None:
    # The forex carve-out was already done; it must remain on the bar path.
    assert keep_on_bar_path(asset_class="forex", symbol="EURUSD") is True


def test_okx_crypto_reaches_bar_path() -> None:
    # STEP1 multi-horizon: OKX SPOT (asset_class 'crypto') carries the 1H swing
    # strategies (tsmom / supertrend / spot_donchian / ema_crossover — whole-
    # universe, NO symbol whitelist). The asset-class skip VACATED the entire OKX
    # spot universe to the tick engine (scalp-only), giving those swing strategies
    # a structural 0-entry ceiling (live: 0 swing/position closes, max hold 28m).
    # They must stay on the bar pipeline for ANY OKX crypto symbol — the tick
    # engine STILL trades it (scalp); the bar strategies add the COMPLEMENTARY 1H
    # trend edge on the same symbol.
    assert keep_on_bar_path(asset_class="crypto", symbol="BTC-USDT") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="ETH-USDT") is True
    # Case-insensitive, and ANY OKX spot symbol (no per-symbol gate — these
    # strategies trade the whole active universe).
    assert keep_on_bar_path(asset_class="Crypto", symbol="SOL-USDT") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="DOGE-USDT") is True


def test_spot_alias_reaches_bar_path() -> None:
    # The OKX swing strategy metadata declares asset_class 'spot'; the live
    # universe row resolves to 'crypto'. Accept BOTH spellings so the carve-out
    # can never drift from the strategy declaration vs the universe label.
    assert keep_on_bar_path(asset_class="spot", symbol="BTC-USDT") is True


def test_unsupported_capital_symbol_still_vacated() -> None:
    # A Capital index/commodity symbol NO enabled bar strategy supports stays
    # owned by the tick engine (routed as before — no widening beyond support).
    assert keep_on_bar_path(asset_class="commodity", symbol="NATURALGAS") is False
    assert keep_on_bar_path(asset_class="index", symbol="HK50") is False


def test_carveout_symbols_union_matches_enabled_strategies() -> None:
    # The carve-out set is exactly the union of the two ENABLED Capital
    # index/commodity bar strategies' SUPPORTED_SYMBOLS (no hand-rolled list).
    expected = (
        xau_indices_trend.SUPPORTED_SYMBOLS | session_breakout.SUPPORTED_SYMBOLS
    )
    assert expected == CAPITAL_BAR_STRATEGY_SYMBOLS
    # GOLD + US100 are both present (the two strategies this wave revives).
    assert "GOLD" in CAPITAL_BAR_STRATEGY_SYMBOLS
    assert "US100" in CAPITAL_BAR_STRATEGY_SYMBOLS
