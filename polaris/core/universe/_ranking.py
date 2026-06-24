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
    UNIVERSE_RANK_TOP_N_ENV,
    UniverseInstrument,
    is_capital_fx_major,
    passes_liquidity_floor,
    universe_watch_max,
)

logger = logging.getLogger(__name__)


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


def apply_alpaca_watch_floor(
    instruments: list[UniverseInstrument],
) -> list[UniverseInstrument]:
    """Per-venue WATCH-set resource bound for Alpaca (the 13k-row equity venue).

    STAGE 2b INC2 (Jin 2026-06-24): with the count cap removed, watch-all-valid on
    Alpaca would otherwise admit ~12.8k tradable equities. Full-snapshot enrichment
    (now the default) gives every row a REAL ``vol_24h_usd`` + ``last_price``, so
    the EXISTING per-venue liquidity floor (min_vol $5M / min_price $1) auto-bounds
    Alpaca to the real-liquid set (measured ~1.5k). This applies that floor at the
    WATCH stage **for Alpaca rows only**:

      * A row with a KNOWN sub-floor real datum (vol > 0 and < $5M, or price > 0
        and < $1) is removed from the watch set — a measured resource cut, never a
        block on an unknown.
      * A sentinel/un-enriched row (vol == 0, price == 0) is KEPT (flow_not_block:
        never drop on a missing datum).

    NON-Alpaca rows pass through BYTE-IDENTICAL: OKX/Capital keep watch-all-valid
    (their floor stays on the TRADE gate — no re-introduction of the OKX 176/186
    pre-cut the 2026-06-24 decouple removed). This is the venue-targeted analogue
    of the Capital FX-major keep: a per-venue policy, not a global ranking change.
    """
    out: list[UniverseInstrument] = []
    dropped = 0
    for ins in instruments:
        if ins.venue == "alpaca" and not passes_liquidity_floor(ins):
            dropped += 1
            continue
        out.append(ins)
    if dropped:
        logger.info(
            "[universe] alpaca watch-floor: %d → %d (dropped %d sub-floor real-datum rows)",
            len(instruments),
            len(out),
            dropped,
        )
    return out


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


def _resolve_rank_top_n(top_n: int | None, valid_count: int) -> int:
    """Resolve the active-set size for ``valid_count`` valid rows.

    STAGE 2b INC2 (Jin 2026-06-24): the COUNT CAP is removed — by default the bot
    WATCHES ALL valid rows. With env unset/garbage and ``top_n=None`` the size is
    ``valid_count`` itself (no cut), bounded only by the GENEROUS safety backstop
    ``universe_watch_max()`` (default 3000) so a pathological tail (enrichment
    outage → many un-floored rows) can never explode the watch set. An explicit
    ``POLARIS_UNIVERSE_RANK_TOP_N`` / ``POLARIS_WATCH_MAX`` / ``top_n`` arg still
    binds (operator resource override). flow_not_block: the normal path is no-cut.
    """
    watch_ceiling = universe_watch_max()
    if top_n is None:
        raw = os.environ.get(UNIVERSE_RANK_TOP_N_ENV)
        if raw is None or raw == "":
            # No explicit request → watch ALL valid (bounded by the safety backstop).
            return max(0, min(valid_count, watch_ceiling))
        try:
            top_n = int(raw)
        except ValueError:
            return max(0, min(valid_count, watch_ceiling))
    return max(0, min(top_n, watch_ceiling))


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

    n = _resolve_rank_top_n(top_n, len(valid))
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
