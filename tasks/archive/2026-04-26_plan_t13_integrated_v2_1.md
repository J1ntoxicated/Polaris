# Plan T13 — Integrated v2.1 (Phase 0/0.5 반영, Jin 승인 전)

> **상태**: T12 draft (`plan_t13_integrated_v2_draft.md`) 를 T13 Phase 0/0.5 코드 전수조사 + Plan vs Code 정합 결과로 업데이트.
> **원칙 준수**: `feedback_no_single_review_verdict` / `feedback_no_quick_patch_ever` / `feedback_flow_not_block` / `feedback_no_hardcode_in_plans`
> **부모**: `tasks/prep_t13_hardcode_audit_and_integration.md` (Part A~M, E1~E15)
> **감사 근거**: `tasks/audit_t13_code_state.md` + `tasks/audit_t13_plan_vs_code.md`
> **변경 요약**: Pillar 2/5 "확장 표현" 수정 + Pillar 4 TIME suppression 흔적 언급 + VII debate 6항 (D-A~F) 추가 + III 기존 자산 footnote.

---

## 0. 원칙 (non-negotiable, 매 변경마다 자가검증)

### 0.1 3종 🚨🚨 최강 원칙
1. `feedback_no_single_review_verdict` — 1회 리뷰/debate/관측 단정 금지. 나무 말고 숲.
2. `feedback_no_quick_patch_ever` — 순간 패치/하드코딩/구조적 결함 절대 금지.
3. `feedback_flow_not_block` — 틀어막지 말고 흐르게. 차단/skip/reject 금지.

### 0.2 Per-Change Validation Gate (E.1, 4축)
- **A 북극성** (aggressive / amplify / flow / asymm / data-driven / no-block)
- **B 타당성** (목적 / 효과 / 부작용 / rollback)
- **C Feedback 위반** (memory 7종)
- **D 구조 결함 자가 감지** (24h 후 의미 / 튠 가능 / contract break / 원복 / 근본 원인)

---

## I. T13 성공 정의

세션 종료 시 아래 모두 달성:
- [x] `audit_t13_code_state.md` + `audit_t13_plan_vs_code.md` (Phase 0/0.5 완료)
- [ ] `docs/metric_taxonomy.yaml` v1 (per_exchange variants 포함)
- [ ] `audit_t13_hardcoded.md` + `audit_t13_units.md` (D1/D2)
- [ ] Unit BUG 전부 fix + regression (profit_target learner 포함)
- [ ] Forensic 산출물 (OKX 붕괴 / stuck 주기 / CAP direction bias)
- [ ] `plan_t13_integrated.md` final (Jin 승인)
- [ ] MVP 구현 (H.1 Trace / H.2 Reconcile / H.5 Kill / H.10 Backup / G1 DB / Cell API M1~M3)
- [ ] 봇 restart + sample-based gate 통과 (OKX 1-2h / CAP 평일 / Alpaca 미장 1회)
- [ ] KPI asymm target preg 달성 확인

---

## II. 근거 — T12 실측 증거 (E1~E15 맵)

| 우선도 | E# | 증거 | T13 실행 |
|---|---|---|---|
| 🔴 | E1 | OKX +$720→-$1048 batch exit | Part O L0 + K.10 + Phase 2.5 |
| 🔴 | E2 | max_profit_pct=0 DB flush 가설 (Phase 0 검증 필요) | K.10 + G1 + Phase 2 실측 |
| 🔴 | E3 | Stuck 재누적 28h 주기 | Part O L0 + H.2 |
| 🔴 | E7 | Trace 부재 — forensic 불가 | H.1 선결 |
| 🔴 | E14 | Exit learner unit bug 재발 방지 (profit_target learner `*100` 잔존) | B.3 validator + E.1 |
| 🟡 | E4 | CAP WR 박스 + short bias | Part M + Phase 1.3 + B.3 |
| 🟡 | E5 | Signal acted→entry 99% drop | D14.5 + H.1 |
| 🟡 | E6 | CAP DB duplicate open | H.2 |
| 🟢 | E8~E11 | Signal hygiene / Liquidity / Queued | Phase 1.3 / 2 / J.11 |
| 🔵 | E12~E13 | Cash cow 반증 / Cleanup 재현 미확인 | T14 plan / 관측 장기 |
| 🔴 | E15 | Jin 3대 원칙 (code-level enforce 부재 확인) | Per-Change Gate + Phase 3 hook |
| 🔴 | E16 | Session × Exchange 성과 차이 (US OKX 74% / Asia OKX -$2853) | Debate 11 + Pillar 2 |

