"""Layer 6 — strategy mid-trade swap (P0 stub).

Spec source: vault/30_components/layer-6-live-recalc.md (Q3, Q4).

Position invariants:
  - ``entry_strategy_id`` is **immutable** (P&L attribution preserved).
  - ``active_strategy_id`` is **mutable** (the strategy currently driving the
    position).
  - max **1** swap per trade (P0 cap; P1 = unlimited).

Swap candidate constraint (per Day 3 / L2 G6):
  - same ``correlation_group``.
  - same venue / symbol / side.

P0 stub semantics:
  - ``evaluate_strategy_swap()`` accepts the candidate, enforces the cap, and
    returns either ``"would_swap"`` (P0 logging) or ``"skipped_cap"``
    (already swapped) / ``"skipped_correlation_mismatch"``.
  - Real swap action (writing ``active_strategy_id``, opening a new segment
    row) is gated by an ``apply=True`` flag — defaults to ``False`` so P0
    callers stay log-only. Tests exercise both paths.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

SWAP_MAX_PER_TRADE: Final[int] = 1

# Layer 6 §Q3 attribution defaults (entry / active). P0 records the active
# share on the new segment row; P1 will reweight to (0.30, 0.70) when the
# late-strategy hold ratio crosses the 70% threshold.
DEFAULT_ENTRY_ATTRIBUTION: Final[float] = 0.40
DEFAULT_ACTIVE_ATTRIBUTION: Final[float] = 0.60


@dataclass(slots=True)
class SwapCandidate:
    position_id: str
    from_strategy_id: str
    to_strategy_id: str
    venue: str
    symbol: str
    side: str
    from_correlation_group: str
    to_correlation_group: str


@dataclass(slots=True)
class SwapDecision:
    position_id: str
    decision: str  # "would_swap" / "applied" / "skipped_cap" / "skipped_correlation_mismatch"
    reason: str
    swap_count_after: int


def evaluate_strategy_swap(
    conn: sqlite3.Connection,
    *,
    candidate: SwapCandidate,
    now_ts: int,
    apply: bool = False,
    max_per_trade: int = SWAP_MAX_PER_TRADE,
) -> SwapDecision:
    """Validate + (optionally) apply a strategy swap.

    Validation checks (in order):
      1. ``from_correlation_group == to_correlation_group``.
      2. position exists and ``swap_count < max_per_trade``.
      3. candidate.venue / symbol / side **must match the live position row**
         (codex Day 4 R1 P1 fix — Layer 6 Q3 invariant).

    On ``apply=True``:
      - Writes ``active_strategy_id``.
      - Increments ``swap_count``.
      - Opens a new ``position_strategy_segments`` row (entry_reason="swap").
      - Closes the previous segment (ended_ts = now).
    """
    if candidate.from_correlation_group != candidate.to_correlation_group:
        return SwapDecision(
            position_id=candidate.position_id,
            decision="skipped_correlation_mismatch",
            reason=(
                f"from={candidate.from_correlation_group} "
                f"to={candidate.to_correlation_group}"
            ),
            swap_count_after=0,
        )
    row = conn.execute(
        "SELECT swap_count, active_strategy_id, venue, symbol, side, "
        "       entry_strategy_id, underlying_group_id, opened_ts "
        "FROM positions WHERE position_id = ?",
        (candidate.position_id,),
    ).fetchone()
    if row is None:
        return SwapDecision(
            position_id=candidate.position_id,
            decision="skipped_correlation_mismatch",
            reason="position_missing",
            swap_count_after=0,
        )
    swap_count = int(row[0] or 0)
    pos_active_strategy = str(row[1] or "")
    pos_venue, pos_symbol, pos_side = str(row[2]), str(row[3]), str(row[4])
    entry_strategy_id = str(row[5] or "")
    underlying_group_id = str(row[6] or "")
    opened_ts = int(row[7] or 0)
    # Layer 6 §Q3 invariant: ``active_strategy_id`` == current driving
    # strategy. Reject candidates whose ``from_strategy_id`` does not
    # match — otherwise a stale source would silently overwrite the
    # active row and corrupt attribution (codex Day 4 R3 fix).
    if pos_active_strategy and pos_active_strategy != candidate.from_strategy_id:
        return SwapDecision(
            position_id=candidate.position_id,
            decision="skipped_active_mismatch",
            reason=(
                f"position active_strategy_id={pos_active_strategy} "
                f"!= candidate from_strategy_id={candidate.from_strategy_id}"
            ),
            swap_count_after=swap_count,
        )
    if (
        candidate.venue != pos_venue
        or candidate.symbol != pos_symbol
        or candidate.side != pos_side
    ):
        return SwapDecision(
            position_id=candidate.position_id,
            decision="skipped_position_mismatch",
            reason=(
                f"position venue/symbol/side ({pos_venue}/{pos_symbol}/{pos_side}) "
                f"!= candidate ({candidate.venue}/{candidate.symbol}/{candidate.side})"
            ),
            swap_count_after=swap_count,
        )
    if swap_count >= max_per_trade:
        return SwapDecision(
            position_id=candidate.position_id,
            decision="skipped_cap",
            reason=f"already swapped {swap_count} time(s)",
            swap_count_after=swap_count,
        )
    if not apply:
        return SwapDecision(
            position_id=candidate.position_id,
            decision="would_swap",
            reason="P0 stub",
            swap_count_after=swap_count + 1,
        )
    # apply path: P0 minimal mutation. P1 will compute attribution math
    # (40/60 default → 30/70 late-hold). For P0 we record `regime_at_start`
    # from the live regime SSOT and seed both segments with the spec default
    # weights (entry=0.40, active=0.60).
    new_swap_count = swap_count + 1
    conn.execute(
        "UPDATE positions SET active_strategy_id = ?, swap_count = ? "
        "WHERE position_id = ?",
        (candidate.to_strategy_id, new_swap_count, candidate.position_id),
    )
    # Resolve segment regime from regime_state SSOT (Layer 6 §Q2 key =
    # venue × underlying_group_id). Empty fallback when the row was not
    # seeded with a group id (legacy data).
    regime_at_start = _lookup_regime(
        conn, venue=candidate.venue, underlying_group_id=underlying_group_id
    )
    # Seed the missing entry segment if first swap on this trade — without
    # it the ledger loses entry attribution forever (R2 fix).
    if swap_count == 0 and not _has_open_or_closed_segments(
        conn, position_id=candidate.position_id
    ):
        entry_seg_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO position_strategy_segments "
            "(position_id, segment_id, strategy_id, regime_at_start, started_ts, "
            " ended_ts, entry_reason, exit_reason, attribution_weight, pnl_r) "
            "VALUES (?, ?, ?, ?, ?, ?, 'entry', 'swap', ?, 0.0)",
            (
                candidate.position_id,
                entry_seg_id,
                entry_strategy_id or candidate.from_strategy_id,
                regime_at_start,
                opened_ts or now_ts,
                now_ts,
                DEFAULT_ENTRY_ATTRIBUTION,
            ),
        )
    else:
        # Close prior open segment(s) (if any) preserving existing attribution.
        conn.execute(
            "UPDATE position_strategy_segments SET ended_ts = ?, exit_reason = 'swap' "
            "WHERE position_id = ? AND ended_ts IS NULL",
            (now_ts, candidate.position_id),
        )
    # Open new segment for the active strategy.
    seg_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO position_strategy_segments "
        "(position_id, segment_id, strategy_id, regime_at_start, started_ts, "
        " ended_ts, entry_reason, exit_reason, attribution_weight, pnl_r) "
        "VALUES (?, ?, ?, ?, ?, NULL, 'swap', NULL, ?, 0.0)",
        (
            candidate.position_id,
            seg_id,
            candidate.to_strategy_id,
            regime_at_start,
            now_ts,
            DEFAULT_ACTIVE_ATTRIBUTION,
        ),
    )
    logger.info(
        "[swap] %s applied: %s → %s venue=%s symbol=%s side=%s count=%d",
        candidate.position_id,
        candidate.from_strategy_id,
        candidate.to_strategy_id,
        candidate.venue,
        candidate.symbol,
        candidate.side,
        new_swap_count,
    )
    return SwapDecision(
        position_id=candidate.position_id,
        decision="applied",
        reason="swap applied",
        swap_count_after=new_swap_count,
    )


def _lookup_regime(
    conn: sqlite3.Connection, *, venue: str, underlying_group_id: str
) -> str:
    """Look up regime via the Layer 6 SSOT key ``(venue, underlying_group_id)``.

    Returns empty string when the row is missing or the position has not
    been tagged with an ``underlying_group_id`` yet (legacy data, smoke
    seeds). Schema accepts the empty default.
    """
    if not underlying_group_id:
        return ""
    row = conn.execute(
        "SELECT regime FROM regime_state "
        "WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0])


def _has_open_or_closed_segments(
    conn: sqlite3.Connection, *, position_id: str
) -> bool:
    """Return True if the position already owns at least one segment row."""
    row = conn.execute(
        "SELECT 1 FROM position_strategy_segments WHERE position_id = ? LIMIT 1",
        (position_id,),
    ).fetchone()
    return row is not None


__all__ = [
    "DEFAULT_ACTIVE_ATTRIBUTION",
    "DEFAULT_ENTRY_ATTRIBUTION",
    "SWAP_MAX_PER_TRADE",
    "SwapCandidate",
    "SwapDecision",
    "evaluate_strategy_swap",
]
