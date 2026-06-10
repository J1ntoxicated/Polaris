"""One-off ops: correct the BUG-A slice-stamping pollution in ``fills.pnl_usd``.

BUG A stamped the FULL position pnl_usd on EVERY partial-close slice (SPCE: 56
fills each carrying the whole -579.78 ⇒ Alpaca booked -$4,679 vs a true
~-$1,861), and BUG B/E over-sells appended duplicate close fills beyond the
entry qty. This script recomputes each close fill's pnl_usd with the SAME
formula the runtime fix now uses (``frac = min(qty, remaining) / entry_qty``,
remainder-clamped, ts ascending) and re-stamps the polluted rows.

DEMO/PAPER only — virtual funds. Dry-run by default (read-only URI, zero
writes); ``--apply`` backs the DB up first and corrects in per-position
transactions with a ``risk_events`` audit row each. Idempotent: a second
``--apply`` finds zero corrections.

Operating order: deploy the runtime fixes, keep the bot STOPPED (BEGIN
IMMEDIATE aborts if another writer holds the DB), then run
``--apply --fix-status`` once. ``--fix-status`` also flips ``status='open'``
rows whose entry qty is fully consumed by close fills (the rows hydrate now
skips) to ``status='closed'``.

Note on observation fan-out: this corrects DOLLARS only. Duplicate full-close
pnl_r observations already folded into cell matrix / learner posteriors are
NOT rebuilt here — the duplicate SOURCE (B/E) is fixed, and the dup-close
audit section quantifies the polluted buckets for a follow-up rebuild call.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Single full-close gate: one close fill covering >= this fraction of the
# entry qty keeps its stamped pnl_usd byte-identical (runtime parity with
# 1 - _CLOSE_FULL_FILL_EPS). The quote-notional twin catches sim-shaped full
# closes whose base_qty was derived at a drifted exit price.
FULL_CLOSE_QTY_RATIO = 0.995
OVER_CLOSE_RATIO = 1.005
DEFAULT_TOL_USD = 0.01


@dataclass(slots=True)
class FillFix:
    fill_id: str
    stamped: float
    corrected: float
    qty_eff: float


@dataclass(slots=True)
class PositionReport:
    position_id: str
    venue: str
    symbol: str
    side: str
    status: str
    strategy_id: str
    entry_qty: float
    closed_qty: float
    n_close: int
    stamped_sum: float
    corrected_sum: float
    last_close_ts_ms: int
    fixes: list[FillFix] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _true_slice_pnl(
    *, side: str, entry_px: float, entry_size_usd: float, fill_px: float,
    frac: float,
) -> float:
    dpx = (fill_px - entry_px) if side == "long" else (entry_px - fill_px)
    return (dpx / entry_px) * entry_size_usd * frac


def analyze_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    side: str,
    status: str,
    strategy_id: str,
    tol_usd: float,
) -> PositionReport:
    """Recompute every close fill's true slice pnl_usd for one position."""
    inst = f"{venue}:{symbol}"
    entries = conn.execute(
        "SELECT fill_price, size_usd, base_qty FROM fills "
        "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 0 "
        "ORDER BY ts_ms ASC",
        (position_id, inst),
    ).fetchall()
    closes = conn.execute(
        "SELECT fill_id, fill_price, base_qty, pnl_usd, ts_ms FROM fills "
        "WHERE contribution_id = ? AND instrument_id = ? AND is_close = 1 "
        "ORDER BY ts_ms ASC",
        (position_id, inst),
    ).fetchall()
    closed_qty = sum(float(c[2] or 0.0) for c in closes)
    stamped_sum = sum(float(c[3] or 0.0) for c in closes)
    rep = PositionReport(
        position_id=position_id, venue=venue, symbol=symbol, side=side,
        status=status, strategy_id=strategy_id,
        entry_qty=float(entries[0][2]) if entries else 0.0,
        closed_qty=closed_qty, n_close=len(closes),
        stamped_sum=stamped_sum, corrected_sum=stamped_sum,
        last_close_ts_ms=int(closes[-1][4]) if closes else 0,
    )
    # Report-only exclusions — corrections need exactly one sane entry fill.
    if not entries:
        rep.flags.append("no-entry-fill")
        return rep
    if len(entries) > 1:
        rep.flags.append("manual-review-multi-entry")
        return rep
    entry_px = float(entries[0][0] or 0.0)
    entry_size = float(entries[0][1] or 0.0)
    entry_qty = float(entries[0][2] or 0.0)
    if entry_px <= 0.0 or entry_qty <= 0.0:
        rep.flags.append("degenerate-entry")
        return rep
    if closed_qty > entry_qty * OVER_CLOSE_RATIO:
        rep.flags.append("over-close")
    if status == "open" and (entry_qty - closed_qty) <= 1e-12:
        rep.flags.append("fills-drained-open")

    # Single full-close gate — byte-identity mandate. The quote-notional twin
    # catches sim-shaped closes (base_qty = notional / drifted exit px).
    if len(closes) == 1:
        bq = float(closes[0][2] or 0.0)
        fp = float(closes[0][1] or 0.0)
        qty_full = bq >= entry_qty * FULL_CLOSE_QTY_RATIO
        quote_full = (
            entry_size > 0.0
            and abs(bq * fp - entry_size) <= entry_size * (1.0 - FULL_CLOSE_QTY_RATIO)
        )
        if qty_full or quote_full:
            rep.flags.append("single-full")
            if quote_full and not qty_full:
                rep.flags.append("sim-quote-match")
            true_full = _true_slice_pnl(
                side=side, entry_px=entry_px, entry_size_usd=entry_size,
                fill_px=fp, frac=1.0,
            )
            if abs(float(closes[0][3] or 0.0) - true_full) > tol_usd:
                rep.flags.append("single-full-anomaly")
            return rep

    # Partial / multi-fill path — same formula as the runtime fix.
    remaining = entry_qty
    corrected_sum = 0.0
    for fill_id, fill_px, base_qty, stamped, _ts in closes:
        bq = float(base_qty or 0.0)
        qty_eff = min(bq, max(0.0, remaining))
        frac = qty_eff / entry_qty
        true = _true_slice_pnl(
            side=side, entry_px=entry_px, entry_size_usd=entry_size,
            fill_px=float(fill_px or 0.0), frac=frac,
        )
        remaining = max(0.0, remaining - bq)
        corrected_sum += true
        old = float(stamped or 0.0)
        if abs(old - true) > max(tol_usd, 1e-6 * abs(true)):
            rep.fixes.append(
                FillFix(fill_id=str(fill_id), stamped=old, corrected=true,
                        qty_eff=qty_eff)
            )
    rep.corrected_sum = corrected_sum
    return rep


