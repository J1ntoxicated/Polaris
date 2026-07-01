"""One-shot migration — stamp the 2026-06-27 21:13 (Sydney) live DB reset.

audit2 P0-2 ③: the forensic log (vault/log.md 2026-06-28 loss-forensic) records
"live DB reset 2026-06-27 21:13" (Sydney local — earliest post-reset fill is
2026-06-27 11:13:50 UTC) as the start of the CURRENT measurement window, but no
``measurement_resets`` row was ever stamped for it — the since-reset dashboard
panel silently fell back to all-time instead of anchoring on the real reset.

This backfills exactly ONE historical row at that ts. No fills/positions existed
at the reset instant (it was a DB wipe), so the baseline equity is the demo
starting capital (OKX + Capital), not a live-probed post-trade equity. Idempotent
— skips if a reset row already carries this ``reset_ts`` (never double-stamps).
DEMO/PAPER; observability only (measurement_resets is read only by the dashboard
— never sizing/gating/exit, per polaris/storage/measurement_reset.py).
"""

from __future__ import annotations

from pathlib import Path

from polaris.core.sizing.constants import demo_starting_equity_total
from polaris.storage.measurement_reset import stamp_measurement_reset
from polaris.storage.schema import init_db

RESET_TS = 1782594780  # 2026-06-27 11:13:50 UTC == 21:13 Sydney (AEST, UTC+10)
RESET_LABEL = "2026-06-27 21:13 live DB reset (audit2 P0-2 backfill)"
RESET_GIT_SHA = "178dbc0"  # vault/log.md 2026-06-26 deploy+reset commit


def run(db_path: Path) -> int:
    conn = init_db(db_path)
    try:
        existing = conn.execute(
            "SELECT 1 FROM measurement_resets WHERE reset_ts = ?", (RESET_TS,),
        ).fetchone()
        if existing is not None:
            return 0
        stamp_measurement_reset(
            conn, label=RESET_LABEL, git_sha=RESET_GIT_SHA,
            reset_ts=RESET_TS, equity_baseline_usd=demo_starting_equity_total(),
        )
        conn.commit()
    finally:
        conn.close()
    return 0


def main() -> int:
    return run(Path("data/polaris_live.sqlite"))


if __name__ == "__main__":
    raise SystemExit(main())
