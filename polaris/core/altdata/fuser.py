"""fuse_evidence — alt-data → regime SUGGESTION + raw evidence (NEVER an action).

DEMO/PAPER. Additive scoring over the fresh alt-data sources for a group, mapped
to Polaris' 4 canonical regime labels (``bull_trend`` / ``bear_trend`` / ``chop``
/ ``crisis``). Ported weights from
``~/Projects/auto_invasion_mk1-main/invasion/market/regime.py``:

  crypto F&G:   < 20 → crisis + bear ; > 75 → bull
  funding:      < -0.001 → bear ; > 0.003 → bull
  VIX:          > 40 (or HY > 500) → crisis ; > 25 → bear ; < 15 (& HY<300) → bull

Per asset-class branch (matched on the ``underlying_group_id`` prefix):

  crypto:*                          → crypto F&G + funding scorers
  forex:* / index:* / commodity:* / equity:* → FRED macro scorer

Stream C (Alpaca US equity, ``equity:*``) is MACRO-sensitive — it reuses the
SAME FRED macro scorer (VIX / HY) and the SAME conservative conviction floor
+ lower-credible discipline as the FX/index/commodity branch. Crypto and FX
outputs are byte-identical (each branch only runs the scorers for its own
routed sources; absent sources are no-ops as before).

Output contract:
  ``(regime_hint, confidence, evidence)``
  - ``regime_hint`` = winning label ONLY if ``best_score >= 1.5`` (conviction
    floor); otherwise ``None`` (no override — price-only regime stands).
  - ``confidence`` = ``(best - runner) / best + 0.3`` floor, clamped to
    ``[0.3, 1.0]``; ``0.0`` when there is no hint.
  - ``evidence`` = the raw values used (read-only context for G3/G7).

This returns a SUGGESTION + evidence. It NEVER sizes, blocks, exits, halts, or
writes to learner/risk state. The hint, if any, is fed into the EXISTING
``detect_regime_flip`` 2-consecutive-close confirm gate upstream.
"""

from __future__ import annotations

from typing import Any

# Polaris canonical labels (mirror live_recalc.regime_flip.REGIME_VALUES).
_BULL = "bull_trend"
_BEAR = "bear_trend"
_CHOP = "chop"
_CRISIS = "crisis"

CONVICTION_FLOOR = 1.5
CONFIDENCE_FLOOR = 0.3

# P1 asset-class differentiated source-type weighting. A multiplier applied to
# each source's base score BEFORE accumulation. Clamped to ``[0.75, 1.25]`` so
# the tilt can never dominate the ported base weights (it emphasises the
# source type that is most informative for each asset class, NOT a throttle).
#   crypto    → funding / F&G amplified (crypto-native risk signal)
#   fx/cmdty  → macro amplified (FX & commodities are macro-driven)
#   equity    → macro amplified (+ gap context already in MarketView)
SOURCE_WEIGHT_MIN = 0.75
SOURCE_WEIGHT_MAX = 1.25
_DEFAULT_WEIGHT = 1.0
# (asset_class_prefix → {source_name → multiplier}). Absent entries default to
# 1.0 (behaviour-identical to pre-P1). Only sources actually routed to a group
# (cache.get_for_group) can appear in ``source_weights`` — routing isolation.
_SOURCE_WEIGHTS: dict[str, dict[str, float]] = {
    "crypto": {"crypto_fg": 1.25, "okx_funding": 1.25},
    "forex": {"fred_macro": 1.25},
    "commodity": {"fred_macro": 1.25, "cftc_cot": 1.25},
    "index": {"fred_macro": 1.15},
    "equity": {"fred_macro": 1.25},
}


def _source_weight(prefix: str, source: str) -> float:
    """Asset-class source-type multiplier, clamped to ``[0.75, 1.25]``."""
    w = _SOURCE_WEIGHTS.get(prefix, {}).get(source, _DEFAULT_WEIGHT)
    return max(SOURCE_WEIGHT_MIN, min(SOURCE_WEIGHT_MAX, w))

