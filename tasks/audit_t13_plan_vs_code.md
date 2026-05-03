# T13 Phase 0.5 — Plan vs Code 정합 (audit_t13_plan_vs_code.md)

> **목적**: `plan_t13_integrated_v2_draft.md` 의 Pillar 1~5 + Cross-cutting H.1/H.2/H.4/H.5/H.10 이 가정하는 현실 vs 실제 코드 상태 gap 표.
> **입력**: Phase 0 발견 (`audit_t13_code_state.md`) + plan_draft 직접 read.
> **원칙**: 단정 X, 관찰 기반. Plan 가정과 현실 차이가 좁으면 **정합**, 크면 **확장 필요**, 반대 방향이면 **재설계 필요**.

---

## 1. Pillar 대조표

### Pillar 1 — Input Taxonomy + Unit Contract
- **Plan 가정**: `docs/metric_taxonomy.yaml` 신규 작성. per_exchange variants. Runtime validator `_metric_contract.py`. Cell API 진입점에서 contract 검증.
- **현실 (Phase 0)**: 두 파일 모두 존재하지 않음. 단위 혼용 실증 1건 확인 — `ticks/hourly_stats.py:655` `p75_peak_pct = _pp * 100` 잔존 (T12 fix 에서 trail/bep 만 수정, profit_target learner 누락).
- **Gap**: **정합** (Plan 이 gap 을 정확히 겨냥). 추가 실증 1건 (profit_target learner) 을 D0~D2 scope 에 포함할 필요.
- **정합도**: ✅ 정합 + 실증 1건 보강 필요.

### Pillar 2 — Multi-Matrix Hierarchical Cell
- **Plan 가정**: `CellKey` 8축 (exchange / group / ticker / session / regime / direction / liquidity_tier / strategy_id). `cell_resolve(metric, *dims)` depth fallback. `cell_learn` sample 기반 업데이트. Lifecycle seed → active → promote → dormant → retire.
- **현실 (Phase 0)**: `strategy/cell_matrix.py` 이미 6-dim (exchange × asset_group × session × regime × strategy × direction). DB `strategy_cell_matrix` 216 rows. session 8-band 활성 (ITEM-025). `lookup_cell_score()` 단일 read (composite score). **ticker 축 + liquidity_tier 축 미존재**. `cell_resolve` fallback API 없음. lifecycle (dormant/retire) 없음 (normalized 재계산은 매 tick).
- **Gap**:
  - **확장 필요** 2축 (ticker / liquidity_tier)
  - **신규 API** `cell_resolve` depth fallback + `cell_learn` + lifecycle
  - Plan 이 "신규" 처럼 기술하나 실제로는 **6-dim → 8-dim 확장 + read-only → learn/lifecycle 확장**. 6-dim 존재 사실을 plan v2.1 에 반영 필요.
- **정합도**: 🟡 확장 기반 있음, plan 표현을 "확장" 으로 수정 필요.

### Pillar 3 — 3-Tier 프로세스 + DB 스키마
- **Plan 가정**: invasion-ingest / invasion-trade / invasion-learn 독립 프로세스 3개. DB 신규 테이블 11종 (market_ticks / market_candles_* / provider_raw / feature_cache / ai_event_audits / position_health / lag_kpi_hourly / signal_queue / loss_attribution / signal_blocks / trade_events).
- **현실 (Phase 0)**: 단일 프로세스 `python3 -m invasion --headless` (PID 38961, 4.5d alive). DB `signals` 테이블만 존재 (Phase 0 Signal-Provider agent 확인). 신규 11 테이블 전부 부재.
- **Gap**: **전면 신규**. D17 (G3+G4 Tier 1 독립 프로세스) 및 D10 (G1 DB 스키마) 이 정확히 이를 겨냥. Risk: PID 분리 시 공유 DB WAL write 권한 linter 차단 필요 (plan 언급됨).
- **정합도**: ✅ 정합 + 작업량 크다.

