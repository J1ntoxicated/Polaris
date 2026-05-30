---
type: debate
status: resolved
date_created: 2026-05-29
tags: [debate, codex, venue-integration, sizing, circuit-breaker, focus, data-collection]
---

# Debate — Venue-Integration Unblock design (codex R1)

Plan: [[venue_integration_unblock_2026-05-29]] (`.claude/plans/`). Topic: 5 money-path design decisions to unblock trustworthy DEMO data collection. codex gpt-5-codex round 1.

## Trigger
Diagnostic real run (data/diag.sqlite) showed the bot is NOT signal-starved — it is venue-reject-paralyzed: OKX $65k equity but $2,211 liquid USDT (rest = orphan alt bags); $1,244 orders reject 51008; GAS/TRUMP reject 51155 (US compliance); 3 rejects → circuit breaker SOFT_HALT tsmom → trading stops. G3 kills=0 (the "73%" was stale). fx_breakout majors never in the global top-30 focus.

## codex R1 verdict: proceed-with-fixes (all 5 SAFE=conditional except liquidation=yes; rejection-keyword sweep clean, 0 hits)

**D1 no-fault on venue reject** — conditional. Keep faulting INTERNAL/client rejects (bad size/symbol/idempotency/adapter-contract); exempt only external (51155/51008/transport). Add `venue_rejects_by_code` telemetry so "no fault" ≠ invisible.

**D2 compliance blocklist** — conditional. `UNIQUE(venue,symbol)` + UPSERT (reason/code/first_ts/last_ts/count); load at boot; guard in-memory set with lock or make DB SSOT (concurrent fan-out = worker.py:294 TaskGroup); permanence only for 51155, TTL/reprobe for transient.

**D3 equity reconcile** — conditional. **CALL-SITE CORRECTION**: sizing equity is read via `production_default_equity_usd()` in `polaris/scripts/_production_run_signal.py:123-150` → `build_sizer_payload`, NOT `_production_pipeline.py` (which only aliases the constant). Thread `equity_usd_by_venue` through `_run_tick → run_pipeline_for_signal → build_sizer_payload`. Concurrent orders size off the same balance snapshot (fence serializes order-key, not cash) → subtract pending reservations / per-tick cash accounting. Floor applies to min-order-viability only, not inflating `PortfolioState.equity_usd`. On fetch fail → stale-last-known + age telemetry, not silent constant.

**D4 focus pinning** — conditional. Make pinned ADDITIVE within FOCUS_TARGET_MAX=48 (don't replace dynamic top-N); de-dupe; raise FOCUS_CYCLE_TARGET + get_focus_targets(max_n) consistently; add ingest/429 telemetry.

**D5 orphan liquidation** — SAFE=yes. Not required for correctness (D3 sizing off USDT stops 51008) but fastest USDT recovery; dry-run printing ccy/availBal/est USD/tradeability/skip-reason; skip compliance + dust.

**Priority**: 3 → 1 → 2 → 4 → 5 (equity before no-fault, else 51008 = invisible churn). Missing-for-trustworthy: reject telemetry by code, internal-reject fault path, concurrent cash accounting, boot blocklist load, Capital 429 telemetry, post-fix assertions vs fresh diag DB.

## Sequencing insight (Claude)
Liquidate orphans FIRST → liquid USDT ≈ $65k ≈ $79k model → 51008 mostly vanishes without the D3 code change. So tonight's critical path to data flow = liquidate + D1 + D2; D3 (equity reconcile) + D4 (pinning) follow as hardening.

## Next
Incorporate fixes into plan; implement seq with TDD + codex code review (R2 = builder≠reviewer gate on actual code). 2× codex consensus = Jin sign-off (autonomous mode).
