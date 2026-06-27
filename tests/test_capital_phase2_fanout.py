"""Phase2 Capital diversification fan-out — OOS-validated SUPPORTED_SYMBOLS widen.

DEMO/PAPER only. AGGRESSIVE / flow_not_block: these tests pin a PURELY ADDITIVE
instrument widen on THREE already-wired verified archetypes — NO new learning
code, NO block/skip/size-cut, NO 9-stack/sizing/rail change. Per-ticker learner
tuning is ALREADY live (L4 cell key=ticker, L5 NIG posterior parent-seeding); the
ONLY wiring is widening each module's SUPPORTED_SYMBOLS frozenset to the
both-OOS-positive instrument set from the Phase2 generalization research, plus the
reachability union so the new Capital commodity epics reach generate_raw_signal.

Three legs (Phase2 scoping wajecs9ct fan_out_plan):
  1. Commodity let-run trend (gold_trend_chandelier_1d) — Tier-1 BUILD = the
     6 both-OOS-confirmed precious+base metals + select energy. HARD EXCLUDE the
     4 OVERFIT energy/grain (WTI/NATGAS/HEATINGOIL/WHEAT — OOS sign-flip).
  2. Index momentum (index_52w_high_momentum) — DROP AU200 (live loser, both
     runs) + ADD the OOS-confirmed global index set. HARD EXCLUDE the choppy
     continental-EU + small-cap cluster (RTY/DE40/FR40/IT40/SW20/CN50). long-only.
  3. Selective FX position-momentum (tsmom_12_1_multiasset) — ADD the 6
     both-OOS-positive trending-USD/carry-EM pairs ONLY (NOT a 23-wide basket).

🚨 Epic-spelling: the live Capital bear epic is OIL_BRENT (not "BRENT") and
SOYBEANOIL (not "SOYOIL") — verified against the live universe table. Using the
wrong spelling = silent no-emit (inert, harmless but dead).
"""

from __future__ import annotations

from polaris.scripts._production_tick import (
    CAPITAL_BAR_STRATEGY_SYMBOLS,
    keep_on_bar_path,
)
from polaris.strategies import (
    gold_trend_chandelier_1d,
    index_52w_high_momentum,
    tsmom_12_1_multiasset,
)

# ---------------------------------------------------------------------------
# 1. Commodity let-run trend — Tier-1 BUILD + Tier-2 learner-watch
# ---------------------------------------------------------------------------

# Tier-1 = both-OOS-confirmed by TWO independent generalization runs.
_COMMODITY_TIER1 = ("GOLD", "SILVER", "PALLADIUM", "COPPER", "OIL_BRENT", "GASOLINE")
# Tier-2 = admit-with-learner-watch (one run both-OOS+ / marginal 2nd run).
_COMMODITY_TIER2 = ("PLATINUM", "SOYBEAN", "ALUMINUM", "SOYBEANOIL")
# HARD EXCLUDE — OVERFIT (OOS sign-flip + fat-tail). Must NEVER be added.
_COMMODITY_OVERFIT = ("WTI_CRUDE", "WTI", "NATGAS", "NATURALGAS", "HEATINGOIL", "WHEAT")


def test_commodity_tier1_in_supported() -> None:
    sup = gold_trend_chandelier_1d.SUPPORTED_SYMBOLS
    for sym in _COMMODITY_TIER1:
        assert sym in sup, f"Tier-1 OOS commodity {sym} must be supported"


def test_commodity_tier2_learner_watch_in_supported() -> None:
    sup = gold_trend_chandelier_1d.SUPPORTED_SYMBOLS
    for sym in _COMMODITY_TIER2:
        assert sym in sup, f"Tier-2 learner-watch commodity {sym} must be supported"


def test_commodity_legacy_alias_kept() -> None:
    # XAUUSD legacy alias stays (purely additive — no path drops the old spelling).
    assert "XAUUSD" in gold_trend_chandelier_1d.SUPPORTED_SYMBOLS


def test_commodity_overfit_excluded() -> None:
    sup = gold_trend_chandelier_1d.SUPPORTED_SYMBOLS
    for sym in _COMMODITY_OVERFIT:
        assert sym not in sup, f"OVERFIT energy/grain {sym} must NOT be added"


