# Plan T13 — Integrated v2.3 (Phase 5 실행 완료)

> **Parent**: `tasks/plan_t13_integrated_v2_2.md` (전체 구조/Pillar/D0~D22 불변)
> **변경 요약 (v2.2→v2.3)**:
> - Phase 5 executed: 5 commit + 3 Plan 이관 확정 + 5 원안 Harness 확정 + 4 Jin 인가 대기 + 1 값 미정
> - I. 성공 정의: Phase 5 ⏳→✅

## I. Phase 5 실행 결과 (신규 10항)

| # | Decision | Commit / 상태 |
|---|---|---|
| D-A | C 즉시 fix + validator 편입 | `8e87f803` p75_peak_pct `*100` 제거 |
| D-B | B 경량 (jsonl append) | `70254876` data/signal_blocks.jsonl |
| D-C | 🔴 Plan v2.3 이관 | signals 1h 3592/0 dup 이미 OK. ticker-tick adapter 수준 D11.5 forensic |
| D-D | A adapter populate | `d8844704` asset_group + unknown fallback |
| D-E | **동시 추가 확정** | ticker + liquidity_tier 동시. lookup fallback ticker → liquidity_tier. 구현 D16a~c |
| D-F | B cell weight only | 코드 변경 없음, cell matrix 작동 확인 |
| D-G | 🔴 Plan v2.3 이관 (B PHS subordinate) | preg 이미 이관, Pillar 4 PHS 구조 구현 시 강화 |
| D-H | C preg + ProactiveExit MVP | `1481e856` feature_disable_flush_days |
| D-I | B cell axis 자동 수렴 | 관찰 항목 |
| D-J | A 즉시 + 구조 방지 | `2a989477` bounds (1.0, 3.0) + learner clamp |
| #3 | Paper→Live Gate 신설 | `7edfa07b` preg 3종 등록 |

## II. 원안 10항 결정 분류

| 분류 | 항 | 결정 |
|---|---|---|
| Harness 확정 (9) | #1 | TIME **B 유지 + PHS subordinate** (Pillar 4 구현 시 강화) |
| | #2 | Cell promote/demote + factor weight **분리 배포** |
| | #3 | Paper→Live Gate **신설** — preg 3종 등록 `7edfa07b` (30/0.55/0.8). 구현 차기 |
| | #4 | Direction **hybrid** (cell_matrix direction 축 학습) |
| | #5 | Cleanup **HIL gate T14 이관** |
| | #6 | Canary **A 포지션 %** (D12 H.4 구현) |
| | #7 | 분류 A/B/C **T14 이관** |
| | #9 | Fallback chain **4단 MVP → 8단 확장** |
| | #10 | Stale signal **queued** (flow_not_block) |
| 값 미정 (1) | #8 | Event Bus 예산 — D13 구현 시 preg bounds 결정 |

## III. T13 성공 정의 (v2.3 갱신)

- [x] Phase 0~5 완료
- [ ] D0 Taxonomy v1 / D0.5 mult<1.0 전수 감사 / D1~D2 하드코딩+Unit BUG
- [ ] Forensic D11.5~9 (D-C 포함)
- [ ] MVP: H.1 Trace / H.2 Reconcile / H.5 Kill / H.10 Backup / G1 DB / Cell API M1~M3
- [ ] 봇 restart + sample-based gate 통과
- [ ] KPI asymm target preg 달성

## IV. 차기 실행 + 참조

1. 봇 restart → 24h 관측 / 2. D0~D2 (v2.2 단계 1) / 3. D0.5 mult<1.0 전수 / 4. 단계 2 이후

참조: v2.2 본문 `plan_t13_integrated_v2_2.md` · Phase 5 로그 `t13_phase5_briefing.md` · 감사 5종 Phase 0~4 산출

---
Per-Change Gate 4축 자가 통과. `feedback_md_max_60_lines_split` / `feedback_no_hardcode_in_plans` 준수.
