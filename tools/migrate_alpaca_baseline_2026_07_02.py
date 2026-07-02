"""One-shot migration — fold the Alpaca display-baseline into the stamped
measurement-reset equity baseline (P0-2, Jin 2026-07-02).

``tools/migrate_stamp_reset_2026_06_27.py`` stamped the 2026-06-27 21:13
(Sydney) reset with ``equity_baseline_usd = demo_starting_equity_total()`` at
the time, which was OKX + Capital only ($130,000) — the Alpaca paper track had
no baseline in the header total then. P0-2 adds an Alpaca DISPLAY-baseline
($30,181.75) into ``demo_starting_equity_total()`` so the 3-venue header sums
correctly going forward; this migration UPDATEs the existing reset row's
``equity_baseline_usd`` in place (130,000.0 -> 160,181.75) so the since-reset
panel's baseline matches the new 3-venue total retroactively — no new reset
row, since the reset TIMESTAMP itself is unchanged (same forward window).

Idempotent — a no-op if the row is already at the new baseline. DEMO/PAPER;
observability only (measurement_resets is read only by the dashboard — never
sizing/gating/exit, per polaris/storage/measurement_reset.py).
"""

from __future__ import annotations

from pathlib import Path

from polaris.core.sizing.constants import demo_starting_equity_total
from polaris.storage.schema import init_db

RESET_TS = 1782594780  # same reset instant as migrate_stamp_reset_2026_06_27
OLD_BASELINE_USD = 130_000.0
NEW_BASELINE_USD = 160_181.75  # 130,000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD


def run(db_path: Path) -> int:
    conn = init_db(db_path)
    try:
        row = conn.execute(
            "SELECT equity_baseline_usd FROM measurement_resets WHERE reset_ts = ?",
            (RESET_TS,),
        ).fetchone()
        if row is None or float(row[0] or 0.0) == NEW_BASELINE_USD:
            return 0
        conn.execute(
            "UPDATE measurement_resets SET equity_baseline_usd = ? WHERE reset_ts = ?",
            (NEW_BASELINE_USD, RESET_TS),
        )
        conn.commit()
    finally:
        conn.close()
    return 0


def main() -> int:
    # Sanity: the new baseline must match the live SSOT total (fails loudly in
    # CI if the constant drifts from this migration's hard-coded expectation).
    assert abs(demo_starting_equity_total() - NEW_BASELINE_USD) < 1e-6, (
        f"demo_starting_equity_total()={demo_starting_equity_total()} != "
        f"expected {NEW_BASELINE_USD} — update this migration's constant"
    )
    return run(Path("data/polaris_live.sqlite"))


if __name__ == "__main__":
    raise SystemExit(main())
