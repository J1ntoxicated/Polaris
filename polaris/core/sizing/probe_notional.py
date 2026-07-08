"""pts-classes (group D) — probe notional formula + PROVE admission gate.

Spec source: MEMORY.md group-D task header, ``U2`` unresolved-point resolution.

``probe_notional = max(K x round_trip_fee_usd_fixed, venue_min_notional)``.

U2 interpretation (SSOT for this module — the reason the K-term rarely
binds): ``FEE_FLOOR_K`` (3.0, ``exit_strategy_config.py``) and
``BASE_RISK_PCT`` (0.02, ``risk_unit.py``) are UNRELATED constants — the
former is a round-trip-fee multiple, the latter a %-of-equity risk unit.
``round_trip_fee_usd_fixed`` is a FLAT per-trade USD fee (independent of
notional) — every venue currently registered (OKX/Capital/Alpaca) charges a
%-of-notional fee instead, so their flat term is 0.0 and ``venue_min_notional``
is what actually binds. The K-term only activates for a hypothetical
flat-fee venue. This is a fixed-USD FLOOR consumed by the PROVE class outside
the T4 %-of-equity chain — it introduces ZERO new T4 multiplier slots (9-stack
ban intact); EARN's byte-identical chain never calls this module.

DEMO/PAPER only. Aggressive bias preserved — a PROVE signal that fails
admission is SHADOW-routed (still computed + logged for learning), never
blocked/rejected outright (no_block_filter_architecture / flow_not_block).
"""

from __future__ import annotations

import math
import os
import sqlite3
from typing import Final

from polaris.core.cell_matrix.schema import ROUTING_BOTTOM_MULT
from polaris.core.economics.fees import real_fee_bps
from polaris.core.sizing.schema import ENTRY_NOTIONAL_FLOOR_USD

__all__ = [
    "DEFAULT_STRATEGY_CLASS",
    "DEFAULT_VENUE_MIN_NOTIONAL_USD",
    "FEE_FLOOR_K",
    "bottom_cell_shadow_hit",
    "probe_notional_usd",
    "prove_admission_ok",
    "prove_probe_on_anti_edge",
    "prove_stop_dist_floor_pct",
    "resolve_strategy_class",
    "round_trip_fee_rate",
    "venue_min_notional_usd",
]

# Mirrors exit_strategy_config.FEE_FLOOR_K (3.0) — same round-trip multiple,
# re-declared here (not imported) because that module lives in the
# scripts layer and polaris.core must not import UP into scripts
# (test_core_layering.py). Kept numerically identical by convention/review,
# not by shared reference.
FEE_FLOOR_K: Final[float] = 3.0

# Generic per-trade dollar floor — reuses the EXISTING static SSOT
# (schema.ENTRY_NOTIONAL_FLOOR_USD, $10) rather than introducing a second
# competing "$10 floor" constant. A per-venue/per-instrument live minimum
# (InstrumentConstraint.min_sz x price) is resolved downstream at order-submit
# time (venues layer) — this is the T4-layer generic floor only.
DEFAULT_VENUE_MIN_NOTIONAL_USD: Final[float] = ENTRY_NOTIONAL_FLOOR_USD


