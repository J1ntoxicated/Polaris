---
type: digest
status: archived
date_created: 2026-06-11
tags: [now-archive, handover]
---

# _NOW 아카이브 2026-06-11 p4/6 — 2026-05-28 venue wire + Day 8-10 backlog + P0 Day 1-4 완료 기록

(2026-06-11 _NOW 다이어트로 원문 무손실 이동 · 원본 [[_NOW]])

**2026-05-28 P0 venue wire fix + 라이브 증명**: 5-axis 검수가 P0 발견 — production paper loop 이 실제 demo 주문을 한 번도 안 보냄 (open/close 둘 다 simulate-only, `--real-roundtrip` 폐기). 기존 `data/polaris.sqlite` 17,259 fills 전부 가짜. builder TDD 로 wire 복구 (618 green) → **codex 외부 review 가 green 코드에서 실주문 안전 P0 5건 포착** (db 혼재 / reservation 누수 / orphan 실포지션 / Capital deal_id 유실 / pnl_r 오산) → 재수정, codex 재review 7/7 safe=yes → 627 green. **라이브 증명**: `ignite_p1 --real-roundtrip --db data/polaris_live.sqlite` 로 Capital demo 실왕복 (13 fills 실 dealId, fault 0 / orphan 0 / fence conflict 0). OKX live = 시그널 미발화로 미확인 (gap). silent-drop 2건 warning 추가. 교훈 = green ≠ safe, builder≠reviewer 실증 → [[ADR-010]] + [[t-p0-wire_2026-05-28]] + [[2026-05-28_5axis_audit]].

**Day 8-10 backlog 상태 (stale 해소)**: Day 8 P0 quad (AllocatorFence/supervise/dynamic focus/ingest) + Day 9 24h production (G6/G7/G8 GPT wire + live_recalc) 완료. Day 10 P1 L5→L3 sizing wire (session×regime×triple_block in T4) done 2026-05-26. Day 10 P0 Capital silent-drop = audit frame error 로 debunk (ts_ms -10h drift historical only). 현재 최우선 = 위 venue round-trip 활성화 완료, 남은 gap = OKX live 증명 + orphan scanner.

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

