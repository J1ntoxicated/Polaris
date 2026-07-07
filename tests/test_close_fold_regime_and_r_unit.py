"""P1-10 + P2-13 (2026-07-02 audit) — learning-fold regime key + cost-R unit fix.

Two independent measurement/learning-only fixes to the close-path fan-out
(``_production_close_effects.py``). Sizing / the -1.0R rail / order path are
untouched (flow_not_block, aggressive bias preserved). DEMO/PAPER only.

FIX 1 (P1-10) — the cell-matrix fold key was the LIVE regime SSOT at CLOSE
time (``_safe_lookup_regime`` → ``regime_state``), not the regime the position
was ADMITTED under (``positions.entry_regime``, stamped once at open). A regime
flip between entry and close credited/blamed a DIFFERENT cell than the one that
actually sized the entry (2026-07-02 audit: 6 of 14 folds mismatched).
``_safe_lookup_entry_regime`` now reads ``positions.entry_regime`` first,
falling back to the live SSOT only when unavailable (no position_id / NULL —
legacy rows).

FIX 2 (P2-13, 2026-07-02) — the cost-in-R denominator (``_read_cost_inputs``'s
returned ``atr_usd``) was a 1m-bar-ATR-derived whole-position 1R value, a
COMPLETELY DIFFERENT unit than ``gross_pnl_r`` (which was then ``pnl_usd /
R_budget``, a fixed per-stream dollar constant — OKX $1,580 / Capital $1,020).
Subtracting an ATR-unit cost from an R_budget-unit gross pinned Capital
scratches to a uniform −0.30R and inflated OKX cost-in-R to −0.69..−1.79R (a
fee-rate/ATR-floor artefact, not real edge). FIX 2 made the denominator
``r_budget_for_venue`` — the SAME constant the gross was then denominated in.

FIX 3 (cross-ruler fee-drag, 2026-07-07 R-ruler unification review) — the
subsequent re-base moved ``gross_pnl_r`` onto per-trade ``positions.risk_usd``
(``realised_r``) WITHOUT moving this denominator, silently re-introducing the
FIX-2 unit mismatch in the opposite direction: gross on risk_usd, fee term
still on the (usually much larger) R_budget constant — shrinking the fee term
enough to flip 29/183 fee-eaten scratches to a ``won`` winner. The denominator
is now per-trade ``positions.risk_usd`` (the SAME ruler gross is denominated
in), falling back to ``r_budget_for_venue`` only when risk_usd is unavailable
(no seeded ``positions`` row in these fixtures below → the fallback path is
what tests 152-183 exercise).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import persist_fill
from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.scripts._production_close_effects import (
    _read_cost_inputs,
    _safe_lookup_entry_regime,
    compute_net_pnl_r,
    fold_close_slice,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


def _live_regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    """The LIVE SSOT regime at close-time — deliberately DIFFERENT from the
    entry_regime stamped on the position, so a test that folds the wrong key
    would visibly disagree with this stub's return."""
    return "chop"


def _trade(position_id: str | None) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0,
        notional_usd=1000.0, open_ts=NOW, position_id=position_id,
    )


def _seed_position(
    conn: sqlite3.Connection, *, position_id: str, entry_regime: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, entry_regime) "
        "VALUES (?, 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', 10.0, 'open', ?, 0, ?)",
        (position_id, NOW, entry_regime),
    )


# ---------------------------------------------------------------------------
# FIX 1 — _safe_lookup_entry_regime
# ---------------------------------------------------------------------------


def test_entry_regime_used_over_live_close_time_regime(memdb: sqlite3.Connection) -> None:
    """The position was ADMITTED under bull_trend; the live SSOT has since
    flipped to chop. The fold key must be bull_trend (the admitting cell), NOT
    the live-SSOT stub's chop."""
    _seed_position(memdb, position_id="pos-bull", entry_regime="bull_trend")
    trade = _trade("pos-bull")

    regime = _safe_lookup_entry_regime(_live_regime_stub, memdb, trade)

    assert regime == "bull_trend"
    assert regime != _live_regime_stub(memdb, trade.venue, trade.symbol)


def test_entry_regime_falls_back_to_live_when_null(memdb: sqlite3.Connection) -> None:
    """A legacy position with no entry_regime stamped (NULL) falls open to the
    live SSOT lookup — never blocks a fold, only re-keys it when unavailable."""
    _seed_position(memdb, position_id="pos-legacy", entry_regime=None)
    trade = _trade("pos-legacy")

    regime = _safe_lookup_entry_regime(_live_regime_stub, memdb, trade)

    assert regime == "chop"  # the live stub's value


def test_entry_regime_falls_back_to_live_when_no_position_id(
    memdb: sqlite3.Connection,
) -> None:
    """No position_id at all (legacy caller) → live SSOT lookup, unchanged from
    the pre-fix behaviour."""
    trade = _trade(None)

    regime = _safe_lookup_entry_regime(_live_regime_stub, memdb, trade)

    assert regime == "chop"


