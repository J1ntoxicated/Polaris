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
    FOCUS_TARGET_MAX,
    RANK_PENALTY_W_DEPTH,
    RANK_PENALTY_W_SPREAD,
    RANK_SCORE_W_ATR,
    RANK_SCORE_W_VOL,
    UNIVERSE_RANK_TOP_N_DEFAULT,
    UNIVERSE_RANK_TOP_N_ENV,
    UniverseInstrument,
    is_capital_fx_major,
    passes_liquidity_floor,
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
    """Hard validity gate (kept hard): tradeable, live, OKX USDT-quote.

    Everything else (vol / spread / depth / atr) is a *ranking* signal, never a
    hard block — weak names still flow and the cell-matrix down-routes them.

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
    if ins.venue == "okx" and ins.quote_ccy not in ALLOWED_QUOTE_CCY_OKX:
        return False
    # Universe-eligibility liquidity floor (Jin-approved 2026-06-22): the SAME
    # KIND of hard keep as the two above — a tradeable-QUALITY membership test on
    # the instrument (does this instrument's microstructure even permit a clean
    # round-trip?), NOT a per-signal block / size-cut / entry-veto / expectancy
    # judgement. A row whose venue spread / $-volume / depth / price falls
    # outside its venue floor is structurally un-tradeable — its spread exceeds
    # the executable round-trip cost (the bid/ask alone consumes the trade
    # before any thesis is tested), so it never reaches the active set, the focus
    # list, the WS sub, or the tick engine. This is a universe-membership
    # (is-it-tradeable) boundary, not an edge/0.3R filter — the ATR-z
    # signal-richness rank still orders every survivor (flow_not_block).
    return passes_liquidity_floor(ins)


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
    """Resolve top-N from arg → env → default, capped at FOCUS_TARGET_MAX."""
    if top_n is None:
        raw = os.environ.get(UNIVERSE_RANK_TOP_N_ENV)
        if raw is not None:
            try:
                top_n = int(raw)
            except ValueError:
                top_n = UNIVERSE_RANK_TOP_N_DEFAULT
        else:
            top_n = UNIVERSE_RANK_TOP_N_DEFAULT
    return max(0, min(top_n, FOCUS_TARGET_MAX))


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
    env → default 40, capped at FOCUS_TARGET_MAX. Empty input and degenerate
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
