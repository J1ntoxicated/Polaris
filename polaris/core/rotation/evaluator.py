"""Capital-rotation evaluator — pure opportunity-cost redeploy logic (no I/O).

DEMO/PAPER ONLY — virtual funds. Finite capital across venues: when a NEW signal
is blocked **for a capital reason** (entry_sizer ``sizing_zero`` on a binding
cap, or OKX ``insufficient_balance``/51008), this evaluator decides whether to
free capital by closing the single WEAKEST measured-losing held position and
redeploying it into the single BEST blocked candidate — **iff** the new
opportunity's expected dollar edge beats the weak one by an ADDITIVE margin plus
the round-trip rotation cost.

This is a **capital-EFFICIENCY** mechanism, NOT a defensive throttle:
  - every fire has a concrete pending entry → NET deployed capital goes UP,
  - winners (``exit_state`` in {protected, harvest}) are NEVER touched,
  - there is NO P&L halt and NO block filter — flow_not_block compatible,
  - it adds NO T4 sizing multiplier; it merely orchestrates close + open.

Correctness contract (the /debate cross-verify fatal fix)
---------------------------------------------------------
Both sides are compared in the SAME unit — **expected DOLLAR edge**:

    E$_new  = fwd_mu(new_cell)   * proposed_risk_pct * equity
    E$_held = fwd_R(position)    * open_risk_pct_held * equity

``proposed_risk_pct`` / ``open_risk_pct_held`` are the CAPITAL SCALE only (the
fraction of equity at risk), NOT the edge. The edge is the per-trade expected R
from the cell's OWN learner posterior. The comparison is

    rotate iff  E$_new - E$_held > IMPROVEMENT_MARGIN + ROTATION_COST   (ADDITIVE)

The margin is ADDITIVE in dollars — never multiplicative ``(1+x)``: the weak
side is negative-by-selection so a multiplicative margin would invert intent
(it would LOWER the bar as the held loss deepens).

Estimator family (chosen ONCE, documented)
-------------------------------------------
The forward score is the **lower credible bound of the NIG marginal Student-t on
the cell expectancy ``mu``** (the same posterior :mod:`polaris.core.learners.
posterior` already maintains). We use a single estimator family throughout —
never the raw point estimate (winner's/loser's-curse guard):

    df    = 2 * alpha_n
    scale = posterior_sd                      (the marginal-t scale on mu)
    lcb   = mu - t_quantile(level, df) * scale

where ``t_quantile(level, df)`` is the upper-tail Student-t quantile at the
chosen one-sided credible ``level`` (e.g. 0.90), inverted from the stdlib-only
:func:`polaris.core.learners.posterior.student_t_cdf` by bisection. Eligibility:
a cell is only rankable at ``n_samples >= POSTERIOR_MIN_N``; a COLD cell (n<N or
no posterior) is **ineligible as a candidate** (returns ``None`` from
:func:`forward_score`) so a cold candidate can NEVER out-rank a measured-negative
held position. A HELD position that lacks a posterior falls back to its raw
``avg_pnl_r`` for ranking among victims (it is being considered for CLOSE, not
for new capital), but a held that cannot be scored at all is excluded from
selection.

The ``ROTATION_COST`` mirrors :func:`polaris.core.learners.posterior.
cost_adjusted_pnl_r` (per-leg bps on Capital, fee + slippage on OKX), summed
over the close leg (A) and the open leg (B).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Final

from polaris.core.learners._primitives import (
    COST_BPS_CAPITAL,
    COST_BPS_OKX_FALLBACK,
)
from polaris.core.learners.posterior import student_t_cdf

__all__ = [
    "POSTERIOR_MIN_N",
    "ROTATION_CREDIBLE_LEVEL",
    "ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD",
    "ROTATION_MAX_PER_TICK",
    "ROTATION_MIN_HOLD_SEC",
    "CellStats",
    "HeldPosition",
    "RotationCandidate",
    "choose_rotation",
    "expected_dollar_edge_held",
    "expected_dollar_edge_new",
    "forward_score",
    "rotation_cost_usd",
    "select_weakest",
    "should_rotate",
]

# Winner-exempt FSM labels (mirror exit_engine.EXIT_STATE_*). A position in
# either of these states has round-tripped a real gain into a precise-exit FSM
# and is NEVER a rotation victim.
_EXIT_STATE_OPEN: Final[str] = "open"
_WINNER_EXIT_STATES: Final[frozenset[str]] = frozenset({"protected", "harvest"})


# --------------------------------------------------------------------------
# Trading parameters — env-overridable. Chosen defaults flagged in the build
# return (pending /debate). MAX_PER_TICK is STRUCTURAL (cascade guard) but is
# surfaced here for the caller; the caller enforces it.
# --------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if math.isfinite(val) and val > 0.0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


# Minimum sample count before a cell posterior is trusted to rank — aligned to
# the codebase trust floor (cell_matrix CELL_SHRINKAGE_N / learner
# LEARNER_MIN_NEFF_FOR_DELTA = 20). Cross-verify raised 10 -> 20.
POSTERIOR_MIN_N: Final[int] = _env_int("POLARIS_ROTATION_POSTERIOR_MIN_N", 20)

# One-sided credible level for the lower bound (0.90 => ~1.3 t-sigma haircut at
# moderate df). Conservative: the new candidate must clear a discounted edge.
ROTATION_CREDIBLE_LEVEL: Final[float] = _env_float(
    "POLARIS_ROTATION_CREDIBLE_LEVEL", 0.90
)

# ADDITIVE improvement bar in DOLLARS. Default 0 is too eager and a large value
# starves rotation; 5.0 USD on a ~10k demo equity is a deliberately small,
# non-zero hysteresis floor so a swap must clear *some* real $ edge beyond cost.
ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD: Final[float] = _env_float(
    "POLARIS_ROTATION_IMPROVEMENT_MARGIN_USD", 5.0
)

# Victim age floor (anti-churn): a position younger than this is never rotated.
ROTATION_MIN_HOLD_SEC: Final[float] = _env_float(
    "POLARIS_ROTATION_MIN_HOLD_SEC", 300.0
)

# Structural cascade guard — at most one rotation per tick. Surfaced for the
# caller (which enforces it); env-tunable only to allow a future /debate.
ROTATION_MAX_PER_TICK: Final[int] = _env_int("POLARIS_ROTATION_MAX_PER_TICK", 1)


# --------------------------------------------------------------------------
# Injected value objects — every DB/venue value is passed in (no I/O here).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CellStats:
    """One cell's forward-edge inputs (injected from the learner posterior).

    ``mu``/``posterior_sd``/``alpha_n`` come from ``learner_posterior`` (the NIG
    marginal-t on expectancy); ``avg_pnl_r`` is the cell-matrix fallback used
    when no posterior is available. A cell is rankable only when it has a usable
    posterior AND ``n_samples >= POSTERIOR_MIN_N``.
    """

    mu: float | None
    posterior_sd: float | None
    alpha_n: float | None
    n_samples: int
    avg_pnl_r: float | None = None


@dataclass(frozen=True, slots=True)
class HeldPosition:
    """An open held position considered as a rotation VICTIM.

    ``fwd_R_held`` is the position's forward expected R (the cell posterior lower
    bound, or the ``avg_pnl_r`` fallback) computed by the CALLER and injected
    here. ``open_risk_pct_held`` is the capital scale (fraction of equity at
    risk). Winners (``exit_state`` in {protected, harvest}) are exempt.
    """

    position_id: str
    venue: str
    fwd_R_held: float | None
    open_risk_pct_held: float
    exit_state: str
    pnl_r: float
    held_sec: float


@dataclass(frozen=True, slots=True)
class RotationCandidate:
    """A capital-blocked NEW signal considered as a rotation WINNER.

    ``cell_stats`` is the candidate's OWN cell posterior (its forward edge);
    ``proposed_risk_pct`` is the capital scale the sizer asked for (NOT the
    edge); ``proposed_size_usd`` is the open-leg notional for the cost estimate.
    """

    candidate_id: str
    venue: str
    cell_stats: CellStats
    proposed_risk_pct: float
    proposed_size_usd: float


# --------------------------------------------------------------------------
# 1. forward_score — posterior lower credible bound (one estimator family).
# --------------------------------------------------------------------------


def _t_quantile_upper(level: float, df: float) -> float:
    """Upper-tail Student-t quantile: t such that CDF(t; df) == level.

    Inverted from the stdlib-only :func:`student_t_cdf` by bisection (no scipy).
    ``level`` in (0.5, 1). Returns >= 0. The bracket [0, hi] is grown until the
    CDF exceeds ``level`` so heavy tails (small df) are covered.
    """
    if not (0.5 < level < 1.0):
        # Degenerate request — no haircut.
        return 0.0
    if df <= 0.0:
        return 0.0
    lo, hi = 0.0, 4.0
    # Grow the upper bracket until CDF(hi) >= level (heavy-tail safety).
    for _ in range(40):
        if student_t_cdf(hi, df=df) >= level:
            break
        hi *= 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if student_t_cdf(mid, df=df) < level:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def forward_score(cell: CellStats, *, lower_credible_bound: float) -> float | None:
    """Forward edge in R = posterior LOWER credible bound, or None if ineligible.

    Returns ``mu - t_quantile(level, df) * posterior_sd`` when the cell has a
    usable NIG posterior AND ``n_samples >= POSTERIOR_MIN_N``. Otherwise the cell
    is **cold** and returns ``None`` — a cold cell is NOT given a near-zero prior
    score (which would let an unproven candidate out-rank a measured-negative
    held position). This is the single estimator family used everywhere.

    ``lower_credible_bound`` is the one-sided credible LEVEL (e.g. 0.90).
    """
    if (
        cell.mu is None
        or cell.posterior_sd is None
        or cell.alpha_n is None
        or cell.n_samples < POSTERIOR_MIN_N
    ):
        return None
    if cell.posterior_sd < 0.0 or not math.isfinite(cell.posterior_sd):
        return None
    df = 2.0 * cell.alpha_n
    z = _t_quantile_upper(lower_credible_bound, df)
    return cell.mu - z * cell.posterior_sd


def held_forward_score(
    cell: CellStats | None, *, lower_credible_bound: float
) -> float | None:
    """Forward R for a HELD victim — posterior LCB, else ``avg_pnl_r`` fallback.

    A held position is being considered for CLOSE (it is not receiving new
    capital), so a cold held is still rankable via its raw ``avg_pnl_r`` measured
    loss. Returns ``None`` only when neither a usable posterior nor an
    ``avg_pnl_r`` is available (truly unscored => excluded from selection).
    """
    if cell is not None:
        lcb = forward_score(cell, lower_credible_bound=lower_credible_bound)
        if lcb is not None:
            return lcb
        if cell.avg_pnl_r is not None and math.isfinite(cell.avg_pnl_r):
            return cell.avg_pnl_r
    return None


# --------------------------------------------------------------------------
# 2-3. expected dollar edge — both sides in $ (same unit, comparable).
# --------------------------------------------------------------------------


def expected_dollar_edge_new(
    *, fwd_mu_new: float, proposed_risk_pct: float, equity: float
) -> float:
    """E$_new = fwd_mu(new cell, R) * proposed_risk_pct(capital scale) * equity.

    ``proposed_risk_pct`` is the CAPITAL SCALE only (fraction of equity at risk),
    never the edge. ``fwd_mu_new`` is the new signal's OWN cell forward edge in R.
    """
    return fwd_mu_new * proposed_risk_pct * equity


def expected_dollar_edge_held(
    *, fwd_R_held: float, open_risk_pct_held: float, equity: float
) -> float:
    """E$_held = fwd_R(position, R) * open_risk_pct_held(capital scale) * equity."""
    return fwd_R_held * open_risk_pct_held * equity


# --------------------------------------------------------------------------
# 4. rotation_cost_usd — round-trip slip+fee in $ (mirrors cost_adjusted_pnl_r).
# --------------------------------------------------------------------------


def rotation_cost_usd(
    *,
    close_size_usd: float,
    open_size_usd: float,
    venue: str,
    close_fee_usd: float = 0.0,
    open_fee_usd: float = 0.0,
    close_slippage_bps: float | None = None,
    open_slippage_bps: float | None = None,
) -> float:
    """Round-trip rotation cost in USD: close leg (A) + open leg (B).

    Mirrors :func:`polaris.core.learners.posterior.cost_adjusted_pnl_r`:
      - Capital demo reports ``fee=0`` so a constant ``COST_BPS_CAPITAL`` per leg
        stands in (one taker leg on the close, one on the open),
      - OKX charges a real fee + slippage on each leg; an absent slippage (the
        caller has no fill yet — ``None``, the default) falls back to
        ``COST_BPS_OKX_FALLBACK``. A caller-supplied real ``0.0`` (a fill with
        honest zero slippage) is a value, not "absent", and is never overridden
        (ledger-reconcile forensic 2026-07-12, bug① symmetric fix).

    Pure; non-negative. This is the ``ROTATION_COST`` subtracted in the additive
    comparison — a cost SUBTRACTION, not a throttle.
    """
    if venue == "capital":
        close_leg = (COST_BPS_CAPITAL / 1e4) * abs(close_size_usd)
        open_leg = (COST_BPS_CAPITAL / 1e4) * abs(open_size_usd)
        return close_leg + open_leg
    close_bps = COST_BPS_OKX_FALLBACK if close_slippage_bps is None else close_slippage_bps
    open_bps = COST_BPS_OKX_FALLBACK if open_slippage_bps is None else open_slippage_bps
    close_slip = close_bps / 1e4 * abs(close_size_usd)
    open_slip = open_bps / 1e4 * abs(open_size_usd)
    return abs(close_fee_usd) + abs(open_fee_usd) + close_slip + open_slip


# --------------------------------------------------------------------------
# 5. should_rotate — ADDITIVE margin, subtract cost, strict win.
# --------------------------------------------------------------------------


def should_rotate(
    *, e_new: float, e_held: float, margin_usd: float, cost_usd: float
) -> bool:
    """rotate iff ``E$_new - E$_held > IMPROVEMENT_MARGIN + ROTATION_COST``.

    ADDITIVE in dollars — never multiplicative ``(1+x)``. The weak held side is
    negative-by-selection, so an additive bar keeps the threshold at
    ``e_held + margin + cost``; a multiplicative one would perversely lower the
    bar as the held loss deepens. Strict ``>`` so a tie does not churn capital.
    """
    return (e_new - e_held) > (margin_usd + cost_usd)


# --------------------------------------------------------------------------
# 6. select_weakest — lowest E$_held among eligible victims, winners exempt.
# --------------------------------------------------------------------------


def _victim_eligible(pos: HeldPosition) -> bool:
    """Eligible victim: open (not a protected/harvest winner) & measured loser &
    aged past the MIN_HOLD floor & has a finite forward R to rank on."""
    if pos.exit_state in _WINNER_EXIT_STATES:
        return False
    if pos.exit_state != _EXIT_STATE_OPEN:
        return False
    if pos.pnl_r >= 0.0:
        return False
    if pos.held_sec < ROTATION_MIN_HOLD_SEC:
        return False
    return pos.fwd_R_held is not None and math.isfinite(pos.fwd_R_held)


def select_weakest(
    held_positions: list[HeldPosition], *, equity: float
) -> HeldPosition | None:
    """Return the eligible held with the LOWEST expected dollar edge, or None.

    Eligibility (cross-verify): ``exit_state == 'open'`` (winners
    protected/harvest are exempt), ``pnl_r < 0`` (measured loser), and
    ``held_sec >= MIN_HOLD`` (anti-churn). A cold-start held is rankable via its
    injected ``fwd_R_held`` (avg_pnl_r fallback). Ties are broken
    deterministically by the lowest forward R then position id.
    """
    eligible = [p for p in held_positions if _victim_eligible(p)]
    if not eligible:
        return None

    def _key(p: HeldPosition) -> tuple[float, float, str]:
        assert p.fwd_R_held is not None  # guaranteed by _victim_eligible
        e_held = expected_dollar_edge_held(
            fwd_R_held=p.fwd_R_held,
            open_risk_pct_held=p.open_risk_pct_held,
            equity=equity,
        )
        return (e_held, p.fwd_R_held, p.position_id)

    return min(eligible, key=_key)


# --------------------------------------------------------------------------
# 7. choose_rotation — per-venue greedy single-weakest vs single-best.
# --------------------------------------------------------------------------


def _candidate_eligible_edge(cand: RotationCandidate) -> float | None:
    """The candidate's forward edge (R) if eligible, else None (cold => skip).

    A candidate receives NEW capital, so it must be measured-eligible: only a
    cell with a usable posterior at ``n >= POSTERIOR_MIN_N`` qualifies. A cold
    candidate returns None and can never out-rank a measured-negative held.
    """
    return forward_score(cand.cell_stats, lower_credible_bound=ROTATION_CREDIBLE_LEVEL)


def choose_rotation(
    *,
    blocked_candidates: list[RotationCandidate],
    held_positions: list[HeldPosition],
    equity: float,
    venue: str,
    improvement_margin_usd: float = ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD,
    rotation_cost_usd_value: float | None = None,
    reason_out: dict[str, object] | None = None,
) -> tuple[HeldPosition, RotationCandidate] | None:
    """Greedy single-best candidate vs single-weakest held, PER-VENUE only.

    Returns ``(victim, winner)`` to rotate, or ``None`` when no swap clears the
    additive ``improvement_margin_usd + rotation_cost`` bar. Per-venue isolation
    is an INVARIANT (OKX USDT and Capital margin are non-fungible): only
    candidates and held on ``venue`` participate. ``MAX_PER_TICK`` is enforced by
    the CALLER (this returns at most one swap).

    ``rotation_cost_usd_value`` may be injected (e.g. computed with live fee/
    slippage); when ``None`` a default per-name round-trip cost is derived from
    the candidate's ``proposed_size_usd`` and the victim's notional proxy.

    ``reason_out`` is an OPTIONAL observability out-param: when a ``dict`` is
    passed, the no-fire diagnosis is written into it (``reason`` plus the values
    available at the decision point: ``weakest_victim`` / ``best_cand_edge`` /
    ``e_held`` / ``e_new`` / ``margin`` / ``cost``). It NEVER affects the
    fire/no-fire decision or the return value — pure observability so a silent
    stall (capital-blocked + no swap) can be surfaced in the bot log. No-fire
    reasons: ``no_held_or_candidates_on_venue`` / ``no_eligible_victim`` /
    ``all_candidates_cold`` / ``below_margin``.
    """
    venue_holds = [p for p in held_positions if p.venue == venue]
    venue_cands = [c for c in blocked_candidates if c.venue == venue]
    if not venue_holds or not venue_cands:
        if reason_out is not None:
            reason_out["reason"] = "no_held_or_candidates_on_venue"
        return None

    victim = select_weakest(venue_holds, equity=equity)
    if victim is None:
        if reason_out is not None:
            reason_out["reason"] = "no_eligible_victim"
        return None
    assert victim.fwd_R_held is not None  # select_weakest guarantees finite R
    e_held = expected_dollar_edge_held(
        fwd_R_held=victim.fwd_R_held,
        open_risk_pct_held=victim.open_risk_pct_held,
        equity=equity,
    )

    # Best ELIGIBLE candidate by expected dollar edge (cold candidates skipped).
    best: RotationCandidate | None = None
    best_e_new = -math.inf
    best_edge = -math.inf
    for cand in venue_cands:
        edge = _candidate_eligible_edge(cand)
        if edge is None:
            continue
        e_new = expected_dollar_edge_new(
            fwd_mu_new=edge,
            proposed_risk_pct=cand.proposed_risk_pct,
            equity=equity,
        )
        if e_new > best_e_new or (e_new == best_e_new and edge > best_edge):
            best, best_e_new, best_edge = cand, e_new, edge
    if best is None:
        if reason_out is not None:
            reason_out["reason"] = "all_candidates_cold"
            reason_out["weakest_victim"] = victim.position_id
            reason_out["e_held"] = e_held
        return None

    cost = rotation_cost_usd_value
    if cost is None:
        # Per-name round-trip: close the victim's notional + open the candidate.
        # The victim notional is approximated by its risk capital (no size_usd on
        # HeldPosition); the open leg uses the candidate's proposed notional.
        cost = rotation_cost_usd(
            close_size_usd=victim.open_risk_pct_held * equity,
            open_size_usd=best.proposed_size_usd,
            venue=venue,
        )

    if not should_rotate(
        e_new=best_e_new, e_held=e_held, margin_usd=improvement_margin_usd, cost_usd=cost
    ):
        if reason_out is not None:
            reason_out["reason"] = "below_margin"
            reason_out["weakest_victim"] = victim.position_id
            reason_out["best_cand_edge"] = best_edge
            reason_out["e_held"] = e_held
            reason_out["e_new"] = best_e_new
            reason_out["margin"] = improvement_margin_usd
            reason_out["cost"] = cost
        return None
    return victim, best
