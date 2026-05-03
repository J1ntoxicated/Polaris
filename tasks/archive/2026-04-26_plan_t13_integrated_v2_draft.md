# Plan T13 — Integrated v2 (초안, T13 세션 debate 후 확정)

> **상태**: T12 종료 시점 초안. T13 세션에서 전체 카드 교차검증 + Jin 승인 후 v2 확정.
> **원칙 준수**: `feedback_no_single_review_verdict` (단정 X) / `feedback_no_quick_patch_ever` / `feedback_flow_not_block` / `feedback_no_hardcode_in_plans`
> **부모 문서**: `tasks/prep_t13_hardcode_audit_and_integration.md` (Part A~M 세부 설계 + E1~E15 증거)

---

## 0. 원칙 (non-negotiable, 매 변경마다 자가검증)

### 0.1 3종 🚨🚨 최강 원칙
1. `feedback_no_single_review_verdict` — 1회 리뷰/debate/관측 단정 금지. 나무 말고 숲.
2. `feedback_no_quick_patch_ever` — 순간 패치/하드코딩/구조적 결함 절대 금지.
3. `feedback_flow_not_block` — 틀어막지 말고 흐르게. 차단/skip/reject 금지.

### 0.2 Per-Change Validation Gate (E.1, 4축)
- **A 북극성** (aggressive / amplify / flow / asymm / data-driven / no-block)
- **B 타당성** (목적 / 효과 / 부작용 / rollback)
- **C Feedback 위반 체크** (memory 7종)
- **D 구조 결함 자가 감지** (24h 후 의미? / 튠 가능? / contract break? / 원복? / 근본 원인?)

---

## I. T13 성공 정의

세션 종료 시 아래 모두 달성:
- [ ] `docs/metric_taxonomy.yaml` v1 확정 (per_exchange variants 포함)
- [ ] `audit_t13_hardcoded.md` + `audit_t13_units.md` (전수조사 완료)
- [ ] Unit BUG 전부 fix + regression 통과
- [ ] Forensic 산출물 (OKX 붕괴 / stuck 주기 / CAP direction bias)
- [ ] `plan_t13_integrated_v2.md` (draft → final, Jin 승인)
- [ ] MVP 구현 (H.1 Trace / H.2 Reconcile / H.5 Kill / H.10 Backup / G1 DB / Cell API M1~M3)
- [ ] 봇 restart + sample-based gate 통과 (OKX 1-2h / CAP 평일 / Alpaca 미장 1회)
- [ ] KPI asymm target preg 달성 확인

---

## II. 근거 — T12 실측 증거 (E1~E15, prep_t13 맵)

| 우선도 | E# | 증거 | T13 실행 |
|---|---|---|---|
| 🔴 | E1 | OKX +$720→-$1048 batch exit | Part O L0 + K.10 + Phase 2.5 |
| 🔴 | E2 | max_profit_pct=0 DB flush 누락 | K.10 + G1 |
| 🔴 | E3 | Stuck 재누적 28h 주기 | Part O L0 + H.2 |
| 🔴 | E7 | Trace 부재 — forensic 불가 | H.1 선결 |
| 🔴 | E14 | Exit learner unit bug 재발 방지 | B.3 validator + E.1 |
| 🟡 | E4 | CAP WR 박스 + short bias | Part M + Phase 1.3 + B.3 |
| 🟡 | E5 | Signal acted→entry 99% drop | D14.5 + H.1 |
| 🟡 | E6 | CAP DB duplicate open | H.2 |
| 🟢 | E8~E11 | Signal hygiene / Liquidity / Queued | Phase 1.3 / 2 / J.11 |
| 🔵 | E12~E13 | Cash cow 반증 / Cleanup 재현 미확인 | T14 plan / 관측 장기 |
| 🔴 | E15 | Jin 3대 원칙 | Per-Change Gate 전 Part 강제 |

---

## III. 설계 기둥 (Pillar) — T13 core

### Pillar 1. Input Taxonomy + Unit Contract (🔴 선결)
- `docs/metric_taxonomy.yaml` — 모든 metric 의 unit / range / semantic / consumer / normalize 정의
- per_exchange variants (depth / cadence / unit 다름)
- Runtime validator (`_metric_contract.py`) — 런타임 unit anomaly 감지
- Cell API 진입점에서 contract 검증
- **Why**: E14 exit learner unit bug 재발 방지 + Multi-Matrix 의 fallback cascade 전제
- **Part 참조**: B.3

