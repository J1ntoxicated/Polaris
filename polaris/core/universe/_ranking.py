"""Layer 0 — continuous active-set ranking (flow_not_block).

Replaces the hard 4-axis cut: hard keep = validity only; vol / spread / depth /
atr become a z-normalized composite *ranking* signal, never a hard block.

Split out of ``discovery.py`` (H4: 500-LOC budget). Public entry point
``rank_active_universe`` is re-exported from ``discovery`` so existing
``from polaris.core.universe.discovery import rank_active_universe`` paths keep
working. Spec source: vault/30_components/layer-0-universe-discovery.md.
"""

from __future__ import annotations

import logging
import math
import os
import statistics
from collections.abc import Sequence

from polaris.core.streams import asset_class_allowed_for_venue
from polaris.core.universe.schema import (
    ALLOWED_QUOTE_CCY_OKX,
    RANK_PENALTY_W_DEPTH,
    RANK_PENALTY_W_SPREAD,
    RANK_SCORE_W_ATR,
    RANK_SCORE_W_VOL,
    UNIVERSE_RANK_TOP_N_DEFAULT,
    UNIVERSE_RANK_TOP_N_ENV,
    UniverseInstrument,
    is_capital_fx_major,
    liquidity_floor_for_venue,
    reliability_min_enriched_ratio,
    universe_watch_max,
)

logger = logging.getLogger(__name__)


def _venue_expects_vol_datum(venue: str) -> bool:
    """True iff the venue's liquidity floor keys on a REAL vol datum (vol>0).

    The reliability guard only governs venues where ``vol_24h_usd`` is the expected
    enrichment datum and a 0.0 means UN-ENRICHED (Alpaca: snapshot-injected; OKX:
    native from the ticker). Capital exposes NO native vol (floor vol==0) — there a
    0.0 is structural, not an enrichment miss, so the guard is a NO-OP there (it
    would otherwise wipe the whole venue). SSOT = the per-venue floor config.
    """
    return liquidity_floor_for_venue(venue).min_vol_24h_usd > 0.0


def apply_enrichment_reliability_guard(
    instruments: list[UniverseInstrument],
    *,
    min_enriched_ratio: float | None = None,
) -> list[UniverseInstrument]:
    """Bar the UN-ENRICHED (sentinel vol==0) slab from the active candidate set when
    a vol-expecting venue's enrichment came back unreliable (the 429-storm guard).

    Per (venue, asset_class) group, among the venues whose floor expects a real
    vol datum (:func:`_venue_expects_vol_datum`): if the fraction of rows carrying
    a REAL datum (``vol_24h_usd > 0``) is BELOW ``min_enriched_ratio``, the group's
    un-enriched rows are dropped from the ranking candidate set for this cycle.
    Enough enrichment landed (ratio at/above threshold) → the group is untouched
    (the sentinel rows still flow and merely rank last, as before).

    This is a DATA-QUALITY gate, NOT a defensive throttle: a row with no real
    liquidity datum cannot be asserted real-liquid, so it is not yet a WATCH
    candidate — but it is never order-blocked on a known-bad value and re-enters the
    instant its snapshot enriches (flow_not_block on every real datum). It is the
    junk-expansion blocker: with an uncapped watch set + a failed full-sweep, this
    keeps the active book bounded to the real-liquid names instead of swelling to
    the ~3000 un-enriched slab that fed the 429 spiral. Venues whose floor does not
    expect vol (Capital) are passed through unchanged.

    ``min_enriched_ratio`` resolves arg → ``POLARIS_RELIABILITY_MIN_ENRICHED_RATIO``
    env → default 0.8. A ratio of 0.0 disables the guard.
    """
    threshold = (
        reliability_min_enriched_ratio()
        if min_enriched_ratio is None
        else min(1.0, max(0.0, min_enriched_ratio))
    )
    if threshold <= 0.0:
        return instruments
    groups: dict[str, list[int]] = {}
    for i, ins in enumerate(instruments):
        groups.setdefault(f"{ins.venue}/{ins.asset_class}", []).append(i)
    drop: set[int] = set()
    for key, idxs in groups.items():
        venue = key.split("/", 1)[0]
        if not _venue_expects_vol_datum(venue):
            continue
        enriched = sum(1 for i in idxs if instruments[i].vol_24h_usd > 0.0)
        ratio = enriched / len(idxs) if idxs else 1.0
        if ratio >= threshold:
            continue
        unenriched = [i for i in idxs if instruments[i].vol_24h_usd <= 0.0]
        drop.update(unenriched)
        logger.warning(
            "[universe] reliability guard: %s enriched %d/%d (%.2f < %.2f) — "
            "barring %d un-enriched rows from active (junk-expansion blocked)",
            key, enriched, len(idxs), ratio, threshold, len(unenriched),
        )
    if not drop:
        return instruments
    return [ins for i, ins in enumerate(instruments) if i not in drop]