def analyze(conn: sqlite3.Connection, *, tol_usd: float) -> list[PositionReport]:
    """All positions with >=1 instrument-scoped close fill (status-agnostic)."""
    rows = conn.execute(
        "SELECT p.position_id, p.venue, p.symbol, p.side, p.status, "
        "p.strategy_id FROM positions p WHERE EXISTS ("
        "SELECT 1 FROM fills f WHERE f.contribution_id = p.position_id "
        "AND f.instrument_id = p.venue || ':' || p.symbol AND f.is_close = 1) "
        "ORDER BY p.opened_ts ASC",
    ).fetchall()
    return [
        analyze_position(
            conn, position_id=str(r[0]), venue=str(r[1]), symbol=str(r[2]),
            side=str(r[3] or "long"), status=str(r[4] or ""),
            strategy_id=str(r[5] or ""), tol_usd=tol_usd,
        )
        for r in rows
    ]


def audit_dup_close_fanout(
    conn: sqlite3.Connection,
) -> list[tuple[str, int, str, list[str]]]:
    """Positions whose G8 close fan-out fired more than once (duplicate
    full-close pnl_r observations into cell matrix / learners). Keyed on
    gate_events.position_id — meta_labels.trade_id is UNIQUE-upserted (the
    duplicate overwrites, not appends) and trade_id is not restart-stable, so
    it cannot see the duplication. Returns (position_id, count, who, cell_keys)."""
    try:
        rows = conn.execute(
            "SELECT g.position_id, COUNT(*), "
            "COALESCE(MAX(p.venue || ':' || p.symbol || ' ' || p.strategy_id), '?') "
            "FROM gate_events g LEFT JOIN positions p "
            "ON p.position_id = g.position_id "
            "WHERE g.gate_id = 8 AND g.position_id IS NOT NULL "
            "GROUP BY g.position_id HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC",
        ).fetchall()
    except sqlite3.Error:
        return []
    out: list[tuple[str, int, str, list[str]]] = []
    for pid, cnt, who in rows:
        try:
            cells = [
                str(c[0]) for c in conn.execute(
                    "SELECT DISTINCT cell_key FROM position_strategy_segments "
                    "WHERE position_id = ? AND cell_key != ''",
                    (str(pid),),
                ).fetchall()
            ]
        except sqlite3.Error:
            cells = []
        out.append((str(pid), int(cnt), str(who), cells))
    return out


