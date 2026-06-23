"""Increment 1 — EntranceJudge: deterministic multi-lens entrance judgment.

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack ban · in-loop GPT = 0.

The audit (``data_foundation_flow_audit_2026-06-23``) found the entrance/judgment
layer effectively *unbuilt*: probes attach ONLY at G6 (exit), the reserved
``Eligibility`` seat is empty, and the focus ranking sees only liquidity-z — no
per-candidate opportunity judgment. This module BUILDS that judgment, AI-free:

  * **5 deterministic lenses** score each active-universe candidate:
      - ``liquidity`` — headroom above the venue liquidity floor (vol + depth −
        spread), grouped-z per ``(venue, asset_class)``.
      - ``atr`` — realized-vol / signal-richness from ``atr_24h_pct``, grouped-z.
      - ``technical`` — live-tick momentum-vs-exhaustion lean (caller-supplied
        from the same drift/atr_slope window the tick engine holds; absent → 0).
      - ``regime`` — ``regime_fit(signal_family, regime)`` scalar (caller-supplied
        per instrument; absent → 0).
      - ``altdata`` — the ALREADY-FUSED ``fuse_evidence`` signed tilt
        (caller-supplied; tilt-only by contract, never a block; absent → 0).
  * Each lens yields a SIGNED lean ∈ [-1, +1] + a confidence ∈ [0, 1]; the judge
    fuses them into ONE confidence-weighted composite (mirrors
    ``probes.engine._composite``), squashed to an ``opportunity_score`` ∈ [0, 1].
  * ``trade_eligible`` = ``opportunity_score >= trade_floor`` (env-tunable,
    PERMISSIVE so MORE symbols stay eligible — aggressive bias). The default is
    eligible: a thin/neutral score never drops a symbol from observation.
  * ``ambiguous`` = the 5 lenses CONFLICT (signed disagreement: small composite
    while individual leans are large) OR confidence is THIN (sum below a floor).
    PURE TELEMETRY — the ambiguity flag NEVER mutates eligibility (the deferred
    Increment-2 async-advisory consumer reads it OUT of the tick loop).

This is a RANKING + ELIGIBILITY-FLAG producer (flow_not_block): it feeds focus
rank + a boolean flag, and touches ZERO sizing multiplier (9-stack untouched).
PURE — no I/O, no DB, no network, no GPT. The optional per-instrument lean maps
are supplied by the caller from data it already holds; an absent map = neutral
(0 lean), so the judge degrades to liquidity+ATR and never errors.
"""

from __future__ import annotations

import math
import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from polaris.core.universe.schema import (
    UniverseInstrument,
    liquidity_floor_for_venue,
)

__all__ = [
    "DEFAULT_TRADE_FLOOR",
    "EntranceJudge",
    "JudgeReading",
    "trade_floor_from_env",
]

# Lens fusion weights — relative confidence multipliers per lens (NOT a ≤1 stack:
# they only weight the confidence-weighted mean, never a cascaded size dampener).
# Liquidity + ATR are the always-present deterministic base; technical / regime /
# altdata weight in when the caller supplies them. /debate calibration target.
_W_LIQUIDITY: Final[float] = 1.0
_W_ATR: Final[float] = 0.8
_W_TECHNICAL: Final[float] = 0.7
_W_REGIME: Final[float] = 0.6
_W_ALTDATA: Final[float] = 0.4

# Permissive default trade-eligibility floor on the [0, 1] opportunity_score.
# LOW by design (aggressive bias): the majority of candidates clear it, so a low
# judgment narrows PRIORITY, it does not veto. Env-tunable.
DEFAULT_TRADE_FLOOR: Final[float] = 0.30
_TRADE_FLOOR_ENV: Final[str] = "POLARIS_ENTRANCE_TRADE_FLOOR"

# Ambiguity: a CONFLICT = a strong FAVOURABLE lens AND a strong ADVERSE lens both
# present (signed disagreement, regardless of where the weighted mean lands). And
# a total confidence-weight below the thin floor = not enough evidence to judge.
# Both flag ambiguous (telemetry only — never mutates eligibility).
_AMBIG_LENS_MAGNITUDE: Final[float] = 0.50
_AMBIG_THIN_CONFIDENCE: Final[float] = 0.30


