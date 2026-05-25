---
type: runtime
status: active
date_created: 2026-05-06
date_updated: 2026-05-08
tags: [now, tier-0]
---

# Polaris _NOW (Tier 0 — read first)

## What matters now (HAND-WRITTEN)

Phase -1 (하네스 build) **완료**. Phase 0 (8 layer codex harden-up) **완료** (2026-05-06): L0~L7 codex round 1 합의 + `vault/30_components/layer-0..7-*.md` 8 spec write. 거부 키워드 0건.

**P0 Day 1 완료 (2026-05-06 + codex R1 fix 2026-05-07)**: Layer 0 (Dynamic Universe) + Layer 1 (Canonical + Baseline) implement.
- `polaris/core/universe/{schema, discovery, watchlist}.py` + `polaris/core/data/{schema, canonical, baseline, normalize}.py` + `polaris/storage/schema.py` (DDL bootstrap).
- **Codex R1 review = REJECT → 4 P0 blockers all fixed** (4-axis hard / Capital P0 categories whitelist / listing_ts wiring / asset_class fallback chain). Debate: `vault/50_research/debates/2026-05-06_p0_day1_codex_review.md`.
- 58 tests pass (37 L0 + 24 L1, 4 hypothesis property), ruff clean, mypy --strict clean.
- Smoke: OKX 182 + Capital 387 (P0 categories only) → 4-axis hard filter 24 (OKX only — Capital lacks vol/depth proxy at P0; Day 2 chart-endpoint task) → dynamic focus 24 (all listing_watch first cycle).

**P0 Day 2 완료 (2026-05-07)**: Layer 7 (isolation) + Layer 4 (cell_matrix) + Capital vol/depth proxy + Layer 3 sizing skeleton.
- `polaris/core/{isolation,cell_matrix,sizing}/` 11 new files ~1750 LOC + `polaris/venues/capital/market_proxy.py` + storage/schema.py 확장 (cell_matrix_*, strategy_halts, allocator_reservations, order_intents, positions/orders/risk_events).
- **Codex R1 R2 R3 review**: REJECT 4 P0 (warmup dead-code / linear parent decay / non-monotonic severity / RISK_ONLY skip) → REJECT (no prod caller) → **APPROVE** (Layer 3 sizing seam wires `apply_cell_routing_mult`).
- 143 tests pass (143 = 58 Day 1 + 32 cell_matrix + 28 isolation + 11 capital_proxy + 7 sizing + 7 추가 monotonic/RISK_ONLY regression), mypy strict + ruff clean.
- Smoke: OKX 182 → filter 19-30 (intra-session); Capital raw 454 (forex/indices/commodity/crypto P0) → 16 typical 4-axis pass with proxy (forex 3 + indices 9 + commodity 2 + crypto 2; peak 95 during active session); cell routing dist top 7 / bottom 7 / mid 11; allocator fence asyncio race PASS.

**P0 Day 3 완료 (2026-05-07)**: Layer 2 per-gate pipeline (8-gate orchestrator + 4 Haiku gates) + Layer 3 T4 full + Layer 1 ingest. 225 tests pass.

**P0 Day 4 완료 (2026-05-07)**: Layer 5 (3 P0 learners + adaptive_learner_attack triple block + hourly commit + snapshot rollback) + Layer 6 stubs (tick recalc dirty mark + regime flip 2-consecutive + strategy swap max-1/trade + conviction stacking) + 7 strategies signal-generator port + schema additions.
- `polaris/core/learners/{base,session,regime,max_hold,scheduler}.py` + `polaris/core/live_recalc/{tick_recalc,regime_flip,strategy_swap,conviction}.py` + `polaris/strategies/{base,volume_burst,tsmom,rsi_bb_pullback,spot_donchian,fx_breakout_basket,xau_indices_trend,session_breakout}.py`.
- vault/20_strategies/ 7 신규 spec; layer-5/6 spec 기존 유지.
- **Codex L4 R1 REJECT_WITH_FIXES → all 4 fixed (P0 max_hold baseline 1.0→expected_holding_bars / P1 commit_hourly atomicity reads-in-tx / P1 strategy_swap venue/symbol/side check / P2 regime initial_seed confirmed=False) → R2 APPROVE**.
- 56 new tests = 281 total pass; mypy strict + ruff clean.
- Smoke: 7/7 strategies emit RawSignal · 3 learner Δ live · live_recalc 3 dirty + regime confirmed_2x + 2 swap decisions.