@pytest.mark.asyncio
async def test_fold_close_slice_cell_matrix_keyed_on_entry_regime(
    memdb: sqlite3.Connection,
) -> None:
    """END-TO-END: fold_close_slice's cell-matrix write lands under the
    ENTRY regime, not the live close-time regime — n_eff increments on the
    bull_trend cell (the one that admitted the trade), not chop."""
    _seed_position(memdb, position_id="pos-e2e", entry_regime="bull_trend")
    trade = _trade("pos-e2e")
    state = ProdLoopState()

    fold_close_slice(
        memdb, trade=trade, lookup_regime=_live_regime_stub,
        slice_pnl_r=0.5, slice_pnl_usd=50.0, now_ts=NOW, state=state,
    )

    bull_row = memdb.execute(
        "SELECT n_eff FROM cell_matrix_p0 WHERE exchange='okx' AND "
        "strategy='tsmom' AND ticker='SOL-USDT' AND regime='bull_trend'"
    ).fetchone()
    chop_row = memdb.execute(
        "SELECT n_eff FROM cell_matrix_p0 WHERE exchange='okx' AND "
        "strategy='tsmom' AND ticker='SOL-USDT' AND regime='chop'"
    ).fetchone()
    assert bull_row is not None
    assert bull_row[0] == pytest.approx(1.0)
    assert chop_row is None  # the close-time live regime never got the credit


# ---------------------------------------------------------------------------
# FIX 2 — cost-in-R unit (R_budget, not ATR-derived) — fallback path only.
# These fixtures never seed a ``positions`` row for posX/posCheap/posExp, so
# ``_position_risk_usd`` returns 0.0 and FIX 3's per-trade risk_usd is
# unavailable — the r_budget_for_venue FALLBACK is what these exercise.
# ---------------------------------------------------------------------------


def test_cost_r_denominator_matches_gross_r_budget_unit(
    memdb: sqlite3.Connection,
) -> None:
    """The denominator _read_cost_inputs returns for OKX is EXACTLY
    r_budget_for_venue('okx') — the same constant realised_r_stream (the
    caller's gross_pnl_r) already divides by."""
    trade = SimulatedTrade(
        signal_id="s1", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=60_000.0, notional_usd=600.0, open_ts=NOW,
        position_id="posX",
    )
    *_rest, r_budget_usd = _read_cost_inputs(memdb, trade)
    assert r_budget_usd == pytest.approx(r_budget_for_venue("okx"))


def test_cost_r_denominator_symbol_price_independent(memdb: sqlite3.Connection) -> None:
    """FIX 2 — the denominator is IDENTICAL for a $0.12 coin and a $60,000 coin
    on the SAME venue: unlike the old ATR-derived value, price no longer
    participates in the cost-in-R denominator at all."""
    cheap = SimulatedTrade(
        signal_id="s_cheap", venue="okx", symbol="ALGO-USDT", strategy_id="tsmom",
        side="long", entry_price=0.12, notional_usd=600.0, open_ts=NOW,
        position_id="posCheap",
    )
    expensive = SimulatedTrade(
        signal_id="s_exp", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=60_000.0, notional_usd=600.0, open_ts=NOW,
        position_id="posExp",
    )
    *_c, cheap_budget = _read_cost_inputs(memdb, cheap)
    *_e, exp_budget = _read_cost_inputs(memdb, expensive)
    assert cheap_budget == pytest.approx(exp_budget)


def test_compute_net_pnl_r_cost_fraction_is_small_and_unit_consistent(
    memdb: sqlite3.Connection,
) -> None:
    """A realistic OKX round-trip fee (~$1) against a real close, expressed as a
    fraction of gross_pnl_r's OWN R_budget unit, must be a SMALL correction
    (a few tenths of a percent of $1,580) — not a uniform ~0.3R-scale pin
    (the old ATR-unit-mismatch artefact the 2026-07-02 audit found on Capital
    scratches, and not the -0.69..-1.79R inflation found on OKX)."""
    inst = "okx:BTC-USDT"
    entry = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="buy",
        size_usd=600.0, fill_price=60_000.0, fee_usd=0.48, slippage_bps=0.0,
        ts_ms=NOW * 1000, order_id="e1", base_qty=0.01, quote_qty=600.0,
    )
    exit_fill = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="sell",
        size_usd=600.0, fill_price=60_060.0, fee_usd=0.6, slippage_bps=0.0,
        ts_ms=(NOW + 60) * 1000, order_id="x1", base_qty=0.01, quote_qty=600.6,
    )
    persist_fill(memdb, entry, is_close=False, contribution_id="posY")
    persist_fill(memdb, exit_fill, is_close=True, pnl_usd=0.6, contribution_id="posY")
    trade = SimulatedTrade(
        signal_id="sY", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=60_000.0, notional_usd=600.0, open_ts=NOW,
        position_id="posY",
    )
    gross_pnl_r = 0.10  # e.g. a $158 gross move on the $1,580 R_budget.
    _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        memdb, trade=trade, gross_pnl_r=gross_pnl_r, gross_pnl_usd=0.6,
    )
    cost_in_r = gross_pnl_r - pnl_r_net
    # ~$1.08 round-trip fee / $1,580 R_budget ≈ 0.00068 — a small fraction,
    # nowhere near the ~0.3R uniform pin or the -0.69..-1.79R inflation.
    assert 0.0 < cost_in_r < 0.01


