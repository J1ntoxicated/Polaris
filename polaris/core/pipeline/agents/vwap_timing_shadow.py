"""G4 VWAP/AVWAP entry-timing SHADOW (frontgate-scan item #6, behavior-0).

DEMO/PAPER, virtual capital. Measures how far the G4 decision-time price sits
from two known-at-time volume-weighted anchors — session VWAP (since the most
recent UTC session-open boundary) and signal-day AVWAP (since UTC midnight) —
so a later /debate can ask "does entering closer to VWAP improve fill bps?"
with data. TAG-ONLY: this module never returns a decision, never touches
``det_result``/``payload``, and is never read by any live gate/sizing/exit
path (frontgate integration-blueprint invariant ①).

``bars.vwap`` is hardcoded 0.0 for okx/capital — both anchors are computed
here from typical price ``(high+low+close)/3`` volume-weighted over the known
bar window (verifier note #1). Anchors reuse
``polaris.core.indicators.production.session_anchor_index`` /
``_SESSION_OPEN_HOURS`` for the session boundary and the same
``(ts // 86400) * 86400`` day-start pattern for the signal-day AVWAP anchor
(verifier note #4) — no new anchor logic is invented.

Two-stage design (mirrors ``gate_kill_counterfactuals`` /
``_production_counterfactual.py``, verifier note #3):
  1. :func:`log_vwap_timing_shadow` — G4 decision-time row: run_id/signal_id/
     venue/symbol/decision_ts + both anchors + the decision-time distance
     (bps, known-at-time only).
  2. ``polaris.scripts.vwap_timing_resolve`` (offline) — backfills the
     ACTUAL fill price via ``gate_events.position_id`` → ``fills`` (a G4
     decision-time mid predates G5 sizing/order placement and can drift from
     the real fill) so the promotion metric is judged on real fills, not the
     G4-time mid.

A distinct ``vwap_timing_shadow`` table (NOT ``gate_shadow_events`` — the
columns don't fit, and VWAP distance must never reach
``ShadowDecision.flags``/``payload["watch_flags"]``, which feed judge-call
escalation — see ``_shadow_rules.py`` / verifier note #2).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Final

from polaris.core.indicators.production import session_anchor_index
from polaris.strategies.base import BarView

logger = logging.getLogger(__name__)

__all__ = [
    "MIN_VOLUME_BARS",
    "VwapAnchor",
    "compute_session_vwap",
    "compute_signal_day_avwap",
    "distance_bps",
    "log_vwap_timing_shadow",
]

# Stratified low-liquidity guard (verifier note #6): an anchor window needs at
# least this many volume>0 bars before its VWAP is trusted — OKX/Capital run
# 71.8%/59.9% zero-volume 1m bars, so a thin window otherwise 1-2-tick-fills a
# "VWAP" that is really one stale print.
MIN_VOLUME_BARS: Final[int] = 3

_INSUFFICIENT_VOLUME = "insufficient_volume_bars"


@dataclass(frozen=True, slots=True)
class VwapAnchor:
    """One anchor's result — ``price=None`` whenever ``reason`` is non-empty."""

    price: float | None
    bar_count: int
    reason: str = ""


def _typical_price_vwap(bars: list[BarView]) -> tuple[float | None, int]:
    """Volume-weighted typical-price VWAP over ``bars`` (volume>0 only)."""
    num = 0.0
    den = 0.0
    n = 0
    for b in bars:
        if b.volume <= 0.0:
            continue
        typical = (b.high + b.low + b.close) / 3.0
        num += typical * b.volume
        den += b.volume
        n += 1
    if n < MIN_VOLUME_BARS or den <= 0.0:
        return None, n
    return num / den, n


def compute_session_vwap(bar_views: list[BarView], *, decision_ts: int) -> VwapAnchor:
    """Session VWAP since the most-recent true session-open bar (reused anchor)."""
    idx = session_anchor_index(bar_views, now_ts=decision_ts)
    if idx is None:
        return VwapAnchor(price=None, bar_count=0, reason="no_session_anchor")
    price, n = _typical_price_vwap(bar_views[idx:])
    if price is None:
        return VwapAnchor(price=None, bar_count=n, reason=_INSUFFICIENT_VOLUME)
    return VwapAnchor(price=price, bar_count=n)


def compute_signal_day_avwap(bar_views: list[BarView], *, decision_ts: int) -> VwapAnchor:
    """Signal-day AVWAP since UTC midnight (``session_anchor_index``'s own
    ``day_start = (ts // 86400) * 86400`` pattern, reused verbatim)."""
    day_start = (int(decision_ts) // 86400) * 86400
    window = [b for b in bar_views if b.ts >= day_start]
    if not window:
        return VwapAnchor(price=None, bar_count=0, reason="no_day_bars")
    price, n = _typical_price_vwap(window)
    if price is None:
        return VwapAnchor(price=None, bar_count=n, reason=_INSUFFICIENT_VOLUME)
    return VwapAnchor(price=price, bar_count=n)


def distance_bps(current_price: float, anchor_price: float) -> float:
    """Signed distance of ``current_price`` from ``anchor_price``, in bps."""
    if anchor_price <= 0.0:
        return 0.0
    return (current_price - anchor_price) / anchor_price * 10_000.0


def log_vwap_timing_shadow(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    venue: str,
    symbol: str,
    decision_ts: int,
    current_price: float,
    bar_views: list[BarView],
) -> None:
    """Append one ``vwap_timing_shadow`` row (no-op when ``conn`` is None or
    ``current_price`` is unusable). ``bar_views`` must already be the
    known-at-time window (``_frontgate_bars.fetch_known_bars``) — this
    function does not re-filter by look-ahead itself."""
    if conn is None or current_price <= 0.0:
        return
    session = compute_session_vwap(bar_views, decision_ts=decision_ts)
    avwap = compute_signal_day_avwap(bar_views, decision_ts=decision_ts)
    session_dist = (
        distance_bps(current_price, session.price) if session.price is not None else None
    )
    avwap_dist = (
        distance_bps(current_price, avwap.price) if avwap.price is not None else None
    )
    try:
        conn.execute(
            """
            INSERT INTO vwap_timing_shadow
                (event_id, run_id, signal_id, venue, symbol, decision_ts,
                 current_price, session_vwap, session_vwap_bar_count,
                 session_vwap_distance_bps, session_vwap_reason,
                 avwap, avwap_bar_count, avwap_distance_bps, avwap_reason,
                 created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, run_id, signal_id, venue, symbol, int(decision_ts),
                float(current_price),
                session.price, session.bar_count, session_dist, session.reason,
                avwap.price, avwap.bar_count, avwap_dist, avwap.reason,
                int(decision_ts),
            ),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[vwap_timing_shadow] log dropped run_id=%s signal_id=%s: %r",
            run_id, signal_id, exc,
        )