**P0 Day 5 완료 (2026-05-07)**: Venue adapters full + fill normalizer + paper loop smoke + dashboard v0.
- OKX `signing.py` (HMAC-SHA256) + `OKXAdapter` (IOC px clamp 5bps, clOrdId sanitize, balance/positions/orders endpoints) + `constraint_translator.py` (lotSz/minSz/tickSz).
- Capital `session.py` (CST + X-SECURITY-TOKEN, 9-min anti-idle, 401 auto-refresh, 1/s rate cap) + `CapitalAdapter` (open/workingorder/close/list/confirm) + `constraint_translator.py` (min_deal_size/step/leverage).
- `polaris/core/data/fill_normalizer.py` — unified `Fill` + OKX/Capital normalizers (with leverage param).
- Scripts: `smoke_paper_loop.py` (519 LOC) + `_smoke_fills.py` + `_smoke_real_probes.py` + `_smoke_haiku_stub.py` + `dashboard_v0.py` (369 LOC).
- 5 new test files (53 tests) + 2 codex regression tests.
- **Codex Day 5 review = REJECT_WITH_FIXES → 2 P1 fixed** (Capital ts naive→UTC + Capital fill leverage in notional). 1 P2 deferred (`update_baseline_from_bars` iterator — Day 1 code).
- 339 pytest pass · mypy strict 86 files clean · ruff clean.
- **Real demo trades confirmed**: OKX BTC-USDT IOC filled 0.00012262 BTC at $81,514 (ordId 3542763044948807680). Capital EURUSD 100-lot open+close at level 1.1752.

**P0 Day 6 완료 (2026-05-07)**: Full pipeline G3-G7 plumbing + fills ledger + P1.0 ignition wire + Day 1 P2 fix.
- `polaris/core/pipeline/payload_builder.py` (5 builders for G3/G4/G5/G6/G7) + `polaris/core/data/fills_persist.py` (idempotent persist + recent-fills query) + `polaris/scripts/_smoke_real_roundtrip.py` (OKX + Capital demo round-trip with dry-run mode) + `polaris/scripts/ignite_p1.py` (P1.0 entry point) + `polaris/scripts/smoke_day6_full_pipeline.py`. `fills` DDL + 3 indexes added to `polaris/storage/schema.py`. Day 1 P2 fix: `update_baseline_from_bars` materializes iterator + recompute anchored at batch max_ts per (instrument, group).
- **Codex R1 R2 R3 review**: REJECT (3 P0 + 2 P1 + 1 P2: paper-mode db_path / OKX close base_ccy semantics / round-trip ok=True without close persisted / baseline batch max_ts / orders fallback nonexistent column / persist_bars contract) → REJECT_WITH_FIXES (2 P1: outer try/except swallowed close-leg exceptions without step + open_fill_id) → **APPROVE**.
- 385 pytest pass (+46 new), mypy strict 91 files clean, ruff clean.
- Smoke 6s 2-tick: 9 full_pipeline_runs · 9 sized (100%) · gate_pass_counts={6: 9, 7: 9} · 12 fills_persisted from sized + 4 round-trip = 16 total · 3 closed_trades · custom db_path honoured · vault appended.
- Dashboard panel reads from `fills` table directly (size_usd / fee / slippage / pnl_usd / is_close).

**P0 Day 7 완료 (2026-05-07)**: 30-min ignition smoke + 24h watchdog readiness.
- `tests/test_day7_ignition_health.py` (+680 LOC, 18 probes) — auth/isolation/fills/dashboard/kill-switch/drawdown-gap/composite + `polaris/scripts/_smoke_real_roundtrip.py` `resolve_okx_base_url` extraction (urlparse-based, sub-domain bypass-safe).
- **Codex R1 R2 R3 review**: REJECT_WITH_FIXES (3 P0 + 4 P1 + 2 P2: kill-switch lie / composite-learner lie / vault leak / env override static / drawdown weak / cell_total vacuous / race-prone / sqlite leaks) → REJECT_WITH_FIXES (2 P0 + 4 P1: rc=1 too permissive / NOW.md leak blind spot / env override still static / drawdown gap weak / cell_total vacuous / cancel race) → **APPROVE_WITH_NITS** (3 NITS all addressed: drawdown docstring narrowed / urlparse netloc-equality / integration-coverage indirect accepted).
- 406 pytest pass (+21 = 18 day7 + 3 incidental), mypy strict 92 files clean, ruff clean.
- Real-demo 30-min smoke: 736 fills (184 closed) · $133,695 notional · +$167 PnL · 0 pipeline kills · 3 active cells (n_eff=63-65 each) · 4 learner types × 15+ keys · 3 hourly snapshots.

