"""Dual realized-PnL equity curve — demo-actual vs real-fee-net (Component A).

The go-live confidence signal (Jin 2026-05-31) is a REAL-FEE-NET equity curve
that trends up. The snapshot must expose TWO curves built from the SAME fills
walk + SAME gross close pnl_usd, differing only in the fee schedule:

  - demo_actual  : stored fills.fee_usd (the 0.7% OKX demo actually charged) —
                   matches the real demo account drain.
  - real_fee_net : fees RECOMPUTED at the real schedule (0.10% OKX taker) per
                   fill notional (size_usd).

Assertion targets (per the plan):
  demo_actual final  ≈ gross - 0.7% * turnover
  real_fee_net final ≈ gross - 0.10% * turnover
  divergence         ≈ (0.7% - 0.1%) * turnover
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.sizing.constants import TOTAL_DEMO_STARTING_EQUITY_USD
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_queries import _build_dual_equity_curve
from polaris.storage.schema import ALL_DDL


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _insert_fill(
    db_path: Path,
    *,
    fill_id: str,
    venue: str = "okx",
    side: str,
    size_usd: float,
    pnl_usd: float,
    fee_usd: float,
    is_close: int,
    ts_ms: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, 'tsmom', ?, ?, 150.0, ?, 1.0, ?, ?, NULL, ?, ?, "
        "        6.6, 1000.0, 'filled')",
        (
            fill_id, venue, f"{venue}:SOL-USDT", side, size_usd, fee_usd, ts_ms,
            f"o_{fill_id}", pnl_usd, is_close,
        ),
    )
    conn.close()


def _seed_roundtrips(db_path: Path, *, n: int, notional: float, gross_each: float) -> None:
    """n round-trips: each = open + close, both at ``notional``, demo fee 0.7%."""
    base = int(time.time() * 1000) - n * 120_000
    demo_fee = notional * 0.007  # 70 bps stored as the demo actual
    for i in range(n):
        ts_open = base + i * 120_000
        ts_close = ts_open + 60_000
        _insert_fill(db_path, fill_id=f"o{i}", side="buy", size_usd=notional,
                     pnl_usd=0.0, fee_usd=demo_fee, is_close=0, ts_ms=ts_open)
        _insert_fill(db_path, fill_id=f"c{i}", side="sell", size_usd=notional,
                     pnl_usd=gross_each, fee_usd=demo_fee, is_close=1, ts_ms=ts_close)


def test_dual_curve_diverges_by_fee_delta(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    n, notional, gross_each = 10, 1000.0, 5.0
    _seed_roundtrips(db_path, n=n, notional=notional, gross_each=gross_each)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        now_s = int(time.time())
        result = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=TOTAL_DEMO_STARTING_EQUITY_USD,
        )
    finally:
        conn.close()

    turnover = n * 2 * notional        # both legs are notional
    gross = n * gross_each
    start = TOTAL_DEMO_STARTING_EQUITY_USD

    demo_final = result.equity_demo[-1]
    real_final = result.equity_real[-1]

    # demo: 0.7% per leg * turnover
    assert abs(demo_final - (start + gross - 0.007 * turnover)) < 1e-6
    # real: 0.10% taker per leg * turnover
    assert abs(real_final - (start + gross - 0.0010 * turnover)) < 1e-6
    # divergence = (0.7% - 0.1%) * turnover
    assert abs((real_final - demo_final) - (0.007 - 0.0010) * turnover) < 1e-6

    assert abs(result.demo_fee_total - 0.007 * turnover) < 1e-6
    assert abs(result.real_fee_total - 0.0010 * turnover) < 1e-6
    assert len(result.equity_demo) == len(result.equity_real) == len(result.bucket_ts)


def test_dual_curve_demo_matches_existing_curve(tmp_path: Path) -> None:
    """demo_actual leg must equal the EXISTING _build_equity_curve (no regression)."""
    from polaris.scripts.dashboard.snapshot_queries import _build_equity_curve

    db_path = _mkdb(tmp_path)
    _seed_roundtrips(db_path, n=5, notional=2000.0, gross_each=-3.0)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        now_s = int(time.time())
        legacy_ts, legacy_eq, legacy_total = _build_equity_curve(
            conn, now_s=now_s, starting_capital=TOTAL_DEMO_STARTING_EQUITY_USD,
        )
        dual = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=TOTAL_DEMO_STARTING_EQUITY_USD,
        )
    finally:
        conn.close()
    assert dual.bucket_ts == legacy_ts
    assert dual.equity_demo == legacy_eq
    assert abs(dual.total_realised_demo - legacy_total) < 1e-9


def test_dual_curve_empty_db(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        now_s = int(time.time())
        dual = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=TOTAL_DEMO_STARTING_EQUITY_USD,
        )
    finally:
        conn.close()
    assert dual.demo_fee_total == 0.0
    assert dual.real_fee_total == 0.0
    assert all(v == TOTAL_DEMO_STARTING_EQUITY_USD for v in dual.equity_demo)
    assert all(v == TOTAL_DEMO_STARTING_EQUITY_USD for v in dual.equity_real)


def test_snapshot_exposes_dual_curve_fields(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    n, notional, gross_each = 8, 1500.0, 4.0
    _seed_roundtrips(db_path, n=n, notional=notional, gross_each=gross_each)
    snap = collect_snapshot(db_path=db_path)

    turnover = n * 2 * notional
    gross = n * gross_each
    # P0-2: collect_snapshot's base is the 3-venue total (OKX + Capital +
    # Alpaca display leg), not the OKX+Capital-only module constant.
    start = snap.starting_capital

    # real-fee-net curve + headline scalars present and additive (no break to demo).
    assert len(snap.equity_curve_real_fee_net) >= 1
    # equity_now (demo, with uPnL) keeps the existing meaning; here no open pos.
    assert abs(snap.equity_now - (start + gross - 0.007 * turnover)) < 1e-3
    assert abs(snap.equity_now_real_fee_net - (start + gross - 0.0010 * turnover)) < 1e-3
    assert abs(snap.demo_fee_total - 0.007 * turnover) < 1e-6
    assert abs(snap.real_fee_total - 0.0010 * turnover) < 1e-6
    # The two final curve points diverge by the fee-delta * turnover.
    div = snap.equity_curve_real_fee_net[-1] - snap.equity_curve[-1]
    assert abs(div - (0.007 - 0.0010) * turnover) < 1e-2


def test_snapshot_real_fee_net_missing_db() -> None:
    snap = collect_snapshot(db_path=Path("/nonexistent/polaris.sqlite"))
    # Graceful zero-snapshot still carries the new fields (defaults).
    assert snap.equity_curve_real_fee_net == []
    assert snap.real_fee_total == 0.0
    assert snap.demo_fee_total == 0.0
    # Confidence panel present + zeroed on a missing DB.
    assert snap.confidence.n_closed == 0
    assert snap.confidence.cells == []


def test_snapshot_surfaces_confidence_panel(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    _seed_roundtrips(db_path, n=6, notional=1000.0, gross_each=4.0)
    snap = collect_snapshot(db_path=db_path)
    # 6 winning round-trips → win_rate 100%, panel populated, fee drag wedge.
    assert snap.confidence.n_closed == 6
    assert abs(snap.confidence.win_rate_pct - 100.0) < 1e-9
    assert snap.confidence.fee_drag_demo_r > snap.confidence.fee_drag_real_r
    assert len(snap.confidence.cells) >= 1
    # dataclasses.asdict (what /api/snapshot serializes) must not raise.
    import dataclasses
    serialized = dataclasses.asdict(snap)
    assert "confidence" in serialized
    assert "equity_curve_real_fee_net" in serialized
