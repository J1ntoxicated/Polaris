# Plan T13 — Integrated v2.2 (Phase 4 정합 감사 반영, Jin 승인 전)

> **상태**: v2.1 (Phase 0/0.5 반영) 을 T13 Phase 4 정합 감사 결과로 업데이트.
> **정합 감사 근거**: `tasks/audit_t13_code_state.md` (Phase 0) + `audit_t13_plan_vs_code.md` (Phase 0.5) + `t13_data_review_report.md` (Phase 2) + `harness_audit_t13.md` (Phase 3) + Phase 4 3-Explore 정합 감사 (`~/.claude/plans/wise-skipping-crab.md`)
> **원칙 준수**: `feedback_no_single_review_verdict` / `feedback_no_quick_patch_ever` / `feedback_flow_not_block` / `feedback_no_hardcode_in_plans`
> **부모**: `tasks/prep_t13_hardcode_audit_and_integration.md` (Part A~M, E1~E15)
> **변경 요약 (v2.1→v2.2)**:
> - E# 맵 E17/E18 추가 (TIME exit WR / Alpaca premarket) + E7 재정의
> - Pillar 4 K.10 disable flush window / Pillar 5 amplify-only clamp 전수 감사
> - H.2 duplicate open rule 명시 / H.1 trade_id write 재정의
> - V 실행 단계 신설: Step -1 MVP Hook (Phase 4 집행) + D0.5 + D3.5 + D5 agent 이름 확정 + D16a.5
> - Debate 16 → **19항** (D-G TIME WR / D-H flush window / D-I Alpaca premarket)
> - Harness 업데이트 spec Plan 내부 편입 (D timeline 동기화)

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
- [x] `t13_data_review_report.md` + `harness_audit_t13.md` (Phase 2/3 완료)
- [x] **Phase 4 정합 감사 + MVP Hook 2개 설치 + Agent 5 스텁** (v2.2 신설)
- [ ] `docs/metric_taxonomy.yaml` v1 (per_exchange variants 포함)
- [ ] `audit_t13_hardcoded.md` + `audit_t13_units.md` (D1/D2)
- [ ] Unit BUG 전부 fix + regression (profit_target learner 포함)
- [ ] **mult<1.0 preg 전수 감사 + amplify-only clamp 이관** (D0.5 신규)
- [ ] Forensic 산출물 (OKX 붕괴 / stuck 주기 / CAP direction bias)
- [ ] `plan_t13_integrated_final.md` (Jin 승인)
- [ ] MVP 구현 (H.1 Trace / H.2 Reconcile / H.5 Kill / H.10 Backup / G1 DB / Cell API M1~M3)
- [ ] 봇 restart + sample-based gate 통과 (OKX 1-2h / CAP 평일 / Alpaca 미장 1회)
- [ ] KPI asymm target preg 달성 확인

---

## II. 근거 — T12/Phase 2 실측 증거 (E1~E18 맵)

| 우선도 | E# | 증거 | T13 실행 |
|---|---|---|---|
| 🔴 | E1 | OKX +$720→-$1048 batch exit | Part O L0 + K.10 + Phase 2.5 |
| 🔴 | E2 | max_profit_pct=0 DB flush (Phase 2 실측 4386건 38.6%) | K.10 + G1 + D11.9 |
| 🔴 | E3 | Stuck 재누적 28h 주기 (Phase 2 관측 102건 peak) | Part O L0 + H.2 |
| 🔴 | E7 | **`signals.trade_id` FK write 누락 0.88%** (Phase 3 재정의, "부재"→"write 경로") | H.1 signals.trade_id write fix |
| 🔴 | E14 | Exit learner unit bug 재발 방지 (profit_target `*100` 잔존) | B.3 validator + E.1 |
| 🟡 | E4 | CAP WR 박스 + short bias | Part M + Phase 1.3 + B.3 |
| 🟡 | E5 | Signal acted→entry 99% drop | D14.5 + H.1 |
| 🟡 | E6 | CAP DB duplicate open | H.2 (duplicate open rule) |
| 🟢 | E8~E11 | Signal hygiene / Liquidity / Queued | Phase 1.3 / 2 / J.11 |
| 🔵 | E12~E13 | Cash cow 반증 / Cleanup 재현 미확인 | T14 plan / 관측 장기 |
| 🔴 | E15 | Jin 3대 원칙 (code-level enforce 부재) | Per-Change Gate + Phase 3/4 hook |
| 🔴 | E16 | Session × Exchange 성과 차이 (US OKX 74% / Asia OKX -$2853) | Debate D-F + Pillar 2 + D16a.5 |
| 🔴 | **E17** | **TIME exit WR 9.2% / 1096건** (Alert 5, threshold 20% 위반) | **Pillar 4 TIME 폐지 debate 1 근거 / D14 signal hygiene 관찰** |
| 🟡 | **E18** | **Alpaca europe_late WR 0-10% / 276건** (Phase 2 신규) | **Debate D-I + Pillar 2 session axis** |

