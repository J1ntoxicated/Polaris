"""Correction script — fills.pnl_usd slice re-stamping (BUG A pollution).

Fixture DB shapes (mirroring the live pollution):
* SPCE-like: 2 partial slices + 1 over-close duplicate, EVERY fill stamped the
  full position PnL (-579.78) → slices re-stamped to their fraction, the
  over-close duplicate to 0.0, sum clamped to the entry qty.
* Single full close: byte-identical (untouched) even when anomalous.
* Multi-entry (scale-in/legacy): flagged manual-review, never auto-corrected.
* Capital short 2-slice: side-aware Δpx, lineage segment re-stamped to the sum.
* status='open' with entry fully consumed by close fills → --fix-status flips.

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

from polaris.scripts.correct_close_pnl_stamping import (
    analyze,
    audit_dup_close_fanout,
    main,
)
from polaris.storage.schema import init_db

ENTRY_QTY = 221.288515406
ENTRY_PX = 7.14
ENTRY_SIZE = ENTRY_QTY * ENTRY_PX  # 1580.0
EXIT_PX = 4.52
DPX = EXIT_PX - ENTRY_PX  # -2.62
FULL_PNL = (DPX / ENTRY_PX) * ENTRY_SIZE  # -579.78… (= DPX × ENTRY_QTY)


def _pos(
    conn: sqlite3.Connection, *, pid: str, venue: str, symbol: str, side: str,
    qty: float, status: str,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        " underlying_group_id, strategy_id, entry_strategy_id, "
        " active_strategy_id, side, qty, status, opened_ts, swap_count) "
        "VALUES (?, ?, ?, '', 's', 's', 's', ?, ?, ?, 1000, 0)",
        (pid, venue, symbol, side, qty, status),
    )


def _fill(
    conn: sqlite3.Connection, *, fill_id: str, venue: str, symbol: str,
    pid: str, is_close: bool, base_qty: float, fill_price: float,
    size_usd: float, pnl_usd: float, ts_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, ?, ?, 's', ?, ?, ?, 0, 0, ?, '', ?, ?, ?, ?, ?, 'filled')",
        (fill_id, venue, f"{venue}:{symbol}", "sell" if is_close else "buy",
         size_usd, fill_price, ts_ms, pid, pnl_usd, 1 if is_close else 0,
         base_qty, size_usd),
    )


def _seed_fixture(conn: sqlite3.Connection) -> None:
    # 1) SPCE shape — every slice stamped the FULL position PnL + over-close dup.
    _pos(conn, pid="p_spce", venue="alpaca", symbol="SPCE", side="long",
         qty=0.0, status="open")
    _fill(conn, fill_id="spce_e", venue="alpaca", symbol="SPCE", pid="p_spce",
          is_close=False, base_qty=ENTRY_QTY, fill_price=ENTRY_PX,
          size_usd=ENTRY_SIZE, pnl_usd=0.0, ts_ms=1_000_000)
    for i, bq in enumerate((161.0, ENTRY_QTY - 161.0, ENTRY_QTY)):
        _fill(conn, fill_id=f"spce_c{i}", venue="alpaca", symbol="SPCE",
              pid="p_spce", is_close=True, base_qty=bq, fill_price=EXIT_PX,
              size_usd=bq * EXIT_PX, pnl_usd=FULL_PNL, ts_ms=2_000_000 + i)
    # 2) single full close — stamped value preserved byte-identically.
    _pos(conn, pid="p_full", venue="okx", symbol="SOL-USDT", side="long",
         qty=0.0, status="closed")
    _fill(conn, fill_id="full_e", venue="okx", symbol="SOL-USDT", pid="p_full",
          is_close=False, base_qty=10.0, fill_price=100.0, size_usd=1000.0,
          pnl_usd=0.0, ts_ms=1_000_000)
    _fill(conn, fill_id="full_c", venue="okx", symbol="SOL-USDT", pid="p_full",
          is_close=True, base_qty=10.0, fill_price=130.0, size_usd=1300.0,
          pnl_usd=300.0, ts_ms=2_000_000)
    # 3) multi-entry — manual review, never auto-corrected.
    _pos(conn, pid="p_multi", venue="okx", symbol="ETH-USDT", side="long",
         qty=0.0, status="closed")
    for i in range(2):
        _fill(conn, fill_id=f"multi_e{i}", venue="okx", symbol="ETH-USDT",
              pid="p_multi", is_close=False, base_qty=1.0, fill_price=3000.0,
              size_usd=3000.0, pnl_usd=0.0, ts_ms=1_000_000 + i)
    _fill(conn, fill_id="multi_c", venue="okx", symbol="ETH-USDT",
          pid="p_multi", is_close=True, base_qty=1.0, fill_price=3100.0,
          size_usd=3100.0, pnl_usd=12345.0, ts_ms=2_000_000)
    # 4) Capital SHORT, 2 slices stamped wrong + a closed lineage segment.
    _pos(conn, pid="p_cap", venue="capital", symbol="EURUSD", side="short",
         qty=0.0, status="closed")
    _fill(conn, fill_id="cap_e", venue="capital", symbol="EURUSD", pid="p_cap",
          is_close=False, base_qty=2.0, fill_price=1.10, size_usd=2000.0,
          pnl_usd=0.0, ts_ms=1_000_000)
    for i in range(2):
        _fill(conn, fill_id=f"cap_c{i}", venue="capital", symbol="EURUSD",
              pid="p_cap", is_close=True, base_qty=1.0, fill_price=1.08,
              size_usd=1080.0, pnl_usd=100.0, ts_ms=2_000_000 + i)
    conn.execute(
        "INSERT INTO position_strategy_segments (position_id, segment_id, "
        " strategy_id, regime_at_start, started_ts, ended_ts, pnl_r, "
        " trade_id, venue, ticker, pnl_usd, cell_key) "
        "VALUES ('p_cap', 'seg_cap', 's', 'chop', 1000, 2000, 0.5, "
        " 'p_cap', 'capital', 'EURUSD', 100.0, 'capital:s:EURUSD:chop')",
    )
    # 5) duplicate G8 close fan-out (pnl_r observation dup) for the audit.
    for i in range(2):
        conn.execute(
            "INSERT INTO gate_events (event_id, run_id, signal_id, "
            " position_id, gate_id, phase, decision, created_ts) "
            "VALUES (?, 'r1', 'p_cap', 'p_cap', 8, 'P0', 'reflect', ?)",
            (f"ge_{i}", 3000 + i),
        )
    conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Iterable[Path]:
    path = tmp_path / "fixture.sqlite"
    conn = init_db(path)
    _seed_fixture(conn)
    conn.close()
    yield path


def _pnl(conn: sqlite3.Connection, fill_id: str) -> float:
    return float(
        conn.execute(
            "SELECT pnl_usd FROM fills WHERE fill_id = ?", (fill_id,)
        ).fetchone()[0]
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_leaves_db_byte_identical(db_path: Path) -> None:
    before = _sha(db_path)
    rc = main(["--db", str(db_path)])
    assert rc == 0
    assert _sha(db_path) == before
    assert not list(db_path.parent.glob("*.bak-*"))  # no backup on dry-run


def test_analyze_recomputes_slices_and_clamps_overclose(db_path: Path) -> None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        reports = {r.position_id: r for r in analyze(conn, tol_usd=0.01)}
    finally:
        conn.close()
    spce = reports["p_spce"]
    assert len(spce.fixes) == 3
    by_id = {f.fill_id: f for f in spce.fixes}
    assert by_id["spce_c0"].corrected == pytest.approx(DPX * 161.0, rel=1e-9)
    assert by_id["spce_c1"].corrected == pytest.approx(
        DPX * (ENTRY_QTY - 161.0), rel=1e-9
    )
    assert by_id["spce_c2"].corrected == 0.0  # over-close dup → clamped to 0
    # Position sum clamps to the ENTRY qty (the -1860.65 → -579.78 direction).
    assert spce.corrected_sum == pytest.approx(FULL_PNL, rel=1e-9)
    assert "over-close" in spce.flags
    assert "fills-drained-open" in spce.flags
    # Single full close — untouched (byte-identity gate) + flagged anomaly.
    full = reports["p_full"]
    assert full.fixes == []
    assert "single-full" in full.flags
    # Multi-entry — manual review, no auto-correction.
    multi = reports["p_multi"]
    assert multi.fixes == []
    assert "manual-review-multi-entry" in multi.flags
    # Capital short — side-aware slice: (1.10-1.08)/1.10 × 2000 × 0.5.
    cap = reports["p_cap"]
    true_slice = (1.10 - 1.08) / 1.10 * 2000.0 * 0.5
    assert [f.corrected for f in cap.fixes] == [
        pytest.approx(true_slice, rel=1e-9),
        pytest.approx(true_slice, rel=1e-9),
    ]


def test_apply_restamps_audits_and_fixes_status(db_path: Path) -> None:
    rc = main(["--db", str(db_path), "--apply", "--fix-status"])
    assert rc == 0
    # Backup was taken before any write.
    assert list(db_path.parent.glob("*.bak-*"))
    conn = sqlite3.connect(db_path)
    try:
        assert _pnl(conn, "spce_c0") == pytest.approx(DPX * 161.0, rel=1e-9)
        assert _pnl(conn, "spce_c1") == pytest.approx(
            DPX * (ENTRY_QTY - 161.0), rel=1e-9
        )
        assert _pnl(conn, "spce_c2") == 0.0
        # Single-full + multi-entry stamped values untouched (exact).
        assert _pnl(conn, "full_c") == 300.0
        assert _pnl(conn, "multi_c") == 12345.0
        # Drained 'open' row flipped terminal (the hydrate-skip zombie).
        status = conn.execute(
            "SELECT status FROM positions WHERE position_id = 'p_spce'"
        ).fetchone()[0]
        assert status == "closed"
        # Lineage segment carries the corrected position sum.
        seg = conn.execute(
            "SELECT pnl_usd FROM position_strategy_segments "
            "WHERE segment_id = 'seg_cap'"
        ).fetchone()[0]
        assert seg == pytest.approx((1.10 - 1.08) / 1.10 * 2000.0, rel=1e-9)
        # Audit rows: one per corrected position + one run summary.
        n_pos_audit = conn.execute(
            "SELECT COUNT(*) FROM risk_events "
            "WHERE event_type = 'pnl_stamp_correction'"
        ).fetchone()[0]
        n_run = conn.execute(
            "SELECT COUNT(*) FROM risk_events "
            "WHERE event_type = 'pnl_stamp_correction_run'"
        ).fetchone()[0]
        assert n_pos_audit == 2  # p_spce + p_cap
        assert n_run == 1
    finally:
        conn.close()


def test_apply_is_idempotent(db_path: Path) -> None:
    assert main(["--db", str(db_path), "--apply", "--fix-status"]) == 0
    conn = sqlite3.connect(db_path)
    snap1 = conn.execute(
        "SELECT fill_id, pnl_usd FROM fills ORDER BY fill_id"
    ).fetchall()
    n_audit1 = conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = "
        "'pnl_stamp_correction'"
    ).fetchone()[0]
    conn.close()

    assert main(["--db", str(db_path), "--apply", "--fix-status"]) == 0
    conn = sqlite3.connect(db_path)
    try:
        snap2 = conn.execute(
            "SELECT fill_id, pnl_usd FROM fills ORDER BY fill_id"
        ).fetchall()
        n_audit2 = conn.execute(
            "SELECT COUNT(*) FROM risk_events WHERE event_type = "
            "'pnl_stamp_correction'"
        ).fetchone()[0]
        # Zero new corrections on the second pass.
        assert snap2 == snap1
        assert n_audit2 == n_audit1
        # Analyze on the corrected DB plans nothing further.
        reports = analyze(conn, tol_usd=0.01)
        assert sum(len(r.fixes) for r in reports) == 0
    finally:
        conn.close()


def test_dup_close_fanout_audit_uses_position_id(db_path: Path) -> None:
    """meta_labels.trade_id is UNIQUE-upserted (dups overwrite, not append) —
    the fan-out audit must key on gate_events.position_id instead."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = audit_dup_close_fanout(conn)
    finally:
        conn.close()
    assert [(r[0], r[1]) for r in rows] == [("p_cap", 2)]
    # The polluted cell bucket is named for the follow-up rebuild call.
    assert rows[0][3] == ["capital:s:EURUSD:chop"]
