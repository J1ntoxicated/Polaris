"""Layer 6 — regime flip SSOT (P0 stub).

Spec source: vault/30_components/layer-6-live-recalc.md (Q2).

Regime SSOT key: ``(venue, underlying_group_id)``. Cross-venue independence
preserved (OKX BTC vs Capital BTC may differ).

Confirmation rule:
  - ``crisis``: 1 close → flip immediately.
  - others: 2 consecutive 5m closes confirming the same candidate.

P0 stub: classification logic returns whatever the caller passes in
(`candidate_regime`). Real 3-axis classifier (trend / vol shock / chop) =
P1. Persistence + 2-close confirm + event log are real.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

REGIME_FLIP_CONFIRM_CLOSES: Final[int] = 2
REGIME_VALUES: Final[tuple[str, ...]] = ("bull_trend", "bear_trend", "chop", "crisis")


@dataclass(slots=True)
class RegimeFlipDecision:
    venue: str
    underlying_group_id: str
    from_regime: str
    to_regime: str
    confirmed: bool
    consecutive_count: int
    reason: str  # "immediate_crisis" / "confirmed_2x" / "pending_1x" / "no_change"


def classify_regime(  # P0 stub
    *,
    venue: str,
    underlying_group_id: str,
    candidate_regime: str,
    confidence: float = 0.5,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a regime classification record.

    P0 stub: trusts caller's ``candidate_regime``. Real classifier (4h EMA20/50
    cross + 24h ATR ratio + 5m-1h efficiency) = P1.
    """
    if candidate_regime not in REGIME_VALUES:
        raise ValueError(f"unknown regime {candidate_regime!r}")
    return {
        "venue": venue,
        "underlying_group_id": underlying_group_id,
        "regime": candidate_regime,
        "confidence": float(confidence),
        "evidence_json": json.dumps(evidence or {}, separators=(",", ":")),
    }


