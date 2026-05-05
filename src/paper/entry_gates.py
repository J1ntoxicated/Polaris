"""Entry gates — pure boolean compositions blocking ENTER_LONG (P6).

Phase 12.2 extraction from realtime_runner. Centralizes the pre-entry
guard checks so that the runner can ask one question instead of stringing
together a 7-condition `and not` chain.

All checks are pre-computed by the caller (shell side); this module only
combines them into a single (allow, reason) verdict.

Order of precedence (most-fatal first → most-specific informational):
    1. has_open       — already in position for this ticker
    2. daily_breached — ADR-010 daily loss cap exceeded
    3. closed_this_tick — just closed in same tick (avoid bounce)
    4. in_cooldown    — re-entry cooldown after recent close
    5. in_regime_pause — strategy-specific pause (HYPO-010 cluster)
    6. spread_too_wide — bid-ask spread > 5bps (Phase 6)
    7. regime_blocked  — regime activation matrix (Phase 11)
    8. liq_skip       — book missing/empty (Phase 7 Codex round-1)
"""
from __future__ import annotations

from typing import NamedTuple


class GateVerdict(NamedTuple):
    """Result of evaluate_entry_gates."""
    allow: bool
    reason: str  # empty when allow=True


def evaluate_entry_gates(
    *,
    has_open: bool,
    daily_breached: bool,
    closed_this_tick: bool,
    in_cooldown: bool,
    in_regime_pause: bool,
    spread_too_wide: bool,
    regime_blocked: bool,
    liq_skip: bool,
) -> GateVerdict:
    """Combine all pre-entry blockers into a single verdict.

    Pure function — no I/O, deterministic.

    Returns GateVerdict(allow=True, reason="") when entry is permitted,
    or GateVerdict(allow=False, reason="<first_blocker>") otherwise.
    """
    if has_open:
        return GateVerdict(False, "has_open")
    if daily_breached:
        return GateVerdict(False, "daily_breached")
    if closed_this_tick:
        return GateVerdict(False, "closed_this_tick")
    if in_cooldown:
        return GateVerdict(False, "in_cooldown")
    if in_regime_pause:
        return GateVerdict(False, "in_regime_pause")
    if spread_too_wide:
        return GateVerdict(False, "spread_too_wide")
    if regime_blocked:
        return GateVerdict(False, "regime_blocked")
    if liq_skip:
        return GateVerdict(False, "liq_skip")
    return GateVerdict(True, "")