### Pillar 4 — Position Health + Real-time Exit (Loss Forensic)
- **Plan 가정**: PHS 6-factor (price/time/signal/liquidity/correlation/regime). Fast-Out (PHS 급락 / peak reversal / signal flip). **TIME timer 폐지 또는 PHS subordinate**. Loss Attribution 5 bucket (signal / entry / size / hold / exit_timing).
- **현실 (Phase 0)**: `trade/exit_cycle.py:475-483` TIME→TRAIL_PROTECTED suppression 존재 (0.3% peak + -2% loss cap 기준). PHS 모듈 없음. Loss attribution 모듈 없음. exit_fsm state machine 존재.
- **Gap**:
  - PHS 전체 신규
  - TIME 폐지 vs subordinate 는 plan debate 1 항목 (미결)
  - 기존 TIME→TRAIL_PROTECTED suppression 이 PHS 의 원시 prototype 이라 볼 수 있음 → Phase 0 결함 2 (state 고착 가설) 가 PHS 설계 시 교훈으로 전이 가능
- **정합도**: ✅ 정합 + 기존 TIME suppression 흔적 Plan v2.1 에서 언급 권장.

### Pillar 5 — Flow Amplifier + Dynamic Signal
- **Plan 가정**: `amp_final = max(1.0, min(amp_desired, amp_flow_*, amp_slip))`. Unfillable-Queued 상태 (market closed / margin / broker reject / liq wait / kill). Dynamic Signal 5-layer evolution.
- **현실 (Phase 0)**: `signals/composer.py:268-310` 5-category drop (skipped/expired/lowconf/error/zero). drop 시 events.jsonl 로그만 남음. queue / quarantine 없음. Flow Amplifier 는 `trade/_pipeline_sizing.py` 의 `_ramp_mult` / `_conviction_mult` / `_atr_exp_mult` 가 이미 amplify-only chain 으로 작동 (T12 ITEM-026/027/030 참조).
- **Gap**:
  - Drop 경로 → Unfillable-Queued 전환 (결함 5 개선)
  - Flow Amplifier 는 **부분 정합** (chain 이미 존재, amp_flow_* 새 항목 추가 + slip 축 추가)
  - Dynamic Signal 5-layer 는 전면 신규
- **정합도**: 🟡 Flow Amplifier 부분 정합, drop 전환 + 5-layer evolution 신규.

---

## 2. Cross-Cutting H# (🔴 T13 필수 5개)

### H.1 — trace_id + `trade_events` 구조화
- **Plan**: E7 모든 forensic 기반, D8 선결.
- **현실 (Phase 0)**: `bus.py:87-100` publish payload 에 trace_id 없음. `trade_events` 테이블 없음. signal_seq/signal_ts 만 있음.
- **Gap**: **정합** (Plan 정확). D8 을 D6 (Kill Switch) 다음 바로 배치 정당.
- **정합도**: ✅ 정합.

### H.2 — Reconciliation (broker ⇄ DB, duplicate open rule)
- **Plan**: E6 CAP DB duplicate open. broker ⇄ DB 일치 검증.
- **현실**: adopted_pending / orphan_cleanup / broker_removed cascade 경로 존재 (T12 ITEM-011). 완전 reconcile loop 없음. duplicate open rule 미확인 (Phase 0 agent 언급 없음 — Phase 2 데이터 확인 필요).
- **Gap**: 부분 기반 있음, **강화 필요**. duplicate open rule 별도 신규.
- **정합도**: 🟡 부분 기반.

### H.4 — Canary + KPI Guard
- **Plan**: 배포 안전 + 북극성 보호.
- **현실**: Canary 기능 없음. `subsystem_reviewer.py` 존재하나 global SAFETY_MODE (T12 ITEM-024).
- **Gap**: **전면 신규**.
- **정합도**: ✅ 정합 (신규 확인).

### H.5 — Kill Switch (`touch data/KILL`)
- **Plan**: Black swan. D6 1순위.
- **현실**: KILL 파일 감지 경로 미확인 (Phase 2 에서 main loop / exit_cycle 확인 필요). 현재 kill_switch_dd_pct=50 preg 만 확인됨 (ITEM-005).
- **Gap**: 파일 기반 kill 은 **신규**. DD 기반 kill 은 기존.
- **정합도**: 🟡 부분.