---

## III. 설계 기둥 (Pillar) — T13 core

### Pillar 1. Input Taxonomy + Unit Contract (🔴 선결)
- `docs/metric_taxonomy.yaml` — 모든 metric 의 unit / range / semantic / consumer / normalize 정의
- per_exchange variants (depth / cadence / unit 다름)
- Runtime validator `_metric_contract.py`
- Cell API 진입점에서 contract 검증
- **Phase 0 보강**: `hourly_stats.py:655` profit_target learner `*100` 잔존 — D0~D2 scope 에 포함
- **Part 참조**: B.3

### Pillar 2. Multi-Matrix Hierarchical Cell (**기존 6-dim → 8-dim 확장**)
- **기존 자산** (Phase 0 확인): `strategy/cell_matrix.py` 이미 6-dim (exchange × asset_group × session × regime × strategy × direction) + session 8-band + DB 216 rows. `lookup_cell_score()` read 경로.
- **확장 대상**: ticker 축 + liquidity_tier 축 2개 추가 → 8-dim `CellKey`.
- **신규 API**: `cell_resolve(metric, *dims)` depth 역순 fallback + `cell_learn(metric, cell, value, sample_n)` sample 기반 update + lifecycle (seed → active → promote → dormant → retire).
- **분리 배포**: M4 읽기 shim (기존 lookup_cell_score 래핑) → M5 쓰기 (learn) → M6 lifecycle → K.4 factor weight
- **Part 참조**: B.2

### Pillar 3. 3-Tier 프로세스 + DB 스키마
- Tier 1 `invasion-ingest` (WS/REST → raw DB)
- Tier 2 `invasion-trade` (raw → signal → order)
- Tier 3 `invasion-learn` (history → preg/strategy 튠)
- DB 스키마 확장 11종 테이블: `market_ticks` / `market_candles_*` / `provider_raw` / `feature_cache` / `ai_event_audits` / `position_health` / `lag_kpi_hourly` / `signal_queue` / `loss_attribution` / `signal_blocks` / `trade_events`
- Write 권한 상호 배타 (linter 차단)
- **Phase 0 확인**: 현재 단일 프로세스 `python3 -m invasion --headless`. DB `signals` 테이블만 존재. 신규 11 테이블 전부 부재.
- **Part 참조**: G

### Pillar 4. Position Health + Real-time Exit (Loss Forensic)
- PHS 6-factor (price / time / signal / liquidity / correlation / regime)
- K.2 지표 (exchange-specific) → PHS factor 입력
- Fast-Out (PHS 급락 / peak reversal / signal flip)
- **TIME timer 폐지 또는 PHS subordinate** (debate 1)
- Loss Attribution (signal / entry / size / hold / exit_timing) → 각 factor cell update
- Signal redefinition (lifecycle shadow/dormant)
- **Phase 0 관찰**: 현재 `trade/exit_cycle.py:475-483` TIME→TRAIL_PROTECTED suppression (peak 0.3% + loss cap -2% 조건) 이 PHS 의 원시 prototype. 결함 2 (state 고착 가설) 는 PHS 설계 시 교훈 반영.
- **Part 참조**: Phase 2.5 + K.10 + O

