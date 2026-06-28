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

import math
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
    "crypto": {
        "crypto_fg": 1.25,
        "okx_funding": 1.25,
        "binance_deriv": 1.25,
        "coinglass": 1.25,
        "news_sentiment": 1.1,
    },
    "forex": {"fred_macro": 1.25, "myfxbook": 1.1, "news_sentiment": 1.0},
    "commodity": {"fred_macro": 1.25, "cftc_cot": 1.25, "news_sentiment": 1.0},
    "index": {"fred_macro": 1.15, "news_sentiment": 1.0},
    "equity": {"fred_macro": 1.25, "news_sentiment": 1.1},
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

# Binance positioning / order-flow thresholds (EVIDENCE-only tilt). The genuinely
# NEW axis Polaris lacked: retail-vs-smart-money divergence + taker imbalance,
# averaged across the perp roster. flow_not_block — these tilt bull/bear evidence,
# never block / size / exit; CRISIS stays price/macro-led.
#   global_long_short = retail crowd account ratio (>1 = crowd net-long)
#   top_long_short    = top-trader (smart-money) position ratio
#   taker_buy_sell    = aggressive taker buy/sell volume ratio (>1 = buy pressure)
# Smart money is the higher-signal leg: top-trader net-long → BULL conviction,
# net-short → BEAR. Taker imbalance adds a same-direction flow confirm. Retail
# crowd is recorded as evidence but NOT scored on its own (noisier / contrarian-
# ambiguous) — it shapes the judge's read, not the score.
_BNC_TOP_LS_BULL = 1.30  # top traders meaningfully net-long
_BNC_TOP_LS_BEAR = 0.77  # top traders meaningfully net-short (~1/1.30)
_BNC_TAKER_BULL = 1.10  # aggressive buy-side flow dominates
_BNC_TAKER_BEAR = 0.91  # aggressive sell-side flow dominates (~1/1.10)
_BNC_TOP_LS_SCORE = 1.0  # smart-money positioning conviction
_BNC_TAKER_SCORE = 0.5  # order-flow confirm (secondary)

# Coinglass aggregated-liquidation thresholds (EVIDENCE-only tilt, crypto). A
# liquidation CASCADE flushes over-leveraged positioning. Heavy LONG liquidations
# (longs forced out) = a capitulation flush → BEAR/exhaustion evidence; heavy
# SHORT liquidations (shorts squeezed) = a short-squeeze → BULL evidence. We tilt
# only when one side dominates the other by a clear ratio AND the dominant side
# is materially large (a meaningful $-cascade, not noise). flow_not_block: an
# evidence tilt, never a block. The funding-aggregate fields are EVIDENCE-only.
_CG_LIQ_RATIO = 2.0  # one side ≥ 2× the other to count as a directional cascade
_CG_LIQ_MIN_USD = 1_000_000.0  # dominant side must clear $1M to be signal
# A clear directional cascade is a strong single signal — scored at the
# conviction floor (×weight 1.25 → 1.875 ≥ 1.5) so a decisive flush can carry an
# override on its own, matching the funding / F&G-greed signal strength.
_CG_LIQ_SCORE = 1.5  # liquidation-cascade conviction

# MyFxBook retail community-outlook contrarian thresholds (EVIDENCE-only tilt,
# forex). Retail FX crowds are persistently wrong at extremes — an OVERWHELMINGLY
# net-long crowd is contrarian BEAR evidence, net-short crowd contrarian BULL.
# Only fires past an extreme crowding level (noisy near 50/50). flow_not_block.
_FXB_CROWD_EXTREME = 75.0  # crowd one side ≥ this % → contrarian tilt
# An extreme crowd (≥75% one side) is a strong contrarian signal — scored at the
# conviction floor (×weight 1.1 → 1.65 ≥ 1.5) so it can carry a contrarian
# override on its own (FX otherwise has only the macro scorer).
_FXB_SCORE = 1.5  # contrarian positioning conviction

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

