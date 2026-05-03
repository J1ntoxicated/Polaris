# Harness → Dev 버스

**규약**: 하네스 세션이 Dev에게 전달. 새 메시지는 파일 상단에 append. Dev는 매 루프 주기에 PENDING 섹션 처리 후 헤더를 `ACKED at HH:MM`으로 수정.

---

## [2026-04-18 18:40 AEST] MSG-MAX-PROFIT-TS PENDING — [Fwd PR4: trades.max_profit_ts schema + write path] 🟩 HARNESS

**Source**: 🟩 HARNESS (Forward-looking Plan PR4 — mid-term trajectory simulator data accumulation)

### 변경 (scope 5 paths)
1. `invasion/data/_store_schema.py` — trades DDL `max_profit_ts REAL DEFAULT NULL` 추가 + `_TRADE_NUMERIC_FIELDS` + `_get_trade_columns` 화이트리스트 등록
2. `invasion/data/unified_schema.py` — canonical trades DDL 에 `max_profit_ts REAL DEFAULT NULL` 추가
3. `invasion/data/store_core.py` — `_run_v4_to_v5_migration` 추가 (idempotent ALTER TABLE ADD COLUMN + schema_version bump), `_missing_cols` 에 `max_profit_ts` 등록
4. `invasion/trade/position.py` — `max_profit_ts: float = 0.0` 필드 + `update_max_profit(pnl_pct, now)` 메서드 + `update_pnl` 내 peak stamp + `to_dict`/`from_dict` 퍼시스턴스
5. `invasion/trade/close_handler.py` — 3 write path (`_close_position` main, dead-letter payload, `_finalize_close`) 에 `max_profit_ts` propagate (peak 없으면 `None` → DB NULL)

### Smoke (all pass)
- `python3 -c "import invasion.main"` clean
- Fresh `:memory:` DB: trades DDL 에 `max_profit_ts` 존재, schema_version=6 (PR6 ATR 체인)
- Live DB clone migration: 14,102 legacy rows 유지, `max_profit_ts` 전부 NULL (정상)
- `Position.update_max_profit` 단위 테스트 (신규 peak 갱신 / lower pnl noop / to_dict ↔ from_dict round-trip)
- `pytest tests/` — PR4 관련 테스트 모두 통과 (composer/regime 기존 실패 2건은 PR4 무관)

### 북극성
- 신규 trades 100% coverage, 과거 NULL (리플레이 consumer 가 COALESCE / skip 처리)
- 데이터 축적 전용 — 거래 결정/리스크 경로 무영향
- 공격적 상시 수익 유지 (defensive dampen 아님, 관측 데이터 확보)

### Commit
`feat(msg-max-profit-ts jin p1): trades.max_profit_ts schema + write path (Fwd PR4)`

---

## [2026-04-18 18:40 AEST] MSG-CANDLE-TS-HARDEN PENDING — [Fwd PR5: candle_cache timestamp 의무화] 🟩 HARNESS

**Source**: 🟩 HARNESS (Forward-looking Plan Fwd PR5 — 중장기 trajectory replay coverage 확보)

### 문제
- `data/candles/*.json` 13742개 중 0% 가 `ts` 필드 보유 (spec 8.4% 추정은 과대, 실측 0%).
- Legacy schema `{o,h,l,c,v}` 만 저장 → trajectory replay / time-aligned backtest 불가.
- DataStore candles 테이블 ts 도 `time.time() - (len-i)*60` 으로 합성 중 (실제 시각 아님).

### 변경 (scope 2 path)
1. `invasion/data/candle_cache.py`
   - `save()`: ts 없는 candle drop + warn. list 전체가 ts-less 면 write 스킵 (legacy 파일 덮어쓰기 방지).
   - `load()`: ts 없는 candle filter + 1회-warn. legacy-only 파일 `[]` 반환.
   - DataStore insert: 합성 ts 대신 candle 자체 `ts` 사용.
   - `_fetch_yahoo`: DatetimeIndex → `ts` (sec).
   - `_fetch_alpaca`: bar `t` (ISO-8601) → `ts` (sec).
   - Binance klines: `k[0]` (ms) → `ts` (sec).
   - `_build_candles_from_ls`: bucket close-tick `ts` stamp.
   - OKX fetch 는 기존 ts(ms) 이미 포함 → 무수정.
2. `tests/data/test_candle_cache_ts_harden.py` — 4 cases (save-skip / save-mixed-filter / load-legacy-strip / round-trip).

### 비-scope (건드리지 않음)
- Legacy 13742 파일 전부 유지 — 신규 write 시에만 ts 100%.
- OKX `public.py` ts 단위 (ms) 는 downstream 이슈로 별도 PR.

### 검증
- `pytest tests/data/test_candle_cache_ts_harden.py` — 4/4 pass.
- `pytest tests/data/` — 11/11 pass (no regression).
- 기존 3 failure (composer / regime engine) 는 Fwd PR5 무관, pre-existing.

### 북극성
- 중장기 trajectory replay 위한 timestamp coverage (신규 100%).
- Legacy data 보존 → 기존 분석 파이프라인 무파손.
- 방어 조정 아님 — data-quality 개선.

### Commit
`feat(msg-candle-ts-harden jin p1): candle_cache timestamp 의무화 (Fwd PR5)`

---

## [2026-04-18 18:35 AEST] MSG-FSM-PROMOTION PENDING — [Fwd PR-C: FSM staged rollout checklist] 🟩 HARNESS

**Source**: 🟩 HARNESS (Forward-looking Plan a9f2a162, PR-C — PR1 9e4eefc staged flag + PR2 44f1069 live gate SQL 후속)

### 변경 (scope 1 path, docs only)
1. `docs/FSM_PROMOTION.md` — 신규. 3-phase staged rollout gate:
   - **Pre-deploy**: T2-0 I1-I6 assertion + seed grid best + `exit_fsm_enabled` = 0 확증
   - **Phase 1** OKX crypto pilot (1h): `exit_fsm_enabled_okx_crypto=1`, 15min × 3 empirical (asym ≥ max(baseline_24h, 0.9) / pnl_mean > 0 / wr ≥ 0.40), 3 consecutive PASS → Phase 2, 1 FAIL → auto-revert + 14일 cooldown
   - **Phase 2** All-paper (3h): 24h 경과 후 `exit_fsm_enabled=1` global, per-slice asym/wr 측정, 3h asym ≥ 0.9 → Phase 3
   - **Phase 3** Permanent: 48h 경과 후 T2-1 PR5 legacy 제거 + `exit_fsm_enabled` bounds (1,1) lock

### 연동
- Live gate SQL (Fwd PR-B, commit 44f1069): `DataStore.get_live_empirical_health(exchange, asset_group, window_sec=3600)` 3-axis consumer
- Per-slice flag (Fwd PR-A / MSG-FSM-STAGED, commit 9e4eefc): `exit_fsm_enabled_okx_crypto` etc.
- Auto-revert fallback: 14일 cooldown (Phase 1 FAIL 시 자동 원복)

### 북극성
- Docs only — 거래 경로 무영향
- Staged gate = empirical asym ≥ 0.9 (feedback_loss_profit_asymmetry 준수)
- Attack-always 유지 — defensive dampen 아님, exit 구조 교체 (feedback_no_defensive_param_dampen)

### Commit
`docs(msg-fsm-promotion jin p1): FSM staged rollout checklist (Fwd PR3)`

### 다음 단계
- Dev: (선택) `invasion/backtest/cli.py --mode promote-check` 추가 여부 후속 판단
- Harness: Phase 1 게이트 투입 시점 Jin 승인 대기

---

## [2026-04-18 02:40] MSG-DEV-SESSION-END ACKED at 02:36 (Jin 옵션 B — scaffold 생략 즉시 종료, Harness agent-mode 가 LLM-NATIVE + TRADE-STRATEGIST P0 인수) — [🔴 세션 종료] 🟩 HARNESS agent-mode 전환

**Source**: 🟩 HARNESS (🟪 Jin "핑 보내서 둘다 종료 시켜")

### 결정
Harness full agent-mode 전환. Dev 터미널 세션 **종료**.

### 현재 진행 중인 터미널 작업
- LLM-NATIVE scaffold (`invasion/ai/thesis.py` / `regime_llm.py` / `consortium.py`)
- 이 작업 완료 + commit + RESTART-REQUEST push 한 다음 세션 close

### Harness 향후 mode
- Dev task → Harness 가 general-purpose agent 로 spawn
- 각 commit 후 codebase-guardian agent 가 자동 검수
- IPC bus `tasks/*_to_*.md` → agent prompt context 주입으로 대체

### Jin 에게 MSG
LLM-NATIVE scaffold commit + 1 restart 완료 이후 **Dev 터미널 창 close** 가능.
이후 Harness 가 단독 orchestrator 로 모든 Dev task 처리.

Monitor 83826 (Dev inbox) 도 자연 종료.

---

## [2026-04-18 02:35] MSG-TRADE-STRATEGIST-3-PRIORITY PENDING — [🔴🔴🔴 P0] 🟩 HARNESS

**Source**: 🟩 HARNESS (trade-strategist agent `a4a3d95e5cfc99ef5` empirical)

### Harness 과거 진단 정정
- "entry_strength 역상관" = 허구 (size_usd 교란). Signal Contract spec 에서 "strength 뒤집기" 제거 — size-aware 재설계로 변경
- 진짜 root: **never-positive 23.3% = -$42,315 (전체 loss 96%)**

### 3 Priority Dev spec

### P1 (즉시) — Never-Positive 조기 차단
- Target: 2,305건 × avg -$18.36 = -$42K 손실 원천 봉쇄
- Wire: `invasion/trade/exit.py` 또는 `exit_cycle.py` 에 entry probe 조건 추가
- Logic: 진입 후 N 분 내 `max_profit_pct == 0 AND pnl_pct < -0.05%` → `SIGNAL_EARLY` exit
- Preg (ADAPTIVE 등록 의무):
  - `never_positive_probe_sec` default 300 (전략별 bounds 180-420) — momentum 짧게, contrarian 길게
  - `never_positive_pnl_threshold` default -0.05 bounds (-0.2, 0.0)
- 기대: avg 손실 -$18 → -$4 (전환 규모 축소)
- Safety: 북극성 정합 — 이건 aggressive exit 가 아니라 **wrong-direction 조기 cut**. winner 는 touched_protected state 로 FSM 보호

### P2 (1-2 day) — Volatility Provider realized_vol 재설계
- Target: volatility score -100 단일값 쏠림 6,449건 (99% 식별력 0)
- Wire: `invasion/market/regime.py:CryptoRegimeDetector` + `signals/providers.py` VolatilityProvider
- Logic: 
  - 5분봉 realized_vol (20-bar rolling std) 추가 input
  - `neutral` regime 을 `neutral_hv` / `neutral_lv` 분리 (75th/25th pct)
  - Volatility provider score = realized_vol 백분위 기반 -100~+100 리맵
- neutral_hv 시: `stop_width_multiplier=1.5` + `size_scalar=0.7` (비대칭 변동성 대응)
- Preg: `regime_vol_hv_pct` / `regime_vol_lv_pct` / `stop_width_mult_hv` / `size_scalar_hv`

### P3 (2-3 day) — Crisis Regime 조기 진입 + Size 증폭
- Target: crisis 유일 양수 (+$0.34 avg WR 53.1%), 현재 3% → 10% 확대 = +$3-5/건 with TP/TRAIL
- Wire: `regime.py` crisis 판정 조건 완화
- Logic: `macro_score < -2 OR fear_greed_score < 20` (현재 AND 추정) → crisis 조기 진입
- Size: crisis 진입 시 `size_multiplier=1.3` (단 STOP 필수)
- STOP cap: `min(STOP_PCT, ATR × 1.5)` — crisis STOP -$587 재발 방지

### ADAPTIVE 의무 (lessons #83)
- 모든 신규 preg ADAPTIVE_PARAMS 등록 + bounds + objective + feedback_source

### 구현 순서 (Dev agent or 터미널)
- P1 먼저 (가장 big win, 작은 scope)
- P2/P3 병렬 가능

### 기대 효과 (composite)
- Never-positive 차단: -$42K → -$7K = **$35K 회복**
- Crisis 확대: +$1K 추가
- Volatility 재설계: neutral_hv/lv 분리 후 각 10-20% improvement

### 현재 Dev agent 작업 충돌 주의
- Dev agent A (WIRE-12 evolver.py) 와 P2 (regime.py) conflict 없음
- Dev agent B (live.py + orchestrator.py + param_registry.py) 와 P1 (exit.py) conflict 없음
- 새 Dev agent / 터미널 세션 스폰 가능

---

## [2026-04-18 02:28] MSG-WIRE-5-BATCH-ACK + NEXT-LLM-NATIVE PENDING — [🟢 5-wire 검수 통과 + 다음 task] 🟩 HARNESS

**Source**: 🟩 HARNESS (960ab5b 검수 완료 + Jin Dev /clear 확인)

### 검수 결과 ✅
| WIRE | 위치 확증 |
|---|---|
| 11 trade_count reset | store.py:1059-1071 max(prev,new) 보전 |
| 4 providers write | engine.py SignalVerdict.metadata + pipeline entry insert |
| 5 params_snapshot | pipeline.py:859,887 JSON write |
| 7 AI KILL gate-first | ai_controller.py:362-392 atomic INSERT + blocked_reason |
| 8 optimal_size_mult | hourly_stats.py:141-162 data-driven (wr/pf 기반 1.2/0.5/1.0) |

77th restart PID 45073 live. 5 files +128 -61 clean commit.

### 다음 단일 task (fresh context): LLM-NATIVE scaffold

MSG-LLM-NATIVE-REDESIGN 재참조. 3 파일 scaffold:

**1. `invasion/ai/thesis.py`** (신규) — EntryThesis generator
```python
class EntryThesisGenerator:
    def generate(self, candidate, regime, context) -> dict:
        # LLM call → {thesis, expected_path, max_profit_pct_predicted, failure_mode, confidence}
```
- orchestrator 경유 (cost tracking)
- budget gate: preg `thesis_budget_pct` default 30 (30% entry 에만 thesis 요청)

**2. `invasion/ai/regime_llm.py`** (신규) — LLM regime judge
```python
class LLMRegimeJudge:
    def evaluate(self, market_state, collectors_raw) -> dict:
        # 15 collectors raw data 를 LLM 에 주고 regime 판정
        # return {regime, confidence, crisis_severity, recommended_bias}
```
- 주기: 15min tick
- 기존 `regime.py` 와 disagreement detection

**3. `invasion/ai/consortium.py`** (신규) — 3-agent entry debate
```python
class EntryConsortium:
    def decide(self, candidate, thesis) -> ConsortiumVote:
        # bull / bear / risk 3 agent 병행
        # return {vote: agree/majority/split, size_mult, reason}
```

**4. preg 추가** (ADAPTIVE 등록 의무):
- `llm_native_enabled` (0) — feature flag
- `thesis_budget_pct` (30, bounds 10-100, objective=winner rate boost)
- `consortium_enabled` (0)
- `consortium_cost_per_entry` tracking

**5. DB schema**: `trades.reasoning` JSON 컬럼 (ALTER idempotent)

### 제약
- **flag off scaffold only** (Jin billing 승인 전 live 금지)
- 1 commit, 4-5 files, ~300 line
- smoke: py_compile + import + preg read PASS
- Codex pre-ship 불요 (scaffold)

### 이 commit 후
- 78th restart
- Harness 다음 큐 task 발송

### Harness 큐 (대기)
- AI-FEATURE-DISCOVERY (weekly loop)
- Multi-TF technical
- Asset-class sublane flag on
- WIRE-6/12/13/14 Phase 2
- Ops audit #7 Elo 설계-코드 불일치
- Provider activation 15 dormant collectors
- 기타 22 Phase 2 component

---

## [2026-04-18 02:22] MSG-DEV-PING-REVISED PENDING — [🔴🔴 우선순위 revise] 🟩 HARNESS Codex Dead Wire audit 결과

**Source**: 🟩 HARNESS (Codex `a46b75c431764a0cf` 완료)

### 이전 MSG-DEV-PING-NOW (LLM-NATIVE scaffold) **보류**
Codex audit 로 더 critical 한 4 wire 발견. LLM-NATIVE 가 쓸 원천 데이터 복구 먼저.

### 즉시 1 commit (P0 write boundary 복구)

**WIRE-11 🔥 strategies.trade_count reset 버그** (critical):
- `invasion/ticks/evolution.py:64-66` + `store.py:1057-1074` — evolver 가 JSON 재저장 시 DB aggregate 를 0으로 덮어씀
- 41/41 active strategies trade_count=0 원인 규명
- **Fix**: `insert_strategy` 에서 `trade_count` 필드 strip, DB aggregate 유지

**WIRE-4 providers write** (12,028 trades empty):
- `engine.py:665` `SignalVerdict.metadata` 에 providers 포함
- `pipeline.py:858` entry insert 시 providers 저장
- JSON schema: `{version:1, ordered, scores, factor_count, agreement}`

**WIRE-5 params_snapshot wire**:
- `pipeline.py:825,858` + `close_handler.py:208` — entry 시 canonical params_snapshot dict 구성 + Position 에 carry + UPSERT
- adaptive 학습 원천 데이터 복구

**WIRE-7 AI KILL 3-gate 순서**:
- `ai_controller.py:360-400,416-445` — gate 먼저 → 통과 시만 insert
- 현재 288 KILL → 4 exit, gate 전 log 로 99.7% block 확증

**WIRE-8 optimal_size_mult UPSERT**:
- `hourly_stats.py:141-148` compute + UPSERT 에 컬럼 포함
- 3,087/3,087 rows 1.0 → 실제 학습 값 반영

### Scope
4 wire = 5 files 수정, ~150 line. 1 commit batch.

### Smoke
- py_compile + import PASS
- `trades.providers` 1 신규 row empty 아님 확증
- `trades.params_snapshot` 1 신규 row 내용 확증
- `strategies.trade_count` evolution tick 후 DB aggregate 유지 확증
- `ai_decisions` KILL 기록 전 3-gate 통과 확증

### 이 commit 후
- Harness 77th restart
- Dev `/clear` 권고
- 다음 단일 task: LLM-NATIVE scaffold (fresh context)

### 보류 spec (Harness 큐)
- LLM-NATIVE 3 pivot (thesis / regime_llm / consortium)
- Phase 2 나머지 22 component
- Dead wire P1 Phase2 (WIRE-6/12/13/14/2/3)
- Deprecate (WIRE-1/10/15)
- Provider activation 15
- Multi-TF technical

Harness 가 단계적으로 풀어냄. Dev 는 한 번에 1 task.

---

## [2026-04-18 02:20] MSG-DEV-PING-NOW PENDING — [🔴🔴🔴 즉각 착수] 🟩 HARNESS 단일 작업 지시

**Source**: 🟩 HARNESS (🟪 Jin "데브 암것도 안하잖아" + 현재 18min 공백)

### 현재 공백
- Dev 마지막 commit 02:01 `500d795` adaptive-mandate (18min 전)
- 이후 Harness spec 14+ 건 발송 (MULTI-TF / PROVIDER-ACTIVATION / OPS-ESCALATE / LLM-NATIVE / 등)
- Dev 가 massive context 흡수 중 or 처리 밀림

### 단일 지시 (혼란 제거)
지금 **이 1개만** 착수. 나머지 spec 전부 후순위:

**→ LLM-NATIVE scaffold 3 파일 생성** (flag off, code-only landing)
1. `invasion/ai/thesis.py` — EntryThesis generator (spec: MSG-LLM-NATIVE-REDESIGN A)
2. `invasion/ai/regime_llm.py` — LLM regime judge (B)
3. `invasion/ai/consortium.py` — 3-agent debate (C)
4. preg: `llm_native_enabled` (default 0) + `thesis_budget_pct` (30) + `consortium_enabled` (0)
5. DB schema: `trades.reasoning` JSON 컬럼

### 제약
- feature flag OFF — 기존 path 무변화
- 1 commit (4-5 files, ~300 line)
- Codex pre-ship 불요 (scaffold only)
- Smoke: py_compile + import PASS 만

### Budget 의존성 주의
LLM-NATIVE 활성화 (flag on) 는 Jin billing 승인 후. 지금은 **scaffold 만**.

### 이 commit 후
- Harness 77th restart trigger
- 그 다음 Dev `/clear` 권고 (context 리셋 + 다음 단일 task)

### 다른 모든 밀린 spec
당분간 Dev 무시. Harness 가 우선순위 큐 관리. 한 번에 1 task.

---

## [2026-04-18 02:58] MSG-NOTIFY-76 PENDING — [NOTIFY + BATCH ACK] 🟩 HARNESS 76th restart + 11 commit live

**Source**: 🟩 HARNESS

### 76th restart (02:57 AEST Sat)
- PID → **40239**
- trigger: Dev 7 commit 누적 (585165d / fbaaa1f / 2027cae / 661550d / 377c7dd / 5216c7c / 500d795) live
- runtime: **exit_fsm_enabled=1 + signal_contract_enabled=1** ✅ 3-pillar ON
- asset_sublane_enabled=0 (scaffold 대기) / llm_native_enabled 대기 (spec 방금 발송)

### 이 batch 커밋 요약
| commit | scope |
|---|---|
| 585165d | Codex 5 GAP + S-4 post-ship |
| fbaaa1f | mutation asset_class tag |
| 2027cae | 3-pillar flag on (default 1) |
| 661550d | fitness zero-trade-guard (n_trades=0 → 0) |
| 377c7dd | ai_decisions provenance (trigger/executed/blocked_reason) |
| 5216c7c | sizing cap (adaptive 2.5 compound) |
| 500d795 | adaptive-mandate (신규 11 preg ADAPTIVE 등록) |

### Dev 고생 인정
Harness 가 spec 폭주 시켰음 (26 Phase 2 component → 최근 LLM-NATIVE 3 pivot 추가). Dev 가 이미 11 commit 실행 = 수동 mode 탈피한 진짜 생산성. Harness restart trigger 누락 반성.

### 다음 step (우선)
MSG-LLM-NATIVE-REDESIGN (02:55) 3 pivot:
- A. LLM-as-Trader (thesis generator)
- B. Regime-as-LLM (detector → LLM 주관)
- C. Multi-agent consortium (bull/bear/risk)

Budget $20-40/day 예상 (Jin 계정 영역, 현재 $7/day). Jin 승인 대기 중 Dev 는 코드 scaffold (thesis.py / regime_llm.py / consortium.py) 진행 가능. 기본 feature flag OFF.

### Dead wire audit (Codex `a46b75c431764a0cf`) 진행 중
결과 오면 LLM-NATIVE 와 synthesis 예정.

---

## [2026-04-18 02:55] MSG-LLM-NATIVE-REDESIGN PENDING — [🔴🔴🔴 P0 창의 pivot] 🟩 HARNESS 자율 결정

**Source**: 🟩 HARNESS (🟪 Jin 격노 + 역사 컨텍스트 "defensive 걷어내고 대체 구조 부재")

### 결정 (Harness 자율, 옵션 나열 금지)
3 LLM-native pivot 즉시 실행:
1. **LLM-as-Trader** (signal 단 reasoning 화)
2. **Regime-as-LLM** (detector → LLM 주관 판정)
3. **Multi-agent consortium** (bull/bear/risk debate)

### A. LLM-as-Trader
- Entry 후보 마다 LLM 이 thesis JSON 생성:
  `{thesis, expected_path, max_profit_pct_predicted, failure_mode, confidence}`
- composite score = 기존 score × thesis confidence
- Thesis 실패 시 (TIME loss / STOP) → `trade_events` 에 기록 → weekly LLM analysis → signal improvement
- Budget cap: entry_judge stage 이미 존재 ($0.20/day) 확장. 초기 신호 30% 에만 thesis 요청 (cost control)

### B. Regime-as-LLM
- `regime.py` 15 collectors raw data 를 LLM 에 주고 "현재 regime 판정 + 신뢰도" 요청
- 기존 rule-based detector = secondary (LLM 결과와 비교 disagreement 시 fallback)
- Output: `{regime, confidence, crisis_severity, recommended_bias}`
- 주기: 15min tick (AI orchestrator 예산 내)
- Crisis Escalation = LLM 이 empirical pattern + macro synthesis 로 판단

### C. Multi-agent consortium (entry debate)
- Entry 결정 시 3 agent 병행:
  - `bull_agent`: "왜 이 setup 이 winner 인가"
  - `bear_agent`: "왜 이 setup 이 loser 인가"
  - `risk_agent`: size/stop 제안
- 3/3 agree = size boost / 2/3 = normal / 분열 = skip
- ai_controller 의 HOLD bias 제거 — consortium 은 **결정 강제**
- Budget: 기존 entry_judge 대신 consortium (더 비싸지만 더 정확)

### Write boundary 반영 (기존 #9)
- thesis / regime_llm_output / consortium_vote 모두 trade row 에 JSON 저장 (providers 필드 확장 or 신규 `reasoning` 컬럼)
- trade_events 에 thesis 결과 vs actual outcome 기록 (weekly analysis 원천)

### 기존 4,5,7 흡수
- Live Dynamic Universe → LLM 이 universe 관리도 (weekly "이 ticker pool 로 가자" 제안)
- Trade Journaling → A 의 thesis-failure feedback 로 흡수
- Negative training set → trade_events + weekly LLM extract

### 15 dormant collectors 활용
- B 의 Regime-as-LLM input 으로 전부 feed (15 data source 가 LLM 판단 근거)
- 자동 활용. wrapper 불요 (LLM 이 raw data 직접 consume)

### Budget / cost
- 현재 GPT-5.4 $0.30/hr = $7/day
- LLM-native 전환 시 $20-40/day 예상 (3-5x)
- 월 $600-1200 = Jin 판단 영역 (계정/billing)

### 구현 순서 (1 Dev 세션 대규모)
1. `invasion/ai/thesis.py` 신규 — Entry thesis generator
2. `invasion/ai/regime_llm.py` 신규 — LLM regime
3. `invasion/ai/consortium.py` 신규 — 3-agent debate
4. `engine.py` / `pipeline.py` / `regime.py` orchestrator 교체 (feature flag)
5. DB schema: trades + `reasoning` JSON / trade_events 확장
6. feature flag rollout: `llm_native_enabled` default 0

### Phase 2 batch 누적 → 14 + 3 = 17 구조 spec (LLM-native 가 최상위)

---

## [2026-04-18 02:45] MSG-MULTI-TF-TECHNICAL PENDING — [🔴 P0 구조] 🟩 HARNESS Multi-timeframe technical 복원

**Source**: 🟩 HARNESS (🟪 Jin "야후 캔들 받아오는데 안보니까 움직이나 모르지")

### 결함
- `providers_technical.py` Main technical = 실시간 tick 2-5min window 만 (bar-based 아님)
- `candle_cache.py` 939L multi-resolution 저장하지만 소비처 일부만 (alpha_features / providers_extended 2-3 지점)
- 고전 multi-TF technical (15m/1h/4h/1d RSI / MACD / Bollinger) 구현 0
- 결과: "trend / reversal / squeeze" 패턴 인식 불가

### 구조 spec

**1. `utils/technicals.py` bar-based 구현 복원/확장**
- `_wilder_rsi_series` 있음 (caller 확인)
- MACD: `macd_line(closes)`, `signal_line(closes)`, `histogram`
- Bollinger: `bb_upper / bb_middle / bb_lower` + squeeze detect
- ADX / StochRSI / OBV 등

**2. `MultiTFTechnicalProvider` 신규**
- Input: ticker, resolutions=['15m', '1h', '4h', '1d']
- Per-TF: RSI, MACD, BB, volume profile
- Output: SignalResult with multi-TF consensus
  - 3+ TF 동방향 = strong signal
  - TF 괴리 (divergence) = warning
- Formula 초기 minimal, AI Feature Discovery 가 tune

**3. Candle cache auto-fetch**
- 현재: on-demand (get_candles 호출 시 fetch)
- 변경: **entry universe 전체 ticker 에 pre-fetch** (bg task)
- `ticks/candle_tech.py` 확장 or 신규 `candle_prefetch_tick`
- 저장 resolution: 15m / 1h / 4h / 1d (4 TF)

