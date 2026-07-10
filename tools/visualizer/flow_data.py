"""Flow-page backend data builder (DEMO/PAPER, display-only, READ-ONLY).

Powers the ``/flow`` "Living Pipeline River" page's ``/api/flow_stats``
endpoint: rolling-1h per-gate decision volume (edge width), per-venue
(OKX/Capital/Alpaca) breakdown per gate, drop-lane reasons (KILL/SKIPPED —
the only structurally-terminal ``GateDecision`` members, see
``polaris/core/pipeline/gate_state.py``; this is measurement honesty, the
same precedent as the globe's kill-sediment motes — NOT a new throttle,
nothing here blocks/sizes a trade), AI-judge shadow mismatches
(``gate_shadow_events``), recent AI-judge verdicts (mini ticker), a
header conversion-rate summary (signals -> sized -> fills), pts-classes
routing state (``strategy_class`` — PROVE/EARN/BENCH + score_F window fill,
for the visual-wall river's class particles/badges/gauges), and recent
``survivor_admissions`` rows (universe-candidate admissions; the table does
not exist yet as of this writing, so this degrades to an empty list —
fail-safe, not a hard dependency). Every query is read-only (``gate_events``
/ ``gate_shadow_events`` / ``positions`` / ``signals`` / ``fills`` /
``strategy_class`` / ``survivor_admissions``); a missing table or bad row
degrades to the empty shape rather than raising (display-only, never breaks
the poll).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Final

from polaris.scripts.dashboard.snapshot_q_common import _safe_query
from polaris.scripts.dashboard.snapshot_q_gate_feed import (
    _payload_strategy_symbol_reason,
)

__all__ = ["build_flow_stats", "FLOW_WINDOW_SEC"]

FLOW_WINDOW_SEC: Final[int] = 3600
_TOP_DROPS_N: Final[int] = 8
_VERDICTS_N: Final[int] = 3

# Terminal (non-flow) GateDecision members — see gate_state.GateDecision. Every
# other member (PASS/MODIFY/PROCEED/HOLD/ADJUST_EXIT/EXIT_NOW/SWAP_STRATEGY/
# SIZED/REFLECTED) is a flow-forward decision, not a drop.
_DROP_DECISIONS: Final[frozenset[str]] = frozenset({"KILL", "SKIPPED"})

_GATE_LABELS: Final[dict[int, str]] = {
    1: "Universe", 2: "Signal", 3: "Validator", 4: "PreEntry",
    5: "Sizer", 6: "Monitor", 7: "Exit", 8: "Reflector",
}

# EntryJudgeVerdict / ExitJudgeVerdict (polaris/core/pipeline/agents/ai_judge.py)
# mapped to the flow page's pulse-ring color. gpt_ok_salvaged (a malformed-but-
# parseable GPT response, ai_judge._salvage_verdict) always reads magenta
# regardless of the salvaged verdict itself.
_VERDICT_COLOR: Final[dict[str, str]] = {
    "PROCEED": "dim", "PROTECT": "dim",
    "SIZE_UP": "green",
    "EXTEND": "amber", "TIGHTEN_ON_CONFIRMED_DECAY": "amber",
    "STRENGTHEN_EVIDENCE": "amber", "REFINE_TIMING": "amber",
}

# ``gate_events.model_used`` is NOT a reliable "AI was called" signal: the judge
# only ANNOTATES the deterministic result (``ai_judge.apply_entry_verdict`` /
# ``apply_exit_verdict`` copy ``model_used=deterministic_result.model_used``
# unchanged — it stays "python" even when GPT was consulted). The honest signal
# is ``payload['ai_judge']['escalation']``: every value except "no_client" (no
# GPT client configured → immediate fallback, no network call) means a real GPT
# call was attempted, whether it succeeded, timed out, or errored.
_NO_REAL_CALL_ESCALATIONS: Final[frozenset[str]] = frozenset({"", "no_client"})


def _venue_of(venue: str | None, instrument_id: str | None) -> str:
    """okx/capital/alpaca lane key from ``positions.venue`` or a
    ``signals.instrument_id`` (``venue:symbol``) prefix. Binance (data-only)
    folds into okx (mirrors ``polaris_graph`` venue conventions). Absent/unknown
    defaults to okx (matches ``polaris_graph._short_venue``)."""
    raw = (venue or "").strip().lower()
    if not raw and instrument_id:
        raw = str(instrument_id).split(":", 1)[0].strip().lower()
    raw = raw[:3]
    if raw == "cap":
        return "capital"
    if raw == "alp":
        return "alpaca"
    return "okx"


def _ai_judge_verdict(payload_json: str | None) -> dict[str, str] | None:
    """``{'verdict', 'escalation'}`` from ``payload['ai_judge']``, else None."""
    if not payload_json:
        return None
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    aj = data.get("ai_judge")
    if not isinstance(aj, dict):
        return None
    verdict = aj.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        return None
    return {"verdict": verdict, "escalation": str(aj.get("escalation") or "")}


def _pct(numer: int, denom: int) -> float:
    return round(100.0 * numer / denom, 1) if denom else 0.0


def _class_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-(venue, strategy_id) pts-classes routing state: current class
    (EARN/PROVE/BENCH/KILL) + a score_F window-fill fraction (``filled`` of
    ``window_w``). Feeds the flow page's PROVE-class particle styling,
    EARN/BENCH transition badges, and the per-strategy window gauge strip.
    Missing table -> empty (pre-pts-classes DBs).

    ``filled`` counts ``score_f_events`` rows — the evidence stream the
    transition FSM actually consumes (``_production_close_classes.py``
    assembles ``intent_scores`` from it). The ``strategy_class.intent_ring``
    column has NO production writer (vestigial: schema + boot-recovery reader
    only), so reading it here showed a permanent "0/20" while the judge was
    in fact fully fed (2026-07-10 gauge-honesty fix)."""
    counts: dict[tuple[str, str], int] = {}
    for v, s, n in _safe_query(
        conn,
        "SELECT venue, strategy_id, COUNT(*) FROM score_f_events "
        "GROUP BY venue, strategy_id",
    ):
        counts[(str(v), str(s))] = int(n or 0)
    rows = _safe_query(
        conn,
        "SELECT venue, strategy_id, strategy_class, window_w FROM strategy_class",
    )
    out: list[dict[str, Any]] = []
    for venue, strategy_id, klass, window_w in rows:
        w = int(window_w or 0)
        n_events = counts.get((str(venue), str(strategy_id)), 0)
        out.append({
            "venue": str(venue), "strategy_id": str(strategy_id),
            "strategy_class": str(klass or "PROVE"),
            "window_w": w, "filled": min(n_events, w) if w else 0,
        })
    return out


