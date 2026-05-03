"""vault_sync_full — orchestrator: db_views_export + targeted crosslink.

Jin 2026-04-26 cleanup: drop over-engineered preg + code module pages.
Lean vault: INSIGHT/ADR/digest/strategies/tickers/regimes/exit_patterns + symlinks.

Run: python -m tools.vault_sync_full
Cron: */20 * * * * python3 -m tools.vault_sync_full
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools._md_writer import now_utc


def _emit(msg: str, level: str = "sys") -> None:
    """Best-effort log_event emit + stdout fallback (LOG-MISS-006 2026-04-26).

    invasion.utils.events available → invasion.log 통합 (Harness ops view).
    Tools standalone mode → stdout only (cron `>> data/vault_sync.log`).
    """
    print(msg)
    try:
        from invasion.utils.events import log_event  # type: ignore
        log_event("VAULT_SYNC", msg.replace("[vault_sync_full] ", ""), level)
    except Exception:
        pass


def main() -> None:
    start = time.time()
    _emit(f"[vault_sync_full] {now_utc()} START")

    try:
        from tools.db_views_export import sync_db
        wm = sync_db()
        _emit(f"  [db_views] cells={wm['cells_count']} avg_score={wm['cells_avg_score']:.3f}")
    except Exception as e:
        _emit(f"  [db_views] ERROR: {e}", "warn")

    try:
        from tools.vault_crosslink import main as crosslink_main
        result = crosslink_main()
        _emit(f"  [crosslink] entities={result['entities_total']} "
              f"wikilinks={result['wikilinks_total']}")
    except Exception as e:
        _emit(f"  [crosslink] ERROR: {e}", "warn")

    elapsed = time.time() - start
    _emit(f"[vault_sync_full] {now_utc()} DONE ({elapsed:.2f}s)")

# Note: tools/preg_export.py + tools/code_ast_export.py are ON-DEMAND ONLY.
# Run manually when forensic needs (e.g., "which modules use hard_stop_pct?").
# Auto-sync them = over-engineering (Jin 2026-04-26 cleanup).


if __name__ == "__main__":
    main()
