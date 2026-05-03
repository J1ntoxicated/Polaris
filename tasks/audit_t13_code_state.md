# T13 Phase 0 — 코드 전수조사 (audit_t13_code_state.md)

> **원칙**: 단정 X. 모든 항목 "관찰 / 가설 / 확인 필요" 3분법.
> **범위**: T12 observation 에서 파생된 7종 구조 결함 + E2/E7/E14/E15/E16 증거.
> **방법**: 3 Explore agent 병렬 (exit / signal-provider / data-cell) + 직접 grep.
> **세션**: T13 부팅 (2026-04-24 Fri 08:10 AEST). 봇 PID 38961 alive (4.5d).

---

## 1. Exit 경로

### E2 — `max_profit_pct=0` DB flush 누락 가설
- **관찰**: `trade/close_handler.py:328/386/496/662` 에서 `pos.max_profit_pct` 를 DB trades.max_profit_pct 컬럼에 copy. 컬럼은 `REAL DEFAULT 0` (schema).
- **가설 A** (flush 경로 누락 아님): close 시점의 pos 객체가 peak 값을 유지하면 정상 기록.
- **가설 B** (runtime peak tracking stuck): pos.max_profit_pct 가 runtime 중 0 으로 고착되는 경로 존재 가능성 (e.g. TOUCHED/HARVEST 전이 시 reset 될 수 있는가).
- **확인 필요**: Phase 2 데이터 분석에서 실제 trades row 에 max_profit_pct=0 비중 + pnl_pct 분포 교차 → DB 기준으로 "flush 누락" vs "실제 peak=0" 구분.

### E14 — exit learner SQL unit bug 재발 방지
- **관찰**: `ticks/hourly_stats.py:665` `AVG(pnl_pct / NULLIF(max_profit_pct, 0))` 및 `:680` `AVG(max_profit_pct)` — T12 commit `f6679d95` fix 반영됨 (trail retention + BEP peak learner).
- **잔존 가설**: `hourly_stats.py:655` `p75_peak_pct = float(_pp or 0) * 100` — profit_target learner 에 여전히 `* 100` 존재. max_profit_pct 단위가 pct (5.2 = 5.2%) 라면 520 으로 왜곡.
- **확인 필요**: `:634` 반환 SQL 의 max_profit_pct 단위 + consumer 측 p75_peak_pct 가 pct 기대 or frac 기대 — unit 체인 end-to-end.

### 결함 2 — TIME→TRAIL_PROTECTED suppression
- **관찰**: `trade/exit_cycle.py:475-483` — `_max_pnl_now >= _time_to_trail_min (0.3%)` 이고 `_pnl_now > _loss_cap (-2%)` 이면 `reason = None` 로 TIME 보류.
- **가설 A** (무한 아님): suppress 된 tick 은 다음 tick 에 재평가.
- **가설 B** (state 고착 경로): pos.state 가 TOUCHED/PROTECTED/HARVEST 로 전이되면 fsm (exit_fsm.py:~399) 에서 TIME 평가 자체가 disallow 될 가능성 — 그 경우 재평가가 영원히 안 일어남.
- **확인 필요**: exit_fsm.py state machine 의 TIME 허용 state 정확 매핑 + TRAIL_PROTECTED 전이 후 peak-down 시 state revert 조건.

---

## 2. Signal / Provider 경로

### 결함 3 — alpaca asset_group=crypto 분류 오류
- **관찰**: `trade/position.py:337` `asset_group = d.get("asset_group") or d.get("group") or "crypto"` — fallback 이 무조건 "crypto".
- **관찰**: `utils/groups.py:140-220` `get_group()` 화이트리스트 기반 매핑 존재. Alpaca ticker 가 이 함수로 routing 안 되는 경로 존재 가능.
- **가설**: alpaca adapter 가 asset_group 필드를 채우지 못하고 전달하면 fallback 이 crypto 로 오분류. Phase 1.3 재분류 설계 대상.
- **확인 필요**: alpaca adapter populate site + DB position 샘플의 asset_group 분포.

### 결함 4 — Drop threshold global
- **관찰**: `signals/composer.py:268-310` 5-category drop (skipped/expired/lowconf/error/zero). `:308` `sig.confidence <= 0` 및 `:525` 두 번째 drop 지점 — 모두 provider/exchange 분기 없음.
- **가설**: 동일 threshold 를 OKX (24/7 고변동) 와 CAP (유럽 저유동) 가 공유 → signal 분포 불균형.
- **확인 필요**: provider/exchange 별 독립 threshold preg 지원 가능성.

### 결함 5 — Drop = 데이터 소실
- **관찰**: DB `signals` 테이블만 존재. `dropped_signals` / `signal_blocks` 테이블 없음. drop 시 events.jsonl 로그만 남음 (composer.py:340).
- **가설**: dropped 신호가 후속 learner 로 들어가지 못함 → provider 유효성 평가 blind.
- **확인 필요**: events.jsonl 기반 재구축 가능성 + 별도 quarantine 테이블 설계.

