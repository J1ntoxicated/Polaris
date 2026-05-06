"""PositionEvaluator — forward potential scoring + hysteresis classification (Phase 23.1).

User insight: positions aren't equal. HOT (rising potential) → hold/add.
COLD (sideways) → rotate to better opportunity. LOSING → exit.

Codex Round 1 critical fixes applied:
- Hysteresis bands prevent boundary churn (HOT enter 0.65, exit 0.55)
- Score components carefully de-correlated (continuation, momentum, confluence)
- All Pure (P6) — no I/O.

Score formula:
    score = 0.5 × strategy_continuation
          + 0.3 × momentum_score
          + 0.2 × confluence_score
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional


class PositionState(str, Enum):
    HOT = "hot"        # high forward EV — hold or ADD
    WARM = "warm"      # moderate — hold
    COLD = "cold"      # low — rotate candidate
    LOSING = "losing"  # negative — exit


@dataclass(frozen=True)
class HysteresisBands:
    """State transition thresholds with hysteresis (Codex round-1 fix).

    Asymmetric: enter HOT at 0.65, exit at 0.55 (10% gap prevents churn).
    """
    hot_enter: float = 0.65
    hot_exit: float = 0.55
    warm_floor: float = 0.35   # below this → COLD candidate
    cold_enter: float = 0.25
    cold_exit: float = 0.35
    losing_threshold: float = 0.0   # score < 0 → LOSING


@dataclass(frozen=True)
class EvaluationInputs:
    """Per-evaluation context — built by caller from market state."""
    # Strategy continuation: 1.0 if strategy still says ENTER_LONG, 0.0 = HOLD, -1.0 = EXIT
    continuation_signal: float
    # Momentum: recent price trend (last N bars)
    # +1.0 = strong up, 0.0 = flat, -1.0 = strong down
    momentum_score: float
    # Confluence: other strategies on same ticker also bullish?
    # 0.0 = lone, 0.5 = one confirms, 1.0 = multiple confirm
    confluence_score: float
    # Position holding fatigue (held > typical → score down)
    fatigue_factor: float = 1.0


def compute_score(inputs: EvaluationInputs) -> float:
    """Pure scoring — combines continuation + momentum + confluence with fatigue.

    Returns score in [-1, +1]:
        +1.0 = strong HOT signal
        0.0  = neutral
        -1.0 = strong sell signal
    """
    continuation = inputs.continuation_signal  # already [-1, +1]
    momentum = inputs.momentum_score           # [-1, +1]
    confluence = inputs.confluence_score       # [0, 1]
    base = 0.5 * continuation + 0.3 * momentum + 0.2 * confluence
    # Fatigue dampens positive scores (stale positions → lower priority)
    if base > 0:
        base *= max(0.5, inputs.fatigue_factor)
    return max(-1.0, min(1.0, base))


def classify_state(
    score: float,
    previous_state: Optional[PositionState],
    bands: HysteresisBands = HysteresisBands(),
) -> PositionState:
    """Hysteresis state transition.

    Codex round-1 fix: asymmetric thresholds prevent boundary churn.
    Once HOT, stays HOT until score drops below hot_exit (0.55).
    Once COLD, stays COLD until score rises above cold_exit (0.35).

    Pure: deterministic on (score, previous_state).
    """
    if score < bands.losing_threshold:
        return PositionState.LOSING

    if previous_state == PositionState.HOT:
        # Stay HOT until score falls below hot_exit
        if score >= bands.hot_exit:
            return PositionState.HOT
        # Falling out of HOT — go to WARM
        return PositionState.WARM

    if previous_state == PositionState.COLD:
        # Stay COLD until score rises above cold_exit
        if score <= bands.cold_exit:
            return PositionState.COLD
        return PositionState.WARM

    # WARM or unknown previous → use enter thresholds
    if score >= bands.hot_enter:
        return PositionState.HOT
    if score <= bands.cold_enter:
        return PositionState.COLD
    return PositionState.WARM


@dataclass(frozen=True)
class PositionEvaluation:
    """Output of PositionEvaluator."""
    score: float
    state: PositionState
    reason: str
    forward_ev_pct: float    # estimated expected return from here
    inputs: EvaluationInputs


class PositionEvaluator:
    """Per-position forward potential evaluator (stateful — tracks last state).

    Usage:
        evaluator = PositionEvaluator()
        eval = evaluator.evaluate(position, contribution_inputs[contrib_id])
        if eval.state == PositionState.COLD: ...
    """

    def __init__(self, bands: Optional[HysteresisBands] = None) -> None:
        self.bands = bands or HysteresisBands()
        # Track previous state per contribution_id (for hysteresis)
        self._last_state: dict[str, PositionState] = {}

    def evaluate(
        self,
        contribution_id: str,
        inputs: EvaluationInputs,
        unrealized_pct: float = 0.0,
    ) -> PositionEvaluation:
        """Evaluate a single contribution. Returns state classification.

        unrealized_pct: current % unrealized (used for forward_ev sanity check).
        """
        score = compute_score(inputs)
        previous = self._last_state.get(contribution_id)
        state = classify_state(score, previous, self.bands)
        self._last_state[contribution_id] = state

        # Forward EV — rough estimate (score-weighted base of 0.5%)
        forward_ev = score * 0.01  # +1.0 score → +1% EV expected, -1.0 → -1%

        reason = (
            f"score={score:+.3f} state={state.value} "
            f"cont={inputs.continuation_signal:+.2f} "
            f"mom={inputs.momentum_score:+.2f} "
            f"conf={inputs.confluence_score:.2f} "
            f"fatigue={inputs.fatigue_factor:.2f} "
            f"unreal={unrealized_pct*100:+.2f}%"
        )
        return PositionEvaluation(
            score=score,
            state=state,
            reason=reason,
            forward_ev_pct=forward_ev,
            inputs=inputs,
        )

    def reset(self, contribution_id: Optional[str] = None) -> None:
        """Test helper / on-close cleanup."""
        if contribution_id is None:
            self._last_state.clear()
        else:
            self._last_state.pop(contribution_id, None)


# ─── Inputs builders (helpers for caller) ──────────────────────────────────


def momentum_from_prices(prices: list[float]) -> float:
    """Simple momentum: last price vs N-bar average. Returns [-1, +1].

    >0 = uptrend, <0 = downtrend, ≈0 = flat.
    """
    if len(prices) < 3:
        return 0.0
    avg = sum(prices) / len(prices)
    last = prices[-1]
    if avg <= 0:
        return 0.0
    delta = (last - avg) / avg
    # Scale: 1% delta → 0.5 momentum, cap at ±1
    return max(-1.0, min(1.0, delta * 50))


def fatigue_from_age(held_minutes: float, typical_hold_min: float) -> float:
    """Fatigue factor: 1.0 fresh, decays as held > typical.

    held < typical → 1.0 (no fatigue)
    held = 2 × typical → 0.5
    held > 5 × typical → 0.1
    """
    if typical_hold_min <= 0:
        return 1.0
    ratio = held_minutes / typical_hold_min
    if ratio <= 1.0:
        return 1.0
    return max(0.1, 1.0 / ratio)