def _survivor_admissions_recent(conn: sqlite3.Connection, *, since: int) -> list[dict[str, Any]]:
    """Recent Layer-0 universe-candidate admissions ("NEW CANDIDATE" flash on
    the river's G1 column). ``survivor_admissions`` is a forward-looking table
    (not yet created by any writer as of this module's authoring) — the query
    degrades to ``[]`` via ``_safe_query`` until it lands, which is the
    intended fail-safe (spec: flash only if the table exists)."""
    rows = _safe_query(
        conn,
        "SELECT symbol, venue, admitted_ts FROM survivor_admissions "
        "WHERE admitted_ts > ? ORDER BY admitted_ts DESC LIMIT ?",
        (since, _TOP_DROPS_N),
    )
    return [
        {"symbol": str(sym), "venue": str(venue), "ts": int(ts or 0)}
        for sym, venue, ts in rows
    ]


def _strategy_activity(conn: sqlite3.Connection, *, since: int) -> list[dict[str, Any]]:
    """Per-(venue, strategy_id) 1h activity — signal admissions (G2 PASS) +
    entry fills — feeds the /flow "Flat Neural Map" page's Z2 strategy-node
    activity sizing (Jin 2026-07-10, feat/flat-neural-map). Two independent
    ``_safe_query`` calls (own JOIN, not the shared ``rows`` fetch above) so a
    ``signals``/``fills`` table missing ``strategy_id`` (older DB generation)
    degrades this field to ``[]`` without touching the existing gate/fill
    reads elsewhere in this module."""
    counts: dict[tuple[str, str], dict[str, int]] = {}

    def bump(venue: str, sid: str, field: str) -> None:
        if not sid:
            return
        counts.setdefault((venue, sid), {"signals_n": 0, "fills_n": 0})[field] += 1

    for inst, sid in _safe_query(
        conn,
        """SELECT s.instrument_id, s.strategy_id FROM gate_events ge
           JOIN signals s ON s.signal_id = ge.signal_id
           WHERE ge.gate_id = 2 AND ge.decision = 'PASS' AND ge.created_ts > ?""",
        (since,),
    ):
        bump(_venue_of(None, inst), str(sid or ""), "signals_n")

    for venue, sid in _safe_query(
        conn,
        "SELECT venue, strategy_id FROM fills WHERE ts_ms > ? AND is_close = 0",
        (since * 1000,),
    ):
        bump(_venue_of(str(venue or ""), None), str(sid or ""), "fills_n")

    return [
        {"venue": v, "strategy_id": sid, **c}
        for (v, sid), c in sorted(counts.items())
    ]


