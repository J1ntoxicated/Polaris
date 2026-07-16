"""probe_decisions → G6/G7 tighten-intent bridge (pure reader + stop synthesis).

DEMO/PAPER · aggressive · flow_not_block. The probe ExitEngine is OBSERVE-ONLY
(Slice 1) — it writes a ``probe_decisions`` row per tick (action ∈ HOLD / WIDEN /
TIGHTEN / HARVEST) to the ``data/probes.sqlite`` sidecar but threads ZERO knobs into
the live exit. This module is the read-side bridge that lets the deterministic G6/G7
tighten consumer (``POLARIS_G6_PROBE_TIGHTEN``) act on an adverse TIGHTEN lean:

  * ``latest_probe_tighten`` — the freshest OPEN (un-backfilled) probe decision for a
    position (action + probe-synthesised trail_mult, NULL in observe mode). FAIL-OPEN.
  * ``synth_tighten_stop`` — the tighter stop from the position's anchor (peak/trough)
    using the probe ``trail_mult`` when present, else an ATR-N% fallback. Returns None
    when the synthesised stop would NOT be tighter than the current stop (the ratchet —
    never loosen — is preserved; G7's ``can_tighten_exit`` re-checks it anyway).

This is precise exit TIMING (a tighter trail), NEVER a HOLD->EXIT_NOW block, NEVER a
size cut. The -1.0R hard rail / entry side / size chain are all untouched. The probe
ExitEngine.compose stays observe-only (this module only READS the sidecar log — it does
not flip the engine to ``trail_only`` / ``full``, which remains an ADR-012 Slice 2
``/debate``-gated decision).

P3 promotion (2026-07-16, probe protect promotion — vault/50_research/
built-not-wired-audit.md): the probe performance readout (25,624 favorable-MFE
positions, 2026-07-15 probe) showed protect actions (TIGHTEN + HARVEST) correctly
called giveback ahead of it (HARVEST realized +0.088R vs HOLD -0.08R; live
``probe_decisions`` volume TIGHTEN 7,359 / HARVEST 5,465), while WIDEN measured
-0.115R (bad) — so ONLY TIGHTEN/HARVEST are promoted to the live consumer
(``PROBE_TIGHTEN_APPLY_ACTIONS``, an ALLOWLIST so WIDEN/HOLD structurally can never
reach the tighten path). ``mark_probe_tighten_applied`` closes the promotion-evidence
loop: once G7 actually persists the tighter stop, the SOURCE probe_decisions row is
flagged ``applied=1`` so a before/after read can isolate the promoted decisions.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from polaris.core.live_recalc.exit_params import EXIT_HARVEST_TRAIL_MULT

logger = logging.getLogger(__name__)

__all__ = [
    "PROBE_TIGHTEN_APPLY_ACTIONS",
    "TIGHTEN_DEBOUNCE_DEFERRED_OVERRIDES",
    "TIGHTEN_DEBOUNCE_DEFERRED_SECONDS",
    "TIGHTEN_FALLBACK_TRAIL_MULT",
    "ProbeTightenIntent",
    "latest_probe_tighten",
    "mark_probe_tighten_applied",
    "synth_tighten_stop",
]

# The probe actions the live G6/G7 consumer is allowed to escalate into a tighter
# trail — an ALLOWLIST (not a WIDEN blocklist) so a future probe action can never
# silently start applying by omission. TIGHTEN and HARVEST are the two "protect a
# winner / cut a giveback" leans the 2026-07-15 probe performance readout confirmed
# net-positive; WIDEN measured -0.115R (net negative) and stays excluded. HOLD is
# never a protect signal by definition.
PROBE_TIGHTEN_APPLY_ACTIONS: frozenset[str] = frozenset({"TIGHTEN", "HARVEST"})

# 1st-increment debounce posture (the per-position override COUNTER + last-override
# TIMESTAMP are a positions-schema addition deferred to the next increment). Until
# then the tighten proposal passes these PLACEHOLDERS to G7's ``can_tighten_exit``
# cooldown / override-cap rails: a count of 0 (never the cap) and a gap past the
# cooldown, so the local debounce is a no-op THIS increment. That is safe because the
# synth + ratchet are monotone — a re-fire that is not strictly tighter is rejected, so
# repeated fires can only ratchet toward profit, never thrash. NOT bare magic numbers:
# named to mark exactly which rail is deferred.
TIGHTEN_DEBOUNCE_DEFERRED_OVERRIDES: int = 0
TIGHTEN_DEBOUNCE_DEFERRED_SECONDS: int = 60

# ATR-N% fallback trail width when the observe-mode probe row carries no trail_mult
# (the common case today — observe mode persists trail_mult=NULL). The 1-ATR HARVEST
# width is the existing "lock a winner" tighten multiplier; reusing it keeps the
# tighten on a CALIBRATED scale instead of a fresh magic number. Env-overridable so a
# /debate calibration can move it without a code edit.
TIGHTEN_FALLBACK_TRAIL_MULT: float = EXIT_HARVEST_TRAIL_MULT


@dataclass(frozen=True, slots=True)
class ProbeTightenIntent:
    """The freshest open probe decision for a position (read-side view)."""

    action: str
    trail_mult: float | None
    composite_lean: float
    ts: int
    # decision_id (probe_decisions PK) — carried so the recalc caller can flag the
    # EXACT source row ``applied=1`` once G7 actually persists the tighter stop
    # (mark_probe_tighten_applied below). Promotion evidence (before/after read).
    decision_id: str


def latest_probe_tighten(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
) -> ProbeTightenIntent | None:
    """Freshest OPEN probe decision for ``position_id`` (FAIL-OPEN → None).

    Reads the ``data/probes.sqlite`` sidecar (NOT the live DB). Only un-backfilled
    rows (``realized_pnl_r IS NULL`` — the position is still open) are considered, so a
    closed/reopened id never resurfaces a stale lean. Returns None on a None handle, a
    missing table, any sqlite error, or no open row — the live recalc must never crash
    on the sidecar read.
    """
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT action, trail_mult, composite_lean, ts, decision_id "
            "FROM probe_decisions "
            "WHERE position_id = ? AND realized_pnl_r IS NULL "
            "ORDER BY ts DESC LIMIT 1",
            (str(position_id),),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    action, trail_mult, composite_lean, ts, decision_id = row
    return ProbeTightenIntent(
        action=str(action),
        trail_mult=None if trail_mult is None else float(trail_mult),
        composite_lean=float(composite_lean) if composite_lean is not None else 0.0,
        ts=int(ts),
        decision_id=str(decision_id),
    )


def mark_probe_tighten_applied(
    conn: sqlite3.Connection | None,
    *,
    decision_id: str,
) -> None:
    """Flag the SOURCE ``probe_decisions`` row ``applied=1`` (promotion evidence).

    Called ONLY after G7 has actually persisted the tighter stop
    (``tightening_applied`` — the write to ``positions.stop_price`` already
    happened). This never gates or reverses that write; it is a pure telemetry
    stamp on the probe sidecar so a before/after read can isolate exactly which
    TIGHTEN/HARVEST decisions were promoted into a real stop move. FAIL-OPEN: a
    None handle or any sqlite error is logged and swallowed — the sidecar stamp
    must never affect the live exit path.
    """
    if conn is None:
        return
    try:
        conn.execute(
            "UPDATE probe_decisions SET applied = 1 WHERE decision_id = ?",
            (str(decision_id),),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[probe] mark_probe_tighten_applied failed for %s (degrade): %r",
            decision_id, exc,
        )


def synth_tighten_stop(
    *,
    side: str,
    anchor_extreme: float,
    entry_price: float,
    atr_pct: float,
    current_stop_price: float,
    probe_trail_mult: float | None,
) -> float | None:
    """Synthesise the tighter stop from the anchor (peak/trough) and trail width.

    Mirrors the FSM ``_trailing_stop`` geometry (long: ``peak - mult*ATR``; short:
    ``trough + mult*ATR``) so the tighten lands on the SAME scale the precise-exit
    engine uses. The trail width is the probe ``trail_mult`` when present, else the
    ATR-N% fallback (``TIGHTEN_FALLBACK_TRAIL_MULT``). Returns the new stop ONLY when it
    is strictly TIGHTER than ``current_stop_price`` (long: higher; short: lower) — else
    None so the caller skips the tighten (the ratchet — never loosen — is preserved).
    """
    side_lc = side.lower()
    mult = (
        float(probe_trail_mult)
        if probe_trail_mult is not None
        else TIGHTEN_FALLBACK_TRAIL_MULT
    )
    # 1-ATR in price terms (relative floor mirrors the engine's _atr_one_usd guard).
    atr_one = max(entry_price * max(atr_pct, 0.0), entry_price * 1e-4)
    if side_lc == "long":
        candidate = anchor_extreme - mult * atr_one
        return candidate if candidate > current_stop_price else None
    if side_lc == "short":
        candidate = anchor_extreme + mult * atr_one
        return candidate if candidate < current_stop_price else None
    return None
