"""Correction script — Capital qty-unit contract regression (risk_usd/pnl_r).

Fixture DB shapes (mirroring the live pollution, [[db-writer-reader-split-design]]):
* US500-shaped OPEN position — the broken price-blind ``notional/10`` unit
  stamped risk_usd ~750x too high ($248,534.73 vs the true $330.06).
* SG25-shaped OPEN position — same bug, different instrument ($73,507.49 vs
  the true $1,439.63).
* USDCAD-shaped CLOSED position — same bug on a ~$1-priced pair (UNDER-states
  risk_usd instead of over-stating it); the closed-position pnl_r must be
  re-derived from the corrected risk_usd too.
* A CLEAN Capital position whose base_qty is already ``size_usd / price``
  (the real-roundtrip / already-fixed shape) — must be left untouched (no
  signature match).
* A LEGACY $10-flat placeholder position — matches the SAME broken formula
  but is a separate, already-acknowledged, out-of-scope historical issue
  (immaterial notional) — must be left untouched.

Dry-run must leave the DB byte-identical; --apply is idempotent (2nd run = 0
corrections) and writes risk_events audit rows + a run summary.

DEMO/PAPER only; virtual funds.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest

from polaris.core.metrics.risk_unit import realised_r, risk_usd_at_entry
from polaris.scripts.correct_capital_risk_usd_stamping import analyze, main
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy
from polaris.storage.schema import init_db

# US500-shaped: $50k notional @ 7530.0, broken base_qty = 50000/10 = 5000.0.
US500_PRICE = 7530.0
US500_SIZE_USD = 50_000.0
US500_ATR = 0.0033005940703341257
US500_STRATEGY = "xau_indices_trend"
US500_STAMPED_RISK = 248_534.73349615966
US500_STOP_MULT = _stop_atr_mult_for_strategy(US500_STRATEGY, atr_pct=US500_ATR)
US500_TRUE_RISK = risk_usd_at_entry(
    entry_price=US500_PRICE, entry_atr_pct=US500_ATR,
    base_qty=US500_SIZE_USD / US500_PRICE, stop_atr_mult=US500_STOP_MULT,
)

# SG25-shaped: $50k notional @ 510.6, broken base_qty = 5000.0.
SG25_PRICE = 510.6
SG25_SIZE_USD = 50_000.0
SG25_ATR = 0.01439629705770339
SG25_STRATEGY = "index_52w_high_momentum"
SG25_STAMPED_RISK = 73_507.49277663352
SG25_STOP_MULT = _stop_atr_mult_for_strategy(SG25_STRATEGY, atr_pct=SG25_ATR)
SG25_TRUE_RISK = risk_usd_at_entry(
    entry_price=SG25_PRICE, entry_atr_pct=SG25_ATR,
    base_qty=SG25_SIZE_USD / SG25_PRICE, stop_atr_mult=SG25_STOP_MULT,
)

# USDCAD-shaped (CLOSED): $50k notional @ 1.41729, broken base_qty = 5000.0.
USDCAD_PRICE = 1.41729
USDCAD_SIZE_USD = 50_000.0
USDCAD_ATR = 0.0007559200790798194
USDCAD_STRATEGY = "cci_reversion"
USDCAD_STAMPED_RISK = 35.43225
USDCAD_STAMPED_PNL_R = -0.6372245861255313
USDCAD_CLOSE_PNL_USD = -22.57830084174636
USDCAD_STOP_MULT = _stop_atr_mult_for_strategy(USDCAD_STRATEGY, atr_pct=USDCAD_ATR)
USDCAD_TRUE_RISK = risk_usd_at_entry(
    entry_price=USDCAD_PRICE, entry_atr_pct=USDCAD_ATR,
    base_qty=USDCAD_SIZE_USD / USDCAD_PRICE, stop_atr_mult=USDCAD_STOP_MULT,
)
USDCAD_TRUE_PNL_R = realised_r(
    pnl_usd=USDCAD_CLOSE_PNL_USD, risk_usd=USDCAD_TRUE_RISK
)


def _pos(
    conn: sqlite3.Connection, *, pid: str, symbol: str, strategy_id: str,
    qty: float, status: str, risk_usd: float, entry_atr_pct: float,
    pnl_r: float | None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count, "
        " risk_usd, entry_atr_pct, pnl_r) "
        "VALUES (?, 'capital', ?, '', ?, ?, ?, 'long', ?, ?, 1783600000, 0, "
        " ?, ?, ?)",
        (pid, symbol, strategy_id, strategy_id, strategy_id, qty, status,
         risk_usd, entry_atr_pct, pnl_r),
    )


def _fill(
    conn: sqlite3.Connection, *, fill_id: str, symbol: str, pid: str,
    strategy_id: str, is_close: bool, base_qty: float, fill_price: float,
    size_usd: float, pnl_usd: float, ts_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'capital', ?, ?, ?, ?, ?, 0, 0, ?, '', ?, ?, ?, ?, ?, "
        " 'filled')",
        (fill_id, f"capital:{symbol}", strategy_id,
         "sell" if is_close else "buy", size_usd, fill_price, ts_ms, pid,
         pnl_usd, 1 if is_close else 0, base_qty, size_usd),
    )


def _seed_fixture(conn: sqlite3.Connection) -> None:
    # 1) US500 — OPEN, ~750x-inflated risk_usd (broken base_qty = size_usd/10).
    _pos(conn, pid="p_us500", symbol="US500", strategy_id=US500_STRATEGY,
         qty=US500_SIZE_USD / 10.0, status="open", risk_usd=US500_STAMPED_RISK,
         entry_atr_pct=US500_ATR, pnl_r=None)
    _fill(conn, fill_id="us500_e", symbol="US500", pid="p_us500",
          strategy_id=US500_STRATEGY, is_close=False,
          base_qty=US500_SIZE_USD / 10.0, fill_price=US500_PRICE,
          size_usd=US500_SIZE_USD, pnl_usd=0.0, ts_ms=1_783_612_858_000)

    # 2) SG25 — OPEN, same bug, different instrument.
    _pos(conn, pid="p_sg25", symbol="SG25", strategy_id=SG25_STRATEGY,
         qty=SG25_SIZE_USD / 10.0, status="open", risk_usd=SG25_STAMPED_RISK,
         entry_atr_pct=SG25_ATR, pnl_r=None)
    _fill(conn, fill_id="sg25_e", symbol="SG25", pid="p_sg25",
          strategy_id=SG25_STRATEGY, is_close=False,
          base_qty=SG25_SIZE_USD / 10.0, fill_price=SG25_PRICE,
          size_usd=SG25_SIZE_USD, pnl_usd=0.0, ts_ms=1_783_607_906_000)

    # 3) USDCAD — CLOSED, ~1-priced pair (risk_usd UNDER-stated), pnl_r wrong.
    _pos(conn, pid="p_usdcad", symbol="USDCAD", strategy_id=USDCAD_STRATEGY,
         qty=USDCAD_SIZE_USD / 10.0, status="closed",
         risk_usd=USDCAD_STAMPED_RISK, entry_atr_pct=USDCAD_ATR,
         pnl_r=USDCAD_STAMPED_PNL_R)
    _fill(conn, fill_id="usdcad_e", symbol="USDCAD", pid="p_usdcad",
          strategy_id=USDCAD_STRATEGY, is_close=False,
          base_qty=USDCAD_SIZE_USD / 10.0, fill_price=USDCAD_PRICE,
          size_usd=USDCAD_SIZE_USD, pnl_usd=0.0, ts_ms=1_783_607_942_000)
    _fill(conn, fill_id="usdcad_c", symbol="USDCAD", pid="p_usdcad",
          strategy_id=USDCAD_STRATEGY, is_close=True,
          base_qty=USDCAD_SIZE_USD / 10.0, fill_price=1.41665,
          size_usd=USDCAD_SIZE_USD, pnl_usd=USDCAD_CLOSE_PNL_USD,
          ts_ms=1_783_609_739_000)

    # 4) CLEAN — base_qty already size_usd/price (real-roundtrip shape);
    # correctly-stamped risk_usd. Must NOT be touched (no signature match).
    clean_price = 1.3000
    clean_size_usd = 1_000.0
    clean_base_qty = clean_size_usd / clean_price
    clean_atr = 0.002
    clean_strategy = "session_breakout"
    clean_risk = risk_usd_at_entry(
        entry_price=clean_price, entry_atr_pct=clean_atr,
        base_qty=clean_base_qty,
        stop_atr_mult=_stop_atr_mult_for_strategy(clean_strategy, atr_pct=clean_atr),
    )
    _pos(conn, pid="p_clean", symbol="GBPUSD", strategy_id=clean_strategy,
         qty=clean_base_qty, status="open", risk_usd=clean_risk,
         entry_atr_pct=clean_atr, pnl_r=None)
    _fill(conn, fill_id="clean_e", symbol="GBPUSD", pid="p_clean",
          strategy_id=clean_strategy, is_close=False, base_qty=clean_base_qty,
          fill_price=clean_price, size_usd=clean_size_usd, pnl_usd=0.0,
          ts_ms=1_783_600_100_000)

    # 5) LEGACY $10-flat placeholder — matches the SAME broken formula
    # (base_qty = size_usd/10 = 1.0) but size_usd is the immaterial legacy
    # constant — a separate, already-acknowledged, out-of-scope issue.
    legacy_price = 4115.16
    legacy_size_usd = 10.0
    legacy_atr = 0.004232679182834197
    legacy_strategy = "xau_indices_trend"
    _pos(conn, pid="p_legacy10", symbol="GOLD", strategy_id=legacy_strategy,
         qty=1.0, status="closed", risk_usd=34.836304132063944,
         entry_atr_pct=legacy_atr, pnl_r=0.0)
    _fill(conn, fill_id="legacy_e", symbol="GOLD", pid="p_legacy10",
          strategy_id=legacy_strategy, is_close=False, base_qty=1.0,
          fill_price=legacy_price, size_usd=legacy_size_usd, pnl_usd=0.0,
          ts_ms=1_783_454_532_000)
    conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Iterable[Path]:
    path = tmp_path / "fixture.sqlite"
    conn = init_db(path)
    _seed_fixture(conn)
    conn.close()
    yield path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_leaves_db_byte_identical(db_path: Path) -> None:
    before = _sha(db_path)
    rc = main(["--db", str(db_path)])
    assert rc == 0
    assert _sha(db_path) == before
    assert not list(db_path.parent.glob("*.bak-*"))  # no backup on dry-run


def test_analyze_identifies_contaminated_rows_only(db_path: Path) -> None:
    """The 2 named contaminated rows (US500 -> ~330, SG25 -> ~1440) plus the
    USDCAD closed row are identified; the clean and legacy-$10 rows are not."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        fixes = {fx.position_id: fx for fx in analyze(conn)}
    finally:
        conn.close()
    assert set(fixes) == {"p_us500", "p_sg25", "p_usdcad"}

    us500 = fixes["p_us500"]
    assert us500.stamped_risk_usd == pytest.approx(248_534.73349615966)
    assert us500.corrected_risk_usd == pytest.approx(330.0594070334126, rel=1e-6)
    assert us500.corrected_risk_usd == pytest.approx(US500_TRUE_RISK)

    sg25 = fixes["p_sg25"]
    assert sg25.stamped_risk_usd == pytest.approx(73_507.49277663352)
    assert sg25.corrected_risk_usd == pytest.approx(1_439.6297057703391, rel=1e-6)
    assert sg25.corrected_risk_usd == pytest.approx(SG25_TRUE_RISK)

    usdcad = fixes["p_usdcad"]
    assert usdcad.stamped_risk_usd == pytest.approx(35.43225)
    assert usdcad.corrected_risk_usd == pytest.approx(USDCAD_TRUE_RISK)
    assert usdcad.stamped_pnl_r == pytest.approx(USDCAD_STAMPED_PNL_R)
    assert usdcad.corrected_pnl_r == pytest.approx(USDCAD_TRUE_PNL_R)