### Pillar 5. Flow Amplifier + Dynamic Signal (**기존 amplify chain 확장**)
- **기존 자산** (Phase 0 확인): `trade/_pipeline_sizing.py` 의 `_ramp_mult` / `_conviction_mult` / `_atr_exp_mult` chain 이 이미 amplify-only 로 작동 (T12 ITEM-026/027/030).
- **확장 대상**: `amp_final = max(1.0, min(amp_desired, amp_flow_signal, amp_flow_liq, amp_flow_slip))` — 3 축 추가 (flow_signal / flow_liq / flow_slip).
- Unfillable-Queued 상태 (market closed / margin / broker reject / liq wait / kill) — 현재 `signals/composer.py:268-310` drop 5-category 를 quarantine 전환 필요 (결함 5).
- Dynamic Signal 5-layer (provider composition / regime / genetic / meta-signal / AI meta) — 전면 신규.
- Signal lifecycle candidate → trial → active → amplified → dormant → retired.
- **Part 참조**: J + M

---

## IV. Cross-Cutting (🔴 5개 T13 필수)

| # | 주제 | 왜 T13 필수 | Phase 0 현실 |
|---|---|---|---|
| H.1 | trace_id + `trade_events` 구조화 | E7 모든 forensic 기반 | bus.py:87-100 trace_id 부재 확인 |
| H.2 | Reconciliation (broker ⇄ DB, duplicate open rule) | E6 + 실손 위험 | adopted/orphan cascade 있으나 loop 없음 |
| H.4 | Canary + KPI Guard | 배포 안전, 북극성 보호 | 전면 신규 |
| H.5 | Kill Switch (`touch data/KILL`) | Black swan 대응 | 파일 기반 kill 신규 (DD kill 기존) |
| H.10 | DB Backup + Restore 리허설 (D7.5) | 학습 데이터 보호 | 전면 신규 |

T14+: H.3 Auto Data QA / H.6 Capital / H.7 DB Growth / H.8 AI Safety / H.9 Time Sync / H.11 HIL

---

## V. 실행 순서 (D0~D22, Phase 0 관찰 반영 유지)

### 단계 0. Step -1 Harness 사전 audit (Part I)
- harness-structure-advisor / drift-detector / dev-audit-advisor 3호출
- Plan v2.1 수용 기준 확정 (B.3 / G / H.2 / K.3 acceptance criteria)
- `tasks/harness_audit_t13.md` 생성 (Phase 3)

### 단계 1. 토대 (6-8h)
- **D0** Taxonomy v1 (Pillar 1) + profit_target learner unit 포함
- **D1** 하드코딩 전수조사 (+ unit tag, `position.py:337` fallback 포함)
- **D2** Unit BUG 즉시 fix + regression (hourly_stats.py:655 포함)

### 단계 2. 설계 확정 (2-3h)
- **D3** 신/구 매핑 grep 검증 (B / B.1 / B.2 / G)
- **D4** plan_t13_integrated_final.md (Jin 승인)
- **D5** Agent gap 5개 spec (I.3)

### 단계 3. MVP (12-16h)
- **D6** H.5 Kill Switch (파일 기반 + DD 기반 통합)
- **D8** H.1 trace_id + `trade_events`
- **D9** H.2 Reconciliation + duplicate open rule
- **D7** H.10 Backup snapshot
- **D7.5** Backup Restore 리허설
- **D10** G1 DB 스키마 11 테이블
- **D17** G3+G4 Tier 1 독립 프로세스 (Cell 전에 안정)
- **D11** M1~M3 Cell API (기존 6-dim wrap → 8-dim 확장 + cell_resolve / cell_learn)

### 단계 3.5. Forensic (8-10h)
- **D11.5** OKX 붕괴 timeline (E1)
- **D11.6** Stuck 28h 주기 (E3)
- **D11.7** CAP direction bias (E4)
- **D11.8** Signal acted→entry 99% drop (E5)
- **D11.9** E2 max_profit_pct=0 실측 검증 (Phase 0 가설 확정/반증)
- **결과**: Part O / M / Phase 1.3 범위 조정 + plan v2.2 update

