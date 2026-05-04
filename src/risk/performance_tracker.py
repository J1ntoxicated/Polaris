"""Recent trade performance tracking (per HYPO).

Pure function (P6) — no I/O. Reads closed Position list.

Used by realtime_runner to feed compute_size() with live win_rate / avg pct.

References:
- INSIGHT-032: Phase 2j AI sizing
- ADR-010: risk caps
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.paper.state import Position

# Cold-start defaults — used when < 5 closed positions exist
_COLD_START_DEFAULTS = {
    "win_rate": 0.5,
    "avg_win_pct": 0.6,
    "avg_loss_pct": 0.5,
}
_MIN_SAMPLE = 5


def compute_recent_stats(
    closed_positions: "list[Position]",
    lookback: int = 20,
) -> dict:
    """Compute recent performance stats for dynamic sizing.

    Win is defined as net positive after round-trip fee (fee = position.fee_round_trip).

    Args:
        closed_positions: Full list of closed positions (list or tuple).
        lookback: Number of recent positions to evaluate.

    Returns:
        dict with keys:
            win_rate (float): fraction of wins in [0, 1]
            avg_win_pct (float): average gross % of winning trades
            avg_loss_pct (float): average gross |%| of losing trades

    Cold start (< _MIN_SAMPLE trades): returns _COLD_START_DEFAULTS.
    """
    recent = list(closed_positions)[-lookback:]
    if len(recent) < _MIN_SAMPLE:
        return dict(_COLD_START_DEFAULTS)

    wins = []
    losses = []
    for pos in recent:
        gross_pct = pos.gross_pct  # signed, e.g. +0.01 = +1%
        net_pct = gross_pct - pos.fee_round_trip  # subtract round-trip fee
        if net_pct > 0:
            wins.append(abs(gross_pct) * 100)   # store as percentage (e.g. 1.0 = 1%)
        else:
            losses.append(abs(gross_pct) * 100)

    win_rate = len(wins) / len(recent)
    avg_win_pct = sum(wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(losses) / len(losses) if losses else 0.0

    return {
        "win_rate": win_rate,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
    }
