"""Historical mfe_r/mae_r re-stamping — entry-anchored R units (DRY-RUN default).

DEMO/PAPER maintenance tool. Pre-anchor positions persisted excursions whose R
denominator was the CURRENT 1m ATR at close time — volatility contraction
inflated them 4-8x (live: -92.58R that was really -11.45%; one -463,734R row
from a collapsed denominator). This script re-denominates ``mfe_r``/``mae_r``
from the tracked ``peak_price``/``trough_price`` extremes using the ENTRY-time
anchor, and stamps ``entry_atr_pct``/``entry_atr_timeframe`` so a re-run is
idempotent (anchor column → path ① → identical output).

Safety contract:
* DRY-RUN by default — read-only URI connection (``mode=ro``) + report only.
  ``--apply`` is the only write path (single transaction).
* ``--db`` is REQUIRED and must exist (a typo'd path can never create a DB).
* Touches ONLY positions.mfe_r / mae_r / entry_atr_pct / entry_atr_timeframe.
  fills / sizing / gating tables untouched. Telemetry correction only — never
  sizing, never an entry block, never a halt.

Anchor resolution per row (mirrors the live open-stamp chain):
① ``entry_atr_pct`` column already stamped → reuse (idempotent re-run).
② Back-compute from bars at/before ``opened_ts`` on the ENTRY strategy's
  timeframe (≥5 bars, degenerate flat-bar windows rejected) → ③ 1m retry →
  ④ ``skipped_no_bars``. peak/trough-NULL rows are ``skipped_no_extremes``;
  rows without an entry fill are ``skipped_no_fill``.

Usage:
  python3 -m polaris.scripts.recalc_excursions --db data/polaris_live.sqlite
  python3 -m polaris.scripts.recalc_excursions --db ... --apply [--limit N]
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from polaris.core.live_recalc.excursion import compute_excursion_r
from polaris.scripts._production_atr import (
    strategy_timeframe,
    timeframe_anchor_atr_pct,
)
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy

__all__ = ["CorrectionReport", "RowCorrection", "main", "run_correction"]


@dataclass(slots=True)
class RowCorrection:
    """One corrected (or correction-candidate) positions row."""

    position_id: str
    strategy_id: str
    timeframe: str
    anchor: float
    anchor_source: str  # column | bars_<tf> | bars_1m
    bar_age_sec: int | None
    old_mfe_r: float | None
    old_mae_r: float | None
    new_mfe_r: float
    new_mae_r: float


@dataclass(slots=True)
class CorrectionReport:
    """Dry-run / apply summary."""

    rows: list[RowCorrection]
    corrected: int = 0
    unchanged: int = 0
    skipped_no_extremes: int = 0
    skipped_no_fill: int = 0
    skipped_no_bars: int = 0
    applied: bool = False


def _entry_fill_price(
    conn: sqlite3.Connection, *, position_id: str, instrument_id: str,
) -> float | None:
    row = conn.execute(
        """
        SELECT fill_price FROM fills
        WHERE contribution_id = ? AND instrument_id = ? AND is_close = 0
        ORDER BY ts_ms ASC LIMIT 1
        """,
        (position_id, instrument_id),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    price = float(row[0])
    return price if price > 0.0 else None


def _latest_bar_age_sec(
    conn: sqlite3.Connection, *, instrument_id: str, timeframe: str,
    opened_ts: int,
) -> int | None:
    """Age of the newest anchor bar vs ``opened_ts`` (weekend-gap visibility —
    report-only judgement material, never a gate)."""
    venue, _, symbol = instrument_id.partition(":")
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE venue = ? AND symbol = ? "
        "AND bar_interval = ? AND ts <= ?",
        (venue, symbol, timeframe, opened_ts + 120),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return max(0, opened_ts - int(row[0]))


def run_correction(
    conn: sqlite3.Connection, *, apply: bool = False, limit: int | None = None,
) -> CorrectionReport:
    """Compute (and with ``apply=True`` persist) the anchored re-stamping."""
    report = CorrectionReport(rows=[])
    cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    has_anchor_cols = "entry_atr_pct" in cols and "entry_atr_timeframe" in cols
    if apply and not has_anchor_cols:
        raise sqlite3.OperationalError(
            "positions.entry_atr_pct missing — run the schema migration "
            "(init_db) before --apply"
        )
    anchor_select = (
        "entry_atr_pct, entry_atr_timeframe" if has_anchor_cols
        else "NULL, NULL"
    )
    sql = (
        "SELECT position_id, venue, symbol, side, strategy_id, "
        " entry_strategy_id, opened_ts, peak_price, trough_price, "
        f" mfe_r, mae_r, {anchor_select} "
        "FROM positions "
        "WHERE peak_price IS NOT NULL AND trough_price IS NOT NULL "
        "ORDER BY opened_ts"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    # Pre-#26 rows never tracked extremes — count them for the report.
    total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    report.skipped_no_extremes = int(total) - len(rows)

    updates: list[tuple[float, float, float, str, str]] = []
    for r in rows:
        position_id = str(r[0])
        instrument_id = f"{r[1]}:{r[2]}"
        side = str(r[3])
        strategy_id = str(r[5] or r[4])  # entry strategy = anchor provenance
        opened_ts = int(r[6])
        peak, trough = float(r[7]), float(r[8])
        old_mfe = None if r[9] is None else float(r[9])
        old_mae = None if r[10] is None else float(r[10])
        entry_price = _entry_fill_price(
            conn, position_id=position_id, instrument_id=instrument_id,
        )
        if entry_price is None:
            report.skipped_no_fill += 1
            continue
        tf = strategy_timeframe(strategy_id)
        col_anchor = None if r[11] is None else float(r[11])
        if col_anchor is not None and col_anchor > 1e-5:
            anchor, anchor_tf = col_anchor, str(r[12] or tf)
            source = "column"
        else:
            got = timeframe_anchor_atr_pct(
                conn, instrument_id=instrument_id, timeframe=tf,
                now_ts=opened_ts,
            )
            if got is None:
                report.skipped_no_bars += 1
                continue
            anchor, anchor_tf = got
            source = f"bars_{anchor_tf}"
        bar_age = _latest_bar_age_sec(
            conn, instrument_id=instrument_id, timeframe=anchor_tf,
            opened_ts=opened_ts,
        )
        # Ruler SSOT (2026-07-10, exit-peak-lock-bind-v2 review follow-up): the
        # live entry/exit/close rulers all read _stop_atr_mult_for_strategy —
        # a re-stamp with a flat 2.0 would overwrite resolver-bound rows and
        # reintroduce the exact mismatch v2 removed.
        _mult = _stop_atr_mult_for_strategy(strategy_id, atr_pct=anchor)
        atr_usd = max(entry_price * anchor * _mult, entry_price * 1e-3)
        new_mfe, new_mae = compute_excursion_r(
            entry_price=entry_price, peak_price=peak, trough_price=trough,
            side=side, atr_usd=atr_usd,
        )
        row_out = RowCorrection(
            position_id=position_id, strategy_id=strategy_id,
            timeframe=anchor_tf, anchor=anchor, anchor_source=source,
            bar_age_sec=bar_age, old_mfe_r=old_mfe, old_mae_r=old_mae,
            new_mfe_r=new_mfe, new_mae_r=new_mae,
        )
        report.rows.append(row_out)
        changed = (
            old_mfe is None or old_mae is None
            or abs(old_mfe - new_mfe) > 1e-9 or abs(old_mae - new_mae) > 1e-9
            or col_anchor is None
        )
        if changed:
            report.corrected += 1
            updates.append((new_mfe, new_mae, anchor, anchor_tf, position_id))
        else:
            report.unchanged += 1

    if apply and updates:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "UPDATE positions SET mfe_r = ?, mae_r = ?, entry_atr_pct = ?, "
                "entry_atr_timeframe = ? WHERE position_id = ?",
                updates,
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        report.applied = True
    return report


def _print_report(report: CorrectionReport, *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[recalc_excursions] mode={mode}")
    top = sorted(
        report.rows,
        key=lambda c: abs((c.old_mfe_r or 0.0) - c.new_mfe_r)
        + abs((c.old_mae_r or 0.0) - c.new_mae_r),
        reverse=True,
    )[:10]
    for c in top:
        print(
            f"  {c.position_id} strat={c.strategy_id} tf={c.timeframe} "
            f"anchor={c.anchor:.6f} src={c.anchor_source} "
            f"bar_age={c.bar_age_sec}s "
            f"mfe {c.old_mfe_r} -> {c.new_mfe_r:.4f} "
            f"mae {c.old_mae_r} -> {c.new_mae_r:.4f}"
        )
    print(
        f"  summary: corrected={report.corrected} unchanged={report.unchanged} "
        f"skipped_no_extremes={report.skipped_no_extremes} "
        f"skipped_no_fill={report.skipped_no_fill} "
        f"skipped_no_bars={report.skipped_no_bars} applied={report.applied}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-stamp historical mfe_r/mae_r with the entry-time ATR "
        "anchor (DRY-RUN default; --apply writes).",
    )
    parser.add_argument("--db", required=True, help="SQLite DB path (must exist)")
    parser.add_argument(
        "--apply", action="store_true",
        help="write corrections (default: dry-run, read-only connection)",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"[recalc_excursions] DB not found: {db} — refusing to create")
        return 2
    if args.apply:
        # init_db = the SSOT idempotent migration path — guarantees the anchor
        # columns exist before the UPDATE (bot stopped; single writer).
        from polaris.storage.schema import init_db

        conn = init_db(db)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        report = run_correction(conn, apply=args.apply, limit=args.limit)
    finally:
        conn.close()
    _print_report(report, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
