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

from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.pipeline.g1_focus_gate import G1FocusCache
from polaris.core.pipeline.g6_call_gate import G6CallCache
from polaris.scripts._production_capital_sizing import CapitalConstraintCache
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
    # Per-position consecutive IN-SESSION close-reject tally. A position whose
    # venue close keeps failing while the market is OPEN (the deal expired / was
    # auto-closed on the demo) is a ZOMBIE — drained to status='reconciled' once
    # this passes a conservative limit so the exit engine stops retrying forever.
    # Reset on a successful close; off-session rejects do NOT increment it.
    close_reject_counts: dict[str, int] = field(default_factory=dict)
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
    # Probe TIGHTEN consumer (POLARIS_G6_PROBE_TIGHTEN) — count of G7-tightened stops
    # persisted (precise exit TIMING; never a block/size cut).
    recalc_tighten_applied: int = 0
    recalc_exit_now: int = 0
    recalc_swap: int = 0
    # #26 precise-exit engine — count of positions closed by the deterministic
    # adaptive-exit pass (ATR-trail stop / protected break-even / loser
    # timeout). EXPECTANCY telemetry only — never a size dampen or entry block.
    recalc_precise_exit: int = 0
    # Phase 3 per-stream session-close RAIL — count of positions force-flattened
    # because the venue's session/weekend close was imminent (CALENDAR
    # INTEGRITY, TIME-only). always_on (A) NEVER increments this → A identical.
    # Never a P&L throttle / size dampen / entry block.
    recalc_session_forced_exit: int = 0
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
    # Observability only (logging-observability 2026-05-31): the LAST observed
    # us_equity_cal session-open state, keyed per venue, used SOLELY to emit a
    # one-shot INFO when the equity trading session flips open↔closed (RTH
    # boundary). None until first observed. NEVER read by any entry/exit/sizing
    # decision — equity_session_entry_hold's gate logic is unchanged.
    equity_session_open_by_venue: dict[str, bool] = field(default_factory=dict)
    # #6 — alt-data EVIDENCE producer counters. ``altdata_refreshes`` = successful
    # non-empty collector fetches that updated the cache + snapshot; ``altdata_errors``
    # = collector exceptions swallowed (last cache kept). SIGNAL/EVIDENCE only —
    # these never drive sizing/blocking/exits; telemetry for the dashboard.
    altdata_refreshes: int = 0
    altdata_errors: int = 0
    # STEP① static-ground coverage producer counters (2026-06-25). The background
    # fill walks the WHOLE active universe so every active ticker gets bars +
    # per-ticker sentiment/event ground (the candidate-sweep ② input), not just the
    # focus subset. ``static_ground_instruments``/``_tickers`` = last cycle's
    # covered counts; ``_bars`` = cumulative bars persisted; ``_cycles``/``_errors``
    # = walk telemetry. OBSERVATION coverage only — never drives sizing/blocking/exits.
    static_ground_instruments: int = 0
    static_ground_tickers: int = 0
    static_ground_bars: int = 0
    static_ground_cycles: int = 0
    static_ground_errors: int = 0
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
    # Component B (2026-05-31) anti-churn novelty tracking. Per
    # (venue, symbol, strategy_id) key → the (created_at_bar, side) of the LAST
    # actually-submitted entry. The re-entry cooldown is exempted ONLY when the
    # current signal is NOVEL vs this (a NEW strategy-timeframe bar OR a side
    # flip — is_novel_reentry); raw signal strength NEVER exempts. In-process,
    # same precedent as ``rotation_vacated_cooldowns``. Updated when an entry is
    # actually submitted (reserve_and_submit returned a trade). Blocking a
    # same-bar same-side re-buy is PRECISION (surgical-strike), not a throttle.
    last_entry_by_key: dict[tuple[str, str, str], tuple[int, str]] = field(
        default_factory=dict
    )
    # Bug C — per-epic Capital dealing-rule cache (TTL/single-flight/stale-serve)
    # so the T4 notional is translated to venue lots with ZERO network calls on
    # a warmed epic. ``capital_constraint_fallbacks`` counts orders that fell
    # back to the legacy 1-lot path (constraint/rate unavailable) — the entry
    # still flows (flow_not_block); the counter keeps the gap visible.
    capital_constraints: CapitalConstraintCache = field(
        default_factory=CapitalConstraintCache
    )
    capital_constraint_fallbacks: int = 0
    # P4 — WS real-time price source (shared QuoteTickWriter, in-mem live_px +
    # ~30-tick ring). Consumers read fresh WS ticks (exit mark #2, G4 tick_window
    # #3) and fall back to bar close when no fresh tick exists (graceful degrade,
    # never halt). None until the loop wires it (smoke/replay paths leave it
    # None → pure bar-close behavior, behavior-identical to pre-P4).
    quote_writer: QuoteTickWriter | None = None
    # Alt-data EVIDENCE cache singleton (#6) — the SAME object fed to
    # compute_and_flip_regime (Layer 6 regime evidence). Shared onto state so the
    # Layer-0 focus producer can build the entrance-judge ``altdata_lean`` from the
    # already-fused tilt (audit code_review_2026-06-24). None until the loop wires
    # it (smoke/replay leave it None → the alt-data lens degrades to neutral).
    altdata_cache: Any = None
    # Timeframe-aligned ATR cache for the live recalc exit ruler:
    # (instrument_id, timeframe) → (atr_pct, computed_ts). TTL =
    # TIMEFRAME_FETCH_CADENCE_SEC[tf] (a fresh bar cannot arrive faster, so a
    # cached value is at most one fetch-cycle stale; the trail ratchet already
    # forbids loosening). Measurement/exit precision only — never sizing.
    tf_atr_cache: dict[tuple[str, str], tuple[float, int]] = field(
        default_factory=dict
    )
    # Gate→outcome counterfactual sweep throttle — monotonic ts of the last
    # forward-mark sweep (60s cadence, piggybacks the bar ingest). 0.0 = never
    # swept. Instrumentation only — never read by any trading decision.
    last_cf_sweep_monotonic: float = 0.0
    # Altdata hint instrumentation — IN-MEMORY counters only (hint_total /
    # hint_tilt_lost_to_price / hint_final_mismatch), accumulated by
    # compute_and_flip_regime and surfaced in the loop summary. The hint is a
    # redundant projection of the evidence scores compose already consumes;
    # per-call DB writes: 0 (focus×tick frequency — STALL-safe).
    regime_hint_stats: dict[str, int] = field(default_factory=dict)
    # ADR-012 Slice 1 — PROBE → ENGINE → TUNING-LOG sidecar (observe-only).
    # ``probe_conn`` is the SEPARATE ``data/probes.sqlite`` tuning-log handle (NOT
    # the live DB — zero WAL contention, mirrors the sentinel sidecar); None until
    # boot opens it (smoke/replay leave it None → the attach is a fail-open no-op).
    # ``probe_bus`` runs the four ROW-only probes with per-probe fault isolation;
    # ``probe_engine`` composes the would-be exit decision. In observe mode the
    # engine threads ZERO knobs into run_precise_exit (provably byte-identical).
    probe_conn: Any = None
    probe_bus: Any = None
    probe_engine: Any = None
    # Count of observe-mode probe evaluations logged (telemetry only; never a
    # throttle / size dampen / entry block).
    probe_observe_evals: int = 0
