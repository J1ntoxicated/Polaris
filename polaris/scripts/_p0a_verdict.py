"""P0a STEP 5 — KILL-spike VERDICT model + pure classifier.

OFFLINE ONLY. Touches NO live trading / sizing / T4 chain / gate thresholds.
The :class:`SpikeVerdict` dataclass + the pure :func:`_classify` function +
the verdict-label constants, extracted from
:mod:`polaris.scripts.run_p0a_spike` (pure move — behavior-0). Every public name
is re-imported / re-exported from ``run_p0a_spike`` so the CLI and tests keep
working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "K_MIN_EVALUABLE",
    "SpikeVerdict",
]

# ~0 is "zero passes". For an integer pass count this is exact; the equivalent
# rate threshold (pass_rate < 1/trials_searched) is reported for transparency.
_ZERO = 0

# FIX 8: FEATURE_BOTTLENECK requires a minimum number of evaluable, non-degenerate
# (>= 2 OOS trade) variants — otherwise the 0-pass measures thin search, not a
# dead feature space. Below this, fall back to DATA_BOUNDED.
K_MIN_EVALUABLE: int = 3


@dataclass(frozen=True, slots=True)
class SpikeVerdict:
    """The structured KILL-spike verdict + the exact numbers behind it.

    ``label`` is one of VALIDATION_STARVED / DATA_BOUNDED / FEATURE_BOTTLENECK /
    OVERFIT_NO_GENERALIZATION / SEARCH_SPACE_EXISTS. The counts are the raw
    evidence so a reader never sees a label without its support.

    ``positive_control_passed`` is the KEYSTONE (FIX 1): whether the gate can
    certify a STRONG synthetic edge at ``positive_control_n`` (the MAX OOS
    n_trades across evaluable variants — the run's most generous held-out N).
    ``min_passable_n`` is the smallest N at which that strong edge clears the
    gate. ``trials_searched`` counts ONLY real entry-varying distinct configs
    (a 0-knob strategy's default contributes 0 search breadth)."""

    label: str
    run_id: str
    n_variants: int
    trials_searched: int
    data_bounded_count: int
    is_pass_count: int
    oos_pass_count: int
    is_pass_rate: float
    oos_pass_rate: float
    zero_threshold: float  # 1/trials_searched (the explicit "~0" cutoff)
    positive_control_passed: bool = False
    positive_control_n: int = 0
    min_passable_n: int = 0
    evaluable_count: int = 0
    per_strategy: tuple[tuple[str, int, int, int], ...] = field(default_factory=tuple)
    rationale: str = ""


def _classify(
    *,
    n_variants: int,
    data_bounded_count: int,
    is_pass_count: int,
    oos_pass_count: int,
    positive_control_passed: bool,
    positive_control_n: int,
    min_passable_n: int,
    evaluable_nondegenerate: int,
) -> tuple[str, str]:
    """Map the raw counts to a verdict label + a one-line rationale (pure).

    Precedence (FIX 1 keystone + FIX 8):
      1. NO positive control -> ``VALIDATION_STARVED`` (the gate cannot certify
         even a STRONG edge at the available held-out N; pass counts are
         meaningless — the instrument is validation-starved, not feature-dead).
         This OUTRANKS the pass-count ladder, regardless of IS/OOS counts.
      2. nothing evaluable / mostly data-bounded -> ``DATA_BOUNDED``.
      3. positive control PASSES + IS pass-rate 0 + >= K_min non-degenerate
         evaluable variants -> ``FEATURE_BOTTLENECK`` (provable: the gate CAN
         pass a strong edge at this N, yet 0 real configs clear IS).
      4. positive control PASSES + IS>0 + OOS 0 -> ``OVERFIT_NO_GENERALIZATION``.
      5. positive control PASSES + IS>0 + OOS>0 -> ``SEARCH_SPACE_EXISTS``.
    ``~0`` means a count of exactly 0 (explicit)."""
    evaluable = n_variants - data_bounded_count
    if n_variants == 0:
        return ("DATA_BOUNDED", "no variants enumerated (empty grid)")
    # 1. NOTHING evaluable (no variant could host a held-out fold at all) is the
    #    degenerate DATA_BOUNDED case — there is no realised OOS N to validate
    #    against, so it is distinct from "had folds, but N too thin" (starved).
    if evaluable == 0 or data_bounded_count > evaluable:
        return (
            "DATA_BOUNDED",
            f"{data_bounded_count}/{n_variants} variants had no held-out fold "
            "(insufficient bar history) — accumulate bars then rerun",
        )
    # 2. KEYSTONE: with held-out folds present, the positive control gates the
    #    whole verdict (FIX 1). If the gate cannot certify a STRONG synthetic edge
    #    at the run's available OOS N, P0a may NOT claim FEATURE_BOTTLENECK —
    #    regardless of the IS/OOS pass counts — it is validation-starved.
    if not positive_control_passed:
        return (
            "VALIDATION_STARVED",
            f"positive control FAILED: the gate cannot certify a strong edge at "
            f"the available OOS N={positive_control_n} (needs N>={min_passable_n}). "
            "0-pass measures validation starvation, NOT feature exhaustion — "
            "accumulate held-out bars before any FEATURE_BOTTLENECK claim",
        )
    # 3. FEATURE_BOTTLENECK is only honest with the positive control PASSED AND a
    #    minimum breadth of non-degenerate evaluable variants (FIX 8); below
    #    K_min the 0-pass measures thin search, not a dead feature space.
    if is_pass_count == _ZERO:
        if evaluable_nondegenerate < K_MIN_EVALUABLE:
            return (
                "DATA_BOUNDED",
                f"positive control passed (N={positive_control_n}) but only "
                f"{evaluable_nondegenerate} non-degenerate evaluable variant(s) "
                f"(< K_min={K_MIN_EVALUABLE}) — too thin a search to declare a "
                "feature bottleneck; accumulate bars/variants then rerun",
            )
        return (
            "FEATURE_BOTTLENECK",
            f"positive control PASSED (gate CAN certify a strong edge at "
            f"N={positive_control_n} >= {min_passable_n}) yet 0/{evaluable} "
            "evaluable variants cleared the IS gate — the TA feature space is "
            "exhausted; redirect to P0b/P1, do NOT build a generator",
        )
    if oos_pass_count == _ZERO:
        return (
            "OVERFIT_NO_GENERALIZATION",
            f"{is_pass_count} IS pass(es) but 0 OOS pass — held-out edge "
            "vanishes (feature/validation bottleneck)",
        )
    return (
        "SEARCH_SPACE_EXISTS",
        f"{oos_pass_count} variant(s) pass the trial-deflated held-out OOS gate "
        "— a P2 generator is justified (real edge in the searched space)",
    )