---

## III. 설계 기둥 (Pillar) — T13 core

### Pillar 1. Input Taxonomy + Unit Contract (🔴 선결)
- `docs/metric_taxonomy.yaml` — 모든 metric 의 unit / range / semantic / consumer / normalize 정의
- per_exchange variants (depth / cadence / unit 다름)
- Runtime validator `_metric_contract.py`
- Cell API 진입점에서 contract 검증
- **Phase 0 보강**: `hourly_stats.py:655` profit_target learner `*100` 잔존 — D0~D2 scope
- **Part 참조**: B.3

### Pillar 2. Multi-Matrix Hierarchical Cell (**기존 6-dim → 8-dim 확장**)
- **기존 자산** (Phase 0 확인): `strategy/cell_matrix.py` 이미 6-dim + session 8-band + DB 216 rows. `lookup_cell_score()` read 경로.
- **확장 대상**: ticker 축 + liquidity_tier 축 2개 추가 → 8-dim `CellKey`.
- **신규 API**: `cell_resolve(metric, *dims)` depth 역순 fallback + `cell_learn(metric, cell, value, sample_n)` + lifecycle (seed→active→promote→dormant→retire).
- **분리 배포**: M4 읽기 shim (기존 lookup_cell_score 래핑) → M5 쓰기 (learn) → M6 lifecycle → K.4 factor weight
- **Session axis 변환** (E16 대응, D-F 결정 후): D16a.5 에서 `session` 이 provider-aware 인지 cell-weight only 인지 분기
- **Part 참조**: B.2

### Pillar 3. 3-Tier 프로세스 + DB 스키마
- Tier 1 `invasion-ingest` / Tier 2 `invasion-trade` / Tier 3 `invasion-learn`
- DB 스키마 확장 11종: `market_ticks` / `market_candles_*` / `provider_raw` / `feature_cache` / `ai_event_audits` / `position_health` / `lag_kpi_hourly` / `signal_queue` / `loss_attribution` / `signal_blocks` / `trade_events`
- Write 권한 상호 배타 (linter 차단)
- **Phase 0/2 확인**: 단일 프로세스. DB `signals` 테이블만 존재 + `signals.trade_id` FK 이미 schema 에 있으나 write 0.88% 만 linkage (E7 재정의).
- **H.1 scope 재정의**: "trace_id 신규" 대신 **`signals.trade_id` write 경로 fix** 가 선결. bus payload trace_id 는 Tier 분리 후 재검토.
- **Part 참조**: G

### Pillar 4. Position Health + Real-time Exit (Loss Forensic)
- PHS 6-factor (price / time / signal / liquidity / correlation / regime)
- K.2 지표 (exchange-specific) → PHS factor 입력
- Fast-Out (PHS 급락 / peak reversal / signal flip)
- **TIME timer 폐지 또는 PHS subordinate** (debate 1) — **근거 강화**: E17 TIME exit WR 9.2% on 1096 trades (Alert 5) 가 현재 TIME 로직 구조 결함 확정 증거
- **K.10 disable flush window** (Alert 3/4 대응, v2.2 신설): disable 된 feature (예: ai_proactive_exit) 의 historical metric 을 learner window 에서 M일 동안 exclude. preg `feature_disable_flush_days` 예약 + subsystem_reviewer 에 `exclude_after_disable` flag.
- Loss Attribution (signal / entry / size / hold / exit_timing) → 각 factor cell update
- Signal redefinition (lifecycle shadow/dormant)
- **Phase 0 관찰**: `trade/exit_cycle.py:475-483` TIME→TRAIL_PROTECTED suppression 이 PHS 원시 prototype.
- **Part 참조**: Phase 2.5 + K.10 + O