def test_apply_restamps_risk_usd_and_pnl_r(db_path: Path) -> None:
    rc = main(["--db", str(db_path), "--apply"])
    assert rc == 0
    assert list(db_path.parent.glob("*.bak-*"))
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT risk_usd, pnl_r FROM positions WHERE position_id = 'p_us500'"
        ).fetchone()
        assert row[0] == pytest.approx(US500_TRUE_RISK)
        assert row[1] is None  # still open — no pnl_r to correct

        row = conn.execute(
            "SELECT risk_usd, pnl_r FROM positions WHERE position_id = 'p_sg25'"
        ).fetchone()
        assert row[0] == pytest.approx(SG25_TRUE_RISK)

        row = conn.execute(
            "SELECT risk_usd, pnl_r FROM positions WHERE position_id = 'p_usdcad'"
        ).fetchone()
        assert row[0] == pytest.approx(USDCAD_TRUE_RISK)
        assert row[1] == pytest.approx(USDCAD_TRUE_PNL_R)

        # Clean + legacy-$10 rows untouched (byte-identical stamped values).
        row = conn.execute(
            "SELECT risk_usd FROM positions WHERE position_id = 'p_clean'"
        ).fetchone()
        assert row[0] == pytest.approx(
            risk_usd_at_entry(
                entry_price=1.3000, entry_atr_pct=0.002,
                base_qty=1_000.0 / 1.3000,
                stop_atr_mult=_stop_atr_mult_for_strategy(
                    "session_breakout", atr_pct=0.002,
                ),
            )
        )
        row = conn.execute(
            "SELECT risk_usd, pnl_r FROM positions WHERE position_id = 'p_legacy10'"
        ).fetchone()
        assert row[0] == pytest.approx(34.836304132063944)
        assert row[1] == pytest.approx(0.0)

        n_pos_audit = conn.execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type = "
            "'capital_risk_usd_qty_contract_correction'"
        ).fetchone()[0]
        n_run = conn.execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type = "
            "'capital_risk_usd_qty_contract_correction_run'"
        ).fetchone()[0]
        assert n_pos_audit == 3  # p_us500 + p_sg25 + p_usdcad
        assert n_run == 1
    finally:
        conn.close()


def test_apply_is_idempotent(db_path: Path) -> None:
    assert main(["--db", str(db_path), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    snap1 = conn.execute(
        "SELECT position_id, risk_usd, pnl_r FROM positions ORDER BY position_id"
    ).fetchall()
    n_audit1 = conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = "
        "'capital_risk_usd_qty_contract_correction'"
    ).fetchone()[0]
    conn.close()

    assert main(["--db", str(db_path), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    try:
        snap2 = conn.execute(
            "SELECT position_id, risk_usd, pnl_r FROM positions ORDER BY position_id"
        ).fetchall()
        n_audit2 = conn.execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type = "
            "'capital_risk_usd_qty_contract_correction'"
        ).fetchone()[0]
        assert snap2 == snap1
        assert n_audit2 == n_audit1  # zero new corrections on the 2nd pass
        assert analyze(conn) == []
    finally:
        conn.close()
