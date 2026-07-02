"""Dashboard PnL must be NET of fees (forensic 2026-05-29 P0).

The forensic audit found the dashboard computed equity / daily PnL as
``starting_capital + SUM(pnl_usd)`` only — `fee_usd` was never subtracted.
With OKX demo charging a flat 70 bps, that hid ~$1.2K of real fee loss and
showed -$145 instead of the true -$1.35K. Fees are a real venue deduction even
on DEMO, so the dashboard must net them out.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
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
    side: str,
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
        "VALUES (?, 'okx', 'okx:SOL-USDT', 'tsmom', ?, "
        "        1000.0, 150.0, ?, 1.0, ?, ?, NULL, ?, ?, 6.6, 1000.0, 'filled')",
        (fill_id, side, fee_usd, ts_ms, f"o_{fill_id}", pnl_usd, is_close),
    )
    conn.close()


def test_daily_pnl_is_net_of_fees(tmp_path: Path) -> None:
    """The realised headline nets the stored ``fills.fee_usd``.

    ``fills.fee_usd`` now holds the REAL fee (fill_normalizer 2026-06-01), so
    ``daily_pnl_usd`` is REAL-fee-net (aligned with go-live viability). Here the
    seeded fees ($10/leg) sum to $20 → gross +$100 − $20 = +$80. (The 70 bps demo
    drain is shown separately by ``equity_now``/``demo_fee_total``.)
    """
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    _insert_fill(db_path, fill_id="open", side="buy", pnl_usd=0.0,
                 fee_usd=10.0, is_close=0, ts_ms=now_ms - 1000)
    _insert_fill(db_path, fill_id="close", side="sell", pnl_usd=100.0,
                 fee_usd=10.0, is_close=1, ts_ms=now_ms)

    snap = collect_snapshot(db_path=db_path)
    # gross +100, stored fees 20 → net +80.
    assert abs(snap.daily_pnl_usd - 80.0) < 1e-6, (
        f"daily_pnl must net stored (real) fees: got {snap.daily_pnl_usd}, expected 80.0"
    )


def test_equity_now_is_net_of_demo_fees(tmp_path: Path) -> None:
    """The DEMO equity curve (``equity_now``) nets the recomputed 70 bps drain.

    ``equity_now`` is the demo-actual leg of the dual-equity curve, which
    recomputes ``demo_fee_usd(venue, size_usd)`` from notional (the stored
    ``fills.fee_usd`` now holds the REAL fee and is not read by this leg).
    size_usd=1000 → $7/leg; gross +$5, two legs → net +$5 − $14 = −$9.
    """
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    _insert_fill(db_path, fill_id="o", side="buy", pnl_usd=0.0,
                 fee_usd=35.0, is_close=0, ts_ms=now_ms - 120_000)
    _insert_fill(db_path, fill_id="c", side="sell", pnl_usd=5.0,
                 fee_usd=35.0, is_close=1, ts_ms=now_ms - 60_000)

    snap = collect_snapshot(db_path=db_path)
    # P0-2: base is the 3-venue total (OKX + Capital + Alpaca display leg).
    expected = snap.starting_capital - 9.0
    assert abs(snap.equity_now - expected) < 1e-6, (
        f"equity_now must net demo fees: got {snap.equity_now}, expected {expected}"
    )
