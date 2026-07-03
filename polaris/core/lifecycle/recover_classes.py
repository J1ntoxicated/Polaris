"""pts-classes (Performance-Tiered Strategy classes) — storage layer (group A).

Split out of ``recover.py`` (file-size cap, ≤500 LOC) — hydrate/bootstrap for
the ``strategy_class`` table only; re-exported from ``recover`` so existing
import sites (``from polaris.core.lifecycle.recover import hydrate_strategy_class``)
are unaffected.

``strategy_class`` is a capital-ROUTING record (which class / track_R cap a
(venue, strategy_id) currently holds), never a block/reject filter — a
BENCH- or KILL-classed strategy keeps signaling/learning/shadow-pricing
(aggressive_always_profit / no_block_filter_architecture). Restart must NOT
reset this state (hydrate reads the persisted row verbatim, mirroring
``hydrate_open_positions`` / ``hydrate_last_entry_by_key`` in ``recover.py``).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ScoreFFn",
    "StrategyClassRow",
    "bootstrap_replay_strategy_class",
    "hydrate_strategy_class",
]


@dataclass(slots=True)
class StrategyClassRow:
    """One persisted ``strategy_class`` row (venue, strategy_id) PK)."""

    venue: str
    strategy_id: str
    strategy_class: str
    window_w: int
    f_track_cap: float
    dwell: int
    epoch_id: int
    last_transition_ts: int
    kill_state: str
    ladder_step: int
    open_lifecycle_id: str
    qty: float
    cum_fees: float
    cum_pnl: float
    intent_ring: list[float]
    shadow_ring: list[float]
    probe_fee_24h: float


def hydrate_strategy_class(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], StrategyClassRow]:
    """Restore every persisted ``strategy_class`` row keyed by (venue, strategy_id).

    Pure read — a restart must reproduce IDENTICAL state to the pre-restart
    in-memory view (no re-derivation, no reset-to-fresh-DB-shape). The ring
    buffers are stored as JSON TEXT (see ``schema_ddl_classes.py`` docstring)
    and decoded back to ``list[float]`` here; a corrupt/legacy-empty value
    degrades to ``[]`` rather than raising (flow_not_block — a hydrate
    failure on one row must not abort boot).
    """
    rows = conn.execute(
        """
        SELECT venue, strategy_id, strategy_class, window_w, f_track_cap,
               dwell, epoch_id, last_transition_ts, kill_state, ladder_step,
               open_lifecycle_id, qty, cum_fees, cum_pnl, intent_ring,
               shadow_ring, probe_fee_24h
        FROM strategy_class
        """
    ).fetchall()
    out: dict[tuple[str, str], StrategyClassRow] = {}
    for r in rows:
        venue, strategy_id = str(r[0]), str(r[1])
        out[(venue, strategy_id)] = StrategyClassRow(
            venue=venue,
            strategy_id=strategy_id,
            strategy_class=str(r[2]),
            window_w=int(r[3]),
            f_track_cap=float(r[4]),
            dwell=int(r[5]),
            epoch_id=int(r[6]),
            last_transition_ts=int(r[7]),
            kill_state=str(r[8]),
            ladder_step=int(r[9]),
            open_lifecycle_id=str(r[10]),
            qty=float(r[11]),
            cum_fees=float(r[12]),
            cum_pnl=float(r[13]),
            intent_ring=_decode_ring(r[14]),
            shadow_ring=_decode_ring(r[15]),
            probe_fee_24h=float(r[16]),
        )
    return out


def _decode_ring(raw: Any) -> list[float]:
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return list(value) if isinstance(value, list) else []


# score_F is owned by the classifier group (group B) — injected here as a
# callable so this storage module has zero import coupling to it.
# Signature: (conn, venue, strategy_id, lookback_days) -> float score.
ScoreFFn = Callable[[sqlite3.Connection, str, str, int], float]

# score_F > this seeds the strategy straight into EARN at bootstrap (the
# "current earner = EARN immediately" mandate) rather than a probation
# class. 0.0 is the natural break-even boundary for a score already scaled
# by the classifier's own units; the classifier group owns re-tuning this
# via its own constant if score_F's scale changes.
_BOOTSTRAP_EARN_THRESHOLD = 0.0

# score_F < this seeds the strategy straight into BENCH at bootstrap (a
# proven loser over the replay window must not enter the live-probe/PROVE
# pool and consume probe fee budget — fee-bleed disease this feature exists
# to cure). Set strictly below _BOOTSTRAP_EARN_THRESHOLD so a genuine PROVE
# band exists between the two (SSOT S8 three-way outcome: current earner ->
# EARN, proven loser -> BENCH, everyone else in the middle -> PROVE — never
# a two-outcome EARN/PROVE collapse). -1.0 is a placeholder break-even-minus
# margin on score_F's own break-even-at-0.0 scale; the classifier group
# owns re-tuning this (and the EARN threshold) via its own constants once
# score_F's real distribution/scale is established.
_BOOTSTRAP_BENCH_THRESHOLD = -1.0


def bootstrap_replay_strategy_class(
    conn: sqlite3.Connection,
    *,
    candidates: Sequence[tuple[str, str]],
    score_f: ScoreFFn,
    lookback_days: int,
    now_ts: int,
) -> None:
    """Seed an initial ``strategy_class`` row for each NOT-YET-TRACKED candidate.

    Bootstrap replay: for every ``(venue, strategy_id)`` candidate without an
    existing row, replays ``lookback_days`` of history through the injected
    ``score_f`` and seeds a three-way outcome (SSOT S8) — EARN immediately
    when the score clears ``_BOOTSTRAP_EARN_THRESHOLD`` (a currently-proven
    earner is never held back in probation), BENCH when the score falls
    below ``_BOOTSTRAP_BENCH_THRESHOLD`` (a proven loser must never enter
    the live-probe/PROVE pool and consume probe fee budget), else PROVE for
    everyone in the middle band — no all-candidates-flattened-to-PROVE or
    -to-two-classes reset; each candidate is judged independently on its own
    score.

    NEVER clobbers an existing row (idempotent bootstrap — a
    live-tracked class/epoch/dwell survives every restart untouched; this
    guards the same "restart must not reset" invariant as ``hydrate_*``
    above). ``score_f`` is the classifier group's function (group B); this
    module only calls it through the injected signature — zero import
    coupling.
    """
    existing = {
        (r[0], r[1])
        for r in conn.execute("SELECT venue, strategy_id FROM strategy_class").fetchall()
    }
    for venue, strategy_id in candidates:
        if (venue, strategy_id) in existing:
            continue
        score = score_f(conn, venue, strategy_id, lookback_days)
        if score > _BOOTSTRAP_EARN_THRESHOLD:
            klass = "EARN"
        elif score < _BOOTSTRAP_BENCH_THRESHOLD:
            klass = "BENCH"
        else:
            klass = "PROVE"
        conn.execute(
            """
            INSERT INTO strategy_class
                (venue, strategy_id, strategy_class, epoch_id,
                 last_transition_ts, dwell)
            VALUES (?, ?, ?, 1, ?, 0)
            ON CONFLICT(venue, strategy_id) DO NOTHING
            """,
            (venue, strategy_id, klass, now_ts),
        )