def backup_db(db: Path) -> str:
    """Copy db (+ -wal/-shm sidecars) to ``<db>.bak-<ts>``; raises on failure."""
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dst = db.with_name(db.name + f".bak-{stamp}")
    shutil.copy2(db, dst)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            shutil.copy2(side, str(dst) + suffix)
    return str(dst)


def apply_corrections(
    conn: sqlite3.Connection,
    reports: list[PositionReport],
    *,
    fix_status: bool,
    backup_path: str,
    now_ts: int,
) -> tuple[int, int]:
    """Per-position BEGIN IMMEDIATE: re-stamp fills + lineage segment + audit.
    Returns (positions_corrected, status_fixed)."""
    n_pos = 0
    n_status = 0
    for rep in reports:
        do_status = (
            fix_status and rep.status == "open"
            and "fills-drained-open" in rep.flags
        )
        if not rep.fixes and not do_status:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for fx in rep.fixes:
                conn.execute(
                    "UPDATE fills SET pnl_usd = ? WHERE fill_id = ?",
                    (fx.corrected, fx.fill_id),
                )
            if rep.fixes:
                # Closed lineage segments carry the position total.
                conn.execute(
                    "UPDATE position_strategy_segments SET pnl_usd = ? "
                    "WHERE position_id = ? AND ended_ts IS NOT NULL",
                    (rep.corrected_sum, rep.position_id),
                )
            if do_status:
                conn.execute(
                    "UPDATE positions SET status = 'closed', closed_ts = ?, "
                    "exit_state = 'closed' WHERE position_id = ? "
                    "AND status = 'open'",
                    (rep.last_close_ts_ms // 1000 or now_ts, rep.position_id),
                )
                n_status += 1
            payload = json.dumps(
                {
                    "position_id": rep.position_id,
                    "fill_ids": [fx.fill_id for fx in rep.fixes],
                    "old": [fx.stamped for fx in rep.fixes],
                    "new": [fx.corrected for fx in rep.fixes],
                    "qty_clamped": "over-close" in rep.flags,
                    "status_fixed": do_status,
                    "formula": "frac=min(qty,remaining)/entry_qty",
                    "backup_path": backup_path,
                },
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO risk_events (risk_event_id, strategy_id, "
                "event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'pnl_stamp_correction', ?, ?)",
                (uuid.uuid4().hex, rep.strategy_id, now_ts, payload),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        if rep.fixes:
            n_pos += 1
    return n_pos, n_status


def _print_report(
    reports: list[PositionReport],
    dup_fanout: list[tuple[str, int, str, list[str]]],
) -> None:
    touched = [r for r in reports if r.fixes or r.flags]
    print(f"close-fill positions scanned: {len(reports)}  "
          f"(with corrections/flags: {len(touched)})")
    hdr = (f"{'position_id':<42} {'venue:symbol':<22} {'st':<10} "
           f"{'entry_qty':>12} {'closed':>12} {'n':>3} {'stamped':>12} "
           f"{'corrected':>12} {'Δ':>12} flags")
    print(hdr)
    for r in touched:
        delta = r.corrected_sum - r.stamped_sum
        print(f"{r.position_id:<42} {r.venue + ':' + r.symbol:<22} "
              f"{r.status:<10} {r.entry_qty:>12.4f} {r.closed_qty:>12.4f} "
              f"{r.n_close:>3} {r.stamped_sum:>12.2f} {r.corrected_sum:>12.2f} "
              f"{delta:>12.2f} {','.join(r.flags) or '-'}")
    # Venue totals.
    print("\nvenue totals (stamped → corrected):")
    for venue in sorted({r.venue for r in reports}):
        vs = [r for r in reports if r.venue == venue]
        print(f"  {venue:<8} fills={sum(r.n_close for r in vs):>4} "
              f"{sum(r.stamped_sum for r in vs):>12.2f} → "
              f"{sum(r.corrected_sum for r in vs):>12.2f}")
    # OKX chunk-split pollution audit (close <=1000USDT children).
    okx_chunk = [
        r for r in reports
        if r.venue == "okx"
        and (r.n_close > 1 or r.closed_qty < r.entry_qty * FULL_CLOSE_QTY_RATIO)
    ]
    print(f"\nOKX chunk audit: {len(okx_chunk)} multi-fill/partial positions, "
          f"Δ={sum(r.corrected_sum - r.stamped_sum for r in okx_chunk):.2f}")
    over = [r for r in reports if "over-close" in r.flags]
    print(f"over-close (closed > entry×{OVER_CLOSE_RATIO}): {len(over)}")
    for r in over:
        print(f"  {r.position_id} closed={r.closed_qty:.6f} "
              f"entry={r.entry_qty:.6f} (+{r.closed_qty - r.entry_qty:.6f})")
    drained = [r for r in reports if "fills-drained-open" in r.flags]
    print(f"status='open' but fills fully consumed: {len(drained)} "
          f"(--fix-status flips these)")
    for r in drained:
        print(f"  {r.position_id} {r.venue}:{r.symbol}")
    print(f"\nduplicate G8 close fan-out (pnl_r observation dup): "
          f"{len(dup_fanout)} positions")
    for pid, cnt, who, cells in dup_fanout:
        print(f"  {pid} ×{cnt} {who} cells={cells or '-'}")


def run(
    *,
    db_path: str,
    apply: bool,
    fix_status: bool,
    tol_usd: float,
) -> int:
    db = Path(db_path)
    if not db.exists():
        print(f"DB not found: {db}")
        return 1
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"db={db}  mode={mode}  fix_status={fix_status}  tol_usd={tol_usd}")
    backup_path = ""
    if apply:
        backup_path = backup_db(db)
        print(f"backup → {backup_path}")
        conn = sqlite3.connect(db)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        reports = analyze(conn, tol_usd=tol_usd)
        dup_fanout = audit_dup_close_fanout(conn)
        _print_report(reports, dup_fanout)
        n_fixes = sum(len(r.fixes) for r in reports)
        if not apply:
            print(f"\n[dry-run] {n_fixes} fill corrections planned across "
                  f"{sum(1 for r in reports if r.fixes)} positions. "
                  f"Re-run with --apply (bot stopped).")
            return 0
        now_ts = int(time.time())
        n_pos, n_status = apply_corrections(
            conn, reports, fix_status=fix_status, backup_path=backup_path,
            now_ts=now_ts,
        )
        # Run-level audit row.
        summary = json.dumps(
            {
                "positions_corrected": n_pos,
                "fills_corrected": n_fixes,
                "status_fixed": n_status,
                "venue_delta": {
                    v: round(sum(
                        r.corrected_sum - r.stamped_sum
                        for r in reports if r.venue == v
                    ), 2)
                    for v in sorted({r.venue for r in reports})
                },
                "backup_path": backup_path,
            },
            separators=(",", ":"),
        )
        conn.execute(
            "INSERT INTO risk_events (risk_event_id, strategy_id, event_type, "
            "created_ts, payload_json) "
            "VALUES (?, '_ops', 'pnl_stamp_correction_run', ?, ?)",
            (uuid.uuid4().hex, now_ts, summary),
        )
        conn.commit()
        print(f"\n[apply] corrected {n_fixes} fills across {n_pos} positions; "
              f"status fixed: {n_status}")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="correct_close_pnl_stamping")
    p.add_argument("--db", default="data/polaris_live.sqlite",
                   help="SQLite DB path (default: data/polaris_live.sqlite).")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Write corrections (default: dry-run, read-only URI).")
    p.add_argument("--fix-status", action="store_true", default=False,
                   help="Also flip fills-drained status='open' rows to closed.")
    p.add_argument("--tol-usd", type=float, default=DEFAULT_TOL_USD,
                   help="Correct only fills off by more than this (USD).")
    args = p.parse_args(argv)
    return run(
        db_path=args.db, apply=args.apply, fix_status=args.fix_status,
        tol_usd=args.tol_usd,
    )


if __name__ == "__main__":
    raise SystemExit(main())
