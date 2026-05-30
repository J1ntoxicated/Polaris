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

    prefix = underlying_group_id.split(":", 1)[0]
    if prefix == "crypto":
        _score_crypto_fg(sources.get("crypto_fg"), scores, evidence)
        _score_funding(sources.get("okx_funding"), scores, evidence)
    else:
        # forex / index / commodity / equity — all macro-sensitive. Equity
        # (Stream C / Alpaca) reuses the SAME FRED macro scorer + conservative
        # conviction floor as the FX/index/commodity branch.
        _score_macro(sources.get("fred_macro"), scores, evidence)

    if not evidence:
        return None, 0.0, {}

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_label, best_score = ranked[0]
    runner_score = ranked[1][1]

    if best_score < CONVICTION_FLOOR:
        # Below conviction floor → no override. Evidence still surfaced.
        return None, 0.0, evidence

    confidence = _confidence(best_score, runner_score)
    return best_label, confidence, evidence


def _confidence(best: float, runner: float) -> float:
    if best <= 0:
        return CONFIDENCE_FLOOR
    gap_ratio = (best - runner) / best
    return max(CONFIDENCE_FLOOR, min(1.0, gap_ratio + CONFIDENCE_FLOOR))


def _score_crypto_fg(
    fg: dict[str, Any] | None, scores: dict[str, float], evidence: dict[str, Any]
) -> None:
    if not fg or "value" not in fg:
        return
    value = fg["value"]
    evidence["crypto_fg"] = value
    if value < _FG_EXTREME_FEAR:
        scores[_CRISIS] += 1.0
        scores[_BEAR] += 1.0
    elif value > _FG_GREED:
        scores[_BULL] += 1.5


def _score_funding(
    funding: dict[str, Any] | None, scores: dict[str, float], evidence: dict[str, Any]
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
    if avg < _FUNDING_BEAR:
        scores[_BEAR] += 2.0
    elif avg > _FUNDING_BULL:
        scores[_BULL] += 2.0


def _score_macro(
    macro: dict[str, Any] | None, scores: dict[str, float], evidence: dict[str, Any]
) -> None:
    if not macro:
        return
    vix = macro.get("vix")
    hy = macro.get("hy_spread")
    if isinstance(vix, (int, float)):
        evidence["vix"] = vix
    if isinstance(hy, (int, float)):
        evidence["hy_spread"] = hy

    crisis = (isinstance(vix, (int, float)) and vix > _VIX_CRISIS) or (
        isinstance(hy, (int, float)) and hy > _HY_CRISIS
    )
    if crisis:
        scores[_CRISIS] += 2.0
        return
    if isinstance(vix, (int, float)) and vix > _VIX_BEAR:
        scores[_BEAR] += 1.0
        return
    low_vix = isinstance(vix, (int, float)) and vix < _VIX_BULL
    low_hy = (not isinstance(hy, (int, float))) or hy < _HY_BULL
    if low_vix and low_hy:
        scores[_BULL] += 2.0
