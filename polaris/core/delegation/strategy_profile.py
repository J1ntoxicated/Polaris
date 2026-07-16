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
# misreading the entry-trigger word "pullback" as mean-reversion. Verified
# against every id in polaris.strategies.STRATEGY_REGISTRY as of 2026-07-16
# (tests/test_delegation_strategy_profile.py iterates the live registry so a
# newly-added strategy is asserted non-"unknown", not silently drifting).
_BREAKOUT_KEYWORDS: Final[tuple[str, ...]] = (
    "breakout", "donchian", "opening_range", "orb",
)
_TREND_KEYWORDS: Final[tuple[str, ...]] = (
    "trend", "tsmom", "momentum", "chandelier", "supertrend",
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


# index/indices is a pre-existing metadata naming split, NOT introduced here:
# index_52w_high_momentum / index_dual_momentum_rotation declare asset_class
# "index" (singular) while polaris.core.universe._capital always persists the
# plural "indices" for the SAME Capital instrument family (uk100/us100/etc).
# Normalizing only this ONE confirmed synonym (not a broader asset_class
# rename — out of this task's scope, surgical-changes) keeps those two real
# index strategies from reading as a false mismatch against their own tickers.
_ASSET_CLASS_ALIASES: Final[dict[str, str]] = {"index": "indices"}


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