def trade_floor_from_env() -> float:
    """Read ``POLARIS_ENTRANCE_TRADE_FLOOR`` → float; default on unset/garbage."""
    raw = os.environ.get(_TRADE_FLOOR_ENV)
    if raw is None or raw == "":
        return DEFAULT_TRADE_FLOOR
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TRADE_FLOOR


@dataclass(frozen=True, slots=True)
class JudgeReading:
    """One candidate's entrance judgment.

    ``opportunity_score`` ∈ [0, 1] (the focus-rank + eligibility input).
    ``trade_eligible`` = score >= floor (flow-preserving default). ``ambiguous`` =
    lens conflict OR thin confidence (telemetry seam for Increment 2; NEVER
    mutates eligibility). ``judge_margin`` = signed distance of the score to the
    floor (negative = below). ``contributing`` lists the lens ids that fed it;
    ``evidence`` is the per-lens signed leans (for the sidecar ambiguity row).
    """

    instrument_id: str
    opportunity_score: float
    trade_eligible: bool
    ambiguous: bool
    judge_margin: float
    contributing: list[str] = field(default_factory=list)
    evidence: dict[str, float] = field(default_factory=dict)


def _z_score(values: Sequence[float]) -> list[float]:
    """Population z-score; zeros when stdev is 0 or len < 2 (mirrors watchlist)."""
    if len(values) < 2:
        return [0.0] * len(values)
    mu = statistics.fmean(values)
    sigma = statistics.pstdev(values)
    if sigma <= 0.0 or not math.isfinite(sigma):
        return [0.0] * len(values)
    return [(v - mu) / sigma for v in values]


def _grouped_z(values: Sequence[float], groups: Sequence[str]) -> list[float]:
    """Per-``(venue, asset_class)`` population z (mirrors ``_grouped_z_score``).

    Each lens is normalized WITHIN its group so no single venue/asset-class
    dominates the cross-venue pool (GPT pitfall-5). Single-group → no-op.
    """
    by_group: dict[str, list[int]] = {}
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    out = [0.0] * len(values)
    for idxs in by_group.values():
        zs = _z_score([values[i] for i in idxs])
        for pos, i in enumerate(idxs):
            out[i] = zs[pos]
    return out


def _lean_from_z(z: float) -> float:
    """Map a population z-score to a signed lean ∈ [-1, +1] (tanh squash)."""
    return math.tanh(z)


def _liquidity_headroom(ins: UniverseInstrument) -> float:
    """Raw tradeable-quality headroom above the venue liquidity floor.

    The floor (``passes_liquidity_floor``) is the hard MEMBERSHIP boundary; this
    converts the HEADROOM above it into a continuous score — vol + depth reward,
    spread penalty — NOT a re-block. A missing datum (0.0) contributes 0 to its
    term (flow_not_block: never penalize an unknown). Log-scaled so a $1B name
    does not dwarf a $30M one to ±inf before the grouped-z normalizes.
    """
    floor = liquidity_floor_for_venue(ins.venue)
    vol_term = math.log1p(ins.vol_24h_usd) if ins.vol_24h_usd > 0.0 else 0.0
    depth_term = math.log1p(ins.depth_10bps_usd) if ins.depth_10bps_usd > 0.0 else 0.0
    # Spread penalty: distance UNDER the venue cap rewards tightness; a 0 cap
    # (axis disabled for the venue) contributes nothing.
    spread_pen = 0.0
    if floor.max_spread_bps > 0.0 and ins.spread_bps > 0.0:
        spread_pen = ins.spread_bps / floor.max_spread_bps
    return vol_term + 0.5 * depth_term - spread_pen


