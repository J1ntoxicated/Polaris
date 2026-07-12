"""fee-split v0 (group GROSS) — SCALE gate (tier-amplifier-only withhold).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
decision function — no DB, no I/O. Spec source:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 items 2 + 4:

    "SCALE 조건: gross_LCB > best_case_friction + edge_margin. 미달 라벨 =
    GROSS_EDGE_FEE_UNPROVEN (BAD_STRATEGY 아님). 진입 차단·강등·쿨다운 0."
    "SCALE 게이트 바인딩 시 베이스라인 100% 무손상 — applied_tier_amp=1.0
    (1.5/2/3x 보너스만 보류). no_defensive_dampen 합격."

Withholding the tier-amplifier bonus is the ONLY effect of this gate binding
— it NEVER touches base_risk_pct / continuous_scalar / cell_mult /
listing_watchdog_mult / entry admission / cooldown (aggressive_always_profit
/ no_block_filter_architecture / no_defensive_param_dampen). This is
distinct from a demotion/BAD_STRATEGY verdict: a strategy whose gross edge
has not yet cleared the fee-plus-margin bar keeps trading and keeps its
FULL baseline size — it just does not additionally get amplified.

Wiring note (fee_split_flip_r2_2026-07-12 item 3, supersedes the v0-era
"not yet wired" note this docstring used to carry): the R2 debate's own
shadow-divergence measurement (886 closes / 743 eval-pts, 12.4% behavior
divergence, well under the 15% flip-readiness bar — see
``shadow_divergence.py`` / ``vault/50_research/debates/
fee_split_flip_r2_2026-07-12.md``) already passed BEFORE this decision, so
``polaris.core.sizing.engine.compute_size`` now calls this function live
(gated off under ``POLARIS_SCOREF_NET_LEGACY=1`` rollback — see
``score_f.use_legacy_net_axis``). ``gross_lcb=None`` (cold-start, N_eff
below ``gross_scorer.N_EFF_MIN_FLOOR``) still binds/withholds by this
function's own contract below — that is the intended, spec'd behavior
("economic 자격 = gross_LCB > best_case_friction + edge_margin"), not a
starvation bug: it withholds the BONUS ONLY, baseline sizing is untouched,
so it stays compliant with aggressive_always_profit / no_defensive_dampen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "GROSS_EDGE_FEE_UNPROVEN_REASON",
    "ScaleGateResult",
    "evaluate_scale_gate",
]

GROSS_EDGE_FEE_UNPROVEN_REASON: Final[str] = "GROSS_EDGE_FEE_UNPROVEN"


@dataclass(slots=True, frozen=True)
class ScaleGateResult:
    """SCALE gate verdict. ``applied_tier_amp`` is the ONLY field a caller
    may use to influence sizing — ``binding`` / ``reason`` are audit-only.
    """

    binding: bool  # True = amplifier withheld
    applied_tier_amp: float  # 1.0 when binding, else the caller's resolved tier_amp
    reason: str | None  # GROSS_EDGE_FEE_UNPROVEN_REASON when binding, else None


def evaluate_scale_gate(
    *,
    gross_lcb: float | None,
    resolved_tier_amp: float,
    best_case_friction: float,
    edge_margin: float,
) -> ScaleGateResult:
    """Withhold the tier-amplifier bonus (fallback to 1.0) when
    ``gross_lcb`` fails to clear ``best_case_friction + edge_margin``.

    ``gross_lcb=None`` (no verdict yet — e.g. N_eff below the cold-start
    floor, see ``gross_scorer.confidence_for_n_eff``) is treated as "not yet
    proven" -> binds (withholds), same as any other below-bar case — this
    is a conservative default for the AMPLIFIER BONUS ONLY; it never blocks
    entry/cooldown (flow_not_block — the gate has no such power, see module
    docstring). ``resolved_tier_amp == 1.0`` already (no amplifier earned)
    -> the gate is a no-op regardless of gross_lcb (nothing to withhold).
    """
    if resolved_tier_amp <= 1.0:
        return ScaleGateResult(binding=False, applied_tier_amp=resolved_tier_amp, reason=None)

    bar = best_case_friction + edge_margin
    proven = gross_lcb is not None and gross_lcb > bar
    if proven:
        return ScaleGateResult(binding=False, applied_tier_amp=resolved_tier_amp, reason=None)

    return ScaleGateResult(
        binding=True, applied_tier_amp=1.0, reason=GROSS_EDGE_FEE_UNPROVEN_REASON,
    )