# News-sentiment thresholds — a SIGNAL-only directional tilt. ``sentiment`` is a
# relevance-weighted mean over the group's recent headlines (∈ [-1, +1]); the
# raw contribution is scaled by the headline ``relevance`` so a thin / off-topic
# datum cannot manufacture conviction. The base weight (1.5) sits at the
# conviction floor so one strong, on-topic headline cluster is suggestive but a
# single weak headline never overrides price. flow_not_block: negative sentiment
# tilts toward BEAR evidence, it never blocks/halts; CRISIS stays price-led.
_NEWS_SENTIMENT_MIN = 0.15  # below |this| → neutral (evidence-only, no score)
_NEWS_BASE_SCORE = 1.5


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
    group_symbol = (
        underlying_group_id.split(":", 1)[1] if ":" in underlying_group_id else ""
    )
    if prefix == "crypto":
        _score_crypto_fg(
            sources.get("crypto_fg"), scores, evidence, source_weights, prefix
        )
        _score_funding(
            sources.get("okx_funding"), scores, evidence, source_weights, prefix
        )
        _score_binance_deriv(
            sources.get("binance_deriv"), scores, evidence, source_weights, prefix
        )
        _score_coinglass(
            sources.get("coinglass"), group_symbol, scores, evidence,
            source_weights, prefix,
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
        if prefix == "forex":
            # MyFxBook retail community-outlook positioning — a CONTRARIAN FX
            # tilt keyed on the group pair (FX otherwise has only the macro
            # scorer). Absent creds / pair → no-op (macro stands).
            _score_myfxbook(
                sources.get("myfxbook"), group_symbol, scores, evidence,
                source_weights, prefix,
            )

    # News-sentiment EVIDENCE — routed to ALL asset classes. The collector emits
    # a per-symbol map; pick THIS group's symbol row (absent → no-op). SIGNAL
    # only: tilts bull/bear evidence, never blocks/sizes/exits.
    news_payload = sources.get("news_sentiment")
    news_row = (
        news_payload.get(group_symbol)
        if isinstance(news_payload, dict) and group_symbol
        else None
    )
    _score_news(news_row, scores, evidence, source_weights, prefix)

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


def _avg_field(rows: list[dict[str, Any]], field: str) -> float | None:
    """Roster average of a numeric ``field`` across rows (``None`` if absent)."""
    vals = [
        float(r[field])
        for r in rows
        if isinstance(r, dict)
        and isinstance(r.get(field), (int, float))
        and math.isfinite(float(r[field]))
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _score_binance_deriv(
    deriv: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """Binance public positioning / order-flow → bull/bear regime EVIDENCE.

    ``deriv`` is the per-symbol map the collector emits. We average each axis
    across the perp roster (a market-wide positioning read, not a single name):

      top_long_short  smart-money position ratio → the PRIMARY conviction leg
                      (>1.30 net-long = BULL, <0.77 net-short = BEAR)
      taker_buy_sell  aggressive taker imbalance → a same-direction flow CONFIRM
      global_long_short / oi_change_24h  recorded as EVIDENCE (shape the judge's
                      read) but NOT scored — retail crowd is noisier and OI delta
                      is directionless without a price pairing.

    Every averaged axis is surfaced in ``evidence`` (read-only context for
    G3/G7) even when below a tilt threshold. Currency (#69): the newest OI
    observation ISO date is propagated as ``binance_oi_asof`` so a stale read is
    age-labelable. flow_not_block: tilts evidence, never blocks/sizes/exits;
    CRISIS stays price/macro-led. Absent / empty payload → no-op.
    """
    if not deriv:
        return
    rows = [r for r in deriv.values() if isinstance(r, dict)]
    if not rows:
        return
    top_ls = _avg_field(rows, "top_long_short")
    taker = _avg_field(rows, "taker_buy_sell")
    glob_ls = _avg_field(rows, "global_long_short")
    funding = _avg_field(rows, "funding_rate")
    oi_chg = _avg_field(rows, "oi_change_24h")
    # Nothing parseable on any axis → no evidence, no weight (true no-op).
    if all(v is None for v in (top_ls, taker, glob_ls, funding, oi_chg)):
        return
    # Evidence is ALWAYS surfaced (read-only context), scored or not.
    if top_ls is not None:
        evidence["binance_top_long_short"] = round(top_ls, 4)
    if taker is not None:
        evidence["binance_taker_buy_sell"] = round(taker, 4)
    if glob_ls is not None:
        evidence["binance_global_long_short"] = round(glob_ls, 4)
    if funding is not None:
        evidence["binance_funding"] = funding
    if oi_chg is not None:
        evidence["binance_oi_change_24h"] = round(oi_chg, 6)
    # Currency: carry the newest OI observation date (any row that has one).
    for r in rows:
        asof = r.get("oi_asof")
        if isinstance(asof, str) and asof:
            evidence["binance_oi_asof"] = asof
            break

    w = _source_weight(prefix, "binance_deriv")
    source_weights["binance_deriv"] = w
    # Smart-money positioning = primary conviction leg.
    if top_ls is not None:
        if top_ls >= _BNC_TOP_LS_BULL:
            scores[_BULL] += _BNC_TOP_LS_SCORE * w
        elif top_ls <= _BNC_TOP_LS_BEAR:
            scores[_BEAR] += _BNC_TOP_LS_SCORE * w
    # Aggressive taker imbalance = same-direction order-flow confirm (secondary).
    if taker is not None:
        if taker >= _BNC_TAKER_BULL:
            scores[_BULL] += _BNC_TAKER_SCORE * w
        elif taker <= _BNC_TAKER_BEAR:
            scores[_BEAR] += _BNC_TAKER_SCORE * w


def _score_coinglass(
    cg: dict[str, Any] | None,
    symbol: str,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """Coinglass aggregated liquidation CASCADE → bull/bear regime EVIDENCE.

    ``cg`` is the per-coin map the collector emits; pick THIS group's coin row.
    Heavy LONG liquidations (longs forced out) = capitulation flush → BEAR;
    heavy SHORT liquidations (shorts squeezed) = squeeze → BULL. We tilt only on
    a clear directional cascade (one side ≥ ``_CG_LIQ_RATIO`` × the other AND the
    dominant side ≥ ``_CG_LIQ_MIN_USD``). Cross-exchange funding fields are
    surfaced as EVIDENCE only. Currency (#69): the collector's ``liquidation_asof``
    is propagated. flow_not_block: an evidence tilt, never a block. Absent coin /
    payload → no-op (key-gated stub yields ``{}`` → no-op today).
    """
    if not cg or not symbol:
        return
    row = cg.get(symbol)
    if not isinstance(row, dict):
        return
    long_liq = row.get("long_liquidation_usd")
    short_liq = row.get("short_liquidation_usd")
    funding_mean = row.get("funding_mean")
    funding_disp = row.get("funding_dispersion")
    if not any(
        isinstance(v, (int, float))
        for v in (long_liq, short_liq, funding_mean, funding_disp)
    ):
        return
    # Evidence is ALWAYS surfaced (read-only context).
    if isinstance(long_liq, (int, float)):
        evidence["cg_long_liq_usd"] = float(long_liq)
    if isinstance(short_liq, (int, float)):
        evidence["cg_short_liq_usd"] = float(short_liq)
    if isinstance(funding_mean, (int, float)):
        evidence["cg_funding_mean"] = float(funding_mean)
    if isinstance(funding_disp, (int, float)):
        evidence["cg_funding_dispersion"] = float(funding_disp)
    asof = row.get("liquidation_asof")
    if isinstance(asof, str) and asof:
        evidence["cg_liquidation_asof"] = asof

    w = _source_weight(prefix, "coinglass")
    source_weights["coinglass"] = w
    if not (isinstance(long_liq, (int, float)) and isinstance(short_liq, (int, float))):
        return
    ll = float(long_liq)
    sl = float(short_liq)
    # Long flush (longs liquidated dominate) → BEAR capitulation evidence.
    if ll >= _CG_LIQ_MIN_USD and ll >= _CG_LIQ_RATIO * max(sl, 1.0):
        scores[_BEAR] += _CG_LIQ_SCORE * w
    # Short squeeze (shorts liquidated dominate) → BULL evidence.
    elif sl >= _CG_LIQ_MIN_USD and sl >= _CG_LIQ_RATIO * max(ll, 1.0):
        scores[_BULL] += _CG_LIQ_SCORE * w


def _score_myfxbook(
    fxb: dict[str, Any] | None,
    symbol: str,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """MyFxBook retail community-outlook → CONTRARIAN FX positioning EVIDENCE.

    ``fxb`` is the per-pair map the collector emits; pick THIS group's pair row.
    Retail FX crowds are persistently wrong at extremes: an overwhelmingly
    net-long crowd (``long_pct`` ≥ ``_FXB_CROWD_EXTREME``) is contrarian BEAR
    evidence, an overwhelmingly net-short crowd is contrarian BULL. Only fires
    past an extreme (noisy near 50/50). Currency (#69): the collector's per-row
    fetch-time ``asof`` is propagated. flow_not_block: a contrarian tilt, never a
    block. Absent pair / creds → no-op (creds-gated stub yields ``{}`` today).
    """
    if not fxb or not symbol:
        return
    row = fxb.get(symbol)
    if not isinstance(row, dict):
        return
    long_pct = row.get("long_pct")
    short_pct = row.get("short_pct")
    if not (isinstance(long_pct, (int, float)) or isinstance(short_pct, (int, float))):
        return
    if isinstance(long_pct, (int, float)):
        evidence["fxb_long_pct"] = float(long_pct)
    if isinstance(short_pct, (int, float)):
        evidence["fxb_short_pct"] = float(short_pct)
    asof = row.get("asof")
    if isinstance(asof, str) and asof:
        evidence["fxb_asof"] = asof

    w = _source_weight(prefix, "myfxbook")
    source_weights["myfxbook"] = w
    lp = float(long_pct) if isinstance(long_pct, (int, float)) else None
    sp = float(short_pct) if isinstance(short_pct, (int, float)) else None
    # Crowd extremely net-long → contrarian BEAR; extremely net-short → BULL.
    if lp is not None and lp >= _FXB_CROWD_EXTREME:
        scores[_BEAR] += _FXB_SCORE * w
    elif sp is not None and sp >= _FXB_CROWD_EXTREME:
        scores[_BULL] += _FXB_SCORE * w


# Expanded FRED panel keys surfaced as read-only EVIDENCE (NOT scored). vix /
# hy_spread are already surfaced + scored above, so they are excluded here.
_MACRO_PANEL_KEYS: tuple[str, ...] = (
    "yield_curve", "yield_curve_10y3m", "fed_funds", "ust_10y", "ust_2y", "sofr",
    "cpi", "core_cpi", "pce", "breakeven_5y", "breakeven_10y",
    "unemployment", "nonfarm_payrolls", "initial_claims",
    "real_gdp", "industrial_production", "m2", "fed_balance_sheet", "ig_spread",
    "housing_starts", "home_price_index", "dollar_index", "eur_usd", "usd_jpy",
    "consumer_sentiment", "wti_crude", "brent_crude", "stl_stress", "nfci",
    "vix_3m", "oil_vol", "gold_vol",
)


def _surface_macro_panel(macro: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Surface the expanded FRED panel as read-only EVIDENCE (no scoring).

    Each present numeric series is copied into ``evidence`` along with its
    ``<key>_asof`` observation date (preserved by the collector) so the AI judge
    sees both the value AND its currency (#69). flow_not_block: pure context,
    never a score / block. Absent series are simply skipped.
    """
    for key in _MACRO_PANEL_KEYS:
        val = macro.get(key)
        if not isinstance(val, (int, float)):
            continue
        evidence[key] = val
        asof = macro.get(f"{key}_asof")
        if isinstance(asof, str) and asof:
            evidence[f"{key}_asof"] = asof


def _avg_field(rows: list[dict[str, Any]], field: str) -> float | None:
    """Roster average of a numeric ``field`` across rows (``None`` if absent)."""
    vals = [
        float(r[field])
        for r in rows
        if isinstance(r, dict)
        and isinstance(r.get(field), (int, float))
        and math.isfinite(float(r[field]))
    ]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _score_binance_deriv(
    deriv: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """Binance public positioning / order-flow → bull/bear regime EVIDENCE.

    ``deriv`` is the per-symbol map the collector emits. We average each axis
    across the perp roster (a market-wide positioning read, not a single name):

      top_long_short  smart-money position ratio → the PRIMARY conviction leg
                      (>1.30 net-long = BULL, <0.77 net-short = BEAR)
      taker_buy_sell  aggressive taker imbalance → a same-direction flow CONFIRM
      global_long_short / oi_change_24h  recorded as EVIDENCE (shape the judge's
                      read) but NOT scored — retail crowd is noisier and OI delta
                      is directionless without a price pairing.

    Every averaged axis is surfaced in ``evidence`` (read-only context for
    G3/G7) even when below a tilt threshold. Currency (#69): the newest OI
    observation ISO date is propagated as ``binance_oi_asof`` so a stale read is
    age-labelable. flow_not_block: tilts evidence, never blocks/sizes/exits;
    CRISIS stays price/macro-led. Absent / empty payload → no-op.
    """
    if not deriv:
        return
    rows = [r for r in deriv.values() if isinstance(r, dict)]
    if not rows:
        return
    top_ls = _avg_field(rows, "top_long_short")
    taker = _avg_field(rows, "taker_buy_sell")
    glob_ls = _avg_field(rows, "global_long_short")
    funding = _avg_field(rows, "funding_rate")
    oi_chg = _avg_field(rows, "oi_change_24h")
    # Nothing parseable on any axis → no evidence, no weight (true no-op).
    if all(v is None for v in (top_ls, taker, glob_ls, funding, oi_chg)):
        return
    # Evidence is ALWAYS surfaced (read-only context), scored or not.
    if top_ls is not None:
        evidence["binance_top_long_short"] = round(top_ls, 4)
    if taker is not None:
        evidence["binance_taker_buy_sell"] = round(taker, 4)
    if glob_ls is not None:
        evidence["binance_global_long_short"] = round(glob_ls, 4)
    if funding is not None:
        evidence["binance_funding"] = funding
    if oi_chg is not None:
        evidence["binance_oi_change_24h"] = round(oi_chg, 6)
    # Currency: carry the newest OI observation date (any row that has one).
    for r in rows:
        asof = r.get("oi_asof")
        if isinstance(asof, str) and asof:
            evidence["binance_oi_asof"] = asof
            break

    w = _source_weight(prefix, "binance_deriv")
    source_weights["binance_deriv"] = w
    # Smart-money positioning = primary conviction leg.
    if top_ls is not None:
        if top_ls >= _BNC_TOP_LS_BULL:
            scores[_BULL] += _BNC_TOP_LS_SCORE * w
        elif top_ls <= _BNC_TOP_LS_BEAR:
            scores[_BEAR] += _BNC_TOP_LS_SCORE * w
    # Aggressive taker imbalance = same-direction order-flow confirm (secondary).
    if taker is not None:
        if taker >= _BNC_TAKER_BULL:
            scores[_BULL] += _BNC_TAKER_SCORE * w
        elif taker <= _BNC_TAKER_BEAR:
            scores[_BEAR] += _BNC_TAKER_SCORE * w


def _score_coinglass(
    cg: dict[str, Any] | None,
    symbol: str,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """Coinglass aggregated liquidation CASCADE → bull/bear regime EVIDENCE.

    ``cg`` is the per-coin map the collector emits; pick THIS group's coin row.
    Heavy LONG liquidations (longs forced out) = capitulation flush → BEAR;
    heavy SHORT liquidations (shorts squeezed) = squeeze → BULL. We tilt only on
    a clear directional cascade (one side ≥ ``_CG_LIQ_RATIO`` × the other AND the
    dominant side ≥ ``_CG_LIQ_MIN_USD``). Cross-exchange funding fields are
    surfaced as EVIDENCE only. Currency (#69): the collector's ``liquidation_asof``
    is propagated. flow_not_block: an evidence tilt, never a block. Absent coin /
    payload → no-op (key-gated stub yields ``{}`` → no-op today).
    """
    if not cg or not symbol:
        return
    row = cg.get(symbol)
    if not isinstance(row, dict):
        return
    long_liq = row.get("long_liquidation_usd")
    short_liq = row.get("short_liquidation_usd")
    funding_mean = row.get("funding_mean")
    funding_disp = row.get("funding_dispersion")
    if not any(
        isinstance(v, (int, float))
        for v in (long_liq, short_liq, funding_mean, funding_disp)
    ):
        return
    # Evidence is ALWAYS surfaced (read-only context).
    if isinstance(long_liq, (int, float)):
        evidence["cg_long_liq_usd"] = float(long_liq)
    if isinstance(short_liq, (int, float)):
        evidence["cg_short_liq_usd"] = float(short_liq)
    if isinstance(funding_mean, (int, float)):
        evidence["cg_funding_mean"] = float(funding_mean)
    if isinstance(funding_disp, (int, float)):
        evidence["cg_funding_dispersion"] = float(funding_disp)
    asof = row.get("liquidation_asof")
    if isinstance(asof, str) and asof:
        evidence["cg_liquidation_asof"] = asof

    w = _source_weight(prefix, "coinglass")
    source_weights["coinglass"] = w
    if not (isinstance(long_liq, (int, float)) and isinstance(short_liq, (int, float))):
        return
    ll = float(long_liq)
    sl = float(short_liq)
    # Long flush (longs liquidated dominate) → BEAR capitulation evidence.
    if ll >= _CG_LIQ_MIN_USD and ll >= _CG_LIQ_RATIO * max(sl, 1.0):
        scores[_BEAR] += _CG_LIQ_SCORE * w
    # Short squeeze (shorts liquidated dominate) → BULL evidence.
    elif sl >= _CG_LIQ_MIN_USD and sl >= _CG_LIQ_RATIO * max(ll, 1.0):
        scores[_BULL] += _CG_LIQ_SCORE * w


def _score_myfxbook(
    fxb: dict[str, Any] | None,
    symbol: str,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """MyFxBook retail community-outlook → CONTRARIAN FX positioning EVIDENCE.

    ``fxb`` is the per-pair map the collector emits; pick THIS group's pair row.
    Retail FX crowds are persistently wrong at extremes: an overwhelmingly
    net-long crowd (``long_pct`` ≥ ``_FXB_CROWD_EXTREME``) is contrarian BEAR
    evidence, an overwhelmingly net-short crowd is contrarian BULL. Only fires
    past an extreme (noisy near 50/50). Currency (#69): the collector's per-row
    fetch-time ``asof`` is propagated. flow_not_block: a contrarian tilt, never a
    block. Absent pair / creds → no-op (creds-gated stub yields ``{}`` today).
    """
    if not fxb or not symbol:
        return
    row = fxb.get(symbol)
    if not isinstance(row, dict):
        return
    long_pct = row.get("long_pct")
    short_pct = row.get("short_pct")
    if not (isinstance(long_pct, (int, float)) or isinstance(short_pct, (int, float))):
        return
    if isinstance(long_pct, (int, float)):
        evidence["fxb_long_pct"] = float(long_pct)
    if isinstance(short_pct, (int, float)):
        evidence["fxb_short_pct"] = float(short_pct)
    asof = row.get("asof")
    if isinstance(asof, str) and asof:
        evidence["fxb_asof"] = asof

    w = _source_weight(prefix, "myfxbook")
    source_weights["myfxbook"] = w
    lp = float(long_pct) if isinstance(long_pct, (int, float)) else None
    sp = float(short_pct) if isinstance(short_pct, (int, float)) else None
    # Crowd extremely net-long → contrarian BEAR; extremely net-short → BULL.
    if lp is not None and lp >= _FXB_CROWD_EXTREME:
        scores[_BEAR] += _FXB_SCORE * w
    elif sp is not None and sp >= _FXB_CROWD_EXTREME:
        scores[_BULL] += _FXB_SCORE * w


# Expanded FRED panel keys surfaced as read-only EVIDENCE (NOT scored). vix /
# hy_spread are already surfaced + scored above, so they are excluded here.
_MACRO_PANEL_KEYS: tuple[str, ...] = (
    "yield_curve", "yield_curve_10y3m", "fed_funds", "ust_10y", "ust_2y", "sofr",
    "cpi", "core_cpi", "pce", "breakeven_5y", "breakeven_10y",
    "unemployment", "nonfarm_payrolls", "initial_claims",
    "real_gdp", "industrial_production", "m2", "fed_balance_sheet", "ig_spread",
    "housing_starts", "home_price_index", "dollar_index", "eur_usd", "usd_jpy",
    "consumer_sentiment", "wti_crude", "brent_crude", "stl_stress", "nfci",
    "vix_3m", "oil_vol", "gold_vol",
)


def _surface_macro_panel(macro: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Surface the expanded FRED panel as read-only EVIDENCE (no scoring).

    Each present numeric series is copied into ``evidence`` along with its
    ``<key>_asof`` observation date (preserved by the collector) so the AI judge
    sees both the value AND its currency (#69). flow_not_block: pure context,
    never a score / block. Absent series are simply skipped.
    """
    for key in _MACRO_PANEL_KEYS:
        val = macro.get(key)
        if not isinstance(val, (int, float)):
            continue
        evidence[key] = val
        asof = macro.get(f"{key}_asof")
        if isinstance(asof, str) and asof:
            evidence[f"{key}_asof"] = asof


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

    # Expanded FRED panel → additive EVIDENCE (read-only context for the AI
    # judge). NOT scored (scoring stays vix/hy only — surgical); these enrich
    # the macro picture the judge reasons over. Each value carries its FRED
    # observation date (``<key>_asof``) preserved by the collector so a
    # monthly-lag / weekend read is age-labelable (#69 currency, flow_not_block).
    _surface_macro_panel(macro, evidence)

    # Expanded FRED panel → additive EVIDENCE (read-only context for the AI
    # judge). NOT scored (scoring stays vix/hy only — surgical); these enrich
    # the macro picture the judge reasons over. Each value carries its FRED
    # observation date (``<key>_asof``) preserved by the collector so a
    # monthly-lag / weekend read is age-labelable (#69 currency, flow_not_block).
    _surface_macro_panel(macro, evidence)

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


def _score_news(
    news: dict[str, Any] | None,
    scores: dict[str, float],
    evidence: dict[str, Any],
    source_weights: dict[str, float],
    prefix: str,
) -> None:
    """News-sentiment headline tone → bull/bear regime EVIDENCE (SIGNAL only).

    ``news`` is the per-GROUP aggregate row (flat ``{"sentiment","relevance",
    "magnitude","n"}``) the collector emits for this group's symbol. Bullish tone
    adds BULL evidence, bearish adds BEAR — scaled by ``relevance`` so a thin or
    off-topic datum contributes little. The raw sentiment is ALWAYS recorded in
    ``evidence`` (even below the neutral band) so the dashboard / G3-G7 trail
    always sees it. News NEVER writes CRISIS (that stays price-led) and NEVER
    blocks/sizes/exits/halts — flow_not_block: a negative datum tilts evidence,
    it does not stop anything. Absent / empty payload → no-op.
    """
    if not news or "sentiment" not in news:
        return
    sentiment = news.get("sentiment")
    if not isinstance(sentiment, (int, float)) or not math.isfinite(float(sentiment)):
        return  # non-finite (nan/inf) cache value → neutral no-op, never max-tilt
    sentiment = max(-1.0, min(1.0, float(sentiment)))
    relevance = news.get("relevance")
    rel = float(relevance) if isinstance(relevance, (int, float)) else 0.5
    rel = max(0.0, min(1.0, rel)) if math.isfinite(rel) else 0.5
    # Evidence is ALWAYS surfaced (even neutral) — read-only context.
    evidence["news_sentiment"] = round(sentiment, 4)
    magnitude = news.get("magnitude")
    if isinstance(magnitude, (int, float)) and math.isfinite(float(magnitude)):
        evidence["news_magnitude"] = round(max(0.0, min(1.0, float(magnitude))), 4)
    n = news.get("n")
    if isinstance(n, (int, float)):
        evidence["news_n"] = int(n)
    w = _source_weight(prefix, "news_sentiment")
    source_weights["news_sentiment"] = w
    # Neutral band → no regime tilt (evidence recorded above, no conviction).
    if abs(sentiment) < _NEWS_SENTIMENT_MIN:
        return
    contribution = _NEWS_BASE_SCORE * abs(sentiment) * rel * w
    if sentiment > 0.0:
        scores[_BULL] += contribution
    else:
        scores[_BEAR] += contribution