### Pillar 5. Flow Amplifier + Dynamic Signal (**기존 amplify chain 확장**)
- **기존 자산** (Phase 0 확인): `_pipeline_sizing.py` 의 `_ramp_mult` / `_conviction_mult` / `_atr_exp_mult` chain amplify-only (T12 ITEM-026/027/030).
- **확장 대상**: `amp_final = max(1.0, min(amp_desired, amp_flow_signal, amp_flow_liq, amp_flow_slip))` — 3 축 추가.
- **amplify-only clamp enforce** (Alert 9 대응, v2.2 신설): 기존 dampen preg 전수 감사 후 `mult < 1.0` 값 전부 `max(1.0, value)` 로 migrate. D0.5 액션.
- Unfillable-Queued 상태 (market closed / margin / broker reject / liq wait / kill) — 현재 `signals/composer.py:268-310` drop 5-category 를 quarantine 전환 필요 (결함 5).
- **Dynamic Signal 5-layer scope risk 명시**: provider composition / regime / genetic / meta-signal / AI meta 는 **전면 신규**. Phase 0 코드 흔적 부재. 과대 범위 위험 — D18.x 에서 증거 기반 단계 배포, 일괄 구현 금지.
- Signal lifecycle candidate → trial → active → amplified → dormant → retired.
- **Part 참조**: J + M

---

## IV. Cross-Cutting (🔴 5개 T13 필수)

| # | 주제 | 왜 T13 필수 | Phase 0/2/3 현실 |
|---|---|---|---|
| H.1 | **`signals.trade_id` write 경로 fix** (재정의) | E7 forensic 기반 | DB FK 존재, write 0.88% |
| H.2 | Reconciliation + **duplicate open rule** (v2.2 신설) | E6 + 결함 8 + 실손 위험 | adopted/orphan cascade 있으나 loop 없음; duplicate 감지 부재 |
| H.4 | Canary + KPI Guard | 배포 안전, 북극성 보호 | 전면 신규 |
| H.5 | Kill Switch (`touch data/KILL`) | Black swan 대응 | 파일 기반 kill 신규 (DD kill 기존) |
| H.10 | DB Backup + Restore 리허설 (D7.5) | 학습 데이터 보호 | 전면 신규 |

**H.2 duplicate open rule (B2 신설)**: 동일 `(broker_id, ticker, direction)` 창 N초 이내 (preg `duplicate_open_window_sec` 예약) 신규 open 시 reject + log + alert. 단 flow 차단 아님: second signal 은 queue 로 redirect.

T14+: H.3 Auto Data QA / H.6 Capital / H.7 DB Growth / H.8 AI Safety / H.9 Time Sync / H.11 HIL

---

## V. 실행 순서 (D0~D22 + Phase 4 Step -1)

