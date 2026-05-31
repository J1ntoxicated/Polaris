"""Lineage read-model (P3 self-evolve foundation).

Records ticker ↔ strategy ↔ exit lineage of every position. Read-model only —
live trading never reads it; recording is INSERT/UPDATE-only (behaviour 0).
"""

from __future__ import annotations

from polaris.core.lineage.segments import (
    ENTRY_REASON_OPEN,
    LineageSegment,
    build_cell_key,
    lineage_for_cell,
    recent_lineage,
    record_segment_close,
    record_segment_open,
)

__all__ = [
    "ENTRY_REASON_OPEN",
    "LineageSegment",
    "build_cell_key",
    "lineage_for_cell",
    "record_segment_close",
    "record_segment_open",
    "recent_lineage",
]
