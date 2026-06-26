"""INERT-strategy reachability fix — wave2 index strategies + equity_52wk warmup.

DEMO/PAPER only. AGGRESSIVE / flow_not_block: these tests pin a PURELY ADDITIVE
reachability widen — NO block / skip / size-cut is introduced. Three wave2
strategies were INERT (built, registered, logged, but their
``generate_raw_signal`` was NEVER reached → silent no-emit):

P1 — index_dual_momentum_rotation + index_52w_high_momentum support the wave2
index symbols (J225 / HK50 / AU200 / AU200AU). The tick-engine vacate-skip at
``_production_tick`` dropped every Capital index symbol NOT in the existing
xau_indices_trend ∪ session_breakout carve-out, so those four symbols had
``keep_on_bar_path == False`` → the bar fan-out ``continue``'d past them → the
two index strategies' ``generate_raw_signal`` was never called (live: 0 signals).
The carve-out now also unions the two index strategies' SUPPORTED_SYMBOLS.

P2 — equity_52wk_high_breakout needs 253 daily bars (HIGH_LOOKBACK 252 + 1) for
``warmup_ok``. The global 1D fetch/read ``limit=240`` capped the canvas at 240 <
253 → ``warmup_ok`` was ALWAYS False (no-emit across all equity symbols). The 1D
timeframe now resolves a limit >= 260 (per-timeframe split); all OTHER timeframes
keep 240 (Alpaca 429 avoidance preserved). HIGH_LOOKBACK / warmup is UNCHANGED.
"""

from __future__ import annotations

from polaris.scripts._production_bars import bar_fetch_limit_for
from polaris.scripts._production_tick import (
    CAPITAL_BAR_STRATEGY_SYMBOLS,
    keep_on_bar_path,
)
from polaris.strategies import (
    index_52w_high_momentum,
    index_dual_momentum_rotation,
    session_breakout,
    xau_indices_trend,
)
from polaris.strategies.equity_52wk_high_breakout import HIGH_LOOKBACK

# ---------------------------------------------------------------------------
# P1 — wave2 index symbols now reach the bar path (generate_raw_signal reachable)
# ---------------------------------------------------------------------------

_WAVE2_INDEX_SYMBOLS = ("J225", "HK50", "AU200", "AU200AU")


def test_wave2_index_symbols_reach_bar_path() -> None:
    # Each wave2 index symbol an ENABLED index bar strategy supports must stay on
    # the bar fan-out (previously vacated → generate_raw_signal never reached).
    for sym in _WAVE2_INDEX_SYMBOLS:
        assert keep_on_bar_path(asset_class="index", symbol=sym) is True, (
            f"{sym} must reach the bar path — index strategies were INERT"
        )


def test_wave2_index_symbols_case_insensitive() -> None:
    # The strategy symbol gate upper-cases; the carve-out must match either case.
    assert keep_on_bar_path(asset_class="index", symbol="j225") is True
    assert keep_on_bar_path(asset_class="Index", symbol="hk50") is True


def test_carveout_unions_index_strategy_symbols() -> None:
    # The carve-out set is exactly the union of ALL FOUR enabled Capital
    # index/commodity bar strategies' SUPPORTED_SYMBOLS — no hand-rolled list.
    expected = (
        xau_indices_trend.SUPPORTED_SYMBOLS
        | session_breakout.SUPPORTED_SYMBOLS
        | index_dual_momentum_rotation.SUPPORTED_SYMBOLS
        | index_52w_high_momentum.SUPPORTED_SYMBOLS
    )
    assert expected == CAPITAL_BAR_STRATEGY_SYMBOLS
    for sym in _WAVE2_INDEX_SYMBOLS:
        assert sym in CAPITAL_BAR_STRATEGY_SYMBOLS


def test_existing_xau_session_carveout_not_regressed() -> None:
    # The wave1 carve-out (GOLD commodity, US100/US500 index) must still hold —
    # the widen is purely additive (no symbol dropped).
    assert keep_on_bar_path(asset_class="commodity", symbol="GOLD") is True
    assert keep_on_bar_path(asset_class="index", symbol="US100") is True
    assert keep_on_bar_path(asset_class="index", symbol="US500") is True
    assert "GOLD" in CAPITAL_BAR_STRATEGY_SYMBOLS
    assert "US100" in CAPITAL_BAR_STRATEGY_SYMBOLS


def test_forex_and_crypto_carveout_not_regressed() -> None:
    # The forex + OKX-spot carve-outs are untouched by the index widen.
    assert keep_on_bar_path(asset_class="forex", symbol="EURUSD") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="BTC-USDT") is True
    assert keep_on_bar_path(asset_class="spot", symbol="ETH-USDT") is True


def test_unsupported_capital_symbol_still_vacated() -> None:
    # A Capital index/commodity symbol NO enabled bar strategy supports stays
    # owned by the tick engine (the widen never goes beyond actual support).
    assert keep_on_bar_path(asset_class="commodity", symbol="NATURALGAS") is False
    assert keep_on_bar_path(asset_class="index", symbol="GER40") is False


# ---------------------------------------------------------------------------
# P2 — 1D warmup depth: limit >= 260 for 1D, 240 elsewhere (equity_52wk reach)
# ---------------------------------------------------------------------------


def test_1d_fetch_limit_clears_equity_52wk_warmup() -> None:
    # equity_52wk_high_breakout warmup is HIGH_LOOKBACK + 1 (253). The 1D fetch
    # limit must be >= that so warmup_ok can become True (was 240 < 253 → INERT).
    warmup_required = HIGH_LOOKBACK + 1
    assert bar_fetch_limit_for("1D") >= warmup_required
    assert bar_fetch_limit_for("1D") >= 260


def test_non_1d_timeframes_keep_240_limit() -> None:
    # Intraday timeframes keep the 240 cap (Alpaca 429 avoidance preserved).
    for tf in ("1m", "5m", "15m", "1H"):
        assert bar_fetch_limit_for(tf) == 240


def test_unknown_timeframe_defaults_to_240() -> None:
    assert bar_fetch_limit_for("4h") == 240


def test_high_lookback_unchanged() -> None:
    # Guard: the warmup depth is NOT lowered (would corrupt the 252-high edge).
    assert HIGH_LOOKBACK == 252


def test_live_tick_read_passes_per_tf_limit() -> None:
    # Source-level regression lint: the live tick bar read-back MUST pass the
    # per-timeframe limit. A read without it defaults to 240 → 1D canvas capped
    # below the 253 equity_52wk warmup → INERT again. Pin the wiring so a future
    # edit can't silently drop it.
    import re
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    # The strategy-canvas read is the ``bars = read_recent_bars_ondemand(...)``
    # call (the 1m regime read is a SEPARATE ``bars_1m = ...`` that is always 1m
    # and correctly keeps the 240 default — only the per-strategy canvas needs
    # the per-tf depth).
    read_call = re.search(
        r"\bbars = await read_recent_bars_ondemand\((?:[^()]|\([^()]*\))*\)", src
    )
    assert read_call is not None, "tick strategy-canvas read-back call not found"
    assert "limit=bar_fetch_limit_for(timeframe)" in read_call.group(0), (
        "tick strategy-canvas read-back must pass "
        "limit=bar_fetch_limit_for(timeframe) so 1D clears the equity_52wk "
        "warmup (regression guard)."
    )
