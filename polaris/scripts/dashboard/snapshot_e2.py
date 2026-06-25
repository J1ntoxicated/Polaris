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
from collections.abc import Callable
from typing import Any, Final

from polaris.scripts.dashboard.snapshot_models import (
    AiShadowPanel,
    ClosedTrade,
    ContextIntelRow,
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


# ---------------------------------------------------------------------------
# CONTEXT/INTEL tab (Jin 2026-06-24)
# ---------------------------------------------------------------------------
# Every alt-data / context input the regime fuser weighs, the LATEST row per
# source from the read-only ``altdata_snapshot`` audit table, summarised to one
# display line. "The bot's eyes" surfaced for the operator. NEVER feeds trading.

# Per-source freshness window (s) — a row newer than this is "fresh" (green); it
# mirrors each collector's own ``ttl_sec`` so the panel greys a source out exactly
# when the bot would treat its cached evidence as stale (cache.py get()). Unknown
# sources fall back to a generous 2h window (best-effort observability).
_SOURCE_FRESH_SEC: Final[dict[str, int]] = {
    "okx_funding": 300,
    "crypto_fg": 1800,
    "fred_macro": 3600,
    "cftc_cot": 21600,
    "coinglass": 300,
    "myfxbook": 1800,
    "news_sentiment": 900,
}
_DEFAULT_FRESH_SEC: Final[int] = 7200


def _num(v: object) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _summarise_funding(p: dict[str, Any]) -> tuple[str, str]:
    """OKX funding — average funding rate across the nested per-instrument rows.

    Positive funding = longs paying shorts (crowded long → bullish positioning);
    deeply negative = crowded short (bearish). Read-only label only."""
    rates = [
        f for f in (_num(row.get("fundingRate")) for row in p.values()
                    if isinstance(row, dict)) if f is not None
    ]
    if not rates:
        return "no funding data", "neutral"
    avg = sum(rates) / len(rates)
    sig = "bullish" if avg > 0.00005 else "bearish" if avg < -0.00005 else "neutral"
    return f"avg funding {avg * 100:+.4f}% · {len(rates)} perp", sig


def _summarise_crypto_fg(p: dict[str, Any]) -> tuple[str, str]:
    """Crypto Fear & Greed (0=extreme fear, 100=extreme greed)."""
    val = _num(p.get("value"))
    label = str(p.get("label", "")) or "?"
    if val is None:
        return f"F&G {label}", "neutral"
    sig = "bullish" if val >= 55 else "bearish" if val <= 45 else "neutral"
    return f"F&G {int(val)} · {label}", sig


def _summarise_fred(p: dict[str, Any]) -> tuple[str, str]:
    """FRED macro — headline on VIX (risk-on/off). Elevated VIX = risk-off."""
    vix = _num(p.get("vix"))
    hy = _num(p.get("hy_spread"))
    parts: list[str] = []
    if vix is not None:
        parts.append(f"VIX {vix:.1f}")
    if hy is not None:
        parts.append(f"HY {hy:.0f}bps")
    summary = " · ".join(parts) if parts else "macro"
    # Risk-off (bearish for risk assets) when VIX is elevated.
    sig = "neutral"
    if vix is not None:
        sig = "bearish" if vix >= 25 else "bullish" if vix <= 15 else "neutral"
    return summary, sig


def _summarise_cot(p: dict[str, Any]) -> tuple[str, str]:
    """CFTC COT — average speculator net-spec percentile across mapped contracts.

    High percentile (crowded net-long vs the contract's own range) = trend-
    confirming bull; low = bear. Read-only positioning context label."""
    pctiles = [
        v for v in (_num(row.get("net_spec_pctile")) for row in p.values()
                    if isinstance(row, dict)) if v is not None
    ]
    if not pctiles:
        return "no COT data", "neutral"
    avg = sum(pctiles) / len(pctiles)
    sig = "bullish" if avg >= 0.66 else "bearish" if avg <= 0.34 else "neutral"
    return f"spec net {avg * 100:.0f}%ile · {len(pctiles)} contracts", sig


def _summarise_news(p: dict[str, Any]) -> tuple[str, str]:
    """News sentiment — relevance-weighted mean sentiment + headline count, plus
    the single most-relevant headline, across the collector's per-symbol payload.

    The ``NewsSentimentCollector`` (built separately) lands rows under
    source='news_sentiment' shaped per-symbol like funding/COT:
    ``{"AAPL": {"sentiment": +0.6, "relevance": 0.8, "magnitude": .., "n": 3,
    "headline": ".."}, ...}``. Fold to one display line; auto-displayed."""
    wsum = 0.0
    wt = 0.0
    total_n = 0
    best_rel = -1.0
    best_headline = ""
    for row in p.values():
        if not isinstance(row, dict):
            continue
        sent = _num(row.get("sentiment"))
        if sent is None:
            continue
        rel = _num(row.get("relevance"))
        weight = rel if (rel is not None and rel > 0.0) else 0.01
        wsum += sent * weight
        wt += weight
        total_n += int(_num(row.get("n")) or 1)
        head = str(row.get("headline", "")).strip()
        if head and (rel if rel is not None else 0.0) > best_rel:
            best_rel = rel if rel is not None else 0.0
            best_headline = head
    if wt <= 0.0:
        return "news", "neutral"
    agg = wsum / wt
    sig = "bullish" if agg > 0.15 else "bearish" if agg < -0.15 else "neutral"
    bits = [f"sentiment {agg:+.2f}"]
    if total_n:
        bits.append(f"{total_n} headlines")
    prefix = " · ".join(bits)
    if best_headline:
        head = best_headline if len(best_headline) <= 70 else best_headline[:67] + "…"
        return f"{prefix} · {head}", sig
    return prefix, sig


def _summarise_generic(p: dict[str, Any]) -> tuple[str, str]:
    """Fallback for a source with no bespoke extractor (e.g. a future collector or
    coinglass/myfxbook stubs): a compact key=value preview, neutral signal."""
    if not p:
        return "—", "neutral"
    parts: list[str] = []
    for k, v in list(p.items())[:3]:
        if isinstance(v, (int, float)):
            parts.append(f"{k}={v:g}")
        elif isinstance(v, str) and v:
            parts.append(f"{k}={v}")
        elif isinstance(v, dict):
            parts.append(f"{k}[{len(v)}]")
    return (" · ".join(parts) or "—"), "neutral"


_Summariser = Callable[[dict[str, Any]], tuple[str, str]]
_SUMMARISERS: Final[dict[str, _Summariser]] = {
    "okx_funding": _summarise_funding,
    "crypto_fg": _summarise_crypto_fg,
    "fred_macro": _summarise_fred,
    "cftc_cot": _summarise_cot,
    "news_sentiment": _summarise_news,
}


def _collect_context_intel(
    conn: sqlite3.Connection, *, now_s: int
) -> list[ContextIntelRow]:
    """LATEST ``altdata_snapshot`` row per source → one CONTEXT/INTEL line each.

    Read-only roll-up of every context input the bot weighs (funding · F&G · FRED
    macro · CFTC COT · news sentiment when present). For each distinct ``source``
    the freshest (max-ts) row is summarised to a one-line value + coarse direction
    + freshness. Display-only; NEVER read by sizing/gating/exit/strategy/loop.
    Graceful empty on a missing/empty table (older schema). Sorted by source name
    so the panel order is stable poll-to-poll."""
    rows = _safe_query(
        conn,
        """SELECT a.source, a.asset_class, a.ts, a.payload_json
             FROM altdata_snapshot a
             JOIN (SELECT source, MAX(ts) AS mts
                     FROM altdata_snapshot GROUP BY source) m
               ON a.source = m.source AND a.ts = m.mts
            ORDER BY a.source ASC""",
    )
    out: list[ContextIntelRow] = []
    for source, asset_class, ts, payload_json in rows:
        src = str(source)
        try:
            payload = json.loads(payload_json or "{}")
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        fn = _SUMMARISERS.get(src, _summarise_generic)
        latest_value, signal = fn(payload)
        age = max(0, int(now_s) - int(ts or 0))
        window = _SOURCE_FRESH_SEC.get(src, _DEFAULT_FRESH_SEC)
        out.append(
            ContextIntelRow(
                source=src,
                asset_class=str(asset_class or ""),
                latest_value=str(latest_value),
                signal=str(signal),
                age_sec=age,
                fresh=age < window,
            )
        )
    return out
