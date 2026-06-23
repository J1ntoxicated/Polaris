"""Step M (2026-06-22) — positions.risk_usd column + entry-ATR sanity bound.

The intended 1R-in-dollars is persisted at entry so realised R (pnl_usd/risk_usd)
is identical on every panel. These tests pin: the migration adds the column, an
in-band anchor yields the exact stop-distance×size risk unit, and an outlier
anchor (the audit's 46.6% denominator-collapse outliers) is bounded BEFORE it
becomes a denominator.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.metrics.risk_unit import (
    ENTRY_ATR_PCT_MAX,
    ENTRY_ATR_PCT_MIN,
    STOP_ATR_MULT,
    risk_usd_at_entry,
)


def test_positions_has_risk_usd_column(memdb: sqlite3.Connection) -> None:
    cols = {r[1] for r in memdb.execute("PRAGMA table_info(positions)")}
    assert "risk_usd" in cols


def test_init_db_migrates_legacy_positions(tmp_path: object) -> None:
    # A legacy positions table without risk_usd must gain the column via the
    # idempotent ALTER migration in init_db (re-running is a no-op).
    from pathlib import Path

    from polaris.storage.schema import init_db

    db = Path(str(tmp_path)) / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE positions (position_id TEXT PRIMARY KEY, venue TEXT, "
        " symbol TEXT, underlying_group_id TEXT, strategy_id TEXT, "
        " entry_strategy_id TEXT, active_strategy_id TEXT, side TEXT, qty REAL, "
        " status TEXT, opened_ts INTEGER, swap_count INTEGER)"
    )
    conn.commit()
    conn.close()
    c2 = init_db(db)
    cols = {r[1] for r in c2.execute("PRAGMA table_info(positions)")}
    assert "risk_usd" in cols
    c2.close()
    # Idempotent second run.
    c3 = init_db(db)
    cols2 = {r[1] for r in c3.execute("PRAGMA table_info(positions)")}
    assert "risk_usd" in cols2
    c3.close()


def test_risk_usd_in_band_is_exact_stop_distance_times_size() -> None:
    # entry 50, atr% 2%, 2 ATR, 1000 units → 50*0.02*2*1000 = 2000.
    risk = risk_usd_at_entry(entry_price=50.0, entry_atr_pct=0.02, base_qty=1000.0)
    assert risk == pytest.approx(50.0 * 0.02 * STOP_ATR_MULT * 1000.0)


def test_collapsed_atr_outlier_is_floored_not_zero() -> None:
    # An entry ATR that collapsed to ~0 (flat/stale bars — the audit's
    # denominator-collapse outlier) must floor, never produce a ~0 risk unit
    # that would explode R. [[harvest_generalization_2026-06-23]]: the risk_usd
    # floor (max of an absolute floor and 0.5% of notional) now binds for a
    # collapsed anchor — the 0.5%-notional floor (0.5% of $50,000 = $250) is the
    # stronger minimum, well above the bare min-atr term. Either way: never ~0.
    from polaris.core.metrics.risk_unit import (
        RISK_USD_ABS_FLOOR,
        RISK_USD_NOTIONAL_PCT,
    )

    risk = risk_usd_at_entry(entry_price=50.0, entry_atr_pct=1e-8, base_qty=1000.0)
    notional = 50.0 * 1000.0
    expected = max(
        50.0 * ENTRY_ATR_PCT_MIN * STOP_ATR_MULT * 1000.0,
        RISK_USD_ABS_FLOOR,
        RISK_USD_NOTIONAL_PCT * notional,
    )
    assert risk == pytest.approx(expected)
    assert risk > 0.0


def test_absurd_high_atr_outlier_is_capped() -> None:
    # An absurd high anchor (would crush R toward 0 and hide a real loss) caps.
    risk = risk_usd_at_entry(entry_price=50.0, entry_atr_pct=5.0, base_qty=1000.0)
    assert risk == pytest.approx(50.0 * ENTRY_ATR_PCT_MAX * STOP_ATR_MULT * 1000.0)
