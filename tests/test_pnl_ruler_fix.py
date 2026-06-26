"""Task #51 — pnl_r/mfe_r ruler honesty + mfe_r ATR-floor inflation fix.

DEMO/PAPER. Measurement-only telemetry — NOTHING here gates sizing / blocks an
entry / throttles. AGGRESSIVE bias preserved, flow_not_block intact.

Two confirmed bugs are pinned here:

BUG ① — the probe-outcome backfill stamped the per-stream LEDGER ``pnl_r``
(``realised_r_stream``) into ``probe_decisions.realized_pnl_r``, a column that is
the per-trade-ATR EXCURSION ruler (``unit_tag='excursion'``). The close path now
rescales the dollar pnl by the per-trade-ATR whole-position 1R
(``realised_r(pnl_usd, atr_risk_usd)``) so ``realized_pnl_r`` AND ``mfe_r_final``
share ONE ruler → ``giveback_r = max(0, mfe - realized)`` is a same-ruler
subtraction, not a meaningless cross-ruler one.

BUG ② — the excursion R-denominator relative floor (``_ATR_PCT_RELATIVE_FLOOR``)
was ``1e-4``, 5x looser than the entry anchor sane-band floor
(``ENTRY_ATR_PCT_MIN`` 5e-4 × the 2-ATR stop = ``1e-3``). A low anchor
(< 5e-4) therefore inflated mfe_r ~4x — and the exit FSM rungs read that same
inflated excursion, firing too early. The floor is now ``1e-3`` everywhere
(excursion / exit_params / recalc / close helper).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from polaris.core.live_recalc import excursion as excursion_mod
from polaris.core.live_recalc import exit_params
from polaris.core.live_recalc.excursion import compute_mfe_r
from polaris.core.metrics.risk_unit import realised_r, realised_r_stream
from polaris.core.probes.tuning_log import (
    backfill_probe_outcome,
    log_probe_decisions,
    open_probe_db,
)
from polaris.scripts._production_close_effects import _safe_backfill_probe_outcome
from polaris.scripts._smoke_fills import SimulatedTrade

# ---------------------------------------------------------------------------
# BUG ② — the relative ATR floor is 1e-3 (matches the anchor sane-band floor).
# ---------------------------------------------------------------------------


def test_relative_atr_floor_is_1e_3_both_modules() -> None:
    """Both the SSOT (exit_params) and the excursion self-def are 1e-3."""
    assert excursion_mod._ATR_PCT_RELATIVE_FLOOR == 1e-3
    assert exit_params._ATR_PCT_RELATIVE_FLOOR == 1e-3


def test_low_anchor_mfe_no_longer_inflated_eurusd_repro() -> None:
    """EURUSD pos_48156bebff repro: anchor 0.00011395 short.

    The per-unit ATR term (entry*anchor*2 = 0.000259) is BELOW the new
    1e-3 relative floor (entry*1e-3 = 0.001137) → the floor now governs, so the
    same favourable move reads ~0.145R instead of the old ~0.637R (ratio 4.39).
    """
    entry = 1.13651
    anchor = 0.00011395
    atr_term = entry * anchor * 2.0  # the per-unit ATR distance the close passes
    # Trough that produced mfe_r == 0.637 under the OLD 1e-4 floor (atr_term
    # governed there). Same trough, now floored at 1e-3 → ~0.145R.
    favourable = 0.637 * atr_term  # old: atr_old == atr_term (floor was below it)
    trough = entry - favourable
    mfe_new = compute_mfe_r(
        entry_price=entry, peak_price=entry, trough_price=trough,
        side="short", atr_usd=atr_term,
    )
    assert mfe_new == pytest.approx(0.145, abs=0.01)
    # The OLD floor (1e-4) would have left atr_term governing → ~0.637R.
    atr_old = max(atr_term, entry * 1e-4)
    mfe_old = favourable / atr_old
    assert mfe_old == pytest.approx(0.637, abs=0.01)
    assert mfe_old / mfe_new == pytest.approx(4.39, abs=0.05)


def test_high_anchor_mfe_unchanged_by_floor() -> None:
    """A normal anchor (atr_term well above 1e-3*entry) is untouched by the floor."""
    entry = 100.0
    atr_usd = 5.0  # 5% per-unit ATR — far above entry*1e-3 = 0.1.
    mfe = compute_mfe_r(
        entry_price=entry, peak_price=110.0, trough_price=100.0,
        side="long", atr_usd=atr_usd,
    )
    assert mfe == pytest.approx(2.0)  # 10 / 5 — floor never bites.


# ---------------------------------------------------------------------------
# BUG ① — probe backfill uses the per-trade-ATR EXCURSION ruler, not stream-R.
# ---------------------------------------------------------------------------


@dataclass
class _FakeState:
    """Minimal ProdLoopState stand-in carrying just the probe sidecar conn."""

    probe_conn: sqlite3.Connection


def _seed_open_decision(conn: sqlite3.Connection, position_id: str) -> None:
    """One observe-mode decision row with NULL outcome cols (backfill target)."""
    from polaris.core.probes import EngineDecision

    decision = EngineDecision(
        action="HOLD", composite_lean=0.0, applied=False,
    )
    log_probe_decisions(
        conn, ts=1, run_id="r1", position_id=position_id, mode="observe",
        decision=decision, pnl_r_at_decision=0.0, pnl_r_truth=0.0,
        mark_source="bar", mark_age_ms=0, exit_state="open",
    )


def test_backfill_stamps_excursion_ruler_not_stream_r(tmp_path: Path) -> None:
    """``realized_pnl_r`` = realised_r(pnl_usd, atr_risk_usd), NOT stream-R.

    A loss of -$30 on an OKX position with a whole-position 1R of $20 →
    excursion realized R = -1.5, while the per-stream ledger R (-30/1580) is
    ~-0.019. The two are wildly different rulers; the column must carry the
    EXCURSION one so it reconciles with mfe_r_final / giveback_r.
    """
    probe_db = open_probe_db(tmp_path / "probes.sqlite")
    try:
        _seed_open_decision(probe_db, "pos-ruler")
        pnl_usd = -30.0
        atr_risk_usd = 20.0
        mfe_r = 0.8  # excursion peak
        realized_atr_r = realised_r(pnl_usd=pnl_usd, risk_usd=atr_risk_usd)
        stream_r = realised_r_stream(pnl_usd=pnl_usd, venue="okx")
        assert realized_atr_r == pytest.approx(-1.5)
        # The stream ruler is a DIFFERENT (tiny) number — proving the mix is real.
        assert abs(stream_r) < 0.05
        assert realized_atr_r != pytest.approx(stream_r)

        state = _FakeState(probe_conn=probe_db)
        trade = SimulatedTrade(
            signal_id="s-ruler", venue="okx", symbol="ETH-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="pos-ruler",
        )
        _safe_backfill_probe_outcome(
            state=state, trade=trade, realized_atr_r=realized_atr_r,
            mfe_r=mfe_r, mae_r=-0.3, now_ts=int(time.time()) + 60,
        )
        row = probe_db.execute(
            "SELECT realized_pnl_r, mfe_r_final, giveback_r, unit_tag "
            "FROM probe_decisions WHERE position_id = 'pos-ruler'"
        ).fetchone()
        # realized_pnl_r is the EXCURSION ruler value, not the stream one.
        assert row[0] == pytest.approx(-1.5)
        assert row[1] == pytest.approx(mfe_r)
        # giveback = max(0, mfe - realized) — same-ruler (both excursion).
        assert row[2] == pytest.approx(max(0.0, mfe_r - realized_atr_r))
        assert row[3] == "excursion"
    finally:
        probe_db.close()


def test_giveback_is_same_ruler_subtraction(tmp_path: Path) -> None:
    """giveback_r reconciles: a winner that gave back excursion reads positive.

    mfe peaked +2.0R, realized +0.5R (same excursion ruler) → giveback +1.5R.
    Under the OLD mix (stream-R realized ~+0.0006) the subtraction was nonsense.
    """
    probe_db = open_probe_db(tmp_path / "probes2.sqlite")
    try:
        _seed_open_decision(probe_db, "pos-give")
        pnl_usd = 10.0
        atr_risk_usd = 20.0  # realized excursion R = +0.5
        realized_atr_r = realised_r(pnl_usd=pnl_usd, risk_usd=atr_risk_usd)
        assert realized_atr_r == pytest.approx(0.5)
        state = _FakeState(probe_conn=probe_db)
        trade = SimulatedTrade(
            signal_id="s-give", venue="okx", symbol="ETH-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="pos-give",
        )
        _safe_backfill_probe_outcome(
            state=state, trade=trade, realized_atr_r=realized_atr_r,
            mfe_r=2.0, mae_r=0.0, now_ts=int(time.time()) + 60,
        )
        row = probe_db.execute(
            "SELECT giveback_r FROM probe_decisions WHERE position_id = 'pos-give'"
        ).fetchone()
        assert row[0] == pytest.approx(1.5)  # 2.0 - 0.5, same excursion ruler.
    finally:
        probe_db.close()


def test_backfill_zero_risk_keeps_realized_zero(tmp_path: Path) -> None:
    """An unknowable per-trade 1R (atr_risk_usd=0) → realised_r returns 0.0.

    The close path passes atr_risk_usd=0 when entry_price/base_qty are
    unknowable; realised_r(0 risk) is 0.0 so the column stays a finite 0, never
    an exploded R from a divide-by-zero.
    """
    probe_db = open_probe_db(tmp_path / "probes3.sqlite")
    try:
        _seed_open_decision(probe_db, "pos-zero")
        realized_atr_r = realised_r(pnl_usd=-30.0, risk_usd=0.0)
        assert realized_atr_r == 0.0
        state = _FakeState(probe_conn=probe_db)
        trade = SimulatedTrade(
            signal_id="s-zero", venue="okx", symbol="ETH-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="pos-zero",
        )
        _safe_backfill_probe_outcome(
            state=state, trade=trade, realized_atr_r=realized_atr_r,
            mfe_r=0.0, mae_r=0.0, now_ts=int(time.time()) + 60,
        )
        row = probe_db.execute(
            "SELECT realized_pnl_r, giveback_r FROM probe_decisions "
            "WHERE position_id = 'pos-zero'"
        ).fetchone()
        assert row[0] == 0.0
        assert row[1] == 0.0
    finally:
        # silence unused-import lint for backfill_probe_outcome (used indirectly).
        _ = backfill_probe_outcome
        probe_db.close()
