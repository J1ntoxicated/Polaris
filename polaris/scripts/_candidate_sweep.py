"""STEP② candidate sweep — dynamic active-focus over the static ground.

Jin "맨날 들어오는 애들만" (only the same names ever come in). STEP① gave every
active ticker a static ground (multi-resolution bars + fused sentiment/event via
``read_ticker_ground``). But the LIVE focus is still a fixed merit rank, so the
same ~53 high-liquidity names sit at the rank head forever and only THEY get
WS/tick/signal/trade — while ``tick_inflow`` shows the market is alive elsewhere.

This module holds the PURE two-stage scoring primitives + the cross-cutting pure
helpers (hysteresis / fast-track / tempo). The orchestration that walks the
universe, reads the static ground, decomposes the cap-200 buckets, and emits
``FocusSelection`` rows lives in ``_candidate_sweep_select`` (split to keep both
files ≤500 LOC); ``select_candidate_focus`` is re-exported here so callers have a
single import surface.

🚨 ABSOLUTE (flow_not_block / 9-stack / aggressive):
- Candidate selection = choosing WHERE to look live, NOT blocking a trade, cutting
  a size, or gating an exit. The scan covers ALL active rows + fast-track catches
  out-of-focus outliers (no scan-miss). Stop-loss rails are untouched.
- The sweep produces ``FocusSelection`` rows (focus_rank/tier/bucket) — NEVER a
  sizing multiplier, NEVER a ≤1 mult stack. Zero sizing code is touched here.
- cap 200 WIDENS the live set; cold-start = vol-penalty 0.5× (NOT exclusion);
  the exploration bucket fights blindness.
- Deterministic Python, NO LLM calls. AI-free.

Look-ahead safety: every bar consumed must have its bar-close ts ≤ ``now_ts``
(the sweep reads stored bars; a bar whose period has not closed is dropped).
Anti-overfit: component functions are FIXED (no learned weights), linear + clamp
only, no per-ticker tuning. All knobs are env-overridable /debate calibration
targets — never magic-in-place.

DEMO/PAPER only.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from polaris.core.universe.schema import UniverseInstrument
from polaris.scripts._candidate_sweep_bars import (
    confirmed,
    range_expansion,
    realized_expected_vol_ratio,
    trend_direction,
)

logger = logging.getLogger(__name__)

# ``select_candidate_focus`` is re-exported via the module ``__getattr__`` below
# (it lives in ``_candidate_sweep_select`` to honor the ≤500-LOC split); it is not
# listed here because it is not a statically-defined name in this module.
__all__ = [
    "activation_score",
    "apply_hysteresis",
    "candidate_score",
    "edge_score",
    "fast_track_outliers",
    "tempo_sweep_period",
]


# ---------------------------------------------------------------------------
# Env constants (FIXED defaults, env-overridable; /debate calibration targets).
# NEVER magic-in-place — every weight/threshold/bucket-count is named here.
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return default


# Activation linear-blend weights (sum need not be 1; the result is clamped).
ACT_W_VOL_RATIO: Final[float] = _env_float("POLARIS_SWEEP_ACT_W_VOL", 0.45)
ACT_W_RANGE_EXP: Final[float] = _env_float("POLARIS_SWEEP_ACT_W_RANGE", 0.35)
ACT_W_MICRO: Final[float] = _env_float("POLARIS_SWEEP_ACT_W_MICRO", 0.20)
# Micro-activity normalizers (venue-level tick pulse → [0,1]).
MICRO_TICKS_FULL: Final[float] = _env_float("POLARIS_SWEEP_MICRO_TICKS_FULL", 20_000.0)
MICRO_FLOW_FULL: Final[float] = _env_float("POLARIS_SWEEP_MICRO_FLOW_FULL", 5.0)

# Edge linear-blend weights.
EDGE_W_SENTIMENT: Final[float] = _env_float("POLARIS_SWEEP_EDGE_W_SENT", 0.55)
EDGE_W_TECH_ALIGN: Final[float] = _env_float("POLARIS_SWEEP_EDGE_W_TECH", 0.45)

# Bucket counts (cap 200 decomposition). /debate calibration targets.
BUCKET_ANCHOR: Final[int] = _env_int("POLARIS_SWEEP_BUCKET_ANCHOR", 40)
BUCKET_DYNAMIC: Final[int] = _env_int("POLARIS_SWEEP_BUCKET_DYNAMIC", 140)
BUCKET_EVENT_HOT: Final[int] = _env_int("POLARIS_SWEEP_BUCKET_EVENT", 10)
BUCKET_EXPLORATION: Final[int] = _env_int("POLARIS_SWEEP_BUCKET_EXPLORE", 10)

# Cold-start penalty: a thin/absent-ground name (or < N confirmed bars) keeps its
# score × this factor — PENALTY, NOT exclusion (flow_not_block / aggressive).
COLD_START_PENALTY: Final[float] = _env_float("POLARIS_SWEEP_COLD_PENALTY", 0.5)
COLD_START_MIN_BARS: Final[int] = _env_int("POLARIS_SWEEP_COLD_MIN_BARS", 8)

# Hysteresis (anti-flicker) deltas on candidate_score.
HYST_ENTER_DELTA: Final[float] = _env_float("POLARIS_SWEEP_HYST_ENTER", 0.08)
HYST_EXIT_DELTA: Final[float] = _env_float("POLARIS_SWEEP_HYST_EXIT", 0.03)

# fast-track: an out-of-focus row whose activation ≥ this is promoted (widen).
FAST_TRACK_SPIKE: Final[float] = _env_float("POLARIS_SWEEP_FASTTRACK_SPIKE", 0.7)

# Tempo modulation bounds (hotter market → shorter period).
TEMPO_MIN_PERIOD_SEC: Final[float] = _env_float("POLARIS_SWEEP_TEMPO_MIN", 120.0)
TEMPO_MAX_PERIOD_SEC: Final[float] = _env_float("POLARIS_SWEEP_TEMPO_MAX", 1800.0)

# Venue/session allocation knobs (the producer modulates dynamic SEATS by session
# state — allocation, never a block). /debate calibration targets, env-overridable.
# A venue near/at a cash close (within LEAD_SEC) or outside US RTH is down-weighted
# to DOWNWEIGHT (fewer seats, never zeroed). RTH window is UTC minutes-of-day
# (EDT reference, matches the equity session gate).
SESSION_LEAD_SEC: Final[int] = _env_int("POLARIS_SWEEP_SESSION_LEAD_SEC", 1800)
SESSION_DOWNWEIGHT: Final[float] = _env_float("POLARIS_SWEEP_SESSION_DOWNWEIGHT", 0.3)
RTH_OPEN_MIN: Final[int] = _env_int("POLARIS_SWEEP_RTH_OPEN_MIN", 13 * 60 + 30)
RTH_CLOSE_MIN: Final[int] = _env_int("POLARIS_SWEEP_RTH_CLOSE_MIN", 20 * 60)


def _clamp01(x: float) -> float:
    if x != x:  # NaN guard
        return 0.0
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


# ---------------------------------------------------------------------------
# Stage 1 — ActivationScore ∈ [0,1] ("will it move today", REQUIRED gate).
# ---------------------------------------------------------------------------


def activation_score(
    bars_by_res: Mapping[str, Sequence[Any]],
    tick_inflow_row: Mapping[str, Any] | None,
    now_ts: int,
) -> float:
    """"Will it move today" gate ∈ [0,1] from CONFIRMED bars + venue tick pulse.

    Blend (linear, FIXED weights, clamp):
    - realized/expected vol ratio: recent 15m/1H true-range vs the ticker's own
      ~1D ATR baseline (own-baseline → cross-asset comparable);
    - recent range expansion: last 15m–1H range vs its trailing median;
    - micro activity: venue ``tick_inflow.ticks_600s`` / ``max_flow_size_600s``
      normalized (venue-level — per-symbol tick counts are not stored).

    GATE form: **0 when no movement** (flat bars + no inflow → 0), so a row that
    will not move today scores ~0 and ``candidate_score`` collapses to 0.

    Look-ahead guard: only bars whose period has fully CLOSED (``ts + interval`` ≤
    ``now_ts``) are consumed — a still-forming bar is dropped by :func:`confirmed`.
    """
    bars15 = confirmed(bars_by_res.get("15m"), now_ts, "15m")
    bars1h = confirmed(bars_by_res.get("1H"), now_ts, "1H")
    bars1d = confirmed(bars_by_res.get("1D"), now_ts, "1D")

    vol_ratio = realized_expected_vol_ratio(bars15, bars1h, bars1d)
    range_exp = range_expansion(bars15, bars1h)
    micro = _micro_activity(tick_inflow_row)

    blended = (
        ACT_W_VOL_RATIO * vol_ratio
        + ACT_W_RANGE_EXP * range_exp
        + ACT_W_MICRO * micro
    )
    return _clamp01(blended)


def _micro_activity(tick_inflow_row: Mapping[str, Any] | None) -> float:
    """Venue micro-pulse → [0,1] from ticks_600s + max_flow_size_600s (clamp)."""
    if not tick_inflow_row:
        return 0.0
    ticks = float(tick_inflow_row.get("ticks_600s", 0) or 0)
    flow = float(tick_inflow_row.get("max_flow_size_600s", 0.0) or 0.0)
    ticks_n = _clamp01(ticks / MICRO_TICKS_FULL) if MICRO_TICKS_FULL > 0 else 0.0
    flow_n = _clamp01(flow / MICRO_FLOW_FULL) if MICRO_FLOW_FULL > 0 else 0.0
    return _clamp01(0.5 * ticks_n + 0.5 * flow_n)


# ---------------------------------------------------------------------------
# Stage 2 — EdgeScore ∈ [0,1] ("edge WHEN it moves", direction).
# ---------------------------------------------------------------------------


def edge_score(
    bars_by_res: Mapping[str, Sequence[Any]],
    ground: Mapping[str, Any] | None,
    now_ts: int,
) -> float:
    """"Edge when it moves" ∈ [0,1] from ground sentiment + multi-TF alignment.

    Blend (linear, FIXED weights, clamp):
    - sentiment direction × strength from the fused ``ground`` (``scores`` /
      ``label``): a clearly-directional ground (bull or bear dominant) → high;
    - multi-TF technical alignment: 15m vs 1H vs 1D trend agreement on confirmed
      bars (all three agreeing → high, conflicting → ~neutral).

    Neutral (≈0.5) when there is no edge — combined with the ``0.5 + 0.5×edge``
    envelope in :func:`candidate_score`, a strong mover with no edge still scores
    on activation alone (never zeroed by a neutral edge).
    """
    bars15 = confirmed(bars_by_res.get("15m"), now_ts, "15m")
    bars1h = confirmed(bars_by_res.get("1H"), now_ts, "1H")
    bars1d = confirmed(bars_by_res.get("1D"), now_ts, "1D")

    sentiment = _sentiment_strength(ground)
    tech = _tech_alignment(bars15, bars1h, bars1d)

    blended = EDGE_W_SENTIMENT * sentiment + EDGE_W_TECH_ALIGN * tech
    return _clamp01(blended)


def _sentiment_strength(ground: Mapping[str, Any] | None) -> float:
    """Directional conviction from fused ground scores → [0,1].

    A ground whose dominant directional label (bull_trend / bear_trend) clearly
    leads the others scores high; a flat/balanced ground (no edge) → ~0.5.
    """
    if not ground:
        return 0.5
    scores = ground.get("scores")
    if not isinstance(scores, Mapping) or not scores:
        return 0.5
    bull = float(scores.get("bull_trend", 0.0) or 0.0)
    bear = float(scores.get("bear_trend", 0.0) or 0.0)
    chop = float(scores.get("chop", 0.0) or 0.0)
    crisis = float(scores.get("crisis", 0.0) or 0.0)
    total = bull + bear + chop + crisis
    if total <= 0.0:
        return 0.5
    # Directional share = how much of the conviction is a CLEAR up/down trend
    # (vs chop/crisis indecision). 0.5 (neutral) + 0.5×directional_lead.
    directional = max(bull, bear) / total
    return _clamp01(0.5 + 0.5 * (directional - 0.25) / 0.75)


def _tech_alignment(
    bars15: Sequence[Any], bars1h: Sequence[Any], bars1d: Sequence[Any]
) -> float:
    """Multi-TF trend agreement → [0,1]. All three TFs agree → high; mixed → 0.5."""
    dirs = [
        trend_direction(bars15),
        trend_direction(bars1h),
        trend_direction(bars1d),
    ]
    nonzero = [d for d in dirs if d != 0]
    if not nonzero:
        return 0.5
    net = sum(nonzero)
    # |net| / count: 3 agreeing → 1.0; 2-vs-1 → 0.33; balanced → 0.0. Map onto
    # 0.5 (neutral) .. 1.0 (full agreement).
    agreement = abs(net) / len(nonzero)
    return _clamp01(0.5 + 0.5 * agreement)


# ---------------------------------------------------------------------------
# Two-stage composite.
# ---------------------------------------------------------------------------


def candidate_score(activation: float, edge: float) -> float:
    """``activation × (0.5 + 0.5 × edge)`` — the gate-amplifier composite.

    A row that will not move today (activation ≈ 0) scores ≈ 0 regardless of edge
    (the gate). A mover is amplified by its edge: no-edge mover keeps half its
    activation, full-edge mover keeps all of it. Result ∈ [0,1].
    """
    a = _clamp01(activation)
    e = _clamp01(edge)
    return a * (0.5 + 0.5 * e)


# ---------------------------------------------------------------------------
# Hysteresis (anti-flicker) — pure function over score maps.
# ---------------------------------------------------------------------------


def apply_hysteresis(
    prev_focus_symbols: set[str],
    new_scored: Mapping[str, float],
    enter_delta: float,
    exit_delta: float,
) -> set[str]:
    """Return the stabilized membership set (anti-flicker).

    A NON-incumbent name is admitted only if it beats the WEAKEST incumbent by
    more than ``enter_delta`` (a challenger inside the band cannot displace). An
    incumbent is RELEASED only if its score falls below ``exit_delta`` (it sticks
    while it is at least marginally alive). Pure over the score maps — it never
    blocks an entry; it only stabilizes WHICH names occupy the active set.
    """
    incumbents = {s for s in prev_focus_symbols if s in new_scored}
    weakest_incumbent = (
        min(new_scored[s] for s in incumbents) if incumbents else 0.0
    )
    kept: set[str] = set()
    # Keep incumbents still above the exit floor.
    for s in incumbents:
        if new_scored[s] >= exit_delta:
            kept.add(s)
    # Admit challengers that clear the weakest incumbent by > enter_delta.
    for s, score in new_scored.items():
        if s in incumbents:
            continue
        if score > weakest_incumbent + enter_delta:
            kept.add(s)
    return kept


# ---------------------------------------------------------------------------
# fast-track — promote out-of-focus outliers (additive widen, never a block).
# ---------------------------------------------------------------------------


def fast_track_outliers(
    active_universe: Sequence[UniverseInstrument],
    scored: Mapping[str, float],
    focus_set: set[tuple[str, str]],
    *,
    spike_threshold: float = FAST_TRACK_SPIKE,
) -> list[tuple[str, str]]:
    """Out-of-focus rows whose score ≥ ``spike_threshold`` → promote (widen).

    A vol/range spike that is currently OUTSIDE the focus set is surfaced so the
    sweep never misses a sudden mover (anti-blindness). ADD-only (flow_not_block):
    it WIDENS what is watched; it never drops or blocks anything. Returns the
    ``(venue, symbol)`` keys to add to the active set.
    """
    out: list[tuple[str, str]] = []
    for inst in active_universe:
        key = (inst.venue, inst.symbol)
        if key in focus_set:
            continue
        if scored.get(inst.instrument_id, 0.0) >= spike_threshold:
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# tempo — market-tempo modulated sweep period (pure function).
# ---------------------------------------------------------------------------


def tempo_sweep_period(
    tick_inflow_rows: Sequence[Mapping[str, Any]],
    base_period_sec: float,
) -> float:
    """Modulate the sweep period by market tempo — hotter market → shorter.

    The aggregate venue tick pulse (max ``ticks_600s`` / ``max_flow_size_600s``
    across venues, normalized) interpolates the period DOWN from
    ``base_period_sec`` toward ``TEMPO_MIN_PERIOD_SEC`` (hot) and is clamped at
    ``TEMPO_MAX_PERIOD_SEC`` (cold). Pure function; the dual-timer scheduling is a
    documented follow-up — the producer wires on the existing single L0 cadence.
    """
    pulse = 0.0
    for row in tick_inflow_rows:
        pulse = max(pulse, _micro_activity(row))
    # Hot (pulse→1) interpolates toward MIN; cold (pulse→0) keeps base.
    period = base_period_sec - pulse * (base_period_sec - TEMPO_MIN_PERIOD_SEC)
    if period < TEMPO_MIN_PERIOD_SEC:
        period = TEMPO_MIN_PERIOD_SEC
    if period > TEMPO_MAX_PERIOD_SEC:
        period = TEMPO_MAX_PERIOD_SEC
    return period


# ---------------------------------------------------------------------------
# Orchestration re-export — the universe walk + bucket selection lives in
# ``_candidate_sweep_select`` (split for the ≤500-LOC cap). Imported lazily so
# the pure-primitive module has no import-time dependency on the orchestrator
# (which imports these primitives back).
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    """Lazily expose ``select_candidate_focus`` from the orchestration module.

    A module-level ``__getattr__`` (PEP 562) keeps the import one-directional:
    ``_candidate_sweep_select`` imports the primitives from here, and a caller's
    ``_candidate_sweep.select_candidate_focus`` resolves on first access — no
    import-time cycle.
    """
    if name == "select_candidate_focus":
        from polaris.scripts._candidate_sweep_select import select_candidate_focus

        return select_candidate_focus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
