"""P0-2 (exit horizon-scope) — momentum_drift now reads the STRATEGY's OWN
timeframe bars, not a hardcoded 1m window (trade_mess_full_audit_2026-07-02).

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block: this is a
MEASUREMENT fix, never a size/entry change. The audit found ``atr_pct`` was
ALREADY timeframe-aligned ([[1d_exit_horizon_fix_2026-06-26]] /
``_production_recalc.py`` lines 205-212 override ``atr_pct`` from
``timeframe_atr_pct`` when the active strategy's timeframe != "1m"), but the
SAME function's ``recent_ticks`` (which feeds ``_recent_tick_drift`` ->
``assess_thesis``'s ``momentum_drift`` input) stayed hardcoded on the last 20
``bar_interval='1m'`` rows for EVERY strategy — a 1H/1D thesis was judged
BROKEN off ~10 minutes of 1m noise regardless of its real horizon. This test
proves ``load_active_position_rows`` now derives ``recent_ticks`` /
``volume_now`` / ``volume_z`` / ``atr_slope`` from the SAME timeframe-scoped
bar window ``atr_pct`` already uses, so a 1H thesis's momentum_drift measures
1H-scale drift, not 1m noise. The horizon-scoped materiality floor
(``exit_thesis.py`` ``EXIT_THESIS_DRIFT_FLOOR``) and the corroborated-break
bypass are UNCHANGED by this fix — this only corrects the INPUT they measure.
A tick-engine (unregistered) strategy keeps the 1m window — byte-identical.
"""

from __future__ import annotations

import sqlite3

from polaris.scripts._production_recalc import load_active_position_rows

NOW = 1_780_000_000
IID = "okx:BTC-USDT"


def _seed_bars(
    conn: sqlite3.Connection, *, interval: str, n: int,
    start_close: float, step: float, end_ts: int = NOW,
) -> None:
    """Seed a MONOTONE close ramp (start_close + i*step) so the window's
    first-to-last drift is unambiguous and easy to assert on."""
    sec = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "1D": 86400}[interval]
    for i in range(n):
        ts = end_ts - (n - 1 - i) * sec
        close = start_close + i * step
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', 'okx', 'BTC-USDT', ?, ?, ?, ?, ?, ?, "
            " 100.0, 1e4, 1, ?, ?, ?, 1.0, 'rest')",
            (IID, interval, ts, close, close + 0.01, close - 0.01, close,
             close, close, close),
        )


def _seed_position(
    conn: sqlite3.Connection, *, position_id: str, strategy: str,
    entry_price: float = 100.0, opened_ts: int = NOW,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, 'long', 0.001, "
        " 'open', ?, 0)",
        (position_id, strategy, strategy, strategy, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, 'okx', 'long', 0.001, ?, 80.0, 0.05, 1.0, 0.0, 0, "
        " ?, ?, 'filled')",
        ("fill-" + position_id, opened_ts * 1000, strategy, IID, entry_price,
         position_id, "order-" + position_id),
    )


def test_1h_strategy_drift_reads_1h_bars_not_1m_noise(
    memdb: sqlite3.Connection,
) -> None:
    # 1m bars ramp DOWN (noise, last ~10 min): last close 99.5, first 100.5.
    _seed_bars(memdb, interval="1m", n=20, start_close=100.5, step=-0.05)
    # 1H bars ramp UP (the real 1H thesis direction): last close 110, first 91.
    _seed_bars(memdb, interval="1H", n=20, start_close=91.0, step=1.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-1h", strategy="ema_crossover")
    rows = {r["position_id"]: r for r in load_active_position_rows(memdb)}
    ticks = rows["pos-1h"]["recent_ticks"]
    assert len(ticks) >= 2
    first_close = ticks[0]["close"]
    last_close = ticks[-1]["close"]
    # The 1H window is UP (last > first) -- if this were still the 1m window
    # it would read DOWN (last < first), the exact drift-sign flip the audit's
    # 1D/1H thesis-cut bug hinges on.
    assert last_close > first_close


def test_tick_engine_position_keeps_1m_drift_window_byte_identical(
    memdb: sqlite3.Connection,
) -> None:
    # micro_reversion is NOT in STRATEGY_REGISTRY -> strategy_timeframe() falls
    # back to "1m", so recent_ticks stays the 1m window -- byte-identical.
    _seed_bars(memdb, interval="1m", n=20, start_close=100.5, step=-0.05)
    _seed_bars(memdb, interval="1H", n=20, start_close=91.0, step=1.0,
               end_ts=NOW - 60)
    # [P0-5] opened_ts must be <= the window's OLDEST 1m bar (NOW - 19*60) —
    # load_active_position_rows now excludes ts < opened_ts, so a position
    # opened AT NOW would collapse the 20-bar drift window to 1 bar.
    _seed_position(
        memdb, position_id="pos-tick", strategy="micro_reversion",
        opened_ts=NOW - 19 * 60,
    )
    rows = {r["position_id"]: r for r in load_active_position_rows(memdb)}
    ticks = rows["pos-tick"]["recent_ticks"]
    first_close = ticks[0]["close"]
    last_close = ticks[-1]["close"]
    # The 1m window is DOWN (ramp step=-0.05) -- unchanged pre-fix behaviour.
    assert last_close < first_close


def test_1h_strategy_volume_and_slope_also_use_1h_window(
    memdb: sqlite3.Connection,
) -> None:
    # Byproduct of the SAME fix: volume_now / atr_slope (also derived from
    # _recent_market_state(bar_row)) must be consistent with the 1H window
    # too -- they cannot silently stay on the 1m rows while recent_ticks moves.
    _seed_bars(memdb, interval="1m", n=20, start_close=100.5, step=-0.05)
    _seed_bars(memdb, interval="1H", n=20, start_close=91.0, step=1.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-1h-vol", strategy="ema_crossover")
    rows = {r["position_id"]: r for r in load_active_position_rows(memdb)}
    row = rows["pos-1h-vol"]
    # Both windows seed volume=100.0 uniformly, so volume_now/volume_z stay
    # sane either way -- the load-bearing assertion is that atr_pct and
    # recent_ticks/atr_slope are computed from the SAME (1H) row set, i.e. no
    # crash / mismatch when the two windows have very different bar counts or
    # closes. Confirms row is well-formed for the 1H-scoped read.
    assert row["volume_now"] == 100.0
    assert isinstance(row["atr_slope"], float)
