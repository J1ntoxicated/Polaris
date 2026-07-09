"""Gate-kill counterfactual VALUE reader — offline (gate_id, cohort) digest.

DEMO/PAPER · AGGRESSIVE · flow_not_block · in-loop GPT = 0.

``gate_kill_counterfactuals`` (07-02 BUILD, ``_production_counterfactual.py``)
already records one row per G3/G4 GPT KILL/PASS decision, with the 1h/4h/24h
forward mark SELF-REFRESHING as bars arrive (``sweep_forward_marks``). At 15.7k+
rows it had ZERO aggregate readers — the write side existed, the feedback loop
never closed (07-02 root-cause #4). This module is that missing READ side: it
groups the table by ``(gate_id, cohort)`` — cohort = the decision-time regime —
and compares the KILL cohort's mean forward return to the PASS cohort's, so a
human + ``/debate`` can finally ask "is this gate killing signals that would
have WON?" with data instead of a guess.

Hard mandate guarantees (every one asserted by the tests):

  * **Read-only** — only ``SELECT``; no INSERT/UPDATE/DELETE anywhere in this
    module. It observes; it never mutates the bot's state or a gate threshold.
  * **Never auto-applied** — every :class:`KillValueHint` carries
    ``auto_apply=False`` / ``debate_gated=True``. A human + ``/debate`` decides
    whether a gate threshold moves; this module only surfaces the evidence.
  * **Stratified** — a ``(gate_id, cohort)`` group only surfaces once BOTH its
    KILL side and its PASS side clear ``min_cohort`` samples (07-02's
    unresolved sample-bias item) — an unbalanced group (e.g. 50 KILLs vs 1
    PASS) is too biased to compare and is held back.
  * **A digest, not a gate** — pure data (means, sample counts, the derived
    ``separation`` / ``anti_edge`` flags). No ``tradeable`` / ``block`` field;
    it cannot skip / size-cut / veto anything. flow_not_block preserved.

PURE read-side: no GPT, no live-DB writes, no network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "DEFAULT_MIN_COHORT",
    "KillValueHint",
    "compute_kill_value_hints",
    "gate_kill_value_panel",
]

# Stratified surfacing floor: EACH side (KILL and PASS) of a (gate_id, cohort)
# group needs at least this many resolved (fwd_r_24h IS NOT NULL) rows before
# the digest reports it. Below the floor either side's mean is too noisy to
# debate — held back from the SURFACE only, never a live trade/gate effect.
DEFAULT_MIN_COHORT: Final[int] = 5


@dataclass(frozen=True, slots=True)
class KillValueHint:
    """One (gate_id × cohort) row of the offline gate-kill-value digest.

    ``mean_killed_fwd_r`` / ``mean_passed_fwd_r`` are the FEE-ADJUSTED 24h
    forward-R means (``fwd_r_24h - cost_r`` — the same ``fee_adj_fwd_r``
    convention as ``v_g34_cohort_outcomes``, apples-to-apples with the KILL
    counterfactual's ignore-fill-reality upper bound). ``separation`` =
    ``mean_passed_fwd_r - mean_killed_fwd_r``: positive means the gate
    discriminates correctly (kills the worse cohort); negative or
    ``anti_edge=True`` (``mean_killed_fwd_r > 0`` — the gate killed signals
    that would have WON) flags an anti-edge candidate for ``/debate``.

    ``auto_apply`` is always ``False`` and ``debate_gated`` always ``True`` —
    this is evidence for a ``/debate``, NEVER a knob the runtime applies on
    its own.
    """

    gate_id: int
    cohort: str
    n_killed: int
    n_passed: int
    mean_killed_fwd_r: float
    mean_passed_fwd_r: float
    separation: float
    anti_edge: bool
    auto_apply: bool = False
    debate_gated: bool = True


def compute_kill_value_hints(
    conn: sqlite3.Connection,
    *,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> list[KillValueHint]:
    """Read + digest ``gate_kill_counterfactuals`` per ``(gate_id, regime)``.

    Only rows with a self-refreshed ``fwd_r_24h`` (i.e. the 24h forward mark
    has already resolved) count. Returns one :class:`KillValueHint` per
    ``(gate_id, regime)`` group whose KILL side AND PASS side both have at
    least ``min_cohort`` resolved rows, ordered worst-offender first
    (``anti_edge`` groups first, then the most damaging ``mean_killed_fwd_r``,
    then sample size as a tiebreak). FAIL-OPEN: a missing table / column or
    any ``sqlite3.Error`` yields ``[]`` (best-effort telemetry, never a
    runtime dependency).

    This function only ``SELECT``s — it never writes. The returned hints are
    surface-only (``auto_apply=False`` / ``debate_gated=True``); applying any
    of them is a separate, ``/debate``-gated human decision.
    """
    try:
        rows = conn.execute(
            """
            SELECT gate_id, regime, decision,
                   COUNT(*)                        AS n,
                   AVG(fwd_r_24h - cost_r)          AS mean_fee_adj_fwd_r
            FROM gate_kill_counterfactuals
            WHERE fwd_r_24h IS NOT NULL
            GROUP BY gate_id, regime, decision
            """
        ).fetchall()
    except sqlite3.Error:
        return []

    by_group: dict[tuple[int, str], dict[str, tuple[int, float]]] = {}
    for gate_id, regime, decision, n, mean_r in rows:
        cohort = str(regime) if regime is not None else "unknown"
        key = (int(gate_id), cohort)
        by_group.setdefault(key, {})[str(decision)] = (
            int(n), float(mean_r) if mean_r is not None else 0.0,
        )

    hints: list[KillValueHint] = []
    for (gate_id, cohort), sides in by_group.items():
        killed = sides.get("KILL")
        passed = sides.get("PASS")
        if killed is None or passed is None:
            continue  # need BOTH cohorts to compare — half a pair is noise
        n_killed, mean_killed = killed
        n_passed, mean_passed = passed
        if n_killed < min_cohort or n_passed < min_cohort:
            continue  # stratified floor — either side too thin to debate
        separation = mean_passed - mean_killed
        hints.append(
            KillValueHint(
                gate_id=gate_id, cohort=cohort,
                n_killed=n_killed, n_passed=n_passed,
                mean_killed_fwd_r=mean_killed, mean_passed_fwd_r=mean_passed,
                separation=separation, anti_edge=mean_killed > 0.0,
            )
        )

    hints.sort(
        key=lambda h: (
            not h.anti_edge, -h.mean_killed_fwd_r, -(h.n_killed + h.n_passed),
        )
    )
    return hints


def gate_kill_value_panel(
    conn: sqlite3.Connection,
    *,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> dict[str, Any]:
    """Dashboard-facing wrapper — read-only, empty-degrades like ``confidence.

    replay_block``. Returns ``{"present": False}`` when no cohort clears the
    stratified floor (missing table, empty table, or every group too thin);
    otherwise ``{"present": True, "auto_apply": False, "hints": [...]}`` with
    one plain dict per :class:`KillValueHint`. Consumption is display + the
    ``/debate`` evidence surface ONLY — this function is never read by any
    live gate/sizing/exit path.
    """
    hints = compute_kill_value_hints(conn, min_cohort=min_cohort)
    if not hints:
        return {"present": False}
    return {
        "present": True,
        "auto_apply": False,
        "hints": [
            {
                "gate_id": h.gate_id,
                "cohort": h.cohort,
                "n_killed": h.n_killed,
                "n_passed": h.n_passed,
                "mean_killed_fwd_r": h.mean_killed_fwd_r,
                "mean_passed_fwd_r": h.mean_passed_fwd_r,
                "separation": h.separation,
                "anti_edge": h.anti_edge,
            }
            for h in hints
        ],
    }