### 단계 0. **Step -1 Harness Bootstrap (Phase 4 집행)** [v2.2 신설]
- MVP Hook 2개 설치: PreCommit MD 60줄 상한 + PostToolUse magic-num warn (invasion/*.py)
- Agent 5 스텁 md 생성: unit-contract / trace-linker / cell-lifecycle / quarantine / session-axis
- canonical_files.md 2차 sync (Phase 3 1차 + 신규 파일 경로)
- audit_framework.md 3축 추가 (quarantined / session × exchange / linkage)
- 본 Phase 4 내 완료. 이후 단계 는 Jin 승인 + debate 19항 결정 후

### 단계 0.5. Harness 사전 audit 재확인
- harness-structure-advisor / drift-detector / dev-audit-advisor 3호출
- Plan v2.2 수용 기준 확정 (B.3 / G / H.2 / K.3 acceptance criteria)

### 단계 1. 토대 (6-8h)
- **D0** Taxonomy v1 (Pillar 1) + profit_target learner unit 포함
- **D0.5** [v2.2 신설] **mult<1.0 preg 전수 감사 + amplify-only clamp migrate** (Alert 9 대응) — grep 전수 + preg 값 migrate + `_params_signal.py` / `_params_sizing.py` 검토
- **D1** 하드코딩 전수조사 (+ unit tag, `position.py:337` fallback 포함)
- **D2** Unit BUG 즉시 fix + regression (hourly_stats.py:655 포함)

### 단계 2. 설계 확정 (2-3h)
- **D3** 신/구 매핑 grep 검증 (B / B.1 / B.2 / G)
- **D3.5** [v2.2 신설] **canonical_files.md 최종 sync gate** — Phase 0 audit 경로 vs canonical 전수 대조. 불일치 시 D4 차단.
- **D4** plan_t13_integrated_final.md (Jin 승인)
- **D5** [v2.2 구체화] Agent gap 5 **이름/trigger 확정**:
  - `dev-unit-contract-validator` → preg 신규/수정 시 dispatch
  - `dev-trace-linker` → signals.trade_id write site 변경 시 dispatch
  - `ops-cell-lifecycle` → cell_matrix 쓰기 발생 시 dispatch
  - `ops-quarantine-reviewer` → quarantined_structural_defect/_noise 주간 dispatch
  - `dev-session-axis-auditor` → cell_matrix 의 session 축 일관성 감사 (D-F 결정 후 활성)

### 단계 3. MVP (12-16h)
- **D6** H.5 Kill Switch (파일 기반 + DD 기반 통합)
- **D8** H.1 `signals.trade_id` write 경로 fix + `trade_events` 확장
- **D9** H.2 Reconciliation + **duplicate open rule** (B2)
- **D7** H.10 Backup snapshot
- **D7.5** Backup Restore 리허설
- **D10** G1 DB 스키마 11 테이블
- **D17** G3+G4 Tier 1 독립 프로세스
- **D11** M1~M3 Cell API (기존 6-dim wrap → 8-dim + cell_resolve / cell_learn)

### 단계 3.5. Forensic (8-10h)
- **D11.5** OKX 붕괴 timeline (E1)
- **D11.6** Stuck 28h 주기 (E3)
- **D11.7** CAP direction bias (E4)
- **D11.8** Signal acted→entry 99% drop (E5)
- **D11.9** E2 max_profit_pct=0 실측 검증
- **결과**: Part O / M / Phase 1.3 범위 조정 + plan v2.3 update

### 단계 4. 확장 (20-28h, Gate 분리 배포)
- **D12** H.4 Canary + KPI Guard + **Hook 확장 4개** (PreToolUse doc magic-num / Block-filter 검출 / SessionStart auto-inject / Stop Gate 4축) [v2.2 편입]
- **D13** Phase 1.5 Event Bus + AI audit
- **D14** Phase 1.3 Signal hygiene (signal_blocks 분리, 결함 5, E17 TIME WR 관찰 포함)
- **D14.5** Lag KPI 집계 job
- **D15** H.3 Auto Data QA + null_strategy filter + **audit_framework.md 3축 업데이트** [v2.2 편입: quarantined / session × exchange / linkage]
- **D16a** M4 cell_resolve shim (read, 기존 lookup_cell_score 래핑)
- **D16a.5** [v2.2 신설] **Session-axis transform** (D-F 결정 반영 자리) — provider session-aware 또는 cell weight only 분기 + `dev-session-axis-auditor` 활성
- **D16.5** Gate 1 (sample 확보 후)
- **D16b** M5 learner → cell_learn 이관
- **D18** Phase 2 Liquidity layer (liquidity_tier 축 주입)
- **D18.5** Part J Flow Amplifier (3 축 추가)
- **D18.6** M.4 Multi-factor composition
- **D18.7** Peak capture / missed opportunity KPI
- **D19** Phase 2.5 PHS skeleton + K.10 disable flush window
- **D19.5** Fast-In/Out latency
- **D19.6~8** Part O L1~L4 Loss Attribution

### 단계 5. 안정 + 관찰 (48h+)
- **D20** 24-48h 관측 + KPI 비교
- **D21** T14 대상 (Part 3 Winner amplify / T14 plan)
- **D22** KPI asymm ≥ preg_target 검증

---

## VI. Per-Change Validation Gate 강제 (E.1)

매 edit/commit/pset 전 자가 통과:
- A 북극성 6 / B 타당성 4 / C Feedback 위반 7 / D 구조 결함 7

실패 시 revert + memory 교훈 + Jin 보고. Phase 4 Hook 2개 는 자동화 1차. Hook 확장 4개 는 D12 이후.

---

## VII. 미결정 (T13 debate 필요 항목, **19항**)

> 아래는 **혼자 결정 금지**. T13 세션에서 전체 카드 교차 + debate + Jin 판단.

### VII.A Plan 원안 10항 (유지)
1. TIME timer 완전 폐지 vs PHS subordinate — **E17 근거 보강**
2. Cell promote/demote + Factor weight 동시 vs 분리 배포
3. Paper → Live 전환 기준 (E12 반증)
4. Direction 결정 로직 (contrarian vs data-driven) — **결함 4 연결**
5. Cleanup 자동화 여부
6. Canary 범위
7. 분류 프레임워크 A/B/C (T14)
8. Event Bus AI budget / debounce
9. Fallback chain 깊이 — **Pillar 2 depth 결정**
10. Stale signal 처리 — **결함 5 scope**

### VII.B Phase 0 신규 6항 (D-A~F, 유지)
- **D-A** `hourly_stats.py:655` profit_target learner `*100` 잔존 fix (단순 vs unit layer 정비)
- **D-B** signal drop quarantine 테이블 scope (signal_blocks vs events.jsonl 재구축)
- **D-C** OKX dedup window (100ms / 1s / per-tick hash, 결함 7)
- **D-D** `position.py:337` fallback `"crypto"` 교정 방식 (alpaca adapter populate 강제 vs groups.py whitelist)
- **D-E** cell_matrix 축 확장 순서 (ticker 먼저 vs liquidity_tier 먼저 vs 동시)
- **D-F** E16 provider session-aware 여부 (ticker_baseline session-specific vs provider session axis)

### VII.C Phase 2/4 신규 3항 (D-G~I) [v2.2 신설]
- **D-G** TIME exit WR 9.2% (E17) 의 PHS 완전 대체 vs 기존 TIME timer 유지 후 PHS subordinate — debate 1 의 세부 구체화
- **D-H** disable flush window 기본 M일 값 (7d vs 14d vs preg 튠) + subsystem_reviewer `exclude_after_disable` flag 기본값
- **D-I** Alpaca europe_late 0-10% WR (E18) 대응 방식 — premarket eligibility block vs cell matrix session axis weight=0 migrate vs 제3안

---

## VIII. 참조

- 세부 설계: `tasks/prep_t13_hardcode_audit_and_integration.md`
- T12 관측: `tasks/observation_log_t12.md` (60h 추가 관측 commit 완료)
- 결함 증거: `tasks/anomaly_snapshot_t12.md` + `anomaly_detection_t12.sql`
- T14 후속: `tasks/next_plan_t14_performance_classification.md`
- 감사 5종: `audit_t13_code_state.md` + `audit_t13_plan_vs_code.md` + `t13_data_review_report.md` + `harness_audit_t13.md` + `~/.claude/plans/wise-skipping-crab.md`
- Memory: `handoff_unified_2026_04_24_T13_phase0_to_3` + `handoff_unified_2026_04_24_T13_phase4` (예정)

---

## 📌 최종 재확인

- 본 파일은 **v2.2 (Phase 4 정합 감사 반영)**. Jin 승인 + debate 19항 결정 후 final 로 확정.
- 모든 숫자 / threshold / priority 는 **preg/cell_learned 예정**.
- Forensic 단계 (D11.5~9) 결과에 따라 Part O / M / Phase 1.3 범위 재조정 허용.
- Per-Change Gate 4축 매 변경 자가 통과 필수.
- **Harness 업데이트 는 Plan 내부 D timeline 에 통합**: Step -1 (Phase 4) + D5 (agent 이름 확정) + D12 (hook 확장) + D15 (audit framework) + D16a.5 (session auditor 활성).
