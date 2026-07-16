"""Polaris dashboard v1 — recent gate-event feed queries.

Best-effort decode of ``gate_events.payload_json`` into the live per-gate
decision feed (strategy/symbol/reason + per-gate rich detail). Split out of
``snapshot_queries.py`` to keep each module ≤500 LOC (move-only; no logic
change). Display-only — never a trading path.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Final

from polaris.scripts.dashboard.snapshot_models import GateEvent
from polaris.scripts.dashboard.snapshot_q_common import _safe_query

# Gate id → short label, kept LOCAL (snapshot_sections imports from this module,
# so this module must not import back from it). Mirror of the _GATE_LABELS map.
_GATE_EVENT_LABELS: Final[Mapping[int, str]] = {
    1: "Universe",
    2: "Strategy",
    3: "Validator",
    # 4 (PreEntry) retired — P2a gate diet 2026-07-16, G4 emits no events
    5: "Sizer",
    6: "Monitor",
    7: "Exit",
    8: "Reflector",
}

RECENT_GATE_EVENTS_N: Final[int] = 60


def _payload_strategy_symbol_reason(
    payload_json: str | None,
) -> tuple[str, str, str]:
    """Best-effort (strategy, symbol, reason) from a gate_events ``payload_json``.

    The table has no dedicated strategy/symbol columns — they live nested in the
    payload, and the shape varies by gate (``raw_signal`` for G2, ``validated_signal``
    for G3 MODIFY, top-level ``reason`` for G3 KILL / G6/G7, etc.). All lookups
    are graceful: a missing key / non-dict / bad JSON yields an empty string so
    the feed never crashes a refresh."""
    if not payload_json:
        return "", "", ""
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return "", "", ""
    if not isinstance(data, dict):
        return "", "", ""
    # The signal envelope can be nested under a few known keys.
    sig: dict[str, Any] = {}
    for key in ("raw_signal", "validated_signal", "signal", "sized_signal"):
        inner = data.get(key)
        if isinstance(inner, dict):
            sig = inner
            break
    strategy = str(sig.get("strategy_id") or data.get("strategy_id") or "")
    symbol = str(sig.get("symbol") or data.get("symbol") or "")
    reason = str(data.get("reason") or data.get("thesis_tag") or "")
    return strategy[:24], symbol[:18], reason[:80]


def _as_dict(v: Any) -> dict[str, Any]:
    """Narrow an arbitrary JSON value to a dict (empty when not a dict)."""
    return v if isinstance(v, dict) else {}


def _payload_detail(gate_id: int, payload_json: str | None) -> dict[str, Any]:
    """Per-gate rich detail for the live feed (display-only, all keys optional).

    G5 → the T4 size line (risk_pct/notional/scalar/tier/cell/leverage);
    G8 → the lesson (lesson_type/confidence); G1 → focus count. Other gates carry
    no extra detail (empty dict). Graceful on bad / absent JSON. NEVER feeds
    sizing/gating — pure feed chrome."""
    if not payload_json:
        return {}
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if gate_id == 5:
        sized = _as_dict(data.get("sized"))
        prop = _as_dict(sized.get("proposal"))
        out: dict[str, Any] = {}
        rp = sized.get("final_risk_pct")
        if isinstance(rp, (int, float)):
            out["risk_pct"] = round(float(rp) * 100.0, 3)
        nt = sized.get("final_notional_usd")
        if isinstance(nt, (int, float)):
            out["notional_usd"] = round(float(nt), 1)
        lev = sized.get("leverage")
        if isinstance(lev, (int, float)):
            out["leverage"] = float(lev)
        for k_src, k_dst in (
            ("continuous_scalar", "scalar"),
            ("tier_amplifier", "tier"),
            ("cell_routing_mult", "cell"),
        ):
            v = prop.get(k_src)
            if isinstance(v, (int, float)):
                out[k_dst] = round(float(v), 3)
        return out
    if gate_id == 8:
        out2: dict[str, Any] = {}
        lt = data.get("lesson_type")
        if isinstance(lt, str) and lt:
            out2["lesson_type"] = lt
        cf = data.get("confidence")
        if isinstance(cf, (int, float)):
            out2["confidence"] = round(float(cf), 3)
        return out2
    if gate_id == 1:
        focus = data.get("focus")
        if isinstance(focus, list):
            return {"focus_n": len(focus)}
    return {}


def _recent_gate_events(
    conn: sqlite3.Connection, *, n: int = RECENT_GATE_EVENTS_N
) -> list[GateEvent]:
    """Last ``n`` per-gate decisions (newest first) for the live gate feed.

    Display-only: only rows with a concrete ``decision`` are surfaced (a NULL
    decision is an in-flight ``request`` phase, not a verdict). strategy/symbol/
    reason are decoded best-effort from ``payload_json``, then back-filled via the
    linked position (``position_id``) and signal (``signal_id``) so HOLD/ADJUST
    monitor/exit events — whose payloads carry no signal envelope — still show a
    ticker + strategy label. Graceful empty when the table is absent."""
    # Volume guard (flow_not_block steady state): G6/G7 HOLD repeats every cycle
    # per open position (measured: G6 HOLD ≈ 99.97% of monitor events) and would
    # drown the meaningful trade-shaping decisions (G5 size / G6 ADJUST·EXIT·SWAP
    # / G7 ADJUST·EXIT / G8 lesson). Those repetitive HOLDs are still tallied in
    # the per-gate decision SUMMARY (``_gate_decisions``); here the feed carries
    # the notable decisions only.
    rows = _safe_query(
        conn,
        """SELECT ge.gate_id, ge.decision, ge.payload_json, ge.created_ts,
                  p.symbol, p.strategy_id, s.instrument_id, s.strategy_id
           FROM gate_events ge
           LEFT JOIN positions p ON p.position_id = ge.position_id
           LEFT JOIN signals s ON s.signal_id = ge.signal_id
           WHERE ge.decision IS NOT NULL
             AND NOT (ge.gate_id IN (6, 7) AND ge.decision = 'HOLD')
           ORDER BY ge.created_ts DESC
           LIMIT ?""",
        (n,),
    )
    out: list[GateEvent] = []
    for r in rows:
        gid = int(r[0] or 0)
        decision = str(r[1] or "").upper()
        strategy, symbol, reason = _payload_strategy_symbol_reason(r[2])
        # Back-fill blank labels: payload → positions → signals (display-only).
        symbol = symbol or str(r[4] or r[6] or "")[:18]
        strategy = strategy or str(r[5] or r[7] or "")[:24]
        out.append(
            GateEvent(
                gate_id=gid,
                label=_GATE_EVENT_LABELS.get(gid, f"G{gid}"),
                decision=decision,
                strategy=strategy,
                symbol=symbol,
                reason=reason,
                ts=int(r[3] or 0),
                detail=_payload_detail(gid, r[2]),
            )
        )
    return out
