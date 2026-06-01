"""Polaris dashboard — E2 Trading-IA tab queries (REGIME / EXIT / AI).

Read-only section helpers for the rebuilt full-width tabs (Jin 2026-05-31 IA
rebuild). Each is best-effort: a missing table (older schema) or empty data
degrades to an empty rollup so the dashboard never crashes a paper loop. Split
into its own module so ``snapshot_sections.py`` stays focused and within the LOC
guideline. NEVER read by sizing / gating / exit / strategy / loop — pure display.

Sources (data/polaris.sqlite):
- ``regime_state``           — REGIME tab: per-(venue, group) regime + confidence
                               + layered evidence + 2-consecutive hysteresis.
- ``positions`` (exit_state) — EXIT tab: exit-FSM state distribution.
- ``gate_events`` (G6/G7)    — EXIT tab: monitor/adaptive-exit decision tallies.
- ``gate_shadow_events``     — AI tab: conductor shadow agreement (technical vs
                               GPT) by gate × regime.
- ``entry_admission_shadow`` — AI tab: edge-first would-suppress stats by regime.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Final

from polaris.scripts.dashboard.snapshot_models import (
    AiShadowPanel,
    ClosedTrade,
    EntryAdmissionStat,
    ExitReasonBar,
    ExitSurface,
    RegimeStateRow,
    ShadowAgreementRow,
)
from polaris.scripts.dashboard.snapshot_queries import (
    GATE_FUNNEL_LOOKBACK_SEC,
    _safe_query,
)

# Lookback for the AI shadow rollups (gate_shadow_events / entry_admission_shadow)
# — the same 1h window the gate funnel uses, so the AI tab and the funnel agree.
SHADOW_LOOKBACK_SEC: Final[int] = GATE_FUNNEL_LOOKBACK_SEC

# The loser-timeout exit reason emitted by ``_recent_closed_trades`` (a losing
# trade held past the 600s SL/TIME boundary). Surfaced as its own count on the
# EXIT tab.
_LOSER_TIMEOUT_REASON: Final[str] = "TIME"

# evidence_json key candidates per layer (best-effort decode — the classifier may
# emit any of these aliases). First present key wins; absent → "".
_EVIDENCE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "l1": ("l1_macro", "l1", "macro"),
    "l2": ("l2_asset_class", "l2", "asset_class"),
    "l3": ("l3_price_action", "l3", "price_action"),
}


# ---------------------------------------------------------------------------
# REGIME tab
# ---------------------------------------------------------------------------


def _decode_evidence(evidence_json: str) -> tuple[str, str, str]:
    """Decode the layered (L1 macro / L2 asset-class / L3 price-action) labels.

    Best-effort: a non-dict / unparseable / empty payload yields ("", "", "").
    For each layer the first present alias key wins; non-string values are
    stringified.
    """
    try:
        data = json.loads(evidence_json or "{}")
    except (ValueError, TypeError):
        return "", "", ""
    if not isinstance(data, dict):
        return "", "", ""
    out: list[str] = []
    for layer in ("l1", "l2", "l3"):
        label = ""
        for key in _EVIDENCE_KEYS[layer]:
            if key in data and data[key] not in (None, ""):
                label = str(data[key])
                break
        out.append(label)
    return out[0], out[1], out[2]


def _regime_states(conn: sqlite3.Connection) -> list[RegimeStateRow]:
    """Per-(venue, asset-group) live regime rows for the REGIME tab.

    Reads ``regime_state`` (the classifier output): regime + confidence +
    layered evidence + 2-consecutive hysteresis. Ordered venue, then group.
    Graceful empty list when the table is missing/empty.
    """
    rows = _safe_query(
        conn,
        """SELECT venue, underlying_group_id, regime, confidence,
                  evidence_json, consecutive_candidate, consecutive_count,
                  updated_ts
           FROM regime_state
           ORDER BY venue ASC, underlying_group_id ASC""",
    )
    out: list[RegimeStateRow] = []
    for r in rows:
        l1, l2, l3 = _decode_evidence(str(r[4] or "{}"))
        out.append(
            RegimeStateRow(
                venue=str(r[0] or ""),
                group_id=str(r[1] or ""),
                regime=str(r[2] or "chop"),
                confidence=float(r[3] or 0.0),
                consecutive_candidate=str(r[5] or ""),
                consecutive_count=int(r[6] or 0),
                updated_ts=int(r[7] or 0),
                evidence_l1=l1,
                evidence_l2=l2,
                evidence_l3=l3,
            )
        )
    return out


# ---------------------------------------------------------------------------
# EXIT tab
# ---------------------------------------------------------------------------


def _exit_surface(
    conn: sqlite3.Connection,
    *,
    now_s: int,
    recent_trades: list[ClosedTrade] | None = None,
) -> ExitSurface:
    """Exit-engine observability rollup for the EXIT tab — read-only.

    - ``fsm_states``: distribution of the OPEN positions' ``exit_state`` FSM
      label (open / touched / protected / harvest / ...).
    - ``g6_decisions`` / ``g7_decisions``: G6 Monitor / G7 Adaptive-Exit gate
      decision tallies over the funnel lookback window.
    - ``reasons``: exit-reason histogram over ``recent_trades`` (passed in by
      ``collect_snapshot`` to reuse the already-paired close fills; read here
      only as a fallback is intentionally avoided — an empty list when absent).
    - ``loser_timeout_n``: count of loser-timeout exits in ``recent_trades``.

    Graceful zero when the source tables/lists are empty.
    """
    # FSM state distribution — open positions only (status not closed/cancelled).
    fsm_rows = _safe_query(
        conn,
        """SELECT COALESCE(exit_state, 'open') AS st, COUNT(*)
           FROM positions
           WHERE status NOT IN ('closed', 'cancelled', 'reconciled')
           GROUP BY st""",
    )
    fsm_states = {str(r[0] or "open"): int(r[1] or 0) for r in fsm_rows}

    # G6 / G7 gate decision tallies over the 1h funnel window.
    gate_rows = _safe_query(
        conn,
        """SELECT gate_id, COALESCE(decision, '?') AS dec, COUNT(*)
           FROM gate_events
           WHERE created_ts >= ? AND gate_id IN (6, 7)
           GROUP BY gate_id, dec""",
        (now_s - GATE_FUNNEL_LOOKBACK_SEC,),
    )
    g6: dict[str, int] = {}
    g7: dict[str, int] = {}
    for gid, dec, cnt in gate_rows:
        target = g6 if int(gid) == 6 else g7
        target[str(dec or "?").upper()] = int(cnt or 0)

    # Exit-reason histogram + loser-timeout count from the recent closed trades.
    reason_counts: dict[str, int] = {}
    loser_timeout_n = 0
    for t in recent_trades or []:
        reason = str(t.exit_reason or "?")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if reason == _LOSER_TIMEOUT_REASON:
            loser_timeout_n += 1
    reasons = [
        ExitReasonBar(reason=k, count=v)
        for k, v in sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return ExitSurface(
        fsm_states=fsm_states,
        loser_timeout_n=loser_timeout_n,
        reasons=reasons,
        g6_decisions=g6,
        g7_decisions=g7,
    )


# ---------------------------------------------------------------------------
# AI tab — conductor shadow agreement + entry-admission shadow
# ---------------------------------------------------------------------------


def _shadow_agreement(
    conn: sqlite3.Connection, *, now_s: int
) -> list[ShadowAgreementRow]:
    """Conductor shadow agreement per (gate_id, regime) — read-only.

    Over ``gate_shadow_events`` (G3/G4 deterministic-vs-GPT): how often the
    technical decision matched the live GPT decision. ``n`` counts only rows
    with a known GPT decision (non-empty ``gpt_decision``); ``mismatch_n`` is
    where they differed; ``n_no_gpt`` counts deterministic-only rows (GPT absent,
    excluded from the ratio). Ordered by gate then regime. Graceful empty list.
    """
    rows = _safe_query(
        conn,
        """SELECT gate_id, regime,
                  SUM(CASE WHEN gpt_decision != '' THEN 1 ELSE 0 END) AS n_gpt,
                  SUM(CASE WHEN gpt_decision != '' AND mismatch = 1
                           THEN 1 ELSE 0 END) AS mismatch_n,
                  SUM(CASE WHEN gpt_decision = '' THEN 1 ELSE 0 END) AS n_no_gpt
           FROM gate_shadow_events
           WHERE created_ts >= ?
           GROUP BY gate_id, regime
           ORDER BY gate_id ASC, regime ASC""",
        (now_s - SHADOW_LOOKBACK_SEC,),
    )
    out: list[ShadowAgreementRow] = []
    for r in rows:
        n = int(r[2] or 0)
        mismatch_n = int(r[3] or 0)
        agree_pct = ((n - mismatch_n) / n * 100.0) if n else 0.0
        out.append(
            ShadowAgreementRow(
                gate_id=int(r[0] or 0),
                regime=str(r[1] or ""),
                n=n,
                mismatch_n=mismatch_n,
                agree_pct=agree_pct,
                n_no_gpt=int(r[4] or 0),
            )
        )
    return out


def _entry_admission_stats(
    conn: sqlite3.Connection, *, now_s: int
) -> list[EntryAdmissionStat]:
    """Entry-admission shadow would-suppress stats per regime — read-only.

    Over ``entry_admission_shadow``: per regime, total evaluations + how many the
    edge-first rule WOULD suppress (net of the real round-trip fee). Pure SHADOW
    telemetry (behavior 0). Ordered by regime. Graceful empty list.
    """
    rows = _safe_query(
        conn,
        """SELECT regime, COUNT(*) AS n,
                  SUM(CASE WHEN would_suppress = 1 THEN 1 ELSE 0 END) AS sup_n
           FROM entry_admission_shadow
           WHERE created_ts >= ?
           GROUP BY regime
           ORDER BY regime ASC""",
        (now_s - SHADOW_LOOKBACK_SEC,),
    )
    out: list[EntryAdmissionStat] = []
    for r in rows:
        n = int(r[1] or 0)
        sup_n = int(r[2] or 0)
        out.append(
            EntryAdmissionStat(
                regime=str(r[0] or ""),
                n=n,
                would_suppress_n=sup_n,
                suppress_pct=(sup_n / n * 100.0) if n else 0.0,
            )
        )
    return out


def _ai_shadow_panel(conn: sqlite3.Connection, *, now_s: int) -> AiShadowPanel:
    """Assemble the AI-tab shadow rollup (conductor agreement + admission)."""
    admission = _entry_admission_stats(conn, now_s=now_s)
    total_n = sum(a.n for a in admission)
    suppress_n = sum(a.would_suppress_n for a in admission)
    return AiShadowPanel(
        shadow_agreement=_shadow_agreement(conn, now_s=now_s),
        admission=admission,
        admission_total_n=total_n,
        admission_suppress_n=suppress_n,
    )