### 단계 4. 확장 (20-28h, Gate 분리 배포)
- **D12** H.4 Canary + KPI Guard
- **D13** Phase 1.5 Event Bus + AI audit (D8 trace 재사용)
- **D14** Phase 1.3 Signal hygiene (signal_blocks 분리, 결함 5)
- **D14.5** Lag KPI 집계 job
- **D15** H.3 Auto Data QA + null_strategy filter
- **D16a** M4 cell_resolve shim (read, 기존 lookup_cell_score 래핑)
- **D16.5** Gate 1 (sample 확보 후)
- **D16b** M5 learner → cell_learn 이관
- **D18** Phase 2 Liquidity layer (liquidity_tier 축 주입)
- **D18.5** Part J Flow Amplifier (3 축 추가)
- **D18.6** M.4 Multi-factor composition
- **D18.7** Peak capture / missed opportunity KPI
- **D19** Phase 2.5 PHS skeleton + K.10
- **D19.5** Fast-In/Out latency
- **D19.6~8** Part O L1~L4 Loss Attribution

### 단계 5. 안정 + 관찰 (48h+)
- **D20** 24-48h 관측 + KPI 비교
- **D21** T14 대상 (Part 3 Winner amplify / T14 plan)
- **D22** KPI asymm ≥ preg_target 검증

---

## VI. Per-Change Validation Gate 강제 (E.1)

매 edit/commit/pset 전 자가 통과:
- A 북극성 6
- B 타당성 4
- C Feedback 위반 7
- D 구조 결함 7

실패 시 revert + memory 교훈 + Jin 보고

---

## VII. 미결정 (T13 debate 필요 항목, 16항)

> 아래는 **혼자 결정 금지**. T13 세션에서 전체 카드 교차 + debate + Jin 판단.

### VII.A Plan 원안 10항 (유지)
1. TIME timer 완전 폐지 vs PHS subordinate — **결함 2 근거**
2. Cell promote/demote + Factor weight 동시 vs 분리 배포
3. Paper → Live 전환 기준 (E12 반증)
4. Direction 결정 로직 구조 (contrarian vs data-driven) — **결함 4 연결**
5. Cleanup 자동화 여부
6. Canary 범위
7. 분류 프레임워크 A/B/C (T14)
8. Event Bus AI budget / debounce
9. Fallback chain 깊이 — **Pillar 2 depth 결정**
10. Stale signal 처리 — **결함 5 scope**

### VII.B Phase 0 신규 6항 (D-A~F)
- **D-A** `hourly_stats.py:655` profit_target learner `*100` 잔존 fix 방식 (단순 fix vs unit layer 정비, Pillar 1 scope)
- **D-B** signal drop quarantine 테이블 scope (signal_blocks vs events.jsonl 재구축)
- **D-C** OKX dedup window (100ms / 1s / per-tick hash, 결함 7)
- **D-D** `position.py:337` fallback `"crypto"` 교정 방식 (alpaca adapter populate 강제 vs groups.py 화이트리스트 재routing)
- **D-E** cell_matrix 축 확장 순서 (ticker 먼저 vs liquidity_tier 먼저 vs 동시)
- **D-F** E16 provider session-aware 여부 (ticker_baseline session-specific 전환 vs provider 자체 session axis 추가, START_HERE 11항 확장)

---

## VIII. 참조

- 세부 설계: `tasks/prep_t13_hardcode_audit_and_integration.md` (Part A~M, E1~E15)
- T12 관측: `tasks/observation_log_t12.md`
- 결함 증거: `tasks/anomaly_snapshot_t12.md` + `anomaly_detection_t12.sql`
- T14 후속: `tasks/next_plan_t14_performance_classification.md`
- 감사: `tasks/audit_t13_code_state.md` + `tasks/audit_t13_plan_vs_code.md`
- Memory: `handoff_unified_2026_04_22_T12_session_end` + 3종 최강 원칙

---

## 📌 최종 재확인

- 본 파일은 **v2.1 (Phase 0/0.5 반영 후)**. Jin 승인 후 v2 final 로 확정.
- 모든 숫자 / threshold / priority 는 **preg/cell_learned 예정**.
- Forensic 단계 (D11.5~9) 결과에 따라 Part O / M / Phase 1.3 범위 재조정 허용.
- Per-Change Gate 4축 매 변경 자가 통과 필수.