def build_flow_stats(conn: sqlite3.Connection, *, now_s: int) -> dict[str, Any]:
    """Assemble the ``/api/flow_stats`` payload (read-only, degrade-never-crash)."""
    since = now_s - FLOW_WINDOW_SEC
    rows = _safe_query(
        conn,
        """SELECT ge.gate_id, ge.decision, ge.payload_json,
                  p.venue, s.instrument_id, ge.created_ts
           FROM gate_events ge
           LEFT JOIN positions p ON p.position_id = ge.position_id
           LEFT JOIN signals s ON s.signal_id = ge.signal_id
           WHERE ge.created_ts > ? AND ge.decision IS NOT NULL""",
        (since,),
    )

    stage_totals: dict[int, int] = dict.fromkeys(_GATE_LABELS, 0)
    stage_venues: dict[int, dict[str, int]] = {
        g: {"okx": 0, "capital": 0, "alpaca": 0} for g in _GATE_LABELS
    }
    drop_reasons: Counter[tuple[int, str]] = Counter()
    ai_calls_n = 0
    signals_n = 0
    sized_n = 0
    g5_kill_n = 0
    g7_exits_n = 0
    g7_holds_n = 0
    verdicts: list[dict[str, Any]] = []

    for gid_raw, decision, payload, venue, inst, ts in rows:
        gid = int(gid_raw or 0)
        if gid not in stage_totals:
            continue
        dec = str(decision or "").upper()
        stage_totals[gid] += 1
        stage_venues[gid][_venue_of(venue, inst)] += 1
        if dec in _DROP_DECISIONS:
            _, _, reason = _payload_strategy_symbol_reason(payload)
            drop_reasons[(gid, reason or dec)] += 1
        if gid == 2 and dec == "PASS":
            signals_n += 1
        if gid == 5 and dec == "SIZED":
            sized_n += 1
        if gid == 5 and dec == "KILL":
            g5_kill_n += 1
        if gid == 7 and dec == "EXIT_NOW":
            g7_exits_n += 1
        if gid == 7 and dec == "HOLD":
            g7_holds_n += 1
        aj = _ai_judge_verdict(payload)
        if aj:
            if aj["escalation"] not in _NO_REAL_CALL_ESCALATIONS:
                ai_calls_n += 1
            verdicts.append({
                "gate_id": gid, "ts": int(ts or 0),
                "verdict": aj["verdict"], "escalation": aj["escalation"],
                "color": _VERDICT_COLOR.get(aj["verdict"], "dim"),
            })

    fills_venues = {"okx": 0, "capital": 0, "alpaca": 0}
    for venue, c in _safe_query(
        conn,
        "SELECT venue, COUNT(*) FROM fills WHERE ts_ms > ? AND is_close = 0 "
        "GROUP BY venue",
        (since * 1000,),
    ):
        fills_venues[_venue_of(str(venue or ""), None)] += int(c or 0)
    fills_n = sum(fills_venues.values())

    # G6 is per-tick (a position gets re-monitored roughly every 2min), so its
    # raw gate_events count (stage_totals[6], kept as `checks_n`) wildly
    # overstates activity vs. the per-journey gates (G1-G5, one event per
    # signal). `live_n` — distinct currently-open positions — is the honest
    # headline (mirrors polaris.scripts.dashboard.snapshot_sections's
    # `_gate_headline` gid==6 precedent). Degrades to 0 on a missing/older
    # schema (_safe_query), never raises.
    live_rows = _safe_query(conn, "SELECT COUNT(*) FROM positions WHERE status = 'open'")
    live_n = int(live_rows[0][0]) if live_rows and live_rows[0][0] else 0

    # Shadow mismatches (deterministic-vs-GPT disagreement, gate_shadow_events)
    # feed the SAME drop lane as KILL/SKIPPED — the page's "kill/skip/shadow"
    # legend promises all three, so a per-gate mismatch count is folded into
    # drop_reasons here rather than left as an orphaned top-level-only number.
    for gid_raw, c in _safe_query(
        conn,
        "SELECT gate_id, COUNT(*) FROM gate_shadow_events "
        "WHERE created_ts > ? AND mismatch = 1 GROUP BY gate_id",
        (since,),
    ):
        gid = int(gid_raw or 0)
        n = int(c or 0)
        if gid in _GATE_LABELS and n:
            drop_reasons[(gid, "shadow_mismatch")] += n
    shadow_n = sum(n for (_, r), n in drop_reasons.items() if r == "shadow_mismatch")

    top_drops = sorted(drop_reasons.items(), key=lambda kv: -kv[1])[:_TOP_DROPS_N]
    verdicts.sort(key=lambda v: -int(v["ts"]))
    classes = _class_rows(conn)
    survivor_admissions = _survivor_admissions_recent(conn, since=since)
    strategy_activity = _strategy_activity(conn, since=since)

    stages: list[dict[str, Any]] = []
    for g in sorted(_GATE_LABELS):
        row: dict[str, Any] = {
            "gate_id": g, "label": _GATE_LABELS[g],
            "total": stage_totals[g], "venues": stage_venues[g],
        }
        # Additive per-journey/per-tick split fields (Jin 2026-07-10 stage-
        # count semantics fix) — `total`/`venues` above are UNCHANGED for
        # back-compat; these are extra keys existing consumers just ignore.
        if g == 5:
            row["sized_n"] = sized_n
            row["kill_n"] = g5_kill_n
        elif g == 6:
            row["live_n"] = live_n
            row["checks_n"] = stage_totals[6]
        elif g == 7:
            row["exits_n"] = g7_exits_n
            row["holds_n"] = g7_holds_n
        stages.append(row)

    return {
        "window_sec": FLOW_WINDOW_SEC,
        "stages": stages,
        "fills": {"total": fills_n, "venues": fills_venues},
        "drops": [
            {"gate_id": g, "label": _GATE_LABELS.get(g, f"G{g}"), "reason": r, "n": n}
            for (g, r), n in top_drops
        ],
        "shadow_mismatch_n": shadow_n,
        "verdicts_recent": verdicts[:_VERDICTS_N],
        "classes": classes,
        "survivor_admissions_recent": survivor_admissions,
        "strategy_activity": strategy_activity,
        "summary": {
            "signals_n": signals_n, "sized_n": sized_n, "fills_n": fills_n,
            "signal_to_sized_pct": _pct(sized_n, signals_n),
            "sized_to_fill_pct": _pct(fills_n, sized_n),
            "drops_total": sum(drop_reasons.values()),
            "ai_calls_n": ai_calls_n,
        },
    }
