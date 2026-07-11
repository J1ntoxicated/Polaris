"""ADR-011/012 — OFFLINE probe calibration reader (structural hardening #4).

DEMO/PAPER. This module CLOSES the self-evolution loop on the READ side only:
an OFFLINE / cron consumer over ``v_probe_outcomes`` that MEASURES, per
``(kind, regime, action)``, the would-be (decision-time MFE) vs realized-R /
giveback separation, applies the ``measurement_resets`` FORWARD filter, and
emits a Vault digest (+ an OPTIONAL ``probe_calibration`` table).

HARD RAILS (non-negotiable, ADR-011 / ADR-012):
  * **NEVER auto-applied.** Nothing here writes a live knob, threshold, or ENV.
    The output feeds ``/debate`` only — a Jin-surface decision (rank 16). When
    the separation is within noise the reader reports
    ``insufficient separation, STAY observe``.
  * **NEVER imported by an in-loop path.** ``run_precise_exit`` /
    ``_production_*`` stay byte-identical; no in-loop module imports this. It is
    invoked OFFLINE (CLI / cron) over the ``data/probes.sqlite`` sidecar.
  * **EXCURSION ruler only.** Every R it reads (``mfe_r_final`` / ``realized_pnl_r``
    / ``giveback_r``) is the per-trade-ATR EXCURSION ruler stamped ``unit_tag=
    'excursion'`` (hardening #6). It is NEVER joined to the per-stream ledger R.

The grouping key honours the requested ``(kind, regime, action)`` signature:
``action`` comes from ``v_probe_outcomes``; ``kind`` is the DOMINANT contributing
probe kind (max ``|lean|*confidence``) joined from ``probe_readings`` by
``(position_id, ts)``; ``regime`` IS now persisted by ``log_probe_decisions``
(backgate-plan W2-d) but this reader does NOT read it yet this wave — the
column/reader split (W3) is deferred, so every group still degrades to
``"unknown"`` (the digest states this honestly rather than inventing a split).
FAIL-OPEN: a missing table / sqlite error yields an empty result.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass, field
from statistics import mean, pstdev

__all__ = [
    "CalibrationGroup",
    "CalibrationResult",
    "read_calibration",
    "render_digest",
    "write_calibration_table",
]

# Minimum closed-outcome count per group before a separation is reported at all
# (below this the group is NOISE, never a signal). /debate-tunable, never a live
# trading knob — this only gates what the OFFLINE digest is willing to claim.
_MIN_GROUP_N: int = 20
# A separation is "decisive" only when the giveback gap between the best- and
# worst-giveback group exceeds this excursion-R band AND clears one pooled
# standard deviation (so a thin-but-lucky split still reads as noise).
_MIN_SEPARATION_R: float = 0.25

_STAY_OBSERVE: str = "insufficient separation, STAY observe"
_SEPARATION_FOUND: str = "separation observed — surface to /debate (never auto-apply)"


@dataclass(frozen=True, slots=True)
class CalibrationGroup:
    """One ``(kind, regime, action)`` bucket of closed probe decisions.

    All R fields are the EXCURSION ruler (``unit`` == ``"excursion"``); they are
    NEVER the per-stream ledger R. ``mean_giveback_r`` = mean(``mfe_r_final`` −
    ``realized_pnl_r``) — the would-be peak minus the realized exit.
    """

    kind: str
    regime: str
    action: str
    n: int
    mean_realized_r: float
    mean_mfe_r: float
    mean_giveback_r: float
    std_giveback_r: float
    unit: str = "excursion"


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """The OFFLINE calibration read — groups + a STAY/surface verdict.

    ``verdict`` is ONE of the two module constants. It is advisory for ``/debate``
    only; this object never reaches a live knob.
    """

    groups: list[CalibrationGroup]
    verdict: str
    reset_ts: int
    total_n: int = 0
    unit: str = "excursion"
    notes: list[str] = field(default_factory=list)


# Flat per-CLOSED-decision read: each outcome row tagged with its DOMINANT
# contributing probe kind (max |lean|*confidence per position_id+ts) joined from
# probe_readings. v_probe_outcomes already filters realized_pnl_r IS NOT NULL.
# Giveback (mfe - realized) and the (kind, regime='unknown', action) buckets are
# computed in Python so the unit boundary is never crossed in SQL.
_CALIBRATION_SQL: str = """
WITH dom AS (
    SELECT r.position_id AS position_id, r.ts AS ts, r.kind AS kind,
           ROW_NUMBER() OVER (
               PARTITION BY r.position_id, r.ts
               ORDER BY ABS(r.lean) * r.confidence DESC, r.probe_id
           ) AS rn
    FROM probe_readings r
)
SELECT COALESCE(dom.kind, 'unknown') AS kind,
       o.action AS action,
       o.realized_pnl_r AS realized_pnl_r,
       o.mfe_r_final AS mfe_r_final