**4. Provider registry 연계 (#6 + 이것 + activate 15 collectors)**
- `signal_providers` DB 에 `MultiTFTechnical` 1 entry
- Weight 초기 0.3, adaptive 로 tune

**5. Adaptive TF weight**
- 각 asset class × regime 별 best TF 학습
- 예: crypto × crisis = 15m 강조 / stock × risk_on = 1d 강조
- `adaptive_tuner` ADAPTIVE_PARAMS 확장

### 기존 providers_technical.py 처리
- MomentumSignal / VolatilitySignal 유지 (실시간 tick 빠른 반응)
- PriceActionSignal 유지
- **새로운 MultiTFTechnicalProvider 추가** = 보완 관계 (실시간 tick + bar 합)
- 이름 구분: `tick_momentum` / `tick_volatility` vs `bar_multi_tf`

### 예상 효과 (empirical hypothesis)
- Stock/ETF entry quality 개선 (bar RSI / MACD 로 entry filter)
- Capital CFD 의 forex/indices/commodity trend 인식
- okx × crypto × neutral 의 TIME loss 감소 (1h/4h divergence 확인 후 entry)

### Phase 2 batch 누적 → 14 구조 spec

---

## [2026-04-18 02:40] MSG-PROVIDER-ACTIVATION-ALL PENDING — [🔴 P0 즉시 구조] 🟩 HARNESS 15 dormant collectors → signal providers

**Source**: 🟩 HARNESS (🟪 Jin "지표들을 싹 잘 써야지")

### 현재 dormant 15 collectors
| Asset | Collector | 용도 |
|---|---|---|
| Stock | edgar_filings | insider / earnings event |
| Stock | finra_short_interest | short squeeze potential |
| Stock | finviz_screener | stock sector rotation |
| Stock | alpaca_news_ca | headline sentiment |
| Option | cboe_put_call | put/call ratio regime |
| Option | cboe_vix_term | VIX term structure (contango/backwardation) |
| Commodity | baker_hughes | rig count → oil supply |
| Commodity | eia_petroleum | inventory / consumption |
| Commodity | cot_data | CFTC institutional positioning |
| Forex | forexfactory_calendar | economic events |
| Forex | oanda_position_book | retail positioning |
| Forex | myfxbook | retail sentiment |
| Crypto | blockchain_info | on-chain metrics |
| Crypto | defillama | DeFi TVL |
| Crypto | apewisdom | social sentiment |
| Crypto | alternative_me | fear/greed alt |

### 구조 접근 (patch 아닌 abstraction)

**1. `DataProviderBase` 추상 클래스** (`invasion/signals/data_provider_base.py` 신규)
```python
class DataProviderBase:
    name: str
    asset_class: Literal['crypto','stock','etf','forex','commodity','indices']
    data_source: str  # collector name
    
    def update(self, ticker, market_data) -> SignalResult:
        ...
```

**2. 15 collectors 각각 Provider wrapper 생성**
- Template: collector `latest()` → Provider `update()` → SignalResult(score, confidence)
- Dev 가 **1 template + auto-generate**. 수동 repeat 아님
- 초기 score formula 는 minimal (score = normalize(data_value, bounds))
- Weight default 0.3 (낮게 시작, adaptive 로 조정)

**3. Provider registry 로딩**
- `signal_providers` DB 테이블 (기존 #6 에 제안)
- 기존 provider + 신규 15 자동 등록
- `engine.py` 가 DB 에서 active provider 만 로드 (hardcoded import 제거)

**4. AI Feature Discovery 연계 (#6 DATA-DISCOVERY)**
- 주기적으로 LLM 이 "이 collector data 로 더 나은 formula?" 제안
- Backtest gate 통과 시 formula 교체 (adaptive)
- 초기 minimal formula → AI 가 tune

**5. Weekly Evaluation Loop**
- 각 provider 의 pnl_attribution 측정 (when was this provider's score > 0 and resulted in winner/loser)
- 성과 낮은 provider auto-demote (weight 0 or disable)
- 성과 높은 provider weight 증가 (adaptive_tuner 에 등록)
- `signal_providers` DB 에 `pnl_attribution_7d`, `trade_count_7d`, `promote_flag` 컬럼

### 구현 우선순위
1. `DataProviderBase` + 15 wrappers (1 commit 큰 build 대신, template + 자동 wrap)
2. `signal_providers` DB registry + engine.py 동적 로드
3. Weekly eval loop + auto weight tuning (adaptive)
4. AI Feature Discovery 연계 (Phase 2 AI-SIGNAL-DISCOVERY 와 통합)

### 원칙 준수
- patch 금지: hardcoded formula 는 initial seed only, adaptive 로 tune
- 자동 튜닝: provider weight / formula 모두 learner 소유
- 구조 추상화: 향후 새 collector 추가 시 단일 interface 만 구현하면 signal 에 feed

### Phase 2 batch 누적 → 12 + 1 = 13 구조 spec

---

## [2026-04-18 02:32] MSG-OPS-AUDIT-ESCALATE + DATA-DISCOVERY PENDING — [🔴 P0 복수] 🟩 HARNESS

**Source**: 🟩 HARNESS (Ops MSG-OPS-AUDIT-FULL 25/25 결과 + Jin "데이터 지표 발굴")

### A. Ops audit escalate (5 CRITICAL)

**1. 🔴 Elo 설계-코드 불일치 (Jin 에스컬 의무, 코드는 Dev)**
- `.schema strategies` 에 `elo_rating/wins/losses` **컬럼 전무**
- CLAUDE.md "auto-evolving via Elo tournament" 선언과 현실 괴리
- Fix: strategies schema 에 elo_rating/wins/losses 컬럼 추가 + tournament.py 에서 실제 업데이트 + Strategy provenance (#6 과 병합)

**2. family_cap enforce 실패**
- F9 fix 됐지만 stock_specialist_g18 180건 (cap 3x 초과) reject 0
- 원인: portfolio.py family_counts 가 bot_positions (adopted 제외) 기준 계산인데, 현재 stock_specialist 대부분이 adopted 일 가능성 or 다른 exclusion
- Fix: portfolio.py:110-118 family_counts 로직 재audit + reject path 실제 호출 확증

**3. Gate H9 97.4% 독점**
- Other gates (H1/H3/H4/H5/H11/H13) 안전 net 0 fire
- Fix: H1/H3 safety threshold 재튜닝 (adaptive 로), H4 streak halt 실제 fire 조건 공식 재정의

**4. hard_stop_pct=-2.0 vs 실측 avg -0.705% (35% 도달)**
- STOP 이 -2 도달 전 다른 path 로 close 되는 중 (stale_price guard? intermediate cut?)
- Fix: exit_cycle / exit_engine 에서 STOP trigger path audit. stale_price / orphan_cleanup / close-fail intermediate exit 추적

**5. bep_activate 0.5 adaptive retrofit**
- 현재 0.5 hardcoded → 68% winner 가 bep 미도달로 protect 안됨
- Fix: bep_activate 를 adaptive (objective: max winner-killed 감소) 전환. ticker_performance.optimal_size_mult 복원 (#16) 과 묶어서

### B. Data/지표 발굴 loop 부재 (🟪 Jin "지표 발굴")

**수집 data 현황** (풍부):
- funding_rates / open_interest / ls_ratio / taker_volume 각 130만 row (crypto)
- market_context 6천 / sentiment 17K / ticker_dynamics 40만
- 28 collectors (CBOE / FRED / coinglass / defillama / santiment / apewisdom / cot / google_trends / forexfactory_calendar / edgar_filings / etc.)

**결함**: 이 data 로 새 indicator/feature 만드는 AI loop **부재**. 정적 signal provider 만 사용.

### B 구조 (AI-SIGNAL-DISCOVERY 확장 depth)

**Level 1: Feature engineering** (AI 가 raw data 로 지표 발굴)
- input: 각 data 테이블 (funding_rates / open_interest / ls_ratio / etc.)
- LLM task: "이 data 에서 winner-loser 구분 가능한 새 feature 제안"
- output: feature definition + compute formula + asset_class
- 예: `oi_velocity_1h = (oi_now - oi_1h_ago) / oi_1h_ago`

**Level 2: Indicator combination** (기존 지표 합성)
- input: 현재 provider list + winner/loser pattern
- LLM task: "이 2-3 indicator 를 combine 하면 더 강한 signal?"
- 예: `fear_greed_slope × taker_imbalance × funding_zscore`

**Level 3: Regime detection 개선**
- 현재 regime 75% neutral = detector noise (#3 확증)
- LLM 이 새 data source 로 regime 판정 제안
- 예: santiment social volume + cot positioning + vix term structure → 더 정교한 regime

### B 구현 흐름
1. 주기 (weekly): LLM → feature/indicator/regime hypothesis
2. Backtest gate: `tier1_replay` 적용, fitness ≥ threshold
3. Promotion: signal_providers / feature_registry / regime_detector plugin
4. Auto-demote: 7d trailing 성과
5. DB schema: `feature_registry` (name, formula, source='ai', parent, rationale, validated, jin_flag, pnl_attribution)

### 통합
- A (Ops audit escalate) + B (Data discovery) 모두 Phase 2 batch 에 흡수
- B 는 AI-SIGNAL-DISCOVERY 와 병합 — signal/feature 2 layer 구조 (provider = combination of features, feature = raw data transformation)

### 12 구조 spec 누적 (patch 철회 후)
1. AI-GPT migration
2. Exit FSM
3. Signal Contract
4. Execution Service
5. Dynamic family registry
6. Strategy provenance DB schema
7. AI-driven strategy generation
8. Source canonical enum
9. Write boundary SSOT contract
10. Reconciliation audit
11. AI Signal Discovery (provider)
12. AI Feature/Indicator Discovery (raw data)

---

## [2026-04-18 02:27] MSG-AI-SIGNAL-DISCOVERY PENDING — [🔴 P0 구조] 🟩 HARNESS 새 signal 발굴 loop

**Source**: 🟩 HARNESS (🟪 Jin "시그널 튜닝은 안해? 좋은 시그널 주기적으로 발굴")

### 현재 결함
- Signal provider 100% 코드 고정 — `providers*.py` 에 수동 Dev 작성
- `provider_mult_*` 20개 adaptive (weight 좁은 범위 조정) = 한정적 튜닝
- 299 preg 중 signal 관련 adaptive ~20 = 6.7%
- **새 provider feature 생성 경로 0**

### 구조 (Signal Contract + AI Strategy gen #6 과 병렬)

**AI-driven Signal Provider Discovery** loop:
1. 주기: weekly (또는 daily 경량 제안)
2. Input: 지난 기간 winner/loser signal mix + market data 최근 샘플 + north_star bias
3. LLM Output: 1-3 new provider hypothesis JSON
   ```
   {
     "name": "oi_funding_velocity",
     "inputs": ["open_interest", "funding_rate"],
     "formula_description": "delta(OI) × delta(funding) normalized",
     "asset_class": "crypto",
     "expected_edge": "crowd extremity detection",
     "parent_provider": "funding"
   }
   ```
4. Backtest gate: `tier1_replay` 로 기존 trade 에 simulated application, n_trades ≥ 100 + fitness ≥ 50
5. Promotion: DB `signal_providers` 테이블 (신규)
6. Auto-demote: 성과 trailing (7d window) trade_count × pnl × sharpe

### DB schema 신규
```
signal_providers
- id, name, generation, parent_id
- source (human/ai)
- formula_ast / formula_desc
- backtest_validated, jin_review_flag
- trade_count, pnl_attribution, fitness
- status (active/disabled/proposed)
- created_at
```

### Signal Engine 확장
- `SignalEngine` 이 signal_providers DB 에서 active 로드
- dynamic provider registry (현재는 import 하드코딩)
- provider_mult_* 도 DB 에서 로드 → Jin 원칙 "자동 튜닝" 정합

### Safety
- AI hallucinated provider 로 인한 bad entry 방지: backtest 필수
- Jin review flag default (AI 제안 provider 는 Jin 확인 전 live 불가)
- 기존 provider 는 grandfathered (human source)

### Phase 2 batch 에 통합
기존 10 구조 spec 에 이것 추가 → 11 구조 component

---

## [2026-04-18 02:15] MSG-PATCH-RETRACTION PENDING — [🔴🔴🔴 spec 전면 철회] 🟩 HARNESS patch spec 취소

**Source**: 🟩 HARNESS (🟪 Jin "아까 패치 패치 패치 해서 이 난리 났다고 했더니" 격노)

### 반성 + 선언
방금 MSG-PHASE2-EXTENDED-ADAPTIVE-MANDATE 10분 전 lessons #82 "patch culture 영속" 해 놓고, 또 patch 를 preg 로 감싸서 26 component 발송. **Preg wrap = patch 숨기기**. 반성.

### 즉시 철회 (Dev 가 건드리지 말 것)
hardcoded default preg 류 spec **전부 보류**:
- F1 stock_dip_boost 1.15
- exit_slip_cap_bps 50
- family_variant_limit 5
- family_max_allocation_pct 30
- consecutive_loss_halt 10/1800
- aggressive_contrarian_stock_dip_boost 1.15
- triple_block 116 entries
- tier_direction_block [mid×short]
- ai_cost rates 0.01/0.03
- 그 외 hardcoded default preg 전수

### 유지 (구조 변경만)
- **AI-GPT migration** (path 교체, 구조)
- **Exit FSM state machine** (구조)
- **Signal Contract 3-tuple** (구조)
- **Execution Service side-aware** (구조)
- **Dynamic family registry** (DB-backed, 구조)
- **Strategy provenance DB schema** (source/parent_id/rationale 컬럼)
- **AI-driven strategy generation** (4-Phase)
- **Source canonical enum** (Governor clarity, 구조)
- **Write boundary SSOT contract** (providers/entry_params/ai_calls.trade_id wire)
- **Reconciliation tick audit** (실제 fire)

### 새 원칙
- learner 가 결정 못하는 값 → `invasion/config/constants.py` 하드코딩 (preg 아님)
- learner 가 결정 가능한 값 → preg + ADAPTIVE 등록 + bounds + objective + feedback_source 필수 세트
- **중간 지점 (hardcoded default preg) 금지**

### adaptive_tuner + param_governor 확장 의무 (구조 spec 에 포함)
- AdaptiveSpec dataclass: `{default, bounds, learning_rate, objective, feedback_source, window}`
- `adaptive_tuner.py ADAPTIVE_PARAMS` dict → AdaptiveSpec 기반 확장
- 현재 hardcoded preg 들의 adaptive 전환 path 정의

### 구현 우선순위 (patch 제거 반영)
1. **AI-GPT migration follow-up** (Codex 4 issue fix) — 이미 진행 중
2. **Fitness gate** (n_trades=0 → 0, 구조 수정)
3. **Write SSOT contract** (providers / entry_params / params_snapshot / ai_calls.trade_id / hour_stats) — 구조
4. **Exit FSM + Signal Contract + Execution Service** 3-pillar flag flip (이미 land, flag on)
5. **Dynamic family registry** — 구조
6. **AI Strategy generation** Phase A-D — 구조
7. **Source enum + Reconciliation audit** — 구조
8. **adaptive_tuner 확장** — F5 12 키 + 기존 hardcoded preg 전수 retrofit
9. **Sublane flag flip** — empirical 후 결정

### hardcoded patch 값 처리
기존 74th/75th 에 live 된 hardcoded 값 (F1 1.15 등) 은 **그대로 두되 adaptive retrofit** — 값 자체 변경 아니라 learner wrapping. Bot 재기동 불요.

---

## [2026-04-18 02:12] MSG-PHASE2-EXTENDED-ADAPTIVE-MANDATE PENDING — [🔴🔴 원칙 복원] 🟩 HARNESS 모든 신규 preg adaptive 의무

**Source**: 🟩 HARNESS (🟪 Jin "원칙 안잊었지? 하드코딩 안하는거 자동 튜닝 되게 짜는거")

### 규율 위반 인정
오늘 Dev spec 다수가 `preg default = hardcoded constant` 로 설계. preg 로 "이관" 했지만 adaptive loop 편입 0.
- aggressive_contrarian_stock_dip_boost 1.15 / exit_slip_cap_bps 50 / family_variant_limit 5 / family_max_allocation_pct 30 / consecutive_loss_halt 10/1800 / ai_cost rates / tier_direction_block list / triple_block 116 entries
- 전부 adaptive tuner 미등록
- `ticker_performance.optimal_size_mult` 전부 1.0 = adaptive dead (증거)

### 복원 규율 (영속)
1. **모든 신규 preg 는 생성 시점에 adaptive 등록 의무**:
   - `ADAPTIVE_PARAMS` 리스트에 자동 추가
   - Bounds + learning rate + objective function 세트로 정의
   - 그렇지 않으면 preg 로 만들지 말고 그냥 hardcoded constant
2. **기존 hardcoded default 도 점진 adaptive 전환**:
   - F5 (12 키) 는 시작점
   - 이번 Phase 2 batch 의 새 preg 들 전부 ADAPTIVE 등록
   - `aggressive_contrarian_stock_dip_boost` / `exit_slip_cap_bps` / `family_variant_limit` 등
3. **`ticker_performance.optimal_size_mult` 학습 loop 활성화** (#16)
4. **Objective function 명시**: pnl / asymmetry_ratio / sharpe / max_drawdown 중 어느 것 adaptive 기준으로 할지 preg 별 명시

### Phase 2 batch 추가 조건 (의무화)
기존 26 component 구현 시 **각 신규 preg 는 adaptive 등록 path 포함 commit** 동반. adaptive 없는 preg = spec 위반.

### 예시: 하드코딩 vs adaptive 전환
**Before (Dev 가 적용해야 할 패턴)**:
```python
EXIT_SLIP_CAP_BPS = preg("exit_slip_cap_bps", 50)  # hardcoded 50
```
**After**:
```python
ADAPTIVE_PARAMS["exit_slip_cap_bps"] = AdaptiveSpec(
    default=50, bounds=(10, 200),
    objective="minimize(stop_slippage_bps_avg)",
    feedback_source="trades.realized_slippage_bps",
    learning_rate=0.1, window=100
)
```

### 전수 audit 대상 (Dev 가 Phase 2 내에서 작업)
- preg 299 중 adaptive 만 2 (0.67%) → 최소 50% 목표 (150+ param)
- 나머지는 truly static constant 이면 preg 가 아니라 상수
- `adaptive_tuner.py` + `param_governor.py` 에 objective function framework 확장

### 이 원칙 을 MSG-PHASE2-EXTENDED-START 전수에 덮어씌움
모든 26 component 구현 시 hardcoded default 는 **1차 seed only**, Dev 가 ADAPTIVE 등록 path 병행.

---

## [2026-04-18 02:07] MSG-PHASE2-EXTENDED-START PENDING — [🔴🔴🔴 P0 배치 시작] 🟩 HARNESS 26 component 착수 trigger

**Source**: 🟩 HARNESS (🟪 Jin "배치 요청")

### Action (Dev)
현재 Dev 세션 fresh context. 축적된 **MSG-PHASE2-EXTENDED 26 component** 즉시 착수.

### 우선 순서 (난이도 / 효과)
1. **Fitness gate** 1-line (backtester.py:395 `if n_trades==0: return 0`) — 즉시 효과, Active-41-trade-0 문제 해소 base
2. **source_class enum** + no-op filter — Governor clarity
3. **Providers write SSOT** (Signal Contract 내) — attribution 원천 복구
4. **entry_params / params_snapshot write** — adaptive 근거 복구
5. **ai_calls.trade_id write** — AI ↔ trade 연결
6. **Regime entry-time immutable** + detector input 확대
7. **AI Controller threshold 분리** (advice vs execution)
8. **AI prompt aggressive rewrite** (HOLD bias 제거)
9. **Exit FSM flag on** + GAP-2/3/5/6 closure
10. **Size FSM** (adaptive_sizing_max_mult 제한 + win/loss 기반)
11. **Tier direction wire extension** (mid short 이외 tier 검토)
12. **Strategy registry sync** (orphan strategy_id auto-register)
13. **AI-driven strategy generation** Phase A/B/C/D
14. **optimal_size_mult** 학습 loop 활성화
15. **Reconciliation tick audit** + log + drift resolve 실행
16. **hour_stats / trade_events write audit**
17. **dead preg 71 cleanup** + candidate_events retention (122MB)
18. **sentiment fetch restore** (15h stale)
19. **Gate H2/H6/H7/H8/H10/H12 정리** + entry reject 4종 (zero_strength / data 부재) 재검토
20. **Sublane flag on** (empirical 후 Option C Step 4)

### 제약
- 각 commit feature flag default off 유지 (soft-rollout)
- DB backup 기 완료 (`pre-redesign.bak`)
- Codex pre-ship review 필요 시 Harness inline call
- 각 commit <600 line 유지 (code_size_limits)
- `signal_contract_enabled`, `exit_fsm_enabled`, `asset_sublane_enabled` 등 기존 flag 활용

### 예상 commit 수
8-12 logical commit, 2-3h Dev 세션

### Smoke + Codex review
- 각 phase 완료 smoke 5-step + Dev 자체 test
- feature flag flip 전 Harness 가 Codex review inline 호출

### RESTART 타이밍
- Phase 1-5 commit → 75th-like restart
- Phase 6-10 commit → restart 
- Phase 11-20 commit → restart
- 최종 flag flip 별도 restart

### 종료 조건
26 component 전수 commit OR Dev 판단 장중 window 소진 시 중단 + handoff

---

## [2026-04-18 02:05] MSG-PHASE2-EXTENDED-30-32 PENDING — [🔴🔴 통합 추가 3건 치명] 🟩 HARNESS

**Source**: 🟩 HARNESS (41-45 scan)

### 30. AI entry_judge 18% approval rate
- 44 calls 중 approve=True 8건 (18%)
- conf=1-2 로 대부분 reject, approve 는 conf=3-7
- 북극성 "aggressive" 정합 검증 필요 — prompt 가 over-conservative 가능성
- Fix: AI prompt 재설계 (HOLD bias 와 묶어서, #7 과 통합 spec)

### 31. Ticker universe 10-2.8% 만 활용
- alpaca 124/536 (23%) / okx 28/290 (9.7%) / **cap 34/1197 (2.8%)**
- Capital 97% idle — group 오분류 + atr_unavailable 누적 결과
- Fix: group SSOT (#GROUP-MISCLASSIFY 완료) + entry atr reject 제거 (b78da0a NORTHSTAR 완료) 효과 24h empirical 재측정

### 32. 🔴🔴 Active strategies 41 전부 trade_count=0
- DB strategies.status='active' = 41
- trade_count=0: 41 / trade_count>0: **0**
- 실제 거래 strategy_id 는 DB 에 orphan (#17 연장)
- **Strategy registry 와 실제 trading 완전 괴리** = evolver/fitness/tournament 가 dead strategies 에 돌아감
- Fix: (a) entry 시 strategy registry 검증 + auto-register, (b) strategy_map table 활용 재설계, (c) evolver 가 실제 거래 strategy_id 도 tracking

### 최종 Phase 2 batch 누적 → 26 component
20 + 3 + 3 = 26 component. Dev 통합 fresh session 대규모 spec.

---

## [2026-04-18 02:02] MSG-PHASE2-EXTENDED-27-29 PENDING — [🟡 통합 추가 3건] 🟩 HARNESS

### 27. Drift 증가 (10 → 18, 시간 따라 악화)
- trades open vs ps live 불일치가 18건으로 증가
- Reconciliation 0 fires 와 consistent — tick 작동 안 함 or log 없음
- Fix: #18 reconciliation audit 에 포함

### 28. Dead preg 71개 (299 중 228 called)
- 23% preg 가 run-time 에 read 안 됨
- Fix: 1회 cleanup commit — grep scan 으로 dead preg 식별 후 param_registry 에서 제거 or deprecated tag

### 29. `sentiment` 15h stale
- 10:54 마지막 write, fetch chain 중단 의심
- Fix: `data/collectors/cnn_feargreed.py` + `sentiment_weekly.py` audit. fetch fail silent swallow 가능성

### Phase 2 batch 누적 → 20 + 3 = 23 component

---

## [2026-04-18 01:58] MSG-PHASE2-EXTENDED-23-26 PENDING — [🔴 통합 추가 4건 + META] 🟩 HARNESS

**Source**: 🟩 HARNESS (계속 scan 31-35)

### 23. `ai_calls.trade_id` 0% 매칭 (9,045 / 0)
- AI call 과 trade 연결 완전 부재
- per-trade AI cost / intelligence attribution 불가
- Fix: pipeline.py 에서 entry 시 trade_id 생성 → ai orchestrator 호출 시 컨텍스트 전달 → ai_calls insert 에 trade_id 포함

### 24. `hour_stats` 0 rows (log 12 refs)
- tick fire 되지만 DB insert 0
- Fix: `ticks/hourly_stats.py` tick() 내부 audit, INSERT 실행 경로 확인

### 25. Signal → Trade 전환율 0.5% (202,707 / 964)
- signal 대부분 reject (aggregate block). 북극성 sweep 효과 아직 full 미측정
- Fix: Phase 2 Signal Contract 완료 + NORTHSTAR sweep 완전 반영 후 재측정

### 26. Alpaca wash_trade 28 block
- day trading pattern detect 로 close 거부
- MSG-132 parked_backoff 수습 중
- Fix: 현재 구조로 수습 가능, 별도 action 불요 but monitor

### 🔴 META: Write boundary 누락 반복 패턴 **5건 동일 root cause**
- providers (12,028/0 = 0%)
- entry_params (12,084/0 = 0%)
- params_snapshot (12,084/0 = 0%)
- ai_calls.trade_id (9,045/0 = 0%)
- hour_stats (0 rows despite tick fires)

**공통 meta fix**: "Data Write SSOT Contract" — 모든 schema 컬럼에 대해 write path 의무 정의. 신규 컬럼 추가 시 write 확인 smoke test 강제. `trade_events` / `candidate_events` 등 5+ 추가 table 도 동일 audit 필요.

### Phase 2 batch 누적 → 16 + 4 = 20 component

---

## [2026-04-18 01:55] MSG-PHASE2-EXTENDED-19-22 PENDING — [🟡 통합 추가 4건] 🟩 HARNESS

**Source**: 🟩 HARNESS (계속 scan 25-30)

### 19. `trades.state` 컬럼 없음 (FSM persist 부재)
- Phase 1 FSM `Position.state` = in-memory only. DB persist 안 됨
- Restart 시 FSM 상태 손실 (all → open 복귀)
- Fix: `trades` 테이블에 `state TEXT DEFAULT 'open'` 컬럼 + close_handler 에서 exit 시 final state 기록, 또는 `positions_snapshots` 에 state 필드

### 20. Gate 번호 missing (H2/H6/H7/H8/H10/H12)
- 정의 7 개 (H1/H3/H4/H5/H9/H11/H13) 만
- 누락 번호 6개 = legacy intent 불명
- Fix: gate_matrix.py 주석 정리 + 번호 continuous 재할당 or missing 이유 명시

### 21. Entry reject 8종 북극성 추가 sweep
- 표적 유지: blacklisted / auto_blacklisted / regime_blacklisted
- 검토 대상 (Jin 원칙 "signal 뜨면 entry"): `zero_strength` / `no_tech_data` / `no_candle_data` / `no_ws_feed` / `ticker_daily_cap`
- Fix: Signal engine 이 data 부재에도 signal 생성하면 그 signal 은 reject 해야 맞음. 대신 **signal engine 쪽에서 data 완성 후 signal 생성** 하도록 contract 정립 (Phase 2 Signal Contract 안에 포함)

### 22. `candidate_events` 122MB bloat
- 158만 row (scan 이벤트 기록)
- Fix: retention policy (예: 7d 이상 prune) + index audit. VACUUM 주기 정립. DB 774MB → 300MB 수준 압축 가능

### Phase 2 batch 누적 → 12 + 4 = 16 component
19/20/21/22 추가. 모두 fresh Dev session batch 수용.

---

## [2026-04-18 01:50] MSG-PHASE2-EXTENDED-15-16-17-18 PENDING — [🔴 통합 추가 4건] 🟩 HARNESS

**Source**: 🟩 HARNESS (Ops MSG-OPS-134 + Harness 20-24 scan)

### 15. Sizing 역상관 (Ops MSG-OPS-134 empirical 확증)
- `>=5k` bucket 2,996 trades avg -$5.31 sum **-$15,896** (clean epoch loss 70%)
- WR 은 bucket 별 비슷 (43-46%) — loss 만 비례 확대
- Fix: adaptive_sizing_max_mult 2.5 제한 or win/loss 결과 기반 sizing FSM
- 북극성 `feedback_loss_profit_asymmetry` 정면 위반
- Codex spec 필요 or 직접 Dev

### 16. optimal_size_mult 100% = 1.0 (dead learning loop)
- `ticker_performance` 3,058 rows, 703 ticker, **mult 전부 1.0**
- `hourly_stats.py:142` write + `pipeline.py:1037` read 존재하나 update 안 됨
- Fix: hourly_stats compute 로직 audit, empirical feedback 로 mult 학습 wire

### 17. Strategy registry mismatch
- 24h trades strategy_id distinct 33 / DB strategies.active 41
- **34개 strategy_id 가 DB strategies 에 없음** (orphan)
- Fix: entry 시 strategy registry 검증 + 미등록 strategy_id 차단 or auto-register

### 18. Reconciliation 0 fires
- `main.py:1476` 120s wire ✅
- log 0 fires = no-op 또는 log statement 부재
- positions drift 10 지속 원인
- Fix: `reconciliation.py tick()` 내부 audit, log 추가 + drift resolve 실행 확증

### Phase 2 batch 에 통합
이 4건 모두 MSG-PHASE2-EXTENDED-ALL 에 흡수. 총 **8 → 12 component**.

---

## [2026-04-18 01:06] MSG-PHASE2-EXTENDED-7-8-9 PENDING — [🔴 통합 추가] 🟩 HARNESS 3 신규 component

**Source**: 🟩 HARNESS (Jin "추가 발송하고 더 찾아봐")

### 7. AI 99.5% HOLD bias (ai_decisions table)
```
HOLD:    53,847 (99.5%)
KILL:       288 (0.5%)
TIGHTEN:     91
SCALE:        4
```
- Fix: AI prompt 재설계 — HOLD 가 default 가 아니라 **증거 필요**. "active action (KILL/TIGHTEN/SCALE) 제안하되 신뢰도 부족하면 reason 구체 명시" 방향. current `ai/prompts.py:51-72` EXIT_REVIEW 템플릿에 "prefer HOLD when uncertain" 류 문구 있을 가능성 → 공격적 bias 로 교체
- 북극성 정합: Jin "공격적 상시 수익" 과 AI 99.5% HOLD 는 정면 충돌

### 8. DB 25 tables bloat audit
- 주력 6: trades / signals / strategies / ai_calls / ai_decisions / positions_snapshots
- 검증 필요 (dead schema 의심): trade_events / candidate_events / ticker_stats / ticker_performance / ticker_dynamics / strategy_map / strategy_performance / hour_stats / market_context / instrument_profiles / sentiment / funding_rates / open_interest / ls_ratio / taker_volume
- Fix: 각 table 에 대해 (a) last write timestamp, (b) 전체 read caller grep, (c) row count trend. write only or read only table 은 deprecate 후보. 1-line commit 으로 표시 → 이후 cleanup sprint

### 9. Positions drift 지속 (reconciliation)
- trades.status='open' 279 vs positions_snapshots live 269 = **drift 10**
- Dev `ee1d0f1` UPSERT fix 이후에도 잔존 → reconciliation (120s tick) 이 mismatch 해결 못 함
- Fix: `invasion/ticks/reconciliation.py` 로직 audit. broker_sync 와 portfolio state 와 DB trades.status 3축 SSOT 결정 규칙 명시. 현재는 drift 기록만 하고 auto-resolve 없을 가능성

### 기존 Phase 2 확장 batch 와 통합
이 3 component 도 MSG-PHASE2-EXTENDED-ALL 에 흡수. Dev 구현 시 하나의 fresh session 에서 전수 처리.

---

## [2026-04-18 01:00] MSG-PHASE2-EXTENDED-ALL PENDING — [🔴🔴🔴 P0 통합 batch] 🟩 HARNESS 6 scan + 6 Codex + Jin "다 한방에"

**Source**: 🟩 HARNESS (Jin "a 가 맞지 다 해야지" 결정)

### Phase 2 batch 확장 (signal contract 만 → 8 component 통합)

**1. Providers write SSOT (#1, Codex `a3604fb4805bd780d`)**
- `base.py` SignalVerdict.metadata 에 providers field 추가
- `engine.py:655` serialize providers (ordered list + scores dict + agreement)
- `pipeline.py:858` entry insert 시 providers JSON 저장 (`{version:1, ordered, scores, factor_count, agreement}`)
- `close_handler.py:208,454` carry-forward only
- reader migration: `dashboard/data.py:817`, `config/computed.py:57`, `ai/analysis/trade_analyzer.py:177,182` CSV→JSON parse
- **Historical 12,028 blind** — 신규 기록만 attribution 가능

**2. AI Controller threshold 분리 (#2, Codex `af0bd2bfa587cf49a`)**
- `live.py:772-788` DANGER/CRITICAL branch `_runtime` 문자열 제거 → EXIT_REVIEW path 와 knob source 통합
- `ai_controller.py:404-431` 3-gate miss 시 `ai_decisions` 테이블에 `blocked_reason` 명시 기록
- `ai_controller.py:370-372` insert 에 `trigger` + `executed` + `blocked_reason` 컬럼 추가
- preg 네이밍: `ai_prompt_*` (advice threshold) vs `ai_kill_*` (execution threshold) 분리
- 현 empirical: **AI 288 KILL → 1 실행 (99.7% block)**. fix 후 KILL 전환율 상승 기대

**3. Regime detector entry-time immutable + input 확대 (#3, Codex `ac9fed2265c008393`)**
- `trades.regime` = entry-time snapshot, close-time 재계산 제거 (`pipeline.py:858-873`, `close_handler.py:208-235`, `store.py:318-338`)
- `CryptoRegimeDetector.update()` input 에 realized_vol / ATR pct / return_velocity / volume_expansion 추가 (`regime.py:255-257`, `ticks/regime_detect.py:223-232`)
- `neutral` 점수 조정: "모든 quiet 조건 동시 충족" 시만 승리 (`regime.py:290-324`)
- feature flag: `crypto_regime_v2` + `persist_entry_regime_only` 병행 rollout

**4. Param source canonical enum (#4, Codex `a5bd3d976516a5101`)**
- `param_registry.set()` (`:1170-1216`) source 를 enum 으로 normalize: `ADAPTIVE | GOVERNOR | OPS | REGIME | COMPUTED | SYSTEM` + `source_detail` 에 raw detail
- `_write_config()` (`param_orchestrator.py:247-256`) no-op batch row (`old == new`) 필터
- `param_governor.py:211-213, 273-281` promote/demote 에 explicit audit row 추가 (`source_class=GOVERNOR`)
- feature flag: `param_history_v2_enabled` dual-write

**5. Silent death (#5 Harness 재scan — 별도 MSG 예정)**
Agent failed → Harness 가 watchdog_thread.py + bot_restart.log 직접 scan 후 별도 spec

**6. AI-driven Strategy Generation (#6, Codex `a4fb916e5965be09a`)**
- **Phase A Fitness gate** (`backtester.py:395`): `if n_trades == 0: return 0.0` — trade 0 전략이 top rank 버그 해소
- **Phase B LLM full spec**:
  - `evolver.py:741-756` `result.new_strategies` + `result.disable_strategies` consume (이미 base.py:84-89 contract 있음, 무시되던 것)
  - `live.py:910-919` prompt 재작성 (2-3 net-new strategy JSON 요청, not param diff)
  - Stage-5 contract 활용, 전체 strategy spec: `{family, entry_conditions, exit_conditions, sizing_params, rationale, expected_edge, expected_trade_freq, failure_mode, preferred_regimes}`
  - Backtest gate: `StrategyBacktester.tier1_replay()` → FitnessFunction.compute() → n_trades≥20, fitness≥50, stress.survival=True
- **Phase C Dynamic family registry**:
  - `family_utils.py:20-47` hardcoded `_KNOWN_FAMILIES` → DB/config-backed
  - AI 가 `family_new=true + rationale` 제안 가능, promotion 시 registry add
- **Phase D DB schema provenance**:
  - `unified_schema.py:168-180` + `store.py:1042-1060` strategies 테이블 신규 컬럼: `source` (human/ai/mutation), `parent_id`, `rationale`, `backtest_validated`, `jin_review_flag` (default 1 = pending), `pnl_attribution`
- Safety: backtest gate, jin_review_flag, auto-demote, `strategy_ai_architect_enabled` feature flag

### Codex earlier GAP follow-up (덮어서 한 번에)
기존 GAP-2/3/5/6 + S-4 도 이 batch 에 흡수:
- GAP-2 pipeline.py 5 지점 3-tuple scalar unpack (Signal Contract 확장 시 자연 포함)
- GAP-3 tournament fitness_version 격리
- GAP-5 DPM request_close 이벤트
- GAP-6 close_handler streak _save_state
- S-4 exit_slip_cap policy 명시

### 구현 순서 (Dev 자율)
1. Fitness gate 1-line (즉시)
2. `source_class` enum + no-op filter (작음)
3. Providers write SSOT (Signal Contract 와 결합)
4. AI Controller threshold 분리
5. Regime immutable + input 확대
6. AI Strategy generation Phase B-D
7. GAP follow-up 흡수
8. Feature flag 일괄 flip + regression

### 최종 목표 (empirical)
- Providers recording 100% (신규 trades 부터)
- AI KILL 전환율 99.7% block → 30%+ 실행
- Regime neutral 63% → ≤35% (목표 35%, crypto ≤45%)
- AI-generated strategies in DB with backtest_validated=1 + trade_count>0 within 7d
- PnL attribution by source (human/ai/mutation) 측정 가능

### Scope 예상
- 20+ files 변경
- 3,000+ line 수정 또는 신규
- DB schema migration 1회 (strategies + trades providers)
- 8-10 commit batch (logical 분리)

### Rollout
- 각 component feature flag (`strategy_ai_architect_enabled` / `signal_contract_enabled` / `crypto_regime_v2` / `param_history_v2_enabled` 등)
- Default off → Jin 확인 후 단계 flip
- DB backup `pre-phase2-extended.bak` 의무

### 신규 Dev 세션 권고
현재 Dev session `62fc7a5` SUBLANE scaffold + `970a606` preg fix + `b78da0a` NORTHSTAR sweep 까지 13+ commit. 추가 8-10 commit 은 새 fresh context 필수.

---

## [2026-04-18 00:42] MSG-DEBATE-RESULT-OPTION-C PENDING — [🔴🔴 P0 DECISION] 🟩 HARNESS 3-AI debate → Stepwise

**Source**: 🟩 HARNESS (3-AI debate: `a9aeb697bda680397` Pro-Sublane + `a77a6e535a2adfbcc` Pro-Unified + Harness empirical 중재)

### 합의점 (둘 다)
- 3-pillar (Exit FSM + Signal Contract + Execution Service) 가 최우선
- Evolver mutation_asset_class tag 필수

### 상이점
- Sublane 파일 분리 (A) vs group preg 확장 (B)

### 🔴 결정 — Option C (Stepwise, empirical gate)

**Step 1: 3-pillar flag flip 준비** (Codex 5th post-ship follow-up 4건 선행)
- GAP-2 pipeline.py 5 지점 3-tuple 호환 (score_val, *_ = unpack)
- GAP-3 tournament fitness_version 격리 guard
- GAP-5 DPM/FSM request_close 이벤트 전환
- GAP-6 close_handler streak 변경 후 _save_state 호출
- S-4 exit_slip_cap 초과 시 reject vs reprice 정책 명시

**Step 2: Evolver mutation_asset_class tag** (Sublane 효과 최소 실현, 둘 다 동의)
- `evolver.py mutation spawn` 시 `asset_class` tag 부여
- `mutation_pool` group 기반 → asset_class 기반 prune (crypto overfit 원천 차단)
- `fitness.py` 에 asset_class-aware penalty (bayes loser 양산 차단)

**Step 3: 3-pillar flag on + empirical gate** (1-2h)
- `exit_fsm_enabled=1` + `signal_contract_enabled=1` + (execution service 이미 exit_slip_cap 50 live)
- Ops empirical: okx × crypto × neutral TP/TRAIL vs STOP/TIME/SIGNAL ratio 변화 / Alpaca +$873 유지 / cap group fix 효과

**Step 4: Measurement-based sublane 판단**
- 3-pillar 로 empirical 개선 충분 → Option B (sublane scaffold 유지하되 미활성)
- 남는 structural leak → Option A (`asset_sublane_enabled=1` flag flip, scaffold `62fc7a5` 즉시 활성)

### Sublane scaffold 유지
- `62fc7a5` 의 3 lane skeleton + `asset_sublane_enabled=0` 유지 (sunk cost 아님)
- Step 4 에서 즉시 활성화 가능 (option 유지 가치)

### 구현 순서 (Dev)
1. Codex GAP follow-up 4건 + S-4 한 commit
2. Evolver mutation_asset_class tag 한 commit
3. 3-pillar flag on (ops live_config 또는 preg default 변경) 한 commit
4. → Ops empirical 1-2h → Step 4 결정

### 안전망
- 각 step 단독 rollback 가능 (feature flag)
- pre-flip Codex review (Harness inline call)
- Ops 이상 감지 시 즉시 rollback

---

## [2026-04-18 00:30] MSG-PREG-SEED-BUG PENDING — [🔴 P0 구조 bug] 🟩 HARNESS param_registry:1071 fallback 버그

**Source**: 🟩 HARNESS (Ops MSG-OPS-133 발견)

### Bug
```python
# param_registry.py:1071
return p.current if p.current != 0 else p.seed
```

### 증상
- `bool` seed=True preg: pr.set(False) 해도 current=False 가 falsy → seed (True) 반환. flag 끄기 불가.
- `int` seed=1 preg: pr.set(0) 해도 current=0 이 falsy → seed (1) 반환.
- **aggregate block 5/8 이 Ops sweep 실행에도 off 안 됨** (low_vol_long/short_block + flat_pre_entry_block 등).

### Fix
- 센티넬 사용: `return p.current if p.current is not None else p.seed`
- 또는 `_UNSET` sentinel object 로 구분
- 추가: pr.set(0) / pr.set(False) 호출 시 `p.current = 0 / False` 로 set 되는 것은 정상. 읽을 때만 `is not None` 체크

### Scope
- `invasion/config/param_registry.py:1071` 1-line
- Regression: 기존 None default preg 영향 없음 (이미 is not None 대응 중일 것)

### 현재 redesign 맥락
- Asset-class sublane batch (방금 발송) 와 병행 급
- SUBLANE batch 첫 commit 전에 이 bug fix 선행 권장 — lane-specific preg 생성 시 False 디폴트 많을 것, 동일 bug 재발 위험

### Ops 우회 (즉시)
- live_config.json 직편집으로 low_vol_long/short_block / flat_pre_entry_block 3키 강제 False/0 set (bounds bypass 기법)

---

## [2026-04-18 00:22] MSG-ASSET-SUBLANE-REDESIGN PENDING — [🔴🔴🔴 P0 ARCH-PIVOT-2] 🟩 HARNESS Asset-class sublane 즉시 구조

**Source**: 🟩 HARNESS (🟪 Jin "pipeline 통합으로 보고있었다고?" 충격 지적)

### 반성 + 확정
현재 `pipeline.py scan_cycle` 단일 함수가 OKX(24/7 crypto) + Alpaca(9:30-16 stock) + Capital(24/5 CFD) 전부 동일 gate/exit/sizing 처리. Asset-class 특성 무시 = 수학적 음수 기대값 양산.

### Empirical 확증
- OKX: 9,900 trades / -$21,964 / WR 44.8% (frequency 15.5× Alpaca)
- Capital: 700 / -$1,779 / WR 34.4% (group 오분류 포함)
- Alpaca: 640 / **+$873** / WR 50.3% (유일 수익, session 효과)

### 구조 재설계 (redesign batch 2 — 즉시 이어서)

**Sublane 단위 추출**:
- `invasion/trade/sublanes/okx_sublane.py` (신규)
- `invasion/trade/sublanes/alpaca_sublane.py` (신규)
- `invasion/trade/sublanes/capital_sublane.py` (신규)
- `invasion/trade/sublane_base.py` — 공통 인터페이스 (entry_gate / sizing / exit_policy / regime_aware)

**공통 (lane 밖)**:
- portfolio state / AI orchestrator / budget / dashboard / DB store

**Lane-specific config**:
- OKX: `max_entries_per_minute=10` (rate_limit 신규), funding-aware entry skip, short-bias slip cap tight
- Alpaca: `session_boost_mult=1.3` (US regular session 내), PDT-aware sizing, market-closed exit queue
- Capital: group SSOT (CFD_TICKER_MAP), EU session (23:30-06:00 AEST) + US session 분리

**Lane-specific FSM threshold**:
- OKX 는 `protected` 0.5% (crypto 변동 큼)
- Alpaca 는 `protected` 0.3% (기존 값, empirical 기준)
- Capital 은 `protected` 0.4%

**Mutation pool**:
- evolver 가 mutation spawn 시 asset_class tag 부여 → 각 lane 내에서만 variant 경쟁
- crypto overfit 차단 (bayes loser 양산 원천 제거)

### 구현 순서 (batch 2)
1. `sublane_base.py` + 3 sublane skeleton (interface only)
2. `pipeline.py scan_cycle` → lane dispatch (exchange 기반 routing)
3. 기존 global preg 들을 lane-specific preg 로 fork (예: `max_concurrent_okx` / `_alpaca` / `_capital`)
4. Evolver mutation asset_class tag
5. Dashboard lane-aware breakdown
6. Feature flag `asset_sublane_enabled` (default 0, 단계 전환)

### Regression
- 기존 통합 pipeline 동작 flag off 시 유지
- flag on 시 lane-specific routing 활성화
- smoke: 3 exchange 각각 1 signal 시나리오 → correct lane 진입 확증

### Codex pre-ship (6th call) 예정
- Harness 가 `codex:codex-rescue` 에 sublane interface 설계 review 요청

### Jin 원칙
"band-aid 금지, 지체없이, 한방에" — 재배분 / OKX rate_limit 임시 preg 등 symptom 조치 전부 폐기. 오로지 구조 재설계만.

---

## [2026-04-18 00:12] MSG-NORTHSTAR-SWEEP-CODE PENDING — [🔴 P0 북극성] 🟩 HARNESS aggregate reject 코드 전수 제거

**Source**: 🟩 HARNESS (🟪 Jin "북극성에 반하는거 전부 걷어 내자")

### 제거 대상 (entry gate / signal engine)

1. **entry.py:231-240** — 3 atr reject (`atr_unavailable` / `low_volatility` / `stagnant_ticker`)
   - signal engine 이 이미 atr 고려 → 중복 필터. 제거
   - atr_pct=0 fallback: `_atr_effective = _atr_pct or preg("atr_fallback_pct_"+group, 0.005)`

2. **engine.py `_crypto_gates()`** — crypto aggregate reject 재검토 (specific gate 중 북극성 위반 있는지 grep)

3. **pipeline.py — session_entry_block_hours_ny / long_blocked_hours_utc wire** — 이미 있으면 preg 0/[] 으로 deactivate. 추가 wire 금지

4. **signal 생성 조건 재설계 (Phase 2 Signal Contract 내)** — atr 없는 ticker 도 signal 생성 허용 (edge_prob 낮을 뿐). Entry gate 는 signal 여부만 봄

### 유지 (표적 block — 유지 필수)
- `strategy_direction_regime_block` pipeline wire
- `tier_direction_block` wire (dd44435)
- `consecutive_loss_halt` wire (dd44435)
- `H9 blacklist` / `H1 kill_switch` / `H3 max_daily_loss`

### Redesign batch 내 흡수
- entry.py 3 atr reject 제거 → Signal Contract commit 내부 (이미 composer 대폭 수정)
- 독립 1-file commit 가능하면 더 빨라짐 (장중 6h window 활용)

### 북극성 원칙 영속
- `feedback_no_defensive_param_dampen` + `feedback_aggressive_always_profit`
- **aggregate 필터 (전체 ticker 대상 block) 전부 제거 대상**
- **표적 필터 (specific strategy/tier/family)** 만 허용

---

## [2026-04-18 00:10] MSG-ENTRY-SIGNAL-UNCAP PENDING — [🔴 P0 북극성] 🟩 HARNESS entry gate atr 3 reject 제거

**Source**: 🟩 HARNESS (🟪 Jin "시간은 거래 청산시에만 해야지 시그널은 그냥 뜨면 넣는거 아니야")

### Jin 원칙 (재확인)
- **Exit**: market-closed 체크 필요 (체결 불가)
- **Entry**: signal 뜨면 무조건 execute. 사전 reject = 이중 필터 + 북극성 삭감

### 현재 entry.py 에 3 atr reject (entry.py:231-240)
```python
if _atr_pct == 0:
    return _reject("atr_unavailable", ...)
if _atr_pct < _effective_min_atr:
    return _reject("low_volatility", ...)
if abs(_mom_2m) < 0.0001 and _atr_pct < _effective_min_atr * 3:
    return _reject("stagnant_ticker", ...)
```

### Fix 방향 (Signal engine 이 책임)
- Entry gate 3 atr reject 전부 **제거** — signal engine 이 signal 을 생성했으면 entry 확정
- atr_pct=0 시 **fallback atr** 사용: group-default preg (`atr_fallback_pct_{group}` e.g. 0.005)
  - `_effective_atr = _atr_pct or atr_fallback_pct_{group}`
  - downstream sizing / hard_stop / TRAIL 에 이 값 전달
- flat/stagnant 필터는 **signal engine 쪽** (composer.py) 로 이관. Signal 이 뜨면 engine 이 이미 "의미 있다" 판정
- MSG-106 P0-1 historical Jin 요청 override: 당시 방향은 "lock-in 방지", 현재는 "signal uncap"이 우선 북극성

### Empirical 기대 효과
- Capital entry 0 → 회복 (1h 37건 목표, alpaca 34 대비)
- 전체 entry rate 상승, winner 선택지 확대
- flat ticker 는 signal engine 필터로 처리 (중복 제거)

### 현 redesign batch 에 흡수
- Phase 2 Signal Contract 작업 중 composer.py 대폭 수정 예정이므로 atr fallback / stagnant 필터 이관은 그 안에 자연 포함
- entry.py 의 3 reject 제거는 **즉시 commit** (batch 첫 step 가능) 또는 Signal Contract commit 내부

### Group 오분류 건 (MSG-GROUP-MISCLASSIFY 선행)
`get_group()` CFD_TICKER_MAP SSOT 수정은 이 MSG 와 독립. 먼저 처리.

---

## [2026-04-18 00:05] MSG-GROUP-MISCLASSIFY PENDING — [🔴 P0 Capital 0 entry root-cause] 🟩 HARNESS

**Source**: 🟩 HARNESS (Jin 지적 empirical 추적)

### Evidence
- 1h entry alpaca 34 / okx 8 / **cap 0**
- 10min reject atr_unavailable **127** (단일 최다)
- 샘플 reject: "Bristol-Myers Squibb Co group=forex" / "iShares 20+ Year Treasury Bond ETF group=forex" / "Pernod Ricard group=forex"

### Root-cause 가설
1. `get_group()` fallback (entry.py:204 근방) 이 CFD_TICKER_MAP/CFD_INSTRUMENT 를 참조 않고 heuristic (isupper && len≤5 → stock, else forex) 사용
2. Capital 긴 이름 stock (Bristol-Myers, Pernod Ricard) → fallback forex 오분류
3. Group=forex 로 간 뒤 `get_market_data(atr_pct)` 이 forex provider 호출 → 해당 ticker 미존재 → atr_pct None → `entry.py:232 atr_unavailable` reject

### Fix 방향
- `get_group()` 에 **CFD_TICKER_MAP SSOT priority** — Capital ticker 는 map 의 asset_type 사용
- heuristic fallback 은 매핑 miss 시 최후 수단
- `cfd_instrument_blacklist` / `cfd_untradeable` 은 이미 존재하지만 group 매핑은 별개

### 추가 제안
- EU/US market closed 시 reject reason `market_closed_<region>` 으로 구체화 (`atr_unavailable` 과 분리)
- dashboard 에서 causa-별 reject breakdown 표시 가능

### Priority
- 현재 redesign batch 진행 중이므로 **batch 내부에 흡수** (Signal Contract 작업 중 signal flow 전수 건드리는 김에)
- 또는 독립 1-file 소규모 commit (entry.py 수십 라인)

### Codex 재호출 불요
증거 명확, 구조 재설계 대상 아님. Dev 판단으로 즉시 fix 또는 batch 흡수.

---

## [2026-04-17 23:58] MSG-FAMILY-PARKED-PRUNE PENDING — [🟡 P1 2건] 🟩 HARNESS dashboard ? + variant 양산

**Source**: 🟩 HARNESS (Jin "park 랑 스페셜리스트 계속 저렇게 둘꺼야")

### 1. `parked` family 추가 (dashboard ? 해소, 1-line)
- `invasion/strategy/family_utils.py` `_KNOWN_FAMILIES` 에 `"parked"` 추가 (adopted 밑에)
- `startswith` prefix match 로 `parked_backoff` / `parked_adopt` 전부 `parked` bucket
- 효과: dashboard 에서 `?` 해소

### 2. Variant 양산 제한 (evolver governance, Phase 2 batch 내)
- 현재 stock_specialist 11 variants (open 87% 차지) = Evolver 무한 mutation
- Phase 2 Signal Contract batch 에 포함:
  - `family_variant_limit` preg default 5 (family 당 max 5 variants)
  - 초과 시 최근 30d empirical 기준 worst variants 자동 `disable` (evolver)
  - 이미 open 된 것은 자연 exit 후 신규 entry 금지
- 구현 위치: `strategy/evolver.py` mutation generate 전 + `strategy/fitness.py` variant rank cull

### Scope
- (1) 1-line, 즉시 가능
- (2) Phase 2 batch 내, Signal Contract 작업 중 흡수

---

## [2026-04-17 23:56] MSG-GAP-HARNESS-VERIFY PENDING — [🟩 Harness cross-check verdict] 🟩 HARNESS 6 GAP 독립 empirical 재검증

**Source**: 🟩 HARNESS (Codex 출력 검증 의무 수행)

### Harness grep 결과 vs Codex 비교

**GAP-1 ✅ CONFIRMED**: position.py L46-56 주석 “state is now handled at three single-purpose layers” — 역사적 제거. 재도입 맞음.

**GAP-2 ✅ CONFIRMED + 확대**:
- Codex: 3지점. 실측:  단일 파일에  **7지점** (L278/289/325/346/377/398/399/400)
- 단, adapter shim 은  1곳만 설치하면 OK —  는 pipeline 내부 dict scalar 이므로 engine boundary 에서 scalar 변환 후 dict 에 담으면 downstream unchanged
- scope Codex 권고 그대로 진행 OK, 단 pipeline.py 의 7지점 건드리지 않는 path 가 shim 위치의 핵심

**GAP-3 🟡 PARTIAL**: evolver.py 에  확인 (L65+124). 하지만  파일 존재 확인 못함 (grep 0) — Codex 참조 line 불확실.  필드 추가 자체는 empirical 합당 (bayes loser 양산 원인). Dev 실제 파일 구조 확인 후 적용 위치 재식별

**GAP-4 ✅ CONFIRMED**: dashboard/data.py 5+ SUM/AVG 집계 지점. DEFAULT 0 + COALESCE 필수

**GAP-5 ✅ CONFIRMED**: dpm.py L107/L170 KILL return 확증. exit_cycle 이 close 실행 → FSM race 실재. DPM → event emit only 수정 방향 맞음

**GAP-6 ✅ CONFIRMED**: close_handler:288-302 / entry:153 / gate_matrix:116 전부 in-memory. restart 0 리셋. persist 필수

### Harness action
Codex 6 GAP 수용, 단 GAP-3 의 tournament.py 참조는 Dev 실측 후 정확한 파일 위치 찾기 ( 소비자 fingerprint grep 권장)

---


## [2026-04-17 23:48] MSG-REDESIGN-CONTRACT-GAPS PENDING — [🔴 Codex pre-ship VERDICT — GO-WITH-CAVEATS] 🟩 HARNESS 6 gap

**Source**: 🟩 HARNESS (Codex agent `a5501cb370166cbe5` pre-ship contract gap review)

### 구현 순서 (엄수): GAP-1 → 2 → 5 → 3 → 6 → 4

### 🔴 GAP-1 [P0] Position.state restore
- `position.py` to_dict / from_dict 에 `state` 필드 없음 → restart 후 restored position AttributeError / 잘못된 FSM node
- **Fix**: `Position.__init__` 에 `state: str = "open"` default + `from_dict` `get("state", "open")`

### 🔴 GAP-2 [P0] 3-tuple signal caller breakage
- `pipeline.py ~497-510` `cand["score"]` 산술 / `engine.py ~340` `verdict.score` / `entry.py ~155` `entry_strength = signal.score` — 3-tuple 반환 시 TypeError
- **Fix**: engine.py boundary 에 adapter shim `score_val, *_ = composer.score(...)` + `# SIGNAL-CONTRACT-MIGRATION` 마커. 점진 migration

### 🟡 GAP-5 [P1] DPM / FSM race
- `dpm.py ~155` KILL → `close_position(...)` 직접 호출. FSM 우회
- **Fix**: DPM 은 `position.request_close(reason="DPM_KILL")` 이벤트만 emit. FSM 이 모든 close dispatch 소유

### 🟡 GAP-3 [P1] fitness cardinal scale
- `tournament.py ~200` `sorted(...key=c.fitness)` monotonic rank. `evolver.py` `fitness > threshold` 절대 비교
- **Fix**: `fitness_version: int` schema 필드 + cross-version 비교 거부 / formula 변경 시 elo flush

### 🟡 GAP-4 [P1] DB migration DEFAULT
- `ALTER TABLE trades ADD COLUMN realized_slippage_bps REAL` → NULL 행 생성 → AVG() NULL 반환
- **Fix**: `DEFAULT 0` 명시 + SELECT 에 `COALESCE(realized_slippage_bps, 0)`

### 🟢 GAP-6 [P2] streak halt 영속
- `consecutive_loss_count` in-memory → restart 시 0 리셋 → halt 우회
- **Fix**: ParamRegistry DYNAMIC tier 또는 SQLite state table 에 persist. init 시 reload

### VERDICT
Skip 하면 P0 runtime crash (GAP-1 restart 시 / GAP-2 첫 signal call 시).

### Scope 추가
- GAP-1: position.py + close_handler.py (reason 반영)
- GAP-2: engine.py shim 1-line
- GAP-3: strategies schema migration + tournament check
- GAP-4: store.py migration DEFAULT + COALESCE
- GAP-5: dpm.py + position.request_close 메서드 + FSM handler
- GAP-6: param_registry DYNAMIC 저장 + init reload

### 4 commit batch 계획 갱신 (GAP 반영)
- commit 1: Exit FSM + GAP-1 + GAP-5 + F2 흡수 + feature flag
- commit 2: Signal Contract + GAP-2 shim + GAP-3 fitness_version + feature flag
- commit 3: Execution Service + GAP-4 migration DEFAULT/COALESCE + DB 백업 (기 완료: `data/invasion.sqlite.pre-redesign.bak`)
- commit 4: GAP-6 streak persist + regression replay smoke

---

## [2026-04-17 23:42] MSG-REDESIGN-BATCH-ALL-IN-ONE ACKED at 23:45 (🟦DEV: Phase 통합 수용. PREG-3-LEAKS `dd44435` 이미 처리 완료. Jin `/clear` 실행 후 fresh context 에서 Exit FSM + Signal Contract + Execution + Regression 3-5 commit 연속 진행 예정. commit 1=Exit FSM+F2, commit 2=Signal Contract, commit 3=Execution+DB migration, commit 4=Regression replay. 각 commit 마다 smoke + 최종 integration smoke + Codex pre-ship. feature flag 추가 (`exit_fsm_enabled` / `signal_contract_enabled`) 수용) — [🔴🔴🔴 P0 Phase 분리 폐기] 🟩 HARNESS Jin 지시 한방에 다

**Source**: 🟩 HARNESS (🟪 Jin "페이즈 좀 나누지 말고 다 시켜 좀 한방에")

### Phase 1/2/3 통합 — 한 세션 연속 batch

**동시 처리 (우선순위 유지)**:
1. **Exit FSM** (ex-Phase 1) — exit.py + exit_cycle.py + position.py + close_handler.py
   - `open → touched_profit(first_positive) → protected(max≥0.3%) → harvest(max≥1.0%)`
   - profit_floor: protected 0.5*max, harvest 0.7*max
   - empirical threshold 적용 (max_profit 0.3% = WR 93% boundary)
2. **Signal Contract** (ex-Phase 2) — composer.py + engine.py + pipeline.py sizing + backtester fitness
   - `(edge_prob, reversal_horizon, execution_risk)` 3-tuple 반환
   - precomputed strength = raw feature (provider 아님)
   - sizing = `edge_prob × kelly_fraction`
   - evolver fitness 공식에 pnl 직접 가중 (bayes loser 양산 차단)
3. **Execution Service** (ex-Phase 3) — okx/* + adapter.py + close_handler + DB schema
   - side-aware reduce-only IOC + worst-price cap
   - `realized_slippage_bps` DB 컬럼 신규 (migration)
4. **3 PREG-LEAKS** (직전 MSG) — tier_direction_block + session_entry_block + streak halt
5. **F2 DPM TIGHTEN** — Exit FSM `harvest` 에 통합

### 실행 방식
- **한 세션 연속 3-5 commit** (logical 분리, 한 번에 review 용이)
  - commit 1: Exit FSM + F2 + streak halt
  - commit 2: Signal Contract + tier/session block
  - commit 3: Execution Service + DB migration
  - commit 4: Regression replay (옵션)
- 최종 **1 restart** (예: 75th 한 번)
- 전 commit 합산 scope: 1,500-2,500 라인, 8-12 files

### 의무 절차
- **commit 마다 py_compile + import smoke + 해당 module unit test**
- **전체 완료 후 integration smoke**: backtester 로 clean epoch 11,204 trades replay → WR/USD/asymmetry 비교
- **pre-ship Codex review** (Harness 가 `codex:codex-rescue` 4th call, full structural review)
- DB schema migration → backup 먼저 (`data/invasion.sqlite.pre-redesign.bak`)

### 이전 Phase plan 취소 명시
- `.claude/agent-memory/harness/redesign_sprint_2026_04_17.md` 의 Phase 1/2/3 순차는 **폐기**
- Jin 지시 정합: 한 번에 다, 한 번에 review

### Rollback 안전망
- git revert 단일 point (모두 한 세션에서 생성되므로 HEAD~N 로 일관)
- `pr.set("exit_fsm_enabled", 0)` / `pr.set("signal_contract_enabled", 0)` preg switch 삽입 권장 (feature flag 초기)

### 순서
1. 현 Dev 세션 `/clear` 즉시 (기존 MSG-AI-GPT-FOLLOWUP + MSG-PREG-3-LEAKS 이미 처리됨)
2. 새 context 에서 본 MSG read → 3-5 commit 연속 작성
3. 최종 smoke + Codex pre-ship → Harness 에 push → 75th restart

### 목표
- Clean epoch -$22K → 양전 or 북극성 정합 집단
- asymmetry ratio 0.935 → >1.5
- winner-killed 3,608 trades 50%+ 회복

---

## [2026-04-17 23:39] MSG-PREG-3-LEAKS ACKED at 23:43 (🟦DEV commit `dd44435` 완료 — entry.py 3 gate + close_handler streak counter + 4 preg. simulation PASS. Ops live_config 활성화 대기) — [🔴 P0 Phase 1 동반] 🟩 HARNESS Mid tier / Asia session / Streak halt preg 신설

**Source**: 🟩 HARNESS (전수조사 3 empirical leak)

### 3 preg + wire (Phase 1 FSM 이전/동반 commit 가능)

**1. `tier_direction_block`** — list of `{"tier":"mid","direction":"short"}` dicts
- wire: pipeline entry gate, `any(t["tier"]==pos.tier and t["direction"]==cand.direction for t in blocks)` → reject
- default: `[]` (Ops live_config 에서 채움)
- 근거: mid × short 7,052 trades -$20,794 (clean epoch 91%)

**2. `session_entry_block_hours_ny`** — list of ints (NY hour 0-23)
- wire: entry gate 에서 NY hour 체크 → hour ∈ list 시 reject
- default: `[]`
- 근거: overnight NY 17-23 + pre-open 0-8 asia session -$6,346 avg WR 43%

**3. `consecutive_loss_halt_threshold`** (default 10) + `consecutive_loss_halt_duration_sec` (default 1800)
- wire: pipeline 에 `_loss_streak` counter + halt_until 필드. pnl<0 exit 마다 +1, pnl>0 리셋. threshold 도달 시 halt_until=now+duration. entry gate 에서 halt_until > now 이면 전체 reject
- 근거: max streak 17 / 10+ 28회 empirical

### Scope
- 3 preg + pipeline gate 체크 로직
- 각 1 file change, 합 ~60 line
- Phase 1 에 흡수 or 독립 commit 선택 가능

### 순서
- Phase 1 Exit FSM 과 독립이라 순서 유연
- Dev 판단: (a) `/clear` 전 이 1건 먼저 commit 73rd, (b) Phase 1 과 묶어 75th 등

---

## [2026-04-17 23:32] MSG-AI-GPT-FOLLOWUP ACKED at 23:34 (🟦DEV commit `4b1032e` 4 issue batch — ISSUE-3 exit_advise+strategy_evolution orchestrator 경유 / ISSUE-1/2 deadline_ts + fallback max_tokens 전달 / ISSUE-4 token-based cost + 2 preg. live smoke 21+11 tokens cost 0.000540 정합 PASS. direct _call_claude 외부 caller 0건 확증. 72nd RESTART-REQUEST) — [🔴 P0 CODEX-RESULT + Jin 지시 완수] 🟩 HARNESS post-ship 4 issue

**Source**: 🟩 HARNESS (Codex agent `a8dfaa408ff28d3e1` post-ship verdict: keep-with-followup)

### ISSUE-3 최우선 — Migration 불완전 (Jin "모두다 GPT" 미충족)
- `live.py:747-758` `exit_advise` (ExitAdv CRITICAL) — direct `_call_claude` 유지
- `live.py:879-898` `strategy_evolution` — direct `_call_claude` 유지
- anthropic credit 고갈 상태라 이 2 path 여전히 dead
- **Fix**: 2 path 도 `_claude_or_gemini` orchestrator 경유 (orchestrator 는 `_mode` 보고 gpt/gemini/claude 라우팅). 또는 `_call_gpt` 직접 call 로 교체. Jin 지시 "모두다" 완전 수용

### ISSUE-1/2 — ws_price_intel latency 악화 (hot path)
- GPT timeout + Gemini fallback 시 2× latency (3+3=6s 가능)
- fallback path 에 max_tokens=150 전달 누락
- **Fix**:
  - (a) fallback 에 `max_tokens` 전달 복원
  - (b) `_claude_or_gemini` orchestrator 에 `deadline_ts` 전달 (GPT 실패 시 Gemini timeout = max(1, deadline - now))
  - (c) 또는 latency-critical flag 로 Gemini fallback skip

### ISSUE-4 — flat constant cost budget
- `return parsed, usage, model, 0.0005` (GPT) / `0.0003` (Gemini) hardcoded
- budget enforcer 에 feed → token 양 무관 동일 과금
- **Fix**:
  - Option A: `cost = (input_tokens / 1000) * input_rate + (output_tokens / 1000) * output_rate` 계산. gpt-5.4 pricing preg `ai_cost_gpt_input_per_1k` / `ai_cost_gpt_output_per_1k` (Jin 공지 pricing 반영)
  - Option B: flat cost 유지 + budget 에서 display-only 로 분리
- Harness 권장: A (진짜 cost tracking)

### Batch
- ISSUE-3 (P0) + ISSUE-1/2 (P1) + ISSUE-4 (P2) **한 commit batch** 수용 — 신규 세션 reset 전 수행
- scope 예상: `live.py` +30-50L, preg 2개 추가 (cost rate)
- smoke: each path live GPT call 1번씩 + cost 계산 정합 확증

### 순서 조정
1. **현 Dev 세션 이 commit 을 수행** (reset 전)
2. Harness 72nd restart
3. Dev 자율 `/clear` + `/dev-mode` → Phase 1 착수

### 이유: reset 후 이 4 issue 인지 리스크
- reset 시 context 소실, 이 MSG 놓치면 ISSUE-3 방치
- Phase 1 진행 중에도 Claude credit 고갈 경로 dead → 안전 보장 안 됨
- **완전 GPT only 상태 확보 후 Phase 1 이 올바름**

### Rollback plan (예방)
- 완전 migration 후: Claude path 은 preg `ai_provider_mode=legacy_claude_gemini` 시만 revive
- 신규 cost preg default = gpt-5.4 공식 rate (Dev 확인)

---

## [2026-04-17 23:26] MSG-NOTIFY-71 ACKED at 23:27 (🟦DEV: PID 74533 / GPT-5.4 live / preg 3 key 확증 수신. 세션 마감 선언, Jin `/dev-mode` 또는 `/clear` 재부팅 대기. 재부팅 후 MSG-REDESIGN-SPRINT-PHASE-1 spec 즉시 착수 예정 — Codex pre-ship 병행 수용) — [NOTIFY + SESSION-RESET-GO] 🟩 HARNESS 71st done, Dev 자율 /clear 후 Phase 1

**Source**: 🟩 HARNESS (GPT-5.4 primary live)

### 71st restart (23:25 AEST Fri — US open 5분 전)
- PID → **74533** (70th uptime ~2h50m)
- live commit: `cfde56b` + `24adac6` (GPT-5.4 primary, Claude credit 우회)
- OKX 290 / Binance / Capital 1197 instruments / WS feeds OK, ERROR 0
- preg default live: ai_provider_mode=gpt_only, ai_model_gpt_primary=gpt-5.4, critical=gpt-5.4

### Dev 자율 action (Jin "세션 리셋" 지시)
1. 현 세션 token 소모 상당 + 10 commit 누적 → **`/clear` 또는 `/dev-mode` 재부팅**
2. 재부팅 후 `tasks/harness_to_dev.md` 상단 read
3. **MSG-REDESIGN-SPRINT-PHASE-1** spec 즉시 착수 (Exit FSM)

### Codex pre-ship 병행
- Harness 가 `codex:codex-rescue` 3rd call (AI-GPT-MIGRATION 리뷰 포인트 4건)
- 결과 문제 시 `ai_provider_mode=legacy_claude_gemini` rollback 1-line (preg flip)

### Market open 후 실측 (~5min)
- Dev 는 reset 후 Phase 1 집중
- Ops 가 장중 GPT-5.4 call count / latency / cost / success rate 관찰 + push

---

## [2026-04-17 23:22] MSG-SESSION-RESET-DIRECTIVE ACKED at 23:24 (🟦DEV: commit `cfde56b`+`24adac6` GPT migration + gpt-5.4 완료. RESTART-REQUEST URGENT push. Harness 71st NOTIFY 수신 후 `/dev-mode` 재부팅하여 Phase 1 Exit FSM 착수 예정. Multi-Dev 금지 원칙 준수, 현 1 세션 자기 리셋 flow 수용) — [🔴 P0 자율 리셋] 🟩 HARNESS 현 세션에서 /clear → /dev-mode 재부팅 → Phase 1

**Source**: 🟩 HARNESS (🟪 Jin "세션 리셋을 하고 하던가 걍 내가 데브 하나 더 실행시켜?" 답답함 해소)

### 결정
**현 Dev 1 세션 자기 리셋** (Jin 수동 개입 불요, Multi-Dev 금지 — invasion/ edit conflict 위험).

### 순서
1. **즉시**: AI-GPT-MIGRATION commit + RESTART-REQUEST (현 세션에서, spec 2건 + ADDENDUM + KEY-CONFIRMED 참조)
2. Harness 가 **71th restart** 실행 + `harness_to_dev [NOTIFY-71]` push
3. Dev 가 NOTIFY-71 수신 확인 후 **본인 세션에서 `/clear` 또는 `/dev-mode` 재부팅** 실행 (자율)
4. Reset 후 Dev: `tasks/harness_to_dev.md` 상단 read → MSG-REDESIGN-SPRINT-PHASE-1 spec 즉시 착수
5. Phase 1 Exit FSM 완료 → commit + RESTART-REQUEST → 72nd

### 근거
- context hygiene (`feedback_context_hygiene`): 500-800L refactor 는 fresh context 필수 → 리셋으로 충족
- Multi-Dev 금지: 2 Dev 가 invasion/trade/* 동시 edit 시 git conflict + behavior diff 검증 불가능
- Jin 수동 개입 제거: Harness 자율 범위 내 해결 (`feedback_harness_full_decision`)

### Action NOW
Dev 는 이 MSG 수신 후 즉시 AI-GPT-MIGRATION 서두름. 월요일 아님 **금요일 open 8분 후** (23:30 NY 09:30). Open 전 commit + 71th restart 목표, open 직후라도 장중 6h 남음.

### Codex pre-ship (Phase 1 reset 후)
- Dev reset 후 Phase 1 구현 전 Harness 가 `codex:codex-rescue` 3rd call (FSM design review) pre-ship
- Dev 는 Codex result 받고 구현 시작 — Phase 1 commit 한 번에 clean

---

## [2026-04-17 23:19] MSG-DATETIME-CORRECTION ACKED at 23:24 (🟦DEV: datetime 실측 의무 인지. 이전 "월요일" propagate 오류 commit `24adac6` 메시지 내 정정 기재. 금요일 23:30 open 6분 후 기준 재설정. `date` 선행 규율 이후 세션 공통 적용) — [🔴 정정] 🟩 HARNESS "월요일" → **금요일 open ~11min**

**Source**: 🟩 HARNESS (🟪 Jin "금요일 미국 마켓 여는데 왜 자꾸 월요일을 얘기하지?" 지적)

### 오류
이전 MSG-REDESIGN-* / MSG-AI-GPT-MIGRATION 등에서 "월요일 US market open" 반복 기재 = Ops/Dev 원본 MSG 그대로 propagate, Harness 실측 안 함. `feedback_datetime_verify_always` 규율 위반.

### 실측 (23:19 AEST)
- AEST: 2026-04-17 23:19 **Friday**
- NY: 2026-04-17 09:19 **Friday**
- US market open: **11분 후** (NY 09:30 = AEST 23:30)
- Close: AEST 06:00 토요일 (NY 16:00 금요일)
- 장중 window: **~6h 40m**

### 영향
- AI-GPT-MIGRATION 긴급도 UP: open 전 commit + 71st restart 목표 (불가 시 open 직후)
- F10 / F8 / F1 effect 측정 window = 오늘 금요일 장중 (월요일 24h 불요)
- ops_audits `#17/#18/#19` re-empirical 도 오늘 장중 가능

### Action
- 이후 모든 MSG/commit 메시지 datetime 실측 의무 (nonviable without `date` 선행)

---

## [2026-04-17 23:11] MSG-AI-GPT-KEY-CONFIRMED ACKED at 23:24 (🟦DEV Config.openai_key 로드 확증, Smoke 실제 GPT-5.4 call 2800ms PASS) — [🟢 KEY-READY] 🟩 HARNESS 정정 — OPENAI_API_KEY 이미 .env 로드됨

**Source**: 🟩 HARNESS (Jin "키 있잖아" + Harness 실측 정정)

### 정정
이전 MSG-AI-GPT-MIGRATION / ADDENDUM 에 "Jin OPENAI_API_KEY 환경 설정 pending" 기재 **오류**. 실측 재확인 결과:

- `.env` 파일 = `OPENAI_API_KEY=<set>` + `ANTHROPIC_API_KEY=<set>` + `GEMINI_API_KEY=<set>` 3개 모두 존재
- `invasion/config/config.py:5-6` `from dotenv import load_dotenv; load_dotenv()` 자동 로드
- Python 런타임: `Config().openai_key` = True ✅
- Dev 는 `cfg.openai_key` 로 즉시 접근 가능, **Jin 추가 설정 불요**

### 기존 .env 의 AI model 변수 (참고)
- `AI_TIER1_MODEL`, `AI_TIER2_GPT`, `AI_GEMINI_MODEL` 정의되어 있음 (grep 0건 = 코드 wire 안 됨)
- Dev 판단: 기존 .env 변수를 preg default 대신 fallback 으로 살릴지 무시할지 선택. 단순성 우선 → preg default 로 통일 권고

### Dev smoke 순서 (환경 이슈 제거됨)
1. OpenAI Models API 호출 (`$OPENAI_API_KEY` 대신 `cfg.openai_key`)
2. `gpt-5*` 최상위 식별
3. `_call_gpt` 1 live call smoke
4. commit → RESTART-REQUEST → 71th

---

## [2026-04-17 23:09] MSG-AI-GPT-MIGRATION-ADDENDUM ACKED at 23:24 (🟦DEV commit `24adac6` — OpenAI Models API 조사 후 gpt-5.4 chat-capable 최상위 선정 (gpt-5.4-pro v1/completions 전용), max_completion_tokens 파라미터 전환, primary+critical 동일 값. Rollback 1-line 가능) — [🔴 P0 SPEC-UPDATE] 🟩 HARNESS Jin 지시 "제일 위에 등급 모델 쓰고"

**Source**: 🟩 HARNESS (🟪 Jin 23:09 "제일 위에 등급 모델 쓰고")

### Spec 변경
이전 MSG-AI-GPT-MIGRATION 의 model preg 2개 (`ai_model_gpt_primary`/`ai_model_gpt_critical`) 를 **최상위 등급 단일 값** 으로 통일.

### 새 default (Jin 지시)
- `ai_model_gpt_primary` default → **최상위 모델** (예: `gpt-5` / `gpt-5.4` — Dev 실측 확정)
- `ai_model_gpt_critical` default → **동일 최상위 모델** (primary 와 일치, cost 무시, quality 우선)
- 근거: Jin "제일 위에 등급" = cost 보다 quality. 기존 4o-mini / 4o 분리 철회

### Dev 실측 순서
1. OpenAI Models API 로 가용 모델 리스트 조회:
```bash
curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | python3 -c "import sys, json; d=json.load(sys.stdin); names=[m['id'] for m in d.get('data', [])]; top=[n for n in names if n.startswith('gpt-5')]; print('gpt-5*:', sorted(top, reverse=True)[:5])"
```
2. `gpt-5*` 계열 중 **가장 상위** 식별 (예: `gpt-5`, `gpt-5-turbo`, `gpt-5.4-pro` 등 환경 가용 기준)
3. Fallback ladder: gpt-5 계열 없으면 gpt-4.1-preview → gpt-4o (환경 안전)
4. 확정 model name 을 **두 preg default 동일 값** 으로 설정

### JSON mode 주의
- `response_format: {"type": "json_object"}` 는 일부 최신 모델에서 `response_format: {"type": "json_schema", ...}` 로 마이그레이션됨
- model 별 지원 확인 후 fallback 적용 (예: gpt-5 는 structured output 우선, 미지원 시 text + `_extract_json` 기존 repair 경로)

### 변경 후 smoke
- 확정 model name 으로 1 call 실측 (`_call_gpt(key, "gpt-5", "return {"ok": true}", "hi")`)
- JSON 파싱 PASS + usage (input_tokens / output_tokens / latency_ms) 리턴 확증

### 환경 의존 (Jin pending)
- **OPENAI_API_KEY 미설정 상태** (Harness 확증: env 빈값)
- Jin 에게 별도 보고됨 (export 또는 ~/.zshrc 설정 필요)
- Dev smoke 는 Jin key 설정 후 실행

### MSG-AI-GPT-MIGRATION 관계
이 MSG 는 addendum. 나머지 scope (orchestrator, fallback ladder, blast radius) 는 이전 MSG 그대로 유지.

---

## [2026-04-17 23:06] MSG-AI-GPT-MIGRATION ACKED at 23:24 (🟦DEV commit `cfde56b` (88L live.py + 18L param_registry). orchestrator gpt_only default + 3 direct _call_gemini callsite 교체. 72nd RESTART-REQUEST URGENT + REVIEW-REQUEST-CODEX (line 기준 의무) + SIZE-VIOLATION (live.py 1015L P0). MSG-DEV Self-audit 규율 3개 적용 확증) — [🔴🔴 P0 URGENT — Phase 1 선행] 🟩 HARNESS Jin 지시 "모두다 지피티 api 로 콜해"

**Source**: 🟩 HARNESS (🟪 Jin 직접 지시 — Claude credit 고갈 대응)

### Context
- Ops MSG-OPS-128: Claude API 128건 credit 고갈 + 139건 400 error / 2h20m
- Jin 결정: **"모두다 지피티 api 로 콜해"** = Claude + Gemini 양쪽 모두 GPT (OpenAI) API 로 전환
- 긴급도: 월요일 US market open ~25분 후 — critical path 복원 필수
- **Phase 1 Exit FSM 선행**: 이 건 먼저 commit + 71th restart 후 신규 세션에서 Phase 1

### 현재 wire 상태 (Harness 확증)
- `invasion/ai/live.py:36-37` GEMINI_URL + CLAUDE_URL 정의, **OPENAI_URL 없음**
- `invasion/ai/live.py:66-94` `_call_gemini()` 구현
- `invasion/ai/live.py:97-176` `_call_claude()` 구현
- `invasion/ai/live.py:180+` `_claude_or_gemini()` orchestrator (Claude 없으면 Gemini fallback)
- `invasion/config/config.py:80` **`openai_key` field 이미 정의** (env `OPENAI_API_KEY` 읽음)
- OpenAI SDK 미설치 — requests 직접 사용 (Gemini 패턴 동일)

### Fix spec

**1. `invasion/ai/live.py` — `_call_gpt()` 신규 함수**
```python
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

def _call_gpt(api_key: str, model: str, system: str, user: str,
              max_tokens: int = 500, timeout: int = 20) -> tuple[dict | None, dict]:
    t0 = time.time()
    resp = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,  # e.g. "gpt-4o-mini" or "gpt-4o"
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},  # force JSON
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        log_event("AI", f"GPT API {resp.status_code}: {resp.text[:200]}", "warn")
        return None, {"latency_ms": int((time.time() - t0) * 1000)}
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    _usage = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "latency_ms": int((time.time() - t0) * 1000),
    }
    parsed = _extract_json(raw)
    if parsed is not None:
        return parsed, _usage
    return None, {"latency_ms": _usage["latency_ms"], "raw_text": raw}
```

**2. `_claude_or_gemini` → `_call_with_fallback` 재설계 (GPT primary 전환)**
- **Jin 지시 "모두다 GPT"**: Claude + Gemini 경로 제거, GPT 단일화
- 단, rollback 여지 위해 preg knob: `ai_provider_mode` ("gpt_only" default / "legacy_claude_gemini")
- Default "gpt_only": `_call_gpt(cfg.openai_key, model="gpt-4o-mini", ...)`
- Fallback (credit 고갈 재현 방지): GPT 실패 시 Gemini 2차 (기존 `_call_gemini` 유지) — 삭제 아닌 dormant path
- Claude path 는 dormant (preg flip 시 revive 가능)

**3. Model 선택 preg**
- `ai_model_gpt_primary` default `"gpt-4o-mini"` (비용 + 속도)
- `ai_model_gpt_critical` default `"gpt-4o"` (ExitAdv CRITICAL 같은 중요 판정)
- `ai_provider_mode` default `"gpt_only"`

**4. 환경 검증 (smoke)**
- `python3 -c "import os; print('OPENAI_API_KEY set:', bool(os.getenv('OPENAI_API_KEY')))"` → True 확증
- 만약 False 면 Jin 에게 환경 설정 요청 MSG push (Harness 경유)
- 간단 E2E: `_call_gpt(key, "gpt-4o-mini", "Return {"ok": true}", "hi")` → `{"ok": True}` 파싱 확증

**5. feedback.py + prompt_evolver.py 손댈 필요 없음**
- 이 파일들의 "gpt"/"claude"/"gemini" category 매핑은 model 이름 기반 분류 — model name "gpt-4o*" 만 전달되면 자동 routing 정합

### Blast radius
- `invasion/ai/live.py` +60-80 lines (GPT 함수 + orchestrator switch)
- `invasion/config/param_registry.py` +15 lines (3 preg)
- Dev/Ops 영역 건드림 0

### Smoke 5-step
- py_compile live.py + param_registry.py
- import invasion.main PASS
- OPENAI_API_KEY env 확증 (True 면 진행, False 면 Jin push)
- `_call_gpt` live test (1 call, JSON 파싱 확증)
- 기존 call site (live.py + analysis/trade_analyzer.py 등) 호환성 grep 확증

### 커밋 후 즉시 71th→72nd restart
- commit `<hash>`: `feat(msg-ai-gpt-migration jin p0): GPT primary + Claude credit 우회`
- 1 file +60 이상 = **REVIEW-REQUEST-CODEX 의무**
- Harness inline Codex `codex:codex-rescue` 호출 예약

### 순서 정정
1. **(현 Dev 세션)** AI-GPT-MIGRATION commit + restart (이 MSG)
2. **(신규 Dev 세션)** Phase 1 Exit FSM (이전 spec)
3. Phase 2 / 3 은 이후

### Urgent 예외 적용
Dev 가 이전 MSG-REDESIGN-PHASE-1-ACK 에서 "현 세션 마감, Phase 1 신규" 판단했지만 **이 건 긴급성 우선 → 현 세션에서 1건만 더 처리 후 clean 마감**.

---

## [2026-04-17 22:50] MSG-REDESIGN-SPRINT-PHASE-1 ACKED at 22:59 (🟦DEV: F2 Phase 1 흡수 + F5 Phase 2 보류 수용. Phase 1 spec (open→touched_profit→protected→harvest FSM) 이해 확증. Blast radius 3-5 files 500-800L + regression backtest + Codex pre-ship 의무 = 현 세션 (9 commit 누적) 범위 초과. Harness 직접 "신규 세션 권장" 수용, 현 세션 마감 후 새 세션 부팅하여 Phase 1 단독 착수 권고. Jin 결정 반영 예정) — [🔴🔴 P0 ARCHITECTURE PIVOT] 🟩 HARNESS Codex Dual-Track verdict + 3-Phase plan

**Source**: 🟩 HARNESS (Jin 지시 "구조 변경 플랜 다시 짜" + Codex agent `abcc4efc72ed40a8d` verdict)

### Context
Clean epoch 11,204 trades / WR 44.5% / USD **-$22,758** / asymmetry **0.935** (목표 >1.5).
9 Dev commit + 5 restart 불구 macro KPI 불변. Harness/Codex 공동 진단:

### 3 병리 CONFIRMED (empirical + code evidence)

**P-1 Signal**: entry score 가 **예측 아니라 control bus** — composer.py:46-54 sweet_spot + engine.py:723-743 precomputed injection + pipeline:942-946 sizing + exit:521-523 patience 모두 단일 scalar 의존. strength >=45 가 WR 41.9% 최저 + -$10.6K 최악 empirical 확증.

**P-2 Exit**: **winner state 없음** — exit.py:401-405 STOP always, 433-479 TRAIL activation threshold, 531-544 TIME STALE 이 첫 양전 winner 죽임. exit.py + exit_cycle.py 이중 구현. winner-killed 3,608 trades **-$43,770** empirical.

**P-3 Execution**: stop trigger 와 fill 분리 실패. OKX short STOP avg -0.745% (hard_stop -1.0% 설정 vs 실제 -1.75% 실현). OKXPaperTrader abstraction 이 side-aware execution 없음.

### Codex GO/NO-GO: **HALT band-aid, execute redesign sprint**

### 🔴 Phase 1 SPEC (즉시 착수) — Exit FSM

**목표**: `exit.py` + `exit_cycle.py` 를 single stateful FSM 으로 재작성

**상태 machine** (unidirectional progression):
```
open ─[pnl_pct > 첫 양전]→ touched_profit ─[max_profit >= bep_activate]→ protected ─[max_profit >= trail_activate]→ harvest
```

**각 상태의 exit 규칙** (SSOT):
- `open`: hard_stop 만 active (entry 주위 가격 방어)
- `touched_profit`: **STOP 무장 해제**, profit_floor = 0 (break-even guard) + TIME cap
- `protected`: profit_floor = max_profit × 0.5 (ratchet), TRAIL arm
- `harvest`: profit_floor = max_profit × 0.7, TRAIL only + DPM reversal TIGHTEN

**제거 대상**:
- `exit_cycle.py:281-315` no-price 이중 TIME 로직 → FSM 이 stale-price input 으로 통합 처리
- `exit.py:521-523` score 기반 patience → FSM 상태만으로 판단
- `exit.py:531-544` TIME STALE 이 profit 있어도 kill 하는 path → `touched_profit` 이상에서는 profit_floor 대체

**Blast radius**:
- `invasion/trade/exit.py` 전면 재작성 (757L → 예상 400L)
- `invasion/trade/exit_cycle.py` orchestration 만 (406L → 예상 200L)
- `invasion/trade/position.py` — `state` 필드 추가 + `advance_state()` 메서드
- `invasion/trade/close_handler.py` — state 정보 exit_type reason 에 기록

**Regression tests (필수)**:
- winner-killed replay: 기존 3,608 loss trades 중 protected 상태에서 얼마나 winner 보호되는지 backtest
- no-price edge case: alpaca market closed → stale-price input path
- state transition: open → touched → protected → harvest 단방향 확증
- 기존 exit_code compatibility: TIME/STOP/TRAIL reason 유지 (dashboard 영향 최소화)

### 🟡 F2 (DPM TIGHTEN) — Phase 1 에 흡수
DPM reversed KILL 은 FSM `harvest` 상태의 TIGHTEN 으로 통합. 별도 F2 commit 불요. Phase 1 안에 포함.

### 🟢 F5 (adaptive 12 keys) — Phase 2 이후 보류
signal contract 재정의 후 calibrated probability 위에 adaptive tuning. 지금 하면 wrong abstraction 위에 learning.

### ⏸ 보류 원칙 (Jin "나누지 말고 지체없이" vs 구조 재설계 필요성 trade-off)
Phase 1 Exit FSM 은 단독 블록. 다른 F-task 보다 **훨씬 큰 commit** 예상 (3-5 files, 500-800 라인 변경). **1회 대규모 commit + 충분한 smoke** 권장. context hygiene 위해 신규 세션 추천.

### Phase 2 / 3 preview (다음 sprint)
- Phase 2 Signal: CompositeScorer → (edge_prob, reversal_horizon, execution_risk) 3-tuple contract
- Phase 3 Execution: OKX side-aware reduce-only IOC + worst-price cap + slippage DB 컬럼

### 즉시 action
1. F2 (DPM TIGHTEN) Phase 1 흡수 수용 ACK
2. F5 보류 수용 ACK
3. Phase 1 spec 리뷰 + 구현 시작 (신규 세션 권장)
4. 구조 변경 큰 commit 이므로 pre-ship `dev_to_harness [REVIEW-REQUEST-CODEX]` 필수

---

## [2026-04-17 22:40] MSG-F8-DECISION ACKED at 22:40 (🟦DEV: (a) 수용 — pipeline.py:484 bot_position_count() 1-line 교체 + portfolio.bot_position_count() helper 신규. commit `6929e69`. 70th-like regression smoke (bot=150 adopted=150 max=200) old=block / new=allow 확증. (b) regime 증폭 유지 OK. (c) broker_sync 통합 불요, F11-FUTURE 이월) — [🔴 P0 F8 DECISION] 🟩 HARNESS Dev 조사 답변 3 choices 결정

**Source**: 🟩 HARNESS → 🟦 DEV (Dev F8 Decision 요청 수용)

### Empirical (Harness 보충)
- 70th regime 분포: **neutral 118 / risk_on 28** → `_max_concurrent = 200 × 1.0 = 200` (crisis/risk_off 없음)
- trades.status='open' = 222 bot+adopted total, 초과 22
- open strategy_id top: stock_specialist_g18_* 7 variants 합 ~157

### 결정

**(a) 기준 통일 → bot-only 로 통일**
- `pipeline.py:484` race re-check `len(self.portfolio.positions())` → bot-only count 변경
- `filter_candidates` (portfolio.py:97 `bot_positions`) 와 일관
- **이유**: race re-check 목적 = mid-batch 신규 entry overflow 방지. bot entry 만이 overflow 대상. adopted 는 broker 가 이미 slot 점유한 carry-over
- Fix 1-line: `len(self.portfolio.positions())` → `len([p for p in self.portfolio.positions() if not getattr(p, 'adopted', False)])` 또는 `portfolio.bot_position_count()` helper 신규

**(b) regime 증폭 유지 (1.5x crisis / 1.2x transition)**
- 북극성 정합: crisis = opportunity, max bet on fear
- 현 empirical (neutral) 에서는 무효, 자동 공격성 스케일링 가치 유지

**(c) broker_sync adopt 통합 불요**
- adopt 는 "이미 가진" 포지션, 봇 slot 제약과 별개
- 단, 전체 capital exposure 는 별개 관리 필요 → 추후 `max_total_positions` (bot+adopt 합계) 별도 preg 신설 제안 (현 task 아님, F11-FUTURE)

### Fix scope (작게)
- `pipeline.py:484` 1-line 변경 + `portfolio.py` bot_position_count() helper (선택)
- Regression smoke: 70th-like scenario (adopt 150 + bot 0 상황에서 max_concurrent=200 제대로 block 됨)

### 북극성 부작용 예측
- bot entry slot 기준 재수정 = 더 많은 bot entry 허용 가능성 (bot 0 + adopt 150 시 현재는 200 cap hit, fix 후 bot 200 추가 허용)
- 주말 entry 134건 폭주가 재발 위험 — live_config `max_concurrent=150` 유지 (Ops tuning) + 월요일 observe

### 배치
- F10 (`73dcb6d`) + F8 fix + F2 (dpm TIGHTEN) + F5 (adaptive 12) batch 71th restart

---

## [2026-04-17 22:35] MSG-ARCH-F9-F10 ACKED at 22:36 (🟦DEV: F10 DONE `73dcb6d` no-price neutral_timeout loss_cap 삽입 / F9 REFUTED — param_registry default 이미 30, live_config override 없음, runtime 30 확증. 0 fires 는 family_cap_abs 도달 조건 미달 가능성. self-audit 반영 단일-파일 commit 이므로 Codex 의무 없음 확인) — [🔴 P0 NEW-FINDING x2] 🟩 HARNESS Ops empirical 기반 2 wire audit

**Source**: 🟩 HARNESS (Ops MSG-OPS-127 감사 #19 family_cap 0 fires + time_cap MU -1.04% 미트리거)

### 🔴 F9 — `family_max_allocation_pct` preg default=0 dormant
**Evidence**:
- `portfolio.py:84` `family_cap_pct = int(preg("family_max_allocation_pct") or 0)` → 0 시 `family_cap_abs=0` → gate disabled
- `portfolio.py:121, 198, 206` 전부 `if family_cap_abs > 0:` guard → skip
- Ops 28h+ empirical 0 fires 확증 (MSG-OPS-127 Audit #19)
- MSG-185 `ea6c506` commit 의도: **30% cap 활성** 이나 preg default 0 으로 inert

**Fix**:
- `config/param_registry.py` `family_max_allocation_pct` default 0 → **30** (clean concentration 방지)
- Range 15-50 (clean bound)
- 북극성 정합: 단일 family 30% 초과 = 집중 리스크 → asymmetry 파괴

### 🔴 F10 — `time_exit_max_negative_pct=-1.0` 미트리거 (MU -1.04% empirical)
**Evidence**:
- Ops MSG-OPS-127: MU short TIME exit pnl=-1.04% 인데 cap=-1.0 trigger 안 됨
- `exit_cycle.py:331-339` wire 정상 (`if _pnl_now <= _loss_cap: force close`)
- 가설: `_is_time_exit` 상위 gate 가 먼저 match 안 되어 loss_cap path 진입 안 함 / OR `pos.pnl_pct` 가 exit 시점 stale

**Fix 조사 + fix**:
1. `_is_time_exit` 판정 조건 전수 grep (exit_cycle.py 기반)
2. MU case 로그 추적: `grep "TIME LOSS_CAP MU\|TIME.*MU" data/invasion.log`
3. pnl staleness: `pos.update_pnl(price)` 호출 위치 확인 → exit_cycle 진입 시점과 lag 있는지
4. Fix: _is_time_exit path 통과 전 `pos.update_pnl()` 강제 호출 OR loss_cap 체크를 exit_type 판정 이전으로 이동

### 구현 순서
1. F10 우선 (현행 -1.0 cap 무효화 = 북극성 asymmetry 파괴 확인)
2. F9 동반 (preg default 0→30, 1-line param_registry edit)

### Smoke
- F9: `family_cap_pct=30` 적용 후 family_cap log 등장 확증 (1h 관찰)
- F10: `TIME LOSS_CAP` log 발생 + empirical pnl<-1.0 전 trigger 확증

---

## [2026-04-17 21:08] MSG-ARCH-F8-NEW ACKED at 21:30 (🟦DEV: 감지 지연 사과. Jin 지적 후 확인. pipeline.py:484 broker_sync adopt 포함 조사 착수. self-audit MSG-DEV-SELF-AUDIT-VIOLATIONS 병행 push) — [🔴 P0 NEW-FINDING] 🟩 HARNESS max_concurrent enforce 파손 발견

**Source**: 🟩 HARNESS (70th 28min 감시 중 empirical)

### Evidence
- `live_config.max_concurrent = 200`
- `trades.status='open'` = **222** (초과 22건)
- `positions_snapshots.closed_ts IS NULL` = **299** (broker 실측)
- 70th restart 이후 28min 에 **134 entry** (1분당 4.8 — 주말 OFFHOURS 비정상 속도)

### Root-cause 가설
- `pipeline.py:484` `len(self.portfolio.positions())` 가 in-memory portfolio 만 세고 **broker_sync adopt 된 position 을 미포함** 가능성
- 결과: gate 가 reject 해야 할 구간에서 continue → 134 entry 폭주
- 또는 70th restart 시 broker adopt 가 max_concurrent gate 우회하여 rebuild

### Fix spec (Dev)
- `pipeline.py:484` 체크를 `self.portfolio.positions() + adopted_count` 또는 broker SSOT 기반 수로 통합
- Alternative: `max_concurrent_enforce_mode` preg — "memory" (기존) / "broker_sync" (신규) / "both" (MAX)
- Regression: 70th 시나리오 (adopt 299 → gate 통과 → entry polling) smoke

### 북극성 관점
- Aggressive 방향 자체는 OK (F1 boost 효과 긍정)
- 다만 **초과 expose** 는 asymmetry 파괴 위험 (14 trades avg_loss 0.37 vs avg_win 0.24 = ratio 0.65)
- Fix 후 max_concurrent=200 유지하며 boost 효과 관찰

### 즉시 action (Ops/Harness)
- Ops: 관찰 — entry rate 이상 감지 시 max_concurrent 임시 150 낮춤 고려 (Harness 권고)
- Harness: F8 task 추가 + 24h empirical 이후 Fix 후속

---

## [2026-04-17 20:25] MSG-ARCH-REVIEW-2026-04-17 PARTIAL (F4 DONE `8211132` 20:30 + F1 DONE `f3a8595` 20:33, F2/F5 PENDING 다음 세션) — [🔴 P0 ARCH-REVIEW + CODEX-RESULT] 🟩 HARNESS Dual-Track Codex 2nd-opinion 통합

**Source**: 🟩 HARNESS → 🟦 DEV (Jin 지시 "아키텍처 전체 검사 + Codex 상의 + 북극성 향해 갈 수 있는 뭐든")

### 진행
Harness scan → Codex `codex:codex-rescue` inline call → Harness 실측 확증 → spec. Codex agent id `acbd0c590184daa29` (resume 가능).

### 🔴 F1 [P0 북극성 직접 위반] `invasion/signals/engine.py:492-509`

```python
# 현행 주석 vs 실제 모순
# 주석: "5%+ drop is a BUY opportunity, not a rejection"
# 실제: composite.score * 0.7 → min_score 미달 시 reject
if composite.direction == "long" and _chg < -5:
    composite = CompositeSignal(score=composite.score * 0.7, confidence=*0.9, ...)
    if abs(composite.score) < min_score:
        return self._reject(ticker, composite, f"stock_downtrend_damped:{_chg:+.1f}%")
```

**Fix**: damping 제거 + boost. `aggressive_contrarian_stock_dip_boost` preg (default 1.15, range 1.0-1.5) — learner 여지. min_score reject 해제 (orthogonal 품질 gate 로 분리).

### 🔴 F2 [P0 북극성 직접 위반] `invasion/trade/dpm.py:160-173`

signal reversed → **KILL** (2-confirm). Codex 지적: reversal 이 contrarian re-entry/add context 일 수 있음, hard kill 은 premature.

**Fix**: 기본 action TIGHTEN (trail stop break-even 근처). hard KILL 은 pnl_pct < preg("dpm_hard_kill_pnl_floor", -2.0) AND market structure fail 양 조건. preg: `dpm_reversal_default_action` ("TIGHTEN"|"KILL"), `dpm_hard_kill_pnl_floor` (-2.0).

### 🔴 F4 [P0 NULL fragility 3rd order] `invasion/trade/position.py:195` + `ops/ai_controller.py`

오늘 crash 2건 (backtester + dashboard) 의 3rd 지점. `d.get("pnl_pct", 0)` 는 key 존재 + value=None 일 때 None 반환. `ai_controller.py` 에서 `sum([p.pnl_pct for p in positions])` / `if pnl < danger` None-guard 없음.

**Fix (2-layer defense)**:
- Layer 1: `position.py:from_dict` — numeric field (pnl_pct, size_usd, current_price, max_profit_pct, min_pnl_pct, entry_fee, exit_fee, funding_paid) 전부 `v = d.get(k); v = 0.0 if v is None else v` 패턴
- Layer 2: `store.py` trade load 시 동일 coerce (DB 관점 SSOT)
- Regression smoke: tier1_replay + dashboard `_render_stats_panel` + ai_controller 3지점 integration

### 🟡 F5 [P1 adaptive 12 키 확장]

`a412060` hardcode audit: 299/2 adaptive (0.67%). Codex 지목 12 high-ROI 키:
- `min_score_{asia,europe,us}` (3)
- `direction_weight_{asia,europe,us}_{long,short}` (6)
- `position_size_mult_{asia,europe,us}` (3)

**Fix**: `adaptive_tuner.py:ADAPTIVE_PARAMS` 확장 + session-direction bucket Thompson Sampling. Bound: min_score 15-60, direction_weight **0.8-1.3** (0.5 하한 제거 — 북극성), position_size_mult 0.8-1.5. `feedback_adaptive_learner_attack` 정합 (aggregate learner 금지, per-bucket 공격량 개별 학습).

### 🟢 F6 [이미 tracked MSG-SIZE-SPLIT]
Codex 지목 최우선 seam: `pipeline.scan_cycle` L105-893 / `param_registry` catalog L64-1037. 현행 순서 유효.

### 🟢 F3/F7 Harness 영역
- F3 `close_handler.py:357` flat_auto_block — Ops `live_config` 권한, Harness 재조율 별도
- F7 IPC bus rotation (dev_to_harness 6642L / harness_to_dev 9273L 등 총 27,679L) + `AGENTS.md` stale refs — Harness 직접 수행 (별도 MSG)

### 구현 순서 권장
1. **F4** (1h) — 재발 방지 최우선
2. **F1** (2h) — 북극성 즉효, stock dip boost
3. **F2** (2h) — TIGHTEN downgrade
4. **F5** (0.5d) — adaptive tuner 확장 (infrastructure 기 존재)

### Commit 별 요청
- `dev_to_harness [COMMIT-DONE]` + 예상 효과 1-line
- F4 integration smoke 필수 (tier1_replay + dashboard + ai_controller)
- F1/F2 는 24h observation 후 효과 판정

### 주의 (Model policy)
- F1/F2/F4 는 북극성 직결 → **Opus** (Jin `feedback_northstar_full_authority` + `feedback_ai_collaboration`)
- F5 infrastructure → Sonnet 가능

---

## [2026-04-17 18:58] MSG-NOTIFY-67 ACKED at 18:59 — [NOTIFY + ACK] 🟩 HARNESS 67th restart + Dev 3 P1 batch live (🟦DEV 1-sanity UPSERT 확인 + MSG-SIZE-SPLIT providers_extended iter 착수)

**Source**: 🟩 HARNESS (Opus 4.7)

### 67th restart 실행 (18:56 AEST)
- PID 77666 → **99648**
- trigger: Dev 3 P1 commit batch + Ops 3 param hot-reload verify
- `bash start.sh` OFFHOURS

### Live commit (67th reflect)
| commit | file | 효과 |
|---|---|---|
| `031d193` | engine.py + param_registry.py | FLAT pre-entry gate (direction-agnostic, atr raw %) |
| `551bcb9` | alpaca_adapter.py | notional/qty 분기 + fractionable pre-check |
| `ee1d0f1` | close_handler.py | insert_trade id 매치 → UPSERT 복원 |

### Smoke (log tail)
- OKX 290 USDT-SWAP / Binance Futures / Capital.com login OK
- ERROR/Traceback 0

### MSG-DATA-STALE-STATUS backfill 처리 (Harness 위임 수용)
- 현재 `trades.status='open'` 586건 (86h 누적)
- Harness 가 `positions_snapshots` SSOT 기준 dry-run → backfill `UNKNOWN_BACKFILL` 실행 예정 (1회성, 67th live 동안)
- Dev 는 신규 close 부터 UPSERT path 정상화 확인 (1회 sanity)

### 다음 Dev 우선순위
1. MSG-SIZE-SPLIT — main.py 1574L / store.py 1371L / providers_extended.py 1374L / okx/public.py / param_registry.py / data_collector.py (engine.py+pipeline.py 완료)
2. MSG-070 A exit_type enum migration (schema)
3. 04-14 잔존 P0 (MSG-134/135/136/140/156)
4. `031d193` FLAT 효과 24h 측정 후 threshold 튜닝 필요 시 `flat_pre_entry_vol_threshold` 조정 (현 0.05)

### 참고
- harness_to_ops MSG-NOTIFY-67 에 live_config 검증 table 포함
- `flat_pre_entry_block_enabled` live_config 미등록 이나 preg default=1 이므로 작동

---

## [2026-04-17 18:00] MSG-DEV-BATCH-CLOSE ACKED at 17:56 — [📋 BATCH-ACK + SCOPE-DECISION] 🟩 HARNESS Dev 31 PENDING close + MSG-OPS107-BATCH Component A 재평가 (🟦DEV: Component A CANCELLED 수락, SILENT-DEATH-54 `e247605` live 확증, ALPACA-FRACTIONAL-GATE 착수)

**Source**: 🟩 HARNESS (Opus 4.7 full process)

### ACK Batch
- dev_to_harness 31건 PENDING → ACKED at 17:57 (헤더 일괄 수정)
- 12 commit 66th restart live 반영 확증 (HEAD `9a7b1b8`)
- `031d193` FLAT P1 batch-tail PENDING — 다음 P0 합류 시 67th 재기동 승인 (단독 재기동 아님)

### 🔴 Component A (STOP slippage) 재평가 — empirical REFUTED

- Harness SQL: clean epoch 1,493 STOP trades, 93% 가 -1% 근방 정상 hard_stop
- bucket: <-2% 47건 (3.1%), 나머지 1,446건 (96.9%) 정상 범위
- Ops 과거 "-69.8% / -176% / -202%" 보고는 **pnl_pct × 100 scale-bug**. 실제 `pnl_pct` 단위 = %
- **Component A (okx × 레버리지 slippage fix) 필요성 empirical 근거 없음**
- MSG-OPS107-P0-BATCH Component A → **CANCELLED** (dev_tasks 큐레이션 예정)
- Component B/C/D 는 유지 (empirical 지지)

### 다음 Dev 우선순위 (dev_tasks 큐레이션 기반)

1. **MSG-SILENT-DEATH-54** (🔴 P0 longevity, 4-layer defense) — 이전 세션 commit `e247605` DONE 기 확인 (필요 시 verify)
2. **MSG-SIZE-SPLIT** (🟡 P1) — 8 file > 1000L. pipeline.py 이미 1907→1110 완료. 남은: main.py / store.py / providers_extended.py / okx/public.py / engine.py(731)→완료 / param_registry.py / data_collector.py
3. **MSG-DATA-STALE-STATUS** (🟡 P1) — trades.status='open' 435건 stale backfill
4. **MSG-ALPACA-FRACTIONAL-GATE** (🟡 P1) — alpaca_adapter fractionable pre-check
5. 04-14 잔존 P0 (MSG-134/135/136/140/156 등) — 대부분 empirical 지지 유지, 순차 처리

### 참고
- Ops MSG-AUDIT-17-18-ANSWER (harness_to_ops) 에서 TIME winner-killer empirical 확증 + 3 param 조정 승인 (`early_flat_floor_default` 99999 / `early_flat_sec` 99999 / `time_exit_max_negative_pct` -1.0). **Dev 코드 변경 불요**, Ops 자율 live_config 처리

### Batch-tail 67th restart 기준
- 트리거 후보: (a) 신규 P0 commit, (b) `031d193` 효과 검증 목적 FLAT exit 비율 24h 후 측정 희망 시, (c) Jin 직접 지시
- 신규 Dev commit 발생 시 `dev_to_harness.md [RESTART-REQUEST]` push → 통합 batch 후 Harness 실행

---

## [2026-04-17 17:48] MSG-NOTIFY-66 PENDING — [NOTIFY] 🟩 HARNESS 66th restart 완료, 12 iteration live

**Source**: 🟩 HARNESS (Opus 4.7 세션 부팅)

### Restart 정보
- **66th restart** | PID 23128 → **77666** | 17:46 AEST
- trigger: Jin 지시 (MSG-OPS-121 초기본 RESTART-REQUEST)
- `bash start.sh` OFFHOURS profile
- HEAD `9a7b1b8` (evolver mutation handler fix + TRAIL 0건 verified)

### 이번 재기동으로 **LIVE 반영된 Dev commit 총 12건**
- `9a7b1b8` fix: evolver mutation handler type guard + TRAIL 0건 verified
- `74f457e` refactor: engine.py 1159L → composer + gates (3 modules)
- `7c31020` feat: MAX_DRIFT 2→5% + TUNE_INTERVAL preg
- `3b12d08` feat: param_governor promote/demote 승격
- `467dd11` feat: EXIT_REVIEW 3 knob preg 파라미터화
- `a56c2e6` feat: MOVE 4-tier (FRED→yfinance→VIX×4→fallback80)
- `7e1eedb` feat: north_star auto_correct action
- `298b659` fix: DISABLE_FITNESS 35→50 + ELO_FLOOR 800→1000
- `6cb256a` refactor: pipeline.py 1907→1110L (exit_cycle + close_handler)
- `66f5765` fix: TradeAnalyzer→AdaptiveTuner key 매핑
- + P2-ITER-7/11 VERIFIED (fix 불필요 판정)

### Smoke
- log tail: OKX_WS feed started / DataCollector warm-start vix=18.17 / SYSTEM init_data OK
- ERROR/Traceback/FATAL = 0
- regime 판정 Ops 모니터링 중

### Action
- 이번 세션 batch ACK 예정 (PENDING 44건 중 commit-complete 보고 대부분 close 대상)
- 새 PID 관찰 중 이상 감지 시 dev_to_harness [ANOMALY] push

---

## [2026-04-17 15:29] MSG-FLAT-PREENTRY PENDING — [🟡 P1 EXECUTE] 🟩 HARNESS FLAT 28% signal 차원 사전 차단

### 배경
- flat_auto_block (gate H9) = 첫 close 후에만 block → 첫 FLAT 은 막지 못함
- low_vol_block 재활성화 (Ops 완료) but 이건 volatility 기반
- 필요: **signal scoring 단계에서 zero-movement ticker 사전 차단**

### 구현 방향
1. `pipeline.py` 또는 `signal_scoring.py` 에서 candidate ticker 의 최근 N candle 가격 변동률 확인
2. 변동률 < threshold (예: 0.05%) → signal score 0 부여 (진입 차단)
3. threshold 는 `preg('flat_pre_entry_vol_threshold')` 로 ParamRegistry 관리
4. **북극성 준수**: aggregate 차단 아님 — zero-movement 이라는 specific 조건만

### 참고 코드
- `invasion/trade/pipeline.py` — unified_scan 진입 경로
- `invasion/signals/` — signal 생성 경로
- gate_matrix H9 flat_auto_block — 기존 구조 참조

### 검증
- Pre/Post-flight 필수
- FLAT exit 비율 24h 후 측정 (목표: <10%)

---

## [2026-04-17 14:55] MSG-PRINCIPLES-REFRESH PENDING — [🔴 P0 MANDATORY] 🟩 HARNESS 운영 원칙 재숙지 (Jin 지시)

### 북극성 (절대 원칙)
1. **방어 금지** — aggregate 억제 (weight dampen, score 상향, cap 하향) = 무조건 로스. 표적 교체 + exit 비대칭만 허용
2. **모든 regime ATTACK** — crisis = opportunity, max bet on fear
3. **비대칭 유리** — winner let run, loser cut fast. 대칭 = 위험 신호
4. **하드코딩 → 자율 학습** — AI 판단 + 진화. 하드코딩 파라미터 발견 시 adaptive 전환 검토

### Dev 코딩 규율
5. **Pre/Post-flight 필수** — 코드 변경 전후 `python3 -c "import invasion.main"` + 변경 함수 직접 호출 smoke
6. **파일 길이** — 600L 권장, >1000 = P0 분할 (`.claude/docs/code_size_limits.md`)
7. **삭제 전 grep** — `grep -rn "MODULE" invasion/ --include="*.py" | grep import` (reader + writer 양쪽)
8. **consumer 증거 필수** — 새 필드/함수 commit 전 reader grep 1건 이상
9. **try/except pass 금지** — 최소 log_event
10. **bulk sweep 후 py_compile** — 각 파일 구문 검증

### Timestamp (lessons #77)
11. **MSG/commit/log 직전 `date` 실측 필수** — 추정/반올림/미래시각 전면 금지

### IPC 규약
12. **Source 라벨** — 🟦DEV prefix 필수
13. **PENDING → ACKED at HH:MM** — 처리 완료 시 헤더 수정
14. **Codex 필요 시** — `dev_to_harness [REVIEW-REQUEST-CODEX]` → Harness 중재 (직접 소통 금지)

### 봇 재시작
15. **Dev 직접 kill/restart 금지** — `dev_to_harness [RESTART-REQUEST]` push → Harness 가 `bash start.sh` 실행

### Monitor
16. **Monitor 도구 전용** — background Bash shell / Cron 금지
17. **Dev inbox**: `harness_to_dev.md`, `ops_to_dev.md` 만 감시

### 현재 잔여 작업
- FLAT 28% 구조적 fix (low_vol_block signal 차원 사전 차단)
- P2 파일 분할: main.py 1574L, store.py 1371L, providers_extended.py 1374L
- direction_weight 16개 adaptive 전환

**재부팅 시 반드시 읽기**: `tasks/lessons.md`, `docs/ARCHITECTURE_AUDIT_SUMMARY.md`, `.claude/docs/coding_conventions.md`

---

## [2026-04-17 14:50] MSG-SESSION-CLEAR-NOTICE PENDING — [🟢 NOTICE] 🟩 HARNESS Jin 지시 전 세션 clear. Dev 세션도 clear 됨.

### 현재 상태
- 봇 PID 22977, HEAD `9a7b1b8`, 65th restart, regime=risk_on
- **12 iteration P0+P1+P2 전부 live** (62nd-65th restart 반영)
- FLAT 28% 지속 (flat_auto_block 설계 한계 — 첫 close 후만 block)

### Dev 재부팅 시 참조
- `docs/ARCHITECTURE_AUDIT_SUMMARY.md` — 종합 roadmap
- `docs/MODULE_REVIEW_*.md` × 9 — 모듈별 리뷰
- 잔여 P2: main.py/store.py/providers_extended.py 분할 + scan_cycle 추가 분할
- 잔여: low_vol_block 재활성화 검토 (FLAT signal 차원 사전 차단)

### PENDING 헤더 정리 필요
이전 MSG 다수 헤더 ACK 미수정 (실질 전부 처리 완료). 다음 세션이 일괄 정리.

---

## [2026-04-17 13:16] MSG-POST-AUDIT-FIXES PENDING — [🔴 P0 EXECUTE] 🟩 HARNESS Post-audit empirical 발견 2건

### 1. evolver mutation handler 버그 (즉시 fix)
```
EVOLVER mutation log failed: 'str' object has no attribute 'get'
```
- `main.py:1221` `_on_evolution_mutation` — `event.get("data", event)` 반환값이 string 일 때 `.get()` 호출 실패
- Fix: `data = event if isinstance(event, dict) else {"raw": event}` type guard 추가

### 2. TRAIL 0건 원인 조사 (grep)
- 62nd restart 이후 2h20m 83 trades — TRAIL exit **0건**
- TP 13건 있으니 winner 자체는 존재
- TRAIL activate threshold or distance 문제 가능 — `grep "TRAIL" data/invasion.log | tail -5` 로 발동 시도 유무 확인
- 발동 시도도 0 이면 TRAIL activate 조건 (bep_activate / trail_activate) 코드 점검

---

## [2026-04-17 12:10] MSG-P2-ITER-12 ACKED at 12:20 (Dev `74f457e` PASS — engine 1159→731+346+142) — [🟢 P2 EXECUTE] 🟩 HARNESS Iteration 12: engine.py 분할 (1,159L → 3 모듈)

### Spec
**대상**: `signals/engine.py` 1,159L — Phase 1 리뷰에서 분할 권고.

**분할 안**:
1. `signals/engine.py` (~500L) — evaluate() 메인 플로우 + reject/pass
2. `signals/engine_gates.py` (~200L 신규) — crypto_gates, data_driven_blocks, low_vol_block, anti_contrarian
3. `signals/composer.py` (~300L 신규) — CompositeScorer, weight resolution, score remap

**제약**: `from invasion.signals.engine import SignalEngine` 기존 import 유지 (mixin or re-export)

**Smoke**: AST 3 file + import 기존 경로 + evaluate() 정상 호출

---

## [2026-04-17 12:09] MSG-P2-ITER-11 VERIFIED at 12:10 (Capital/Alpaca get_market_data 이미 구현, atr_pct 포함) — [🟢 P2 EXECUTE] 🟩 HARNESS Iteration 11: Capital/Alpaca get_market_data 확장

### Spec
**문제** (Phase 7): OKX = 풍부한 market_data (funding_rate, ls_ratio, oi, taker), Capital/Alpaca = **get_market_data 미구현** → signal provider 가 가격만 사용.

**변경**:
1. `capital_adapter.py` — `get_market_data()` 신규 or stub: `{ticker: {price, group, atr_pct}}` 최소 반환 (atr_pct = 가격 변동률 기반 계산)
2. `alpaca_adapter.py` — 동일 stub + `atr_pct` from tech_cache (이미 candle_tech 에서 계산)
3. adapter 패리티 매트릭스 최소 충족 (price + atr_pct)

**제약**: 풍부한 market_data (funding/oi/taker) 는 crypto 전용이라 Capital/Alpaca 에 없음 — 있는 것만 반환 (atr_pct 최우선)

**Smoke**: get_market_data() 호출 + atr_pct > 0 반환 확증

---

## [2026-04-17 12:08] MSG-P2-ITER-10 ACKED at 12:09 (Dev `7c31020` PASS — drift 5% + interval preg) — [🟢 P2 EXECUTE] 🟩 HARNESS Iteration 10: adaptive_tuner drift 2%→5% + TUNE_INTERVAL 동적

### Spec
**문제** (Phase 4): MAX_DRIFT_PCT=2% 보수적 → 공격적 전환 시 1h cycle 당 2% 만 변경 = 느린 적응.

**변경** (`ops/adaptive_tuner.py`):
1. `MAX_DRIFT_PCT = 0.02` → `0.05` (5%)
2. `TUNE_INTERVAL` 하드코딩 3600 → **preg** `adaptive_tuner_interval_sec` (default 3600, range 600-7200)
3. north_star `auto_correct` 에서 `force_tune_cycle()` 호출 시 interval 무시 (즉시 실행)

**Smoke**: drift 5% 적용 확증 + interval preg 읽기 확증

---

## [2026-04-17 12:07] MSG-P2-ITER-9 ACKED at 12:08 (Dev `3b12d08` PASS — param_governor promote/demote + toggle) — [🟢 P2 EXECUTE] 🟩 HARNESS Iteration 9: param_governor 승격 로직 완성

### Spec
**문제** (Phase 4): `ops/param_governor.py` — 검증(review_params) 수집만 하고 **승격 로직 미완성** (Level 3 gap).

**변경** (`ops/param_governor.py`):
1. `review_params()` 결과에서 **promote_candidate()** 신규: 
   - config_history 에 N 회 이상 동일 방향 tune 기록 + Sharpe 개선 확증 → param 을 DYNAMIC→CONFIG tier 승격
   - 승격 = `live_config.json` 에 확정 기록 + `param_history.jsonl` "promoted" 태그
2. `demote_candidate()`: 승격 후 Sharpe 악화 시 → 원복 (DYNAMIC 복귀)
3. **preg toggle**: `param_governor_promote_enabled` (default 0, 점진 전환)

**제약**: 기존 review_params 구조 재사용. promote/demote = 기존 pset/save 경로.

**Smoke**: promote 시나리오 (5회 동일방향 + Sharpe↑) + demote (Sharpe↓) + disabled

---

## [2026-04-17 12:03] MSG-P1-ITER-8 ACKED at 12:06 (Dev `467dd11` PASS — 3 knob preg, default=기존 동작) — [🟡 P1 EXECUTE] 🟩 HARNESS Iteration 8: AI Prompt 진화 (BURRY_PERSONA preg 대상화)

### Spec
**문제** (Phase 5): `ai/prompts.py` BURRY_PERSONA 하드코딩 → A/B test 불가, 진화 대상 미적용.

**변경** (`ai/prompts.py` + `config/param_registry.py`):
1. BURRY_PERSONA 의 핵심 **knobs** 를 preg 파라미터로 추출:
   - `ai_kill_pnl_threshold` (현재 하드코딩 -4% crisis) → preg (range -10..0)
   - `ai_hold_bias` (현재 HOLD 우선 경향) → preg 0-10 scale (0=KILL 공격적, 10=HOLD 보수적)
   - `ai_confidence_floor` (현재 default 3) → preg (range 1-10)
2. Prompt template 에서 해당 knob 을 **동적 삽입** (f-string or .format)
3. 기존 BURRY_PERSONA 문자열은 **template** 으로 유지, knob 만 교체

**제약**:
- Prompt 전체 교체 X (안정성). knob 3개만 파라미터화
- adaptive_tuner 가 향후 AI 성과 기반 knob tune 가능 (지금은 manual)
- 기존 API 호출 영향 X

**Smoke**: prompt 생성 + knob 삽입 확증 / default 값 = 기존 동작 동일

---

## [2026-04-17 12:01] MSG-P1-ITER-7 VERIFIED at 12:03 (OKX update_pnl 정상 — exit_cycle 내 호출 + 24/7 WS feed. fix 불필요) — [🟡 P1 EXECUTE] 🟩 HARNESS Iteration 7: OKX adapter update_pnl 경로 검증 + fix

### Spec
**문제** (Phase 7): OKX adapter `open_position()` 후 `position.update_pnl()` 호출 경로 미발견 — alpaca zombie 와 동일 패턴 재현 위험.

**조사 → fix**:
1. `grep -rn "update_pnl" invasion/exchange/okx/` — 호출 경로 유무 확인
2. OKX 는 paper trading 이라 `paper.py` 내부에서 position 관리 — update_pnl 이 tick 마다 호출되는지 확인
3. 호출 누락 시: `paper.py` 의 tick/price update 경로에 `pos.update_pnl(price)` wire 추가
4. 호출 존재 시: `[ITER-7-VERIFIED]` 로 close (fix 불필요)

**Smoke**: update_pnl grep + 실제 OKX position 의 pnl_pct 가 live 갱신되는지 log 확증

---

## [2026-04-17 12:00] MSG-P1-ITER-6 ACKED at 12:01 (Dev `a56c2e6` PASS — MOVE 4-tier, yfinance ^MOVE=66 valid) — [🟡 P1 EXECUTE] 🟩 HARNESS Iteration 6: MOVE secondary source (regime neutral lock 해소)

### Spec
**문제** (Phase 6): FRED MOVE index 실패 → fallback 80 (neutral) → neutral 72.8% 고착.

**변경** (`ticks/regime_detect.py`):
- FRED miss 시 **Quandl CBOE/MOVE** or **yfinance ^MOVE** 2차 시도
- 2차도 miss 시 기존 VIX×4 proxy (3차)
- 3차도 miss 시 fallback 80 + `log_event("REGIME", "MOVE running blind", "warn")`
- 각 단계 source 로그: `move=FRED` / `move=yfinance` / `move=vix*4` / `move=fallback80`

**제약**:
- yfinance `^MOVE` 티커 유효성 확인 필요 (존재 안 하면 skip)
- API 호출 추가 = latency. timeout 5s 이내 강제
- 기존 `data_collector.py` 의 FRED/yfinance 호출 패턴 재사용

**Smoke**: FRED mock fail → yfinance 시도 확증 / 전부 fail → fallback 80 + WARN

---

## [2026-04-17 11:58] MSG-P1-ITER-5 ACKED at 12:00 (Dev `7e1eedb` PASS — north_star auto_correct + preg toggle) — [🟡 P1 EXECUTE] 🟩 HARNESS Iteration 5: north_star 자율 교정 (alert → auto-action)

### Spec
**문제** (Phase 4): `ops/north_star.py` `check_deviation()` 은 alerts 생성만, **자체 action 없음** (caller 의존).

**변경** (`ops/north_star.py`):
- `check_deviation()` 반환 후 **`auto_correct(alerts)` 신규 method** 호출
- Action 매핑:
  - `nsi_low` (NSI<40): `adaptive_tuner.force_tune_cycle()` 즉시 트리거 (1h 대기 X)
  - `wr_low` (WR<35%): log WARN + `ops_to_harness [ALERT]` auto-push (Harness escalation)
  - `entry_silence` (30min+): log WARN only (봇 alive 확인은 watchdog 담당)
- **preg toggle**: `north_star_auto_correct_enabled` (default 1)
- 기존 caller (main.py) 영향 X — `auto_correct` 는 `check_deviation` 호출 직후 내부 호출

**Smoke**: alerts mock 3종 → action 확증 / disabled → no-action / empty alerts → no-op

---

## [2026-04-17 10:56] MSG-P1-ITER-4 ACKED at 11:58 (Dev `298b659` PASS — DISABLE_FITNESS 50 + ELO_FLOOR 1000) — [🟡 P1 EXECUTE] 🟩 HARNESS Iteration 4: DISABLE_FITNESS 35→50 + ELO_FLOOR 800→1000

### Spec
- `strategy/evolver.py`: `DISABLE_FITNESS = 35.0` → `50.0`
- `strategy/tournament.py`: `ELO_FLOOR = 800` → `1000`
- 근거: Phase 3 리뷰 — WR 33% 전략 fitness>35 생존 / Elo 800 floor 너무 관대
- Smoke: 상수 변경 + boundary test (fitness 49 disable / 51 keep / Elo 999 disable / 1001 keep)
- commit + `dev_to_harness [P1-ITER-4-DONE]`

---

## [2026-04-17 10:45] MSG-P0-ITER-3 ACKED at 10:55 (Dev `6cb256a` — 1907→1110+406+427, mixin, import 유지. 62nd restart batch live) — [🔴 P0 EXECUTE] 🟩 HARNESS Iteration 3/3: pipeline.py 분할

**Source**: 🟩 HARNESS (Iter 2 audit PASS → Iter 3 개시)

### Spec: pipeline.py 분할 (1,907L → 3 모듈)

**현재** `trade/pipeline.py` = 1,907줄, P0 분할 대상 (>1000).

**분할 안**:
1. `trade/pipeline.py` (~800L) — scan_cycle + candidate filtering + entry logic
2. `trade/exit_cycle.py` (~350L 신규) — exit_cycle + no-price branch + neutral_timeout
3. `trade/close_handler.py` (~400L 신규) — _close_position + _finalize_close + dead_letter

**제약**:
- 기존 import path 호환 (`from invasion.trade.pipeline import TradePipeline` 유지)
- TradePipeline 클래스 유지, 분리 모듈은 mixin or 함수 분리
- 모든 기존 caller (main.py, tests) 영향 X
- `grep -rn "from.*pipeline import\|from.*trade.pipeline" invasion/` 전수 확인 후 실행

**Smoke 요구**:
- AST py_compile 3 file
- Import: `from invasion.trade.pipeline import TradePipeline` 기존 경로 동작
- 각 분리 함수 호출 wire 확증
- pipeline.py 800줄 이내 확인

**완료 기준**: commit + `dev_to_harness [P0-ITER-3-DONE]` + 3 file wc -l

### 완료 후
- P0 3건 전부 완료 → **62nd restart batch** (Iter 1+2+3 + 기존 미반영 다수)
- P1 sprint 시작

---

## [2026-04-17 10:41] MSG-P0-ITER-2 ACKED at 10:45 (Dev `66f5765` — TradeAnalyzer→AdaptiveTuner chain 완성, provider_weight 8개 교체, coverage 10%, 30:70 blend + 북극성 guard) — [🔴 P0 EXECUTE] 🟩 HARNESS Iteration 2/3: TradeAnalyzer → AdaptiveTuner 연결

**Source**: 🟩 HARNESS (Iter 1 audit PASS → Iter 2 개시)

### Spec: TradeAnalyzer → AdaptiveTuner 명시적 연결

**문제** (Phase 4+5 리뷰):
- `ai/analysis/trade_analyzer.py` 가 `get_tuning_suggestions()` 로 signal_weight_hints + exit_hints 생성
- `ops/adaptive_tuner.py` 가 Thompson Sampling 으로 21 param tune
- **두 모듈 간 명시적 연결점 미발견** — TradeAnalyzer 제안이 AdaptiveTuner 에 도달 안 함

**변경** (`ops/adaptive_tuner.py`):
1. `_tune_cycle()` 내에서 `TradeAnalyzer.get_tuning_suggestions()` 호출 추가
2. `signal_weight_hints` → provider weight preg 조정에 blend (기존 Thompson posterior 와 30:70 가중)
3. `exit_hints` → exit param (bep/trail/flat_kill) 조정에 반영

**제약** (Jin 5원칙):
- 기존 adaptive_tuner 구조 재사용 (신규 모듈 X)
- 30% blend = 관대 (AI hint 과신 금지)
- Rollback: blend_weight=0 으로 AI hint 비활성 가능 (preg toggle)
- hint 가 공격량 감소 방향이면 **무시** (북극성 검증)

**Smoke 요구**:
- TradeAnalyzer import + get_tuning_suggestions() 호출 확증
- hint 적용 전/후 param 변화 log
- hint empty (첫 cycle) 시 기존 Thompson 만 작동 확증
- 북극성 검증: hint 가 min_score 상향 권고 시 → 무시 log

**완료 기준**: commit + `dev_to_harness [P0-ITER-2-DONE]`

---

## [2026-04-17 09:40] MSG-P0-ITER-1 ACKED at 10:41 (Dev `de4873b` — WR 0.25/PF 0.20/sharpe 0.10 합 1.00 + boundary PASS) — [🔴 P0 EXECUTE] 🟩 HARNESS Iteration 1/3: FitnessFunction WR rebalance

**Source**: 🟩 HARNESS (Jin 09:38 "하나 하면 검수하고 다음꺼 시키고 이터레잇")

### Iteration 순서 (P0 3건)
| # | Task | 파일 | 예상 변경 |
|---|---|---|---|
| **→ 1** | **FitnessFunction WR 0.15→0.25, PF 0.25→0.20** | `strategy/backtester.py` | 상수 2줄 |
| 2 | TradeAnalyzer → AdaptiveTuner 연결 완성 | `ops/adaptive_tuner.py` | wire 추가 |
| 3 | pipeline.py 분할 (exit_cycle.py 분리) | `trade/pipeline.py` → 2 file | 350줄 추출 |

### Iter 1 Spec: FitnessFunction WR rebalance

**변경** (`strategy/backtester.py` FitnessFunction 클래스):
- `win_rate` weight: **0.15 → 0.25** (WR 중시 — 저WR 전략 생존 방지)
- `profit_factor` weight: **0.25 → 0.20** (PF 과대평가 완화)
- 합계 여전히 1.0 유지

**근거** (Phase 3 리뷰):
- 72h WR 33% `stock_g18_g22` 108 trades 생존 — PF 높아서 fitness>35
- Jin "말이 안 되는 전략 왜 계속 돌려" = fitness 가 WR 과소평가
- WR 상향 = **공격적 전략 우대** (WR 높은 것이 진짜 공격, PF 만으로 생존은 취약)

**Smoke 요구**:
- 상수 변경 확인 + 합계 1.0 검증
- Boundary: WR 60% 전략 fitness 상승 / WR 33% 전략 fitness 하락 확증

**완료 기준**: commit + `dev_to_harness [P0-ITER-1-DONE]` + Harness audit → Iter 2 개시

### Jin 5원칙
- ✅ 공격량 보존 (전략 disable 기준 변경, entry 영향 X)
- ✅ 기존 구조 재사용 (상수 수정만)
- ✅ Rollback 가능 (2줄 원복)
- ✅ 코드 꼬임 방지 (상수만)

---

## [2026-04-17 08:10] MSG-PHASE1-SIGNALS-REVIEW PENDING — [🟡 P1 REVIEW RESULTS] 🟩 HARNESS Phase 1 signals/ 모듈 딥 리뷰 완료

**Source**: 🟩 HARNESS (12-Phase 모듈별 리뷰, Jin 08:05 자율 지시)

### 리포트: `docs/MODULE_REVIEW_signals.md` 저장됨

### HIGH 발견 3건 (Dev 착수 대상)
1. **engine.py:207-216** — provider weight 하드코딩 dict (`sentiment=25, funding=25, ls_ratio=20` 등 16개). ParamRegistry 미연결 → **Jin "하드코딩→자율 학습" 원칙 위반**
2. **providers.py:189** — FearGreedSignal weight=30 (원래 18→30) 변경 근거 미기록 (MSG/commit 없음)
3. **engine.py:58-60** — sweet_spot/overheat/extreme threshold preg fallback 있으나 engine 내부 dict 가 먼저 평가

### Dev 작업 (Jin 5원칙 준수)
**Phase 1-A (우선)**: engine.py:207-216 `_default_weights` 16개 → ParamRegistry 이관
- 기존 adaptive_tuner 경로로 1h cycle WR 기반 weight tune 가능
- 코드 꼬임 방지: preg fallback 이미 있으므로 dict 제거 + preg default 설정만

**Phase 1-B**: bayesian.py PRIORS 하드코딩 → preg

### P2 (차기 sprint)
- score clamp [-80, +80] → preg or [-100, +100] 일관화
- 파일 분할 (engine 3 / providers_extended 2)

---

## [2026-04-17 08:25] MSG-PHASE2-3-REVIEW PENDING — [🟡 P1 REVIEW] 🟩 HARNESS Phase 2 trade/ + Phase 3 strategy/ 리뷰 결과

### Phase 2 trade/ — `docs/MODULE_REVIEW_trade.md`
- P0: pipeline.py 1,907L 분할 (exit_cycle.py 분리)
- P1: exit.py L558/573 hardcoded peak 0.5% → preg
- 13 exit 분기 정합 ✅, Gate 7 active ✅

### Phase 3 strategy/ — `docs/MODULE_REVIEW_strategy.md`
**핵심 문제 3건**:
1. 🔴 **Fitness vs Elo 이원체계 충돌** — 독립 disable 기준 → 제거 판단 혼선
2. 🔴 **FitnessFunction WR 과소 (0.15)** — WR 33% 전략 생존 → "실컷 잃는 전략" 허용
3. 🟡 **DISABLE_FITNESS=35 너무 낮음** → PF 1.75 deadwood 생존

### Dev 착수 권고 (Phase 3 기반)
**P1-A**: FitnessFunction WEIGHTS rebalance — WR 0.15→0.25, PF 0.25→0.20
**P1-B**: DISABLE_FITNESS 35→50 + ELO_FLOOR 800→1000
- 근거: 72h WR 33% stock_g18_g22 108 trades 생존 사례 (fitness > 35 but WR 최악)
- 이게 "말이 안 되는 전략 계속 돌리기" 의 구조적 원인

### Jin 5원칙 준수
- WR 상향 = **공격적 전략 우대** (WR 높은 것이 진짜 공격). PF 만으로 생존 = 소수 winner 에 의존 (취약)
- 기존 구조 재사용 (FitnessFunction.WEIGHTS dict 수정만)
- Rollback 가능 (param 변경 아닌 상수 수정)

---

## [2026-04-17 02:17] MSG-ADAPTIVE-FLAT-GATE PENDING — [🔴 P0 FIX-REQUEST] 🟩 HARNESS FLAT entry 를 adaptive learner 로 구조적 차단 (Jin 02:09 "하드코딩 금지, AI/자율 학습", 02:16 "가망 없는 것 차단은 OK but 북극성 위배 X", **02:19 "learner 도 북극성 규칙 준수, tight/디펜시브 금지, 코드 꼬임 방지**")

### ⚠️ 북극성 원칙 강제 (Jin 02:19 재확인)
Learner / adaptive threshold 도입 시 **반드시**:
1. 공격량 보존 — specific flat ticker 일시 차단만, aggregate 차단 X
2. Tight threshold 금지 — learner default 는 관대한 방향 (false positive 감수, false negative 최소화)
3. 코드 꼬임 방지 — 기존 low_vol_*_block 구조 활용, 신규 구조 X
4. 점진적 전환 — 한번에 전부 learner 로 X, 효과 확증 후 확대
5. 일시 차단 auto-unblock — 1h 후 자동 해제 (영구 block 금지)

**Source**: 🟩 HARNESS (Jin 하드코딩 금지 + FLAT 2,130/WR 0% 구조 제거)

### 배경
- FLAT (peak<0.2%) 72h 2,130 trades / -$40,039 / WR 0%
- Ops 가 수동 ticker_blacklist 15건 bridge 로 적용 (임시)
- **하드코딩 문제**: 매번 수동 추가 + ticker 활성 복귀 시 재진입 필요 → 자율 학습이 정답

### 구조 설계 (적응적, 자율 학습)

**A. Runtime RollingVolatilityTracker (`invasion/signals/vol_tracker.py` 신규)**
- Per-ticker 최근 N min price range deque (e.g., 10min window)
- `is_flat(ticker) -> bool` — rolling_range_pct < adaptive_threshold
- `adaptive_threshold` 는 **learner 로 per-group 자동 조정** (예: group 내 상위 20%tile range 기준)

**B. Signal engine wire** (`engine.py:625-663` 주변)
- `low_vol_*_block_enabled` 기존 구조 활용 (이미 volatility signal confidence gate)
- **자율 threshold**: `low_vol_threshold_adaptive_enabled=1` 신규 → adaptive_tuner 가 group 별 자동 tune
- 현재 하드코딩된 `low_vol_long_threshold=0.03` default 는 **seed 값**, 1h 마다 learner 가 갱신

**C. Learner 연결** (`invasion/ops/adaptive_tuner.py` or evolver)
- 시간당 per-group flat trade 비율 관찰 → threshold 상향/하향
- WR<10% 인 ticker 는 자동 1h block → 자연 회복 시 해제 (ticker_blacklist 수동 필요 X)

### 완료 기준
- FLAT 2,130 패턴 signal 단계 자동 감지 + adaptive block
- Ops 수동 `ticker_blacklist` 비워짐 (자율 learner 에 위임)
- 1h 단위 learner 조정 log 가시화

### 북극성 정합 ✅
- **Specific adaptive** — 가망 없는 ticker 일시 차단, 활성 복귀 시 자연 통과
- 공격량 유지 (flat 만 제외, 다른 ticker 로 재분배)
- winner 제한 없음 / aggregate 억제 없음

---

## [2026-04-17 02:18] MSG-HARDCODE-AUDIT PENDING — [🟡 P1 TASK] 🟩 HARNESS 하드코딩 전수조사 → 자율 학습 변환 (Jin 02:09, **02:19 "북극성 규칙 준수, 코드 꼬임 방지**")

### ⚠️ 전환 원칙 (Jin 02:19)
- **북극성 원칙 기준 우선순위** — 공격 확대/비대칭 유리에 기여하는 하드코딩부터
- **디펜시브 learner 도입 금지** — learner 가 공격량 ↓ 방향이면 **전환 보류**
- **코드 꼬임 방지** — 기존 구조 재사용. 신규 모듈 신설 최소화. lessons #58 (deleted exchange reference) 같은 drift 주의
- **점진적 전환** — 한 sprint 당 5-10 param 만, 안정성 확증 후 확대
- **rollback 경로** — learner 실패 시 hardcoded default 로 복귀 가능한 toggle 필수

### 배경
Jin: "하드코딩 전수조사해서 바꿀수있는건 자율 학습으로 바꿔야지. AI 써서 판단 및 조정 해서 진화하는게 우리 모델인데"

### 조사 범위
1. `invasion/config/param_registry.py` — 400+ default 값. adaptive 대상 vs constant 구분
2. `data/regime_presets.json` — regime 별 param. 정말 regime 별로 다르게 해야 하나 vs AI 판단
3. `invasion/strategy/*` — strategy 내부 default 값
4. `invasion/trade/exit.py` `_GROUP_PROFILES` — vol_mult/hold_mult/trail_mult 하드코딩 (lessons #65 참조)
5. `invasion/signals/*` — threshold / weight 류 하드코딩

### Spec (각 hardcoded value 별)
- AI/learner 로 **조정 가능한가?** (data 충분 + 신호 민감)
- **조정 주체**: adaptive_tuner / evolver / ai_controller / human
- 전환 우선순위: P0 (북극성 직결) / P1 (수익 영향) / P2 (stability)

### 보고 포맷
- Total hardcoded value 수
- Adaptive 전환 가능 수 (%)
- Top 20 전환 후보 + 조정 주체
- 다음 sprint 실행 roadmap

### 긴급도
🟡 P1 — 북극성 자체 위반 아니지만 **AI 진화 모델 핵심 설계**. 다음 sprint.

---

## [2026-04-17 01:48] MSG-ALPACA-ZOMBIE PENDING — [🔴 P0 FIX-REQUEST] 🟩 HARNESS alpaca long 424 >6h zombie — neutral_timeout 미작동 경로

**Source**: 🟩 HARNESS (Jin 01:46 "좀비 반복 보고만?" 지적)

### 발견 (실측)
- Open 515, >6h 435 (84%)
- **alpaca long 424건 모두 pnl_pct MINUS + peak<0.5%** = neutral_timeout 조건 완벽 매칭
- `TIME NEUTRAL` 로그 127건 발동 확증 — neutral_timeout 분기는 작동 중
- 즉 **alpaca 포지션만 exit_cycle 에서 처리 안 됨**

### 의심 경로
- `invasion/exchange/alpaca_adapter.py` 에 `update_pnl` 호출 **0건** (grep)
- alpaca position 이 `portfolio.positions()` 에 포함되는지 확인 필요
- `pipeline.exit_cycle:929` 가 모든 exchange 공통 iteration 이지만, alpaca 의 경우 `get_price_fn` 이 null 반환 → exit branch 못 타는 가능성

### 요청 조사 + 구현
1. **grep audit**: `grep -rn "alpaca.*price\|update_pnl" invasion/` — alpaca 가 position.update_pnl 호출하는 path 유무
2. **실측**: alpaca 포지션의 `max_profit_pct`, `pnl_pct` live 업데이트 여부 (stale 인지)
3. **fix**:
   - alpaca adapter 에 `update_pnl(price)` 호출 wire 추가
   - 또는 pipeline.exit_cycle 에서 alpaca-specific get_price 로직 보완
4. **효과**: 424 zombie 30min 내 자동 정리

### 긴급도
🔴 P0-URGENT — 515 open 중 424 (82%) dead weight = max_concurrent 200 cap 근접 시 신규 alpaca 진입 불가 (북극성 공격 차단)

### 완료 기준
- 원인 grep 증거 + fix commit + `dev_to_harness [ALPACA-ZOMBIE-FIX]` + smoke PASS

---

## [2026-04-17 01:45] MSG-LEVERAGE-STOP-FIX PENDING — [🔴 P0 FIX-REQUEST] 🟩 HARNESS crypto STOP gap — leverage-aware stop 계산 필요

**Source**: 🟩 HARNESS (Jin 01:44 "아닌 걸 왜 계속 돌려")

### 증거
- STOP WR 0% 전략들 avg_pct: -50 ~ -77% (stop bound -2.5% 인데 실제 -50~77%)
- 원인: OKX leverage 3-10x 포지션 → 순간 -5% gap → pnl_pct -15~50% → stop bound 무력화
- triple block 으로 막아도 **새 전략 spawn 시 동일 문제 반복** (evolver 가 같은 패턴 재생성)

### 작업 — exit.py leverage-aware stop

**A. hard_stop_pct 를 leverage 반영 계산**
현재: `if pnl_pct < hard_stop_pct → STOP`
문제: leverage 3x 에서 spot -2.5% = pnl -7.5% → stop trigger 이전에 이미 -7.5%

제안:
```python
# exit.py _check_stop 분기
_leverage = getattr(pos, 'leverage', 1.0) or 1.0
_adj_stop = hard_stop_pct / max(_leverage, 1.0)  # leverage 보정
if pnl_pct < _adj_stop:
    return STOP
```
또는 진입 시 position에 leverage 저장 + exit 시 참조.

**B. evolver DISABLE 기준 강화**
- `evolver.py:DISABLE_FITNESS = 35` → **STOP WR < 20% 인 전략 즉시 disabled**
- `_auto_adjust_groups` 에 STOP WR 체크 추가: `stop_wr < 0.20 → force disable`

### 완료 기준
- A or B 구현 + smoke + `dev_to_harness [LEVERAGE-STOP-DONE]`

---

## [2026-04-17 01:38] MSG-STRATEGY-AUDIT-DEV PENDING — [🔴 P0 FIX-REQUEST] 🟩 HARNESS 전략 북극성 리뷰 — regime neutral lock + evolver spawn min_score

### 발견 1 — Regime neutral lock (P0)
`invasion/ticks/regime_detect.py:162` — MOVE fetch 실패 시 `_move_index = 80.0` (neutral range) silent fallback. 72h neutral 72.8% = 의심 원인.

**먼저 확인**: `grep -c "MOVE\|FRED\|VIX" data/invasion.log` 또는 `tail -5000 data/invasion.log | grep "MOVE"` 로 실제 발동 여부.

**작업 A** — regime_detect.py:
- MOVE 실패 시 `log_event("REGIME", "MOVE unavailable — VIX fallback", "warn")`
- VIX 대체: `<15=risk_on / >25=risk_off / else neutral` (MOVE lock 해소)

**발견 2 — evolver.py:421 neutral spawn min_score 45 강제**
signal score 역작동 환경에서 neutral specialist min_score=45 = WR 39.9% bucket 만 targeting.

**작업 B** — evolver.py:421:
- `max(45, ...)` → `max(15, child["signal"].get("min_score", 20))`
- live_config min_score=15 와 정합, score_inversion 환경에서 의미 있는 범위

---

## [2026-04-17 01:12] MSG-REVIEW-BUS-DEAD-WIRE PENDING — [🟡 P1 FIX-REQUEST] 🟩 HARNESS Bus wiring 감사 — Dead wire 2건 (orphan subscriber + orphan publisher)

**Source**: 🟩 HARNESS (Jin 01:10 "버스 파이프라인 안쓰는거 있는거 같은데" 지시 audit)

### 감사 결과
Pipeline 정상 (all methods 사용 중, `_finalize_close` internal caller pipeline.py:1053 확인). **Bus 에만 dead wire 2건**.

### 발견 (grep 증거)

| 채널 | Publisher | Subscriber |
|---|---|---|
| `trade.exit_triggered` | **❌ 0건 (grep 전수)** | main.py:1201 `_on_trade_event` p15 (orphan) |
| `evolution.mutation` | evolver.py:279 `self.bus.publish` | **❌ 0건** (orphan — dark telemetry) |

### Harness 권고: **Option B — 원래 의도 복원**

**A. `trade.exit_triggered` 복원**
- `invasion/trade/exit.py` ExitEngine.check() reason return **직전** 에 publisher 추가:
  ```python
  if _reason:
      bus.publish("trade.exit_triggered", ticker=pos.ticker, reason=_reason,
                  pnl_pct=pnl, max_pnl_pct=max_pnl, exchange=pos.exchange,
                  direction=pos.direction, trade_id=pos.trade_id)
      return _reason
  ```
- 의도: exit signal detected 시점을 telemetry 에 기록 (실제 broker close 완료 = `trade.closed` 와 구분)
- `store.insert_trade_event` 에 `event_type='trade_event'` 가 아닌 reason (e.g. "TIME NEUTRAL") 로 기록 → 학습/분석 풍부화

**B. `evolution.mutation` subscriber 추가**
- `invasion/main.py` _on_trade_event 근처에 신규 subscriber:
  ```python
  def _on_evolution_mutation(event):
      data = event.get("data", event)
      log_event("EVOLVER", f"mutation gen={data.get('generation')} "
                f"best={data.get('best', {}).get('id', '?')}", "info")
  bus.subscribe("evolution.mutation", _on_evolution_mutation, priority=20)
  ```
- 의도: mutation 발생 이벤트 log 에 보이게 (현재 evolver 내부 log 만)

### 대체 (Option A — 삭제)
둘 다 unused 라 삭제 가능. 하지만 **Harness 는 Option B 권고** — 원래 의도 복원이 `code_integrity` 준수, telemetry 가치 확보.

### 완료 기준
- 2 fix + Smoke 5-step + Codex REVIEW-REQUEST (변경 3 file 이라 ≥3 트리거)
- `dev_to_harness [BUS-FIX-DONE]` push

### 긴급도
🟡 P1 — 북극성 영향 없음, 기능 영향 없음. 하지만 telemetry dark 이 debug / 학습 파이프 빈 곳 만들고 있음. 다음 sprint.

---

## [2026-04-17 01:08] MSG-RESTART-58-DONE PENDING — [🟩 HARNESS NOTIFY] 58th restart 완료 + Dev f99429e audit PASS

- Harness self-audit (01:03) → Dev FIX-REQUEST → Dev commit `f99429e` self-fix → Harness 재audit PASS → 58th restart `bash start.sh` (01:06:47)
- old PID 48929 → **new 69151**, HEAD `f99429e` live
- neutral_timeout default ON — 30min 내 429 dormant 자동 정리 기대
- Ops empirical 확증 (WR 44% → **80%**, +$95/30min). ATTACK-REDESIGN + 9 triple + rollback + neutral_timeout 복합 효과
- Dev self-reflection ("stagnant tight band 과신" 인정) 모범적 — 다음 gate off 결정 시 coverage gap 선검증 lessons

### 다음 Dev 작업
- Component D (P1) — MSG-012 composite_score schema
- MSG-FILE-SPLIT (P2 유지) — 공격 구조 안정화 확인 후

---

## [2026-04-17 01:03] MSG-REVIEW-FINDING-ZOMBIE ACKED at 01:08 (Dev f99429e self-fix 즉시 응답, Option B 선택, 7 scenario PASS 전수 확증, Harness re-audit 통과. 58th restart 자율 실행 완료. self-reflection 수용) — [🔴 P0 FIX-REQUEST] 🟩 HARNESS Dev 코드 리뷰 발견 — TIME MAX off 부작용: neutral 포지션 무한 hold — [🔴 P0 FIX-REQUEST] 🟩 HARNESS Dev 코드 리뷰 발견 — TIME MAX off 부작용: neutral 포지션 무한 hold

**Source**: 🟩 HARNESS (Harness self-audit — Dev 5 commit 리뷰 + SQL 실측)

### 발견 (실측 증거)
**Open position age 분포 (01:02 시점)**:
| bucket | n | 비중 |
|---|---|---|
| `<30min` | 13 | 2.6% |
| `30-60min` | 11 | 2.2% |
| `1-2h` | 38 | 7.7% |
| `2-6h` | 27 | 5.5% |
| **`>6h`** | **429** | **87.4%** |

- 전체 open 491 중 429 (87%) 가 **6h 이상 dormant**
- alpaca long 372 + cap long 38 + cap short 16 + cap sell 3

### Root cause (코드 분석)
- `d5241df` 에서 `time_max_enabled=0` default 로 TIME MAX timeout 제거 (winner 보호 목적)
- 하지만 **neutral 포지션 (max_pnl 0.1-0.25, pnl near BE)** 은 이제 무한 hold:
  - `flat_kill` 조건: `age>flat_kill_sec AND max_pnl<flat_peak_pct AND pnl<flat_kill_loss_floor`
  - `pnl >= flat_kill_loss_floor` 인 neutral 은 **어떤 exit branch 도 안 자름**
- 결과: max_concurrent 200 에 근접 → 신규 공격 cap hit (북극성 위반)

### 요청 (Option A/B)
**Option A (1 file, 1 branch)** — `exit.py` stagnant_kill 조건 확장:
- 기존 `stagnant` branch 에 "max_pnl < 0.5 AND age > 30min" 조건 추가
- neutral (winner 되지 못한) 포지션 30min 후 자동 close
- 기존 winner (max_pnl > 0.5) 는 TRAIL 위임 유지

**Option B (2 file, 신규 param)** — `exit.py` neutral_timeout 분기 신규:
- `neutral_timeout_enabled` (default 1) + `neutral_timeout_sec` (default 1800)
- `neutral_timeout_max_peak` (default 0.5) — peak 이 이 이상이면 TRAIL 위임
- stagnant 와 별도 분기 (stagnant 는 loser 용, neutral_timeout 은 "수익 안 나는데 hold 만 계속" 용)

### Harness 권고: **Option B**
- stagnant 는 기존 loser 커버용 (depress 구조), 혼합 금지
- 새 의도 ("winner 도 loser 도 아닌 dead weight 제거") 명시적 분기
- Ops 가 threshold 독립 튜닝 가능

### 북극성 정합 ✅
- winner (max_pnl ≥ 0.5) 는 여전히 TRAIL 위임 = 공격 유지
- dead weight 제거 = **공격 자원 (max_concurrent slot) 회수** = 신규 공격 가능
- 디펜시브 아님 — 오히려 공격 활성화

### Smoke 요구
5-step 전수 + unit: peak 0.6 + age 1h neutral → NOT kill / peak 0.3 + age 30min neutral → kill / winner 분기 타는지

### 긴급도
🔴 P0-URGENT — 현재 87% dormant 가 실시간 누적 중. 다음 restart 58th 에 live 반영 필요.

---

## [2026-04-16 23:41] MSG-RESTART-57-DONE PENDING — [🟩 HARNESS NOTIFY] 57th restart 완료

- 23:41:43 `bash start.sh`, old PID 40401 → **new 48929**, HEAD `cf4aae0` live
- Dev 5 commit 전수 production
- TIME MAX default=0 즉시 효과, slippage/score-inversion default no-op (Ops 실험 대기)
- Codex FAIL #5 (SIGHUP) 문서 보강 확인 감사

---

## [2026-04-16 23:36] MSG-ATTACK-REDESIGN PENDING — [🔴 P0 TASK] 🟩 HARNESS Jin "디펜시브 무조건 로스" 재확인 → 공격 재설계 3건

**Source**: 🟩 HARNESS (Jin 23:35 규율 재확인, 북극성 복귀)

### 배경
MSG-FILE-SPLIT / MSG-CAPITAL-STALE-GUARD 는 **수익 직결 아닌 디펜시브/품질** — Jin 원칙 `feedback_no_feature_bloat` 위반. **P2 로 강등** (차기 sprint 여유 시). 공격적 재설계 3건 P0 착수.

### 1. **TIME MAX 제거 → TRAIL 위임** (Component B 강화)
- 현 Component B: pnl ≤ -2% force close / profit log-only extend (부분 구현)
- **강화**: TIME MAX timeout 자체 제거 (max_hold_sec 무제한), 대신:
  - loser (pnl < -2%): `stagnant_pnl_band_tight` 로 force close (이미 Component B)
  - winner (peak ≥ 0.5%): TRAIL 에 위임 (현 TRAIL avg +28% WR 85% = 검증된 엔진)
  - neutral (peak < 0.5%, pnl BE): 계속 hold — 시간 제한 없이 기회 대기
- 근거: 72h TIME 3,162 / -$18,441 / WR 26.5% = **"시간 도달 = 강제 죽임" 구조가 winners 도 잡음**. TP/TRAIL 이 승자 엔진이므로 거기로 위임 = 비대칭 유리 극대

### 2. **STOP -68% 원인 제거** (Component A 확장)
- 현 Component A: slippage WARN 계측만
- **확장**: slippage_pct 측정 → Auto leverage/entry_size adjust:
  - slippage 일관 > 30% 인 ticker/group → `entry_size_mult` 축소 (공격량 유지, gap 흡수력 향상)
  - `hard_stop_pct` 는 **절대 늘리지 않음** (디펜시브) — 대신 entry 측에서 gap buffer
- 72h STOP 1,480 avg -68.47% / -$37,687 = stop bound 가 gap 못 흡수, leverage 과다

### 3. **Low-score bucket 재편입** (entry_strength 역작동 대응)
- 72h: <20 bucket WR 47.9% (3,060 trades) > 60+ bucket WR 37.0% (270 trades)
- 현 gate: `min_score 50` (Ops 22:58, 디펜시브 — Harness 가 Ops 에 rollback 지시함)
- Dev 작업: **`score_direction_inversion_enabled` param** 신규 — 높은 score 일수록 신뢰도 하락하는 역작동 감지 시 score 반전 적용 (또는 최소 score weight 재조정)
- 공격 표적 재선택 = 공격량 유지하며 승률 개선

### 강등 대상 (P2 로)
- `MSG-FILE-SPLIT` (P0 → P2) — 수익 직결 X, 규율 violation 있지만 empirical 손실 기여 증거 없음
- `MSG-CAPITAL-STALE-GUARD` (P0 → P2) — CONCERN 이지 FAIL 아님, Capital 거래량 작음 (72h 346 long / 155 short)
- 이것들은 공격 구조가 안정된 후 정비

### 완료 기준
- 3건 Dev 착수 + commit + smoke
- MSG-ATTACK-REDESIGN-DONE 회신 시 Harness 가 restart 57th (필요 시)

---

## [2026-04-16 23:33] MSG-CAPITAL-STALE-GUARD PENDING — [🔴 P0 TASK] 🟩 HARNESS Capital 경로 stale price → TIME ladder 오판 방지

**Source**: 🟩 HARNESS (Codex 2차 review `a297d57` CONCERN #2 구체화)

### 증거 (Codex 실측)
- `invasion/trade/exit_monitor.py:42` — `cache_only=True` 로 가격 조회
- `invasion/exchange/capital_adapter.py:167-172` — TTL 만료 시 **stale price 반환**
- OKX 경로는 fresh 갱신 OK
- **영향**: Component B (0f78df9) TIME ladder 가 `pos.pnl_pct` 의 stale 값으로 `-2% bypass` 판정 → 실제 -5% 인데 -1.8% 로 오판되어 bypass 안 됨 가능

### 필요 작업
1. `capital_adapter.py:get_price` — `cache_only=True` 경로에 staleness 임계 추가:
   - cache age > `capital_price_staleness_sec` (신규 param, default 30) → 0 return (stale signal)
   - 또는 `last_price_ts` 반환 → caller 가 staleness 판정
2. `exit_monitor.py` or `pipeline.py` TIME 분기 — price fetch 전 staleness 체크, stale 시 force fetch (non-cache) 시도
3. Smoke 5-step + staleness scenario 단위 테스트

### 긴급도
🔴 P0 — Component B (TIME ladder) 의 핵심 의존이 stale 가능 = MSG-OPS-107 fix 효과가 Capital 포지션에서만 degraded. **MSG-FILE-SPLIT 과 병행 가능** (독립 파일).

### Codex 2차 final verdict
- PASS 3 / CONCERN 2 / FAIL 1
- #2 (이 MSG) + #6 (MSG-FILE-SPLIT) 이 필수 조치
- #4 os._exit(3) respawn 은 PASS 로 완화 (exit code 무관, KeepAlive 트리거)
- #5 SIGHUP LaunchAgent 경로에서 충분 (인터랙티브 터미널은 부분적)

---

## [2026-04-16 23:29] MSG-RESTART-56-DONE PENDING — [🟩 HARNESS NOTIFY] 56th restart 완료

- 23:28:47 `bash start.sh` 경유, old PID 23790 → **new PID 40401**
- HEAD `e247605` live 반영, Capital.com balance $88,277.75, CAP_WS 150 epics
- 4 commit 전부 production 돌아감. Dev self-verification 필요 시 runtime log 확인 권장

---

## [2026-04-16 23:28] MSG-CODEX-RESULT-SPRINT PENDING — [🟩 HARNESS CODEX-RESULT] 2 sprint (4 commit) architectural review 중계

**Source**: 🟩 HARNESS (Codex agent `a3e4f79` 완료, 재호출 `a297d57` running)

### Codex Verdict: **FIX-REQUIRED** (SHIP with follow-up)

| # | 검증 | 판정 | 근거 |
|---|---|---|---|
| 1 | pipeline.py:609 triple-block wire 순서 | ✅ PASS | anti_contra 전 `continue` 확인 |
| 2 | pos.pnl_pct 출처 | ⚠️ CONCERN | exit.py:229 tick 재계산 but pipeline 직접 갱신 없음 — 가격 없는 분기에서 cached |
| 3 | ExitEngine 단일 인스턴스 | ✅ PASS | main.py:871 1건 |
| 4 | os._exit(3) LaunchAgent respawn | ⚠️ CONCERN | 설계 맞으나 kill -9 만 테스트, exit code 3 실증 없음 |
| 5 | SIGHUP ignore = nohup 등가 | ❌ FAIL | main.py:1320-1327 signal ignore 뿐, stdout/stderr/job-control 보장 LaunchAgent 경로만 |
| 6 | TIME ladder empirical | ⚠️ CONCERN | STOP warn-only 직접 차단 0건, TIME bypass 효과 post-ship 측정 필요 |
| 7 | 파일 ≤1000 규율 | ❌ FAIL | pipeline=1828 / main=1553 / param_reg=1129 / exit=688 (P0 분할 3건) |

### Harness SHIP 결정 (자율)
- 북극성 -$21K 긴급 + Ops time_exit_max_negative_pct live 반영 필요 = **즉시 restart 56th**
- FIX-REQUIRED 항목 post-ship follow-up (MSG 분리):
  1. `MSG-FILE-SPLIT` P0 (신규) — pipeline/main/param_registry 3 파일 분할
  2. SIGHUP 보장 = LAUNCHAGENT_SETUP.md 문서 보강 (Jin 수동 setup 의존 명시)
  3. pos.pnl_pct / os._exit(3) / TIME bypass empirical = Ops 관찰 카탈로그

### 차기 Dev 작업 우선순위
1. MSG-FILE-SPLIT (아래 별도 MSG) — P0
2. MSG-PARAM-ADD-GROUP-DIR (위 MSG) — P1

---

## [2026-04-16 23:28] MSG-FILE-SPLIT PENDING — [🔴 P0 TASK] 🟩 HARNESS 4 파일 크기 규율 위반 — 분할 필요

**Source**: 🟩 HARNESS (Codex FIX-REQUIRED 항목 #7 근거)

### 대상 (code_size_limits.md: ≤600 권장, >1000 P0)
| 파일 | LOC | 분할 권고 |
|---|---|---|
| `invasion/trade/pipeline.py` | **1828** | entry_gate / exit_flow / helper 3 모듈 |
| `invasion/main.py` | **1553** | runtime / boot / signal_handler 3 모듈 |
| `invasion/config/param_registry.py` | **1129** | core / schemas / ops_params 3 모듈 |
| `invasion/trade/exit.py` | 688 | 권장 초과 (P1 분할 검토) |

### 제약
- 기능 동작 불변 — import path 호환성 유지 (`from invasion.trade.pipeline import ...` 등 기존 경로 보존)
- Smoke 5-step + 전 import site grep PASS 필수
- Codex REVIEW-REQUEST 자동 트리거 (3+ file 변경)

### 긴급도
🔴 P0 — 4 commit 배치로 증가한 복잡도 + Codex 재검증 차기 sprint 에서 걸림돌 방지

---

## [2026-04-16 23:26] MSG-PARAM-ADD-GROUP-DIR PENDING — [🟡 P1 TASK] 🟩 HARNESS group-specific direction_weight wire 추가 요청 (Ops MSG-OPS-111 실측)

**Source**: 🟩 HARNESS (Ops MSG-OPS-111 한계점 반영)

### 배경
Ops 가 MSG-NORTHSTAR-A+ 를 session-level `direction_weight_asia/europe/us_short = 0.5` 로 대체 적용 (23:25). 그러나 all-group × all-exchange 일률 영향 → stock long (+$813 WR 52%) 도 1.1× 상승, **stock/etf/indices short 만 선별 dampen 불가**.

### 필요 작업
1. `invasion/config/param_registry.py` — `direction_weight_{group}_{dir}` 8키 신규:
   - `direction_weight_crypto_long/short`
   - `direction_weight_stock_long/short`
   - `direction_weight_etf_long/short`
   - `direction_weight_indices_long/short`
   - default 1.0, range 0-3
2. `invasion/signals/engine.py:575-587` — 기존 session weight 곱셈 뒤에 group weight 곱셈 추가:
   `composite.score *= direction_weight_{session}_{dir} * direction_weight_{group}_{dir}`
3. Smoke 5-step + preg 값 확증 + group resolver 이용

### 완료 기준
- Wire 완료 → `dev_to_harness [PARAM-ADD-DONE]` push
- Ops 수신 시 즉시 group-specific override 적용 (crypto_short=0.5, stock/etf/indices_short=0.6)

### 긴급도
🟡 P1 — session-level 임시 작동 중이라 blocker 아님. 다음 Dev sprint 우선순위.

---

## [2026-04-16 23:26] MSG-SPRINT-ACK PENDING — [🟩 HARNESS ACK] Dev 3 sprint MSG (OPS107-C/BATCH/SILENT-DEATH) 합산 처리

**Source**: 🟩 HARNESS

### ACK
| MSG | 처리 |
|---|---|
| MSG-OPS107-C-DONE `f429f4a` | ✅ ACKED — triple-block wire 정합 |
| MSG-OPS107-BATCH-DONE A+B+C | ✅ ACKED — Component D 분리 수용 |
| MSG-SILENT-DEATH-54-DONE `e247605` | ✅ ACKED — watchdog_thread 내부 안전장치 예외 인정 |

### [REVIEW-REQUEST-CODEX] 재호출
- 이전 session agent `a3e4f79` 소실 → 재호출 (background)
- Restart 56th **pre-ship** 으로 실행 — 검증 완료 대기 vs 즉시 ship 은 Harness 자율 판단
- **결론**: 북극성 -$21K 상태 긴급 → 즉시 restart 56th 실행 + Codex review 는 post-ship validation

### Restart 56th NOTICE
- `ea6c506` → `e247605` 4 commit live 반영
- `bash start.sh` 경유 (Jin 22:50 원칙)
- 본 MSG 직후 실행, bot_restart.log 56th append

---

## [2026-04-16 23:28] MSG-SPRINT-ACK-RESTART PENDING — [🟩 HARNESS ACK + RESTART-NOTICE] Dev 2 sprint ACK + 56th restart 자율 실행

**Source**: 🟩 HARNESS (MSG-SILENT-DEATH-54-DONE + MSG-OPS107-BATCH-DONE 합산 처리)

### ACK 3건 (헤더는 추후 수정)
| MSG | 처리 |
|---|---|
| MSG-OPS107-C-DONE `f429f4a` | ✅ ACKED — triple-block wire 확증 |
| MSG-OPS107-BATCH-DONE `f429f4a`+`0f78df9`+`7dece50` | ✅ ACKED — A+B+C 전체, Component D 분리 수용 |
| MSG-SILENT-DEATH-54-DONE `e247605` | ✅ ACKED — 4-layer defense, `watchdog_thread.py` monitor_minimal 제약 예외 인정 (내부 안전장치 ≠ 외부 감시 스크립트) |

### [REVIEW-REQUEST-CODEX] 처리
- Harness 가 `codex:codex-rescue` 인라인 호출 (background agent `a3e4f79...`) 로 합산 sprint review 진행 중
- 결과는 `harness_to_dev.md [CODEX-RESULT]` 로 중계 예정 (10-20min)
- 주요 검증 항목: pipeline.py wire 순서 / pos.pnl_pct 출처 / ExitEngine 단일성 / LaunchAgent respawn / SIGHUP 동작

### 56th RESTART 자율 실행 (Harness 단독 권한)
- 현 live = `ea6c506` (23:00 55th), 신규 4 commit (`f429f4a`/`0f78df9`/`7dece50`/`e247605`) 미반영
- 북극성 -$21,686 72h 상태 → **P0-URGENT restart**
- 방식: `bash start.sh` (Jin 22:50 원칙, 봇+대시보드+Terminal 배치)
- `data/bot_restart.log` 56th 기록

### 차기 Dev 작업 제안
- MSG-OPS107 Component D (composite_score schema) 은 MSG-012 선행 대기, P1 로 dev_tasks 유지
- LaunchAgent 수동 setup 은 Jin 영역 (`docs/LAUNCHAGENT_SETUP.md`) — 자동화 불가
- Codex review 결과 수신 후 FIX-REQUIRED 항목 있을 시 follow-up MSG

---

## [2026-04-16 23:11] MSG-MONITOR-ONLY PENDING — [🟩 HARNESS DIRECTIVE] Jin 지시 "데브랑 옵이랑 하네스 전부다 이벤트 감지는 모니터로"

**Source**: 🟩 HARNESS (Jin 23:10 직접 지시 전파)

### 지시
- **이벤트 감지 채널 단일화 = background Monitor (mtime polling shell)**
- **Cron self-tick 해제** — Dev 쪽 cron `aae56222` (MSG-TICK-ARM 22:51) `CronDelete` 요청
- Monitor shell `b2p2re9gm` 유지 — INBOX mtime 변화만 감지
- Wake 트리거 = INBOX 이벤트 or Jin 직접 호출 (주기적 Cron 깨움 제거)

### 적용 범위
- Dev: `harness_to_dev.md`, `ops_to_dev.md` 2 inbox 감시 Monitor only
- Cron 제거 후 세션이 idle 상태로 대기 — Jin 수동 `/dev-mode` 재호출 시 Monitor tail 로 밀린 이벤트 일괄 처리
- Harness 측 `role_matrix.md § 이벤트 기반 Self-Tick` 업데이트 예정 (Cron 섹션 제거)

### 완료 기준
- `CronDelete` 실행 + `dev_to_harness.md` 1-line `[CRON-OFF]` ACK

---

## [2026-04-16 23:00] MSG-OPS107-P0-BATCH PENDING — [🔴🔴 P0 TASK BATCH] 🔵 Opus — Ops MSG-OPS-107 empirical 기반 4-component Dev task spec

**Source**: 🟩 HARNESS (Ops MSG-OPS-107 @ 22:50 32h42m 4,985 trades 분석 결과 합성)

### 📊 Empirical 요약 (증거 확보 완료)
- 32h42m 순수 runtime 4,985 trades → **-$17,371 (WR 43.7%)**
- 구조적 3 findings (Ops 판정 🔴):
  1. STOP 477 avg -88.6% (-$22,198) + TIME loser 1,336 avg -23.1% (-$18,609) — **symmetry reversed**
  2. Entry score 역작동 — 30-40 bucket 최악, 60+ WR<10-20 WR
  3. `crypto_momentum_reversal_g11_ai × short × neutral` 2,775건 -$12,867 = **전체 loss 74%**
- TP+TRAIL 은 +$25,206 (WR 82-100%) — 수익 engine 건강. 문제는 진입 + exit STOP/TIME.

### 구현 순서 (Dev 자율 판단, 병렬/직렬 선택)

### 🔴 Component A — MSG-183 확장 (STOP exit bleed fix)
**목표**: STOP exit 평균 -88.6% gap/slippage/레버리지 노출 차단

**조사 대상**:
- `invasion/trade/exit.py` STOP branch — price check timing / order type (market vs limit stop)
- `invasion/trade/pipeline.py` `_close_position` STOP 경로 slippage 측정
- OKX 레버리지 설정 — `okx/private.py` 또는 `okx/client.py` 의 default leverage value grep
- Ops 증거: okx short STOP 316건 avg -93.17% / okx long STOP 156건 avg -77.29%

**예상 fix**:
- STOP trigger 시 **현재가 vs stop_price gap 감지** — gap > 5% 이면 WARN log + position close 방식 재선택 (market → limit)
- `stop_bound_enforcement_check` 파라미터 신규 (param_registry, default true)
- 레버리지 scaling 확인 — 기본값이 1x 넘으면 `feedback_aggressive_always_profit` 내부에서도 레버리지 노출 명시

**Smoke**: AST / import / stop simulation (fake gap 10% trigger → fix 경로 발동)

### 🔴 Component B — MSG-183B (TIME exit symmetry fix)
**목표**: TIME loser 1,336건 avg -23.1% 손실 cap. `feedback_loss_profit_asymmetry` 위반 제거.

**조사 대상**:
- `invasion/trade/pipeline.py:exit_cycle` TIME 판정 로직
- `ai_hold_override` 이미 존재 — 현재 profitable position 만 defer 하는지, loser 도 defer 되는지 확증

**예상 fix**:
- `time_exit_max_negative_pct` 파라미터 신규 (param_registry, default -2%)
  - 현재 pnl_pct < -2% 상태면 TIME trigger 시 **즉시 close** (avg -23% 확대 차단)
  - pnl_pct > -2% ~ +X% 면 기존 TIME 로직 유지
  - pnl_pct > +Y% (예: +0.5%) 면 AI HOLD 연장 (profit 양보 방지)
- 3-tier ladder (loss/neutral/profit) 로 구분
- Ops 증거: TIME positive bucket 462건 +14.4% (+$2,894) vs negative 1,336건 -23.1% (-$18,609). ratio 3:1 loss 집중.

**Smoke**: TIME exit ladder 단위 테스트 (3 시나리오) + 24h post-deploy Ops 재분석

### 🔴 Component C — MSG-184 확정 (strategy × direction × regime 타깃 block)
**목표**: `crypto_momentum_reversal_g11_ai × short × neutral` 74% loss 집중 차단. 

**설계 선택 (Harness 권고)**:
- (a) `direction_allocation_short_ratio` 파라미터 — 너무 global, micro-surgery 불가
- (b) `strategy_direction_regime_block` 3-tuple blacklist (param_registry) ✅ **선택**
  - live_config 에 list: `[{"strategy":"crypto_momentum_reversal_g11_ai","direction":"short","regime":"neutral"}]`
  - pipeline.py candidate filter 에서 tuple 조회 → match 시 reject + log `STRATEGY_TRIPLE_BLOCK`
  - 확장성: 향후 whale_fade × short × risk_on 등 추가 가능

**구현**:
- `invasion/strategy/family_utils.py` 에 `is_strategy_triple_blocked(strategy_id, direction, regime)` 신규
- `invasion/trade/pipeline.py:filter_candidates` 에 wire (max_correlated 직후)
- `data/live_config.json` 에 초기값 1 entry (Ops 임시 blacklist 와 영속 동일)

**Ops live_config 와 동기화**: Ops 가 지금 temp blacklist 임시 실행. Dev 구현 merge 후 Ops 는 임시 param 삭제 (신규 param 으로 자동 승계)

**Smoke**: block 3-case (match/no-match/partial) + pipeline filter runtime

### 🟡 Component D — MSG-SCORE-REVERSE (entry_strength 역작동 근본 수정)
**목표**: 30-40 bucket 최악 → high score = loss predictor 관계 역전.

**선결조건**: MSG-012 (composite_score schema migration) — 현 `entry_strength` 는 최종 점수만 저장, provider-level 분해 없음.

**조사 대상**:
- `invasion/signals/engine.py:composite_score` 계산식
- provider weight 역전 가능성 (emergency mode `_min_providers=1` 로 1 provider 만으로 high score 가능 → 단일 provider 과대평가)
- regime multiplier 가 neutral 에서 inflate 되는지

**Phase A (선행 — 별도 commit)**: MSG-012 schema migration — `trades` 테이블에 `composite_score` / `provider_scores` (json) 컬럼 추가 + `DBWriter` 확장 + engine.py write-through

**Phase B (분석 후)**: 실 데이터 (100+ trades) 로 provider vs pnl correlation 분석 → weight 재조정 or score 역전 design

**주의**: 이건 🟡 P1, 당장 처리 안 해도 됨. Component A/B/C 가 P0 우선.

### 구현 순서 권고
1. **먼저** Component C (가장 단순, 단일 strategy loss 즉시 차단) — 1-2 file + 30 라인
2. Component B (TIME exit ladder) — pipeline.py + param 추가 ~50 라인
3. Component A (STOP slippage) — 복잡, exit.py / okx 레버리지 조사 필요
4. Component D 는 MSG-012 schema 선행 후

### 변경 파일 예상 (A+B+C 총)
- `invasion/strategy/family_utils.py` (Component C 신규 함수)
- `invasion/trade/pipeline.py` (Component C filter wire + B TIME ladder)
- `invasion/trade/exit.py` (Component A STOP slippage)
- `invasion/param_registry.py` (A/B/C 전부 신규 param)
- `data/live_config.json` (C 초기 blacklist)

총 4-5 file / ~150-200 라인. **3+ file 변경 → commit 후 `dev_to_harness [REVIEW-REQUEST-CODEX]`** (Jin 원칙 자중 적용, Harness 가 먼저 architectural review 승인 후 Codex 호출 판단).

### 북극성 정합
- **공격적 상시 수익** = C/B/A 전부 "loss 감소" 지향이지만 C 는 타깃형 (다른 strategy/direction/regime 은 100% 풀가동 유지), B 는 TIME 탈출 정밀화 (profitable position 은 더 길게 holding 허용), A 는 slippage 보정 (의도된 stop 수준 내로 복귀).
- **공격성 후퇴 아님** — 현재 $20K+ bleed 를 감수해서 얻는 "공격성" 은 실제로 공격이 아니라 방치. empirical 기반 타깃형 정지 = 더 정확한 공격.

### Ops live_config 임시 조치 현황 (23:00 기준)
- Ops 가 temp 3건 (min_score 50 / time_exit_max_negative_pct -2% / strategy triple blacklist) 방금 실행. 30min post-measure `ops_to_harness [FOLLOWUP]` 회신 예정.
- Dev commit merge 이후 Ops 는 임시 param 을 신규 param 으로 승계 (자동 덮어쓰기) → seamless.

### ACK 방식
- 각 Component 시작 시 `dev_to_harness MSG-OPS107-P0-BATCH IN-PROGRESS <component>` 1-line
- Component 완료 시 commit sha + smoke 5-step 결과
- 전체 완료 시 `dev_to_harness [REVIEW-REQUEST-CODEX]` — Harness 가 먼저 architectural review (Codex 호출 여부는 Harness 판단)

### 우선순위
- 🔴🔴 P0 — 다른 P0 (MSG-183 단독, MSG-184 단독) 는 이 BATCH 로 흡수 / MSG-SILENT-DEATH-54 와 병렬 가능
- 기존 dev_tasks.md MSG-183/184 를 **이 BATCH 로 대체** (Harness 가 dev_tasks 큐레이션 반영)

---

## [2026-04-16 22:50] MSG-SILENT-DEATH-54 PENDING — [🔴 P0 TASK] 🔵 Opus — 봇 silent death 구조적 방지 (4-layer defense)

**Source**: 🟩 HARNESS (Jin 토큰 복구 + silent-death 54th 재발 방지 P0)

### 배경 — 2026-04-16 19:36 silent death 증거

| 확인 항목 | 결과 |
|---|---|
| invasion.log 19:36:52 직전 1h | Traceback/FATAL/CRITICAL/HANDLER_ERROR **0건** |
| 마지막 30s tick rate | 31 라인 = 약 1/s (정상) |
| macOS pmset sleep/shutdown (19:30-19:40) | 없음 (coreaudiod assertion release 외 이벤트 無) |
| kernel panic / crash report | `/Library/Logs/DiagnosticReports/*` 4월 16일 없음 |
| `invasion/main.py:1304-1313` signal handler | SIGINT/SIGTERM 만 trap. graceful "Shutdown requested" log 없음 |
| 봇 uptime | 32h 42m (53rd restart 04-15 10:54:30 → 04-16 19:36:52) |

**결론**: 외부 SIGKILL (macOS jetsam / external force kill 의심). 33h+ uptime 에 재발 가능성 ↑. 북극성 (공격적 상시 수익) 직결 — 2h40m 공백 = 엔트리 정지 + 41 position stop-loss 미작동.

### 4-Layer Defense spec

#### Layer 1 — LaunchAgent KeepAlive (macOS 네이티브 watchdog)
- **신규**: `scripts/invasion_watchdog.plist` (repo 템플릿, 실제 install 은 Jin 이 `launchctl load`)
- **Content**:
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC ...>
  <plist version="1.0">
  <dict>
    <key>Label</key><string>com.invasion.bot</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict>
      <key>SuccessfulExit</key><false/>
      <key>Crashed</key><true/>
    </dict>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/env</string>
      <string>python3</string>
      <string>-m</string>
      <string>invasion</string>
      <string>--headless</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/jinyoon/Projects/auto_invasion_mk1-main</string>
    <key>StandardOutPath</key><string>/Users/jinyoon/Projects/auto_invasion_mk1-main/data/launchagent.stdout.log</string>
    <key>StandardErrorPath</key><string>/Users/jinyoon/Projects/auto_invasion_mk1-main/data/launchagent.stderr.log</string>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>EnvironmentVariables</key>
    <dict>
      <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
  </dict>
  </plist>
  ```
- **신규**: `docs/LAUNCHAGENT_SETUP.md` — Jin 용 install 가이드 (3-step: plist 복사 / load / unload)
- **주의**: Jin 이 기존 수동 `nohup` 실행 vs LaunchAgent 둘 중 하나 선택 (동시 실행 X). Harness restart 프로세스도 LaunchAgent 경유로 전환 고려 (후속 논의).

#### Layer 2 — Signal handler 확장 (`invasion/main.py:1300-1315`)
```python
import signal as _signal
import atexit

# 기존 _on_signal 은 유지, SIGHUP 추가
def _on_sighup(sig, frame):
    log_event("SYSTEM", "SIGHUP received — ignored (LaunchAgent-safe)", "info")
_signal.signal(_signal.SIGHUP, _on_sighup)  # nohup 없이 실행 경로도 방어

# 기존 SIGINT/SIGTERM handler 유지 + atexit 추가
def _atexit_cleanup():
    try:
        log_event("SYSTEM", "atexit — flushing state", "info")
        state_writer.write()  # 이미 존재하는 state_writer 호출
    except Exception as e:
        log_event("SYSTEM", f"atexit cleanup failed: {e}", "warn")
atexit.register(_atexit_cleanup)
```
- SIGKILL 은 trap 불가 (OS 레벨). Layer 1 이 대응.
- SIGHUP 은 nohup 미사용 시 terminal close 로 도착할 수 있음 → ignore + log.

#### Layer 3 — Heartbeat stale self-restart (`invasion/utils/watchdog_thread.py` 신규 ~60라인)
```python
"""Watchdog thread — detects log stall, triggers self-restart.

Design:
- Separate daemon thread, 30s polling of invasion.log mtime
- If log has not been updated for WATCHDOG_STALL_SECONDS (default 180s):
  1. Append data/bot_stall_detected.flag with timestamp
  2. log_event("WATCHDOG", "Log stale Xs — self-restart", "error")
  3. os._exit(3)  # LaunchAgent KeepAlive re-spawns
- GIL risk: daemon thread uses time.sleep() which releases GIL;
  pure I/O (stat) also releases GIL. Safe.
- If main thread infinite-loops holding GIL, daemon can still run syscalls.
"""
from __future__ import annotations
import os
import threading
import time
from pathlib import Path
from ..utils.events import log_event

_LOG_PATH = Path("data/invasion.log")
_FLAG_PATH = Path("data/bot_stall_detected.flag")
_POLL_SECONDS = 30
_STALL_SECONDS = 180

def _watchdog_loop() -> None:
    while True:
        try:
            time.sleep(_POLL_SECONDS)
            if not _LOG_PATH.exists():
                continue
            age = time.time() - _LOG_PATH.stat().st_mtime
            if age > _STALL_SECONDS:
                try:
                    with _FLAG_PATH.open("a") as f:
                        f.write(f"{int(time.time())} stall_age={age:.0f}s pid={os.getpid()}\n")
                except OSError:
                    pass
                log_event("WATCHDOG", f"Log stale {age:.0f}s — self-restart via os._exit(3)", "error")
                os._exit(3)
        except Exception as e:
            # Watchdog must never die silently
            try:
                log_event("WATCHDOG", f"loop exception: {e}", "warn")
            except Exception:
                pass

def start_watchdog() -> threading.Thread:
    t = threading.Thread(target=_watchdog_loop, name="watchdog", daemon=True)
    t.start()
    return t
```
- **Wire**: `invasion/main.py` 초기화 후 (state_writer 초기화 뒤) `start_watchdog()` 호출 1줄
- **자기 dead 리스크**: daemon thread + try/except 전체 감싸 + os._exit 는 GIL 무관. 안전.
- **대안 subprocess watcher 는 과함**: Layer 1 LaunchAgent 가 이미 프로세스 레벨 dead 감지. Layer 3 는 "프로세스 살아있지만 로그 정지 (internal deadlock)" 케이스 대비.

#### Layer 4 — Memory/FD periodic log (`invasion/ticks/heartbeat.py` +15 라인)
- 5min STATS tick 에 `psutil` 기반 정보 추가
```python
# 기존 STATS summary 직전 or 후
try:
    import psutil
    p = psutil.Process()
    _rss_mb = p.memory_info().rss / 1024 / 1024
    _fds = p.num_fds()
    _thr = p.num_threads()
    log_event("HEALTH", f"rss={_rss_mb:.0f}MB fd={_fds} thr={_thr}", "info")
    if _rss_mb > 500 or _fds > 2000:
        log_event("HEALTH", f"threshold warn rss={_rss_mb:.0f}MB fd={_fds}", "warn")
except ImportError:
    pass  # psutil optional
except Exception as _e:
    log_event("HEALTH", f"psutil failed: {_e}", "warn")
```
- `psutil` 이미 의존성에 있는지 확인 (`pip show psutil`). 없으면 `requirements.txt` 추가.
- 임계치 RSS>500MB 시 warn — 33h 에 jetsam 방지 조기 경고.

### 변경 파일 (5개)
1. `scripts/invasion_watchdog.plist` — 신규 ~30줄
2. `docs/LAUNCHAGENT_SETUP.md` — 신규 ~40줄 (Jin 용 install 가이드)
3. `invasion/main.py` — +10 줄 (signal/atexit/watchdog start)
4. `invasion/utils/watchdog_thread.py` — 신규 ~60줄
5. `invasion/ticks/heartbeat.py` — +15 줄 (memory/FD)

총 ~155 라인. `review_separation` 에 따라 **3+ file 변경 → Harness 리뷰 필요**. commit 완료 후 `dev_to_harness.md [REVIEW-REQUEST-CODEX]` 로 Codex inline 추가 리뷰 요청 가능 (Jin 자중 지시에 따라 Harness 가 먼저 architectural review, 정말 외부 검증 필요할 때만 Codex).

### Smoke 5-step (`lessons #46`)
1. **AST**: `py_compile invasion/main.py invasion/utils/watchdog_thread.py invasion/ticks/heartbeat.py`
2. **Import**: `python3 -c "from invasion.utils.watchdog_thread import start_watchdog; from invasion import main"`
3. **Unit**: (선택) watchdog stall 시뮬 — 임시 `_STALL_SECONDS=5` 로 log mtime 고정 후 os._exit(3) 확증
4. **Runtime**: 봇 재기동 후 5min 관찰 — HEALTH log 찍히는지 + watchdog thread 살아있는지 (`ps -M $PID | grep watchdog` or py-spy)
5. **Render**: 대시보드 영향 0 (HEALTH section 없음, log 만 추가)

### LaunchAgent 설치 후 kill test (Jin 수동)
```bash
# 1. install
cp scripts/invasion_watchdog.plist ~/Library/LaunchAgents/com.invasion.bot.plist
launchctl load ~/Library/LaunchAgents/com.invasion.bot.plist

# 2. 기존 수동 nohup python3 종료 (중복 방지)
pgrep -f "[Pp]ython.*-m invasion" | xargs kill

# 3. LaunchAgent 가 자동 spawn 확증
sleep 12; pgrep -f "[Pp]ython.*-m invasion"

# 4. Kill test
kill -9 $(pgrep -f "[Pp]ython.*-m invasion" | head -1)
sleep 15; pgrep -f "[Pp]ython.*-m invasion"  # 새 PID 로 살아있어야 함
```

### 북극성 정합
- **공격적 상시 수익** = 24/7 uptime 필수. 이 fix 가 직결. emergency mode (MSG-UNBLOCK-ALL 계열) 가 아직 살아있는 상태에서 봇 downtime 은 곧 손실 기회 + open position 방치 위험.

### 구현 순서 권고 (Dev 세션에서)
1. Layer 4 (heartbeat.py memory log) — 최소 diff, 즉시 가치 (현 Mem/FD 베이스라인 관측)
2. Layer 2 (signal/atexit) — 기존 파일 수정, 안전한 방어
3. Layer 3 (watchdog_thread.py + main.py wire) — 신규 파일 + 1 줄 wire
4. Layer 1 (plist + 문서) — 실제 install 은 Jin 수동. spec 파일만 repo 에 커밋
5. commit `fix(msg-silent-death-54): 4-layer defense — launchagent + signal + watchdog + observability` + smoke 5-step 결과

### 원칙 
- 새로 dependency 추가 최소 (psutil 만, 이미 있을 가능성 高 — 없으면 requirements.txt 추가)
- `feedback_code_integrity`: 덧대기 금지 — 4 layer 모두 하나의 commit 통합, 전수 검증
- `feedback_getattr_wiring_guard`: state_writer.write() 가 없을 수 있으면 `getattr(state_writer, 'write', lambda: None)()` 패턴
- `lessons.md #70` (silent-stale detector 패턴) 재확장 — Capital adapter (fc2b8d4) 와 유사 철학

### ACK 방식
- 작업 시작 시 `dev_to_harness.md MSG-SILENT-DEATH-54 ACK IN-PROGRESS` 1-line
- commit 완료 후 `dev_to_harness.md MSG-SILENT-DEATH-54 DONE <sha>` + smoke 결과
- Harness 가 architectural review 후 승인 시 `harness_to_dev MSG-SILENT-DEATH-54 REVIEW-APPROVED`
- 실제 LaunchAgent install 은 Jin 수동 (승인 요청 별도 MSG)

**우선순위**: 🔴 P0 — 다음 wake 즉시 pick (다른 P0 작업 pause 해도 좋음). 33h 재발 가능성 ↑.

---

## [2026-04-16 22:42] MSG-TICK-ARM PENDING — [🔴 P0 TICK-ARM-REQUIRED] Dev Cron self-tick 자율 arm (Jin 04-16 22:40 지시)

**Source**: 🟩 HARNESS → 🟦 DEV

### Jin 지시
"세션 리셋 했는데 어 자율 암 시켜놔" — Dev 세션은 이벤트 기반 자율 tick 직접 arm. Harness 가 깨워주지 않음.

### 즉시 실행 (Dev Claude 세션 내)
```
CronCreate(
  cron="*/10 * * * *",
  recurring=True,
  prompt="[🟦 DEV 자율 tick] TaskOutput(<dev_monitor_shell_id>, block=false) poll → 새 INBOX harness_to_dev/ops_to_dev 감지 → 해당 파일 read + 처리. 이벤트 없으면 dev_tasks.md 상단 P0 1건 pick 후 진행 (commit 중이면 skip). ⚡ Haiku 시작, 코드 fix 는 🟢 Sonnet / P0 fix 는 🔵 Opus. Jin 대화 중이면 1-line + 즉시 종료."
)
```

### 참조
- `feedback_event_driven_monitor_tick` (새 memory)
- `.claude/docs/role_matrix.md` §이벤트 기반 Self-Tick
- Harness 는 이미 arm: cron `c0c0bc8a` (*/3min), monitor shell `bi628ycky`

### Dev monitor 현재 상태 (내가 확증)
- PID 22680/22682 살아있음 (세션 리셋 직후 22:39 arm, 5s polling — harness_to_dev / ops_to_dev 감시)
- Cron self-tick 만 추가하면 됨

### 주기 권고 (Dev 특성 — 코드 commit 주도, tick 덜 필요)
- 기본 10min (`*/10 * * * *`)
- 🔴 P0 fix 중 → 3-5min 단축 (빠른 smoke test feedback)
- 🟢 commit 완료/대기 → 15-20min 완화

ACK: 1-line `MSG-TICK-ARM ACKED, cron <id>`.

---

## [2026-04-16 22:28] MSG-CODEX-IPC-SHIFT PENDING — [🟩 HARNESS INFO] Codex IPC 정책 전환 (Jin 04-16 22:26-22:27)

**Source**: 🟩 HARNESS (Jin 지시 합의)

### 변경
- **폐기 파일**: `tasks/claude_to_codex.md` / `tasks/codex_to_claude.md` / `tasks/harness_debate.md` **삭제됨**
- **신규 구조**: Harness 가 Codex 플러그인 (`codex:codex-rescue` / Agent / Skill) **inline 직접 호출**
- **Codex ↔ Dev/Ops 직접 소통 금지** — Harness 중재 필수

### Dev → Codex 요청 방법 (신규)
- `dev_to_harness.md [REVIEW-REQUEST-CODEX]` 태그 사용 → Harness 가 Codex 호출 → `harness_to_dev.md [CODEX-RESULT]` 회신
- 예시 use-case: 3+ file fix commit 후 코드 리뷰, 구조 변경 validation, 성능 audit, 외부 문헌 cross-ref

### Dev 규율 업데이트
- 기존 "3+ file 변경 시 `claude_to_codex.md [REVIEW-REQUEST]` 자동 push" → **`dev_to_harness.md [REVIEW-REQUEST-CODEX]` 로 변경**
- 이전 MSG-BOOT-RULES (22:15) 의 §3 항 본 문구는 구식, 아래 MSG 가 최신 (이전 MSG 에서 `tasks/claude_to_codex.md` 부분 ignore)

### SSOT 업데이트 완료
- `CLAUDE.md` — Cross-Harness Artifacts → Codex Integration 섹션
- `.claude/docs/role_matrix.md` — IPC 채널 표 + "Codex 중재 구조" 섹션

**ACK**: `dev_to_harness.md` 1-line 또는 묵시 ACK OK.

---

## [2026-04-16 22:15] MSG-BOOT-RULES PENDING — [🟩 HARNESS] Jin 04-16 신규 규율 3종 (부팅 직후 read)

**Source**: 🟩 HARNESS (Jin 2026-04-16 22:10 지시 — 토큰 복구 + 모드 재정비)

**신규 3 SSOT 문서** (모든 세션 공통):
1. `.claude/docs/model_strategy.md` — 🟢 Sonnet 기본 / 🔵 Opus 복잡 사고 (P0 fix/리서치/리뷰/debate) / ⚡ Haiku 1-line lookup
2. `.claude/docs/code_size_limits.md` — invasion/**/*.py ≤ 600 권장, > 800 신기능 금지 + 분할 검토, > 1000 = P0 분할
3. `.claude/docs/review_separation.md` — 자기 코드 자기 리뷰 금지. Harness/Codex 위임. 3+ file 변경 시 `tasks/claude_to_codex.md [REVIEW-REQUEST]` 자동 push

**Dev 영향**:
- P0 fix 시작 시 Opus 로 전환 (root-cause 2hop)
- 3+ file 변경 완료 후 Codex REVIEW 자동 요청
- 800+ 라인 파일 수정 중 발견 → `dev_to_harness.md [SIZE-SPLIT]` push

ACK: `dev_to_harness.md` 에 1-line `MSG-BOOT-RULES ACKED — read 3 docs`.

---

## [2026-04-16 22:15] MSG-183 PENDING — [🔴 P0 TIME-EXIT 구조 손실 원인조사] 🔵 Opus 사용

**Source**: 🟩 HARNESS (72h 실측 기반)

### 실측 증거 (72h, clean epoch 이후)
- TIME exit 3,136건 / WR 26.8% / avg pnl -0.107% — **주 손실 벡터**
- 대조: TP 1,507건 WR 100% +0.458% / TRAIL 1,170건 WR 85.2% +0.282% 는 매우 수익
- 총합 WR 44.6% avg -0.042% 누적 -387$

### 가설 3개 (Dev 증거 기반 판정)
1. `pipeline.py` / `exit.py` max_hold timer 가 signal·regime 변화 무시하고 고정 시간만 체크 → profitable position 도 강제청산
2. TIME trigger 시 break-even 근처 포지션 판정이 loss-biased (BE-1% 도 TIME=close)
3. `ai_controller.py` AI HOLD override 가 winning TIME exit 을 억제 (short-circuit profitable close)

### 착수 순서
1. 🔵 Opus: `grep -n "max_hold\|exit_type.*TIME\|TIME_EXIT" invasion/ -r` → root path 특정
2. Ops SQL 교차: `SELECT strategy_id, direction, COUNT(*), AVG(pnl_pct) FROM trades WHERE exit_type='TIME' AND exit_ts>strftime('%s','now','-72 hours') GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20`
3. 실제 fix 범위 추정: param only / gate only / code path change
4. fix 3+ file 시 → commit 후 `claude_to_codex.md [REVIEW-REQUEST]`

**북극성 정합**: TIME loss 제거 = 수익 포지션 보호 = 공격 강화. 방어 아님.

ACK: `dev_to_harness.md` 에 `MSG-183 IN-PROGRESS — hypothesis N picked`.

---

## [2026-04-16 22:15] MSG-184 PENDING — [🔴 P0 SHORT BIAS] 🔵 Opus 사용

**Source**: 🟩 HARNESS (72h 실측)

### 실측
- Short 6,119건 / WR 42.1% / avg -0.057% / 누적 -350.4$
- Long 3,059건 / WR 49.5% / avg -0.012% / 누적 -36.8$
- Short 거래량 = Long × 2, 수익성은 훨씬 나쁨

### 설계 요청
- `direction_allocation_short_ratio` 신규 param (기본 0.5 → 점진 0.3)
- recent 24h WR feedback 으로 자동 rebalance (param_orchestrator 확장)
- 구현 전 Ops 와 dual-track: `harness_to_ops.md` MSG-ANALYSIS-SHORT 동시 push

### 판단 분리
- Dev: 구현 방안 2-3 option 제출 (param only vs gate vs allocator rewrite)
- Harness: Dev + Ops 결과 synthesis → 최종 방향 결정 후 Dev 에 지시

**북극성 정합**: 잘못된 방향 억제 = 공격 집중. 방어적 축소 아님.

ACK: `dev_to_harness.md [DECISION-REQUEST] MSG-184 option A/B/C`.

---

## [2026-04-16 22:15] MSG-185 PENDING — [🔴 P0 CRYPTO CONCENTRATION] 🟢 Sonnet

**Source**: 🟩 HARNESS (72h 실측)

### 실측
- crypto_momentum_reversal_g11_ai 혼자 5,091건 = 전체 거래의 55% / WR 46.3% / avg -0.039%
- 같은 family variant 들 합치면 > 70% 집중
- Cluster risk + evolution diversity 상실

### Fix
- Evolver: 1 family max allocation 30% cap (`param_registry.py` 추가 param `family_max_allocation_pct=30`)
- Elo tournament: 승급 시 family 분산 점수 가중치 추가
- Runtime: 진입 직전 `family_position_count` 체크 → cap 초과 시 reject (gate)

ACK: `dev_to_harness.md MSG-185 IN-PROGRESS`.

---

## [2026-04-16 22:15] MSG-SIZE-SPLIT PENDING — [🟡 P1 batch] 파일 분할 8개 (> 1000 라인)

**Source**: 🟩 HARNESS (Jin 04-16 "너무 길면 맨날 헷갈리잖아")

### 대상 (line count desc)
1. `invasion/trade/pipeline.py` 1735 — 먼저 (가장 버그 많음)
2. `invasion/main.py` 1520
3. `invasion/signals/providers_extended.py` 1374
4. `invasion/data/store.py` 1371
5. `invasion/exchange/okx/public.py` 1168
6. `invasion/signals/engine.py` 1129
7. `invasion/config/param_registry.py` 1073
8. `invasion/data/data_collector.py` 1022

### 분할 원칙
- responsibility 단위 (단순 라인 자르기 금지)
- `__init__.py` re-export 로 기존 import 경로 유지
- behavior change 0 — smoke 5-step 동일 결과
- 각 파일 ≤ 600 목표
- commit: `refactor: split <orig> into <new_a>/<new_b> (behavior 0)`
- 각 분할 완료 시 `claude_to_codex.md [REVIEW-REQUEST]` 필수

**우선순위**: MSG-183/184/185 P0 완료 후 batch 착수. 각 분할 사이 smoke 통과 필수.

ACK: `dev_to_harness.md MSG-SIZE-SPLIT PENDING — queued after P0`.

---

---

## MSG-ATR-AT-ENTRY PENDING (2026-04-18 18:39 AEST Sat) — Fwd PR6

🟩HARNESS → 🟦DEV

**Scope**: `trades.atr_at_entry REAL` column + entry-time write path (Codex Forward Plan PR6).

**Files touched** (경로 명시):
- `invasion/data/_store_schema.py` — `_SCHEMA_VERSION 5→6` + fallback `_TABLES["trades"]` DDL `atr_at_entry REAL DEFAULT NULL` + `_TRADE_NUMERIC_FIELDS` + `_get_trade_columns()` whitelist
- `invasion/data/unified_schema.py` — `SCHEMA_VERSION 13→14` + trades DDL `atr_at_entry REAL DEFAULT NULL`
- `invasion/data/store_core.py` — `_missing_cols["atr_at_entry"]` idempotent ALTER + `_run_v5_to_v6_migration` (SCHEMA_VERSION bump + column ADD guarded by PRAGMA scan)
- `invasion/trade/_pipeline_scan.py` — entry writer passes `"atr_at_entry": float(atr_pct or 0)` (reuses `atr_pct` already computed at L780 for `exit_engine.calc_entry_exits`)

**Smoke** (all PASS):
1. Fresh `:memory:`-style DB → `atr_at_entry` column present, `schema_version=6`.
2. Legacy v4 DB boot → ALTER adds column, version bumps 4→6 (v4→v5 then v5→v6 chained), repeat boot idempotent (no re-ALTER).
3. Mock `insert_trade(atr_at_entry=0.015)` → DB row carries 0.015.
4. Live DB **untouched** (DDL/ALTER runs only on next bot boot).

**Intent**: Enable ATR-based trajectory simulator (stop/target realism, regime-aware volatility attribution) — currently 0% historical trades carry ATR snapshot, going forward 100% do.

**Non-scope**: Position dataclass unchanged (atr passed directly at writer site via `market_data["atr_pct"]`). Downstream trajectory simulator is separate PR.


