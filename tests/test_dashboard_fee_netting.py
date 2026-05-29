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

from polaris.core.sizing.constants import TOTAL_DEMO_STARTING_EQUITY_USD
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
    """Open fee $10 + close (gross +$100, fee $10) → net realised = +$80."""
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    _insert_fill(db_path, fill_id="open", side="buy", pnl_usd=0.0,
                 fee_usd=10.0, is_close=0, ts_ms=now_ms - 1000)
    _insert_fill(db_path, fill_id="close", side="sell", pnl_usd=100.0,
                 fee_usd=10.0, is_close=1, ts_ms=now_ms)

    snap = collect_snapshot(db_path=db_path)
    # gross +100, fees 20 → net +80. (pre-fix this returned +100.)
    assert abs(snap.daily_pnl_usd - 80.0) < 1e-6, (
        f"daily_pnl must net fees: got {snap.daily_pnl_usd}, expected 80.0"
    )


def test_equity_now_is_net_of_fees(tmp_path: Path) -> None:
    """A losing-after-fees round-trip must drag equity below starting capital."""
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    # Gross break-even (+$5) but $70 fees → net -$65. Timestamps well inside the
    # 24h curve window (not on the final bucket boundary) so both fills count.
    _insert_fill(db_path, fill_id="o", side="buy", pnl_usd=0.0,
                 fee_usd=35.0, is_close=0, ts_ms=now_ms - 120_000)
    _insert_fill(db_path, fill_id="c", side="sell", pnl_usd=5.0,
                 fee_usd=35.0, is_close=1, ts_ms=now_ms - 60_000)

    snap = collect_snapshot(db_path=db_path)
    expected = TOTAL_DEMO_STARTING_EQUITY_USD - 65.0
    assert abs(snap.equity_now - expected) < 1e-6, (
        f"equity must net fees: got {snap.equity_now}, expected {expected}"
    )
