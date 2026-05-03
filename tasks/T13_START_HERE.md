# T13 START HERE — 새 세션 부팅 5분 가이드

> **이 파일 먼저 read**. 그 다음 아래 순서대로 진입.

## ⚡ 30초 상태 파악

- **T12 종료 상태**: 2026-04-23~24 Fri. 관측 48h+. Plan v2 draft 완비. 코드 감사 / 플랜 vs 코드 정합 / Harness 정비는 **T13 에서 실행 예정 (Phase 0~5 대기 상태)**
- **현재 코드 변경**: exit learner unit bug fix / CAP max_hold 확장 / Phase 1 Exchange×Group composite (총 3건)
- **미실행**: Pillar 1~5 (Taxonomy / Cell / 3-Tier / PHS / Flow+Signal) + Cross-cutting H.1~11 + Part O Loss Forensic

## 🚨 절대 원칙 (매 변경 자가통과)

1. `feedback_no_single_review_verdict` — 1회 리뷰 단정 금지. 나무 말고 숲.
2. `feedback_no_quick_patch_ever` — 순간 패치/하드코딩/구조적 결함 절대 금지.
3. `feedback_flow_not_block` — 막지 말고 흐르게. 차단/skip/reject 금지.
4. `feedback_no_hardcode_in_plans` — Plan 내 magic number 금지.
5. **Per-Change Gate 4축**: A 북극성 6 / B 타당성 4 / C Feedback 위반 7 / D 구조결함 7

## 📖 문서 체인 (순서대로 read)

1. **본 파일** (T13_START_HERE.md) — 30초 방향
2. `memory/handoff_unified_2026_04_22_T12_session_end.md` — T12 전체 상태
3. `tasks/plan_t13_integrated_v2_2.md` — **Plan v2.2** (Phase 4 정합 감사 + Harness 통합, debate 19항)
4. `tasks/prep_t13_hardcode_audit_and_integration.md` — 세부 설계 (Part A~M + E1~E16)
5. `tasks/observation_log_t12.md` — 48h hourly 관측 + 주요 발견
6. `tasks/anomaly_snapshot_t12.md` — 구조 결함 증거
7. `tasks/next_plan_t14_performance_classification.md` — T14 후속

## 🎯 T13 실행 Phase (순차)

각 Phase 완료 후 commit. 중간 중단되어도 재개 가능.

| Phase | 작업 | 예상 | 산출 | 상태 |
|---|---|---|---|---|
| **0** | 코드 전수조사 | 2-3h | `audit_t13_code_state.md` | ✅ `b2f637a0` |
| **0.5** | Plan vs Code 정합 | 1-1.5h | `audit_t13_plan_vs_code.md` | ✅ `ee18cb04` |
| **1** | Plan v2 → v2.1 업데이트 | 1h | `plan_t13_integrated_v2_1.md` | ✅ `bcfaffa7` |
| **2** | Data 전수 분석 (signal/trade/open/regime) | 2-3h | `t13_data_review_report.md` | ✅ `70e78d69` |
| **3** | Harness 정비 (6 agent + 4 skill + hook) | 1-1.5h | `harness_audit_t13.md` + files | ✅ `305d186c` |
| **4** | 정합 감사 + Plan v2.2 + Harness 동기 | 2h | `plan_t13_integrated_v2_2.md` + agent/docs | ✅ `15aa463a`/`818562d0` |
| **5** | Jin 브리핑 + 신규 10항 즉시 실행 + Plan v2.3 | 2h | `plan_t13_integrated_v2_3.md` + 5 commit | ✅ `8e87f803`/`70254876`/`d8844704`/`1481e856`/`2a989477` |

**예상 총**: 8-10h (fresh session token)

## 🚦 시작 명령 (T13 세션 open 시)

