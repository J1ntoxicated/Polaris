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
from typing import Any

from polaris.core.pipeline.g1_focus_gate import G1FocusCache
from polaris.core.pipeline.g6_call_gate import G6CallCache
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
    # Meta-labeling (#10) — triple-barrier labels recorded (collection-only).
    meta_labels: int = 0
    bars_persisted: int = 0
    bars_baseline_samples: int = 0
    universe_refreshes: int = 0
    capital_refreshes: int = 0
    alpaca_refreshes: int = 0
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
    # #26 precise-exit engine — count of positions closed by the deterministic
    # adaptive-exit pass (ATR-trail stop / protected break-even / loser
    # timeout). EXPECTANCY telemetry only — never a size dampen or entry block.
    recalc_precise_exit: int = 0
    # #15 — G6 GPT call-efficiency: per-position cooldown/context cache + a
    # counter of ticks where the prior GPT decision was reused (no call).
    recalc_g6_skipped: int = 0
    g6_call_cache: G6CallCache = field(default_factory=G6CallCache)
    # G1-EFF — G1 GPT call-efficiency: shared on-change-only focus cache + a
    # counter of pipeline runs where the cached focus was reused (no G1 GPT
    # call). The focus DECISION is still GPT-chosen; only the call FREQUENCY
    # drops while the universe composition is unchanged. Cost telemetry only —
    # G1 still always PASS, no entry blocked.
    g1_focus_skipped: int = 0
    g1_focus_cache: G1FocusCache = field(default_factory=G1FocusCache)
    # T13 — us_equity_cal RTH integrity gate (Track C / Alpaca equity ONLY;
    # OKX always_on + Capital fx_indices_cal are NEVER gated → A/B identical).
    # ``equity_session_holds`` counts NEW equity entries HELD because the US
    # equity market was closed (outside 13:30-20:00 UTC RTH). This is an
    # INTEGRITY hold (the venue would reject a closed-market order), NOT a P&L
    # throttle and NOT a size dampener — existing positions are never touched.
    equity_session_holds: int = 0
    # T13 — PDT rolling-day day-trade count (sourced from Alpaca
    # /v2/account.daytrade_count via parse_account_pdt; defaults 0 until a live
    # account read populates it). ``equity_pdt_rank_downs`` counts equity entries
    # that were RANKED DOWN (lower priority) because daytrade_count >= 3 — NOT
    # blocked. Overnight holds are free; there is no P&L halt, no entry veto.
    pdt_daytrade_count: int = 0
    equity_pdt_rank_downs: int = 0
    # #6 — alt-data EVIDENCE producer counters. ``altdata_refreshes`` = successful
    # non-empty collector fetches that updated the cache + snapshot; ``altdata_errors``
    # = collector exceptions swallowed (last cache kept). SIGNAL/EVIDENCE only —
    # these never drive sizing/blocking/exits; telemetry for the dashboard.
    altdata_refreshes: int = 0
    altdata_errors: int = 0
    # Capital rotation (Jin 2026-05-30) — finite-capital opportunity-cost
    # redeploy. A NEW signal blocked *for a capital reason* (entry_sizer
    # ``sizing_zero`` on a binding cap, or OKX ``insufficient_balance``/51008) is
    # pushed here as a rotation CANDIDATE carrying its conviction-derived
    # ``proposed_risk_pct`` (the capital scale only). Cleared every tick after the
    # rotation pass. rotation = capital EFFICIENCY, NOT a defensive throttle:
    # every fire pairs with a concrete pending entry so net deployed capital goes
    # UP; winners (exit_state protected/harvest) are never touched; no P&L halt;
    # no T4 multiplier (9-stack ban intact). DEMO/PAPER only.
    rotation_candidates: list[dict[str, Any]] = field(default_factory=list)
    # Per-rotation telemetry (REQUIRED before any live run). One record per fire:
    # victim_id / victim_fwd_R / victim_pnl_r / e_new / e_held / margin / cost +
    # same_symbol_reopen_count + rotations_this_hour. Observability only.
    rotations: list[dict[str, Any]] = field(default_factory=list)
    # Vacated-side anti-churn: a just-rotated victim (venue, symbol, strategy) →
    # cooldown-until ts. The reentry backdoor (strong-signal exempt) is CLOSED
    # for these names so the freed capital is NOT immediately re-spent on the
    # name we just exited (cross-verify item 4). No strong-signal escape hatch.
    rotation_vacated_cooldowns: dict[tuple[str, str, str], int] = field(
        default_factory=dict
    )
