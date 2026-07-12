"""Transition state machine — pts-classes (Performance-Tiered Strategy
classes), group C.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``evaluate_transition`` is a pure capital-ROUTING decision function — never a
block/reject filter. A strategy demoted to PROVE/BENCH/KILL keeps
signaling/learning/shadow-pricing regardless of its class
(aggressive_always_profit / no_block_filter_architecture). No sizing/
9-stack/-1.0R rail touch — this module only decides which
class/dwell/epoch/ladder_step a (venue, strategy_id) track occupies next;
capital allocation elsewhere reads the result. Storage (persisting the
result into ``strategy_class``) is group A's; score_F computation is group
B's — this module consumes pre-computed score_F samples and R-multiples,
it does not read the DB or call the classifier itself (same
zero-import-coupling shape as ``recover_classes.bootstrap_replay_strategy_class``).

Priority order every call evaluates (first match wins):
    1. KILL — ``kill_state == "KILLED"`` never auto-transitions (no
       automatic revival; only an external/manual action, out of this
       module's scope, can clear it).
    2. Tripwire — severe recent R-multiple streak; fires immediately,
       ignoring dwell/promotion-rate-limit/fill-gate... except the fill
       gate, which still applies (EXEC_STARVED means there is not enough
       execution evidence to justify ANY transition, tripwire included).
    3. Fill gate — below-threshold execution evidence blocks every
       remaining transition path (Schmitt/dwell/stagnation/ladder) with
       ``EXEC_STARVED``.
    4. Schmitt asymmetric hysteresis (EARN demotion only).
    5. PROVE stagnation (PROVE -> BENCH).
    6. PROVE -> EARN promotion (dwell + post-EARN-demotion cooldown +
       24h promotion rate limit).
    7. Reentry ladder (BENCH -> PROVE, pessimistic shadow_ring based).
    8. No trigger -> unchanged (``NO_TRANSITION``).

Interpretation notes (task spec shorthand -> concrete rule; no separate SSOT
doc existed for group C at build time, so these are documented here per the
project's ambiguous-threshold precedent, e.g. ``recover_classes.py``'s
``_BOOTSTRAP_BENCH_THRESHOLD`` docstring):

- "Schmitt 비대칭(EARN->PROVE 0.4W<-1 / ->BENCH 0.4W<-4 v W<-3)": evaluated
  against ``intent_scores`` (the LIVE score_F ring — Schmitt hysteresis
  governs an actively-trading EARN track, never the shadow ring). ``0.4W``
  is the most recent ``ceil(0.4*window_w)`` closes' mean score; ``W`` is the
  full ``window_w`` closes' mean. EARN->BENCH fires if EITHER the harsh
  partial-window mean clears -4 OR the full-window mean clears -3; else
  EARN->PROVE fires if the partial-window mean clears -1. BENCH is checked
  first (harsher outcome wins when both would trigger).
- "dwell(PROVE->EARN 10, EARN 강등 후 5)": two independent locks, both must
  clear before a PROVE->EARN promotion — ``dwell`` (closes since entering
  the current class) >= 10, AND ``dwell_since_earn_demotion`` (closes since
  the most recent EARN->PROVE/BENCH demotion, if any) >= 5.
- "tripwire(intraday/swing last8<-4, 1D+ last5<-3, dwell·cap 무시 즉시)":
  sum of the last 8 (intraday/swing) or last 5 (1d_plus) realized
  R-multiples crossing the threshold demotes straight to BENCH, bypassing
  dwell and the promotion-rate-limit (not the fill gate — see priority
  order above).
- "PROVE 정체(1.0W closes에 score_F<=+2->BENCH)": full-window
  (``window_w``) mean of ``intent_scores`` <= +2 while in PROVE ->
  demote to BENCH. Requires a full window's sample (fewer closes yet =>
  no verdict, stays PROVE).
- "승격 1회/24h·강등 무제한": any promotion (PROVE->EARN, ladder step-up,
  ladder exit-to-PROVE) is blocked if ``now_ts - last_promotion_ts < 24h``;
  demotions are never rate-limited.
- "재진입 사다리(step0 1.0W&+3/step1 2.0W&+6/step2 3.0W&+9 cap, decay
  1.0W&+3, pessimistic shadow 기반)": BENCH-only, driven by ``shadow_ring``
  (populated upstream with pessimistic shadow-pricing per score_F group).
  Each step requires its OWN sample size (``1.0*W``/``2.0*W``/``3.0*W``
  most recent shadow closes) with mean score_F at/above the paired
  threshold to step up; step2 clearing its threshold exits the ladder
  directly into PROVE (ladder_step resets to 0). Decay: at ANY step > 0,
  if the most recent ``1.0*W`` shadow closes' mean falls below +3, drop
  one ladder step (floor 0; already-0 holds, no demotion below BENCH from
  ladder decay alone).
- "epoch(승격·median notional>1.5x·R alloc>1.5x=new epoch, ... 강등 판단은
  항상 live 신규 100%)": ``epoch_id`` increments on ANY promotion, or when
  the caller-supplied ``median_notional_ratio``/``r_alloc_ratio`` exceeds
  1.5x (a capital-routing regime change even without a class transition).
  Never on demotion. The "allocation blend 50/50 until 8 closes" / "강등
  판단은 always live 100%" halves are the ALLOCATION layer's concern
  (consumes ``epoch_id`` + close-count elsewhere) — this module only emits
  the epoch counter, it does not blend allocations itself.
- "fill gate(n<10 off / 10-49 fills>=max(3,ceil(0.2n)) / >=50 last-50
  rate>=0.20, 미달=전이 없음+EXEC_STARVED)": n<10 total fills is too early
  to judge execution health at all, so the gate does not block (every
  other rule's own sample-size requirements are the effective floor at
  that regime). 10-49: ``n_fills_recent >= max(3, ceil(0.2 *
  n_signals_recent))``. >=50: ``last_50_fill_rate >= 0.20``. Any tier
  failing blocks EVERY transition this call (KILL check still runs first,
  since it is not a "transition" being evaluated so much as a permanent
  floor).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal

__all__ = [
    "TransitionInput",
    "TransitionResult",
    "TransitionThresholds",
    "evaluate_transition",
]

_VALID_CLASSES = frozenset({"EARN", "PROVE", "BENCH", "KILL"})

# Schmitt asymmetric hysteresis (EARN demotion) — see module docstring.
_SCHMITT_PARTIAL_WINDOW_FRAC = 0.4
_SCHMITT_PROVE_THRESHOLD = -1.0
_SCHMITT_BENCH_PARTIAL_THRESHOLD = -4.0
_SCHMITT_BENCH_FULL_THRESHOLD = -3.0

# Dwell locks.
_DWELL_PROVE_TO_EARN = 10
_DWELL_POST_EARN_DEMOTION = 5

# Tripwire — evaluated against ``recent_r_multiples`` (realized R, NOT
# score_F/gross_bps), so the fee_split_flip_r2_2026-07-12 threshold-remap
# (see ``TransitionThresholds`` below) never touches these: a different scale
# entirely, unaffected by which judged score axis feeds intent_scores.
_TRIPWIRE_INTRADAY_N = 8
_TRIPWIRE_INTRADAY_SUM_THRESHOLD = -4.0
_TRIPWIRE_1D_PLUS_N = 5
_TRIPWIRE_1D_PLUS_SUM_THRESHOLD = -3.0

# PROVE stagnation.
_PROVE_STAGNATION_THRESHOLD = 2.0

# Promotion rate limit.
_PROMOTION_RATE_LIMIT_SECONDS = 86_400

# Reentry ladder — (window_multiple, score_threshold) per step-up transition,
# indexed by current ladder_step (0, 1, 2). Step 2 clearing exits to PROVE.
_LADDER_STEP_UP = {
    0: (1.0, 3.0),
    1: (2.0, 6.0),
    2: (3.0, 9.0),
}
_LADDER_DECAY_WINDOW_MULT = 1.0
_LADDER_DECAY_THRESHOLD = 3.0
_LADDER_MAX_STEP = 2

# Epoch bump on capital-routing regime change even without a class flip.
_EPOCH_BUMP_RATIO = 1.5

Timeframe = Literal["intraday", "swing", "1d_plus"]


@dataclass(slots=True, frozen=True)
class TransitionThresholds:
    """The 7 score-scale thresholds ``evaluate_transition`` checks
    ``intent_scores``/``shadow_scores`` against (Schmitt hysteresis, PROVE
    stagnation, reentry-ladder step-up + decay) — everything EXCEPT the
    tripwire (a separate R-multiple-scale check, never remapped).

    Defaults are the pre-flip literal constants above, so a caller that never
    supplies ``thresholds=`` (every existing call site/test) reproduces the
    byte-identical legacy behavior. fee_split_flip_r2_2026-07-12 item 2:
    once the judged score axis flips (score_f.py, gross_bps by default),
    these 7 values must move onto the SAME new scale via a percentile-
    preserving remap — ``polaris.core.classes.remap_table.resolve_thresholds``
    is the (DB-backed, fail-open) resolver that builds this dataclass for a
    live (venue, strategy_id) track; this module stays pure (no I/O, no
    knowledge of score_f_events) and only consumes whatever the caller hands
    it.
    """

    schmitt_bench_partial: float = _SCHMITT_BENCH_PARTIAL_THRESHOLD
    schmitt_bench_full: float = _SCHMITT_BENCH_FULL_THRESHOLD
    schmitt_prove: float = _SCHMITT_PROVE_THRESHOLD
    prove_stagnation: float = _PROVE_STAGNATION_THRESHOLD
    ladder_step0: float = _LADDER_STEP_UP[0][1]
    ladder_step1: float = _LADDER_STEP_UP[1][1]
    ladder_step2: float = _LADDER_STEP_UP[2][1]
    ladder_decay: float = _LADDER_DECAY_THRESHOLD


@dataclass(slots=True)
class TransitionInput:
    """Everything ``evaluate_transition`` needs for one (venue, strategy_id)
    track's decision. Mirrors the relevant subset of ``StrategyClassRow``
    (group A storage) plus freshly-computed signal windows the caller
    assembles from ``score_f_events`` / fill history — this module performs
    no DB I/O itself.
    """

    strategy_class: str
    kill_state: str
    window_w: int
    dwell: int
    ladder_step: int
    epoch_id: int
    last_transition_ts: int
    now_ts: int
    timeframe_bucket: Timeframe
    shadow_scores: list[float]
    intent_scores: list[float]
    recent_r_multiples: list[float]
    n_fills_total: int
    n_signals_recent: int
    n_fills_recent: int
    last_50_fill_rate: float
    last_earn_demotion_ts: int | None = None
    last_promotion_ts: int | None = None
    dwell_since_earn_demotion: int = 0
    median_notional_ratio: float = 1.0
    r_alloc_ratio: float = 1.0
    thresholds: TransitionThresholds = field(default_factory=TransitionThresholds)

    def __post_init__(self) -> None:
        if self.strategy_class not in _VALID_CLASSES:
            raise ValueError(f"unknown strategy_class: {self.strategy_class!r}")


@dataclass(slots=True)
class TransitionResult:
    """Next-state decision. Group A's storage layer persists this verbatim;
    this module never writes to the DB.
    """

    strategy_class: str
    kill_state: str
    dwell: int
    ladder_step: int
    epoch_id: int
    reason: str


def evaluate_transition(inp: TransitionInput) -> TransitionResult:
    """Evaluate one (venue, strategy_id) track's next state. Pure function,
    no I/O — see module docstring for the full priority order and every
    threshold's interpretation.
    """
    if inp.kill_state == "KILLED":
        return _unchanged(inp, reason="KILL_NO_AUTO_REVIVE")

    tripwire_hit = _check_tripwire(inp)

    if not _fill_gate_ok(inp):
        return _unchanged(inp, reason="EXEC_STARVED")

    if tripwire_hit:
        return _demote(inp, to_class="BENCH", reason="TRIPWIRE")

    if inp.strategy_class == "EARN":
        schmitt = _check_schmitt(inp)
        if schmitt is not None:
            return _demote(inp, to_class=schmitt, reason=f"SCHMITT_EARN_TO_{schmitt}")
        return _unchanged(inp, reason="NO_TRANSITION")

    if inp.strategy_class == "PROVE":
        if _check_prove_stagnation(inp):
            return _demote(inp, to_class="BENCH", reason="PROVE_STAGNATION")
        return _check_prove_to_earn(inp)

    if inp.strategy_class == "BENCH":
        return _check_reentry_ladder(inp)

    return _unchanged(inp, reason="NO_TRANSITION")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unchanged(inp: TransitionInput, *, reason: str) -> TransitionResult:
    return TransitionResult(
        strategy_class=inp.strategy_class,
        kill_state=inp.kill_state,
        dwell=inp.dwell,
        ladder_step=inp.ladder_step,
        epoch_id=inp.epoch_id,
        reason=reason,
    )


def _demote(inp: TransitionInput, *, to_class: str, reason: str) -> TransitionResult:
    return TransitionResult(
        strategy_class=to_class,
        kill_state=inp.kill_state,
        dwell=0,
        ladder_step=0,
        epoch_id=inp.epoch_id,
        reason=reason,
    )


def _promote(inp: TransitionInput, *, to_class: str, reason: str, ladder_step: int = 0) -> TransitionResult:
    return TransitionResult(
        strategy_class=to_class,
        kill_state=inp.kill_state,
        dwell=0,
        ladder_step=ladder_step,
        epoch_id=_next_epoch(inp, promoted=True),
        reason=reason,
    )


def _next_epoch(inp: TransitionInput, *, promoted: bool) -> int:
    if promoted:
        return inp.epoch_id + 1
    regime_change = (
        inp.median_notional_ratio > _EPOCH_BUMP_RATIO or inp.r_alloc_ratio > _EPOCH_BUMP_RATIO
    )
    return inp.epoch_id + 1 if regime_change else inp.epoch_id


def _mean_tail(values: list[float], n: int) -> float | None:
    """Mean of the most recent ``n`` entries in ``values`` (list is assumed
    oldest-first, matching the ring-buffer append order). ``None`` if fewer
    than ``n`` samples exist yet — callers must treat that as "no verdict",
    never as a zero/pass-through score.
    """
    if n <= 0 or len(values) < n:
        return None
    tail = values[-n:]
    return sum(tail) / n


def _fill_gate_ok(inp: TransitionInput) -> bool:
    if inp.n_fills_total < 10:
        return True
    if inp.n_fills_total < 50:
        required = max(3, math.ceil(0.2 * inp.n_signals_recent))
        return inp.n_fills_recent >= required
    return inp.last_50_fill_rate >= 0.20


def _check_tripwire(inp: TransitionInput) -> bool:
    if inp.timeframe_bucket in ("intraday", "swing"):
        window_n, threshold = _TRIPWIRE_INTRADAY_N, _TRIPWIRE_INTRADAY_SUM_THRESHOLD
    else:
        window_n, threshold = _TRIPWIRE_1D_PLUS_N, _TRIPWIRE_1D_PLUS_SUM_THRESHOLD
    if len(inp.recent_r_multiples) < window_n:
        return False
    tail_sum = sum(inp.recent_r_multiples[-window_n:])
    return tail_sum < threshold


def _check_schmitt(inp: TransitionInput) -> str | None:
    t = inp.thresholds
    partial_n = math.ceil(_SCHMITT_PARTIAL_WINDOW_FRAC * inp.window_w)
    partial_mean = _mean_tail(inp.intent_scores, partial_n)
    full_mean = _mean_tail(inp.intent_scores, inp.window_w)

    bench_hit = (partial_mean is not None and partial_mean < t.schmitt_bench_partial) or (
        full_mean is not None and full_mean < t.schmitt_bench_full
    )
    if bench_hit:
        return "BENCH"
    if partial_mean is not None and partial_mean < t.schmitt_prove:
        return "PROVE"
    return None


def _check_prove_stagnation(inp: TransitionInput) -> bool:
    full_mean = _mean_tail(inp.intent_scores, inp.window_w)
    if full_mean is None:
        return False
    return full_mean <= inp.thresholds.prove_stagnation


def _check_prove_to_earn(inp: TransitionInput) -> TransitionResult:
    if inp.dwell < _DWELL_PROVE_TO_EARN:
        return _unchanged(inp, reason="DWELL_LOCKED")
    if inp.last_earn_demotion_ts is not None and inp.dwell_since_earn_demotion < _DWELL_POST_EARN_DEMOTION:
        return _unchanged(inp, reason="DWELL_LOCKED")
    if _promotion_rate_limited(inp):
        return _unchanged(inp, reason="PROMOTION_RATE_LIMITED")
    return _promote(inp, to_class="EARN", reason="PROVE_TO_EARN")


def _promotion_rate_limited(inp: TransitionInput) -> bool:
    if inp.last_promotion_ts is None:
        return False
    return (inp.now_ts - inp.last_promotion_ts) < _PROMOTION_RATE_LIMIT_SECONDS


def _check_reentry_ladder(inp: TransitionInput) -> TransitionResult:
    """Step-up is checked (and, if satisfied, wins) before decay: a step's own
    (larger) window clearing its threshold is the more-authoritative, less
    noisy verdict, so a step-up on strong full-window evidence is not
    overridden by decay's smaller, noisier recent-window dip in the same
    call. Only when step-up's own window fails to clear does decay get a
    chance to fire.
    """
    step = inp.ladder_step
    t = inp.thresholds
    ladder_score_thresholds = (t.ladder_step0, t.ladder_step1, t.ladder_step2)

    if step in _LADDER_STEP_UP:
        window_mult, _legacy_threshold = _LADDER_STEP_UP[step]
        score_threshold = ladder_score_thresholds[step]
        window_n = math.ceil(window_mult * inp.window_w)
        mean_score = _mean_tail(inp.shadow_scores, window_n)
        if mean_score is not None and mean_score >= score_threshold:
            if _promotion_rate_limited(inp):
                return _unchanged(inp, reason="PROMOTION_RATE_LIMITED")
            if step == _LADDER_MAX_STEP:
                return _promote(inp, to_class="PROVE", reason="LADDER_EXIT_TO_PROVE")
            return TransitionResult(
                strategy_class=inp.strategy_class,
                kill_state=inp.kill_state,
                dwell=inp.dwell,
                ladder_step=step + 1,
                epoch_id=_next_epoch(inp, promoted=True),
                reason="LADDER_STEP_UP",
            )

    if step > 0:
        decay_n = math.ceil(_LADDER_DECAY_WINDOW_MULT * inp.window_w)
        decay_mean = _mean_tail(inp.shadow_scores, decay_n)
        if decay_mean is not None and decay_mean < t.ladder_decay:
            return replace(
                _unchanged(inp, reason="LADDER_DECAY"),
                ladder_step=step - 1,
            )

    return _unchanged(inp, reason="NO_TRANSITION")