```
처음부터 다시. Phase 0 클린 스타트.
T12 종료 시 Phase 0 일부 샘플 확인만 했고 정식 착수 X.
산출 파일 없음 — T13 에서 전체 새로 생성.

Phase 0 부터 순차 실행. Phase 간 commit.
매 Phase 산출물 파일 저장.
단정 X, 모든 발견 "관찰/가설/debate 대상" 형식.
Jin 복귀 전까지 Phase 5 제외 완료.
```

## 🔑 최중요 E# 증거 (T13 첫 조사 대상)

| E# | 증거 | Phase |
|---|---|---|
| E1 | OKX batch exit (+$720→-$1048) | Phase 2 + Part O forensic |
| E2 | `max_profit_pct=0` DB flush 누락 | Phase 0 코드 확인 |
| E7 | trace_id 부재 → forensic 불가 | Phase 3 (선결) |
| E14 | exit learner unit bug 재발 방지 | Phase 0 + 1 |
| E15 | Jin 3대 원칙 준수 환경 | Phase 3 Harness |
| **E16** | **Session × Exchange 극명한 성과 차이** (US OKX 74% / Asia OKX -$2853) | **Phase 2 + Pillar 2 cell session axis** |

## ❓ T13 Debate 필수 항목 (Plan v2.2 확정 전 — **19항**)

1. TIME timer 완전 폐지 vs PHS subordinate
2. Cell promote/demote + Factor weight 동시 vs 분리 배포
3. Paper → Live 전환 기준 (E12 반증 반영)
4. Direction 결정 로직 구조 (contrarian 원칙 vs data-driven)
5. Cleanup 자동화 여부 (재발 방지 vs 증상 치료)
6. Canary 범위 (포지션 % / strategy / time)
7. 분류 프레임워크 A/B/C (T14)
8. Event Bus AI budget / debounce
9. Fallback chain 깊이 (4단 vs 8단)
10. Stale signal 처리 (queued vs expired)
11. **Session 축 provider/cell 설계** (E16 신규) — provider 자체가 session-aware 여야 하는가, cell weight 만으로 충분한가

### Phase 4 신규 (D-G~I, Plan v2.2)
12. **TIME exit WR 9.2% / 1096건** (E17, Alert 5) — PHS 완전 대체 vs TIME 유지 후 PHS subordinate — debate 1 세부
13. **Disable flush window 기본 M일** (Alert 3/4 대응) — 7d vs 14d vs preg 튠 + `exclude_after_disable` flag 기본값
14. **Alpaca europe_late 0-10% WR / 276건** (E18) — premarket eligibility block vs cell matrix session axis weight=0 migrate vs 제3안

### Phase 4 보강 Gate (요청 시 검토, D-A~F 내)
15~19. D-A (profit_target unit) / D-B (quarantine scope) / D-C (OKX dedup window) / D-D (alpaca fallback) / D-E (cell 확장 순서)

## 🛡 Phase 0 진입 전 체크

- [ ] MEMORY.md 자동 load 확인
- [ ] 본 파일 read 완료
- [ ] Handoff memory read 완료
- [ ] Plan v2 draft 훑어봄
- [ ] 봇 상태 확인 (PID alive, KPI 기록)
- [ ] Git clean 상태 (uncommitted X)

## 📝 Phase 완료 시 체크

매 Phase 끝에:
- [ ] 산출 파일 저장
- [ ] git commit (Per-Change Gate 4축 통과)
- [ ] handoff memory update (진행 상태)
- [ ] 다음 Phase 상태 ⏳ → 진행 마킹

---

## 🔴 절대 금지 (세션 전체)

- 단정 (1회 리뷰/debate/관측 으로 결정 X)
- 하드코딩 (모든 숫자 preg/cell)
- Block-filter 추가 (skip/reject/disallow)
- "빨리 돌아가게만" 내심 동기
- Patchwork (Phase 순서 건너뛰기)

## 🟢 권장

- 모든 발견 = 관찰 + 가설 + 반례 + T13 debate 대상
- 각 Phase 산출 = 단순 파일 + git trace 완비
- Jin 판단 필요 항목 = 명시적 debate 리스트
- 실측 데이터 우선 (코드 추정 X)
