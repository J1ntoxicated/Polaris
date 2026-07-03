"""pts-classes (group F followup) — G5 PROVE-branch probe cap CONSUMER.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``tools.ops.probe_reranker`` (07:30 sweeper) already computes+persists which
PROVE candidates hold a concurrent probe slot (``probe_slot_assignment``,
group F). This module is the missing READ side ``compute_size``'s PROVE
branch (``engine.py``) needs to actually CONSUME that routing decision instead
of leaving it unread — the reranker's own docstring named this the "separate,
out-of-scope for this module" consumer.

Two independent caps, both must clear for admission (mirrors
``probe_reranker.assign_slots``'s own two-cap shape, evaluated here per-signal
at sizing time instead of per-track at 07:30):

  1. Concurrent probe cap 3/2/1 per track (A/B/C) — consumed from the LATEST
     ``probe_slot_assignment`` snapshot's ``slot_active`` column. A
     missing/stale snapshot (reranker never ran yet, or its last run is older
     than ``_SNAPSHOT_STALE_SEC``) falls back to counting this track's
     CURRENTLY OPEN probe positions directly against
     ``r_pool.PROBE_CONCURRENCY_CAP[track]`` — the cap must stay alive even
     before the reranker has ever produced a snapshot
     (no_block_filter_architecture cuts both ways: this is a routing cap that
     must ACTUALLY apply, not silently go inert on a cold start).
  2. 24h probe fee cap (6/4/2 x F_track_cap) — this candidate's own
     ``strategy_class.probe_fee_24h`` running total vs.
     ``PROBE_FEE_CAP_MULT[track] x f_track_cap_usd`` (caller supplies both —
     this module performs no additional DB read for the fee side, the caller
     already has both values at hand from the ``strategy_class`` row it read
     to route into this class branch at all).

Read-only consumption — zero hot-path writes. Accrual stays owned by
``polaris.core.classes.probe_fee.accrue_probe_fee`` (close-hook / WIRE path);
snapshot writes stay owned by ``tools.ops.probe_reranker``. A cap miss is a
capital-ROUTING decision (shadow, not block) — the caller (``engine.py``)
reuses its EXISTING shadow-route swap, this module only decides admitted vs.
not + the falsifiable reason (aggressive_always_profit /
no_block_filter_architecture / flow_not_block).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Final

from polaris.core.classes.probe_reranker import PROBE_FEE_CAP_MULT
from polaris.core.classes.r_pool import PROBE_CONCURRENCY_CAP
from polaris.core.sizing.schema import Track
from polaris.core.streams.config import resolve_stream

logger = logging.getLogger(__name__)

__all__ = ["ProbeCapResult", "probe_cap_check"]

# A snapshot older than this is treated as ABSENT (fallback path) rather than
# trusted as still-current. The reranker's own cadence is once daily
# (07:30) — 2x that (172_800s) tolerates one missed/delayed run without
# flapping into fallback, while a genuinely dead reranker (no run in 2+ days)
# correctly falls back to the live open-position count instead of serving a
# week-old routing decision.
_SNAPSHOT_STALE_SEC: Final[int] = 2 * 86_400


@dataclass(frozen=True, slots=True)
class ProbeCapResult:
    """Admission outcome for one PROVE candidate's probe-cap check."""

    admitted: bool
    reason: str  # "" (admitted) | "slots" | "fee_24h"
    used_fallback: bool  # True iff the concurrency check used the open-position fallback


def _latest_slot_active(
    conn: sqlite3.Connection, *, track: str, venue: str, strategy_id: str, now_ts: int
) -> bool | None:
    """Latest ``probe_slot_assignment`` row's ``slot_active`` for this
    candidate, or ``None`` if no fresh-enough snapshot exists (caller falls
    back to the direct open-position count)."""
    row = conn.execute(
        "SELECT run_ts, slot_active FROM probe_slot_assignment "
        "WHERE track = ? AND venue = ? AND strategy_id = ? "
        "ORDER BY run_ts DESC LIMIT 1",
        (track, venue, strategy_id),
    ).fetchone()
    if row is None:
        return None
    run_ts, slot_active = int(row[0]), bool(row[1])
    if now_ts - run_ts > _SNAPSHOT_STALE_SEC:
        return None
    return slot_active


def _open_probe_count_for_track(conn: sqlite3.Connection, *, track: Track) -> int:
    """Count currently-open positions whose (venue, strategy_id) holds
    PROVE class, scoped to this track — the fallback concurrency read used
    when no fresh reranker snapshot exists yet.

    Joins ``positions`` (status='open') against ``strategy_class`` on
    (venue, strategy_id) — PROVE-class membership at read time is the only
    signal this module needs (same source ``resolve_strategy_class`` reads;
    no per-position "is probe" flag exists, probes and EARN trades share the
    same ``positions`` row shape). ``strategy_class`` has no ``track`` column
    (A/B/C is a venue-derived concept, not stored per-row — same SSOT
    ``resolve_stream`` the reranker itself uses), so track scoping happens in
    Python over each open PROVE row's own venue rather than in SQL.
    """
    rows = conn.execute(
        "SELECT p.venue FROM positions p "
        "JOIN strategy_class sc ON sc.venue = p.venue AND sc.strategy_id = p.strategy_id "
        "WHERE p.status = 'open' AND sc.strategy_class = 'PROVE'"
    ).fetchall()
    count = 0
    for (row_venue,) in rows:
        try:
            if resolve_stream(str(row_venue)).track == track:
                count += 1
        except KeyError:
            continue
    return count


def probe_cap_check(
    conn: sqlite3.Connection,
    *,
    track: Track,
    venue: str,
    strategy_id: str,
    probe_fee_24h_usd: float,
    f_track_cap_usd: float,
    now_ts: int,
) -> ProbeCapResult:
    """Admission check for one PROVE candidate against both probe caps.

    Concurrency cap short-circuits the fee cap in the ``reason`` (mirrors
    ``probe_reranker.assign_slots``'s own "concurrency wins over fee in
    reason labeling" convention) — the caller only needs ONE reason string
    and a slot miss is the more legible signal.
    """
    slot_active = _latest_slot_active(conn, track=track, venue=venue, strategy_id=strategy_id, now_ts=now_ts)
    used_fallback = slot_active is None
    if used_fallback:
        open_count = _open_probe_count_for_track(conn, track=track)
        slot_active = open_count < PROBE_CONCURRENCY_CAP[track]

    if not slot_active:
        logger.info(
            "[pts-classes] probe_cap %s/%s track=%s reason=slots admitted=False "
            "used_fallback=%s",
            venue, strategy_id, track, used_fallback,
        )
        return ProbeCapResult(admitted=False, reason="slots", used_fallback=used_fallback)

    fee_budget = PROBE_FEE_CAP_MULT[track] * f_track_cap_usd
    if probe_fee_24h_usd > fee_budget:
        logger.info(
            "[pts-classes] probe_cap %s/%s track=%s reason=fee_24h admitted=False "
            "probe_fee_24h_usd=%.2f fee_budget=%.2f",
            venue, strategy_id, track, probe_fee_24h_usd, fee_budget,
        )
        return ProbeCapResult(admitted=False, reason="fee_24h", used_fallback=used_fallback)

    return ProbeCapResult(admitted=True, reason="", used_fallback=used_fallback)
