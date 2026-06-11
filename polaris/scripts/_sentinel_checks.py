"""Sentinel check functions — W1 deterministic live audit (observe-only).

DEMO/PAPER only. Each check is an independent pure-read function over the
live DB (opened read-only by the caller); a check failure never propagates
to other checks (the runner isolates exceptions). Checks NEVER write the
live DB and NEVER influence bot behavior — they only emit findings.

v1 checks (spec: .claude/plans/organic_ops_ai_free_2026-06-11.md §4):
  S1 price freshness        — per-venue quote_ticks MAX(ts) age, session-aware
  S2 exit→fill parity       — closed position must have its close fill
                              (exact fills.contribution_id join, stateful)
  S3 entry parity + rejects — fill↔position parity, reject repetition, surge
  S4 stop ratchet monotone  — per-position high-water stop watermark
  S6 feature availability   — tick inflow rate, in-session WS no-inflow,
                              flow-size death (bid_size+ask_size == 0 → OFI
                              proxy unusable; the 2026-06-11 Capital finding)
  S7 zombie-drain           — new status='reconciled' transitions (the P0
                              never-closed class drained by recovery)
S5 reconcile drift is DEFERRED to v1.1: no reconcile output table exists in
the live schema (fills/positions are SSOT; nothing to diff yet).

Time is injected (``now_ts``) for test determinism. Thresholds are
env-overridable via ``Thresholds.from_env`` (POLARIS_SENTINEL_*).
``Finding``/``Thresholds``/session clocks live in ``_sentinel_types``
(re-exported here — 500 LOC file-limit split).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any

from polaris.core.streams.config import VENUE_TO_STREAM
from polaris.scripts._sentinel_types import (
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARN,
    Finding,
    Thresholds,
    _venue_calendar,
    in_session,
)

__all__ = [
    "Finding",
    "Thresholds",
    "check_s1_price_freshness",
    "check_s2_exit_fill_parity",
    "check_s3_entry_parity_rejects",
    "check_s4_stop_ratchet",
    "check_s6_feature_availability",
    "check_s7_zombie_drain",
    "in_session",
]


# ---------------------------------------------------------------------------
# S1 — price freshness
# ---------------------------------------------------------------------------


def check_s1_price_freshness(
    live: sqlite3.Connection, now_ts: int, th: Thresholds
) -> list[Finding]:
    """Per-venue latest WS/REST tick age vs thresholds (session-aware)."""
    out: list[Finding] = []
    rows = live.execute(
        "SELECT venue, MAX(ts) FROM quote_ticks GROUP BY venue"
    ).fetchall()
    for venue_raw, max_ts in rows:
        if max_ts is None:
            continue
        venue = str(venue_raw)
        if not in_session(_venue_calendar(venue), now_ts):
            continue  # market closed → staleness is expected, skip judgment
        age = now_ts - int(max_ts)
        if age > th.s1_crit_sec:
            severity = SEV_CRITICAL
        elif age > th.s1_warn_sec:
            severity = SEV_WARN
        else:
            continue
        out.append(
            Finding(
                check_id="S1",
                severity=severity,
                subject=venue,
                summary=f"price stale on {venue}: last tick {age}s old (in-session)",
                detail={"age_sec": age, "last_tick_ts": int(max_ts)},
            )
        )
    return out


# ---------------------------------------------------------------------------
# S2 — closed-position ↔ close-fill ledger parity (stateful)
# ---------------------------------------------------------------------------


def check_s2_exit_fill_parity(
    live: sqlite3.Connection,
    now_ts: int,
    th: Thresholds,
    prev_state: dict[str, Any],
) -> tuple[list[Finding], dict[str, Any]]:
    """Every position flipped to status='closed' must have its close fill.

    Matching is the EXACT ledger join ``fills.contribution_id = position_id``
    (``is_close=1``) — never venue/time-window proximity, so an adjacent close
    of the same instrument cannot alias as this position's fill.

    Scope: ledger parity of CLOSED rows only. A position whose close decision
    never executed at the venue stays 'open' (or drains to 'reconciled') and
    is invisible here — that zombie-drain class is S7's job.

    Stateful: flagged positions are remembered (``pending``) and re-checked
    every pass — even after closed_ts leaves the lookback window — until the
    fill appears or the row leaves status='closed'; only then do they resolve.
    Returns (findings, new_state); the runner persists state in
    ``sentinel_state``.
    """
    raw_pending = prev_state.get("pending")
    pending: dict[str, Any] = raw_pending if isinstance(raw_pending, dict) else {}
    candidates: dict[str, tuple[str, int]] = {}
    for pid, venue, symbol, closed_ts in live.execute(
        "SELECT position_id, venue, symbol, closed_ts FROM positions "
        "WHERE status='closed' AND closed_ts IS NOT NULL "
        "AND closed_ts >= ? AND closed_ts <= ?",
        (now_ts - th.s2_lookback_sec, now_ts - th.s2_grace_sec),
    ).fetchall():
        candidates[str(pid)] = (f"{venue}:{symbol}", int(closed_ts))
    for pid_raw, meta in pending.items():
        pid = str(pid_raw)
        if pid in candidates:
            continue
        row = live.execute(
            "SELECT venue, symbol, closed_ts FROM positions "
            "WHERE position_id=? AND status='closed'",
            (pid,),
        ).fetchone()
        if row is None:
            continue  # no longer status='closed' → premise gone, drop
        fallback = meta.get("closed_ts", 0) if isinstance(meta, dict) else 0
        candidates[pid] = (
            f"{row[0]}:{row[1]}",
            int(row[2]) if row[2] is not None else int(fallback),
        )
    out: list[Finding] = []
    new_pending: dict[str, Any] = {}
    for pid in sorted(candidates):
        instrument, closed_ts_i = candidates[pid]
        n = live.execute(
            "SELECT COUNT(*) FROM fills WHERE contribution_id=? AND is_close=1",
            (pid,),
        ).fetchone()[0]
        if n:
            continue
        new_pending[pid] = {"closed_ts": closed_ts_i}
        out.append(
            Finding(
                check_id="S2",
                severity=SEV_CRITICAL,
                subject=pid,
                summary=(
                    f"closed position {pid} ({instrument}) has no close fill "
                    f"(fills.contribution_id join)"
                ),
                detail={"closed_ts": closed_ts_i, "instrument": instrument},
            )
        )
    return out, {"pending": new_pending}


# ---------------------------------------------------------------------------
# S3 — entry parity + reject anomaly
# ---------------------------------------------------------------------------


def check_s3_entry_parity_rejects(
    live: sqlite3.Connection, now_ts: int, th: Thresholds
) -> list[Finding]:
    """Entry-side integrity: fill↔position parity + reject repetition/surge."""
    out: list[Finding] = []
    cutoff = now_ts - th.s3_lookback_sec
    judge_before = now_ts - th.s3_grace_sec

    # (a) position opened recently must have an entry (is_close=0) fill nearby.
    # status='reconciled' rows are recovery artifacts (no entry fill by
    # construction) — excluded to avoid false criticals (F5).
    for pid, venue, symbol, opened_ts in live.execute(
        "SELECT position_id, venue, symbol, opened_ts FROM positions "
        "WHERE opened_ts > 0 AND opened_ts >= ? AND opened_ts <= ? "
        "AND status != 'reconciled'",
        (cutoff, judge_before),
    ).fetchall():
        instrument = f"{venue}:{symbol}"
        lo = (int(opened_ts) - th.s3_tol_sec) * 1000
        hi = (int(opened_ts) + th.s3_tol_sec) * 1000
        n = live.execute(
            "SELECT COUNT(*) FROM fills WHERE venue=? AND instrument_id=? "
            "AND is_close=0 AND ts_ms BETWEEN ? AND ?",
            (venue, instrument, lo, hi),
        ).fetchone()[0]
        if not n:
            out.append(
                Finding(
                    check_id="S3",
                    severity=SEV_CRITICAL,
                    subject=str(pid),
                    summary=(
                        f"position {pid} ({instrument}) has no entry fill "
                        f"within ±{th.s3_tol_sec}s of opened_ts"
                    ),
                    detail={"opened_ts": int(opened_ts), "instrument": instrument},
                )
            )

    # (b) entry fill recently must have a positions row nearby (reverse parity).
    for fid, venue, instrument_id, ts_ms in live.execute(
        "SELECT fill_id, venue, instrument_id, ts_ms FROM fills "
        "WHERE is_close=0 AND ts_ms >= ? AND ts_ms <= ?",
        (cutoff * 1000, judge_before * 1000),
    ).fetchall():
        symbol = str(instrument_id).split(":")[-1]
        fill_ts = int(ts_ms) // 1000
        n = live.execute(
            "SELECT COUNT(*) FROM positions WHERE venue=? AND symbol=? "
            "AND ABS(opened_ts - ?) <= ?",
            (venue, symbol, fill_ts, th.s3_tol_sec),
        ).fetchone()[0]
        if not n:
            out.append(
                Finding(
                    check_id="S3",
                    severity=SEV_CRITICAL,
                    subject=f"fill:{fid}",
                    summary=(
                        f"entry fill {fid} ({instrument_id}) has no positions row "
                        f"within ±{th.s3_tol_sec}s"
                    ),
                    detail={"fill_ts": fill_ts, "instrument": str(instrument_id)},
                )
            )

    # (c) repeated rejects on the same venue:symbol (e.g. DEP-USDT 51020 loop).
    counts: dict[str, int] = {}
    for (detail_json,) in live.execute(
        "SELECT detail_json FROM strategy_fault_events "
        "WHERE fault_type='reject' AND event_ts >= ?",
        (now_ts - th.s3_reject_window_sec,),
    ).fetchall():
        try:
            d = json.loads(detail_json or "{}")
        except ValueError:
            d = {}
        key = f"{d.get('venue', '?')}:{d.get('symbol', '?')}"
        counts[key] = counts.get(key, 0) + 1
    for key in sorted(counts):
        n = counts[key]
        if n >= th.s3_reject_warn_n:
            out.append(
                Finding(
                    check_id="S3",
                    severity=SEV_WARN,
                    subject=f"reject:{key}",
                    summary=(
                        f"{n} venue rejects for {key} in last "
                        f"{th.s3_reject_window_sec}s"
                    ),
                    detail={"count": n, "window_sec": th.s3_reject_window_sec},
                )
            )

    # (d) fault surge across all strategies/types.
    total = live.execute(
        "SELECT COUNT(*) FROM strategy_fault_events WHERE event_ts >= ?",
        (now_ts - th.s3_fault_window_sec,),
    ).fetchone()[0]
    if int(total) >= th.s3_fault_crit_n:
        out.append(
            Finding(
                check_id="S3",
                severity=SEV_CRITICAL,
                subject="fault_surge",
                summary=(
                    f"fault surge: {total} fault events in last "
                    f"{th.s3_fault_window_sec}s (>= {th.s3_fault_crit_n})"
                ),
                detail={"count": int(total), "window_sec": th.s3_fault_window_sec},
            )
        )
    return out


# ---------------------------------------------------------------------------
# S4 — stop ratchet monotonicity (18b851a / 8fc9aa1 invariant, live)
# ---------------------------------------------------------------------------


def check_s4_stop_ratchet(
    live: sqlite3.Connection,
    now_ts: int,
    th: Thresholds,
    prev_snapshot: dict[str, Any],
) -> tuple[list[Finding], dict[str, Any]]:
    """Compare open-position stops against a per-position high-water mark.

    Long stop may only ratchet up; short stop may only ratchet down. The
    state keeps the best stop ever seen per (position_id, side) — long max /
    short min ("hw"). While the current stop sits regressed past the
    watermark the finding re-fires EVERY pass (stays active); once the stop
    recovers to the watermark it stops firing and resolves naturally. (A
    pass-to-pass snapshot diff would false-resolve a persisting violation
    after one pass.) Legacy state rows carry only "stop" — used as the
    watermark seed, so an existing sentinel.sqlite keeps working. Returns
    (findings, new_snapshot); the runner persists it in ``sentinel_state``.

    SANCTIONED loosening: a G7 ADJUST_EXIT with ``widening_applied`` (the
    adaptive-exit gate deliberately giving a winner more room — live case
    2026-06-11 capital:J225, widened twice then harvested +0.62R) is NOT a
    ratchet break. Detected via gate_events; the finding downgrades to info
    and the watermark REBASES to the widened stop so the new baseline
    governs future checks. A regression with no recent widen stays critical.
    """
    del th  # no tunable threshold — monotonicity is exact (float-eps only)
    out: list[Finding] = []
    snapshot: dict[str, Any] = {}
    for pid, side, stop in live.execute(
        "SELECT position_id, side, stop_price FROM positions "
        "WHERE status='open' AND stop_price IS NOT NULL"
    ).fetchall():
        pid_s = str(pid)
        side_s = str(side).strip().lower()
        stop_f = float(stop)
        is_long = side_s in ("long", "buy")
        hw = stop_f
        old = prev_snapshot.get(pid_s)
        if isinstance(old, dict) and old.get("side") == side_s:
            raw_hw = old.get("hw", old.get("stop", stop_f))  # legacy: "stop"
            if raw_hw is None:
                raw_hw = stop_f
            try:
                prev_hw = float(raw_hw)
            except (TypeError, ValueError):
                prev_hw = stop_f
            hw = max(prev_hw, stop_f) if is_long else min(prev_hw, stop_f)
        snapshot[pid_s] = {
            "side": side_s, "hw": hw, "stop": stop_f, "ts": int(now_ts),
        }
        eps = 1e-9 * max(1.0, abs(hw))
        broke = (stop_f < hw - eps) if is_long else (stop_f > hw + eps)
        if broke:
            since_ts = now_ts - 3600
            if isinstance(old, dict):
                with contextlib.suppress(TypeError, ValueError):
                    since_ts = int(old.get("ts", since_ts))
            if _recent_g7_widen(live, pid_s, since_ts):
                snapshot[pid_s]["hw"] = stop_f  # widened stop = new baseline
                out.append(
                    Finding(
                        check_id="S4",
                        severity=SEV_INFO,
                        subject=pid_s,
                        summary=(
                            f"stop loosened by sanctioned G7 widen on {pid_s}: "
                            f"{side_s} stop {stop_f:.10g} (was high-water "
                            f"{hw:.10g}) — watermark rebased"
                        ),
                        detail={
                            "side": side_s, "high_water": hw, "stop": stop_f,
                            "g7_widen": True,
                        },
                    )
                )
                continue
            rel = "below" if is_long else "above"
            out.append(
                Finding(
                    check_id="S4",
                    severity=SEV_CRITICAL,
                    subject=pid_s,
                    summary=(
                        f"stop ratchet broken on {pid_s}: {side_s} stop "
                        f"{stop_f:.10g} {rel} high-water {hw:.10g}"
                    ),
                    detail={"side": side_s, "high_water": hw, "stop": stop_f},
                )
            )
    return out, snapshot


def _recent_g7_widen(
    live: sqlite3.Connection, position_id: str, since_ts: int,
) -> bool:
    """True when a G7 ADJUST_EXIT with ``widening_applied=true`` touched this
    position since ``since_ts`` (the sanctioned-loosening signature the G7
    widen path persists via gate_events)."""
    rows = live.execute(
        "SELECT payload_json FROM gate_events WHERE position_id = ? "
        "AND gate_id = 7 AND decision = 'ADJUST_EXIT' AND created_ts >= ?",
        (position_id, int(since_ts)),
    ).fetchall()
    for (payload,) in rows:
        try:
            if json.loads(payload or "{}").get("widening_applied") is True:
                return True
        except (ValueError, TypeError):
            continue
    return False


# ---------------------------------------------------------------------------
# S6 — feature availability (tick inflow + flow-size death)
# ---------------------------------------------------------------------------


def check_s6_feature_availability(
    live: sqlite3.Connection, now_ts: int, th: Thresholds
) -> list[Finding]:
    """Per-venue tick inflow + flow-size availability (OFI proxy liveness).

    - in-session venue with ZERO ticks in the no-inflow window → warn
      (WS dead / venue never connected — e.g. the alpaca no-connection case).
    - venue streaming ticks but max(bid_size+ask_size)==0 over the window →
      warn: flow features (OFI) are structurally dead (2026-06-11 Capital
      finding — quote stream carries no sizes).
    """
    out: list[Finding] = []
    window_counts: dict[str, tuple[int, float | None]] = {}
    for venue, cnt, max_size in live.execute(
        "SELECT venue, COUNT(*), MAX(bid_size + ask_size) FROM quote_ticks "
        "WHERE ts >= ? GROUP BY venue",
        (now_ts - th.s6_window_sec,),
    ).fetchall():
        window_counts[str(venue)] = (
            int(cnt),
            None if max_size is None else float(max_size),
        )
    recent_counts: dict[str, int] = {}
    for venue, cnt in live.execute(
        "SELECT venue, COUNT(*) FROM quote_ticks WHERE ts >= ? GROUP BY venue",
        (now_ts - th.s6_no_inflow_sec,),
    ).fetchall():
        recent_counts[str(venue)] = int(cnt)

    for venue in sorted(VENUE_TO_STREAM):
        cal = _venue_calendar(venue)
        live_session = in_session(cal, now_ts)
        if live_session and recent_counts.get(venue, 0) == 0:
            out.append(
                Finding(
                    check_id="S6",
                    severity=SEV_WARN,
                    subject=f"{venue}:no_inflow",
                    summary=(
                        f"no WS ticks from {venue} in last "
                        f"{th.s6_no_inflow_sec}s while in-session"
                    ),
                    detail={"calendar": cal, "window_sec": th.s6_no_inflow_sec},
                )
            )
        cnt, max_size = window_counts.get(venue, (0, None))
        rate = cnt / float(th.s6_window_sec) if th.s6_window_sec > 0 else 0.0
        if cnt >= th.s6_min_ticks_for_size and (max_size is None or max_size <= 0.0):
            out.append(
                Finding(
                    check_id="S6",
                    severity=SEV_WARN,
                    subject=f"{venue}:size_dead",
                    summary=(
                        f"flow size dead on {venue}: {cnt} ticks in "
                        f"{th.s6_window_sec}s but max(bid_size+ask_size)=0 "
                        f"(OFI proxy unusable)"
                    ),
                    detail={"tick_count": cnt, "tick_rate_per_sec": rate},
                )
            )
    return out


# ---------------------------------------------------------------------------
# S7 — zombie-drain (new status='reconciled' transitions; the P0 class)
# ---------------------------------------------------------------------------


def check_s7_zombie_drain(
    live: sqlite3.Connection,
    now_ts: int,
    th: Thresholds,
    prev_state: dict[str, Any],
) -> tuple[list[Finding], dict[str, Any]]:
    """NEW transitions to positions.status='reconciled' (observe-only warn).

    'reconciled' is the drain terminal of the P0 never-closed class: exit
    decided, venue close rejected/ghosted, the row never closed normally and
    recovery drained it. Snapshot diff against sentinel_state: the first pass
    only baselines pre-existing rows (no findings — installing the sentinel
    must not flag history); each later new transition emits one warn.
    """
    del now_ts, th  # event detection — no clock/threshold involved
    raw_ids = prev_state.get("ids")
    known: set[str] | None = (
        {str(x) for x in raw_ids} if isinstance(raw_ids, list) else None
    )
    current = sorted(
        str(pid)
        for (pid,) in live.execute(
            "SELECT position_id FROM positions WHERE status='reconciled'"
        ).fetchall()
    )
    out: list[Finding] = []
    if known is not None:
        for pid in current:
            if pid in known:
                continue
            out.append(
                Finding(
                    check_id="S7",
                    severity=SEV_WARN,
                    subject=pid,
                    summary=(
                        f"zombie-drain: position {pid} newly drained to "
                        f"status='reconciled' (close decided, never filled)"
                    ),
                    detail={"position_id": pid},
                )
            )
    return out, {"ids": current}