### 결함 6 — Provider score scale 미정규화
- **관찰**: `config/computed.py:87-131` `compute_provider_effectiveness()` 에 per-provider WR 계산 있음. per-exchange normalize 없음. `signals/providers.py` 각 provider score 독립 계산.
- **가설**: OKX funding signal 의 raw score 100 과 CAP sentiment 의 raw 100 이 동일 weight 로 비교 → 가중치 왜곡.
- **확인 필요**: z-score / per-exchange mean-std normalize layer 부재 확인 + taxonomy per_exchange 설계.

### E7 — trace_id 부재
- **관찰**: `bus.py:87-100` publish payload 에 `{ts, seq, type, data}` 만 존재. trace_id / correlation_id 없음. `trade/_pipeline_scan.py:1013-1042` entry 기록에 signal_seq/signal_ts 는 있으나 close_handler.py:291-337 의 exit 경로와 양방향 FK 없음.
- **가설**: signal→entry→exit 체인이 implicit (다중 join 필수) → forensic 불가.
- **확인 필요**: bus payload + trades/exit_events 테이블에 trace_id 추가 — Phase 3 선결.

---

## 3. Data / Cell Matrix

### E16 — Session × Exchange 극명한 성과 차이
- **관찰**: `strategy/cell_matrix.py:3-4` 6-dim (exchange × asset_group × session × regime × strategy × direction) 구현. `:55-61` `_session_8band(ts)` 8 밴드 활성 (T12 ITEM-025 확장). DB `strategy_cell_matrix` 216 rows 보유.
- **관찰**: `cell_matrix.py:232` normalized_score 계산 시 `ticker_baseline.pnl_pct_std` lookup — ticker_baseline 이 session-aggregated 일 가능성 (session-specific 아님).
- **가설 A**: session 축 자체는 작동. 극명한 성과 차이는 실제 시간대 의존성 (US core = 고유동 + 북극성 정합, Asia core = 저유동 손실).
- **가설 B**: provider score 가 session 별로 재계산 안 됨 → OKX europe_late 통계가 us_core 와 섞여 normalize → entry 가 잘못된 expected value 로 sizing.
- **확인 필요**: ticker_baseline.pnl_pct_std 의 session aggregation 여부 + `lookup_cell_score()` 에서 provider score 가 session-aware 인지. → **Debate 11항 (provider session-aware) 의 직접 근거**.

### 결함 7 — OKX coalescing 부재
- **관찰**: `exchange/okx/ws_feed.py:239-330` `_process_message()` 모든 incoming tick 즉시 처리, `_msg_count += 1`, dedup 없음. `:276-278` TickHistory ring buffer 에 전 tick 기록. `:295-300` price_hist append-only.
- **관찰**: capital/alpaca adapter 에 dedup 로직 미확인 (비대칭 가설).
- **가설**: 동일 ticker 에 짧은 시간 내 identical price 여러 건 모두 저장됨 → momentum/vol_spike 오염 → sentiment 오류 → entry precision 저하.
- **확인 필요**: OKX WS 실측 초당 tick / 중복률 + 타 거래소 coalescing 구현 유무.

---

## 4. Harness / 3대 원칙 Enforce (E15)

- **관찰**: `.claude/settings.json` permission whitelist 만. hook/pre-commit/audit skill 없음. `tasks/T13_START_HERE.md` 는 문서 원칙만, code-level enforce 아님.
- **관찰**: `utils/harness_log.py`, `ops/harness_alerter.py` 존재하나 feedback/verdict 자동 검사 hook 부재.
- **가설**: 현재 수동 리뷰 + memory/plan 문서 의존 — 재발 방지 장치 없음.
- **확인 필요**: Phase 3 에서 `before_commit` hook + simplify skill 활용 + Per-Change Gate 4축 자동화 설계.

---

## 5. Phase 0 종합 판정

### Phase 0.5 (Plan vs Code) 진입 전 확정 요점
1. E2 / E14 / 결함 2 — **Exit 경로 3건 모두 확인 필요 깊이** Phase 2 데이터 (실제 DB row) 로 증명 or 반증 필요.
2. 결함 3~6 — signal/provider 경로 **구조적 공백 확정** (코드상 분기 없음). debate 대상 아닌 구현 과제.
3. E7 — **trace_id 는 Phase 3 선결 과제** (후속 forensic 전부 의존).
4. E16 — Debate 11항 (provider session-aware) 근거 확보.
5. 결함 7 — OKX dedup 은 Tier 1 quick-win 가능성.
6. E15 — Phase 3 Harness hook 설계 대상.

### T13 debate 에 즉시 추가 항목
- **New D-A**: profit_target learner `* 100` 잔존 (hourly_stats.py:655) 제거 방식 — 단순 fix vs unit layer 전체 정비.
- **New D-B**: signal drop quarantine 테이블 scope (signal_blocks vs events.jsonl 재구축).
- **New D-C**: OKX dedup window (100ms / 1s / per-tick hash).

### 다음 (Phase 0.5) 입력
본 파일 + `plan_t13_integrated_v2_draft.md` + `prep_t13_hardcode_audit_and_integration.md` 교차 → Plan 가정 vs 현실 매칭 gap 표 작성.
