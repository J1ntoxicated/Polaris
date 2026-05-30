"""Capital-rotation WIRE — DB readers (split from ``_production_rotation``).

DEMO/PAPER ONLY. The I/O half of the rotation wire: read the learner posterior
and cell-matrix fallback into the pure evaluator's value objects, and load the
OPEN held positions on a venue as rotation victims. Split out of
:mod:`polaris.scripts._production_rotation` for the ≤500-LOC budget; the parent
re-exports :func:`read_cell_posterior`, :func:`build_rotation_candidate`, and
:func:`load_held_positions` so existing import paths keep working.

A position already ``status='closed'`` (e.g. the precise-exit FSM closed it THIS
tick) is excluded by the WHERE clause, so rotation never double-closes it — it
defers to the exit engine. No T4 multiplier, no P&L halt: this only reads state.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable

from polaris.core.rotation import (
    ROTATION_CREDIBLE_LEVEL,
    CellStats,
    HeldPosition,
    RotationCandidate,
    held_forward_score,
)
from polaris.strategies import RawSignal

logger = logging.getLogger(__name__)

__all__ = [
    "build_rotation_candidate",
    "load_held_positions",
    "read_cell_posterior",
]


# ---------------------------------------------------------------------------
# learner_posterior reader — CellStats from the NIG marginal-t on mu.
# ---------------------------------------------------------------------------


def read_cell_posterior(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
) -> CellStats | None:
    """Read a cell's forward-edge inputs for the rotation evaluator.

    Reads ``learner_posterior`` by (exchange, strategy, ticker, regime) and
    derives the forward-score inputs the pure evaluator needs:

      * ``mu``           — the NIG marginal-t loc on cell expectancy,
      * ``posterior_sd`` — the marginal-t SCALE ``sqrt(beta*(kappa+1)/(alpha*
                           kappa))`` (same model as
                           :mod:`polaris.core.learners.posterior`),
      * ``alpha_n``      — so the evaluator uses ``df = 2*alpha`` for the t-tail,
      * ``n_samples``    — the trust gate (the evaluator rejects ``n < N``).

    When there is no posterior row, falls back to ``cell_matrix_p0.avg_pnl_r``
    (``mu`` left ``None`` so the cell is COLD for a *candidate* but still rankable
    as a *held victim* via the avg fallback). Returns ``None`` only when neither
    source has the cell. Fail-open: a DB error returns ``None`` (rotation simply
    skips this cell) so it can never wedge the tick.
    """
    try:
        row = conn.execute(
            "SELECT mu, kappa, alpha, beta, n_samples FROM learner_posterior "
            "WHERE exchange=? AND strategy=? AND ticker=? AND regime=?",
            (exchange, strategy, ticker, regime),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("[rotation] posterior read failed %s:%s — %r", strategy, ticker, exc)
        return None
    if row is not None:
        mu = float(row[0])
        kappa = float(row[1])
        alpha = float(row[2])
        beta = float(row[3])
        n_samples = int(row[4])
        posterior_sd = _marginal_t_scale(kappa=kappa, alpha=alpha, beta=beta)
        avg = _read_cell_avg_pnl_r(
            conn, exchange=exchange, strategy=strategy, ticker=ticker, regime=regime
        )
        return CellStats(
            mu=mu, posterior_sd=posterior_sd, alpha_n=alpha,
            n_samples=n_samples, avg_pnl_r=avg if avg is not None else mu,
        )
    # No posterior — try the cell-matrix fallback (held-victim ranking only).
    avg = _read_cell_avg_pnl_r(
        conn, exchange=exchange, strategy=strategy, ticker=ticker, regime=regime
    )
    if avg is None:
        return None
    return CellStats(
        mu=None, posterior_sd=None, alpha_n=None, n_samples=0, avg_pnl_r=avg,
    )


def _marginal_t_scale(*, kappa: float, alpha: float, beta: float) -> float | None:
    """NIG marginal-t SCALE on mu: ``sqrt(beta*(kappa+1)/(alpha*kappa))``.

    This is the same scale :mod:`polaris.core.learners.posterior` documents for
    the Student-t marginal on the expectancy ``mu`` (df = 2*alpha). Returns
    ``None`` when the params are degenerate (so the evaluator treats the cell as
    unscored rather than crashing).
    """
    if alpha <= 0.0 or kappa <= 0.0 or beta < 0.0:
        return None
    val = beta * (kappa + 1.0) / (alpha * kappa)
    if not math.isfinite(val) or val < 0.0:
        return None
    return math.sqrt(val)


def _read_cell_avg_pnl_r(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    strategy: str,
    ticker: str,
    regime: str,
) -> float | None:
    """Read ``cell_matrix_p0.avg_pnl_r`` for the held-victim forward fallback."""
    try:
        row = conn.execute(
            "SELECT avg_pnl_r FROM cell_matrix_p0 "
            "WHERE exchange=? AND strategy=? AND ticker=? AND regime=?",
            (exchange, strategy, ticker, regime),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    val = float(row[0])
    return val if math.isfinite(val) else None


# ---------------------------------------------------------------------------
# build CORE value objects from DB state.
# ---------------------------------------------------------------------------


def build_rotation_candidate(
    conn: sqlite3.Connection,
    *,
    sig: RawSignal,
    venue: str,
    symbol: str,
    regime: str,
    proposed_risk_pct: float,
    equity: float,
) -> RotationCandidate | None:
    """Build a pure ``RotationCandidate`` from the signal's OWN cell posterior.

    The candidate's forward edge is its (venue, strategy, ticker, regime)
    learner posterior — NOT the proposed_risk_pct (that enters only as the
    capital scale). The open-leg notional for the cost estimate is the capital
    the entry would deploy (``proposed_risk_pct * equity``). Returns ``None``
    when the cell cannot be read at all (the evaluator skips it).
    """
    cell = read_cell_posterior(
        conn, exchange=venue, strategy=sig.strategy_id, ticker=symbol, regime=regime
    )
    if cell is None:
        cell = CellStats(
            mu=None, posterior_sd=None, alpha_n=None, n_samples=0, avg_pnl_r=None
        )
    return RotationCandidate(
        candidate_id=symbol,
        venue=venue,
        cell_stats=cell,
        proposed_risk_pct=float(proposed_risk_pct),
        proposed_size_usd=max(0.0, float(proposed_risk_pct) * float(equity)),
    )


# ---------------------------------------------------------------------------
# held-position loader — OPEN positions on a venue as rotation victims.
# ---------------------------------------------------------------------------


def load_held_positions(
    conn: sqlite3.Connection,
    *,
    venue: str,
    now_ts: int,
    lookup_regime: Callable[[sqlite3.Connection, str, str], str],
    equity: float,
) -> tuple[list[HeldPosition], dict[str, dict[str, str]]]:
    """Load OPEN held positions on ``venue`` as ``HeldPosition`` victims.

    Returns the list plus an id->(symbol, strategy) map (the frozen CORE
    ``HeldPosition`` carries no symbol/strategy; the wire needs them for the
    close call + the vacated cooldown). A position already ``status='closed'``
    (e.g. the precise-exit FSM closed it THIS tick) is excluded by the WHERE
    clause, so rotation never double-closes it (defers to the exit engine).

    The forward R is the cell posterior LOWER credible bound (or the cell-matrix
    ``avg_pnl_r`` fallback) so a held is rankable even when its posterior is
    cold. ``open_risk_pct_held`` is approximated from the entry fill notional vs
    equity (the positions row carries no risk_pct).
    """
    rows = conn.execute(
        """
        SELECT p.position_id, p.symbol, p.active_strategy_id, p.side,
               p.opened_ts, p.exit_state, p.entry_strategy_id, p.strategy_id,
               f.size_usd, f.fill_price
        FROM positions p
        LEFT JOIN fills f
          ON f.contribution_id = p.position_id AND f.is_close = 0
        WHERE p.venue = ? AND p.status NOT IN ('closed','cancelled')
        ORDER BY p.opened_ts ASC
        """,
        (venue,),
    ).fetchall()
    held: list[HeldPosition] = []
    id_map: dict[str, dict[str, str]] = {}
    for r in rows:
        position_id = str(r[0])
        symbol = str(r[1])
        strategy = str(r[2] or r[6] or r[7])
        side = str(r[3])
        opened_ts = int(r[4])
        exit_state = str(r[5] or "open")
        size_usd = float(r[8]) if r[8] is not None else 0.0
        entry_price = float(r[9]) if r[9] is not None else 0.0
        regime = lookup_regime(conn, venue, symbol)
        cell = read_cell_posterior(
            conn, exchange=venue, strategy=strategy, ticker=symbol, regime=regime
        )
        fwd_R = held_forward_score(
            cell, lower_credible_bound=ROTATION_CREDIBLE_LEVEL
        )
        open_risk_pct = (size_usd / equity) if equity > 0.0 else 0.0
        pnl_r = _held_pnl_r(conn, venue=venue, symbol=symbol, side=side,
                            entry_price=entry_price)
        held.append(
            HeldPosition(
                position_id=position_id,
                venue=venue,
                fwd_R_held=fwd_R,
                open_risk_pct_held=open_risk_pct,
                exit_state=exit_state,
                pnl_r=pnl_r,
                held_sec=float(max(0, now_ts - opened_ts)),
            )
        )
        id_map[position_id] = {
            "symbol": symbol, "strategy": strategy, "size_usd": str(size_usd),
        }
    return held, id_map


def _held_pnl_r(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    side: str,
    entry_price: float,
) -> float:
    """Unrealized R for the held — drift of the latest 1m bar vs the entry fill.

    Mirrors the realised-PnL denominator (``entry*atr_pct*2``) used elsewhere so
    the sign + magnitude are consistent. Fail-open to 0.0 (treated as a
    non-loser, hence NOT an eligible victim) when bars are missing.
    """
    if entry_price <= 0.0:
        return 0.0
    try:
        bars = conn.execute(
            "SELECT close, high, low FROM bars "
            "WHERE instrument_id = ? AND bar_interval='1m' "
            "ORDER BY ts DESC LIMIT 14",
            (f"{venue}:{symbol}",),
        ).fetchall()
    except sqlite3.Error:
        return 0.0
    if not bars:
        return 0.0
    last_close = float(bars[0][0])
    if last_close <= 0.0:
        return 0.0
    samples = [
        (float(b[1]) - float(b[2])) / float(b[0]) for b in bars if float(b[0]) > 0.0
    ]
    atr_pct = sum(samples) / len(samples) if samples else 0.005
    atr_usd = max(entry_price * atr_pct * 2.0, 1e-6)
    drift = (last_close - entry_price) if side == "long" else (entry_price - last_close)
    return max(-10.0, min(10.0, drift / atr_usd))
