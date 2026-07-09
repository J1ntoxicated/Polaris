"""One-off ops: re-stamp ``positions.risk_usd`` (+ ``pnl_r``) for the Capital
qty-unit contract regression (fix/capital-qty-contract).

Root cause: ``polaris.scripts._smoke_fills``'s Capital SIM branch used to
derive ``Fill.base_qty`` (persisted to ``fills.base_qty`` AND the entry
``risk_usd_at_entry`` stamp via ``positions.qty``/``entry_base_qty``) from
``notional_usd / 10.0`` — a legacy pip-value placeholder with NO relationship
to price — instead of ``notional_usd / entry_price``, the "quantity of
underlying" contract every OTHER ``base_qty`` consumer assumes (OKX's
``accFillSz``, Alpaca's ``filled_qty``, ``risk_usd_at_entry`` itself). This is
fixed going forward by ``_smoke_fills._capital_sim_units``; this script
corrects the historical rows the broken formula already stamped.

Detection here is a PRECISE, mechanical signature match — not a blanket
recompute-and-diff. A blanket recompute (any Capital position vs a fresh
``risk_usd_at_entry(base_qty=size_usd/price, quote_usd_rate=1.0)``) also flags
~130 older Capital positions produced by the REAL-roundtrip venue-fill path or
an earlier stamping-formula era (a different, possibly-correct expression, or
a real non-1.0 ``quote_usd_rate`` this offline script cannot recover) — those
are OUT OF SCOPE and must not be touched. A position is IN SCOPE only when its
OWN entry fill's persisted ``base_qty`` equals ``size_usd / 10.0`` (the exact
broken expression, within float tolerance) AND ``size_usd`` is materially
above the ($10.00-flat, already-acknowledged, separately out-of-scope) legacy
placeholder era. On the live DB (2026-07-10) this isolates exactly 4
positions — USDCAD / SG25 / USDJPY / US500, all opened 2026-07-09 ~14:38-16:01
UTC (the first real-notional Capital sim fills after the qty-regression's
introducing commit reached the live bot).

Closed positions in scope additionally get ``positions.pnl_r`` re-derived from
``realised_r(pnl_usd=<sum of close fills' pnl_usd>, risk_usd=<corrected>)`` —
``pnl_usd`` itself is untouched (the slice formula never reads ``base_qty``),
only the R-unit RESCALING was wrong.

DEMO/PAPER only — virtual funds. Dry-run by default (read-only URI, zero
writes); ``--apply`` backs the DB up first and corrects in per-position
transactions with a ``risk_events`` audit row each. Idempotent: a second
``--apply`` finds zero corrections (a position already carrying the corrected
``risk_usd`` is skipped).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from polaris.core.metrics.risk_unit import realised_r, risk_usd_at_entry
from polaris.scripts._smoke_fills import _CAPITAL_SIM_PIP_VALUE_USD
from polaris.scripts.correct_close_pnl_stamping import backup_db
from polaris.scripts.exit_strategy_config import _stop_atr_mult_for_strategy

# The exact legacy pip-value era stamped size_usd == $10.00 flat (Capital
# $10-flat collapse, live 2026-07-07..07-09) — a SEPARATE, already-acknowledged
# historical issue, out of scope here. Anything strictly above this ceiling is
# a genuine T4-sized notional that went through the SAME broken formula.
LEGACY_PLACEHOLDER_CEILING_USD = 15.0
_SIGNATURE_REL_TOL = 1e-6
_ALREADY_CORRECTED_REL_TOL = 1e-6


@dataclass(slots=True)
class RiskUsdFix:
    position_id: str
    venue: str
    symbol: str
    strategy_id: str
    status: str
    entry_price: float
    size_usd: float
    stamped_risk_usd: float
    corrected_risk_usd: float
    stamped_pnl_r: float | None
    corrected_pnl_r: float | None


def _is_broken_signature(*, base_qty: float, size_usd: float) -> bool:
    """True iff ``base_qty == size_usd / _CAPITAL_SIM_PIP_VALUE_USD`` — the
    EXACT broken expression ``_smoke_fills`` used to stamp it. This is the
    precise, evidence-based fingerprint of a position that went through the
    qty-unit regression, as opposed to the real-roundtrip path or an earlier
    stamping era (which use a DIFFERENT ``base_qty`` expression, not
    necessarily wrong)."""
    if size_usd <= 0.0:
        return False
    legacy_qty = size_usd / _CAPITAL_SIM_PIP_VALUE_USD
    return abs(legacy_qty - base_qty) <= _SIGNATURE_REL_TOL * max(1.0, abs(base_qty))


def analyze(conn: sqlite3.Connection) -> list[RiskUsdFix]:
    """Every Capital position matching the broken-formula signature, with its
    corrected ``risk_usd`` (and ``pnl_r`` when closed). Already-corrected rows
    (idempotency) and out-of-scope rows (real-path / legacy-$10 era / no
    entry fill) are silently excluded."""
    rows = conn.execute(
        """
        SELECT p.position_id, p.venue, p.symbol, p.strategy_id, p.status,
               p.risk_usd, p.entry_atr_pct, p.pnl_r,
               f.fill_price, f.size_usd, f.base_qty
        FROM positions p
        JOIN fills f ON f.contribution_id = p.position_id AND f.is_close = 0
        WHERE p.venue = 'capital'
        ORDER BY p.opened_ts ASC
        """,
    ).fetchall()
    fixes: list[RiskUsdFix] = []
    for (
        position_id, venue, symbol, strategy_id, status, stamped_risk,
        entry_atr_pct, stamped_pnl_r, fill_price, size_usd, base_qty,
    ) in rows:
        price = float(fill_price or 0.0)
        size = float(size_usd or 0.0)
        bq = float(base_qty or 0.0)
        if price <= 0.0 or size <= LEGACY_PLACEHOLDER_CEILING_USD:
            continue
        if not _is_broken_signature(base_qty=bq, size_usd=size):
            continue
        true_base_qty = size / price
        atr_pct = float(entry_atr_pct) if entry_atr_pct is not None else 0.0
        stop_mult = _stop_atr_mult_for_strategy(strategy_id, atr_pct=atr_pct)
        corrected_risk = risk_usd_at_entry(
            entry_price=price, entry_atr_pct=atr_pct, base_qty=true_base_qty,
            stop_atr_mult=stop_mult, quote_usd_rate=1.0,
        )
        stamped = float(stamped_risk) if stamped_risk is not None else 0.0
        if abs(stamped - corrected_risk) <= _ALREADY_CORRECTED_REL_TOL * max(
            1.0, abs(corrected_risk)
        ):
            continue  # already corrected — idempotency
        corrected_pnl_r: float | None = None
        stamped_pnl_r_f = float(stamped_pnl_r) if stamped_pnl_r is not None else None
        if status == "closed":
            pnl_row = conn.execute(
                "SELECT SUM(pnl_usd) FROM fills WHERE contribution_id = ? "
                "AND instrument_id = ? AND is_close = 1",
                (position_id, f"{venue}:{symbol}"),
            ).fetchone()
            pnl_usd = (
                float(pnl_row[0]) if pnl_row and pnl_row[0] is not None else None
            )
            if pnl_usd is not None:
                corrected_pnl_r = realised_r(pnl_usd=pnl_usd, risk_usd=corrected_risk)
        fixes.append(
            RiskUsdFix(
                position_id=position_id, venue=venue, symbol=symbol,
                strategy_id=strategy_id, status=status, entry_price=price,
                size_usd=size, stamped_risk_usd=stamped,
                corrected_risk_usd=corrected_risk,
                stamped_pnl_r=stamped_pnl_r_f, corrected_pnl_r=corrected_pnl_r,
            )
        )
    return fixes


def apply_corrections(
    conn: sqlite3.Connection, fixes: list[RiskUsdFix], *,
    backup_path: str, now_ts: int,
) -> int:
    """Per-position ``BEGIN IMMEDIATE``: re-stamp ``risk_usd`` (+ ``pnl_r``) +
    a ``risk_events`` audit row. Returns the number of positions corrected."""
    n = 0
    for fx in fixes:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if fx.corrected_pnl_r is not None:
                conn.execute(
                    "UPDATE positions SET risk_usd = ?, pnl_r = ? "
                    "WHERE position_id = ?",
                    (fx.corrected_risk_usd, fx.corrected_pnl_r, fx.position_id),
                )
            else:
                conn.execute(
                    "UPDATE positions SET risk_usd = ? WHERE position_id = ?",
                    (fx.corrected_risk_usd, fx.position_id),
                )
            payload = json.dumps(
                {
                    "position_id": fx.position_id,
                    "venue": fx.venue,
                    "symbol": fx.symbol,
                    "stamped_risk_usd": fx.stamped_risk_usd,
                    "corrected_risk_usd": fx.corrected_risk_usd,
                    "stamped_pnl_r": fx.stamped_pnl_r,
                    "corrected_pnl_r": fx.corrected_pnl_r,
                    "formula": "size_usd/entry_price base_qty (qty-unit contract fix)",
                    "backup_path": backup_path,
                },
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO risk_events (risk_event_id, strategy_id, "
                "event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'capital_risk_usd_qty_contract_correction', ?, ?)",
                (uuid.uuid4().hex, fx.strategy_id, now_ts, payload),
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        n += 1
    return n


def _print_report(fixes: list[RiskUsdFix]) -> None:
    print(f"Capital positions with the qty-unit contract signature: {len(fixes)}")
    hdr = (f"{'position_id':<42} {'symbol':<10} {'price':>10} {'size_usd':>10} "
           f"{'stamped_risk':>14} {'corrected_risk':>14} {'stamped_r':>10} "
           f"{'corrected_r':>12}")
    print(hdr)
    for fx in fixes:
        print(
            f"{fx.position_id:<42} {fx.symbol:<10} {fx.entry_price:>10.4f} "
            f"{fx.size_usd:>10.2f} {fx.stamped_risk_usd:>14.4f} "
            f"{fx.corrected_risk_usd:>14.4f} "
            f"{'-' if fx.stamped_pnl_r is None else f'{fx.stamped_pnl_r:>10.6f}'} "
            f"{'-' if fx.corrected_pnl_r is None else f'{fx.corrected_pnl_r:>12.6f}'}"
        )


def run(*, db_path: str, apply: bool) -> int:
    db = Path(db_path)
    if not db.exists():
        print(f"DB not found: {db}")
        return 1
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"db={db}  mode={mode}")
    backup_path = ""
    if apply:
        backup_path = backup_db(db)
        print(f"backup -> {backup_path}")
        conn = sqlite3.connect(db)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        fixes = analyze(conn)
        _print_report(fixes)
        if not apply:
            print(f"\n[dry-run] {len(fixes)} position corrections planned. "
                  f"Re-run with --apply (bot stopped).")
            return 0
        now_ts = int(time.time())
        n_pos = apply_corrections(conn, fixes, backup_path=backup_path, now_ts=now_ts)
        summary = json.dumps(
            {
                "positions_corrected": n_pos,
                "backup_path": backup_path,
            },
            separators=(",", ":"),
        )
        conn.execute(
            "INSERT INTO risk_events (risk_event_id, strategy_id, event_type, "
            "created_ts, payload_json) "
            "VALUES (?, '_ops', 'capital_risk_usd_qty_contract_correction_run', "
            "?, ?)",
            (uuid.uuid4().hex, now_ts, summary),
        )
        conn.commit()
        print(f"\n[apply] corrected {n_pos} positions")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="correct_capital_risk_usd_stamping")
    p.add_argument("--db", default="data/polaris_live.sqlite",
                   help="SQLite DB path (default: data/polaris_live.sqlite).")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Write corrections (default: dry-run, read-only URI).")
    args = p.parse_args(argv)
    return run(db_path=args.db, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
