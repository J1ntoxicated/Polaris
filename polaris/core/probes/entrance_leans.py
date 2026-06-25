"""Entrance-judge lean builders — turn held data into per-instrument signed leans.

DEMO/PAPER · AGGRESSIVE · flow_not_block · 9-stack collapse permanently blocked.

The :class:`~polaris.core.probes.entrance.EntranceJudge` is a 5-lens design, but
production fed only the two always-present lenses (liquidity + ATR); the optional
``technical`` / ``regime`` / ``altdata`` lenses defaulted to neutral (0) forever —
the bot held the evidence but never spent it on the entrance rank (a "헛도는"
cycle, audit ``code_review_2026-06-24``). These three PURE builders adapt data the
production loop ALREADY holds into per-``instrument_id`` signed leans ∈ [-1, +1]:

  * ``build_technical_lean`` — live-tick mid-drift over the quote-writer feature
    window, tanh-squashed (the same drift the tick-decision re-map uses).
  * ``build_regime_lean`` — the regime_state SSOT directional bias
    (``bull_trend`` → +1 / ``bear_trend`` → -1; chop/crisis/unknown → neutral).
    Signal-family-free, so it is available at the Layer-0 focus cadence (no
    strategy has fired yet — ``regime_fit`` needs a family this stage lacks).
  * ``build_altdata_lean`` — the ALREADY-FUSED ``fuse_evidence`` label scores
    collapsed to ONE signed tilt (bull − bear, normalized; a dominant ``crisis``
    score → strong adverse) and broadcast to every instrument in the group.

CONTRACT (all three): an ABSENT / thin / neutral datum is OMITTED from the map —
the judge reads an absent ``instrument_id`` as 0 (neutral). So every builder
degrades to neutral and NEVER errors, NEVER blocks, NEVER cuts size. The leans
feed ONLY ``opportunity_score`` (focus rank + the permissive eligibility flag);
they touch ZERO sizing multiplier (9-stack untouched — signal-only).

PURE / SYNC: the callers (a quote-writer accessor, a regime reader, a fuser
adapter) inject the side-effecting reads as plain callables, so this module does
no I/O, no DB, no network, no GPT and is fully unit-testable.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from polaris.core.ticks.regime_gate import direction_bias
from polaris.core.universe.schema import UniverseInstrument

__all__ = [
    "build_altdata_lean",
    "build_regime_lean",
    "build_technical_lean",
]


class _FeatureWindowSource(Protocol):
    """The quote-writer surface the technical lean needs (``feature_window``)."""

    def feature_window(self, instrument_id: str) -> list[Any]:  # pragma: no cover
        ...


def build_technical_lean(
    instruments: Sequence[UniverseInstrument],
    writer: _FeatureWindowSource | None,
) -> dict[str, float]:
    """Per-instrument signed momentum lean from the live tick feature window.

    The lean is the signed mid drift ``(newest - oldest) / oldest`` over the
    writer's in-mem ring, tanh-squashed to [-1, +1] (a positive lean = price
    rose over the window = momentum FOR an entry; negative = exhaustion). A
    missing writer, an empty/single-tick window, or a non-positive oldest mid →
    the instrument is OMITTED (the judge reads it as neutral 0; flow_not_block —
    an un-observed name is never penalized). NEVER a size cut: the lean only
    tilts the opportunity_score rank.
    """
    if writer is None:
        return {}
    out: dict[str, float] = {}
    for ins in instruments:
        window = writer.feature_window(ins.instrument_id)
        drift = _window_drift(window)
        if drift is None:
            continue  # thin/empty window → neutral (omitted)
        out[ins.instrument_id] = math.tanh(drift)
    return out


def _window_drift(window: Sequence[Any]) -> float | None:
    """Signed mid drift over a tick window, or ``None`` when too thin to judge.

    Mirrors ``_production_tick_decision._window_mid_drift`` but returns ``None``
    (not 0.0) on a thin window so the lean builder can OMIT it — an omitted
    instrument is neutral, while a real 0.0 drift (flat window) is a genuine
    neutral observation. < 2 ticks / non-positive oldest → None (degrade safe).
    """
    if not window or len(window) < 2:
        return None
    try:
        first = float(window[0].mid)
        last = float(window[-1].mid)
    except (AttributeError, TypeError, ValueError):
        return None
    if first <= 0.0:
        return None
    return (last - first) / first


def build_regime_lean(
    instruments: Sequence[UniverseInstrument],
    regime_reader: Callable[[str, str], str | None],
) -> dict[str, float]:
    """Per-instrument directional regime lean from the regime_state SSOT.

    ``regime_reader(venue, underlying_group_id)`` returns the current regime
    label (or ``None``). The label's :func:`direction_bias` (+1 ``bull_trend`` /
    -1 ``bear_trend`` / 0 otherwise) is the signed lean — a regime tilt the
    entrance rank can lean on without a signal_family (which is unknown at the
    Layer-0 focus cadence). The reader is consulted ONCE per distinct group and
    the bias broadcast to every instrument in that group. A direction-neutral
    regime (chop / crisis / unknown / missing) → bias 0 → the instrument is
    OMITTED (neutral). A BIAS, not a block: both sides stay tradable.
    """
    bias_by_group: dict[tuple[str, str], int] = {}
    out: dict[str, float] = {}
    for ins in instruments:
        key = (ins.venue, ins.underlying_group_id)
        if key not in bias_by_group:
            bias_by_group[key] = direction_bias(regime_reader(*key))
        bias = bias_by_group[key]
        if bias != 0:
            out[ins.instrument_id] = float(bias)
    return out


def build_altdata_lean(
    instruments: Sequence[UniverseInstrument],
    scores_reader: Callable[[str], dict[str, float] | None],
) -> dict[str, float]:
    """Per-instrument signed alt-data tilt from the ALREADY-FUSED label scores.

    ``scores_reader(underlying_group_id)`` returns the fuser's per-label score
    dict (``fuse_evidence`` ``ev["scores"]``: ``{bull_trend, bear_trend, chop,
    crisis}``) or ``None`` when there is no fresh evidence. The dict is collapsed
    to ONE signed tilt ∈ [-1, +1] (see :func:`_scores_to_tilt`) and broadcast to
    every instrument sharing the group (the fuser is keyed per group, not per
    venue-instrument). No evidence, or a zero tilt (balanced / chop-only), →
    OMITTED (neutral). The fuser is called ONCE per distinct group. tilt-only by
    contract — never a block, never a size cut (the alt-data mandate: SIGNAL).
    """
    tilt_by_group: dict[str, float] = {}
    out: dict[str, float] = {}
    for ins in instruments:
        group = ins.underlying_group_id
        if group not in tilt_by_group:
            tilt_by_group[group] = _scores_to_tilt(scores_reader(group))
        tilt = tilt_by_group[group]
        if tilt != 0.0:
            out[ins.instrument_id] = tilt
    return out


def _scores_to_tilt(scores: dict[str, float] | None) -> float:
    """Collapse fused label scores to one signed tilt ∈ [-1, +1].

    A dominant ``crisis`` (> both directional scores) is a strong adverse tilt
    (-1) — alt-data evidence of a stress regime leans an entrance OUT (signal,
    never a block: the name still ranks + is watched). Otherwise the tilt is the
    normalized bull/bear differential ``(bull - bear) / max(bull, bear, 1)`` so a
    one-sided book → ±1 and a balanced / absent book → 0 (omitted upstream).
    """
    if not scores:
        return 0.0
    bull = float(scores.get("bull_trend", 0.0))
    bear = float(scores.get("bear_trend", 0.0))
    crisis = float(scores.get("crisis", 0.0))
    if crisis > bull and crisis > bear and crisis > 0.0:
        return -1.0
    denom = max(bull, bear, 1.0)
    tilt = (bull - bear) / denom
    return max(-1.0, min(1.0, tilt))
