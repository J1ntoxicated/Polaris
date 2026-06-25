"""STEP② candidate sweep — per-symbol activation MOTION components (#39).

Split from ``_candidate_sweep.py`` to keep both files ≤500 LOC. This module holds
the FIVE activation components and their normalizer constants — the per-symbol
live-motion primitives (tick rate / short move / intraday range), the ADDITIVE
spread_tradeability term, and the demoted Yahoo daily-vol ratio. The weighted
ADDITIVE blend itself (``activation_score``) stays in ``_candidate_sweep`` next to
``edge_score`` / ``candidate_score``.

🚨 ABSOLUTE (9-stack / flow_not_block): every component returns a value in [0,1]
and the caller SUMS ``weight × component`` — there is NO multiplier here.
``spread_tradeability`` is an ADDITIVE term (tight→1, wide→0), NEVER a multiplier
and NEVER a filter — a wide spread lowers the rank, it never blocks the name.

Pure + linear + clamp only (anti-overfit; no learned weights). Look-ahead-safe:
the daily-vol component reads ONLY confirmed bars (``confirmed`` guard). All knobs
are env-overridable /debate-calibration targets — never magic-in-place.

DEMO/PAPER only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Final

from polaris.scripts._candidate_sweep_bars import (
    confirmed,
    realized_expected_vol_ratio,
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _clamp01(x: float) -> float:
    if x != x:  # NaN guard
        return 0.0
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


# Per-symbol motion normalizers (raw metric → [0,1]). All named (no magic-in-place).
# tick_rate: ticks_600s linearly mapped FLOOR→HOT.
ACT_TICK_RATE_FLOOR_600S: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_TICK_FLOOR_600S", 5.0
)
ACT_TICK_RATE_HOT_600S: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_TICK_HOT_600S", 600.0
)
# short_move: |last_mid - mid_120s_ago| / mid in bps, vs HOT bps → [0,1].
ACT_SHORT_MOVE_HOT_BPS: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_SHORT_MOVE_HOT_BPS", 15.0
)
# intraday_range: (mid_high - mid_low) / mid in bps over 600s, vs HOT bps → [0,1].
ACT_RANGE_HOT_BPS: Final[float] = _env_float("POLARIS_SWEEP_ACT_RANGE_HOT_BPS", 40.0)
# spread_tradeability: TIGHT→1, WIDE→0 (an ADDITIVE rank-positive term).
ACT_SPREAD_TIGHT_BPS: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_SPREAD_TIGHT_BPS", 1.0
)
ACT_SPREAD_WIDE_BPS: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_SPREAD_WIDE_BPS", 12.0
)
# daily_vol: existing realized/expected vol ratio already maps to [0,1]; this is
# the HOT divisor applied on top (so a name moving HOT_RATIO× its own day → 1.0).
ACT_DAILY_VOL_HOT_RATIO: Final[float] = _env_float(
    "POLARIS_SWEEP_ACT_DAILY_VOL_HOT_RATIO", 1.0
)


def activation_components(
    bars_by_res: Mapping[str, Sequence[Any]],
    motion: Mapping[str, Any] | None,
    spread_bps: float | None,
    now_ts: int,
) -> dict[str, float]:
    """The five normalized activation components ∈ [0,1] (pure, no DB).

    Exposed for telemetry/tests so each ADDITIVE component is independently
    verifiable (no hidden multiplier). Keys: ``tick_rate``, ``short_move``,
    ``intraday_range``, ``spread_tradeability``, ``daily_vol``. The first three
    read ``motion`` (the writer's per-symbol accumulator snapshot); a missing/empty
    ``motion`` degrades them to 0 (the name scores on bars+spread alone — never an
    error, flow_not_block). ``spread_bps`` None → spread component 0.

    Look-ahead guard: only bars whose period has fully CLOSED (``ts + interval`` ≤
    ``now_ts``) feed the daily-vol component (``confirmed`` drops a forming bar).
    """
    bars15 = confirmed(bars_by_res.get("15m"), now_ts, "15m")
    bars1h = confirmed(bars_by_res.get("1H"), now_ts, "1H")
    bars1d = confirmed(bars_by_res.get("1D"), now_ts, "1D")

    vol_ratio = realized_expected_vol_ratio(bars15, bars1h, bars1d)
    daily_vol = (
        _clamp01(vol_ratio / ACT_DAILY_VOL_HOT_RATIO)
        if ACT_DAILY_VOL_HOT_RATIO > 0.0
        else 0.0
    )
    return {
        "tick_rate": _tick_rate_score(motion),
        "short_move": _short_move_score(motion),
        "intraday_range": _intraday_range_score(motion),
        "spread_tradeability": _spread_tradeability_score(spread_bps),
        "daily_vol": daily_vol,
    }


def _tick_rate_score(motion: Mapping[str, Any] | None) -> float:
    """Per-symbol ``ticks_600s`` mapped FLOOR→HOT → [0,1] (no motion → 0)."""
    if not motion:
        return 0.0
    ticks = float(motion.get("ticks_600s", 0) or 0)
    span = ACT_TICK_RATE_HOT_600S - ACT_TICK_RATE_FLOOR_600S
    if span <= 0.0:
        return 0.0
    return _clamp01((ticks - ACT_TICK_RATE_FLOOR_600S) / span)


def _short_move_score(motion: Mapping[str, Any] | None) -> float:
    """``|last_mid - mid_120s_ago| / mid`` bps vs HOT_BPS → [0,1] (no motion → 0)."""
    if not motion:
        return 0.0
    last = float(motion.get("last_mid", 0.0) or 0.0)
    ref = float(motion.get("mid_120s_ago", 0.0) or 0.0)
    if last <= 0.0 or ref <= 0.0:
        return 0.0
    move_bps = abs(last - ref) / last * 1e4
    if ACT_SHORT_MOVE_HOT_BPS <= 0.0:
        return 0.0
    return _clamp01(move_bps / ACT_SHORT_MOVE_HOT_BPS)


def _intraday_range_score(motion: Mapping[str, Any] | None) -> float:
    """``(mid_high - mid_low) / mid`` 600s bps vs RANGE_HOT_BPS → [0,1]."""
    if not motion:
        return 0.0
    hi = float(motion.get("mid_high_600s", 0.0) or 0.0)
    lo = float(motion.get("mid_low_600s", 0.0) or 0.0)
    last = float(motion.get("last_mid", 0.0) or 0.0)
    if last <= 0.0 or hi <= 0.0 or hi < lo:
        return 0.0
    range_bps = (hi - lo) / last * 1e4
    if ACT_RANGE_HOT_BPS <= 0.0:
        return 0.0
    return _clamp01(range_bps / ACT_RANGE_HOT_BPS)


def _spread_tradeability_score(spread_bps: float | None) -> float:
    """Per-symbol spread → tradeability ∈ [0,1] (tight→1, wide→0). ADDITIVE term.

    🚨 This is an ADDITIVE component inside activation, NEVER a multiplier and
    NEVER a filter — a wide spread lowers the activation rank, it never blocks the
    name. ``None`` (no quote row) → 0 (unknown tradeability, never a manufactured
    edge). Linear clamp from WIDE_BPS (0) up to TIGHT_BPS (1).
    """
    if spread_bps is None:
        return 0.0
    span = ACT_SPREAD_WIDE_BPS - ACT_SPREAD_TIGHT_BPS
    if span <= 0.0:
        return 0.0
    return _clamp01((ACT_SPREAD_WIDE_BPS - float(spread_bps)) / span)