# Ported thresholds (auto_invasion weighted scoring).
_FG_EXTREME_FEAR = 20
_FG_GREED = 75
_FUNDING_BEAR = -0.001
_FUNDING_BULL = 0.003
_VIX_CRISIS = 40.0
_VIX_BEAR = 25.0
_VIX_BULL = 15.0
_HY_CRISIS = 500.0
_HY_BULL = 300.0

# CFTC COT positioning-EXTREMITY thresholds — a percentile of THIS week's net-spec
# vs the contract's OWN ~3yr distribution (0=most net-short ever, 1=most net-long
# ever). Per-contract normalisation: a commodity at its own median (~0.5) is
# neutral; only an extreme vs its own range fires. Speculators unusually net-long
# for THIS contract confirm an uptrend (BULL); unusually net-short → BEAR. FLAGGED
# for /debate calibration (momentum-confirmation vs contrarian-at-extreme reading).
_COT_BULL_STRONG = 0.85
_COT_BULL_MILD = 0.70
_COT_BEAR_STRONG = 0.15
_COT_BEAR_MILD = 0.30


def fuse_evidence(
    underlying_group_id: str,
    cache: Any,
    *,
    now_ts: float | None = None,
) -> tuple[str | None, float, dict[str, Any]]:
    """Fuse fresh alt-data for a group into a regime suggestion + evidence."""
    sources = cache.get_for_group(underlying_group_id, now_ts=now_ts)
    if not sources:
        return None, 0.0, {}

    scores: dict[str, float] = {_BULL: 0.0, _BEAR: 0.0, _CHOP: 0.0, _CRISIS: 0.0}
    evidence: dict[str, Any] = {}
    source_weights: dict[str, float] = {}

    prefix = underlying_group_id.split(":", 1)[0]
    if prefix == "crypto":
        _score_crypto_fg(
            sources.get("crypto_fg"), scores, evidence, source_weights, prefix
        )
        _score_funding(
            sources.get("okx_funding"), scores, evidence, source_weights, prefix
        )
    else:
        # forex / index / commodity / equity — all macro-sensitive. Equity
        # (Stream C / Alpaca) reuses the SAME FRED macro scorer + conservative
        # conviction floor as the FX/index/commodity branch.
        _score_macro(
            sources.get("fred_macro"), scores, evidence, source_weights, prefix
        )
        if prefix == "commodity":
            # CFTC COT speculative positioning — a directional conviction signal
            # for THIS specific commodity, keyed on the group symbol (energy /
            # metals / ags). Absent for unmapped markets → macro stands (no-op).
            symbol = (
                underlying_group_id.split(":", 1)[1]
                if ":" in underlying_group_id
                else ""
            )
            _score_cot(
                sources.get("cftc_cot"), symbol, scores, evidence,
                source_weights, prefix,
            )

    if not evidence:
        return None, 0.0, {}

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_label, best_score = ranked[0]
    runner_score = ranked[1][1]

    # P1: structured synthesis context for G3/G7 (read-only) + downstream
    # weighted compose. Recorded on EVERY evidence-bearing return (even below
    # the conviction floor) so the consumer always sees scores/weights/class.
    evidence["asset_class"] = prefix
    evidence["scores"] = dict(scores)
    evidence["source_weights"] = source_weights

    if best_score < CONVICTION_FLOOR:
        # Below conviction floor → no override. Evidence still surfaced.
        return None, 0.0, evidence

    evidence["label"] = best_label
    confidence = _confidence(best_score, runner_score)
    return best_label, confidence, evidence


def _confidence(best: float, runner: float) -> float:
    if best <= 0:
        return CONFIDENCE_FLOOR
    gap_ratio = (best - runner) / best
    return max(CONFIDENCE_FLOOR, min(1.0, gap_ratio + CONFIDENCE_FLOOR))