def test_commodity_brent_epic_spelling() -> None:
    # Live Capital epic = OIL_BRENT (Yahoo BZ=F is a fetch detail, NEVER the
    # RawSignal.symbol). The naive "BRENT" spelling = silent no-emit (inert).
    sup = gold_trend_chandelier_1d.SUPPORTED_SYMBOLS
    assert "OIL_BRENT" in sup
    assert "BRENT" not in sup


def test_commodity_soyoil_epic_spelling() -> None:
    # Live Capital epic = SOYBEANOIL (not "SOYOIL").
    sup = gold_trend_chandelier_1d.SUPPORTED_SYMBOLS
    assert "SOYBEANOIL" in sup
    assert "SOYOIL" not in sup


def test_commodity_supported_is_exact_set() -> None:
    # The widened set is EXACTLY Tier-1 ∪ Tier-2 ∪ {XAUUSD legacy alias} — no
    # extra symbol crept in, none of the OOS set is missing.
    expected = frozenset(_COMMODITY_TIER1) | frozenset(_COMMODITY_TIER2) | {"XAUUSD"}
    assert expected == gold_trend_chandelier_1d.SUPPORTED_SYMBOLS


# ---------------------------------------------------------------------------
# 2. Index 52w-high momentum — DROP AU200 loser, ADD OOS-confirmed global set
# ---------------------------------------------------------------------------

# Existing OOS-holding baseline that must remain.
_INDEX_BASELINE_KEEP = ("US500", "US100", "J225", "HK50")
# New OOS-confirmed additions.
_INDEX_NEW = ("US30", "UK100", "SP35", "NL25", "EU50", "IN50", "KS200", "SG25")
# DROP — both runs flag AU200 a live LOSER (-0.28R).
_INDEX_DROPPED = ("AU200", "AU200AU")
# HARD EXCLUDE — mom52w OOS-negative (choppy continental-EU + small-cap).
_INDEX_OVERFIT = ("RTY", "DE40", "FR40", "IT40", "SW20", "CN50")


def test_index_baseline_kept() -> None:
    sup = index_52w_high_momentum.SUPPORTED_SYMBOLS
    for sym in _INDEX_BASELINE_KEEP:
        assert sym in sup, f"OOS-holding baseline index {sym} must remain"


def test_index_new_oos_additions() -> None:
    sup = index_52w_high_momentum.SUPPORTED_SYMBOLS
    for sym in _INDEX_NEW:
        assert sym in sup, f"OOS-confirmed index {sym} must be added"


def test_index_au200_loser_dropped() -> None:
    # AU200 / AU200AU = live LOSER per both research runs → removed (corrective,
    # not additive — the ONE removal in this fan-out).
    sup = index_52w_high_momentum.SUPPORTED_SYMBOLS
    for sym in _INDEX_DROPPED:
        assert sym not in sup, f"live-loser index {sym} must be dropped"


def test_index_overfit_excluded() -> None:
    sup = index_52w_high_momentum.SUPPORTED_SYMBOLS
    for sym in _INDEX_OVERFIT:
        assert sym not in sup, f"OOS-negative index {sym} must NOT be added"


def test_index_supported_is_exact_set() -> None:
    expected = frozenset(_INDEX_BASELINE_KEEP) | frozenset(_INDEX_NEW)
    assert expected == index_52w_high_momentum.SUPPORTED_SYMBOLS


def test_index_still_long_only() -> None:
    # short/new-low mirror REJECTED (1/15 positive) — the entry side is unchanged.
    # Guard: the module exposes no SHORT path (long-only kept).
    import inspect

    src = inspect.getsource(index_52w_high_momentum.Index52WHighMomentumStrategy)
    assert 'side="short"' not in src
    assert 'side="long"' in src


# ---------------------------------------------------------------------------
# 3. Selective FX position-momentum (tsmom 12-1) — 6 both-OOS pairs only
# ---------------------------------------------------------------------------

_FX_TSMOM_SELECTIVE = ("USDJPY", "USDCAD", "NZDUSD", "GBPJPY", "USDZAR", "USDTRY")


def test_fx_tsmom_selective_added() -> None:
    sup = tsmom_12_1_multiasset.SUPPORTED_SYMBOLS
    for sym in _FX_TSMOM_SELECTIVE:
        assert sym in sup, f"both-OOS FX pair {sym} must be added to tsmom"


def test_fx_tsmom_is_selective_not_basket() -> None:
    # FX is where the research most strongly says NOT to mass-fan-out (class-wide
    # never exceeds 6/23 OOS). Pin EXACTLY the 6 selective pairs — no broad basket.
    fx_added = tsmom_12_1_multiasset.FX_TSMOM_LEG
    assert fx_added == frozenset(_FX_TSMOM_SELECTIVE)
    assert len(fx_added) == 6


