"""Day 8 production paper loop — per-run loop state counters.

``ProdLoopState`` split out of ``production_paper_loop`` so the tick body
(``_production_tick``) and the main loop can both reference it without a
circular import. ``production_paper_loop`` re-exports it so existing import
paths (``from polaris.scripts.production_paper_loop import ProdLoopState``)
keep working — including the TYPE_CHECKING-only references in the production
sub-modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from polaris.scripts._smoke_fills import SimulatedTrade


@dataclass(slots=True)
class ProdLoopState:
    """Per-run counters for the production paper loop (no fixtures)."""

    fills_open: int = 0
    fills_close: int = 0
    open_trades: list[SimulatedTrade] = field(default_factory=list)
    closed_trades: list[SimulatedTrade] = field(default_factory=list)
    pipeline_runs: int = 0
    pipeline_kills: int = 0
    sized_count: int = 0
    # P1 re-entry cooldown skips: duplicate opens on the same
    # (venue, symbol, strategy_id) suppressed inside the cooldown window.
    # Turnover-cost telemetry only — never a P&L halt or size dampen.
    reentry_skips: int = 0
    fence_reservations: int = 0
    fence_conflicts: int = 0
    idempotency_conflicts: int = 0
    fault_events: int = 0
    # Venue-reject telemetry (Task 2 / D1): per-code count of EXTERNAL venue
    # rejects (compliance 51155 / balance 51008 / no_fill ...) that were
    # released WITHOUT a strategy fault. Observability only — never a throttle.
    venue_rejects_by_code: dict[str, int] = field(default_factory=dict)
    # Close-leg venue rejects (external — min-order 51020 / compliance / market
    # closed) released WITHOUT a strategy fault; position preserved + retried.
    venue_close_rejects: int = 0
    g1_runs: int = 0
    g2_emits: int = 0
    g8_runs: int = 0
    bars_persisted: int = 0
    bars_baseline_samples: int = 0
    universe_refreshes: int = 0
    capital_refreshes: int = 0
    # F11 — Day 9: Layer 7 SSOT supervisor counters (TaskGroup-managed).
    supervised_tasks_total: int = 0
    supervised_tasks_failed: int = 0
    # F10 — Day 9: per-timeframe ingest tracking. ``last_fetch_monotonic_by_tf``
    # gates per-timeframe fetches at their cadence (1m every tick / 15m 60s /
    # 1H 5min). ``bars_persisted_by_tf`` is logged per summary so smoke can
    # verify Capital strategies receive their 1H bars.
    last_fetch_monotonic_by_tf: dict[str, float] = field(default_factory=dict)
    bars_persisted_by_tf: dict[str, int] = field(default_factory=dict)
    signals_by_tf: dict[str, int] = field(default_factory=dict)
    # F1+F2 — Day 9: live recalc loop counters (G6/G7 per-tick GPT calls).
    recalc_g6_calls: int = 0
    recalc_g7_calls: int = 0
    recalc_widen_applied: int = 0
    recalc_exit_now: int = 0
    recalc_swap: int = 0