# ---------------------------------------------------------------------------
# FIX 3 — cross-ruler fee-drag regression (the MAJOR this file's audit found).
# A seeded ``positions`` row WITH a per-trade risk_usd exercises the NEW-ruler
# path in ``_read_cost_inputs`` (not the FIX-2 fallback tests 152-197 above).
# ---------------------------------------------------------------------------


def _seed_position_with_risk_usd(
    conn: sqlite3.Connection, *, position_id: str, risk_usd: float,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, risk_usd) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', 0.01, 'open', ?, 0, ?)",
        (position_id, NOW, risk_usd),
    )


def test_fee_eaten_scratch_does_not_flip_to_won_cross_ruler_regression(
    memdb: sqlite3.Connection,
) -> None:
    """THE MAJOR (2026-07-07 R-ruler unification review): gross is slightly
    POSITIVE on the risk_usd ruler (+0.02R on a $50 stake == +$1 gross) but the
    REAL round-trip fee ($2.00) exceeds that gross dollar move. Net must be a
    LOSS on the SAME risk_usd ruler the gross used — cost_usd/risk_usd, NOT
    cost_usd/R_budget (R_budget=$1,580 for OKX would shrink the $2 fee to
    ~0.0013R, leaving net_r barely positive — a fee-eaten scratch silently
    folding as a ``won`` winner into the cell matrix/learners/NIG posterior,
    the exact 29/183-flip defect this test pins closed)."""
    risk_usd = 50.0
    _seed_position_with_risk_usd(memdb, position_id="posFee", risk_usd=risk_usd)
    inst = "okx:BTC-USDT"
    entry = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="buy",
        size_usd=600.0, fill_price=60_000.0, fee_usd=1.0, slippage_bps=0.0,
        ts_ms=NOW * 1000, order_id="e1", base_qty=0.01, quote_qty=600.0,
    )
    exit_fill = Fill(
        venue="okx", instrument_id=inst, strategy_id="tsmom", side="sell",
        size_usd=600.0, fill_price=60_001.0, fee_usd=1.0, slippage_bps=0.0,
        ts_ms=(NOW + 60) * 1000, order_id="x1", base_qty=0.01, quote_qty=600.01,
    )
    persist_fill(memdb, entry, is_close=False, contribution_id="posFee")
    persist_fill(memdb, exit_fill, is_close=True, pnl_usd=1.0, contribution_id="posFee")
    trade = SimulatedTrade(
        signal_id="sFee", venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        side="long", entry_price=60_000.0, notional_usd=600.0, open_ts=NOW,
        position_id="posFee",
    )
    gross_pnl_usd = 1.0
    gross_pnl_r = gross_pnl_usd / risk_usd  # == realised_r(1.0, 50.0) == 0.02

    # Sanity: the denominator _read_cost_inputs now returns for a position WITH
    # risk_usd is risk_usd itself, NOT the venue R_budget (the bug this pins).
    *_rest, cost_denom_usd = _read_cost_inputs(memdb, trade)
    assert cost_denom_usd == pytest.approx(risk_usd)
    assert cost_denom_usd != pytest.approx(r_budget_for_venue("okx"))

    _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
        memdb, trade=trade, gross_pnl_r=gross_pnl_r, gross_pnl_usd=gross_pnl_usd,
    )

    # The real $2.00 round-trip fee exceeds the $1.00 gross -> net must be a
    # LOSS, and the won verdict (net_r > 0) must NOT flip to a winner.
    assert pnl_r_net < 0.0
    won = pnl_r_net > 0.0
    assert won is False

    # Cross-check against the OLD (buggy) ruler to prove this is a REAL fix,
    # not a tautology: dividing the SAME $2 fee by the OLD R_budget constant
    # would have left net_r positive (the flip this defect caused).
    old_ruler_cost_in_r = 2.0 / r_budget_for_venue("okx")
    old_ruler_net_r = gross_pnl_r - old_ruler_cost_in_r
    assert old_ruler_net_r > 0.0, "fixture must reproduce the pre-fix flip"
    assert (old_ruler_net_r > 0.0) != (pnl_r_net > 0.0)