FROM v_probe_outcomes o
LEFT JOIN dom ON dom.position_id = o.position_id AND dom.ts = o.ts AND dom.rn = 1
WHERE o.ts >= ?
  AND o.realized_pnl_r IS NOT NULL
  AND o.mfe_r_final IS NOT NULL
"""


def read_calibration(
    conn: sqlite3.Connection | None, *, reset_ts: int
) -> CalibrationResult:
    """Read ``v_probe_outcomes`` → per-(kind, regime, action) separation groups.

    ``reset_ts`` is the FORWARD measurement-window anchor (``measurement_resets``
    newest ``reset_ts``); rows with ``ts < reset_ts`` are excluded so the read
    reflects the CURRENT edge, never the diluted all-time blend. FAIL-OPEN — a
    missing table / sqlite error returns an empty STAY-observe result.
    """
    if conn is None:
        return CalibrationResult([], _STAY_OBSERVE, int(reset_ts))
    rows: list[tuple[str, str, float, float]] = []
    with contextlib.suppress(sqlite3.Error):
        rows = list(conn.execute(_CALIBRATION_SQL, (int(reset_ts),)))
    # Bucket the flat per-decision rows by (kind, regime='unknown', action).
    buckets: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    for kind, action, realized_r, mfe_r in rows:
        buckets.setdefault((str(kind), "unknown", str(action)), []).append(
            (float(realized_r), float(mfe_r))
        )

    groups: list[CalibrationGroup] = []
    for (kind, regime, action), pairs in sorted(buckets.items()):
        realized = [p[0] for p in pairs]
        mfe = [p[1] for p in pairs]
        givebacks = [m - r for r, m in pairs]
        if len(pairs) < _MIN_GROUP_N:
            continue  # below the noise floor — not a reportable group
        groups.append(
            CalibrationGroup(
                kind=kind, regime=regime, action=action, n=len(pairs),
                mean_realized_r=mean(realized), mean_mfe_r=mean(mfe),
                mean_giveback_r=mean(givebacks),
                std_giveback_r=pstdev(givebacks) if len(givebacks) > 1 else 0.0,
            )
        )

    total_n = sum(g.n for g in groups)
    verdict = _verdict(groups)
    notes = [
        "regime IS persisted in probe_decisions (backgate-plan W2-d) but this "
        "reader does not split by it yet — bucketed as 'unknown' pending the "
        "W3 calibrator read-side wiring.",
        "EXCURSION ruler only — never joined to the per-stream ledger R.",
        "OFFLINE / observe-only — NEVER auto-applied to a live knob (ADR-011/012); "
        "feeds /debate (rank 16) only.",
    ]
    return CalibrationResult(
        groups=groups, verdict=verdict, reset_ts=int(reset_ts),
        total_n=total_n, notes=notes,
    )


def _verdict(groups: list[CalibrationGroup]) -> str:
    """STAY-observe unless a decisive, above-noise giveback spread exists.

    Decisive = the best-vs-worst group giveback gap clears ``_MIN_SEPARATION_R``
    AND one pooled std. Conservative by construction: a thin/noisy split stays
    observe so the offline reader never over-claims a separation into /debate.
    """
    if len(groups) < 2:
        return _STAY_OBSERVE
    givebacks = [g.mean_giveback_r for g in groups]
    spread = max(givebacks) - min(givebacks)
    pooled_std = mean(g.std_giveback_r for g in groups)
    if spread >= _MIN_SEPARATION_R and spread >= pooled_std:
        return _SEPARATION_FOUND
    return _STAY_OBSERVE


def render_digest(result: CalibrationResult) -> str:
    """Render the calibration read as a Vault-ready Markdown digest.

    Display-only. States the EXCURSION ruler explicitly, the forward-window ts,
    the per-group giveback split, and the STAY/surface verdict. NEVER claims a
    stream-R figure and NEVER an auto-apply (the digest is /debate fodder).
    """
    lines: list[str] = [
        "# Probe calibration digest (OFFLINE, observe-only) [excursion R]",
        "",
        f"- forward window: ts >= {result.reset_ts} (measurement_resets anchor)",
        f"- groups (n >= {_MIN_GROUP_N}): {len(result.groups)} | "
        f"total closed decisions: {result.total_n}",
        f"- **verdict: {result.verdict}**",
        "- ruler: EXCURSION (per-trade ATR) — NEVER auto-applied to a live knob "
        "(ADR-011/012); /debate only.",
        "",
    ]
    if result.groups:
        lines.append(
            "| kind | regime | action | n | mean realized R | mean MFE R | "
            "mean giveback R | std giveback |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for g in result.groups:
            lines.append(
                f"| {g.kind} | {g.regime} | {g.action} | {g.n} | "
                f"{g.mean_realized_r:+.3f} | {g.mean_mfe_r:+.3f} | "
                f"{g.mean_giveback_r:+.3f} | {g.std_giveback_r:.3f} |"
            )
        lines.append("")
    for note in result.notes:
        lines.append(f"> {note}")
    return "\n".join(lines)


# Optional sidecar table — a durable record of each OFFLINE read (NOT a live
# knob). Kept in data/probes.sqlite (same sidecar) so the live DB is untouched.
_CALIBRATION_TABLE_DDL: str = """
CREATE TABLE IF NOT EXISTS probe_calibration (
    read_ts        INTEGER NOT NULL,
    reset_ts       INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    regime         TEXT NOT NULL,
    action         TEXT NOT NULL,
    n              INTEGER NOT NULL,
    mean_realized_r REAL NOT NULL,
    mean_mfe_r     REAL NOT NULL,
    mean_giveback_r REAL NOT NULL,
    std_giveback_r REAL NOT NULL,
    unit_tag       TEXT NOT NULL DEFAULT 'excursion',
    verdict        TEXT NOT NULL
);
"""


def write_calibration_table(
    conn: sqlite3.Connection | None, result: CalibrationResult
) -> None:
    """Persist one row per group into the OPTIONAL ``probe_calibration`` table.

    FAIL-OPEN (mirrors the tuning-log writers): ``conn=None`` / any sqlite error
    is a no-op. This is a durable OFFLINE record only — it is NEVER read by an
    in-loop path and never threads into a live knob.
    """
    if conn is None or not result.groups:
        return
    read_ts = int(time.time())
    with contextlib.suppress(sqlite3.Error):
        conn.execute(_CALIBRATION_TABLE_DDL)
        conn.executemany(
            "INSERT INTO probe_calibration "
            "(read_ts, reset_ts, kind, regime, action, n, mean_realized_r, "
            " mean_mfe_r, mean_giveback_r, std_giveback_r, unit_tag, verdict) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    read_ts, result.reset_ts, g.kind, g.regime, g.action, g.n,
                    g.mean_realized_r, g.mean_mfe_r, g.mean_giveback_r,
                    g.std_giveback_r, g.unit, result.verdict,
                )
                for g in result.groups
            ],
        )