def _env_float(name: str, default: float) -> float:
    """Same private-per-module idiom as ``schema._cap_env`` / ``constants._read_float_env``
    (each sizing sub-module owns its own tiny env-float reader rather than a shared
    cross-file private import)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def prove_probe_on_anti_edge() -> bool:
    """B (2026-07-08): when True (default), a PROVE probe fires a small REAL
    size even on an anti-edge / bottom-suppression cell, instead of routing to
    shadow (size 0).

    Rationale — the shadow-on-anti-edge routing was a catch-22: a PROVE track
    that is never sized-real never fills, so it never accrues the dwell/fill
    evidence the transition machine needs (``_check_prove_to_earn`` dwell>=10
    closes + the ``_fill_gate_ok`` execution-evidence gate), so it stays
    shadowed forever and can never promote to EARN. Since the losing history
    that made the learner mark the cell anti-edge is exactly what PROVE is
    meant to re-test, forcing shadow guarantees the re-test never happens.

    Bounded, NOT churn: the probe stays min-notional (``probe_notional_usd``),
    is 24h-fee-capped (``probe_cap_check`` still routes a cap-exhausted probe to
    shadow), admission-gated (``prove_admission_ok``: stop_dist > K*round-trip
    fee — genuinely sub-fee setups still shadow), and headroom-clipped (the same
    per_symbol/cluster/track/venue/total caps an EARN trade clips against). A
    PROVE track that keeps losing on real probes self-demotes to BENCH (which
    IS unconditional shadow) via ``PROVE_STAGNATION``.

    Set ``POLARIS_PROVE_PROBE_ON_ANTI_EDGE=0`` to restore shadow-on-anti-edge.
    """
    return os.environ.get("POLARIS_PROVE_PROBE_ON_ANTI_EDGE", "1") == "1"


def venue_min_notional_usd(venue: str) -> float:
    """Generic per-trade USD minimum for ``venue``.

    Env: ``POLARIS_PROBE_MIN_NOTIONAL_<VENUE>_USD``. Every currently-registered
    venue shares the same static default — an
    unknown venue is NOT treated as $0 (flow_not_block: a probe must never
    silently vanish to a zero floor).
    """
    return _env_float(
        f"POLARIS_PROBE_MIN_NOTIONAL_{venue.upper()}_USD", DEFAULT_VENUE_MIN_NOTIONAL_USD
    )


def probe_notional_usd(
    venue: str,
    *,
    round_trip_fee_usd_fixed: float = 0.0,
    venue_min_notional_override: float | None = None,
) -> float:
    """``max(K x round_trip_fee_usd_fixed, venue_min_notional)`` — U2 formula.

    ``round_trip_fee_usd_fixed`` defaults to 0.0 (no currently-registered
    venue charges a flat per-trade fee) so this degenerates to
    ``venue_min_notional_usd(venue)`` for OKX/Capital/Alpaca, matching the
    U2 resolution. Non-finite/negative fee input clamps to 0.0 (data-integrity
    fallback — never a negative probe size).
    """
    fee_term = round_trip_fee_usd_fixed if math.isfinite(round_trip_fee_usd_fixed) else 0.0
    fee_term = max(0.0, fee_term)
    min_notional = (
        venue_min_notional_override
        if venue_min_notional_override is not None
        else venue_min_notional_usd(venue)
    )
    return max(FEE_FLOOR_K * fee_term, min_notional)


# ---------------------------------------------------------------------------
# PROVE admission gate — stop_dist_pct > 3 x round-trip fee_rate
# ---------------------------------------------------------------------------


def round_trip_fee_rate(venue: str) -> float:
    """Two REAL taker legs (entry+exit) for ``venue``, as a fraction of notional.

    Same source as ``exit_strategy_config._fee_round_trip_pct_for_venue``
    (``economics.fees.real_fee_bps``, REAL not demo-penalty bps) — re-declared
    here rather than imported for the same core/scripts layering reason as
    ``FEE_FLOOR_K`` above.
    """
    return 2.0 * real_fee_bps(venue) / 10_000.0


def prove_stop_dist_floor_pct(venue: str) -> float:
    """``3 x round_trip_fee_rate(venue)`` — the PROVE admission floor."""
    return FEE_FLOOR_K * round_trip_fee_rate(venue)


def prove_admission_ok(*, venue: str, stop_dist_pct: float) -> bool:
    """True iff a PROVE-class signal is admitted to the live probe pool.

    Admission: ``stop_dist_pct > FEE_FLOOR_K x round_trip_fee_rate(venue)``
    (strict — a signal exactly AT the floor has not cleared it). A
    non-finite/non-positive ``stop_dist_pct`` never admits (data-integrity
    fallback — routes to shadow, same as a genuine fee-floor miss;
    flow_not_block: shadow still computes + learns, never blocks the signal
    from being observed).
    """
    if not math.isfinite(stop_dist_pct) or stop_dist_pct <= 0.0:
        return False
    return stop_dist_pct > prove_stop_dist_floor_pct(venue)


# ---------------------------------------------------------------------------
# PROVE shadow-routing trigger — bottom-suppression cell (the "cell_mult=0
# cell" eligibility check, group-D task header). P0's cell-mult floor is
# ROUTING_BOTTOM_MULT (0.5) — a literal 0 never occurs
# (apply_cell_routing_mult's own defensive comment: "a cell mult of 0 would
# silently kill the trade"). The bottom-suppression mult is this scheme's
# 0-analog. This is a pure cell_mult comparison (no learner-table read) so it
# stays in sizing — the SIBLING anti-edge shadow-routing trigger lives in
# ``polaris.core.cell_matrix.fetch_learner_anti_edge`` instead (this package
# must never read the Bayesian-learner edge table directly — SSOT guard,
# see test_edge_validation.py's regression test).
# ---------------------------------------------------------------------------


def bottom_cell_shadow_hit(cell_mult: float) -> bool:
    """True iff ``cell_mult`` is at/below the bottom-suppression floor.

    A non-finite input is treated as a hit (fail toward shadow, never toward
    an unguarded EARN-style full size on a corrupt read).
    """
    if not math.isfinite(cell_mult):
        return True
    return cell_mult <= ROUTING_BOTTOM_MULT


# ---------------------------------------------------------------------------
# strategy_class resolver — shared by the 3 class-aware compute_size call
# sites (entry_sizer.py / replay/engine.py / _production_tick_engine.py). Read
# lives here (not in ``polaris.core.lifecycle.recover_classes``, group A's
# storage module — out of this group's scope) as a thin single-row query
# against the SAME ``strategy_class`` table group A owns.
# ---------------------------------------------------------------------------

DEFAULT_STRATEGY_CLASS: Final[str] = "EARN"
"""Fallback when no ``strategy_class`` row exists yet (bootstrap hasn't run /
brand-new strategy) or the table read fails — EARN reproduces the
byte-identical pre-pts-classes %-of-equity chain, never a silent BENCH/shadow
lockout on a data gap (flow_not_block)."""


def resolve_strategy_class(conn: sqlite3.Connection | None, *, venue: str, strategy_id: str) -> str:
    """Read the current ``strategy_class`` for ``(venue, strategy_id)``.

    No row / no conn / any DB error -> ``DEFAULT_STRATEGY_CLASS`` ("EARN") — a
    missing row is NOT a proven-loser signal (bootstrap seeds the row from the
    replay-scored 3-way outcome; until that has run, or for a table read
    error, the safe/byte-identical default wins). ``conn is None`` is a real
    caller shape (e.g. a test-only compute_size stand-in that never touches
    the DB) — same fail-open contract, not a crash.

    VIRTUAL ACCOUNT (``POLARIS_VIRTUAL_ACCOUNT=1``): the shadow gate is bypassed —
    EVERY registered strategy routes to ``EARN`` so every signal becomes a real
    (virtual) trade that is visible + measured. The whole point of the virtual
    account is to SEE every edge trade (limits removed); the Prove-then-Scale
    shadow gate is REAL-money capital protection, unneeded on virtual funds. The
    ``strategy_class`` table still tracks the live class (the transition FSM keeps
    scoring per-strategy virtual performance for the eventual real-wire flip) —
    only virtual SIZING ignores it here. Env read directly (not via the
    scripts-layer ``virtual_account_enabled``) to keep core→scripts layering clean.
    """
    if os.environ.get("POLARIS_VIRTUAL_ACCOUNT", "0") == "1":
        return "EARN"
    if conn is None:
        return DEFAULT_STRATEGY_CLASS
    try:
        row = conn.execute(
            "SELECT strategy_class FROM strategy_class WHERE venue = ? AND strategy_id = ?",
            (venue, strategy_id),
        ).fetchone()
    except sqlite3.Error:
        return DEFAULT_STRATEGY_CLASS
    if row is None:
        return DEFAULT_STRATEGY_CLASS
    return str(row[0])
