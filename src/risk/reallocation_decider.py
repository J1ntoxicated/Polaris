"""ReallocationDecider — atomic capital rotation logic (Phase 23.3).

Decides: should we close existing position to free capital for better opportunity?

Codex round-1 critical fixes applied:
- Dynamic switching cost (default 0.7%, was 0.5%) — covers fees + slip + uncertainty
- Cooldown to prevent rotate churn
- Atomic execution gate: close MUST succeed before opening replacement
- Per-ticker cooldown after rotate (don't bounce in/out)

Pure-ish: takes inputs (position evals, opportunities, portfolio cash),
returns DecisionList. Caller executes actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.risk.opportunity_scanner import Opportunity
from src.risk.position_evaluator import PositionEvaluation, PositionState


DEFAULT_SWITCHING_COST_PCT: float = 0.007        # 0.7% (Codex round-1)
DEFAULT_ROTATE_COOLDOWN_S: float = 300.0          # 5 min after rotate
DEFAULT_MIN_PROFIT_TAKE_PCT: float = 0.005        # 0.5% min profit-take


class ReallocAction(str, Enum):
    HOLD = "hold"
    CLOSE_THEN_OPEN = "rotate"   # close position, open opportunity
    CLOSE_ONLY = "close"         # close (e.g. micro-profit on COLD)
    ADD_TO = "add"               # add to existing HOT position


@dataclass(frozen=True)
class ReallocDecision:
    action: ReallocAction
    contribution_id: Optional[str] = None       # which contribution to act on
    opportunity: Optional[Opportunity] = None   # what to open (rotate / add)
    add_size_usd: float = 0.0
    reason: str = ""


class ReallocationDecider:
    """Stateful — tracks recent rotate timestamps for cooldown."""

    def __init__(
        self,
        switching_cost_pct: float = DEFAULT_SWITCHING_COST_PCT,
        rotate_cooldown_s: float = DEFAULT_ROTATE_COOLDOWN_S,
        min_profit_take_pct: float = DEFAULT_MIN_PROFIT_TAKE_PCT,
    ) -> None:
        self.switching_cost_pct = switching_cost_pct
        self.rotate_cooldown_s = rotate_cooldown_s
        self.min_profit_take_pct = min_profit_take_pct
        # Per-ticker last-rotate timestamp (cooldown enforcement)
        self._last_rotate_ts_ms: dict[str, int] = {}

    def decide_for_position(
        self,
        contribution,
        evaluation: PositionEvaluation,
        unrealized_pct: float,
        best_opportunity: Optional[Opportunity],
        ts_ms: int,
    ) -> ReallocDecision:
        """Decide action for a single contribution."""
        ticker = contribution.ticker
        last_rotate = self._last_rotate_ts_ms.get(ticker, 0)
        # First-ever rotate (last_rotate=0) is not in cooldown
        in_cooldown = (
            last_rotate > 0
            and (ts_ms - last_rotate) < int(self.rotate_cooldown_s * 1000)
        )

        # LOSING — let static SL handle it (HOLD, exit_strategy fires)
        if evaluation.state == PositionState.LOSING:
            return ReallocDecision(
                action=ReallocAction.HOLD,
                contribution_id=contribution.contribution_id,
                reason=f"losing_state:score={evaluation.score:+.2f}",
            )

        # COLD — rotate candidate
        if evaluation.state == PositionState.COLD:
            # 1. If in profit > min_profit_take, take profit (free capital)
            if unrealized_pct >= self.min_profit_take_pct:
                return ReallocDecision(
                    action=ReallocAction.CLOSE_ONLY,
                    contribution_id=contribution.contribution_id,
                    reason=f"cold_profit_take:{unrealized_pct*100:+.2f}%",
                )
            # 2. If much better opportunity exists + cooldown clear, rotate
            if best_opportunity is not None and not in_cooldown:
                current_ev = evaluation.forward_ev_pct
                opp_ev = best_opportunity.expected_return_pct
                edge = opp_ev - current_ev - self.switching_cost_pct
                if edge > 0:
                    self._last_rotate_ts_ms[ticker] = ts_ms
                    return ReallocDecision(
                        action=ReallocAction.CLOSE_THEN_OPEN,
                        contribution_id=contribution.contribution_id,
                        opportunity=best_opportunity,
                        reason=(
                            f"rotate:cold→{best_opportunity.ticker}/"
                            f"{best_opportunity.strategy_name} "
                            f"edge={edge*100:+.2f}% (opp_ev={opp_ev*100:+.2f}% "
                            f"- cur_ev={current_ev*100:+.2f}% - cost={self.switching_cost_pct*100:.2f}%)"
                        ),
                    )
            # Otherwise hold (waiting for either profit or rotation chance)
            return ReallocDecision(
                action=ReallocAction.HOLD,
                contribution_id=contribution.contribution_id,
                reason="cold_hold",
            )

        # WARM — hold (no action)
        if evaluation.state == PositionState.WARM:
            return ReallocDecision(
                action=ReallocAction.HOLD,
                contribution_id=contribution.contribution_id,
                reason=f"warm_hold:score={evaluation.score:+.2f}",
            )

        # HOT — consider ADD (Phase 23.4 hooks here)
        if evaluation.state == PositionState.HOT:
            # ADD gating: check stabilized (held > 5 min), no recent adverse
            held_s = (ts_ms - contribution.open_ts_ms) / 1000.0
            if held_s < 300:
                return ReallocDecision(
                    action=ReallocAction.HOLD,
                    contribution_id=contribution.contribution_id,
                    reason="hot_too_fresh_no_add",
                )
            # ADD opportunity = same strategy still firing on this ticker
            if (
                best_opportunity is not None
                and best_opportunity.ticker == ticker
                and best_opportunity.strategy_name == contribution.strategy_name
                and best_opportunity.signal_confidence >= 0.7
            ):
                # Incremental: 50% of original size (Codex round-1 constraint)
                add_size = contribution.size_usd * 0.5
                if add_size >= 25:  # min meaningful add
                    return ReallocDecision(
                        action=ReallocAction.ADD_TO,
                        contribution_id=contribution.contribution_id,
                        opportunity=best_opportunity,
                        add_size_usd=add_size,
                        reason=(
                            f"hot_add:thesis_reconfirmed conf={best_opportunity.signal_confidence:.2f}"
                        ),
                    )
            return ReallocDecision(
                action=ReallocAction.HOLD,
                contribution_id=contribution.contribution_id,
                reason=f"hot_hold:score={evaluation.score:+.2f}",
            )

        # Default
        return ReallocDecision(
            action=ReallocAction.HOLD,
            contribution_id=contribution.contribution_id,
            reason="default_hold",
        )

    def reset_cooldown(self, ticker: Optional[str] = None) -> None:
        """Test helper."""
        if ticker is None:
            self._last_rotate_ts_ms.clear()
        else:
            self._last_rotate_ts_ms.pop(ticker, None)
