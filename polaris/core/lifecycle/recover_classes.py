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
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.core.classes.transition import (
    _SCHMITT_PARTIAL_WINDOW_FRAC,
    _TRIPWIRE_1D_PLUS_N,
    _TRIPWIRE_INTRADAY_N,
    Timeframe,
)

__all__ = [
    "NClosedFn",
    "ScoreFFn",
    "StrategyClassRow",
    "Timeframe",
    "TimeframeBucketFn",
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

# Closed-lifecycle COUNT over the same lookback window ``score_f`` scores —
# injected the same way (zero import coupling to group B's counting logic).
# Signature: (conn, venue, strategy_id, lookback_days) -> int n_closed.
# Optional: a caller that never passes this (or passes None) gets the
# pre-existing behavior byte-for-byte (BENCH floor below is skipped).
NClosedFn = Callable[[sqlite3.Connection, str, str, int], int]

# Track/timeframe classifier for the BENCH minimum-evidence floor below —
# injected the same way (mirrors _production_close_classes.py's own
# strategy-metadata-timeframe -> Timeframe mapping; this module has zero
# import coupling to that scripts-layer helper, only to transition.py's pure
# tripwire constants). Signature: (strategy_id) -> Timeframe.
TimeframeBucketFn = Callable[[str], Timeframe]

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

# BENCH minimum-evidence floor — STRENGTH-MATCHED 2-BAND (codex FINAL
# CONSENSUS, vault/50_research/debates/prove_then_scale_classes_2026-07-03.md
# "부트스트랩 경계 (2026-07-04 추가)" section, superseding an earlier
# single-tripwire-floor draft of this same gap). A score below
# _BOOTSTRAP_BENCH_THRESHOLD still seeds PROVE (not BENCH) unless the
# candidate's closed-lifecycle count n clears the evidence floor matching
# its score's OWN strength band — reusing the two live evidence units
# transition.py already defines (tripwire window / Schmitt 0.4W partial
# window), never a bespoke bootstrap-only number:
#   Band A (score_F <= -4, tripwire-strength): n >= tripwire window
#     (_TRIPWIRE_INTRADAY_N=8 intraday/swing, _TRIPWIRE_1D_PLUS_N=5 1d_plus)
#     — this score magnitude is exactly what LIVE tripwire demotes on, so
#     bootstrap requires the same n the live tripwire would.
#   Band B (-4 < score_F <= -1, Schmitt-strength): n >= ceil(0.4 * W)
#     (12 intraday / 8 swing / 5 1d_plus, W = 30/20/12 per track) — this
#     score magnitude is what LIVE Schmitt EARN->BENCH demotes on (0.4W
#     partial-window mean < -4), so bootstrap requires that same partial-
#     window evidence count.
# A score this deeply negative on TOO FEW closes is exactly the fee-formula
# variance this floor exists to guard against (a single lifecycle with a
# near-zero fee denominator can swing score_F into the hundreds — the
# fee-normalization variance blowup this same debate flags as a separate
# follow-up, not fixed here). Below either band's floor -> PROVE (schema
# default), never BENCH. Rationale for asymmetric treatment vs EARN seeding
# (score > 0 -> EARN immediately, never floored): the LIVE demotion rules
# (tripwire, Schmitt) already require this much evidence before demoting a
# TRACKED strategy out of EARN/PROVE — letting bootstrap's one-shot score_F
# skip straight to BENCH with fewer closes than that same live bar would
# ever accept is a shortcut with no live-rule counterpart, and it is
# asymmetric in the WORSE direction: a wrongly-BENCHed net-positive strategy
# loses capital routing and re-enters only through the slow reentry ladder.
# Concrete miss this guards against (2026-07-04): gold_breakout_1h
# bootstrapped at n=2 closed lifecycles, net +$21.87, score_F -1.10 (Band B)
# -> would BENCH a net-positive strategy on two data points.
_BOOTSTRAP_BENCH_BAND_A_THRESHOLD = -4.0
_BOOTSTRAP_BENCH_BAND_A_MIN_N = {
    "intraday": _TRIPWIRE_INTRADAY_N,
    "swing": _TRIPWIRE_INTRADAY_N,
    "1d_plus": _TRIPWIRE_1D_PLUS_N,
}
# Per-track W feeding Band B's ceil(0.4*W) — debate §① W: intraday 30 /
# swing 20 / 1d_plus 12 (calendar-agnostic trade-count window, timeframe-
# scaled for low-frequency earners; distinct from the persisted per-row
# strategy_class.window_w, which defaults to 20 for every track today).
_BOOTSTRAP_BENCH_BAND_B_W = {"intraday": 30, "swing": 20, "1d_plus": 12}


def _bootstrap_bench_min_n(bucket: Timeframe, score: float) -> int:
    """Strength-matched BENCH evidence floor for one candidate's own score.

    Band A (``score <= _BOOTSTRAP_BENCH_BAND_A_THRESHOLD``, tripwire-strength)
    needs the track's tripwire-window n; Band B (everyone else below
    ``_BOOTSTRAP_BENCH_THRESHOLD`` — the only callers of this helper) needs
    the track's ``ceil(0.4 * W)`` Schmitt-partial-window n. Both bands reuse
    live evidence units (transition.py's tripwire window / Schmitt fraction),
    never a bespoke bootstrap-only threshold.
    """
    if score <= _BOOTSTRAP_BENCH_BAND_A_THRESHOLD:
        return _BOOTSTRAP_BENCH_BAND_A_MIN_N[bucket]
    return math.ceil(_SCHMITT_PARTIAL_WINDOW_FRAC * _BOOTSTRAP_BENCH_BAND_B_W[bucket])


def bootstrap_replay_strategy_class(
    conn: sqlite3.Connection,
    *,
    candidates: Sequence[tuple[str, str]],
    score_f: ScoreFFn,
    lookback_days: int,
    now_ts: int,
    n_closed_fn: NClosedFn | None = None,
    timeframe_bucket_fn: TimeframeBucketFn | None = None,
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

    BENCH minimum-evidence floor: when both ``n_closed_fn`` and
    ``timeframe_bucket_fn`` are supplied, a score below
    ``_BOOTSTRAP_BENCH_THRESHOLD`` is downgraded to PROVE instead of BENCH
    unless the candidate's closed-lifecycle count clears the STRENGTH-MATCHED
    evidence floor for its own score band — Band A (score_F <= -4) needs
    the track's tripwire-window n, Band B (-4 < score_F <= -1) needs the
    track's ceil(0.4*W) Schmitt-partial-window n (see the
    ``_BOOTSTRAP_BENCH_BAND_*`` constants above for the full rationale).
    EARN seeding is never floored. Either callable omitted (``None``, the
    default) reproduces the exact pre-floor behavior — existing callers are
    unaffected.

    NEVER clobbers an existing row (idempotent bootstrap — a
    live-tracked class/epoch/dwell survives every restart untouched; this
    guards the same "restart must not reset" invariant as ``hydrate_*``
    above). ``score_f`` / ``n_closed_fn`` are the classifier group's
    functions (group B); this module only calls them through the injected
    signatures — zero import coupling (``timeframe_bucket_fn`` is the one
    exception: it may reuse ``transition.py``'s own ``Timeframe`` literal,
    which this module already imports for the tripwire constants).
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
            if n_closed_fn is not None and timeframe_bucket_fn is not None:
                bucket = timeframe_bucket_fn(strategy_id)
                min_n = _bootstrap_bench_min_n(bucket, score)
                n_closed = n_closed_fn(conn, venue, strategy_id, lookback_days)
                if n_closed < min_n:
                    klass = "PROVE"
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
