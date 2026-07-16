"""Delegation-gate fit-score — pure, deterministic strategy<->ticker ranking.

Design SSOT: vault/50_research/delegation-gate-blueprint.md. v1 is SHADOW
ONLY (see ``polaris.core.delegation.shadow``) — ``fit_score`` /
``rank_strategies`` never gate, size, or route a live signal; they compute a
comparison score logged alongside the ACTUAL (dumb asset_class-routed)
dispatch for later /debate-judged promotion (blueprint's "섀도우-후-승격"
2-stage plan).

Three terms, each independently falsifiable, weighted here as constants (not
fitted — a documented v1 starting point, revisitable once the shadow
accumulates enough rows for an empirical calibration):

  - history:      score_f_events realised (venue,strategy) x (symbol)
                   gross_bps — the "최강 신호" (strongest signal) per the
                   blueprint, so it gets the LARGEST weight.
  - asset_class:   the strategy's DECLARED asset_class vs the ticker's
                   UNIVERSE-OBSERVED asset_class — targets the audited
                   routing bug directly (a commodity-declared strategy
                   dispatching on an fx-classified ticker).
  - regime:        strategy edge_type (derived, see strategy_profile) vs the
                   ticker's live regime — the coarsest of the three (MVP
                   2-state trend/chop split), so the smallest weight.

vol/ATR percentile, liquidity (vol_24h/spread), and session fit are TICKER-
ONLY features (blueprint's feature-vector list) — for a FIXED ticker they add
the SAME constant to every candidate strategy's score, so by construction
they cannot change the ranking or the margin (a weighted sum's argmax/gap is
invariant to any term that does not vary across candidates). They are
captured on the logged shadow row for future cross-ticker analysis but are
deliberately NOT folded into this ranking formula — a documented v1 design
choice, not an omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from polaris.core.delegation.strategy_profile import (
    EdgeType,
    asset_class_match,
    edge_type_for,
)
from polaris.strategies.base import StrategyMetadata

__all__ = [
    "AMBIGUOUS_MARGIN_THRESHOLD",
    "HISTORY_NORM_BPS",
    "W_ASSET_CLASS",
    "W_HISTORY",
    "W_REGIME",
    "FitScoreEntry",
    "fit_score",
    "margin_of",
    "rank_strategies",
    "regime_fit_score",
]

W_HISTORY: Final[float] = 0.5
W_ASSET_CLASS: Final[float] = 0.3
W_REGIME: Final[float] = 0.2

HISTORY_NORM_BPS: Final[float] = 100.0
"""gross_bps normalizer: +-100bps of realised edge maps to +-1.0 (full
confidence). Loosely calibrated against real per-leg taker fees (~5-10bps
OKX/Capital) — revisit once enough closed lifecycles accumulate to fit this
empirically rather than assert it."""

AMBIGUOUS_MARGIN_THRESHOLD: Final[float] = 0.05
"""margin < this -> ambiguous=True (blueprint's v2 AI-escalation threshold;
v1 only FLAGS the row, never calls AI — no gpt-5-mini call in this package)."""


def regime_fit_score(edge_type: EdgeType, regime: str) -> float:
    """[-1, 1] regime alignment for a strategy's edge type vs the live regime.

    trend/breakout amplified in a trending regime, dampened in chop;
    reversion is the mirror. ``crisis`` / any unclassified regime, or
    ``unknown`` edge type, stay neutral (0.0) — no directional claim without
    evidence (mirrors ``regime_alignment_mult``'s NEUTRAL default)."""
    trending = regime in ("bull_trend", "bear_trend")
    if edge_type in ("trend", "breakout"):
        if trending:
            return 1.0
        if regime == "chop":
            return -1.0
        return 0.0
    if edge_type == "reversion":
        if regime == "chop":
            return 1.0
        if trending:
            return -1.0
        return 0.0
    return 0.0


def fit_score(
    *,
    strategy_id: str,
    correlation_group_id: str,
    strategy_asset_class: str,
    ticker_asset_class: str,
    regime: str,
    historical_edge_bps: float,
) -> float:
    """Deterministic weighted-sum fit score for ONE (strategy, ticker) pair.

    ``historical_edge_bps`` is 0.0 (neutral) for a strategy with no closed
    lifecycle yet on this ticker — cold data is never punished, mirrors the
    "cold cell always admits" philosophy elsewhere in this codebase.
    """
    edge_type = edge_type_for(
        strategy_id=strategy_id, correlation_group_id=correlation_group_id
    )
    history_term = max(-1.0, min(1.0, historical_edge_bps / HISTORY_NORM_BPS))
    asset_term = asset_class_match(strategy_asset_class, ticker_asset_class)
    regime_term = regime_fit_score(edge_type, regime)
    return (
        W_HISTORY * history_term
        + W_ASSET_CLASS * asset_term
        + W_REGIME * regime_term
    )


@dataclass(frozen=True, slots=True)
class FitScoreEntry:
    strategy_id: str
    score: float


def rank_strategies(
    candidates: list[StrategyMetadata],
    *,
    ticker_asset_class: str,
    regime: str,
    historical_edge_bps: dict[str, float],
) -> list[FitScoreEntry]:
    """Rank ``candidates`` (same-venue strategies) best-first.

    Ties keep ``candidates``' input order (Python's stable sort) — never a
    live decision, so no tie-break beyond determinism is needed. Empty input
    -> empty output (never raises)."""
    scored = [
        FitScoreEntry(
            strategy_id=meta.strategy_id,
            score=fit_score(
                strategy_id=meta.strategy_id,
                correlation_group_id=meta.correlation_group_id,
                strategy_asset_class=meta.asset_class,
                ticker_asset_class=ticker_asset_class,
                regime=regime,
                historical_edge_bps=historical_edge_bps.get(meta.strategy_id, 0.0),
            ),
        )
        for meta in candidates
    ]
    scored.sort(key=lambda e: e.score, reverse=True)
    return scored


def margin_of(ranked: list[FitScoreEntry]) -> float | None:
    """1st-2nd score gap. ``None`` when fewer than 2 candidates exist — there
    is no second place to diff against (not a measured 0.0 margin)."""
    if len(ranked) < 2:
        return None
    return ranked[0].score - ranked[1].score
