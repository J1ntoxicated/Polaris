"""Layer 5 — session_mult learner.

Spec source: vault/30_components/layer-5-learner-network.md (Q1, Q6).

Tunes ``session × strategy`` size multiplier from rolling WR. Hourly recalibration
only (session edge is slow-moving).

Key format: ``"<strategy_id>:<session>"`` (vault Q1 example).

Update logic (per close):
  - n_eff += 1
  - wins_eff += won?1:0
  - pnl_r_sum_eff += trade.pnl_r

Commit value (hourly) — same expectancy-aware ladder as regime_mult (D2):
  - n_eff < 20      → unchanged (sparse fallback)
  - WR ≥ 0.55 AND expectancy > 0 → ``value + 0.1`` (deserved promotion)
  - WR ≤ 0.40       → ``value - 0.1`` (WR-driven, unchanged)
  - else            → unchanged
A high-WR / negative-expectancy bucket is withheld from promotion (it does not
get full size) — this is a SCORE change, never a defensive cut (flow_not_block).
"""

from __future__ import annotations

from polaris.core.learners._primitives import expectancy_aware_value
from polaris.core.learners.base import (
    NEUTRAL_MULT,
    BaseLearner,
    ClosedTrade,
)


class SessionMultLearner(BaseLearner):
    learner_id = "session_mult"

    def key_for(self, trade: ClosedTrade) -> str:
        return f"{trade.strategy_id}:{trade.session}"

    def _key_for_lookup(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str,
    ) -> str:
        return f"{strategy_id}:{session}"

    def observe(
        self,
        prior: dict[str, float],
        trade: ClosedTrade,
    ) -> dict[str, float]:
        n_eff = prior.get("n_eff", 0.0) + 1.0
        wins_eff = prior.get("wins_eff", 0.0) + (1.0 if trade.won else 0.0)
        pnl_sum = prior.get("pnl_r_sum_eff", 0.0) + trade.pnl_r
        return {
            "value": prior.get("value", NEUTRAL_MULT),
            "n_eff": n_eff,
            "wins_eff": wins_eff,
            "pnl_r_sum_eff": pnl_sum,
            "pending_delta": prior.get("pending_delta", 0.0),
        }

    def compute_value_from_stats(self, stats: dict[str, float]) -> float:
        # WR + expectancy ladder (D2): a high-WR but negative-expectancy session
        # bucket is NOT promoted; demotion stays WR-driven. flow_not_block.
        return expectancy_aware_value(stats)


__all__ = ["SessionMultLearner"]