### Pillar 2. Multi-Matrix Hierarchical Cell
- `CellKey` 8축 (exchange / group / ticker / session / regime / direction / liquidity_tier / strategy_id)
- `cell_resolve(metric, *dims)` — depth 역순 fallback
- `cell_learn(metric, cell, value, sample_n)` — sample 기반 업데이트
- Lifecycle: seed → active → promote → dormant → retire
- 분리 배포 (M4 읽기 → M5 쓰기 → M6 lifecycle → K.4 factor weight)
- **Why**: E1~E6 의 모든 축 조정 통일 API
- **Part 참조**: B.2

### Pillar 3. 3-Tier 프로세스 + DB 스키마
- Tier 1 `invasion-ingest` (WS/REST → raw DB)
- Tier 2 `invasion-trade` (raw → signal → order)
- Tier 3 `invasion-learn` (history → preg/strategy 튠)
- DB 스키마 확장: `market_ticks` / `market_candles_*` / `provider_raw` / `feature_cache` / `ai_event_audits` / `position_health` / `lag_kpi_hourly` / `signal_queue` / `loss_attribution` / `signal_blocks` / `trade_events`
- Write 권한 상호 배타 (linter 차단)
- **Why**: PID rotation / 단일 fail-point / raw data 보존
- **Part 참조**: G

### Pillar 4. Position Health + Real-time Exit (Loss Forensic)
- PHS 6-factor (price / time / signal / liquidity / correlation / regime)
- K.2 지표 (exchange-specific) → PHS factor 입력
- Fast-Out (PHS 급락 / peak reversal / signal flip)
- TIME timer 폐지 또는 PHS subordinate
- Loss Attribution (signal / entry / size / hold / exit_timing) → 각 factor cell update
- Signal redefinition (lifecycle shadow/dormant)
- **Why**: E1 OKX batch exit / E3 stuck 재누적
- **Part 참조**: Phase 2.5 + K.10 + O

### Pillar 5. Flow Amplifier + Dynamic Signal
- `amp_final = max(1.0, min(amp_desired, amp_flow_signal, amp_flow_liq, amp_flow_slip))`
- Unfillable-Queued 상태 (market closed / margin / broker reject / liq wait / kill)
- Dynamic Signal 5-layer (provider composition / regime / genetic / meta-signal / AI meta)
- Signal lifecycle candidate → trial → active → amplified → dormant → retired
- **Why**: E4 direction bias / E12 cash cow 반증의 구조적 진화 필요
- **Part 참조**: J + M

---

## IV. Cross-Cutting (🔴 5개 T13 필수)

| # | 주제 | 왜 T13 필수 |
|---|---|---|
| H.1 | trace_id + `trade_events` 구조화 | E7 모든 forensic 기반 |
| H.2 | Reconciliation (broker ⇄ DB, duplicate open rule) | E6 + 실손 위험 |
| H.4 | Canary + KPI Guard | 배포 안전, 북극성 보호 |
| H.5 | Kill Switch (`touch data/KILL`) | Black swan 대응 |
| H.10 | DB Backup + Restore 리허설 (D7.5) | 학습 데이터 보호 |

T14+: H.3 Auto Data QA / H.6 Capital / H.7 DB Growth / H.8 AI Safety / H.9 Time Sync / H.11 HIL

---

## V. 실행 순서 (D0~D22, E 우선순위 반영)

### 단계 0. Step -1 Harness 사전 audit (Part I)
- harness-structure-advisor / drift-detector / dev-audit-advisor 3호출
- Plan v2 수용 기준 먼저 확정 (B.3 / G / H.2 / K.3 acceptance criteria)
- `tasks/harness_audit_t13.md` 생성

### 단계 1. 토대 (6-8h)
- **D0** Taxonomy v1 (Pillar 1)
- **D1** 하드코딩 전수조사 (+ unit tag)
- **D2** Unit BUG 즉시 fix + regression

### 단계 2. 설계 확정 (2-3h)
- **D3** 신/구 매핑 grep 검증 (B / B.1 / B.2 / G)
- **D4** plan_t13_integrated_v2.md (이 파일) final (Jin 승인)
- **D5** Agent gap 5개 spec 작성 (I.3)

