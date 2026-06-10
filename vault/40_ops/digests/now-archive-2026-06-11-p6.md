---
type: digest
status: archived
date_created: 2026-06-11
tags: [now-archive, handover]
---

# _NOW 아카이브 2026-06-11 p6/6 — What changed / Pending decisions / Active plan (구 백로그)

(2026-06-11 _NOW 다이어트로 원문 무손실 이동 · 원본 [[_NOW]])

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
- [ ] Vault `regrets/` 폐기 confirm (B'+D+C 대체 — [[ADR-002-vision|ADR-002]])
- [ ] Live 진입 결정 = 별도 ADR (본 plan 책임 X, Jin 단독)
- [x] **Vault audit P1 wave 1 (2026-05-07)**: 199→0 lint issues, vault_lint hardened, post_trade_reflector frontmatter inline, log.md 261-dupe collapse, strategy backlink density fix → digest [[2026-05-07_p1_vault_audit]]
- [ ] Day 9+ vault backlog: 8 component spec split (≤60 line summary + impl/decisions sub-pages) / start_dashboard hook 1-min dedup / ADR-007 provenance back-fill / vault-curator agent pattern (per-Day dispatch)
- [x] **Day 9 P0 quad bundle (full-audit 2026-05-07 [[2026-05-07_p1_full_audit]])**: F1+F2 G6/G7 GPT P1 wire + per-tick re-invocation + close_specific_position (FIFO 폐기) + G8 phase=P1 default — done 2026-05-07. F10 timeframe + F11 supervise + F12 equity SSOT — done 2026-05-07.
- [x] **Day 9 24h production loop completed (2026-05-08)**: G6 GPT 27,003 / G7 GPT 20,833 / G8 GPT lessons 1,917 / live_recalc exit_now 95,778 widen 10,645 / OKX PnL +$599.43 / cell pool 201 / fence reservations 5,616. Audit: [[2026-05-08_p1_day9_24h_full_audit]] + [[2026-05-08_p1_day9_24h_audit_detail]].
- [x] **Day 10 P0 "Capital fills 0 silent drop" — diagnosed 2026-05-26** ([[2026-05-26_p0_capital_silent_drop_diagnosis]]): audit query frame 잘못 (ts_ms 필터가 -10h drift된 ts 못 잡음 — 실제 165 capital fills 정상 persist). 진짜 P0 = `fills.ts_ms` -36000s drift (Sydney AEST naive→UTC artefact, historical only). 현재 코드는 0 drift reproduce — 다음 24h paper run 으로 확정 close.
- [ ] **Day 10 P0 follow-up**: 다음 24h paper run 후 신규 capital fills 의 ts_ms drift verify (0 → close ; non-0 → remaining naive-ts path hunt)
- [x] **Day 10 P1 (session×regime in T4) — done 2026-05-26**: [[ADR-005-sizing-formula-cell-routing|ADR-005]] T4 chain 에 L5_product wire (`9d3c79d feat(L3) wire L5 learner mults`). plan: `.claude/plans/p0_l5_l3_sizing_wire.md`. 10 new tests, 609 suite pass, mypy strict + ruff clean.
- [ ] Day 10 P1 remaining: fx_breakout_basket 0 signals all-time / xau_indices_trend US100 ticker mismatch / G3 KILL ratio 73% (target 50%, Variant B v2 + cell_score evidence)
- [ ] Day 10+ P2: F6 persist signals/orders/quote_ticks / fault_events table empty vs counter 153 reconcile / F8 ignite_p1 bootstrap dedup hook
## Active plan
- Main plan v5: `/Users/jinyoon/.claude/plans/valiant-baking-sutton.md`
- Detail spec: `/Users/jinyoon/Projects/Polaris/.claude/plans/polaris_v2_plan_final.md` (520줄, 일부 superseded)
- 8-layer architecture: see [[ADR-003-8-layer-architecture|ADR-003]]
- Per-gate AI pipeline: see [[ADR-004-per-gate-ai-pipeline|ADR-004]]
