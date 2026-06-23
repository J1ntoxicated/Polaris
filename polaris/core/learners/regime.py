"""Layer 5 — regime_mult learner + adaptive_learner_attack triple block source.

Spec source: vault/30_components/layer-5-learner-network.md (Q1, Q4).
ADR-007: WR ≥ 55% +0.1 / ≤ 40% -0.1 per regime.

Key format: ``"<strategy_id>:<regime>"``.

This learner is also the SSOT for the triple block emission (see ``BaseLearner._maybe_emit_triple_block``):
the (ticker × strategy × regime) row that is updated alongside the regime key
drives the SQLite block registry. 1h auto-unblock + ``size_mult=0.3``.
"""

from __future__ import annotations

from polaris.core.learners._primitives import expectancy_aware_value
from polaris.core.learners.base import (
    NEUTRAL_MULT,
    BaseLearner,
    ClosedTrade,
)


class RegimeMultLearner(BaseLearner):
    learner_id = "regime_mult"

    def key_for(self, trade: ClosedTrade) -> str:
        return f"{trade.strategy_id}:{trade.regime}"

    def _key_for_lookup(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str,
    ) -> str:
        return f"{strategy_id}:{regime}"

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
        # WR + expectancy ladder (D2): a high-WR but negative-expectancy regime
        # bucket is NOT promoted; demotion stays WR-driven. flow_not_block.
        return expectancy_aware_value(stats)


__all__ = ["RegimeMultLearner"]
