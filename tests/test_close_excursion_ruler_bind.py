"""[[exit_peak_lock_bind_2026-07-10]] v2 — close-time excursion ruler SSOT bind.

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block — MEASUREMENT-only fix
(the R-unit ruler ``_close_excursion_r`` computes ``mfe_r``/``mae_r`` against);
no throttle / entry-block / size-cut. Sizing (T4 / the 9-stack) and the G6
-1.0R rail never read ``positions.mfe_r``/``mae_r`` — untouched here.

REJECTION THIS FIXES (v1 review, ``fix/exit-peak-lock-bind``): v1 threaded
``_stop_atr_mult_for_strategy`` into the ENTRY stamp only
(``positions.risk_usd``, the realised-``pnl_r`` denominator) — but
``_close_excursion_r`` (``_production_close_helpers.py``, the close-time
``mfe_r``/``mae_r`` backfill) kept its OWN hardcoded ``* 2.0`` on both the
entry-anchor branch and the recent-bars fallback branch. The two rulers are
coupled by design (documented in the module docstrings): fixing only one moves
the ruler MISMATCH into the training columns (``mfe_r``/``mae_r``/``pnl_r``)
instead of removing it. This file guards the SSOT fix — BOTH branches of
``_close_excursion_r`` now resolve their ATR multiplier via the SAME
``_stop_atr_mult_for_strategy`` the entry stamp (v1) and the live exit engine
already use.

Guards:
1. ``test_close_excursion_ruler_matches_entry_stamp_resolver`` — for a
   fee-floor-WIDENED strategy/atr_pct (the scenario the bug bit on), the
   close-time ``atr_risk_usd`` (this file's dollar 1R) and the entry-stamp
   ``risk_usd`` (``risk_usd_at_entry`` with the SAME resolved multiplier)
   agree exactly — one ruler, not two.
2. ``test_close_excursion_no_override_stays_byte_identical`` /
   ``test_close_excursion_bars_fallback_no_override_stays_byte_identical`` —
   a strategy/atr_pct where the fee floor never binds (unregistered id or
   comfortably-wide ATR) resolves the flat SSOT ``STOP_ATR_MULT`` (2.0)
   unchanged, so both the entry-anchor and the bars-fallback branch stay
   byte-identical to the pre-fix hardcoded ``* 2.0`` — the fix is a pure
   widen, never a behaviour change for the common case.
3. ``test_trend_winner_peak_lock_capture_ratio_matches_true_fraction`` — a
   TREND-bucket winner (``ema_crossover``) whose peak-lock floor locked the
   stop at ``entry + BAR_TREND_PEAK_LOCK_FRAC * (peak - entry)`` (0.65, the
   let-winners-run floor's own fraction) reports a realised-R / mfe_r capture
   ratio that matches ``BAR_TREND_PEAK_LOCK_FRAC`` EXACTLY once both rulers
   share the same multiplier — and demonstrably does NOT match it (materially
   understates the true capture) when the close-side ruler is still the old
   flat 2.0 while the entry-side ruler is v1's widened value. This is the
   direction of the fix: peak-lock capture reads TRUE, not distorted.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.metrics.risk_unit import STOP_ATR_MULT, realised_r, risk_usd_at_entry
from polaris.scripts._production_close_helpers import _close_excursion_r
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.exit_strategy_config import (
    BAR_TREND_PEAK_LOCK_FRAC,
    _stop_atr_mult_for_strategy,
)
from polaris.storage.schema import init_db

_STRATEGY_ID = "ema_crossover"  # okx, TREND bucket (spot_ema_trend), registered
_TIGHT_ATR_PCT = 0.002  # fee floor binds -> resolved mult 3.0 (> STOP_ATR_MULT)
_WIDE_ATR_PCT = 0.04  # fee floor never binds -> resolved mult stays 2.0 (no-op)


def _memdb() -> sqlite3.Connection:
    return init_db(":memory:")


def _seed_position_and_fill(
    conn: sqlite3.Connection, *, position_id: str, entry_price: float,
    entry_atr_pct: float | None, base_qty: float,
    peak: float | None = None, trough: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " peak_price, trough_price, entry_atr_pct) "
        "VALUES (?, 'okx', 'BTC-USDT', ?, ?, ?, 'long', ?, 'open', ?, ?, ?, ?)",
        (
            position_id, _STRATEGY_ID, _STRATEGY_ID, _STRATEGY_ID, base_qty,
            int(time.time()), peak, trough, entry_atr_pct,
        ),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:BTC-USDT', ?, 'buy', "
        "        ?, ?, 0.1, 1.0, ?, 'o1', ?, 0.0, 0, ?, ?, 'filled')",
        (
            f"f_{position_id}", _STRATEGY_ID, entry_price * base_qty, entry_price,
            int(time.time() * 1000), position_id, base_qty, entry_price * base_qty,
        ),
    )
    conn.commit()


def _seed_bars_for_atr_pct(
    conn: sqlite3.Connection, *, atr_pct: float, close: float = 100.0, n: int = 14,
) -> None:
    """Seed ``n`` 1m bars whose ``(high-low)/close`` mean is exactly ``atr_pct``
    (drives ``_atr_pct_from_bars`` -> the bars-fallback branch of
    ``_close_excursion_r``, entry_atr_pct left NULL)."""
    band = (close * atr_pct) / 2.0
    now = int(time.time())
    for i in range(n):
        ts = now - (n - 1 - i) * 60
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
            " symbol, bar_interval, ts, open, high, low, close, volume) VALUES "
            " ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            " ?, ?, ?, ?, 0.0)",
            (ts, close, close + band, close - band, close),
        )
    conn.commit()


def test_close_excursion_ruler_matches_entry_stamp_resolver() -> None:
    """Fee-floor-widened case: close-side ``atr_risk_usd`` == entry-side
    ``risk_usd`` (same resolver, same inputs) — the SSOT bind."""
    conn = _memdb()
    try:
        resolved_mult = _stop_atr_mult_for_strategy(_STRATEGY_ID, atr_pct=_TIGHT_ATR_PCT)
        assert resolved_mult > STOP_ATR_MULT  # scenario actually exercises the widen

        _seed_position_and_fill(
            conn, position_id="p_tight", entry_price=100.0,
            entry_atr_pct=_TIGHT_ATR_PCT, base_qty=2.0,
            peak=130.0, trough=100.0,
        )
        trade = SimulatedTrade(
            signal_id="s1", venue="okx", symbol="BTC-USDT",
            strategy_id=_STRATEGY_ID, side="long", entry_price=100.0,
            notional_usd=200.0, open_ts=int(time.time()), position_id="p_tight",
        )
        _mfe_r, _mae_r, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=119.5,
        )
        entry_risk_usd = risk_usd_at_entry(
            entry_price=100.0, entry_atr_pct=_TIGHT_ATR_PCT, base_qty=2.0,
            stop_atr_mult=resolved_mult,
        )
        assert atr_risk_usd == pytest.approx(entry_risk_usd)
        assert atr_risk_usd == pytest.approx(1.2)  # 100*0.002*3.0*2.0, floors clear
    finally:
        conn.close()


def test_close_excursion_no_override_stays_byte_identical() -> None:
    """Wide-ATR entry-anchor branch: fee floor never binds -> resolved mult
    stays the flat SSOT 2.0 -> byte-identical to the pre-fix hardcoded
    ``* 2.0`` formula."""
    conn = _memdb()
    try:
        resolved_mult = _stop_atr_mult_for_strategy(_STRATEGY_ID, atr_pct=_WIDE_ATR_PCT)
        assert resolved_mult == STOP_ATR_MULT

        _seed_position_and_fill(
            conn, position_id="p_wide", entry_price=100.0,
            entry_atr_pct=_WIDE_ATR_PCT, base_qty=0.8,
            peak=110.0, trough=100.0,
        )
        trade = SimulatedTrade(
            signal_id="s2", venue="okx", symbol="BTC-USDT",
            strategy_id=_STRATEGY_ID, side="long", entry_price=100.0,
            notional_usd=80.0, open_ts=int(time.time()), position_id="p_wide",
        )
        _mfe_r, _mae_r, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=105.0,
        )
        old_atr_usd = max(100.0 * _WIDE_ATR_PCT * 2.0, 100.0 * 1e-3)  # pre-fix formula
        old_atr_risk_usd = old_atr_usd * 0.8
        assert atr_risk_usd == pytest.approx(old_atr_risk_usd)
    finally:
        conn.close()


def test_close_excursion_bars_fallback_no_override_stays_byte_identical() -> None:
    """Same no-op guarantee for the RECENT-BARS fallback branch (no
    ``entry_atr_pct`` anchor) — the second hardcoded ``* 2.0`` site."""
    conn = _memdb()
    try:
        _seed_position_and_fill(
            conn, position_id="p_bars", entry_price=100.0,
            entry_atr_pct=None, base_qty=0.8, peak=110.0, trough=100.0,
        )
        _seed_bars_for_atr_pct(conn, atr_pct=_WIDE_ATR_PCT)
        trade = SimulatedTrade(
            signal_id="s3", venue="okx", symbol="BTC-USDT",
            strategy_id=_STRATEGY_ID, side="long", entry_price=100.0,
            notional_usd=80.0, open_ts=int(time.time()), position_id="p_bars",
        )
        _mfe_r, _mae_r, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=105.0,
        )
        old_atr_usd = max(100.0 * _WIDE_ATR_PCT * 2.0, 1e-6)  # pre-fix bars formula
        old_atr_risk_usd = old_atr_usd * 0.8
        assert atr_risk_usd == pytest.approx(old_atr_risk_usd)
    finally:
        conn.close()


def test_close_excursion_bars_fallback_widens_with_resolver() -> None:
    """The bars-fallback branch ALSO threads the resolved (widened) mult —
    not just the entry-anchor branch."""
    conn = _memdb()
    try:
        resolved_mult = _stop_atr_mult_for_strategy(_STRATEGY_ID, atr_pct=_TIGHT_ATR_PCT)
        assert resolved_mult > STOP_ATR_MULT

        _seed_position_and_fill(
            conn, position_id="p_bars_tight", entry_price=100.0,
            entry_atr_pct=None, base_qty=2.0, peak=130.0, trough=100.0,
        )
        _seed_bars_for_atr_pct(conn, atr_pct=_TIGHT_ATR_PCT)
        trade = SimulatedTrade(
            signal_id="s4", venue="okx", symbol="BTC-USDT",
            strategy_id=_STRATEGY_ID, side="long", entry_price=100.0,
            notional_usd=200.0, open_ts=int(time.time()), position_id="p_bars_tight",
        )
        _mfe_r, _mae_r, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=119.5,
        )
        expected = max(100.0 * _TIGHT_ATR_PCT * resolved_mult, 1e-6) * 2.0
        assert atr_risk_usd == pytest.approx(expected)
        # REGRESSION: pre-fix flat-2.0 would have been materially smaller.
        old = max(100.0 * _TIGHT_ATR_PCT * 2.0, 1e-6) * 2.0
        assert atr_risk_usd > old
    finally:
        conn.close()


def test_trend_winner_peak_lock_capture_ratio_matches_true_fraction() -> None:
    """A TREND winner whose peak-lock floor locked in EXACTLY
    ``BAR_TREND_PEAK_LOCK_FRAC`` (0.65) of its peak PRICE move reports a
    realised-R / mfe_r capture ratio of 0.65 once both rulers share the SAME
    resolved multiplier (this fix) — and a materially WRONG ratio when the
    close-side ruler is still the pre-fix flat 2.0 (the exact regression the
    v1-only fix left behind, per the v1 REJECT)."""
    conn = _memdb()
    try:
        entry_price = 100.0
        peak_price = 130.0  # peak move = 30 price units
        peak_move = peak_price - entry_price
        captured_move = BAR_TREND_PEAK_LOCK_FRAC * peak_move  # 19.5
        exit_price = entry_price + captured_move  # 119.5 — the locked stop
        base_qty = 2.0

        resolved_mult = _stop_atr_mult_for_strategy(_STRATEGY_ID, atr_pct=_TIGHT_ATR_PCT)
        _seed_position_and_fill(
            conn, position_id="p_winner", entry_price=entry_price,
            entry_atr_pct=_TIGHT_ATR_PCT, base_qty=base_qty,
            peak=peak_price, trough=entry_price,
        )
        trade = SimulatedTrade(
            signal_id="s5", venue="okx", symbol="BTC-USDT",
            strategy_id=_STRATEGY_ID, side="long", entry_price=entry_price,
            notional_usd=entry_price * base_qty, open_ts=int(time.time()),
            position_id="p_winner",
        )
        mfe_r_new, _mae_r, _atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=exit_price,
        )
        risk_usd = risk_usd_at_entry(
            entry_price=entry_price, entry_atr_pct=_TIGHT_ATR_PCT,
            base_qty=base_qty, stop_atr_mult=resolved_mult,
        )
        pnl_usd = (exit_price - entry_price) * base_qty
        pnl_r = realised_r(pnl_usd=pnl_usd, risk_usd=risk_usd)

        new_capture_ratio = pnl_r / mfe_r_new
        assert new_capture_ratio == pytest.approx(BAR_TREND_PEAK_LOCK_FRAC, abs=1e-6)

        # The v1-only (pre-v2) regression: close ruler still flat 2.0.
        old_atr_usd = max(entry_price * _TIGHT_ATR_PCT * 2.0, entry_price * 1e-3)
        mfe_r_old = peak_move / old_atr_usd
        old_capture_ratio = pnl_r / mfe_r_old
        assert abs(old_capture_ratio - BAR_TREND_PEAK_LOCK_FRAC) > 0.15
    finally:
        conn.close()