def apply_stream_asset_class_filter(
    instruments: list[UniverseInstrument],
) -> list[UniverseInstrument]:
    """Drop rows whose ``asset_class`` is not in their venue's stream whitelist.

    STEP 2 SSOT enforcement (Jin 2026-05-30 STEP 0 (a)): the universe selection
    now obeys ``resolve_stream(venue).asset_classes`` — A=OKX crypto, B=Capital
    forex/index/commodity, C=Alpaca equity. This removes the Capital crypto-CFD
    rows that the nav-tree walk pulled in, routing the crypto edge to OKX track A
    where it belongs. An **unregistered** venue is untouched (permissive — smoke
    paths). This is an INTENDED asset-class routing correction, NOT a defensive
    throttle: a mis-venued row is re-routed, not deemed "too risky".
    """
    kept: list[UniverseInstrument] = []
    dropped = 0
    for ins in instruments:
        if asset_class_allowed_for_venue(ins.venue, ins.asset_class):
            kept.append(ins)
        else:
            dropped += 1
    if dropped:
        logger.info(
            "[universe] stream asset-class filter: %d → %d (dropped %d off-venue rows)",
            len(instruments),
            len(kept),
            dropped,
        )
    return kept


def _is_valid_candidate(ins: UniverseInstrument) -> bool:
    """Hard validity gate (basic-valid ONLY): tradeable, live, OKX USDT-quote.

    Everything else (vol / spread / depth / atr) is a *ranking* signal, never a
    hard block — weak names still flow and the cell-matrix down-routes them.

    WATCH/TRADE DECOUPLE (Jin 2026-06-24): the universe-eligibility LIQUIDITY
    FLOOR that USED to live here was strangling breadth — it pre-cut ~176 of 186
    OKX names BEFORE z-ranking, so they never reached the active set, focus, WS,
    or the curator (the curator could only judge the surviving ~10, leaving OKX
    focus pinned at 2). The floor is NOT deleted: it is RE-HOMED to the curator
    TRADE gate (``EntranceJudge`` floor-aware ``trade_eligible``), so a sub-floor
    name is now WATCHED / streamed / dashboarded (flow_not_block) but its
    order-open is deferred (slippage protection preserved at the single
    ``_run_entries`` eligible_set seam). Basic-valid = state==live + OKX USDT
    quote; the ATR-z signal-richness rank orders every survivor.

    STEP 3 (session asymmetry, Jin 2026-05-30): ``state != 'live'`` excludes a
    row from the *active set*, but for CFD (Capital) this is a **session-wait**,
    not a permanent drop — the row is still persisted (``is_active=0``,
    ``session_wait`` reason) and re-enters automatically the next refresh once
    the venue reports it TRADEABLE again. Capital trades only when its session is
    open (off-session OKX 24/7 carries the book); the routing here is session
    STATE, not a hard block (flow_not_block). Crypto (24/7) is unaffected.
    """
    if ins.state != "live":
        return False
    return not (ins.venue == "okx" and ins.quote_ccy not in ALLOWED_QUOTE_CCY_OKX)


def _pop_z(values: Sequence[float]) -> list[float]:
    """Population z-score; zeros when len<2 or stdev is 0/non-finite (tie-safe)."""
    if len(values) < 2:
        return [0.0] * len(values)
    mu = statistics.fmean(values)
    sigma = statistics.pstdev(values)
    if sigma <= 0.0 or not math.isfinite(sigma):
        return [0.0] * len(values)
    return [(v - mu) / sigma for v in values]


def _grouped_pop_z(values: Sequence[float], groups: Sequence[str]) -> list[float]:
    """Per-group population z-score in parallel order (see watchlist._grouped_z_score).

    Each (venue, asset_class) group is z-normalized within itself BEFORE the single
    cross-venue top-N cut, so crypto's absolute trillion-scale vol cannot sweep
    every active-set slot and bury the strongest equity. A single-venue population
    is one group → byte-identical to :func:`_pop_z` (no-op). Ranking-order only
    (flow_not_block): hard keep is still validity + the liquidity floor.
    """
    by_group: dict[str, list[int]] = {}
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    out = [0.0] * len(values)
    for idxs in by_group.values():
        zs = _pop_z([values[i] for i in idxs])
        for pos, i in enumerate(idxs):
            out[i] = zs[pos]
    return out