**현재 단계**: P0 Day 7 완료 → P1.0 ignition fired (PID 57257, `--paper --duration 86400 --tick 5 --full-pipeline --real-roundtrip`).

**P0 sprint coherence verdict (2026-05-07 08:37, codex gpt-5.4 R1 = CONDITIONAL PASS)**: primitives complete (415/415, ruff+mypy clean, real demo trades) but **4 P0 cross-layer integration gaps + 7 P1** detected by cumulative review. Day-by-day reviews missed these because each Day reviewed self-consistency, not whole-stack assembly. ignite_p1 keeps running — fixes restart-only.
- **P0 blockers (Day 8 PR — bundle together)**: A2 AllocatorFence not wired in submit path / A3 supervisor + circuit_breaker not wired (raw `asyncio.create_task`) / A5 Layer 0 disconnected from dispatch (`FOCUS` hardcoded) / A6 Layer 1 ingest not driven (`bars/positions/orders/signals/quote_ticks` all 0 rows after 11min, hard caps non-binding because position_risk_state empty).
- **P1 (this week)**: A1 session×regime not multiplied into T4 / A4 Layer 6 dirty sweep not running / A7 regime+session hardcoded vacuous / A8 `emitted[:3]` cap throttles 7→3 strategies / X1 max_hold unwired / X2 idempotent order keys unwired / X3 G6 swap predicate not Layer 6 SSOT.
- **Refuted**: C1 Kelly-not-multiplied = doc clarification only (ADR-005 §Priority already says Kelly governs cap, not chain factor).
- **Aggressive bias**: 0 defensive throttles introduced, all 4 P0 fixes preserve hard MAX `headroom_min()` 1-call contract + cell mult clip-전 placement + top ×1.5 amplify + G8 P0/P1 split.
- Digest: [[2026-05-07_p0_sprint_cumulative_review]] (40_ops/digests).

## What changed since last session (HAND-WRITTEN)

- 2026-05-06 reset: clean slate (모든 v1 코드 삭제, tag pre-reset-2026-05-06 archive)
- 4 round codex 디베이트 (round 1 ROLLBACK demo context 누락, round 2 demo unlock, round 3 4 critical/high, internal review + Jin sign-off)
- Jin clarification 21:30: per-gate AI + dynamic universe + 자가 진화 (active autonomous vision, [[active-autonomous-vision]])
- Memory 정리: 108 → 27 (81 archive)
- .env 통합 (OKX US demo + Capital CFD demo + AI providers)
- OKX 401 root cause = base URL `www.okx.com` (international) → `us.okx.com` (US region) (`feedback_okx_region_endpoint` — global memory)
- **Phase 0 완료 (2026-05-06)**: 8 layer codex 디베이트 (gpt-5.4, round 1 each) + vault/30_components/ 8 spec write. ADR-005 patch 권고 (top mult ×1.3 → ×1.5). raw fragments `/tmp/polaris_phase0/L*_r1_response.md`.

## Pending decisions (HAND-WRITTEN)