def _score_crypto_fg(
    fg: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    if not fg or "value" not in fg:
        return
    value = fg["value"]
    evidence["crypto_fg"] = value
    w = _source_weight(prefix, "crypto_fg")
    source_weights["crypto_fg"] = w
    if value < _FG_EXTREME_FEAR:
        scores[_CRISIS] += 1.0 * w
        scores[_BEAR] += 1.0 * w
    elif value > _FG_GREED:
        scores[_BULL] += 1.5 * w


def _score_funding(
    funding: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    if not funding:
        return
    rates = [
        row["fundingRate"]
        for row in funding.values()
        if isinstance(row, dict) and isinstance(row.get("fundingRate"), (int, float))
    ]
    if not rates:
        return
    avg = sum(rates) / len(rates)
    evidence["avg_funding"] = avg
    w = _source_weight(prefix, "okx_funding")
    source_weights["okx_funding"] = w
    if avg < _FUNDING_BEAR:
        scores[_BEAR] += 2.0 * w
    elif avg > _FUNDING_BULL:
        scores[_BULL] += 2.0 * w


def _score_macro(
    macro: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    if not macro:
        return
    vix = macro.get("vix")
    hy = macro.get("hy_spread")
    if isinstance(vix, (int, float)):
        evidence["vix"] = vix
    if isinstance(hy, (int, float)):
        evidence["hy_spread"] = hy

    w = _source_weight(prefix, "fred_macro")
    source_weights["fred_macro"] = w
    crisis = (isinstance(vix, (int, float)) and vix > _VIX_CRISIS) or (
        isinstance(hy, (int, float)) and hy > _HY_CRISIS
    )
    if crisis:
        scores[_CRISIS] += 2.0 * w
        return
    if isinstance(vix, (int, float)) and vix > _VIX_BEAR:
        scores[_BEAR] += 1.0 * w
        return
    low_vix = isinstance(vix, (int, float)) and vix < _VIX_BULL
    low_hy = (not isinstance(hy, (int, float))) or hy < _HY_BULL
    if low_vix and low_hy:
        scores[_BULL] += 2.0 * w


def _score_cot(
    cot: dict[str, Any] | None,
    symbol: str,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """CFTC COT speculative positioning for ONE commodity → bull/bear evidence.

    ``cot`` is the collector payload keyed by our universe symbol. We score on
    ``net_spec_pctile`` — where THIS week's large-spec net position ranks within
    the contract's OWN ~3yr range (per-contract normalisation, so the structural
    net-long bias of storables does not pin them permanently bullish). Unusually
    net-long FOR THIS CONTRACT is trend-confirming BULL evidence; unusually
    net-short BEAR. Magnitude buckets the score (mild adds context below the
    conviction floor; strong can carry an override). Absent symbol → no-op.
    """
    if not cot or not symbol:
        return
    row = cot.get(symbol)
    if not isinstance(row, dict):
        return
    pctile = row.get("net_spec_pctile")
    if not isinstance(pctile, (int, float)):
        return
    evidence["cot_net_spec_pctile"] = pctile
    raw_pct = row.get("net_spec_pct")
    if isinstance(raw_pct, (int, float)):
        evidence["cot_net_spec_pct"] = raw_pct
    chg = row.get("net_spec_chg")
    if isinstance(chg, (int, float)):
        evidence["cot_net_spec_chg"] = chg
    w = _source_weight(prefix, "cftc_cot")
    source_weights["cftc_cot"] = w
    if pctile >= _COT_BULL_STRONG:
        scores[_BULL] += 2.0 * w
    elif pctile >= _COT_BULL_MILD:
        scores[_BULL] += 1.0 * w
    elif pctile <= _COT_BEAR_STRONG:
        scores[_BEAR] += 2.0 * w
    elif pctile <= _COT_BEAR_MILD:
        scores[_BEAR] += 1.0 * w
