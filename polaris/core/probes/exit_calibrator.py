"""backgate-plan W2-d (design-exit-matrix.md §A) — OFFLINE exit-parameter
counterfactual calibrator.

DEMO/PAPER. Completes the ADR-012 Slice-2 "shadow-compare" the ADR named but
never measured before any ``trail_only`` flip: reads the CLOSED-position rows
already accumulated in ``v_probe_outcomes`` (``mfe_r_final`` / ``mae_r_final`` /
``realized_pnl_r`` — the EXCURSION ruler, hardening #6) and counterfactually
rescores a SWEEP of candidate harvest / protect-floor R thresholds against what
actually happened, purely to REPORT which candidate would have banked more R.

HARD RAILS (mirrors ``calibration.py``):
  * **NEVER auto-applied.** Nothing here writes a live knob, threshold, or ENV.
    The output feeds ``/debate`` only (rank 16).
  * **NEVER imported by an in-loop path.** OFFLINE (CLI / cron) over the
    ``data/probes.sqlite`` sidecar only.
  * **EXCURSION ruler only** — never joined to the per-stream ledger R.

COUNTERFACTUAL MODEL (an explicit simplification, not a bar-by-bar replay — only
the FINAL ``mfe_r_final`` / ``mae_r_final`` / ``realized_pnl_r`` scalars are in
hand, not the intra-trade sequence):
  * **Harvest candidate H (R)**: a harvest exit at ``+H`` fires the moment the
    trade's MFE reaches H. ``counterfactual_r = H`` if ``mfe_r_final >= H``,
    else the trade's own ``realized_pnl_r`` (H was never reached — harvest is
    moot, the real outcome stands).
  * **Protect candidate F (R)**: a protect-floor exit at ``-F`` fires the
    moment the trade's MAE breaches ``-F``. ``counterfactual_r = -F`` if
    ``mae_r_final <= -F`` AND ``realized_pnl_r < -F`` (the floor was breached
    AND the real exit already banked WORSE than the floor would have), else
    ``realized_pnl_r`` (the floor was never breached, or the real exit already
    banked at least as well — the floor changes nothing).
  Both IGNORE intra-trade sequencing (did the MFE happen before the MAE, or
  after?) — a real bar-by-bar replay would need the full excursion path, which
  the sidecar does not persist. The digest states this caveat explicitly, same
  as ``calibration.py``'s EXCURSION-ruler note.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass, field
from statistics import mean

__all__ = [
    "DEFAULT_HARVEST_CANDIDATES_R",
    "DEFAULT_PROTECT_CANDIDATES_R",
    "ExitCalibrationResult",
    "ParamScore",
    "read_exit_calibration",
    "render_exit_digest",
]

# Minimum closed-position count before a candidate's score is reported at all
# (mirrors calibration.py's noise floor — below this the sweep point is NOISE).
_MIN_PARAM_N: int = 20

# Candidate sweep grids (R units), /debate-tunable — NEVER auto-applied. Chosen
# to straddle the calibrated bar/equity rungs already live
# (``exit_params.EXIT_BAR_MFE_BEP_R``/``EXIT_BAR_MFE_PROTECT_R`` default
# 0.30/0.45) so the report reads directly against the CURRENT knobs.
DEFAULT_HARVEST_CANDIDATES_R: tuple[float, ...] = (0.20, 0.30, 0.45, 0.60, 0.80, 1.00)
DEFAULT_PROTECT_CANDIDATES_R: tuple[float, ...] = (0.20, 0.30, 0.45, 0.60, 0.80, 1.00)


@dataclass(frozen=True, slots=True)
class ParamScore:
    """One candidate parameter's counterfactual score against the actual mean.

    ``delta_r`` = ``mean_counterfactual_r - mean_actual_r`` — positive means the
    candidate would have banked MORE mean R than what actually happened.
    """

    param_r: float
    n: int
    mean_actual_r: float
    mean_counterfactual_r: float
    delta_r: float
    unit: str = "excursion"


@dataclass(frozen=True, slots=True)
class ExitCalibrationResult:
    """The OFFLINE exit-parameter calibration read — two candidate sweeps."""

    harvest: list[ParamScore]
    protect: list[ParamScore]
    total_n: int
    reset_ts: int
    unit: str = "excursion"
    notes: list[str] = field(default_factory=list)


# One row per CLOSED position (deduped — v_probe_outcomes carries one row per
# probe-evaluation TICK, all sharing the same backfilled outcome triple for a
# given position_id, since backfill_probe_outcome overwrites every still-open
# decision row for that position at once). GROUP BY collapses that back to the
# per-position scalar the counterfactual sweep needs.
_OUTCOME_SQL: str = """
SELECT MAX(mfe_r_final), MAX(mae_r_final), MAX(realized_pnl_r)
FROM v_probe_outcomes
WHERE ts >= ?
  AND realized_pnl_r IS NOT NULL
  AND mfe_r_final IS NOT NULL
  AND mae_r_final IS NOT NULL