- [x] Phase 0 codex round 8 (L0~L7) — 2026-05-06 완료
- [x] **ADR-005 patch** (file patched 2026-05-06): §Cell Routing Mult `top ×1.3` → `×1.5` 적용 ✓ (audit 2026-05-07 verify, [[2026-05-07_p1_full_audit]])
- [x] **ADR-006 patch** (file patched 2026-05-06): warmup shrinkage 5-19 + EWMA decay 7d + dynamic quartile activation gate (≥20) 적용 ✓ (audit 2026-05-07 verify)
- [ ] Plan v5 `valiant-baking-sutton.md` detail 통합 update (선택)
- [x] P0 Day 1 완료: Layer 0 dynamic universe + Layer 1 canonical/baseline (2026-05-06)
- [x] P0 Day 2 완료: Layer 7 isolation + Layer 4 cell_matrix + Capital proxy + Layer 3 sizing skeleton (2026-05-07)
- [x] P0 Day 3: Layer 2 per-gate skeleton + 4 Haiku gates + Layer 1 ingest wiring + Layer 3 full T4 (2026-05-07)
- [x] P0 Day 4: Layer 5 learner network + Layer 6 live recalc stub + 7 strategies signal-generator port (2026-05-07)
- [x] P0 Day 5 완료: venue adapters full + fill normalizer + paper loop smoke + dashboard v0 + codex review (2026-05-07)
- [x] P0 Day 6 완료: full pipeline G3-G7 plumbing + fills DDL + ignite_p1 + Day 1 P2 fix + codex R1 R2 R3 review APPROVE (2026-05-07)
- [x] P0 Day 7 완료 (24h watchdog 18 probes APPROVE_WITH_NITS) + P1.0 ignition fired PID 57257 (2026-05-07)
- [x] P0 sprint cumulative coherence review (2026-05-07): codex APPROVE w/ 4 P0 + 7 P1 wiring debt
- [ ] **Day 8 P0 PR (one bundle)**: A2 AllocatorFence wire / A3 supervise_strategies + record_fault wire / A5 ignite_p1 dynamic focus inject / A6 per-tick ingest_bars + persist positions/orders/risk_state
- [ ] Day 8+ P1: A1 session×regime in T4 / A4 Layer 6 dirty sweep / A7 regime+session SSOT / A8 emitted[:3] cap removal / X1 max_hold consume / X2 idempotent order keys / X3 G6 swap → Layer 6 SSOT
- [ ] Day 8+ P2 docs: ADR-005 Kelly clarification / sprint-complete digest test name update / `tests/test_integration_p0_pipeline.py`
- [ ] Vault `regrets/` 폐기 confirm (B'+D+C 대체 — [[ADR-002]])
- [ ] Live 진입 결정 = 별도 ADR (본 plan 책임 X, Jin 단독)
- [x] **Vault audit P1 wave 1 (2026-05-07)**: 199→0 lint issues, vault_lint hardened, post_trade_reflector frontmatter inline, log.md 261-dupe collapse, strategy backlink density fix → digest [[2026-05-07_p1_vault_audit]]
- [ ] Day 9+ vault backlog: 8 component spec split (≤60 line summary + impl/decisions sub-pages) / start_dashboard hook 1-min dedup / ADR-007 provenance back-fill / vault-curator agent pattern (per-Day dispatch)
- [x] **Day 9 P0 quad bundle (full-audit 2026-05-07 [[2026-05-07_p1_full_audit]])**: F1+F2 G6/G7 GPT P1 wire + per-tick re-invocation + close_specific_position (FIFO 폐기) + G8 phase=P1 default — done 2026-05-07. F10 timeframe + F11 supervise + F12 equity SSOT — done 2026-05-07.
- [x] **Day 9 24h production loop completed (2026-05-08)**: G6 GPT 27,003 / G7 GPT 20,833 / G8 GPT lessons 1,917 / live_recalc exit_now 95,778 widen 10,645 / OKX PnL +$599.43 / cell pool 201 / fence reservations 5,616. Audit: [[2026-05-08_p1_day9_24h_full_audit]] + [[2026-05-08_p1_day9_24h_audit_detail]].
- [ ] **Day 10 P0 from 24h audit**: Capital fills 0 silent drop (55 SIZED→55 reservation→0 fills, 0 fault_events) — trace simulate_open_fill→persist_fill for venue=capital
- [ ] Day 10 P1 from 24h audit: fx_breakout_basket 0 signals all-time / xau_indices_trend US100 ticker mismatch / G3 KILL ratio 73% (target 50%, Variant B v2 + cell_score evidence)
- [ ] Day 10+ P2: F6 persist signals/orders/quote_ticks / fault_events table empty vs counter 153 reconcile / F4 A1 session×regime in T4 / F8 ignite_p1 bootstrap dedup hook

## Auto-generated (DO NOT EDIT BELOW)
<!-- AUTO-START -->
- Latest daily: (none yet)
- Open incidents: 0
- Recent ADRs (last 7d): [[ADR-001]] [[ADR-002]] [[ADR-003]] [[ADR-004]] [[ADR-005]] [[ADR-006]] [[ADR-007]] [[ADR-008]]
- Top touched (7d): vault/10_decisions, .claude/agents, .claude/skills, .claude/hooks, tools/vault_lint.py
<!-- AUTO-END -->

## Active plan
- Main plan v5: `/Users/jinyoon/.claude/plans/valiant-baking-sutton.md`
- Detail spec: `/Users/jinyoon/Projects/Polaris/.claude/plans/polaris_v2_plan_final.md` (520줄, 일부 superseded)
- 8-layer architecture: see [[ADR-003]]
- Per-gate AI pipeline: see [[ADR-004]]

## Implementation status
- P1.0 ignition fired at 2026-05-25 23:46 (paper=False, full_pipeline=True)
