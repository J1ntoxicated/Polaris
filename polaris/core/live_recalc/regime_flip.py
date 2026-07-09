"""Layer 6 — regime flip SSOT (P0 stub).

Spec source: vault/30_components/layer-6-live-recalc.md (Q2).

Regime SSOT key: ``(venue, underlying_group_id)``. Cross-venue independence
preserved (OKX BTC vs Capital BTC may differ).

Confirmation rule:
  - ``crisis``: 1 close → flip immediately (PRICE-derived only).
  - others: 2 consecutive 5m CLOSES confirming the same candidate. ``detect_
    regime_flip`` runs at TICK cadence (~5-10s); a candidate advances the
    consecutive count AT MOST ONCE per closed bar (``last_advanced_bar_id``
    dedup) so the gate measures distinct CLOSES, not ticks. Without this the
    gate confirmed in ~10s (24h live: 1233 flip-flops). SIGNAL-only — regime
    never sizes/blocks/exits.

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
# Bar-close confirm cadence: a candidate may advance the consecutive count AT
# MOST ONCE per closed bar of this width. detect_regime_flip runs at TICK
# cadence (~5-10s) in the production loop; without this dedup the "2-consecutive
# CLOSE" gate advanced per CALL and confirmed in ~10s (24h live: 1233 flips).
# Default bar = 5m. Callers may pass an explicit ``bar_id`` to override.
REGIME_CONFIRM_BAR_SEC: Final[int] = 300
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
    candidate_source: str = "price",
    bar_id: int | None = None,
) -> RegimeFlipDecision:
    """Apply 2-consecutive-close rule (or immediate for a PRICE ``crisis``).

    ``bar_id`` is the CLOSED-bar identity the candidate belongs to. This is the
    flip-flop fix: ``detect_regime_flip`` runs at TICK cadence (~5-10s) in the
    production loop, so advancing the consecutive count once per CALL defeated
    the "2-consecutive-close" hysteresis (24h live: 1233 flips). A candidate may
    now advance the count AT MOST ONCE per closed bar — a confirm requires the
    candidate to persist across two DISTINCT bars. ``bar_id`` defaults to
    ``now_ts // REGIME_CONFIRM_BAR_SEC`` (5m bucket) so every caller threading
    ``now_ts`` gets bar-close dedup automatically; callers with a real
    closed-bar timestamp may pass it explicitly. The immediate PRICE-crisis fast
    path is UNAFFECTED (sharp crash still acts now). SIGNAL-only — this only
    stabilizes the regime LABEL; it never sizes/blocks/exits (flow_not_block).

    State machine on ``regime_state``:
      - row missing → first observation, write row, no flip.
      - candidate == current → reset consecutive_candidate, no flip.
      - candidate != current:
          - ``crisis`` from PRICE → immediate flip.
          - ``crisis`` from EVIDENCE → 2-consecutive-close (no bypass).
          - else: increment ``consecutive_count`` for that candidate.
            On count ≥ 2 → flip + reset.

    ``candidate_source`` (P4 🔴BLOCKING) tags how the candidate was derived:
      * ``"price"`` (default) — the L3 price regime saw the drawdown; a price
        crisis keeps the immediate-flip fast path (sharp crash = act now).
      * ``"evidence"`` — the crisis was introduced by alt-data evidence tilt.
        Evidence must NEVER bypass the 2-consecutive-close confirm gate, so an
        evidence-derived crisis is confirmed exactly like any other non-crisis
        flip.

    ``evidence`` / ``confidence`` are alt-data EVIDENCE context (#6). They are
    persisted to ``regime_state.evidence_json`` / ``confidence`` on every write
    path as read-only context for G3/G7 — they do NOT alter the confirm gate.
    The candidate (which may have been tilted by an alt-data hint upstream)
    still has to clear the SAME 2-consecutive-close gate; evidence never
    bypasses it.
    """
    if candidate not in REGIME_VALUES:
        raise ValueError(f"unknown regime {candidate!r}")
    has_evidence = bool(evidence)
    evidence_json = json.dumps(evidence or {}, separators=(",", ":"))
    conf = float(confidence)
    # Closed-bar identity (flip-flop fix). Default = 5m bucket of now_ts so any
    # caller threading now_ts gets bar-close dedup without changes.
    if bar_id is None:
        bar_id = now_ts // REGIME_CONFIRM_BAR_SEC
    row = conn.execute(
        "SELECT regime, consecutive_candidate, consecutive_count, "
        "last_advanced_bar_id "
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
            " consecutive_candidate, consecutive_count, last_advanced_bar_id, "
            " updated_ts) "
            "VALUES (?, ?, ?, ?, ?, NULL, 0, NULL, ?)",
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
    cur_advanced_bar_id = row[3]  # None until a bar has advanced the count

    if candidate == cur_regime:
        # N1: an EVIDENCE-LESS tick must NOT wipe evidence/confidence written
        # by a prior evidence-bearing tick — only overwrite when new non-empty
        # evidence is supplied (durable G3/G7 context). The 2-close confirm gate
        # is untouched; this only governs which columns the UPDATE rewrites.
        if has_evidence:
            conn.execute(
                "UPDATE regime_state SET confidence = ?, evidence_json = ?, "
                "consecutive_candidate = NULL, "
                "consecutive_count = 0, last_advanced_bar_id = NULL, "
                "updated_ts = ? "
                "WHERE venue = ? AND underlying_group_id = ?",
                (conf, evidence_json, now_ts, venue, underlying_group_id),
            )
        else:
            conn.execute(
                "UPDATE regime_state SET consecutive_candidate = NULL, "
                "consecutive_count = 0, last_advanced_bar_id = NULL, "
                "updated_ts = ? "
                "WHERE venue = ? AND underlying_group_id = ?",
                (now_ts, venue, underlying_group_id),
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

    if candidate == "crisis" and candidate_source == "price":
        # Immediate flip — PRICE-derived crisis only (sharp crash = act now).
        # An EVIDENCE-derived crisis falls through to the 2-close gate below
        # (P4 🔴BLOCKING: evidence never bypasses the confirm gate).
        conn.execute(
            "UPDATE regime_state SET regime = ?, confidence = ?, "
            "evidence_json = ?, consecutive_candidate = NULL, "
            "consecutive_count = 0, last_advanced_bar_id = NULL, "
            "updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (candidate, conf, evidence_json, now_ts, venue, underlying_group_id),
        )
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

    # Non-crisis: 2-consecutive-CLOSE confirm with BAR-CLOSE dedup.
    same_candidate = cur_candidate == candidate
    # A candidate already pending in THIS closed bar must not advance again on a
    # later tick of the same bar — that is the flip-flop fix. The count holds at
    # its current value until a DISTINCT bar arrives. Evidence/confidence is
    # still refreshed (durable G3/G7 context), but the confirm gate does not move.
    if (
        same_candidate
        and cur_advanced_bar_id is not None
        and int(cur_advanced_bar_id) == bar_id
    ):
        if has_evidence:
            conn.execute(
                "UPDATE regime_state SET confidence = ?, evidence_json = ?, "
                "updated_ts = ? "
                "WHERE venue = ? AND underlying_group_id = ?",
                (conf, evidence_json, now_ts, venue, underlying_group_id),
            )
        else:
            conn.execute(
                "UPDATE regime_state SET updated_ts = ? "
                "WHERE venue = ? AND underlying_group_id = ?",
                (now_ts, venue, underlying_group_id),
            )
        return RegimeFlipDecision(
            venue=venue,
            underlying_group_id=underlying_group_id,
            from_regime=cur_regime,
            to_regime=candidate,
            confirmed=False,
            consecutive_count=cur_count,
            reason="pending_same_bar",
        )

    # New bar (or new candidate) → advance the count once, anchored to this bar.
    new_count = cur_count + 1 if same_candidate else 1

    if new_count >= REGIME_FLIP_CONFIRM_CLOSES:
        conn.execute(
            "UPDATE regime_state SET regime = ?, confidence = ?, "
            "evidence_json = ?, consecutive_candidate = NULL, "
            "consecutive_count = 0, last_advanced_bar_id = NULL, "
            "updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (candidate, conf, evidence_json, now_ts, venue, underlying_group_id),
        )
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
    # N1: pending UPDATE preserves prior evidence/confidence on an evidence-less
    # tick (only overwrite when new non-empty evidence is supplied). Counter +
    # candidate tracking — i.e. the confirm gate — is unchanged either way. The
    # advancing bar is recorded so further ticks within it do not re-advance.
    if has_evidence:
        conn.execute(
            "UPDATE regime_state SET confidence = ?, evidence_json = ?, "
            "consecutive_candidate = ?, "
            "consecutive_count = ?, last_advanced_bar_id = ?, updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (conf, evidence_json, candidate, new_count, bar_id, now_ts, venue,
             underlying_group_id),
        )
    else:
        conn.execute(
            "UPDATE regime_state SET consecutive_candidate = ?, "
            "consecutive_count = ?, last_advanced_bar_id = ?, updated_ts = ? "
            "WHERE venue = ? AND underlying_group_id = ?",
            (candidate, new_count, bar_id, now_ts, venue, underlying_group_id),
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


def fetch_regime(
    conn: sqlite3.Connection, *, venue: str, underlying_group_id: str
) -> str | None:
    row = conn.execute(
        "SELECT regime FROM regime_state WHERE venue = ? AND underlying_group_id = ?",
        (venue, underlying_group_id),
    ).fetchone()
    return None if row is None else str(row[0])


__all__ = [
    "REGIME_CONFIRM_BAR_SEC",
    "REGIME_FLIP_CONFIRM_CLOSES",
    "REGIME_VALUES",
    "RegimeFlipDecision",
    "classify_regime",
    "detect_regime_flip",
    "fetch_regime",
]
