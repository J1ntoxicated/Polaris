"""Go-live confidence metrics — REAL-FEE-NET edge evidence (READ-ONLY).

Component A (Jin 2026-05-31). The go-live trigger is a REAL-FEE-NET equity
curve that trends up; these pure read functions quantify the edge behind it so
Jin can judge confidence to open real OKX. They MEASURE — they never gate, size,
block, or touch any trading decision path (no INSERT/UPDATE/DELETE, no network).

What it reports
---------------
Per ``(strategy_id × regime)`` bucket:
  - ``expected_real_fee_net_r`` : mean per-trade R after the REAL fee schedule
    (the round-trip real fee subtracted from gross close pnl, then / R-denom).
  - ``n``                       : closed-trade sample count.
  - ``lcb_real_fee_net_r``      : posterior lower-confidence-bound on that mean,
    via the NIG posterior in ``learners.posterior`` (folds the net-R samples;
    LCB = μ_n − z·scale of the marginal Student-t on μ, z = 1.0 ≈ one stderr).
    ``lcb_sign`` is ``+``/``-``/``0`` — a ``+`` LCB is the confidence signal
    (the edge is positive even at the lower bound).

Overall:
  - ``win_rate_pct``     : gross-positive closes / closes.
  - ``profit_factor``    : Σ gross win / Σ gross loss.
  - ``turnover_ratio``   : Σ all-fill notional / starting_equity.
  - ``fee_drag_real_r``  : Σ real-schedule fees / R-denom (the real cost wedge).
  - ``fee_drag_demo_r``  : Σ stored demo fees / R-denom (the 0.7% drain).

Join limitation (documented, surfaced in ``notes``)
----------------------------------------------------
There is NO per-trade historical regime persisted. A close fill is joined to its
``positions`` row (``fills.contribution_id = positions.position_id``) and then to
``regime_state`` on ``(venue, underlying_group_id)`` for the CURRENT regime label
— so the regime attribution reflects the regime *now*, not necessarily the
regime at the moment the trade closed. Closes with no matched position/regime
fall back to ``chop``. The R conversion is the same constant-risk
``PNL_R_USD_DENOM`` proxy used across the shadow-acceptance module.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from polaris.core.economics.fees import demo_fee_usd, real_fee_usd
from polaris.core.learners.posterior import NIGPosterior, nig_update

__all__ = [
    "LCB_Z",
    "PNL_R_USD_DENOM",
    "confidence_summary",
]

# Mirrors ``shadow_acceptance.PNL_R_USD_DENOM`` / ``payload_builder`` — the
# $-risk-per-trade heuristic that projects $-PnL into R. Constant-risk proxy.
PNL_R_USD_DENOM: float = 50.0

# One-sided lower-confidence-bound z. z = 1.0 ≈ one standard error (the simple
# ``mean − z·stderr`` LCB the spec permits), applied to the NIG marginal-t scale.
LCB_Z: float = 1.0

# Default weakly-informative NIG prior (mirrors ``posterior`` defaults).
_PRIOR = NIGPosterior(mu=0.0, kappa=1.0, alpha=1.0, beta=1.0)


def _nig_lcb(samples: list[float]) -> float:
    """Posterior LCB on the mean of ``samples`` via the NIG marginal Student-t.

    Folds the samples into the weak NIG prior (reusing ``posterior.nig_update``),
    then returns ``μ_n − LCB_Z · scale`` where ``scale`` is the marginal-t scale
    on μ (``sqrt(β(κ+1)/(α·κ))``). Empty / single-sample buckets degrade to the
    raw mean (no usable spread). Pure; no DB.
    """
    if not samples:
        return 0.0
    post = _PRIOR
    for x in samples:
        post = nig_update(post, x)
    if post.alpha <= 0.0 or post.kappa <= 0.0:
        return post.mu
    scale_sq = post.beta * (post.kappa + 1.0) / (post.alpha * post.kappa)
    if scale_sq <= 0.0:
        return post.mu
    return float(post.mu - LCB_Z * math.sqrt(scale_sq))


def _sign(x: float) -> str:
    return "+" if x > 0.0 else ("-" if x < 0.0 else "0")


def confidence_summary(
    conn: sqlite3.Connection, *, starting_equity: float
) -> dict[str, Any]:
    """All go-live confidence metrics in one read-only dict.

    Walks closed ``fills`` once, joining each close to its position's current
    regime (documented limitation above). Real-fee-net per-trade R subtracts the
    ROUND-TRIP real fee (open + close leg, both at the close-fill notional, since
    a round trip turns over the notional twice) from the gross close pnl.
    """
    # Current regime per (venue, group) — the only regime source available.
    regime_lookup: dict[tuple[str, str], str] = {}
    for venue, group, regime in conn.execute(
        "SELECT venue, underlying_group_id, regime FROM regime_state"
    ):
        regime_lookup[(str(venue), str(group))] = str(regime or "chop")

    # Close fills joined to their position (for the group → regime), LEFT JOIN so
    # an unmatched close still counts (regime falls back to chop).
    rows = conn.execute(
        """
        SELECT f.venue, f.strategy_id, f.size_usd, f.pnl_usd, f.fee_usd,
               p.underlying_group_id
        FROM fills f
        LEFT JOIN positions p ON f.contribution_id = p.position_id
        WHERE f.is_close = 1
        """
    ).fetchall()

    # Per-(strategy, regime) net-R samples (real-fee-net).
    cell_samples: dict[tuple[str, str], list[float]] = {}
    gross_win = 0.0
    gross_loss = 0.0
    wins = 0
    n_closed = 0
    for venue, strategy, size_usd, pnl_usd, _fee_usd, group in rows:
        n_closed += 1
        v = str(venue or "")
        strat = str(strategy or "?")
        notional = abs(float(size_usd or 0.0))
        gross = float(pnl_usd or 0.0)
        if gross > 0.0:
            wins += 1
            gross_win += gross
        elif gross < 0.0:
            gross_loss += -gross
        # Round-trip real fee = open leg + close leg, both at this notional.
        rt_real_fee = 2.0 * real_fee_usd(v, notional)
        net_r = (gross - rt_real_fee) / PNL_R_USD_DENOM
        regime = regime_lookup.get((v, str(group or "")), "chop")
        cell_samples.setdefault((strat, regime), []).append(net_r)

    # Turnover + fee drag over ALL fills (both legs).
    turnover = 0.0
    fee_drag_real_usd = 0.0
    fee_drag_demo_usd = 0.0
    for venue, size_usd, fee_usd in conn.execute(
        "SELECT venue, size_usd, fee_usd FROM fills"
    ):
        v = str(venue or "")
        notional = abs(float(size_usd or 0.0))
        turnover += notional
        fee_drag_real_usd += real_fee_usd(v, notional)
        # Demo drag = the fee the venue ACTUALLY stored (fall back to the demo
        # schedule when a row carries no stored fee).
        stored = float(fee_usd or 0.0)
        fee_drag_demo_usd += stored if stored > 0.0 else demo_fee_usd(v, notional)

    win_rate = (wins / n_closed * 100.0) if n_closed else 0.0
    profit_factor = (
        (gross_win / gross_loss) if gross_loss > 0.0
        else (9.99 if gross_win > 0.0 else 0.0)
    )
    turnover_ratio = (turnover / starting_equity) if starting_equity > 0.0 else 0.0

    by_cell: list[dict[str, Any]] = []
    for (strat, regime), samples in sorted(cell_samples.items()):
        n = len(samples)
        mean_r = sum(samples) / n if n else 0.0
        lcb = _nig_lcb(samples)
        by_cell.append({
            "strategy_id": strat,
            "regime": regime,
            "n": n,
            "expected_real_fee_net_r": mean_r,
            "lcb_real_fee_net_r": lcb,
            "lcb_sign": _sign(lcb),
        })
    # Most-sampled, then highest expectancy first (most-established edges top).
    by_cell.sort(key=lambda c: (c["n"], c["expected_real_fee_net_r"]), reverse=True)

    return {
        "overall": {
            "n_closed": n_closed,
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "turnover_ratio": turnover_ratio,
            "fee_drag_real_r": fee_drag_real_usd / PNL_R_USD_DENOM,
            "fee_drag_demo_r": fee_drag_demo_usd / PNL_R_USD_DENOM,
        },
        "by_strategy_regime": by_cell,
        "notes": {
            "r_conversion": f"pnl_usd / {PNL_R_USD_DENOM} (constant-risk proxy)",
            "lcb": f"NIG posterior μ_n − {LCB_Z}·scale (marginal Student-t on μ)",
            "join_limitation": (
                "regime = CURRENT regime_state on (venue, group) via "
                "fills.contribution_id→positions; not the regime at close time; "
                "unmatched closes fall back to 'chop'"
            ),
        },
    }
