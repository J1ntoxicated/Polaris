"""Layer 4 — routing & update entry points (quartile classify + atomic per-trade update).

Spec source: vault/30_components/layer-4-cell-matrix.md (Q3, Q4, Q5, Q6).
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Sequence
from typing import Literal

from polaris.core.cell_matrix.schema import (
    CELL_DECAY_HALF_LIFE_SEC,
    CELL_MIN_LIVE_N,
    CELL_MIN_POOL_SIZE,
    CELL_SHRINKAGE_N,
    ROUTING_BOTTOM_MULT,
    ROUTING_MID_MULT,
    ROUTING_TOP_MULT,
    CellContext,
    CellKeyP0,
    CellStat,
    TradeClose,
)
from polaris.core.cell_matrix.score import (
    compute_avg_pnl_r,
    compute_cell_score,
    decay_factor,
    posterior_tilt,
    regime_alignment_mult,
    resolve_effective_score,
)
from polaris.core.cell_matrix.update import update_on_trade_close

logger = logging.getLogger(__name__)

__all__ = [
    "Quartile",
    "classify_quartile",
    "compute_routing_mult",
    "fetch_cell_stat",
    "fetch_parent2_score",
    "fetch_parent3_score",
    "fetch_posterior_tilt",
    "load_eligible_scores",
    "load_eligible_scores_decayed",
    "resolve_routing_for_cell",
    "update_on_trade_close",
]

Quartile = Literal["top", "bottom", "mid", "cold"]


# ---------------------------------------------------------------------------
# Quartile classification (dynamic, recomputed each routing decision)
# ---------------------------------------------------------------------------


def classify_quartile(
    score: float,
    *,
    eligible_scores: Sequence[float],
    min_pool_size: int = CELL_MIN_POOL_SIZE,
) -> Quartile:
    """Classify ``score`` into top / bottom / mid quartile of the live pool.

    Returns ``"cold"`` (forces ×1.0 routing) when the eligible pool is below
    the activation gate, regardless of where the score lands.
    """
    if len(eligible_scores) < min_pool_size:
        return "cold"
    arr = sorted(eligible_scores)
    q25 = _quantile(arr, 0.25)
    q75 = _quantile(arr, 0.75)
    if not (math.isfinite(q25) and math.isfinite(q75)):
        return "mid"
    if score >= q75:
        return "top"
    if score <= q25:
        return "bottom"
    return "mid"


def _quantile(sorted_arr: list[float], pct: float) -> float:
    if not sorted_arr:
        return 0.0
    if pct <= 0.0:
        return sorted_arr[0]
    if pct >= 1.0:
        return sorted_arr[-1]
    pos = pct * (len(sorted_arr) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_arr) - 1)
    frac = pos - lo
    return sorted_arr[lo] * (1.0 - frac) + sorted_arr[hi] * frac


# ---------------------------------------------------------------------------
# Routing multiplier
# ---------------------------------------------------------------------------


def compute_routing_mult(
    *,
    n_eff: float,
    effective_score: float,
    eligible_scores: Sequence[float],
    min_live_n: float = CELL_MIN_LIVE_N,
    min_pool_size: int = CELL_MIN_POOL_SIZE,
    top_mult: float = ROUTING_TOP_MULT,
    bottom_mult: float = ROUTING_BOTTOM_MULT,
    mid_mult: float = ROUTING_MID_MULT,
) -> float:
    """Resolve the cell routing multiplier (×1.5 / ×0.5 / ×1.0).

    - n_eff < min_live_n → ×1.0 neutral (cold cell, parent fallback rejected).
    - eligible pool < min_pool_size → ×1.0 (gate not yet active).
    - top quartile → ``top_mult`` (default 1.5 — ADR-005 patch).
    - bottom quartile → ``bottom_mult`` (default 0.5).
    - mid 50% → ``mid_mult`` (1.0).

    NOTE: ``effective_score`` MUST already be the warmup-shrunk parent-blended
    value (see :func:`resolve_routing_for_cell`). Passing the raw
    ``cell.score`` directly is a spec violation for ``5 ≤ n_eff < 20``.
    """
    if n_eff < min_live_n:
        return mid_mult
    if len(eligible_scores) < min_pool_size:
        return mid_mult
    quartile = classify_quartile(
        effective_score,
        eligible_scores=eligible_scores,
        min_pool_size=min_pool_size,
    )
    if quartile == "top":
        return top_mult
    if quartile == "bottom":
        return bottom_mult
    return mid_mult


def resolve_routing_for_cell(
    conn: sqlite3.Connection,
    key: CellKeyP0,
    *,
    now_ts: int,
    min_live_n: float = CELL_MIN_LIVE_N,
    min_pool_size: int = CELL_MIN_POOL_SIZE,
    half_life_sec: int = CELL_DECAY_HALF_LIFE_SEC,
    top_mult: float = ROUTING_TOP_MULT,
    bottom_mult: float = ROUTING_BOTTOM_MULT,
    mid_mult: float = ROUTING_MID_MULT,
) -> float:
    """End-to-end routing decision for one cell at ``now_ts`` (production SSOT).

    Composes:
      1. Read cell + parent3 + parent2 (decayed to ``now_ts``).
      2. Apply warmup shrinkage 5 ≤ n_eff < 20 (parent3 → parent2 → 0).
      3. Edge-routing tilt of the (warmup-shrunk) effective score — the INPUT to
         the quartile classifier, NOT a new sizing multiplier:
           * ``regime_alignment_mult`` (1.25/1.0/0.8, ADR constants) for the
             regime-aligned/misaligned strategy,
           * a continuous ``posterior_tilt`` (0.5..1.5) from the cell's
             ``learner_posterior.p_pos`` (neutral 1.0 below POSTERIOR_TILT_MIN_N).
         Both are pure multipliers ⇒ sign preserved, never zeroed; the eligible
         pool (the comparison distribution) is left raw, so the tilt re-ranks the
         cell against a stable pool. The mult OUTPUT is still {1.5, 1.0, 0.5}.
      4. Build the eligible pool from cells with decayed n_eff ≥ min_live_n.
      5. Classify quartile + return mult.

    This is the only function strategy code should call. Lower-level helpers
    are exported for tests + observability.
    """
    cell = fetch_cell_stat(conn, key)
    if cell is None:
        return mid_mult

    cell_n_eff = _decayed_n_eff(cell.n_eff, last_ts=cell.last_closed_ts, now_ts=now_ts,
                                half_life_sec=half_life_sec)
    if cell_n_eff < min_live_n:
        return mid_mult

    cell_score_decayed = _decayed_score(
        cell.score, factor_n=cell_n_eff / cell.n_eff if cell.n_eff > 0 else 1.0,
    )

    parent3 = fetch_parent3_score(conn, key, now_ts=now_ts, half_life_sec=half_life_sec)
    parent2 = fetch_parent2_score(conn, key, now_ts=now_ts, half_life_sec=half_life_sec)

    effective = resolve_effective_score(
        cell_score=cell_score_decayed,
        cell_n_eff=cell_n_eff,
        parent3_score=parent3,
        parent2_score=parent2,
    )

    # Edge-routing tilt of the effective score (the quartile-classifier INPUT).
    # Pure multipliers on the routing score only — NO new sizing-chain slot, NO
    # block: regime alignment (existing 1.25/1.0/0.8) + a continuous posterior
    # tilt (0.5..1.5, neutral 1.0 when the posterior is cold/sparse). Sign is
    # preserved and the value is never zeroed (flow_not_block).
    align = regime_alignment_mult(
        strategy=key.strategy, regime=key.regime, exchange=key.exchange
    )
    tilt = fetch_posterior_tilt(conn, key)
    effective = effective * align * tilt

    pool = load_eligible_scores_decayed(conn, now_ts=now_ts, min_live_n=min_live_n,
                                        half_life_sec=half_life_sec)

    mult = compute_routing_mult(
        n_eff=cell_n_eff,
        effective_score=effective,
        eligible_scores=pool,
        min_live_n=min_live_n,
        min_pool_size=min_pool_size,
        top_mult=top_mult,
        bottom_mult=bottom_mult,
        mid_mult=mid_mult,
    )
    quartile = (
        classify_quartile(effective, eligible_scores=pool, min_pool_size=min_pool_size)
        if cell_n_eff >= min_live_n and len(pool) >= min_pool_size
        else "cold"
    )
    logger.info(
        "[cell] %s/%s/%s/%s n_eff=%.1f score=%.3f (align=%.2f tilt=%.2f) "
        "pool=%d quartile=%s → mult=%.2fx",
        key.exchange,
        key.strategy,
        key.ticker,
        key.regime,
        cell_n_eff,
        effective,
        align,
        tilt,
        len(pool),
        quartile,
        mult,
    )
    return mult


def _decayed_n_eff(
    stored_n_eff: float,
    *,
    last_ts: int,
    now_ts: int,
    half_life_sec: int,
) -> float:
    if stored_n_eff <= 0.0 or last_ts <= 0:
        return stored_n_eff
    elapsed = max(0.0, float(now_ts - last_ts))
    return stored_n_eff * decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec)


def _decayed_score(stored_score: float, *, factor_n: float) -> float:
    """Decay a stored score given the n_eff decay factor.

    score = avg × √n / 70 ⇒ score ∝ √n. So decaying n by ``f`` decays score by
    ``√f``. ``avg_pnl_r`` (a ratio) is unchanged by the decay.
    """
    if factor_n <= 0.0:
        return 0.0
    return stored_score * math.sqrt(factor_n)


def load_eligible_scores_decayed(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    min_live_n: float = CELL_MIN_LIVE_N,
    half_life_sec: int = CELL_DECAY_HALF_LIFE_SEC,
) -> list[float]:
    """Eligible-pool scores with read-time EWMA decay applied.

    Stale cells whose ``n_eff`` decays below ``min_live_n`` are dropped from
    the pool, so a stagnant winner cannot keep a top-quartile rank forever.
    """
    rows = conn.execute(
        "SELECT n_eff, score, last_closed_ts FROM cell_matrix_p0 WHERE n_eff > 0"
    ).fetchall()
    out: list[float] = []
    for stored_n, stored_score, last_ts in rows:
        n_dec = _decayed_n_eff(
            float(stored_n), last_ts=int(last_ts), now_ts=now_ts, half_life_sec=half_life_sec
        )
        if n_dec < min_live_n:
            continue
        factor = n_dec / float(stored_n) if stored_n > 0 else 1.0
        out.append(_decayed_score(float(stored_score), factor_n=factor))
    return out





# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def fetch_cell_stat(conn: sqlite3.Connection, key: CellKeyP0) -> CellStat | None:
    """Fetch a 4-dim cell stat row; returns None if absent."""
    row = conn.execute(
        """
        SELECT n_eff, wins_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts
        FROM cell_matrix_p0
        WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?
        """,
        (key.exchange, key.strategy, key.ticker, key.regime),
    ).fetchone()
    if row is None:
        return None
    return CellStat(
        key=key,
        n_eff=float(row[0]),
        wins_eff=float(row[1]),
        pnl_r_sum_eff=float(row[2]),
        avg_pnl_r=float(row[3]),
        score=float(row[4]),
        last_closed_ts=int(row[5]),
    )


def fetch_posterior_tilt(conn: sqlite3.Connection, key: CellKeyP0) -> float:
    """Read the cell's posterior conviction tilt for the routing score.

    Reads ``learner_posterior.p_pos`` + ``n_samples`` for this 4-dim cell and
    maps them through :func:`posterior_tilt` (continuous 0.5..1.5, neutral 1.0
    below ``POSTERIOR_TILT_MIN_N``). Returns 1.0 (neutral — no tilt) when there
    is no row OR on any DB error: a cold / unreadable posterior must never
    amplify or suppress, so the cell routes exactly as it did before the
    posterior signal existed (fail-open, flow_not_block).
    """
    try:
        row = conn.execute(
            "SELECT p_pos, n_samples FROM learner_posterior "
            "WHERE exchange = ? AND strategy = ? AND ticker = ? AND regime = ?",
            (key.exchange, key.strategy, key.ticker, key.regime),
        ).fetchone()
    except sqlite3.Error:
        return 1.0
    if row is None:
        return 1.0
    return posterior_tilt(p_pos=float(row[0]), n_samples=int(row[1]))


def fetch_parent3_score(
    conn: sqlite3.Connection,
    key: CellKeyP0,
    *,
    now_ts: int | None = None,
    half_life_sec: int = CELL_DECAY_HALF_LIFE_SEC,
) -> float | None:
    """Fetch the 3-dim parent score (exchange × strategy × regime).

    With ``now_ts`` set, the stored score is decayed by ``√(n_decay_factor)``
    because T11 score ∝ √n_eff (linear-decay would over-shrink — the
    ``avg_pnl_r`` ratio is unaffected by the decay itself).
    """
    row = conn.execute(
        """
        SELECT score, last_closed_ts
        FROM cell_matrix_parent3
        WHERE exchange = ? AND strategy = ? AND regime = ?
        """,
        (key.exchange, key.strategy, key.regime),
    ).fetchone()
    if row is None:
        return None
    score, last_ts = float(row[0]), int(row[1])
    if now_ts is None or last_ts <= 0:
        return score
    elapsed = max(0.0, float(now_ts - last_ts))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec)
    return score * math.sqrt(factor)


def fetch_parent2_score(
    conn: sqlite3.Connection,
    key: CellKeyP0,
    *,
    now_ts: int | None = None,
    half_life_sec: int = CELL_DECAY_HALF_LIFE_SEC,
) -> float | None:
    """Fetch the 2-dim parent score (strategy × regime), EWMA-decayed to ``now_ts``.

    Decay scales by ``√factor`` (T11 score ∝ √n_eff).
    """
    row = conn.execute(
        """
        SELECT score, last_closed_ts
        FROM cell_matrix_parent2
        WHERE strategy = ? AND regime = ?
        """,
        (key.strategy, key.regime),
    ).fetchone()
    if row is None:
        return None
    score, last_ts = float(row[0]), int(row[1])
    if now_ts is None or last_ts <= 0:
        return score
    elapsed = max(0.0, float(now_ts - last_ts))
    factor = decay_factor(elapsed_sec=elapsed, half_life_sec=half_life_sec)
    return score * math.sqrt(factor)


def load_eligible_scores(
    conn: sqlite3.Connection,
    *,
    min_live_n: float = CELL_MIN_LIVE_N,
) -> list[float]:
    """Return the bare stored scores (NO read-time decay).

    .. warning::
        Observability/diagnostics path only. Production routing **must** call
        :func:`load_eligible_scores_decayed` (or :func:`resolve_routing_for_cell`)
        so stale winners drop out of the eligible pool. Mixing this helper into
        a routing path re-introduces the codex-round-1 P0 staleness bug.
    """
    rows = conn.execute(
        "SELECT score FROM cell_matrix_p0 WHERE n_eff >= ?",
        (min_live_n,),
    ).fetchall()
    return [float(r[0]) for r in rows]


__all__ += [
    "CELL_DECAY_HALF_LIFE_SEC",
    "CELL_MIN_LIVE_N",
    "CELL_MIN_POOL_SIZE",
    "CELL_SHRINKAGE_N",
    "CellContext",
    "CellKeyP0",
    "CellStat",
    "TradeClose",
    "compute_avg_pnl_r",
    "compute_cell_score",
    "decay_factor",
    "resolve_effective_score",
]
