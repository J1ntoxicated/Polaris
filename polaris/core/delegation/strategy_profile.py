"""Strategy profile derivation for the delegation gate (P2b, SHADOW v1).

Design SSOT: vault/50_research/delegation-gate-blueprint.md, §"전략 프로파일":
edge type (trend / mean-reversion / breakout) derived from metadata or
naming, hardcoded mapping documented with a vault backlink (this file, per
that instruction).

``edge_type_for`` classifies EVERY registered strategy (keyword match against
``strategy_id`` + ``correlation_group_id``) so the delegation fit-score's
regime-fit term has full coverage — unlike
``polaris.core.cell_matrix.score.regime_alignment_mult``, whose
``_TREND_STRATEGIES`` / ``_COUNTER_TREND_STRATEGIES`` allowlists only cover 2
of the ~26 live strategies (that module's own G2 emit-priority ranking is a
DIFFERENT, narrower MVP signal and is intentionally NOT reused here — mixing
its 2-strategy allowlist into this fit-score would just re-import the same
gap this module exists to close).
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = ["EdgeType", "asset_class_match", "edge_type_for"]

EdgeType = Literal["trend", "reversion", "breakout", "unknown"]

# Keyword -> edge type. Checked BREAKOUT -> TREND -> REVERSION (first match
# wins) against the lower-cased "{strategy_id} {correlation_group_id}"
# haystack. Order matters: several live strategies are trend-CONTINUATION
# entries that trigger on a "pullback" (macd_ema_trend_pullback,
# equity_etf_trend_pullback) — checking TREND before REVERSION classifies
# them correctly via their correlation_group_id's "trend" keyword instead of
# misreading the entry-trigger word "pullback" as mean-reversion.
#
# "pocket_pivot" / "vol_expansion" cover equity_vol_expansion_pocket_pivot,
# whose id/correlation_group_id contain neither "trend" nor a breakout
# keyword despite the strategy module's own metadata comment classifying it
# as the "TREND exit archetype (let-winners-run)" (no reversion substring in
# its correlation_group_id) — bucketed TREND here to match. regime_fit_score
# treats "trend" and "breakout" identically (same branch), so this choice
# does not change the computed score either way.
#
# Verified against the RUNTIME-DEFAULT registry (POLARIS_VIRTUAL_ACCOUNT=1,
# CLAUDE.md "VIRTUAL ACCOUNT 기본 ON" — 31 strategies, a superset of the
# 26-strategy REAL registry) as of 2026-07-16
# (tests/test_delegation_strategy_profile.py reloads polaris.strategies under
# the virtual env so a newly-added strategy in EITHER registry is asserted
# non-"unknown", not silently drifting).
_BREAKOUT_KEYWORDS: Final[tuple[str, ...]] = (
    "breakout", "donchian", "opening_range", "orb",
)
_TREND_KEYWORDS: Final[tuple[str, ...]] = (
    "trend", "tsmom", "momentum", "chandelier", "supertrend",
    "pocket_pivot", "vol_expansion",
    # "riskoff" — capital_macro_riskoff_catalyst (P3 promotion, 2026-07-16):
    # a macro flight-to-quality EVENT is a TREND move, not a bounded
    # revert-to-mean (matches its own metadata's TREND exit-bucket rationale
    # — see the strategy module's "NO reversion/range/mean_reversion
    # substring" comment). gold_riskoff_trend_amplify already matches via
    # "trend" — this is additive, no reclassification there.
    "riskoff",
)
_REVERSION_KEYWORDS: Final[tuple[str, ...]] = (
    "reversion", "pullback", "fade", "flush", "meanrev", "mean_reversion",
)


def edge_type_for(*, strategy_id: str, correlation_group_id: str) -> EdgeType:
    """Classify a strategy's edge type from its id / correlation_group_id.

    Unknown (no keyword matches) degrades to neutral regime-fit downstream —
    never a crash, never a guess dressed up as a claim.
    """
    haystack = f"{strategy_id} {correlation_group_id}".lower()
    if any(kw in haystack for kw in _BREAKOUT_KEYWORDS):
        return "breakout"
    if any(kw in haystack for kw in _TREND_KEYWORDS):
        return "trend"
    if any(kw in haystack for kw in _REVERSION_KEYWORDS):
        return "reversion"
    return "unknown"


# Pre-existing metadata naming splits, NOT introduced here — each strategy
# declares asset_class from its own vocabulary while universe.asset_class
# (polaris.core.universe._capital / .discovery) persists a DIFFERENT string
# for the SAME instrument family:
#   * index_52w_high_momentum / index_dual_momentum_rotation declare "index"
#     (singular) vs the plural "indices" _capital.py always persists for the
#     Capital uk100/us100/etc family.
#   * ema_crossover / rsi_bb_pullback / spot_donchian / supertrend /
#     weekend_funding_capitulation_maker / weekend_thin_book_flush_maker /
#     tsmom / okx_funding_carry_persist / volume_burst declare "spot" vs the
#     "crypto" polaris.core.universe.discovery persists for OKX SPOT tickers
#     — mirrors _production_tick.py's keep_on_bar_path, which already treats
#     ("forex", "crypto", "spot") as equivalent for OKX/Capital-FX routing.
#   * fx_breakout_basket / cfd_fx_range_fade_short / fx_range_fade declare
#     "fx" vs the "forex" _capital.py persists for Capital FX tickers.
# Normalizing only these confirmed synonyms (not a broader asset_class rename
# — out of this task's scope, surgical-changes) keeps those strategies from
# reading as a false mismatch against the tickers they actually trade.
_ASSET_CLASS_ALIASES: Final[dict[str, str]] = {
    "index": "indices",
    "spot": "crypto",
    "fx": "forex",
}


def _normalize_asset_class(value: str) -> str:
    v = value.strip().lower()
    return _ASSET_CLASS_ALIASES.get(v, v)


def asset_class_match(strategy_asset_class: str, ticker_asset_class: str) -> float:
    """+1.0 exact match, -1.0 mismatch, 0.0 when either side is unknown/empty.

    Targets the audited P2 bug directly (a commodity-declared strategy
    dispatching on an fx-classified ticker) — see the blueprint's problem
    statement.
    """
    if not strategy_asset_class or not ticker_asset_class:
        return 0.0
    return (
        1.0
        if _normalize_asset_class(strategy_asset_class)
        == _normalize_asset_class(ticker_asset_class)
        else -1.0
    )