def detect_regime_flip(
    conn: sqlite3.Connection,
    *,
    venue: str,
    underlying_group_id: str,
    candidate: str,
    now_ts: int,
    evidence: dict[str, Any] | None = None,
    confidence: float = 0.5,
) -> RegimeFlipDecision:
    """Apply 2-consecutive-close rule (or immediate for ``crisis``).

    State machine on ``regime_state``:
      - row missing → first observation, write row, no flip.
      - candidate == current → reset consecutive_candidate, no flip.
      - candidate != current:
          - ``crisis`` → immediate flip.
          - else: increment ``consecutive_count`` for that candidate.
            On count ≥ 2 → flip + reset.

    ``evidence`` / ``confidence`` are alt-data EVIDENCE context (#6). They are
    persisted to ``regime_state.evidence_json`` / ``confidence`` on every write
    path as read-only context for G3/G7 — they do NOT alter the confirm gate.
    The candidate (which may have been tilted by an alt-data hint upstream)
    still has to clear the SAME 2-consecutive-close gate; evidence never
    bypasses it.
    """
    if candidate not in REGIME_VALUES:
        raise ValueError(f"unknown regime {candidate!r}")
    evidence_json = json.dumps(evidence or {}, separators=(",", ":"))
    conf = float(confidence)
    row = conn.execute(
        "SELECT regime, consecutive_candidate, consecutive_count "
        "FROM regime_state WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    if row is None:
        # First observation: persist the row but do NOT report a confirmed
        # flip — bootstrap is not the same as a regime change (codex Day 4
        # R1 P2 fix).
        conn.execute(
            "INSERT INTO regime_state "
            "(venue, underlying_group_id, regime, confidence, evidence_json, "
            " consecutive_candidate, consecutive_count, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, NULL, 0, ?)",
            (venue, underlying_group_id, candidate, conf, evidence_json, now_ts),
        )
        return RegimeFlipDecision(
            venue=venue,
            underlying_group_id=underlying_group_id,
            from_regime="",
            to_regime=candidate,
            confirmed=False,
            consecutive_count=0,
            reason="initial_seed",
        )
    cur_regime = row[0]
    cur_candidate = row[1]
    cur_count = int(row[2] or 0)

    if candidate == cur_regime:
        conn.execute(
            "UPDATE regime_state SET confidence = ?, evidence_json = ?, "
            "consecutive_candidate = NULL, "
            "consecutive_count = 0, updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (conf, evidence_json, now_ts, venue, underlying_group_id),
        )
        return RegimeFlipDecision(
            venue=venue,
            underlying_group_id=underlying_group_id,
            from_regime=cur_regime,
            to_regime=candidate,
            confirmed=False,
            consecutive_count=0,
            reason="no_change",
        )

    if candidate == "crisis":
        # Immediate flip
        conn.execute(
            "UPDATE regime_state SET regime = ?, confidence = ?, "
            "evidence_json = ?, consecutive_candidate = NULL, "
            "consecutive_count = 0, updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (candidate, conf, evidence_json, now_ts, venue, underlying_group_id),
        )
        _publish_event(conn, venue, underlying_group_id, cur_regime, candidate, now_ts)
        logger.warning(
            "[regime] %s/%s FLIP %s → %s (immediate_crisis)",
            venue,
            underlying_group_id,
            cur_regime,
            candidate,
        )
        return RegimeFlipDecision(
            venue=venue,
            underlying_group_id=underlying_group_id,
            from_regime=cur_regime,
            to_regime=candidate,
            confirmed=True,
            consecutive_count=1,
            reason="immediate_crisis",
        )

    # Non-crisis: 2-consecutive confirm
    new_count = cur_count + 1 if cur_candidate == candidate else 1

    if new_count >= REGIME_FLIP_CONFIRM_CLOSES:
        conn.execute(
            "UPDATE regime_state SET regime = ?, confidence = ?, "
            "evidence_json = ?, consecutive_candidate = NULL, "
            "consecutive_count = 0, updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (candidate, conf, evidence_json, now_ts, venue, underlying_group_id),
        )
        _publish_event(conn, venue, underlying_group_id, cur_regime, candidate, now_ts)
        logger.info(
            "[regime] %s/%s FLIP %s → %s (confirmed_2x)",
            venue,
            underlying_group_id,
            cur_regime,
            candidate,
        )
        return RegimeFlipDecision(
            venue=venue,
            underlying_group_id=underlying_group_id,
            from_regime=cur_regime,
            to_regime=candidate,
            confirmed=True,
            consecutive_count=new_count,
            reason="confirmed_2x",
        )
    conn.execute(
        "UPDATE regime_state SET confidence = ?, evidence_json = ?, "
        "consecutive_candidate = ?, "
        "consecutive_count = ?, updated_ts = ? "
        "WHERE venue = ? AND underlying_group_id = ?",
        (conf, evidence_json, candidate, new_count, now_ts, venue, underlying_group_id),
    )
    return RegimeFlipDecision(
        venue=venue,
        underlying_group_id=underlying_group_id,
        from_regime=cur_regime,
        to_regime=candidate,
        confirmed=False,
        consecutive_count=new_count,
        reason="pending_1x",
    )


def _publish_event(
    conn: sqlite3.Connection,
    venue: str,
    underlying_group_id: str,
    from_regime: str,
    to_regime: str,
    now_ts: int,
) -> None:
    """Append a ``regime_flip`` row to ``market_events``."""
    payload = json.dumps(
        {
            "underlying_group_id": underlying_group_id,
            "from_regime": from_regime,
            "to_regime": to_regime,
        },
        separators=(",", ":"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO market_events "
        "(ts, type, venue, symbol, payload_json) VALUES (?, 'regime_flip', ?, ?, ?)",
        (now_ts, venue, underlying_group_id, payload),
    )


def fetch_regime(
    conn: sqlite3.Connection, *, venue: str, underlying_group_id: str
) -> str | None:
    row = conn.execute(
        "SELECT regime FROM regime_state WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    return None if row is None else str(row[0])


__all__ = [
    "REGIME_FLIP_CONFIRM_CLOSES",
    "REGIME_VALUES",
    "RegimeFlipDecision",
    "classify_regime",
    "detect_regime_flip",
    "fetch_regime",
]
