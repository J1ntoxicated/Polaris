"""[[exit_peak_lock_bind_2026-07-10]] — risk_usd / stop_atr_mult ruler bind fix.

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block — this is a MEASUREMENT
fix (the R-unit ruler ``positions.risk_usd`` is stamped against), never a
throttle / entry-block / size-cut. Sizing (T4 / the 9-stack) never reads
``risk_usd`` (proven in ``test_pnl_r_rebase_risk_usd.py``); the G6 -1.0R rail
reads ``_stop_atr_mult_for_strategy`` directly (``compute_unrealized_pnl_r``),
never ``risk_usd`` — both untouched here.

ROOT CAUSE (confirmed via code read + live DB cross-check,
data/polaris_live.sqlite ro): the live precise-exit engine denominates
``mfe_r`` / the peak-lock floor's rungs against
``_stop_atr_mult_for_strategy``'s FEE_FLOOR_K-widened multiplier
([[trade_mess_full_audit_2026-07-02]], up to ~8-18x observed live on tight-ATR
Capital FX legs — GBPUSD stamped 8.18x, USDJPY 2.40x). ``risk_usd_at_entry()``
— which stamps ``positions.risk_usd``, the realised-R denominator since the
2026-07-07 re-base (``real_pnl_r_from_fills`` -> ``realised_r(pnl_usd,
risk_usd)``) — was NEVER updated to thread that SAME resolved multiplier; it
silently defaulted to the flat SSOT ``STOP_ATR_MULT=2.0``. A winner whose
peak-fraction floor correctly locked ~65% of its peak PRICE move (cross-checked
against live GBPUSD fills: stop_price sat almost exactly
``entry + 0.65*(peak-entry)``) got its REPORTED realised R computed on a
~4x-narrower ruler than its own "MFE 3.2R" telemetry — reading as a ~10-15%
capture instead of the true ~65%. This is a param-plumbing/bind gap, NOT a
peak-fraction-floor logic defect (``exit_engine.py``'s floor math is correct;
``MfeProtectSchedule.peak_lock_arm_r`` IS >0 for every TREND-bucket bar
strategy — see ``test_letrun_peak_fraction_wiring.py``).

FIX: ``_production_pipeline.py``'s entry-stamp call now threads
``stop_atr_mult=_stop_atr_mult_for_strategy(sig.strategy_id, atr_pct=...)`` —
the SAME resolver the exit engine (``run_precise_exit``) and the T4 sizer
(``build_sizer_payload``) already use. WIDENS ONLY — a tight-ATR instrument's
``risk_usd`` grows (never shrinks), so realised R only gets SMALLER-magnitude
(fewer, not more, R units per dollar) — never inflates a loss.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.core.metrics.risk_unit import STOP_ATR_MULT, risk_usd_at_entry
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.strategies.base import RawSignal

NOW = 1_780_000_000
IID = "okx:BTC-USDT"


def _seed_bars(
    conn: sqlite3.Connection, *, interval: str, n: int, band: float,
    close: float = 100.0, end_ts: int = NOW,
) -> None:
    sec = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "1D": 86400}[interval]
    for i in range(n):
        ts = end_ts - (n - 1 - i) * sec
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', 'okx', 'BTC-USDT', ?, ?, ?, ?, ?, ?, "
            " 100.0, 1e4, 1, ?, ?, ?, 1.0, 'rest')",
            (IID, interval, ts, close, close + band, close - band, close,
             close, close, close),
        )


def _entry_fill(
    conn: sqlite3.Connection, position_id: str | None,
) -> tuple[float, float]:
    row = conn.execute(
        "SELECT fill_price, base_qty FROM fills "
        "WHERE contribution_id = ? AND is_close = 0", (position_id,),
    ).fetchone()
    return float(row[0]), float(row[1])


@pytest.mark.asyncio
async def test_open_path_stamps_risk_usd_with_fee_floor_widened_ruler(
    memdb: sqlite3.Connection,
) -> None:
    """A tight-ATR 1H instrument widens ``stop_atr_mult`` past the SSOT 2.0
    (FEE_FLOOR_K / OKX real-taker floor) — ``positions.risk_usd`` must reflect
    THAT widened multiplier, matching what the live exit engine denominates
    mfe_r / the peak-lock floor against for the SAME position."""
    reset_process_fence()
    # band=0.1 on close=100 -> atr_pct=0.002, well under the ~0.3% threshold
    # (FEE_FLOOR_K=3.0 * 2*OKX-real-taker-10bps / 2.0) that triggers the fee
    # floor to exceed the SSOT base 2.0x (resolves ~3.0x here). notional_usd
    # =200 (vs the 80 other fixtures use) so the ATR-derived risk term for
    # BOTH the flat and widened multiplier clears ``RISK_USD_ABS_FLOOR``
    # ($0.50) — otherwise the tiny-notional floor masks the multiplier
    # difference entirely (both collapse to the SAME floored $0.50).
    _seed_bars(memdb, interval="1H", n=20, band=0.1, close=100.0)
    sig = RawSignal(
        signal_id="sig-riskusd", strategy_id="ema_crossover", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=200.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    entry_atr_pct, risk_usd = memdb.execute(
        "SELECT entry_atr_pct, risk_usd FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert entry_atr_pct is not None
    resolved_mult = _stop_atr_mult_for_strategy("ema_crossover", atr_pct=entry_atr_pct)
    # The fixture must actually exercise the widened path (test is meaningful).
    assert resolved_mult > STOP_ATR_MULT

    fill_price, base_qty = _entry_fill(memdb, trade.position_id)
    expected_risk_usd = risk_usd_at_entry(
        entry_price=fill_price, entry_atr_pct=entry_atr_pct, base_qty=base_qty,
        stop_atr_mult=resolved_mult,
    )
    assert risk_usd == pytest.approx(expected_risk_usd)

    # REGRESSION: the pre-fix flat STOP_ATR_MULT=2.0 stamp would have been
    # materially SMALLER — proves the widen is load-bearing, not a no-op.
    flat_risk_usd = risk_usd_at_entry(
        entry_price=fill_price, entry_atr_pct=entry_atr_pct, base_qty=base_qty,
    )
    assert risk_usd > flat_risk_usd


@pytest.mark.asyncio
async def test_open_path_entry_atr_anchor_resolves_from_md_conn(
    memdb: sqlite3.Connection,
) -> None:
    """storage-split (round 4 MED fix): bars is marketdata-domain — the
    entry-time ATR anchor must resolve against ``state.md_conn``, not the
    (post-split, permanently empty) trading conn passed as ``conn``."""
    reset_process_fence()
    from polaris.storage.schema import ALL_DDL

    md_conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        md_conn.execute(stmt)
    _seed_bars(md_conn, interval="1H", n=20, band=0.1, close=100.0)  # ONLY md_conn
    sig = RawSignal(
        signal_id="sig-mdanchor", strategy_id="ema_crossover", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState(md_conn=md_conn)
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=200.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT entry_atr_pct FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    # resolved from md_conn (the empty trading `memdb` alone would yield NULL).
    assert row[0] is not None
    md_conn.close()


@pytest.mark.asyncio
async def test_open_path_wide_atr_stays_byte_identical_to_ssot_mult(
    memdb: sqlite3.Connection,
) -> None:
    """A WIDE-ATR instrument (fee floor never binds, base 2.0x already clears
    it) stamps risk_usd IDENTICAL to the pre-fix flat-2.0 call — no-op for the
    common case, mirroring every other ``_stop_atr_mult_for_strategy`` no-op
    guarantee in the codebase."""
    reset_process_fence()
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0)  # atr_pct ~0.04
    sig = RawSignal(
        signal_id="sig-wide", strategy_id="ema_crossover", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=80.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    entry_atr_pct, risk_usd = memdb.execute(
        "SELECT entry_atr_pct, risk_usd FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    resolved_mult = _stop_atr_mult_for_strategy("ema_crossover", atr_pct=entry_atr_pct)
    assert resolved_mult == STOP_ATR_MULT  # fee floor did not bind — no-op regime

    fill_price, base_qty = _entry_fill(memdb, trade.position_id)
    flat_risk_usd = risk_usd_at_entry(
        entry_price=fill_price, entry_atr_pct=entry_atr_pct, base_qty=base_qty,
    )
    assert risk_usd == pytest.approx(flat_risk_usd)


@pytest.mark.asyncio
async def test_open_path_no_anchor_risk_usd_none_byte_identical(
    memdb: sqlite3.Connection,
) -> None:
    """No bars -> no entry_atr_pct anchor -> risk_usd stays None (legacy
    fallback path, unaffected by the stop_atr_mult thread — the entry call
    passes ``atr_pct=0.0`` which degrades ``_stop_atr_mult_for_strategy`` to
    the flat base, and the ``entry_atr_pct=0.0`` ``risk_usd_at_entry`` input
    is unchanged pre/post-fix)."""
    reset_process_fence()
    sig = RawSignal(
        signal_id="sig-noanchor", strategy_id="ema_crossover", symbol="ETH-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="ETH-USDT",
        asset_class="crypto", underlying_group_id="crypto:ETH",
        notional_usd=80.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    entry_atr_pct, risk_usd = memdb.execute(
        "SELECT entry_atr_pct, risk_usd FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert entry_atr_pct is None
    # risk_usd_at_entry floors to a nonzero notional-pct value even at
    # entry_atr_pct=0.0 (RISK_USD_ABS_FLOOR / notional-pct floor) — assert it
    # matches the byte-identical flat-mult call (no anchor -> no widen input).
    fill_price, base_qty = _entry_fill(memdb, trade.position_id)
    expected = risk_usd_at_entry(
        entry_price=fill_price, entry_atr_pct=0.0, base_qty=base_qty,
    )
    assert risk_usd == pytest.approx(expected)
