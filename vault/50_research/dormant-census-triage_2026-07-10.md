---
type: research
status: active
date_created: 2026-07-10
tags: [audit, dormant, triage, wake-delete-keep, db-graveyard, orphans]
related: ["[[dormant-census-triage_2026-07-10-table]]", "[[dormant-census-triage_2026-07-10-wake]]", "[[dormant-census-triage_2026-07-10-delete]]", "[[store-graveyard-census]]", "[[ai-hooks-audit-verdict]]"]
---

# 동면 센서스 삼분류 판정 (2026-07-10)

> Opus 판정자. 4-축 센서스(db_graveyard·dead_modules·env_gated·unconsumed) 전 항목 → WAKE/DELETE/KEEP-DORMANT. Jin 기준: "이롭게 만들거나, 필요 없으면 없애서 안 헷갈리게". read-only(ro)·PID 737 무접촉. 07-02 감사([[ai-hooks-audit-verdict]]) 재검.

## 한줄
관찰 인프라는 넘치나 소비 레그가 비었다 — **깨울 건 3(승자 피라미딩·counterfactual reader·replay 패널), 없앨 건 8(중복/폐물/데이터소스 부재), 정당 대기 12(실-와이어·키·미발화 lifecycle).**

## 센서스 대비 핵심 정정 (07-02→07-10 재검, 코드 실측)
1. **benchmark_results/replay_runs = display-only 확정** — 센서스가 "G3 confidence 게이트 소비 배선"이라 했으나 오독. `confidence.py:109 replay_block` docstring **"NEVER touches a trading decision"**, 소비처 `snapshot.py:257`(대시보드 패널), `snapshot_models.py:427` "never feeds sizing/gating". → 게이트 아님, WAKE 효과는 **대시 패널 가동**뿐(LOW).
2. **knowledge_loop.py / calibration.py = 데이터소스 부재** — 각각 `FROM probe_decisions` / `FROM v_probe_outcomes·probe_readings`인데 **세 객체 전부 DB 미존재**(ro 확인). write-side 미빌드 → orphan reader = DELETE(센서스는 ai_lessons reader로 오인, 실제 코드는 probe_decisions read).
3. **market_events = regime_state 중복** — 라이브 regime SSOT는 `regime_state`(strategy_swap:258·entrance_leans·altdata가 소비). market_events는 같은 flip을 append하는 zero-reader 이벤트로그(43k행, ~3,200 INSERT/day WAL 단일-writer 경합). regime 신호 무손실 → DELETE.
4. **capital_reopen_pending = 라이브 lifecycle**(센서스 truncated "ca…" 항목) — writer/reader/DELETE 전부 `reopen_route.py`(라이브 `_production_pipeline.py:101` import). 0행 = 현재 재개 대기 Capital 포지션 없음 → 무덤 아님, KEEP-DORMANT.
5. **ai_lessons = 의도적 archival SSOT** — `post_trade_reflector.py:20,26` "SSOT raw data"(타 telemetry는 삭제, 이건 보존). 라이브 학습은 learner_posterior/cell EWMA 별도 경로. reader 0이나 저비용(40/day)·명시적 의도 → KEEP-DORMANT(wake=lessons 디제스트 reader 신설 시).

## 판정 집계
- **WAKE 5**(빌드그룹 3): gate_kill_counterfactuals·position_conviction_layers·benchmark_results+replay_runs·learner_prune(low)
- **DELETE 8**(빌드그룹 2): market_events·orders·REFINE_TIMING stamp·dashboard_v0·shadow_acceptance·normalize·knowledge_loop·calibration
- **KEEP-DORMANT 12**: ai_lessons·maker_fill_shadow·loop_rotation_events·learner_blocks·capital_reopen_pending·G6_PROBE_TIGHTEN·PROBE_ENGINE(trail_only/full)·okx/alpaca adapter·EOD_FLATTEN·ORPHAN_RECONCILE·RECONCILE_VENUE_IMPORT·coinglass/myfxbook
- **KEEP(의도적, 비-동면)**: AI_FREE(ADR-011, 문서 stale)·correct_*/backfill/recalc/liquidate 수동툴·env_gated C(alive)
- **RESOLVED(무액션)**: weekend_shadow_orders(ALIVE-display 재분류)·EIA/USDA(CFTC COT 스왑 완료)

상세: [[dormant-census-triage_2026-07-10-table]] · WAKE JSON [[dormant-census-triage_2026-07-10-wake]] · DELETE JSON [[dormant-census-triage_2026-07-10-delete]]
