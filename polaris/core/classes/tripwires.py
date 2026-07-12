"""fee-split v1 FLIP (item 5) — post-flip tripwires + label-churn (T4).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
the R2 red-team's T5 rollback-review tripwires + T4 label-churn HOLD
condition (codex R2 output, vault/50_research/debates/fee_split_judgment_
2026-07-10.md's follow-up debate). Every check here is AUTO-FLAG ONLY — it
computes and reports, it NEVER blocks/throttles/kills a strategy or a
transition (flow_not_block / no_block_filter_architecture). Intended
consumer: ``tools/ops/scoref_remap_refresh.py`` (same tool, item 5) and/or a
future digest panel.

5 signals:
  1. Shadow divergence — legacy-axis vs gross-axis 3-way bucket disagreement
     over the last 200 closes (or 7d), reusing ``shadow_divergence.py``'s
     existing bucket/aggregate machinery. Flag: >18% (200-window) or >20% (7d).
  2. EARN-class aggregate: rolling 250 closes gross_sum<0 AND a 95% one-sided
     LCB on gross_bps also <0 (double confirmation, not noise off one bad day).
  3. "Kill-rule" fire rate — this codebase has NO dedicated 100-trade-gross-
     kill / kill_state=KILLED writer yet (grepped, confirmed absent as of
     this build); the nearest EXISTING analog is a BENCH demotion (transition.
     evaluate_transition's SCHMITT_EARN_TO_BENCH / TRIPWIRE / PROVE_STAGNATION
     reasons persisted via last_transition_ts). Flag: today's demotion count
     > 2x the prior-14d daily average, OR >=3 distinct tracks demoted today.
  4. Remap drift — ``score_f_remap_table.stale=1`` rows (PSI/KS/near-zero-
     threshold-drift, all computed by ``remap_table.upsert_remap_entry``).
  5. Label-churn (T4) — per-track: current ``dwell < 20`` (the project's own
     window_w default) flags a track that has transitioned recently enough
     that it has not yet re-accumulated a full window's worth of evidence —
     the cheap, always-available proxy for "oscillating faster than the
     window can support" (a literal >3-flips/100-closes count would need a
     transition-history ledger this codebase does not persist yet).
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass, field

from polaris.core.classes.remap_table import resolve_thresholds
from polaris.core.classes.shadow_divergence import (
    DualScoreSample,
    ShadowDivergenceResult,
    classify_against_thresholds,
    compute_shadow_divergence,
)
from polaris.core.classes.transition import TransitionThresholds

__all__ = [
    "DIVERGENCE_200_MAX_PCT",
    "DIVERGENCE_7D_MAX_PCT",
    "EARN_ROLLING_WINDOW",
    "KILL_FIRE_RATE_MULTIPLE",
    "KILL_FIRE_RATE_MIN_TRACKS",
    "MIN_DWELL_FLOOR",
    "TripwireReport",
    "compute_tripwire_report",
]

DIVERGENCE_200_MAX_PCT = 0.18
DIVERGENCE_7D_MAX_PCT = 0.20
EARN_ROLLING_WINDOW = 250
KILL_FIRE_RATE_MULTIPLE = 2.0
KILL_FIRE_RATE_MIN_TRACKS = 3
# T4 label-churn proxy floor (see _label_churn_signal docstring) — a track
# below this dwell has transitioned too recently to have re-accumulated a
# full window's evidence yet.
MIN_DWELL_FLOOR = 20

_SEVEN_DAYS_SEC = 7 * 86_400
_FOURTEEN_DAYS_SEC = 14 * 86_400
_ONE_DAY_SEC = 86_400
_LCB95_Z = 1.645  # one-sided 95% normal-approx critical value


@dataclass(slots=True, frozen=True)
class TripwireReport:
    """One computed snapshot of all 5 T5 tripwires + T4 churn. Every field is
    a REPORT — nothing downstream of this dataclass may treat it as a
    trading decision input."""

    shadow_divergence: ShadowDivergenceResult
    shadow_divergence_flag: bool
    shadow_divergence_7d_pct: float
    shadow_divergence_7d_flag: bool
    earn_gross_sum: float
    earn_gross_lcb95: float | None
    earn_flag: bool
    demotions_today: int
    demotion_baseline_14d_avg: float
    demoted_tracks_today: int
    kill_fire_rate_flag: bool
    remap_stale_scopes: list[str] = field(default_factory=list)
    remap_drift_flag: bool = False
    churn_flagged_tracks: list[str] = field(default_factory=list)
    label_churn_flag: bool = False


def _lcb95(values: list[float]) -> float | None:
    """One-sided 95% normal-approximation LCB — a lighter-weight cousin of
    ``gross_scorer.compute_gross_lcb`` (which caps at 80% confidence by
    design, R2 item 2's cold-start ramp); this tripwire wants the stricter
    95% bar the T5 spec calls for. ``None`` on fewer than 2 samples (no
    variance to estimate)."""
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    n = float(len(values))
    return mean - _LCB95_Z * stdev / math.sqrt(n)


def _shadow_divergence_samples(
    conn: sqlite3.Connection, *, limit: int | None, since_ts: int | None,
) -> list[DualScoreSample]:
    query = (
        "SELECT venue, strategy_id, net_usd, fee_denom_usd, score_contrib "
        "FROM score_f_events"
    )
    params: tuple[object, ...] = ()
    if since_ts is not None:
        query += " WHERE closed_ts >= ?"
        params = (since_ts,)
    query += " ORDER BY closed_ts DESC"
    if limit is not None:
        query += " LIMIT ?"
        params = (*params, limit)
    rows = conn.execute(query, params).fetchall()

    legacy_defaults = TransitionThresholds()
    cache: dict[tuple[str, str], TransitionThresholds] = {}
    samples: list[DualScoreSample] = []
    for venue, strategy_id, net_usd, fee_denom_usd, score_contrib in rows:
        legacy_score = float(net_usd) / float(fee_denom_usd) if fee_denom_usd else 0.0
        key = (str(venue), str(strategy_id))
        if key not in cache:
            cache[key] = resolve_thresholds(conn, venue=key[0], strategy_id=key[1])
        t = cache[key]
        old_v = classify_against_thresholds(
            legacy_score,
            down_threshold=legacy_defaults.schmitt_bench_full,
            up_threshold=legacy_defaults.prove_stagnation,
        )
        new_v = classify_against_thresholds(
            float(score_contrib), down_threshold=t.schmitt_bench_full, up_threshold=t.prove_stagnation,
        )
        samples.append(DualScoreSample(old_verdict=old_v, new_verdict=new_v))
    return samples


def _earn_signal(conn: sqlite3.Connection) -> tuple[float, float | None, bool]:
    earn_tracks = conn.execute(
        "SELECT venue, strategy_id FROM strategy_class WHERE strategy_class = 'EARN'"
    ).fetchall()
    gross_values: list[float] = []
    for venue, strategy_id in earn_tracks:
        rows = conn.execute(
            "SELECT gross_usd FROM score_f_events WHERE venue = ? AND strategy_id = ? "
            "AND gross_usd IS NOT NULL ORDER BY closed_ts DESC LIMIT ?",
            (venue, strategy_id, EARN_ROLLING_WINDOW),
        ).fetchall()
        gross_values.extend(float(r[0]) for r in rows)
    gross_sum = sum(gross_values)
    lcb95 = _lcb95(gross_values)
    flagged = bool(gross_sum < 0.0 and lcb95 is not None and lcb95 < 0.0)
    return gross_sum, lcb95, flagged


def _kill_fire_rate_signal(conn: sqlite3.Connection, *, now_ts: int) -> tuple[int, float, int, bool]:
    today_start = now_ts - _ONE_DAY_SEC
    baseline_start = now_ts - _FOURTEEN_DAYS_SEC
    today_rows = conn.execute(
        "SELECT strategy_id FROM strategy_class WHERE last_transition_ts >= ?", (today_start,),
    ).fetchall()
    demotions_today = len(today_rows)
    demoted_tracks_today = len({str(r[0]) for r in today_rows})
    baseline_rows = conn.execute(
        "SELECT COUNT(*) FROM strategy_class WHERE last_transition_ts >= ? AND last_transition_ts < ?",
        (baseline_start, today_start),
    ).fetchone()
    baseline_avg = (int(baseline_rows[0]) if baseline_rows else 0) / 14.0
    flagged = bool(
        (baseline_avg > 0.0 and demotions_today > KILL_FIRE_RATE_MULTIPLE * baseline_avg)
        or demoted_tracks_today >= KILL_FIRE_RATE_MIN_TRACKS
    )
    return demotions_today, baseline_avg, demoted_tracks_today, flagged


def _remap_drift_signal(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT scope_key FROM score_f_remap_table WHERE stale = 1"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _label_churn_signal(conn: sqlite3.Connection) -> list[str]:
    """Flag tracks whose CURRENT dwell sits below :data:`MIN_DWELL_FLOOR` —
    the cheap, always-available proxy for T4's "label flips faster than one
    window's evidence" concern (a track that has transitioned recently
    enough that it has not yet re-accumulated a full window's dwell)."""
    rows = conn.execute(
        "SELECT venue, strategy_id FROM strategy_class WHERE dwell < ?", (MIN_DWELL_FLOOR,),
    ).fetchall()
    return [f"{venue}:{strategy_id}" for venue, strategy_id in rows]


def compute_tripwire_report(conn: sqlite3.Connection, *, now_ts: int) -> TripwireReport:
    """Compute all 5 T5 tripwires + T4 label-churn in one pass. Read-only,
    fail-open in spirit (every sub-signal degrades to "no evidence yet ->
    not flagged" on an empty population, never raises)."""
    samples_200 = _shadow_divergence_samples(conn, limit=200, since_ts=None)
    div_200 = compute_shadow_divergence(samples_200, min_n=1)
    div_200_flag = div_200.behavior_divergence_pct > DIVERGENCE_200_MAX_PCT

    samples_7d = _shadow_divergence_samples(conn, limit=None, since_ts=now_ts - _SEVEN_DAYS_SEC)
    div_7d = compute_shadow_divergence(samples_7d, min_n=1)
    div_7d_flag = div_7d.behavior_divergence_pct > DIVERGENCE_7D_MAX_PCT

    earn_gross_sum, earn_lcb95, earn_flag = _earn_signal(conn)
    demotions_today, baseline_avg, demoted_tracks_today, kill_flag = _kill_fire_rate_signal(
        conn, now_ts=now_ts,
    )
    stale_scopes = _remap_drift_signal(conn)
    churn_tracks = _label_churn_signal(conn)

    return TripwireReport(
        shadow_divergence=div_200,
        shadow_divergence_flag=div_200_flag,
        shadow_divergence_7d_pct=div_7d.behavior_divergence_pct,
        shadow_divergence_7d_flag=div_7d_flag,
        earn_gross_sum=earn_gross_sum,
        earn_gross_lcb95=earn_lcb95,
        earn_flag=earn_flag,
        demotions_today=demotions_today,
        demotion_baseline_14d_avg=baseline_avg,
        demoted_tracks_today=demoted_tracks_today,
        kill_fire_rate_flag=kill_flag,
        remap_stale_scopes=stale_scopes,
        remap_drift_flag=bool(stale_scopes),
        churn_flagged_tracks=churn_tracks,
        label_churn_flag=bool(churn_tracks),
    )