def _resolve_rank_top_n(top_n: int | None) -> int:
    """Resolve top-N from arg → env → default, capped at the WATCH_MAX ceiling.

    WATCH/TRADE DECOUPLE (Jin 2026-06-24): the active/watch ceiling is now
    ``universe_watch_max()`` (``POLARIS_WATCH_MAX``, default 120), NOT the focus
    window ``FOCUS_TARGET_MAX`` (48). The old ``min(top_n, FOCUS_TARGET_MAX)``
    clamp CONFLATED the active set with the focus window and pinned watch at 48;
    splitting them lets the bot watch dozens+ while focus stays 12-48 downstream.
    """
    if top_n is None:
        raw = os.environ.get(UNIVERSE_RANK_TOP_N_ENV)
        if raw is not None:
            try:
                top_n = int(raw)
            except ValueError:
                top_n = UNIVERSE_RANK_TOP_N_DEFAULT
        else:
            top_n = UNIVERSE_RANK_TOP_N_DEFAULT
    return max(0, min(top_n, universe_watch_max()))


def rank_active_universe(
    instruments: list[UniverseInstrument],
    *,
    top_n: int | None = None,
) -> list[UniverseInstrument]:
    """Continuous-ranking active-set selection (replaces the hard 4-axis cut).

    Hard keep = validity only (state=live, OKX USDT-quote). Liquidity reward
    (vol + ATR realized-vol proxy) minus soft penalties (wide spread, thin
    depth) form a z-normalized composite score; the top ``top_n`` rows become
    the active set. ``top_n`` resolves arg → ``POLARIS_UNIVERSE_RANK_TOP_N``
    env → default 120, capped at the WATCH_MAX ceiling (``POLARIS_WATCH_MAX``,
    decoupled from the focus window). Empty input and degenerate
    (all-tied) populations are safe — z-scores collapse to 0 and ordering is
    stable by input order.
    """
    # STEP 2: enforce the per-venue stream asset-class whitelist BEFORE validity
    # ranking (Capital crypto-CFD rows are off-venue → routed to OKX track A).
    scoped = apply_stream_asset_class_filter(instruments)
    valid = [ins for ins in scoped if _is_valid_candidate(ins)]
    if not valid:
        return []

    # Junk-expansion blocker (Jin 2026-06-24): when a vol-expecting venue's
    # enrichment came back unreliable (the data-host 429 storm), bar its
    # un-enriched (sentinel vol==0) slab from the active candidate set so the watch
    # book stays bounded to the real-liquid names instead of swelling to ~3000
    # un-enriched rows. A NO-OP once enough enrichment lands (flow_not_block on
    # every real datum; Capital, which exposes no native vol, is untouched).
    valid = apply_enrichment_reliability_guard(valid)
    if not valid:
        return []

    groups = [f"{ins.venue}/{ins.asset_class}" for ins in valid]
    vol_z = _grouped_pop_z([max(0.0, ins.vol_24h_usd) for ins in valid], groups)
    atr_z = _grouped_pop_z([max(0.0, ins.atr_24h_pct) for ins in valid], groups)
    spread_z = _grouped_pop_z([max(0.0, ins.spread_bps) for ins in valid], groups)
    depth_z = _grouped_pop_z([max(0.0, ins.depth_10bps_usd) for ins in valid], groups)

    scores = [
        RANK_SCORE_W_VOL * vol_z[i]
        + RANK_SCORE_W_ATR * atr_z[i]
        - RANK_PENALTY_W_SPREAD * spread_z[i]
        + RANK_PENALTY_W_DEPTH * depth_z[i]
        for i in range(len(valid))
    ]

    n = _resolve_rank_top_n(top_n)
    order = sorted(range(len(valid)), key=lambda i: scores[i], reverse=True)
    selected = order[:n]

    # Per-venue Capital FX-majors keep/floor (flow_not_block): Capital exposes no
    # 24h notional (vol=0), so high-ATR exotic crosses outrank the quiet FX majors
    # and the majors never reach the active set — starving fx_breakout_basket /
    # session_breakout. Union in any VALID (state=live) curated major that the
    # top-N cut dropped, ALONGSIDE the exotics (seat BOTH, remove nothing). This
    # is a FLOW INCREASE, not a throttle, and touches no global ranking weight —
    # OKX/Alpaca active sets stay byte-identical (no Capital major present → no-op).
    chosen = set(selected)
    kept_majors = [
        i
        for i in order
        if i not in chosen and is_capital_fx_major(valid[i].venue, valid[i].symbol)
    ]
    out = [valid[i] for i in selected] + [valid[i] for i in kept_majors]
    logger.info(
        "[universe] continuous-rank: %d → valid %d → active %d (top_n=%d, fx_majors_kept=%d)",
        len(instruments),
        len(valid),
        len(out),
        n,
        len(kept_majors),
    )
    return out
