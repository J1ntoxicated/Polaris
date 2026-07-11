"""News publication/ingestion timestamp audit + syndicate dedup SHADOW
(frontgate-scan item #7, G2/G3, behavior-0).

DEMO/PAPER, virtual capital. Per-article (not per-symbol-aggregate) shadow log
of the news collector's own already-scored output — the RAW publication-to-
ingestion delay + a deterministic syndicate-dedup group + the signal-time
sentiment/relevance, all recorded BEFORE ``news_sentiment._aggregate`` folds
everything down to a per-symbol MAX age (verifier note #4 — the per-article
distribution is what a later IC probe needs, and ``_aggregate`` only ever
kept the worst case). Reuses the news collector's EXISTING ~15min gpt-5-mini
cadence — zero new GPT calls (this module never calls an LLM).

Dedup rule (verifier note #3 — deterministic, no fuzzy similarity, no new
GPT call): articles whose NORMALIZED headline text is byte-identical share a
``dedup_group_id`` (the id of the first article seen with that text,
insertion order preserved) — collapses a syndicated wire story republished
across outlets/tickers into one group; a unique headline is its own
singleton group.

TAG-ONLY: this module never returns a decision and is never read by any live
gate/sizing/exit path (frontgate integration-blueprint invariant ①). It is
wired as an OPTIONAL, additive side-effect of ``NewsSentimentCollector.fetch``
(``shadow_conn=None`` default preserves the collector's existing return value
byte-for-byte — see that module's docstring).

Known gap (verifier note #2, deliberately NOT closed this wave): the
aggregate ``news_max_age_h`` field ``news_sentiment._aggregate`` already emits
is still a dead-read in ``altdata/fuser.py::_score_news`` (never copied into
the fused ``evidence`` dict, so ``judge_gate._observation_freshness`` and
``ai_judge``'s "news age" prompt suffix both silently no-op). Wiring that
1-line fix live would change ``evidence_robustness``/judge-escalation
thresholds and the judge's prompt text — an actual TRADING-relevant behavior
change, which this wave's behavior-0 invariant forbids with NO exception.
Left for a future non-shadow change.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["dedup_syndicate", "log_news_timing_shadow", "normalize_headline"]


def normalize_headline(headline: str) -> str:
    """Dedup key: lowercase, strip punctuation, collapse whitespace."""
    cleaned = "".join(
        c.lower() if (c.isalnum() or c.isspace()) else " " for c in headline
    )
    return " ".join(cleaned.split())


def dedup_syndicate(articles: list[dict[str, Any]]) -> dict[Any, str]:
    """``{article_id: dedup_group_id}`` — exact-normalized-headline grouping."""
    groups: dict[str, Any] = {}
    out: dict[Any, str] = {}
    for a in articles:
        key = normalize_headline(str(a.get("headline", "")))
        aid = a.get("id")
        group_id = groups.setdefault(key, aid) if key else aid
        out[aid] = str(group_id)
    return out


def _parse_publication_ts(created_at: Any) -> _dt.datetime | None:
    """Alpaca ``created_at`` ISO8601 -> aware UTC datetime (mirrors
    ``news_sentiment._headline_age_hours``'s parse — kept local so this
    module stays decoupled from that module's private helpers)."""
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        ts = _dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.UTC)
    return ts


def log_news_timing_shadow(
    conn: sqlite3.Connection | None,
    *,
    articles: list[dict[str, Any]],
    scored: dict[Any, dict[str, float]],
    now: _dt.datetime,
) -> int:
    """Append one ``news_timing_shadow`` row per (article x tagged symbol).

    No-op (returns 0) when ``conn`` is None or ``articles`` is empty. Fail-open
    on any ``sqlite3.Error`` (instrumentation must never break the collector's
    own return value). ``n_syndicate`` is the dedup group's article count
    (this fetch batch only — a rolling cross-fetch count is a later addition
    once the table has accumulated history).
    """
    if conn is None or not articles:
        return 0
    dedup = dedup_syndicate(articles)
    group_counts: dict[str, int] = {}
    for gid in dedup.values():
        group_counts[gid] = group_counts.get(gid, 0) + 1
    ingestion_ts = int(now.timestamp())
    written = 0
    try:
        for a in articles:
            aid = a.get("id")
            score = scored.get(str(aid))
            if score is None:
                continue
            pub_dt = _parse_publication_ts(a.get("created_at"))
            publication_ts = int(pub_dt.timestamp()) if pub_dt is not None else None
            delay_h = (
                max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                if pub_dt is not None else None
            )
            group_id = dedup.get(aid, str(aid))
            symbols = a.get("symbols") or []
            for sym in symbols:
                conn.execute(
                    """
                    INSERT INTO news_timing_shadow
                        (event_id, symbol, headline_id, publication_ts,
                         ingestion_ts, delay_h, dedup_group_id, sentiment,
                         relevance, n_syndicate, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex, str(sym), str(aid), publication_ts,
                        ingestion_ts, delay_h, group_id,
                        float(score.get("sentiment", 0.0)),
                        float(score.get("relevance", 0.0)),
                        group_counts.get(group_id, 1), ingestion_ts,
                    ),
                )
                written += 1
    except sqlite3.Error as exc:
        logger.warning("[news_timing_shadow] log dropped: %r", exc)
        return written
    return written