### H.10 — DB Backup + Restore 리허설 (D7.5)
- **Plan**: 학습 데이터 보호 + restore 리허설.
- **현실**: `data/invasion.sqlite` WAL. 자동 snapshot / restore 리허설 없음.
- **Gap**: **전면 신규**.
- **정합도**: ✅ 정합.

---

## 3. Debate 항목 보강 (Plan VII 미결 + Phase 0 신규)

### Plan 원안 (10항) 정합 상태
1. TIME timer 완전 폐지 vs PHS subordinate — **Pillar 4 debate 대상**. 결함 2 (TIME→TRAIL suppress) 가 debate 의 근거 확보.
2. Cell promote/demote + Factor weight 동시 vs 분리 — Pillar 2 확장 시 순서 결정.
3. Paper → Live 전환 기준 — T14 대상 (Plan VII.3).
4. Direction 결정 로직 구조 — 결함 4 (drop threshold global) 와 연결. provider/exchange 분기 여부가 여기로 귀결.
5. Cleanup 자동화 — broker_removed cascade 개선 방향.
6. Canary 범위 — H.4 설계 시 결정.
7. 분류 프레임워크 A/B/C — T14 (next_plan_t14).
8. Event Bus AI budget / debounce — D13 설계.
9. Fallback chain 깊이 — Pillar 2 cell_resolve depth 결정.
10. Stale signal 처리 — 결함 5 (drop quarantine) scope 와 맞닿음.

### Phase 0 에서 신규 추가 제안
- **D-A** `hourly_stats.py:655` profit_target learner `* 100` 잔존 fix 방식 (단순 fix vs unit layer 정비, Pillar 1 scope 결정).
- **D-B** signal drop quarantine 테이블 scope (signal_blocks vs events.jsonl 재구축, 결함 5).
- **D-C** OKX dedup window (100ms / 1s / per-tick hash, 결함 7).
- **D-D** position.py:337 fallback `"crypto"` 교정 방식 (alpaca adapter populate 강제 vs groups.py 화이트리스트 재routing).
- **D-E** cell_matrix 축 확장 순서 (ticker 먼저 vs liquidity_tier 먼저 vs 동시).
- **D-F** **E16 provider session-aware 여부** (T13_START_HERE 11항 — ticker_baseline session-specific 전환 vs provider 자체 session axis 추가).

---

## 4. 정합 요약

| Pillar / H | 정합도 | 주 gap |
|---|---|---|
| Pillar 1 Taxonomy | ✅ | profit_target learner 실증 추가 |
| Pillar 2 Cell | 🟡 | 6-dim→8-dim 확장 + API/lifecycle 추가 |
| Pillar 3 3-Tier | ✅ | 단일 → 3 프로세스 분리 큰 작업 |
| Pillar 4 PHS | ✅ | TIME suppression 흔적 plan 언급 |
| Pillar 5 Flow | 🟡 | Flow Amplifier 부분 존재 |
| H.1 trace_id | ✅ | 전면 신규 |
| H.2 Reconcile | 🟡 | cascade 경로 기반 위 강화 |
| H.4 Canary | ✅ | 전면 신규 |
| H.5 Kill | 🟡 | 파일 kill 신규 |
| H.10 Backup | ✅ | 전면 신규 |

**결론**: Plan v2 draft 는 **대부분 정합**, 다만 Pillar 2 와 Pillar 5 는 **확장 표현** 으로 Plan v2.1 에서 수정 필요. Phase 0 신규 debate 6항 (D-A~F) 을 Plan VII 에 추가.

---

## 5. 다음 (Phase 1) 입력

- 본 파일 + audit_t13_code_state.md → `plan_t13_integrated_v2_1.md` 작성 반영
- 새 debate 6항 (D-A~F) 포함
- Pillar 2/5 "확장" 표현으로 수정
- Plan III 에 Phase 0 에서 확인된 기존 자산 (6-dim cell / TIME suppression / amplify chain) 명시
