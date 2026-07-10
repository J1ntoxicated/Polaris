"""fee-split v0 (group GROSS) — v0 gross_edge_proxy: percentile-map over the
EXISTING ``score_f_events`` population.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec
source: vault/50_research/debates/fee_split_judgment_2026-07-10.md — final
spec: "v0: 기존 score_f_events 399행으로 gross_edge_proxy percentile-map
(notional 컬럼 부재 — 진짜 bps 아님을 명시). v1: fills 조인으로 실
gross_bps/fee_drag_bps."

The bulk of ``score_f_events`` history predates the ``gross_usd`` /
``notional_usd`` additive columns (``schema_ddl_classes.py`` — new rows
only, see that module's docstring), so a real gross-bps figure
(``gross_usd / notional_usd * 10_000``) is not computable for most existing
rows. v0 therefore does NOT attempt to fabricate a bps number — it maps
each closed lifecycle's ``net_usd`` (== gross P&L; ``fills.pnl_usd`` is
already fee-EXCLUSIVE, price-movement-only — see ``score_f.py``'s
``compute_lifecycle_fee`` docstring) to its ECDF percentile rank (0-1)
within the track's own historical population. This is an explicit PROXY
scale, never mixed with a real bps threshold (the SCALE gate stays
un-wired in v0 — see ``scale_gate.py``'s module docstring).
"""

from __future__ import annotations

import sqlite3

from polaris.core.classes.gross_scorer import GrossEdgeSample
from polaris.core.classes.threshold_migration import ecdf_percentile

__all__ = ["compute_gross_edge_proxy"]


def compute_gross_edge_proxy(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str,
) -> list[GrossEdgeSample]:
    """Percentile-map every closed lifecycle in ``score_f_events`` for
    ``(venue, strategy_id)`` against that SAME track's own ``net_usd``
    population — in-sample ECDF (no leave-one-out; matches this project's
    existing rollup-scoping convention, e.g. ``f_track_cap``'s per-track
    median). Returns oldest-first, ready to feed ``compute_gross_lcb``.

    Empty history -> empty list (no crash, no fabricated proxy value).
    """
    rows = conn.execute(
        "SELECT net_usd, closed_ts FROM score_f_events "
        "WHERE venue = ? AND strategy_id = ? ORDER BY closed_ts ASC",
        (venue, strategy_id),
    ).fetchall()
    population = [float(r[0]) for r in rows]
    return [
        GrossEdgeSample(
            value=ecdf_percentile(float(net_usd), population),
            closed_ts=int(closed_ts),
        )
        for net_usd, closed_ts in rows
    ]