class EntranceJudge:
    """Composes 5 deterministic lenses into a per-candidate JudgeReading.

    PURE / SYNC. The optional ``technical_lean`` / ``regime_lean`` /
    ``altdata_lean`` maps are per-``instrument_id`` signed leans ∈ [-1, +1] the
    caller supplies from data it already holds (the tick feature window, the
    regime-fit scalar, the fused alt-data tilt); an absent entry = 0 (neutral),
    so the judge degrades gracefully to the always-present liquidity + ATR base.
    """

    __slots__ = ("_trade_floor",)

    def __init__(self, *, trade_floor: float | None = None) -> None:
        self._trade_floor = (
            trade_floor if trade_floor is not None else trade_floor_from_env()
        )

    def judge_universe(
        self,
        instruments: list[UniverseInstrument],
        *,
        technical_lean: Mapping[str, float] | None = None,
        regime_lean: Mapping[str, float] | None = None,
        altdata_lean: Mapping[str, float] | None = None,
    ) -> dict[str, JudgeReading]:
        """Score every candidate → ``{instrument_id: JudgeReading}`` (deterministic)."""
        if not instruments:
            return {}
        technical_lean = technical_lean or {}
        regime_lean = regime_lean or {}
        altdata_lean = altdata_lean or {}

        groups = [f"{ins.venue}/{ins.asset_class}" for ins in instruments]
        liq_z = _grouped_z([_liquidity_headroom(ins) for ins in instruments], groups)
        atr_z = _grouped_z([ins.atr_24h_pct for ins in instruments], groups)

        out: dict[str, JudgeReading] = {}
        for i, ins in enumerate(instruments):
            iid = ins.instrument_id
            # Signed leans per lens. Liquidity + ATR are always present (grouped-z
            # → tanh lean); the optional lenses default to 0 (neutral).
            leans: dict[str, float] = {
                "liquidity": _lean_from_z(liq_z[i]),
                "atr": _lean_from_z(atr_z[i]),
                "technical": _clamp_lean(technical_lean.get(iid, 0.0)),
                "regime": _clamp_lean(regime_lean.get(iid, 0.0)),
                "altdata": _clamp_lean(altdata_lean.get(iid, 0.0)),
            }
            out[iid] = self._compose(iid, leans)
        return out

    def _compose(self, iid: str, leans: dict[str, float]) -> JudgeReading:
        """Confidence-weighted fuse → opportunity_score + eligibility + ambiguity.

        Confidence per lens = ``|lean| * weight`` (a lens that abstains, lean 0,
        carries no weight — GPT pitfall-4: no ambiguity inflation from neutral
        lenses). The composite is the confidence-weighted mean ∈ [-1, +1], mapped
        to [0, 1] for the opportunity_score (0.5 = neutral).
        """
        weights = {
            "liquidity": _W_LIQUIDITY,
            "atr": _W_ATR,
            "technical": _W_TECHNICAL,
            "regime": _W_REGIME,
            "altdata": _W_ALTDATA,
        }
        conf = {k: abs(leans[k]) * weights[k] for k in leans}
        weight_sum = sum(conf.values())
        if weight_sum <= 0.0:
            composite = 0.0
        else:
            composite = sum(leans[k] * conf[k] for k in leans) / weight_sum
            composite = max(-1.0, min(1.0, composite))

        opportunity_score = (composite + 1.0) / 2.0
        floor = self._trade_floor
        trade_eligible = opportunity_score >= floor
        judge_margin = opportunity_score - floor

        contributing = [k for k in leans if abs(leans[k]) > 0.0] or ["liquidity"]
        ambiguous = self._is_ambiguous(composite, leans, weight_sum)
        return JudgeReading(
            instrument_id=iid,
            opportunity_score=opportunity_score,
            trade_eligible=trade_eligible,
            ambiguous=ambiguous,
            judge_margin=judge_margin,
            contributing=contributing,
            evidence={k: round(v, 4) for k, v in leans.items()},
        )

    @staticmethod
    def _is_ambiguous(
        composite: float, leans: dict[str, float], weight_sum: float
    ) -> bool:
        """CONFLICT (a strong favourable AND a strong adverse lens) OR THIN.

        Telemetry only — flags WHERE Increment 2's async-advisory consumer could
        weigh in. NEVER mutates eligibility (GPT pitfall-1: advisory not
        authoritative; pitfall-4: conflict/thin-based, not blanket). ``composite``
        is unused by the conflict rule (a conflict can land at any weighted mean);
        kept in the signature for the offline reader's provenance.
        """
        _ = composite
        if weight_sum < _AMBIG_THIN_CONFIDENCE:
            return True  # thin evidence — not enough to judge confidently
        values = leans.values()
        strong_up = any(v >= _AMBIG_LENS_MAGNITUDE for v in values)
        strong_down = any(v <= -_AMBIG_LENS_MAGNITUDE for v in values)
        return strong_up and strong_down


def _clamp_lean(value: float) -> float:
    """Clamp a caller-supplied lean to the signed [-1, +1] contract."""
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))