### 단계 3. MVP (12-16h, Codex 반영 순서)
- **D6** H.5 Kill Switch
- **D8** H.1 trace_id + `trade_events` ← 이후 forensic 기반
- **D9** H.2 Reconciliation (duplicate open rule 포함)
- **D7** H.10 Backup 스냅샷
- **D7.5** Backup Restore 리허설
- **D10** G1 DB 스키마 (모든 신규 테이블)
- **D17** G3+G4 Tier 1 독립 프로세스 ← 안정 위해 Cell 전에
- **D11** M1~M3 Cell API (CellKey + resolve + learn)

### 단계 3.5. Forensic (E1~E6 실측 원인 규명, 8-10h)
- **D11.5** OKX 붕괴 timeline forensic (E1)
- **D11.6** Stuck 28h 주기 원인 (E3)
- **D11.7** CAP direction bias 원인 (E4)
- **D11.8** Signal acted→entry 99% drop (E5)
- **결과**: Part O / Part M / Phase 1.3 범위 조정 (실측 후 plan v2.1 update)

### 단계 4. 확장 (20-28h, Gate 분리 배포)
- **D12** H.4 Canary + KPI Guard
- **D13** Phase 1.5 Event Bus + AI audit (D8 trace 재사용)
- **D14** Phase 1.3 Signal hygiene (signal_blocks 분리)
- **D14.5** Lag KPI 집계 job (E5)
- **D15** H.3 Auto Data QA + null_strategy filter
- **D16a** M4 cell_resolve shim (read)
- **D16.5** Gate 1 (sample 확보 후)
- **D16b** M5 learner → cell_learn 이관
- **D18** Phase 2 Liquidity layer
- **D18.5** Part J Flow Amplifier
- **D18.6** M.4 Multi-factor composition (D16b 후)
- **D18.7** Peak capture / missed opportunity KPI
- **D19** Phase 2.5 PHS skeleton + K.10
- **D19.5** Fast-In/Out latency
- **D19.6~8** Part O L1~L4 Loss Attribution

### 단계 5. 안정 + 관찰 (48h+)
- **D20** 24-48h 관측 + KPI 비교
- **D21** T14 대상 작업 (Part 3 Winner amplify / T14 plan)
- **D22** KPI asymm ≥ preg_target 검증

---

## VI. Per-Change Validation Gate 강제 (E.1)

매 edit/commit/pset 전 자가 통과:
- A 북극성 6
- B 타당성 4
- C Feedback 위반 7 (no_single / no_quick / flow / no_block / no_dampen / code_verify / wiring)
- D 구조 결함 7 (24h / preg / contract / behavior / rollback / root / '빨리')

실패 시 revert + memory 교훈 + Jin 보고

---

## VII. 미결정 (T13 debate 필요 항목)

> 아래는 **혼자 결정 금지**. T13 세션에서 전체 카드 교차 + debate + Jin 판단.

1. **TIME timer 완전 폐지 vs PHS subordinate** — batch exit 원인 forensic 후
2. **Cell promote/demote + Factor weight 동시 vs 분리 배포 범위**
3. **Paper → Live 전환 기준 (E12 cash cow 반증 반영)**
4. **Direction 결정 로직 구조** (contrarian 원칙 vs data-driven signal)
5. **Cleanup 자동화 여부** (재발 방지 vs 증상 치료)
6. **Canary 범위** (포지션 % / strategy / time 기반)
7. **분류 프레임워크 A/B/C (T14)** — BCG / 연속 score / Jin hybrid
8. **Event Bus AI budget / debounce 값**
9. **Fallback chain 깊이** (4단 vs 8단)
10. **Stale signal 처리** (queued vs expired)

---

## VIII. 참조

- 세부 설계: `tasks/prep_t13_hardcode_audit_and_integration.md` (Part A~M, E1~E15)
- T12 관측: `tasks/observation_log_t12.md`
- 결함 증거: `tasks/anomaly_snapshot_t12.md` + `anomaly_detection_t12.sql`
- T14 후속: `tasks/next_plan_t14_performance_classification.md`
- Memory: `handoff_unified_2026_04_22_T12_session_end` + 3종 최강 원칙

---

## 📌 최종 재확인 (절대 원칙)

- 본 파일은 **초안 (draft)**. T13 세션에서 전체 카드 교차 후 v2 확정.
- 모든 숫자 / threshold / priority 는 **preg/cell_learned 예정** (하드코딩 금지).
- Forensic 단계 (D11.5~8) 결과에 따라 **Part O / Part M / Phase 1.3 범위 재조정** 허용 — 초안 고집 금지.
- Per-Change Gate 4축 매 변경마다 자가 통과 필수.
