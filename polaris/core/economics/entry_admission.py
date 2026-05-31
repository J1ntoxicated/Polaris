"""Component C (SHADOW) — edge-first entry admission, pure decision rule.

Go-live-confidence redesign (``.claude/plans/okx_live_confidence_redesign_2026-05-31.md``
§C): trade only where the EXPECTED edge net of the REAL round-trip fee is
positive. Two cell-derived signals drive the would-suppress decision:

(a) REGIME-CONDITIONED — the live ``(strategy x regime)`` cell expectancy
    (``cell_matrix_p0.avg_pnl_r`` in R). tsmom is +0.7~1.1R in bull/trend but
    -0.5~-0.9R in chop/crisis; admitting a cell whose realised expectancy is
    already below the real round-trip cost is paying fees to lose.
(b) COST-AWARE — the cell's EXPECTED favourable move (in R) vs the REAL
    round-trip cost (in R). When the move you can expect to capture does not
    even clear the cost of getting in and out, the trade is -EV by construction.

SHADOW-FIRST (behavior 0): this module ONLY computes ``admit`` /
``would_suppress`` + a reason. It NEVER blocks the pipeline. The wiring logs the
decision; the live admit/skip is unchanged. A cutover happens only after the
shadow log proves it would_suppress *only* proven -EV warm setups.

flow_not_block (STRICT, non-negotiable — aggressive bias preserved):

- A COLD cell (``cell_n_eff < CELL_POOL_MIN_N_EFF``) is ALWAYS admitted
  ("모호하면 통과"). Thin / ambiguous / insufficient evidence = ADMIT. A cold cell
  can NEVER be would_suppress, regardless of expectancy, cost or expected move.
- Only a WARM cell (``cell_n_eff >= CELL_POOL_MIN_N_EFF``) — i.e. PROVEN sample —
  may be would_suppress, and ONLY when its evidence is net-negative:
  regime-conditioned expectancy net of real cost < 0, OR the expected move does
  not clear the real round-trip cost. Otherwise admit.
- ``net_edge`` (the self-negating ``net_edge_r`` placeholder) is NEVER consulted
  — it is not even an input to this function.

This is a PURE function: no DB, no network, no global state. The caller derives
the cell stats + the cost/move R-projections (see ``entry_admission_shadow`` for
the proxy derivation) and passes them in.

``expected_move_r`` proxy (documented per spec): the favourable move (in R) the
cell can be expected to capture. The shadow wiring derives it from the cell's
hit-rate-weighted historical win magnitude (``wins_eff / n_eff`` scaled by the
positive part of ``avg_pnl_r``) as a conservative MFE stand-in — the cell does
not persist per-trade MFE in P0. A cold cell never reaches the cost-aware test
(Rule 1 admits it first), so the proxy only ever feeds a warm decision.

``real_round_trip_cost_r`` (documented per spec): two real fee legs
(``fees.real_fee_usd`` entry + exit) divided by the SAME ``atr_usd`` that defines
the cell-matrix R unit (``atr_usd = entry_price * atr_pct * 2.0``; per-trade
``pnl_r = pnl_abs / atr_usd`` in ``_production_close.py``). So the cost is ATR-R
(``cost_usd / atr_usd``) — the exact convention of ``_production_close_effects``'s
``pnl_r_net = pnl_r - cost_usd / atr_usd`` — and shares ONE R unit with the
regime-conditioned ``cell_avg_pnl_r`` and the cost-aware ``expected_move_r``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from polaris.core.pipeline.payload_builder import CELL_POOL_MIN_N_EFF

__all__ = [
    "CELL_POOL_MIN_N_EFF",
    "EntryAdmissionDecision",
    "entry_admission_decision",
    "expected_move_r_from_cell",
    "real_round_trip_cost_r_from_usd",
]

# Reason tags (stable strings for the shadow-log query layer).
REASON_COLD_ADMIT: Final[str] = "cold_admit"
REASON_WARM_NEGATIVE_EXPECTANCY: Final[str] = "warm_negative_expectancy_net_cost"
REASON_WARM_MOVE_BELOW_COST: Final[str] = "warm_expected_move_below_cost"
REASON_WARM_ADMIT: Final[str] = "warm_positive_edge_admit"

# Basis tags — which signal drove the decision (regime-conditioned vs cost-aware
# vs the cold pass-through). Surfaced so the acceptance analysis can split the
# would-suppress population by the signal that flagged it.
BASIS_COLD: Final[str] = "cold_passthrough"
BASIS_REGIME_CONDITIONED: Final[str] = "regime_conditioned_expectancy"
BASIS_COST_AWARE: Final[str] = "cost_aware_move_vs_cost"
BASIS_WARM_ADMIT: Final[str] = "warm_edge_positive"


@dataclass(frozen=True, slots=True)
class EntryAdmissionDecision:
    """Result of the edge-first admission rule (SHADOW — never blocks in P0).

    ``admit`` is what the live pipeline WOULD do under a future cutover; in
    shadow it is logged only. ``would_suppress`` is the inverse flag the
    acceptance gate counts (True only for a warm proven-negative cell). ``reason``
    is the stable rule tag; ``basis`` names the signal (regime-conditioned vs
    cost-aware) that drove a would_suppress, or the cold pass-through.
    """

    admit: bool
    would_suppress: bool
    reason: str
    basis: str


def expected_move_r_from_cell(
    *, n_eff: float, wins_eff: float, avg_pnl_r: float
) -> float:
    """Cell-derived EXPECTED favourable move in R (conservative MFE proxy).

    P0 cells do not persist per-trade MFE, so the favourable-move proxy is the
    cell hit-rate (``wins_eff / n_eff``) times the positive part of the realised
    expectancy magnitude — the average up-move the cell has actually delivered.
    It derives from ``avg_pnl_r`` (itself ATR-R: per-trade ``pnl_abs / atr_usd``),
    so the returned move is ALSO ATR-R — the same unit as ``cell_avg_pnl_r`` and
    ``real_round_trip_cost_r``, which is what makes the cost-aware comparison
    (move vs cost) unit-consistent.
    Non-positive / non-finite inputs collapse to 0.0 (no expected move), which a
    warm cell reads as "move does not clear cost" (cost-aware would_suppress).

    Pure helper exposed so the shadow wiring and tests share ONE proxy
    definition. A cold cell never reaches the cost-aware comparison (Rule 1
    admits it first), so this only ever informs a warm decision.
    """
    if n_eff <= 0.0:
        return 0.0
    hit_rate = max(0.0, min(1.0, wins_eff / n_eff))
    win_magnitude = max(0.0, avg_pnl_r)
    return hit_rate * win_magnitude


def real_round_trip_cost_r_from_usd(
    *, real_fee_one_leg_usd: float, atr_usd: float
) -> float:
    """Two real fee legs (entry + exit) expressed in ATR-R.

    ``real_fee_one_leg_usd`` is ``fees.real_fee_usd(venue, notional)`` for a
    single leg; a round trip is two legs. The round-trip $ cost is divided by the
    SAME ``atr_usd`` (= ``entry_price * atr_pct * 2.0``) that defines the
    cell-matrix R unit — per-trade ``pnl_r = pnl_abs / atr_usd`` — so this returns
    ATR-R (``cost_usd / atr_usd``), the exact convention of
    ``_production_close_effects.py``'s ``pnl_r_net = pnl_r - cost_usd / atr_usd``.
    Cost and ``cell_avg_pnl_r`` therefore share ONE unit (no $-basis mismatch).

    A non-positive (degenerate) ``atr_usd`` guards against a divide-by-zero and
    returns 0.0 — no cost rather than infinite cost, so a degenerate ATR can NEVER
    manufacture a would_suppress (fail-open to admit, flow_not_block).
    """
    if atr_usd <= 0.0:
        return 0.0
    round_trip_usd = 2.0 * abs(real_fee_one_leg_usd)
    return round_trip_usd / atr_usd


def entry_admission_decision(
    *,
    cell_n_eff: float,
    cell_avg_pnl_r: float,
    regime: str,
    expected_move_r: float,
    real_round_trip_cost_r: float,
) -> EntryAdmissionDecision:
    """Edge-first admission rule — flow_not_block STRICT (cold = always admit).

    Rules (in order):
    1. COLD cell (``cell_n_eff < CELL_POOL_MIN_N_EFF``) → admit, never suppress
       ("모호하면 통과"). Thin / ambiguous evidence is ALWAYS admitted; no cost or
       expectancy test runs on a cold cell.
    2. WARM cell, REGIME-CONDITIONED: realised expectancy net of the real
       round-trip cost < 0 → would_suppress. (tsmom -0.5R in chop, minus fees,
       is paying to lose.)
    3. WARM cell, COST-AWARE: expected favourable move <= real round-trip cost →
       would_suppress. (The move you can expect to capture does not clear the
       cost of the round trip.)
    4. Otherwise (warm, positive edge net of cost AND move clears cost) → admit.

    UNIT INVARIANT: ``cell_avg_pnl_r``, ``expected_move_r`` and
    ``real_round_trip_cost_r`` are ALL ATR-R (each divides by the same
    ``atr_usd = entry_price * atr_pct * 2.0``), so Rule 2's subtraction and
    Rule 3's comparison are unit-consistent (no $-basis cancellation).

    ``regime`` is carried for the shadow row (the cell is already
    regime-conditioned upstream — the cell key includes regime); it does not
    branch the rule. ``net_edge`` is NEVER consulted (not even a parameter).
    """
    warm = cell_n_eff >= CELL_POOL_MIN_N_EFF

    # Rule 1 — cold cell is always admitted (flow_not_block: 모호하면 통과).
    if not warm:
        return EntryAdmissionDecision(
            admit=True,
            would_suppress=False,
            reason=REASON_COLD_ADMIT,
            basis=BASIS_COLD,
        )

    # Rule 2 — regime-conditioned: warm realised expectancy net of real cost < 0.
    if cell_avg_pnl_r - real_round_trip_cost_r < 0.0:
        return EntryAdmissionDecision(
            admit=False,
            would_suppress=True,
            reason=REASON_WARM_NEGATIVE_EXPECTANCY,
            basis=BASIS_REGIME_CONDITIONED,
        )

    # Rule 3 — cost-aware: warm expected move does not clear the real round trip.
    if expected_move_r <= real_round_trip_cost_r:
        return EntryAdmissionDecision(
            admit=False,
            would_suppress=True,
            reason=REASON_WARM_MOVE_BELOW_COST,
            basis=BASIS_COST_AWARE,
        )

    # Rule 4 — warm, positive edge net of cost AND move clears cost → admit.
    return EntryAdmissionDecision(
        admit=True,
        would_suppress=False,
        reason=REASON_WARM_ADMIT,
        basis=BASIS_WARM_ADMIT,
    )