GROUP BY position_id
"""


def _read_outcome_rows(
    conn: sqlite3.Connection | None, *, reset_ts: int
) -> list[tuple[float, float, float]]:
    """Read one ``(mfe_r_final, mae_r_final, realized_pnl_r)`` row per closed
    position since ``reset_ts``. FAIL-OPEN: ``conn=None`` / any sqlite error →
    empty list.
    """
    if conn is None:
        return []
    rows: list[tuple[float, float, float]] = []
    with contextlib.suppress(sqlite3.Error):
        rows = list(conn.execute(_OUTCOME_SQL, (int(reset_ts),)))
    return [(float(mfe), float(mae), float(real)) for mfe, mae, real in rows]


def _score_harvest(
    rows: list[tuple[float, float, float]], candidates: tuple[float, ...]
) -> list[ParamScore]:
    n = len(rows)
    if n < _MIN_PARAM_N:
        return []
    actual = [real for _, _, real in rows]
    mean_actual = mean(actual)
    out: list[ParamScore] = []
    for h in candidates:
        cf = [h if mfe >= h else real for mfe, _, real in rows]
        mean_cf = mean(cf)
        out.append(
            ParamScore(
                param_r=h, n=n, mean_actual_r=mean_actual,
                mean_counterfactual_r=mean_cf, delta_r=mean_cf - mean_actual,
            )
        )
    return out


def _score_protect(
    rows: list[tuple[float, float, float]], candidates: tuple[float, ...]
) -> list[ParamScore]:
    n = len(rows)
    if n < _MIN_PARAM_N:
        return []
    actual = [real for _, _, real in rows]
    mean_actual = mean(actual)
    out: list[ParamScore] = []
    for f in candidates:
        cf = [
            -f if (mae <= -f and real < -f) else real
            for _, mae, real in rows
        ]
        mean_cf = mean(cf)
        out.append(
            ParamScore(
                param_r=f, n=n, mean_actual_r=mean_actual,
                mean_counterfactual_r=mean_cf, delta_r=mean_cf - mean_actual,
            )
        )
    return out


def read_exit_calibration(
    conn: sqlite3.Connection | None,
    *,
    reset_ts: int,
    harvest_candidates: tuple[float, ...] = DEFAULT_HARVEST_CANDIDATES_R,
    protect_candidates: tuple[float, ...] = DEFAULT_PROTECT_CANDIDATES_R,
) -> ExitCalibrationResult:
    """Read ``v_probe_outcomes`` → harvest + protect counterfactual sweeps.

    ``reset_ts`` is the FORWARD measurement-window anchor (mirrors
    ``calibration.read_calibration``) — rows with ``ts < reset_ts`` are
    excluded. Below ``_MIN_PARAM_N`` closed positions, BOTH sweeps report empty
    (noise floor, never a thin-sample claim). FAIL-OPEN.
    """
    rows = _read_outcome_rows(conn, reset_ts=reset_ts)
    notes = [
        "counterfactual model: FIRST-TOUCH of the candidate threshold assumed "
        "to trigger exit — intra-trade sequencing (MFE-before-MAE or reverse) "
        "is NOT replayed (only the final mfe_r/mae_r/realized_pnl_r scalars "
        "are in the sidecar). Read as a directional signal, not a precise sim.",
        "ASYMMETRY (review): harvest is pure first-touch, but protect is "
        "OUTCOME-GATED (cf=-f only when mae<=-f AND realized<-f) — a dip-below-"
        "then-recover trade keeps its realized R, so protect deltas read "
        "systematically OPTIMISTIC vs harvest.",
        "EXCURSION ruler only — never joined to the per-stream ledger R.",
        "OFFLINE / observe-only — NEVER auto-applied to a live knob; feeds "
        "/debate (rank 16) only.",
    ]
    return ExitCalibrationResult(
        harvest=_score_harvest(rows, harvest_candidates),
        protect=_score_protect(rows, protect_candidates),
        total_n=len(rows),
        reset_ts=int(reset_ts),
        notes=notes,
    )


def _render_table(title: str, scores: list[ParamScore]) -> list[str]:
    lines = [f"### {title}", ""]
    if not scores:
        lines.append(f"_below n>={_MIN_PARAM_N} noise floor — no sweep reported._")
        lines.append("")
        return lines
    lines.append("| candidate R | n | mean actual R | mean counterfactual R | delta R |")
    lines.append("|---|---|---|---|---|")
    for s in scores:
        lines.append(
            f"| {s.param_r:.2f} | {s.n} | {s.mean_actual_r:+.3f} | "
            f"{s.mean_counterfactual_r:+.3f} | {s.delta_r:+.3f} |"
        )
    lines.append("")
    return lines


def render_exit_digest(result: ExitCalibrationResult) -> str:
    """Render the exit-parameter calibration read as a Vault-ready digest.

    Display-only, mirrors ``calibration.render_digest``'s honesty conventions:
    states the EXCURSION ruler, the forward-window ts, and NEVER claims an
    auto-apply.
    """
    lines: list[str] = [
        "# Exit-parameter calibration digest (OFFLINE, observe-only) [excursion R]",
        "",
        f"- forward window: ts >= {result.reset_ts} (measurement_resets anchor)",
        f"- closed positions in window: {result.total_n} "
        f"(sweep reported only if >= {_MIN_PARAM_N})",
        "- ruler: EXCURSION (per-trade ATR) — NEVER auto-applied to a live knob; "
        "/debate only.",
        "",
    ]
    lines += _render_table("Harvest candidates (+H R)", result.harvest)
    lines += _render_table("Protect-floor candidates (-F R)", result.protect)
    for note in result.notes:
        lines.append(f"> {note}")
    return "\n".join(lines)
