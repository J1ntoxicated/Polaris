"""One-shot restamp of OPEN positions.risk_usd for the quote-ccy conversion fix.

DEMO/PAPER only. ``risk_usd_at_entry`` (``polaris/core/metrics/risk_unit.py``)
previously computed ``entry_price * atr_pct * stop_atr_mult * base_qty`` with NO
quote→USD conversion — for a non-USD-quoted Capital instrument (USDJPY, J225,
EU50, ...) this stamped a raw-quote-ccy dollar figure, not USD (audit rank 4,
[[trade_mess_full_audit_2026-07-02_verdict]]: live J225 risk_usd $47,615.89 vs
real ≈$317). The live stamping site (``_production_pipeline.py``) and the
close-time excursion path (``_close_excursion_r``) are now fixed going forward;
this migration re-stamps the ALREADY-OPEN positions' ``risk_usd`` so in-flight
trades read correctly too. CLOSED positions are untouched (a separate
measurement-correction pass owns historical backfill — out of scope here).

Quote ccy is derived from the symbol string (the same no-network derivation the
#50 dashboard fix uses — ``_quote_ccy_for_symbol``) since an offline migration
has no warm live constraint cache; the USD rate is the latest 1m bar close of
the conversion pair (``_bars_quote_usd_rate`` — the same bars-first source the
live stamping path prefers). A position whose rate cannot be resolved (no bars
for the conversion pair) is left untouched and reported, never guessed.

Usage
-----
    python3 -m tools.restamp_risk_usd_quote_ccy --db data/polaris_live.sqlite
    python3 -m tools.restamp_risk_usd_quote_ccy --db data/polaris_live.sqlite --apply
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from dataclasses import dataclass

from polaris.core.metrics.risk_unit import STOP_ATR_MULT, risk_usd_at_entry
from polaris.scripts._production_capital_sizing import _bars_quote_usd_rate
from polaris.scripts.dashboard.snapshot_q_common import _quote_ccy_for_symbol

logger = logging.getLogger("restamp_risk_usd_quote_ccy")


@dataclass(frozen=True, slots=True)
class RestampResult:
    position_id: str
    venue: str
    symbol: str
    quote_ccy: str
    old_risk_usd: float | None
    new_risk_usd: float | None
    skipped_reason: str | None = None


def _entry_fill_price(conn: sqlite3.Connection, *, position_id: str, instrument_id: str) -> float | None:
    row = conn.execute(
        "SELECT fill_price FROM fills WHERE contribution_id = ? "
        "AND instrument_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
        (position_id, instrument_id),
    ).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else None


def compute_restamps(conn: sqlite3.Connection) -> list[RestampResult]:
    """Read every OPEN position and compute its corrected ``risk_usd``.

    Pure read — no writes. Skips (with a reason) any position whose quote ccy
    is USD-equivalent (nothing to fix) or whose conversion rate is unresolvable
    (no bars for the conversion pair — never guessed).
    """
    rows = conn.execute(
        "SELECT position_id, venue, symbol, qty, entry_atr_pct, risk_usd "
        "FROM positions WHERE status = 'open'"
    ).fetchall()
    out: list[RestampResult] = []
    for position_id, venue, symbol, qty, entry_atr_pct, old_risk_usd in rows:
        quote_ccy = _quote_ccy_for_symbol(venue, symbol, "USD")
        if quote_ccy in ("", "USD"):
            continue  # nothing to convert
        entry_price = _entry_fill_price(
            conn, position_id=position_id, instrument_id=f"{venue}:{symbol}"
        )
        if entry_price is None or entry_price <= 0.0 or qty is None or float(qty) <= 0.0:
            out.append(RestampResult(
                position_id=position_id, venue=venue, symbol=symbol,
                quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=None,
                skipped_reason="no entry fill / qty",
            ))
            continue
        rate = _bars_quote_usd_rate(conn, quote_ccy)
        if rate is None or rate <= 0.0:
            out.append(RestampResult(
                position_id=position_id, venue=venue, symbol=symbol,
                quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=None,
                skipped_reason=f"no {quote_ccy}->USD rate (no conversion-pair bars)",
            ))
            continue
        new_risk_usd = risk_usd_at_entry(
            entry_price=entry_price,
            entry_atr_pct=float(entry_atr_pct) if entry_atr_pct is not None else 0.0,
            base_qty=float(qty), stop_atr_mult=STOP_ATR_MULT,
            quote_usd_rate=rate,
        )
        out.append(RestampResult(
            position_id=position_id, venue=venue, symbol=symbol,
            quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=new_risk_usd,
        ))
    return out


def apply_restamps(conn: sqlite3.Connection, results: list[RestampResult]) -> int:
    """Write the corrected ``risk_usd`` for every resolved (non-skipped) result."""
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in results:
            if r.skipped_reason is not None or r.new_risk_usd is None:
                continue
            conn.execute(
                "UPDATE positions SET risk_usd = ? WHERE position_id = ?",
                (r.new_risk_usd, r.position_id),
            )
            n += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restamp OPEN positions.risk_usd for the quote-ccy fix."
    )
    parser.add_argument("--db", required=True, help="path to the live sqlite DB")
    parser.add_argument(
        "--apply", action="store_true",
        help="write the corrected risk_usd (default: dry-run report only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    conn = sqlite3.connect(args.db, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        results = compute_restamps(conn)
        resolved = [r for r in results if r.skipped_reason is None]
        skipped = [r for r in results if r.skipped_reason is not None]
        for r in resolved:
            logger.info(
                "[restamp] %s %s:%s (%s) risk_usd %s -> %.4f",
                r.position_id, r.venue, r.symbol, r.quote_ccy,
                f"{r.old_risk_usd:.4f}" if r.old_risk_usd is not None else "NULL",
                r.new_risk_usd,
            )
        for r in skipped:
            logger.warning(
                "[restamp] SKIP %s %s:%s (%s) — %s",
                r.position_id, r.venue, r.symbol, r.quote_ccy, r.skipped_reason,
            )
        if args.apply:
            n = apply_restamps(conn, results)
            logger.info("[restamp] APPLIED — %d position(s) updated, %d skipped", n, len(skipped))
        else:
            logger.info(
                "[restamp] DRY-RUN — %d position(s) would update, %d skipped "
                "(pass --apply to write)",
                len(resolved), len(skipped),
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
