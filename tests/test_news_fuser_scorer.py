"""Tests for the news_sentiment fuser scorer (#6 regime EVIDENCE, SIGNAL-only).

DEMO/PAPER only — virtual funds. ``_score_news`` contributes regime SUGGESTION
evidence ONLY. It never sizes/blocks/exits/halts. It respects the existing
CONVICTION_FLOOR (1.5) + 2-consecutive-close confirm gate: below-floor scores
are recorded in ``evidence`` (so the dashboard / G3-G7 trail always sees them)
but never force a regime override. flow_not_block: a negative-sentiment payload
tilts toward bear evidence, it does NOT block anything.
"""

from __future__ import annotations

from typing import Any

from polaris.core.altdata.fuser import (
    _BEAR,
    _BULL,
    SOURCE_WEIGHT_MAX,
    SOURCE_WEIGHT_MIN,
    _score_news,
    _source_weight,
)


def _fresh() -> tuple[dict[str, float], dict[str, Any], dict[str, float]]:
    scores = {_BULL: 0.0, _BEAR: 0.0, "chop": 0.0, "crisis": 0.0}
    evidence: dict[str, Any] = {}
    source_weights: dict[str, float] = {}
    return scores, evidence, source_weights


# A news payload is the per-group aggregate the fuser sees: the collector emits
# per-symbol rows; the cache/group lookup hands the scorer the group-relevant
# aggregate. We score on a flat {"sentiment","relevance","magnitude","n"} dict.
def _news(sentiment: float, relevance: float = 0.8, magnitude: float = 0.7, n: int = 3) -> dict[str, Any]:
    return {
        "sentiment": sentiment,
        "relevance": relevance,
        "magnitude": magnitude,
        "n": n,
    }


def test_score_news_none_payload_is_noop() -> None:
    scores, evidence, sw = _fresh()
    _score_news(None, scores, evidence, sw, "equity")
    assert scores[_BULL] == 0.0 and scores[_BEAR] == 0.0
    assert evidence == {}
    assert sw == {}


def test_score_news_empty_payload_is_noop() -> None:
    scores, evidence, sw = _fresh()
    _score_news({}, scores, evidence, sw, "equity")
    assert evidence == {}


def test_score_news_bullish_adds_bull_and_records_evidence() -> None:
    scores, evidence, sw = _fresh()
    _score_news(_news(0.8), scores, evidence, sw, "crypto")
    assert scores[_BULL] > 0.0
    assert scores[_BEAR] == 0.0
    # Evidence ALWAYS recorded, even if below the conviction floor.
    assert evidence["news_sentiment"] > 0.0
    assert "news_sentiment" in sw


def test_score_news_bearish_adds_bear() -> None:
    scores, evidence, sw = _fresh()
    _score_news(_news(-0.7), scores, evidence, sw, "equity")
    assert scores[_BEAR] > 0.0
    assert scores[_BULL] == 0.0
    assert evidence["news_sentiment"] < 0.0


def test_score_news_neutral_records_evidence_no_score() -> None:
    """Weak/neutral sentiment = no regime tilt, but evidence is still recorded."""
    scores, evidence, sw = _fresh()
    _score_news(_news(0.05), scores, evidence, sw, "forex")
    assert scores[_BULL] == 0.0 and scores[_BEAR] == 0.0
    # flow_not_block: even a near-zero datum is surfaced as evidence context.
    assert "news_sentiment" in evidence


def test_score_news_low_relevance_dampens() -> None:
    """Low relevance shrinks the contribution (no spurious conviction)."""
    scores_hi, ev_hi, sw_hi = _fresh()
    scores_lo, ev_lo, sw_lo = _fresh()
    _score_news(_news(0.9, relevance=0.9), scores_hi, ev_hi, sw_hi, "equity")
    _score_news(_news(0.9, relevance=0.1), scores_lo, ev_lo, sw_lo, "equity")
    assert scores_hi[_BULL] > scores_lo[_BULL]


def test_news_source_weight_clamped() -> None:
    # Whatever the configured weight, the clamp holds (never a >1.25 dominator
    # or a <0.75 throttle).
    w = _source_weight("crypto", "news_sentiment")
    assert SOURCE_WEIGHT_MIN <= w <= SOURCE_WEIGHT_MAX
    for prefix in ("crypto", "forex", "commodity", "index", "equity"):
        w = _source_weight(prefix, "news_sentiment")
        assert SOURCE_WEIGHT_MIN <= w <= SOURCE_WEIGHT_MAX


def test_score_news_never_writes_crisis_directly() -> None:
    """News alone must not manufacture a CRISIS regime (that stays price-led)."""
    scores, evidence, sw = _fresh()
    _score_news(_news(-1.0, relevance=1.0, magnitude=1.0), scores, evidence, sw, "equity")
    assert scores["crisis"] == 0.0  # only bear evidence, never a crisis halt


def test_score_news_surfaces_magnitude_evidence() -> None:
    scores, evidence, sw = _fresh()
    _score_news(_news(0.8, magnitude=0.65), scores, evidence, sw, "equity")
    assert evidence["news_magnitude"] == 0.65


def test_score_news_nonfinite_sentiment_is_noop() -> None:
    """A corrupted nan/inf cache value must read as neutral, never max-bullish."""
    scores, evidence, sw = _fresh()
    _score_news(_news(float("nan")), scores, evidence, sw, "equity")
    assert scores[_BULL] == 0.0 and scores[_BEAR] == 0.0
    _score_news(_news(float("inf")), scores, evidence, sw, "crypto")
    assert scores[_BULL] == 0.0 and scores[_BEAR] == 0.0
