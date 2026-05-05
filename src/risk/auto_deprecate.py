"""Auto-deprecate triggers — pure (P6).

Jin mandate: fail-fast paradigm — deploy all alphas simultaneously,
auto-cut when not working, immediately try next alpha.

Each HYPO has pre-defined deprecation triggers. When any trigger is hit,
the runner removes the HYPO from REALTIME_HYPOS, closes positions, and logs WARN.

No manual Codex round patterns. Triggers are mathematical and automatic.

Trigger definitions (DEPRECATE_TRIGGERS):
  fast_fail: n >= 5 AND win_rate < 40% → EV likely negative, cut fast
             (Phase 2M: 10 → 5 for faster random strategy elimination)
  loss_cap:  realized PnL < -$5 → hard loss stop
             (Phase 2M: -$10 → -$5 for tighter academic-grade validation)
  frequency: 24h elapsed AND n < 3 → signal too infrequent to validate

Pure function — no I/O, no module state. Shell calls check_deprecate with position data.
"""
from __future__ import annotations

import time
from typing import Optional

# Trigger thresholds — Phase 5 Codex review (2026-05-04)
# Rationale: Bailey (2014) minimum n=20 for statistically meaningful win-rate.
#   loss_cap -$15 = 7.5% of $200 baseline sizing across 30-trade validation window.
#   Prevents premature deprecation of high-variance strategies before statistical signal.
DEPRECATE_TRIGGERS: dict = {
    "fast_fail": {
        "min_n": 20,          # Phase 5: 5 → 20 (Bailey 2014 statistical minimum)
        "max_win_rate": 0.40,
    },
    "loss_cap": {
        "max_loss_usd": -15.0,  # Phase 5: -$5 → -$15 (7.5% of $200 sizing / 30-trade window)
    },
    "frequency": {
        "max_age_h": 24.0,
        "min_n": 3,
    },
}


def _is_win(pos) -> bool:
    """Determine if a closed position is a winner (net positive after fee).

    Pure function — operates on Position-like objects with attributes:
      direction: int (+1 long)
      exit_price: float
      entry_price: float
      fee_round_trip: float

    Returns True if net_pct > 0.
    """
    if pos.exit_price <= 0 or pos.entry_price <= 0:
        return False
    gross = pos.direction * (pos.exit_price - pos.entry_price) / pos.entry_price
    net = gross - pos.fee_round_trip
    return net > 0


def _realized_usd(positions: list) -> float:
    """Sum of net_usd across all closed positions.

    Pure function — no I/O.
    """
    total = 0.0
    for pos in positions:
        if pos.exit_price <= 0 or pos.entry_price <= 0:
            continue
        gross = pos.direction * (pos.exit_price - pos.entry_price) / pos.entry_price
        net = gross - pos.fee_round_trip
        total += net * pos.size_usd
    return total


def check_deprecate(
    hypo_id: str,
    closed_positions: list,
    started_at_ms: int,
    now_ms: Optional[int] = None,
) -> Optional[str]:
    """Check if a HYPO should be auto-deprecated.

    Args:
        hypo_id: HYPO identifier string (for logging only — logic is position-based).
        closed_positions: list of closed Position objects with attributes:
            direction, exit_price, entry_price, fee_round_trip, size_usd.
        started_at_ms: HYPO start timestamp in milliseconds.
        now_ms: current timestamp ms (default: time.time() * 1000).

    Returns:
        Deprecation reason string if triggered, None otherwise.

    Pure function core — now_ms default uses time.time() (shell boundary).
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    n = len(closed_positions)

    # Trigger 1: fast_fail — n >= 20 and win_rate < 40%
    fast_cfg = DEPRECATE_TRIGGERS["fast_fail"]
    if n >= fast_cfg["min_n"]:
        wins = sum(1 for p in closed_positions if _is_win(p))
        win_rate = wins / n
        if win_rate < fast_cfg["max_win_rate"]:
            return (
                f"fast_fail n={n} win_rate={win_rate:.0%} < {fast_cfg['max_win_rate']:.0%}"
            )

    # Trigger 2: loss_cap — realized PnL < -$15
    loss_cfg = DEPRECATE_TRIGGERS["loss_cap"]
    realized = _realized_usd(closed_positions)
    if realized < loss_cfg["max_loss_usd"]:
        return f"loss_cap realized=${realized:.2f} < ${loss_cfg['max_loss_usd']:.2f}"

    # Trigger 3: frequency — 24h+ elapsed and n < 3
    freq_cfg = DEPRECATE_TRIGGERS["frequency"]
    age_h = (now_ms - started_at_ms) / 3_600_000
    if age_h >= freq_cfg["max_age_h"] and n < freq_cfg["min_n"]:
        return f"frequency 24h+ n={n} < {freq_cfg['min_n']} (signal too infrequent)"

    return None


def check_all_hypos(
    hypos: list[dict],
    get_closed_positions: callable,
    get_started_at_ms: callable,
    now_ms: Optional[int] = None,
) -> list[tuple[str, str]]:
    """Batch check all HYPOs for deprecation triggers.

    Args:
        hypos: list of HYPO config dicts with 'hypo_id' key.
        get_closed_positions: callable(hypo_id) -> list of closed positions.
        get_started_at_ms: callable(hypo_id) -> int start timestamp ms.
        now_ms: current timestamp ms (default: time.time() * 1000).

    Returns:
        list of (hypo_id, reason) for HYPOs that should be deprecated.

    Shell function — calls I/O accessors (get_closed_positions).
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    to_deprecate: list[tuple[str, str]] = []
    for hypo in hypos:
        hid = hypo["hypo_id"]
        closed = get_closed_positions(hid)
        started = get_started_at_ms(hid)
        reason = check_deprecate(hid, closed, started, now_ms=now_ms)
        if reason is not None:
            to_deprecate.append((hid, reason))
    return to_deprecate
