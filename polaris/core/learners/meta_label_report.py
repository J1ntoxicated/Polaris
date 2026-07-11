"""Meta-label accumulation report + purged-split prep (frontgate-scan item #10).

READ-ONLY instrumentation over the existing ``meta_labels`` table
(``meta_label.py`` remains the only writer — this module never labels/trains).
Two pieces:

- :func:`build_distribution_report` — pooled + per-strategy/barrier/regime/
  session counts, checked against the label gate (pooled >= 2000, per-strategy
  >= 500) that must pass before ANY 2nd-stage act/skip model may train.
- :func:`load_timed_observations` — converts ``meta_labels`` rows into
  ``purged_time_split.TimedObservation`` so a future model's CV harness can
  purge boundary-straddling trades ahead of time.

SESSION CAVEAT: ``per_session`` reflects ``_production_close_effects.py``'s
``session="asia"`` hardcode (line ~258 of that module) — every row today
carries the SAME constant string. It is NOT a real per-trade signal; do not
read a session split out of this report as evidence of anything. (The
cell-matrix / learner fan-out in the SAME close path already uses the real
``resolve_venue_session()`` — only the meta-label writer kept the old
constant. Fixing that hardcode is a separate behavior-touching change, out of
this report/pipeline-only scope.)
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Final

from polaris.core.benchmark.purged_time_split import TimedObservation

__all__ = [
    "POOLED_LABEL_GATE",
    "PER_STRATEGY_LABEL_GATE",
    "MetaLabelDistributionReport",
    "build_distribution_report",
    "load_timed_observations",
]

# ``meta_labels.holding_bars`` is stamped from elapsed wall-clock over
# 1-minute bars regardless of the strategy's own timeframe
# (``_safe_record_meta_label``: ``holding_bars = elapsed_sec // 60``) — so 60
# is the FIXED conversion back to seconds, not a strategy-specific bar
# interval.
_HOLDING_BAR_SECONDS: Final[int] = 60

# Label gate (roadmap item #10): pooled labels + per-strategy labels must both
# clear these thresholds before a 2nd-stage act/skip model may train. Purely a
# READINESS check here — no gating of sizing/exits.
POOLED_LABEL_GATE: Final[int] = 2000
PER_STRATEGY_LABEL_GATE: Final[int] = 500


@dataclass(frozen=True, slots=True)
class MetaLabelDistributionReport:
    """Pooled + per-dimension ``meta_labels`` counts and label-gate readiness."""

    total: int
    per_strategy: dict[str, int]
    per_barrier: dict[str, int]
    per_regime: dict[str, int]
    per_session: dict[str, int]
    pooled_gate_met: bool
    strategies_gate_met: dict[str, bool]


def build_distribution_report(conn: sqlite3.Connection) -> MetaLabelDistributionReport:
    """Count/distribute every existing ``meta_labels`` row (no filtering)."""
    rows = conn.execute(
        "SELECT strategy_id, barrier, regime, session FROM meta_labels"
    ).fetchall()
    total = len(rows)
    per_strategy = Counter(r[0] for r in rows)
    per_barrier = Counter(r[1] for r in rows)
    per_regime = Counter((r[2] or "") for r in rows)
    per_session = Counter((r[3] or "") for r in rows)
    return MetaLabelDistributionReport(
        total=total,
        per_strategy=dict(per_strategy),
        per_barrier=dict(per_barrier),
        per_regime=dict(per_regime),
        per_session=dict(per_session),
        pooled_gate_met=total >= POOLED_LABEL_GATE,
        strategies_gate_met={
            s: c >= PER_STRATEGY_LABEL_GATE for s, c in per_strategy.items()
        },
    )


def load_timed_observations(conn: sqlite3.Connection) -> list[TimedObservation]:
    """Build ``TimedObservation``s from ``meta_labels`` for ``purged_time_splits``.

    ``start_ts`` is INFERRED (``meta_labels`` has no entry-timestamp column):
    ``created_ts - holding_bars * _HOLDING_BAR_SECONDS``. ``end_ts`` is
    ``created_ts`` itself (the label is created at close, when the outcome
    becomes known).
    """
    rows = conn.execute(
        "SELECT label_id, created_ts, holding_bars FROM meta_labels"
    ).fetchall()
    return [
        TimedObservation(
            obs_id=str(label_id),
            start_ts=int(created_ts) - int(holding_bars) * _HOLDING_BAR_SECONDS,
            end_ts=int(created_ts),
        )
        for label_id, created_ts, holding_bars in rows
    ]