def test_fx_tsmom_crypto_core_preserved() -> None:
    # The crypto OKX core + equity-ETF leg are untouched (the FX leg is additive).
    sup = tsmom_12_1_multiasset.SUPPORTED_SYMBOLS
    assert "BTC-USDT" in sup
    assert "ETH-USDT" in sup
    assert "SPY" in sup  # EQUITY_ETF_LEG preserved


# ---------------------------------------------------------------------------
# 4. Reachability — new Capital commodity epics reach the bar path
# ---------------------------------------------------------------------------


def test_capital_bar_union_includes_gold_module() -> None:
    # The reachability union MUST now import gold_trend_chandelier_1d's
    # SUPPORTED_SYMBOLS — otherwise the new commodity epics are vacate-skipped off
    # the bar fan-out and generate_raw_signal is NEVER reached (INERT recurrence).
    for sym in (*_COMMODITY_TIER1, *_COMMODITY_TIER2):
        assert sym in CAPITAL_BAR_STRATEGY_SYMBOLS, (
            f"{sym} must be in the bar-strategy union (reachability)"
        )


def test_new_commodity_epics_keep_on_bar_path() -> None:
    for sym in (*_COMMODITY_TIER1, *_COMMODITY_TIER2):
        assert keep_on_bar_path(asset_class="commodity", symbol=sym) is True, (
            f"{sym} must keep_on_bar_path (else vacate-skip → no-emit)"
        )


def test_new_index_epics_keep_on_bar_path() -> None:
    # The newly-added index epics that EXIST in the Capital universe must reach the
    # bar path. (IN50/KS200 are absent from this Capital demo → inert by bar
    # coverage, reported separately — keep_on_bar_path still returns True since the
    # union keys on the strategy SUPPORTED_SYMBOLS, not live universe membership.)
    for sym in _INDEX_NEW:
        assert keep_on_bar_path(asset_class="index", symbol=sym) is True, (
            f"{sym} index must keep_on_bar_path"
        )


def test_dropped_au200_no_longer_in_52w_module() -> None:
    # AU200/AU200AU are dropped from index_52w but STILL supported by
    # index_dual_momentum_rotation → they legitimately remain in the union (that
    # other strategy still trades them). Assert the drop is real at the 52w level.
    assert "AU200" not in index_52w_high_momentum.SUPPORTED_SYMBOLS
    assert "AU200AU" not in index_52w_high_momentum.SUPPORTED_SYMBOLS


def test_overfit_commodity_still_vacated() -> None:
    # The OVERFIT exclusions are NOT supported → stay owned by the tick engine
    # (the widen never goes beyond actual OOS-validated support).
    for sym in ("NATURALGAS", "WTI_CRUDE", "WHEAT"):
        assert keep_on_bar_path(asset_class="commodity", symbol=sym) is False


# ---------------------------------------------------------------------------
# 5. Non-regression — counts + the existing carve-out still holds
# ---------------------------------------------------------------------------


def test_commodity_instrument_count_grew() -> None:
    # was 2 (GOLD/XAUUSD) → 11 (6 Tier-1 + 4 Tier-2 + XAUUSD alias).
    assert len(gold_trend_chandelier_1d.SUPPORTED_SYMBOLS) == 11


def test_index_instrument_count() -> None:
    # was 6 (incl. AU200/AU200AU loser) → 12 (4 baseline + 8 new, AU200 dropped).
    assert len(index_52w_high_momentum.SUPPORTED_SYMBOLS) == 12


def test_existing_gold_index_carveout_not_regressed() -> None:
    # GOLD + the wave1/wave2 index symbols still reach the bar path (additive).
    assert keep_on_bar_path(asset_class="commodity", symbol="GOLD") is True
    assert keep_on_bar_path(asset_class="index", symbol="US100") is True
    assert keep_on_bar_path(asset_class="index", symbol="US500") is True
    assert keep_on_bar_path(asset_class="index", symbol="J225") is True


def test_forex_crypto_carveout_not_regressed() -> None:
    assert keep_on_bar_path(asset_class="forex", symbol="EURUSD") is True
    assert keep_on_bar_path(asset_class="crypto", symbol="BTC-USDT") is True
    assert keep_on_bar_path(asset_class="spot", symbol="ETH-USDT") is True
