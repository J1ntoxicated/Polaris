"""Layer-6 / lifecycle helpers.

``recover`` — session hydrate from persisted SQLite OPEN rows so paper-loop
restart does not strand prior-session positions (drives A-PR1 of the
2026-05-10 lifecycle-fix debate, forensic
``vault/50_research/forensic/2026-05-10_position_lifecycle_drift.md``).
"""
from polaris.core.lifecycle.recover import (
    hydrate_last_entry_by_key,
    hydrate_open_positions,
)

__all__ = ["hydrate_last_entry_by_key", "hydrate_open_positions"]
