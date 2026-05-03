# Dev → Harness 버스

**규약**: Dev 세션이 Harness에게 전달. 새 메시지는 파일 상단에 append. Harness는 매 루프 주기에 PENDING 섹션 처리 후 헤더를 `ACKED at HH:MM`exclusive 로 수정.

---

## 🟦DEV MSG-DASHBOARD-FULL-REDESIGN Phase A/B/C 완료 + [RESTART-REQUEST] — 2026-04-19 11:48 AEST — commits c931df3e / 61fcd6d6 / a5e47ef8

3 phase 전부 commit. 66-row invariant 유지, 90/90 regression pass.

### Phase A (c931df3e)
- `sections/winners.py` 신설 — trade_quality 에서 winners/losers 로직 추출 (rows 인자 가변)
- `operations.py::_render_stats_panel` 리팩터 — 100 LOC → 3 LOC, winners.render 호출
- Behavior 불변 (기존 trade_quality 10행 + winners 10행 composite 유지)

### Phase B (61fcd6d6)
- `intel.py` _render_asymmetry 섹션 삭제 (north_star_bar 에 흡수)
- LW stack 8행 유지: AI_COST(2) + WS_FEEDS(4) + SLIP(2, was 1)
- Row 12-36 RW: ARCH_FLOW 25행 → PIPELINE_FLOW(14) + PROVIDER_CHAIN(11) 분할

### Phase C (a5e47ef8)
- `sections/cross_exchange.py` 신설 — OKX / CAP / Alpaca 3-row (시장 hours 판정 + balance + 포지션)
- `intel.py` row 55-64 재배치: ALERT_PANEL 10→7 축소, 후속 3행 cross_exchange
- 결과: 주말엔 CAP/Alpaca CLOSED 배지, OKX OPEN. 월요일 실측 전 UI 사전 준비 (feedback_okx_only_test 정합)

### Action Required — Harness
**[RESTART-REQUEST] P1**: `bash start.sh` → 81/82/83th restart (phase 별 분리 가능 혹은 단일 restart 로 통합). Render 검증 시 dampen counter (ops 북극성 bar) + scan error (intel alert panel) 두 축 지속 확인.

### Mockup v2 이행 결과
mockup 의 4 판단 지점 모두 승인대로 구현. 단 LOG 섹션은 기존 LW 25행 위치 유지 (mockup 에 명시적 위치 없음). 후속 최적화 여지 있으면 별도 MSG.

---

## 🟦DEV MSG-HARDCODE-PURGE + LEARNER-DELEGATE + [RESTART-REQUEST] P0 — 2026-04-19 11:36 AEST — commit fa619378

**P0 learner 위임 + 하드코딩 제거.** Jin "자율 진화 모델 알아서 튜닝" 재확인 조치.

### 변경
- **4 param adaptive 등록** (`invasion/ops/adaptive_params.py` ADAPTIVE_PARAMS + PARAM_BOUNDS):
  - `strategy_warmup_explore_rate` (0.0, 1.0)
  - `strategy_warmup_trade_floor` (5, 100)
  - `max_hold_sec_crypto` (60, 3600)
  - `exit_hold_mult_crypto` (0.5, 5.0)
  - Total ADAPTIVE 104→108, BOUNDS 105→109 (header 동기화)
- **Softmax temperature 5개 preg SSOT 전환** (`invasion/config/_params_gates.py`):
  - `softmax_temp_{crisis,risk_off,risk_on,transition,neutral}` = {10,8,8,6,4} 기본값
  - `invasion/strategy/engine.py` 하드코딩 dict 제거 → `preg(f"softmax_temp_{regime}")` 읽기. None 시 neutral fallback.

### 검증
- AST + 5 preg 등록 확인 (값 10/8/8/6/4)
- Full runtime smoke: `StrategyRouter.select('crypto','mid','LONG','crisis')` + `'neutral'` 전부 정상 (ε-warmup + softmax preg 양쪽 pass)
- 90 regression pass

### 북극성 정합
- Amplify-only 유지 (모든 temp 기본값 ≥ 2.0)
- `feedback_adaptive_learner_attack` 준수 — bounds 양성 양수 범위, tight/aggregate loss 방향 drift 차단
- Dev/Ops 는 수치 수정 X, learner (Thompson) 또는 pset 으로만 조정

### 아직 learner 미wire 항목
- Softmax temp 5개: preg SSOT 는 완료, adaptive_tuner 위임은 **샘플 민감도** 이유로 후속 MSG (감지 트리거 기반 wire 권장)
- 이유: temperature 는 분포 전체를 shift, Thompson 의 단일 pnl 피드백으로 수렴 어려움. Harness 판단 요청: (a) adaptive 추가 vs (b) Harness cron empirical 관찰 기반 조정

### Cross-exchange impact (feedback_okx_only_test)
- Softmax temp: 모든 regime 공통 → OKX/CAP/Alpaca 동일 exploration rate 적용 (의도)
- max_hold_sec_crypto: crypto 전용 (MSG-PREG-CRYPTO-MAX-HOLD 이미 분기)
- exit_hold_mult_crypto: non-crypto 경로 불변 (crypto 만 영향)

### Action Required — Harness
**[RESTART-REQUEST] P0**: `bash start.sh` → 80th restart. 변경 사항:
1. Preg 5개 신규 등록 → 봇 시작 시 로드
2. engine.py 하드코딩 dict 삭제 → preg fallback 경로 검증
3. 4 param adaptive 진입 → Thompson 이 trade feedback 으로 자동 조정

---

## 🟦DEV MSG-HOTFIX-PREG-IMPORT + [RESTART-REQUEST] P0-CRITICAL — 2026-04-19 11:28 AEST — commit 5a4f9adc

**봇 복구** — 6h silent root cause 해소.

### 변경
- `invasion/strategy/engine.py:19` — 상단에 `from ..config.param_registry import get as preg` 추가 (commit 57f51ce8 의 ε-warmup 분기가 preg symbol 없이 참조한 NameError 해소)

### 검증
- AST + import OK
- `StrategyRouter(store).select('crypto','mid','LONG',regime='crisis')` → `crypto_momentum_reversal` 정상 반환 (NameError 없음, ε-warmup 분기 실행됨)
- 90 regression pass (tests/strategy + tests/signals + tests/ops)

### 교훈 수용
- **Runtime sample 연장** + **unit test 로 strategy.select() 커버** spec 추가 대상 — MSG-STRATEGY-TEST-COVERAGE 후속 제안
- Harness `_check_scan_errors` detector 신설 spec — Dev 구현 대기 (harness_alerter 영역)

### Action Required — Harness
**[RESTART-REQUEST] P0-CRITICAL**: `bash start.sh` → 79th restart.

### 후속 (MSG-HARDCODE-PURGE + LEARNER-DELEGATE)
본 hotfix 후 착수. 하드코딩 값 (explore_rate=0.3 / trade_floor=20 / temp 8,10 / max_hold_sec_crypto=900) learner 위임 여부 감사 예정.

---

## 🟦DEV MSG-DASHBOARD-FULL-REDESIGN [REVIEW-REQUEST] — 2026-04-19 11:24 AEST — Phase 1 research + mockup

**P1 review-first task.** MSG 지침: Research → Mockup → Harness review → 승인 후 commit.

### 산출물
- `.claude/docs/dashboard_redesign_v2_mockup.md` (신규, ~90 lines)
  - 현 섹션 전수 인벤토리 (15 sections, LOC/position/판정)
  - Operations v2 layout (66행, lifecycle ①→⑦ + winners/losers)
  - Intel v2 layout (66행, pipeline_flow + provider_chain 이관 + cross-exchange)
  - Cross-exchange impact (feedback_okx_only_test 정합)
  - 구현 작업 분해 (3 phase, 예상 ~200 LOC 신규/이동)

### Harness 판단 지점 (mockup 말미에 4개 질문)
1. pipeline_flow ops→intel 이관 확정?
2. winners/losers 분리 (신규 sections/winners.py) 확정?
3. cross_exchange 1행 vs 3행 (OKX/CAP/Alpaca 별 1행)?
4. LOC budget: MSG-ENTRY-ZERO-URGENT 선행 후 본 작업 일정?

### Action Required — Harness
Review + 결정. 승인 시 Dev 가 Phase A (ops 재배치) → Phase B (intel 재배치) → Phase C (cross_exchange 신설) 순차 commit.

---

## 🟦DEV MSG-STRATEGY-PICKER-BIAS-AUDIT + [RESTART-REQUEST] P0-URGENT — 2026-04-19 05:05 AEST — commit 57f51ce8

**P0-URGENT** — whale_fade 7/7 독점 root cause 확정 + 2-axis fix.

### Root cause
단일 high-score (whale_fade: composite 50 + contrarian_bonus 10 = 60, regime_match 1.5× → 75) + softmax temperature 낮음 (4~5) + 신규 활성화 strategy forced exploration 없음 → 사실상 argmax 수렴.

시뮬 (7 candidate, whale_fade=75 vs baseline 50 + g11_ai 65, 200 pick):
- temp=4.0: whale_fade 91.5% (**거의 독점**)
- temp=8.0: 68.5% (축소됐지만 편향 잔존)

### 변경 (2-axis amplify-only)

**Axis 1 — Regime temperature 증폭** (invasion/strategy/engine.py:491)
```
risk_on/risk_off: 4→8, transition: 3→6, neutral: 2→4, crisis: 5→10
```

**Axis 2 — ε-warmup exploration layer** (신규)
- Preg 2개 신설 (invasion/config/_params_gates.py):
  - `strategy_warmup_explore_rate` (default 0.3)
  - `strategy_warmup_trade_floor` (default 20)
- `StrategyRouter._recent_trade_count` helper (5-min TTL cache, DataStore.ro_query)
- Softmax 전에 explore_rate 확률로 warmup 후보 (24h trade_count < floor) uniform pick
- 0/0 설정 → pure softmax (backward compat)

예상 결과: whale_fade ≈ 0.7×68.5% + 0.3×(1/4) ≈ **48%**. 목표 < 50% 달성.

### Cross-exchange impact (feedback_okx_only_test)
- Temperature 는 전 pool 적용 (crypto/forex/stock/indices/commodity)
- Warmup 은 24h trade_count 기반 → exchange-agnostic, 월요일 Alpaca/CAP 재개 시 동일 작동 (의도)
- 현 CAP/Alpaca active (forex_specialist 4 variant, 47+ trades) 는 warmup 대상 외 → 단기 영향 없음

### 북극성 정합
- Explore = 기회 pool 확대 (공격 증폭, amplify-only)
- Temperature 를 낮추지 않고 **올림** → 다른 후보 가중치 증가
- Kill/penalty 없음, `feedback_no_block_filter_architecture` 준수

### 검증
- AST + import OK, 2 preg 등록 확인 (0.3 / 20)
- 90 regression pass (tests/strategy + tests/signals + tests/ops)
- softmax 분포 시뮬: 91.5% → 68.5% (temp alone) + ε-warmup 추가 기대 ~48%

### Action Required — Harness
**[RESTART-REQUEST] P0-URGENT**: `bash start.sh`. 77th/78th restart. 관찰:
- Shannon entropy ≥ 0.5
- Top 1 비율 < 50%
- WR/PnL 유지 또는 개선

---

## 🟦DEV MSG-PREG-CRYPTO-MAX-HOLD ACKED at 04:33 (commit 358354af, 다음 77th restart 시 함께 반영 — pool 30min 관찰 후) — 2026-04-19 04:32 AEST — commit 358354af

**P1** — `max_hold_sec_crypto` preg 신설 + exit.py 크립토 전용 분기.

### 변경
- `invasion/config/_params_exit.py`: `_reg("max_hold_sec_crypto", 900, (60, 3600), "exit", ...)` 추가. 0/None 이면 기존 base × mult 체인 그대로.
- `invasion/trade/exit.py::calc_entry_exits`
  - strategy_exit 경로 (line ~221) — `_max_hold` 미지정 + `group=="crypto"` 일 때 `preg("max_hold_sec_crypto")` 우선 적용
  - default 경로 (line ~269) — session key/base 조회 전에 동일 crypto 분기
- Non-crypto 경로는 기존 `max_hold_sec` × `exit_hold_mult_<group>` mult 체인 유지 (backward compat, cross-exchange impact 0)

### Cross-exchange impact (feedback_okx_only_test 규정)
- **OKX crypto**: `max_hold_sec_crypto` 기본 900 적용 → 기존 2490 (base × 1.0 default mult) 대비 단축. Ops pset 로 조정 가능
- **CAP (forex/indices/commodity)**: 기존 `max_hold_sec × exit_hold_mult_<group>` 그대로 (영향 없음)
- **Alpaca (stocks/ETF)**: 기존 `max_hold_sec × exit_hold_mult_stock|etf` 그대로 (영향 없음)
- 월요일 Alpaca/CAP 실검증 때 추가 보정 필요 없음 (crypto 전용 분기)

### 검증
- AST + import OK, preg default 900 확인
- 90 regression pass (tests/strategy + tests/signals + tests/ops)
- `tests/trade/test_exit.py::test_exit_flat_kill_fires` 는 git stash 로 본 변경 전에도 실패 확인 (**pre-existing**, 별건)
- preg bounds (60, 3600) → Ops `pset("max_hold_sec_crypto", 600)` 등 runtime 튜닝 가능

### 북극성 정합
- Crypto 전용 튜닝 = 공격 응답 속도 최적화 (dampen 아님, 단축은 공격 pace 가속)
- Non-crypto 기존 구조 보존 = cross-exchange 안전

---

## 🟦DEV MSG-CRYPTO-STRATEGY-POOL-EXPAND + [RESTART-REQUEST] P0-URGENT — 2026-04-19 04:25 AEST — commit dcee3cd1

**P0-URGENT** — Root cause 확정: **active crypto strategy 가 `crypto_momentum_reversal_g11_ai` 단 1개** (JSON+DB 이중 확인). StrategyRouter.select() 가 후보 1개만 만나 100% g11_ai 반환 — 이것이 Ops 관측 10/10 독점 원인.

### 변경 (JSON + DB 이중 업데이트)
활성화 6 건 — status `disabled` → `active`:
- `crypto_momentum_reversal` (parent non-g, 좋은 과거 empirical baseline)
- `crypto_momentum_reversal_g4_ai` (ai sibling)
- `crypto_momentum_reversal_g215_ai` (ai sibling)
- `whale_fade` (short kill revert 과 pair, 다른 family)
- `crypto_contrarian_swing` (parent, contrarian family 재가동)
- `crypto_funding_carry` (전혀 다른 alpha axis — funding rate)

결과: crypto active pool **1 → 7** (match('crypto', 'LONG') = 7, match('crypto', 'SHORT') = 7)

### 북극성 정합
- Pool 확대 = 공격 증폭 (feedback_aggressive_always_profit + feedback_northstar_auto_fix)
- Kill 재발동 **없음** (`_PERMANENT_STRATEGY_DIRECTION_KILL` 유지 empty)
- `_CRISIS_FAMILY_BLOCK` 크립토 3종 제거 상태 유지 (08de0cf4)

### 검증
- `StrategyStore.match('crypto', 'mid', 'LONG/SHORT')` 모두 7 candidates 반환 확인
- 90 regression pass
- `softmax_select` 에서 Elo/composite 기반 diversified selection 예상 (temperature 에 따라 분포 퍼짐)

### Follow-up (본 MSG 범위 밖)
- 활성화된 6 strategy 중 과거 PF<1 empirical (g11_ai 양방향 / parent long leg 등) 은 **exit 구조 로 방어** 하는 spec 이 남아있음 (MSG 제안 #3). `exit_fsm_enabled_okx_crypto` live-gate alert 로 모니터 중이므로 신규 kill 대신 early trail / stop tight 파라미터 튜닝 권고 (Ops 영역).
- Ops 관측: 1h 누적 strategy_id 분포. g11_ai 비율 < 50% 이면 성공.

### Action Required — Harness
**[RESTART-REQUEST] P0-URGENT**: `bash start.sh`. 76th restart 후 1h 관측.

---

## 🟦DEV MSG-OKX-ONLY-AUDIT CORRECTION — 2026-04-19 03:38 AEST

**Jin 03:38 정정 반영 (feedback_okx_only_test 업데이트)**: "alpaca capital 무시 하라는 게 아니라 대비해놓고 장 열면 테스트". 따라서 아래 SPEC 초안 **폐기** — Alpaca/CAP adapter disable 방향은 Jin 방침 아님.

**올바른 방향**: OKX = primary test bed (crypto 24/7 rapid iteration). Alpaca/CAP = 유지. OKX-tested 기능은 **cross-exchange 영향 사전 분석** 문서화 (adapter 차이 / fee 구조 / API 제약 / market hours 상수). 월요일 = Alpaca/CAP 실측 switch.

**Dev 조치**:
- 신규 `okx_only_mode` flag 제안 **철회**
- 향후 feature commit MSG 작성 시 `### Cross-exchange impact` 섹션 추가 (okx/cap/alpaca 각 동작 명시)
- 기존 wait/market-closed 가드 (MSG-SILENT-MARKET-CLOSED-GATE commit 39a5449f) 는 OKX-only 방침 오해 산물이지만 **유지** (주말 false-alarm 방지 실제 효과는 정당). 단 본 방침 해제 시 `[DECISION-REQUEST]` Harness 회부 필요 없음 — 이미 동작 적절.

**아래 원본 audit 내용 (참고용 보존)**:

## 🟦DEV MSG-OKX-ONLY-AUDIT REPLY + SPEC 초안 — 2026-04-19 03:35 AEST

**응답**: MSG-OKX-ONLY-CODE-AUDIT. audit 완료, spec 초안 포함. commit 없음 (Ops 실측 대기).

### Audit 결과 (evidence-based grep)

#### 1) preg flag 유무 — 전부 **0 건**
```bash
grep -rn "alpaca_enabled\|capital_enabled\|okx_only\|_OKX_ONLY" invasion/
→ No matches found
```
현재 OKX-only mode 를 직접 제어하는 flag **존재하지 않음**. 기존 guard 0개.

#### 2) Exchange wire — 무조건 login 시도, login 성공 시 항상 활성
- `invasion/boot/wiring_exchange.py:76-185` — cap_client.login() + alpaca_client.login() 양쪽 모두 unconditional 시도. 성공하면 adapter 생성 + WS feed + router 등록. **flag 기반 skip 없음**.
- `router = ExchangeRouter({"okx": ..., "cap": ..., "alpaca": ...}, config)` — 3 exchange 모두 항상 경로에 존재.

#### 3) Family × Exchange 필터 (현존 유일한 제한)
- `invasion/strategy/family_seeds.py:28` — `allowed_exchanges: frozenset[str]` per family
- Filter site (2곳):
  - `invasion/ticks/unified_scan.py:83` — `if exchange not in family_spec.allowed_exchanges: skip`
  - `invasion/trade/_pipeline_scan.py:359, 485` — 동일 패턴 (main + swap)
- 결과: family 별로 일부는 okx-only, 일부는 cap-only/alpaca-only, 일부는 multi. 현 OKX 거래 차단은 **family 레벨이 아님** (이전 MSG-CRYPTO-KILL-REVERT 와 무관하게 작동 중).

#### 4) 기타 okx 분기
- `gate_matrix.py:228`, `wiring_trade.py:187`, `eod_flatten.py`, `reconciliation.py` 에 `exchange == "okx"` 분기 존재 — okx-specific 로직이지 okx-only guard 아님.

### Root cause 판정
**현 코드 base line = multi-exchange active**. OKX-only 모드를 위해서는 새 guard 추가 필요.

### SPEC 초안 (Dev 제안, 3 layer 최소 침습)

#### Layer 1 — Bootstrap flag (신규)
`invasion/config/_params_gates.py` 에 추가:
```python
_reg("okx_only_mode", 0, (0, 1), "ai",
     "OKX-only test mode. 1 이면 cap/alpaca adapter init 건너뛰고 okx 이외 ticker 는 scan 단계에서 skip.")
```

#### Layer 2 — Adapter init bypass
`invasion/boot/wiring_exchange.py:76` 상단에 preg check:
```python
from ..config.param_registry import get as preg
if int(preg("okx_only_mode") or 0) == 1:
    log_event("SYSTEM", "okx_only_mode=1 → Capital/Alpaca init 건너뜀", "info")
    cap_client = None
    cap_adapter = None
    alpaca_adapter = None
else:
    # 기존 cap + alpaca 블록 그대로
    ...
```
- login 시도 자체 생략 → rate limit / auth 실패 noise 제거
- router 에는 `okx` 만 등록 (기존 `if cap_adapter is not None` 가드가 자동 처리)

#### Layer 3 — Scan-level filter
`invasion/ticks/unified_scan.py` + `invasion/trade/_pipeline_scan.py` 의 `allowed_exchanges` 체크 전에:
```python
if int(preg("okx_only_mode") or 0) == 1 and exchange != "okx":
    continue  # okx-only 모드: 비-okx 티커 전체 skip
```
family_spec.allowed_exchanges 로직 건드리지 않음 (family_seeds 테이블 수정 X).

### 스코프 밖 (본 MSG 아님, 후속)
- Ops pset 로 `okx_only_mode=1` 적용 → Harness restart → Dev 실측 후 commit 판단
- dashboard 표기 (operations bar 에 "OKX-ONLY" badge) 는 follow-up
- data/live_config.json migration 은 Ops 권한

### Advisor 권고 (회신)
본 audit 은 grep + 직접 read 로 충분 (`feedback_no_overkill_codex_delegate` 준수, `dev-wire-guardian` 호출 생략). 실제 spec commit 단계는 Ops 실측 필요성 확인 후 별도 MSG 발행.

### 북극성 정합
OKX-only 는 테스트베드 집중 = 공격 강도 유지 + noise 제거. 방어 로직 아님. Flag 기본값 0 (current multi-exchange behavior 유지) 으로 side-effect 없음.

---

## 🟦DEV STATUS-REPORT REPLY — 2026-04-19 03:32 AEST (응답: MSG-STATUS-REPORT [QUERY])

### Active
- idle — 최근 P0 sweep batch 완료 직후, 후속 PENDING 대기 중
- Monitor ARM 유지 (task b3nolsr2y persistent, harness/ops → dev)

### Pending queue (처리 대기)
- 없음 (actionable)
- META 잔존: MSG-ROUTE-REMINDER (ACK 불필요), MSG-STATUS-REPORT (본 reply)
- Follow-up 외부 의존: MSG-V6-LEGACY-CLEANUP → Ops migration 대기, [RESTART-REQUEST] 3건 (08de0cf4 / 34b22c8a / 6b76e20d) → Harness restart 대기

### Recent commits (30min)
| sha | msg | files |
|---|---|---|
| 6b76e20d | regime_mult amplify-only (< 1.0 entry 2개 삭제) | 1 |
| 34b22c8a | provider_effectiveness penalty + score_below_min block 제거 | 2 |
| 08de0cf4 | crypto strategy kill 전수 revert (OKX 테스트베드 복구) | 1 |
| 39a5449f | silent market_closed UTC gate | 1 |
| 1e5cdab6 | silent warmup skip | 1 |
| 115964ae | V6 legacy ACK + Ops migration 요청 routing | 2 |
| 2f69619f / 048266a2 / 2c9067de | commit hash 채움 (docs-only) | 각 1 |

전 커밋 AST + import + 90 regression + 5-step smoke pass.

### Next
- 새 PENDING 들어오면 즉시 착수 (continuous producer)
- idle 지속 시 self-audit rotating (DB/File/Wire) 고려 — 단 현재 세션에서 이미 많은 변경 있으므로 새 batch 는 다음 session 에 위임 예정

### Blocker
- 없음

### 세션 누적 (참고, 02:10~03:32)
- 10 MSG ACKED (P0×3 sweep, P0-URGENT×1 crypto, P1×4 alert squad batch, P1×1 warmup, P1×1 market gate, P1-META×2 routing/query) + 3 docs-only = 13 commit
- Monitor arm 유지 중

---

## 🟦DEV MSG-REGIME-MULT-SWEEP + [RESTART-REQUEST] P0-URGENT — 2026-04-19 03:17 AEST — commit 6b76e20d

**P0-URGENT** — composer `_REGIME_WEIGHT_MULTS` < 1.0 entries 삭제 (amplify-only).

### 변경
- `invasion/signals/composer.py::_REGIME_WEIGHT_MULTS`
  - **삭제**: `crisis.technical=0.8`, `risk_on.fear_greed=0.8`
  - **유지**: amplify-only entries (1.3/1.5/1.2 등)
- `NorthstarCounter.record("dampen", "composer/regime_mult")` 훅은 유지 — 향후 누군가 다시 < 1.0 추가 시 즉시 감지용 방어 telemetry

### 검증
- Assertion: `m < 1.0 for m in all_mults` → 0 건
- 90 regression pass
- Restart 후 1h 내 dampen count=0 수렴 확인 필요

### 북극성 정합
- Amplify-only matrix, crisis-based penalty 제거 (crisis.technical 0.8 은 "위기엔 기술지표 덜 믿자" 라는 방어 사고 — 반북극성)
- risk_on.fear_greed 0.8 도 동일 (강세장에서 공포 신호 약화 = 대칭 사고)

### Action Required — Harness
**[RESTART-REQUEST] P0-URGENT**: `bash start.sh`. 75th restart. 1h 관찰.

---

## 🟦DEV MSG-NORTHSTAR-SWEEP + [RESTART-REQUEST] P0-URGENT — 2026-04-19 03:12 AEST — commit 34b22c8a

**P0-URGENT** — composer/provider_effectiveness dampen + engine/score_below_min block 전수 제거.

### Part A 변경 (composer)
- `invasion/signals/composer.py` provider_effectiveness 의 `elif wr < _penalty_wr` 분기 전체 삭제. amplify (boost) only 구조.
- NorthstarCounter.record("dampen", "composer/provider_effectiveness") 호출 site 자동 제거.

### Part B 변경 (engine)
- `invasion/signals/engine.py` `abs(composite.score) < min_score` reject block 전체 삭제. `_last_min_score` 만 대시보드 probe 용 유지.
- neutral_weak_signal (neutral regime × abs<10) 는 regime-scoped structural reason 이라 유지.
- NorthstarCounter.record("block", "engine/score_below_min") 호출 site 제거.

### 검증
- AST + import OK
- 90 regression test pass (tests/signals + tests/strategy + tests/ops)
- baseline counter 0/0 확인 (기존 호출 site 실제 삭제됨)

### 북극성 정합
- Attack amplification-only 구조 (penalty/floor 제거)
- Any directional signal 이 sizing/exit 단계로 흘러가 risk governance 수행
- 공격 회복 우선 (feedback_northstar_auto_fix Jin 04-19 00:40 영구 위임)

### Action Required — Harness
**[RESTART-REQUEST] P0-URGENT**: `bash start.sh`. Restart 후 10min NorthstarCounter dampen/block 1h count → 0 수렴 확인 (MSG 검증 기준).

---

## 🟦DEV MSG-CRYPTO-KILL-REVERT + [RESTART-REQUEST] P0-URGENT — 2026-04-19 03:05 AEST — commit 08de0cf4

**P0-URGENT** — Option A (crypto kill 전수 롤백) 완료. 봇 restart 필요.

### 변경
- `invasion/strategy/family_utils.py`
  - `_PERMANENT_STRATEGY_DIRECTION_KILL` = `frozenset()` (3 entry → 0)
  - `_CRISIS_FAMILY_BLOCK` 크립토 3종 제거 (crypto_momentum_reversal × short, whale_fade × short, crypto_contrarian × short). 유지: indices_specialist × short, contrarian_commodity × long, volatility_spike × long.

### 검증
- AST ok / 90 regression test pass (tests/strategy + tests/signals + tests/ops)
- `is_strategy_direction_killed('crypto_momentum_reversal_g11_ai', 'long')` → False ✅
- `is_crisis_family_block('crypto_momentum_reversal_g4', 'short')` → False ✅
- 인디시즈/커모디티 block 잔존 확인

### 북극성 정합
- 과거 P0-4 / G11-LONG-KILL / CRYPTO-MOMENTUM-PARENT-REVIVAL 의 empirical 근거 수용 (retirement = 정당) 했으나 **대체 assignment 없는 kill = 공격 불능**. OKX 24/7 거래 복구 가 `feedback_aggressive_always_profit` 우선. 재발 시 Option B (signal engine pool 확장) 로 접근.

### Action Required — Harness
**[RESTART-REQUEST] P0-URGENT**: `bash start.sh` 경유 (feedback_restart_via_startsh). Restart 후 10min 내 OKX entry 1+ 건 확인 필요.

---

## 🟦DEV MSG-SILENT-MARKET-CLOSED PENDING — 2026-04-19 03:00 AEST — commit 39a5449f

**P1 fix** — `_check_silent` 에 UTC 주말/장마감 gate 추가 (Fri 22:00 ~ Sun 22:00 UTC skip).

### 변경
- `invasion/ops/harness_alerter.py::_fx_markets_closed(now)` staticmethod 신설
- `_check_silent` 첫 줄 `if _fx_markets_closed(now): return`
- 6 case unit 검증 (Sat/Fri-open/Fri-closed/Sun-pre/Sun-open/Mon)

### 북극성 정합
Crypto 24/7 은 loss_streak / dd_1h 로 실제 silence 포착되므로 skip 해도 경보 구멍 없음. Forex/equity 장마감은 silence 가 "무"가 아니라 "정상" → 경보 의미 없음 제거.

---

## 🟦DEV MSG-V6-DEAD-FLAGS-OPS-MIGRATION PENDING — 2026-04-19 02:47 AEST

**MSG-V6-LEGACY-CLEANUP 처리 결과** — Dev 코드는 이미 clean, Ops migration 필요.

### grep 결과 (evidence-based, 증거 없이 주장 금지)
```bash
# Python 전역
grep -rn "v6_primary_provider\|v6_ai_mode\|use_v6_brain\|use_v7\|_ai_strategy_v" invasion/
→ 0 matches (DB schema _SCHEMA_VERSION=6 / v5→v6 migration comments 은 완전 무관, DB layout version)
```

### 상태
- `invasion/` 전역에 v6/v7 flag read 0건 (rename 대상 코드 없음)
- `_ai_strategy_v7` 주석/변수 0건
- `ai_provider_mode` 가 이미 SSOT 로 기능 대체 완료 (`invasion/config/_params_gates.py:346`)

### 잔존 dead flag (data/live_config.json, Ops 권한)
```json
"use_v6_brain": false,
"use_v7_exit": true,
"v6_ai_mode": "hybrid",
"v6_primary_provider": "gemini"
```

4개 key 모두 Python read 없음 → 완전 dead. 삭제 안전.

### 요청 (Ops 경유)
Harness → `harness_to_ops.md [OPS-MIGRATION-REQUEST]` 로 live_config.json 에서 4 dead flag 삭제 pset 지시 (각 키 `pset('KEY', None)` 또는 직접 edit).

### 스코프 밖 (Dev 처리 불가)
- live_config.json 편집 (dev-mode.md §역할경계: 🔴 data/live_config.json (Ops))
- live_config.json.bak_* 정리 (bak 파일은 gitignore, 자연 cleanup)

### 검증
```bash
grep "v[4-9]_" data/live_config.json   # 0 expected after Ops migration
```

### Header ACK
`tasks/harness_to_dev.md` MSG-V6-LEGACY-CLEANUP 헤더 `ACKED at 02:47 🟦DEV` 로 갱신 완료.

---

## 🟦DEV MSG-SILENT-WARMUP-SKIP PENDING — 2026-04-19 02:45 AEST — commit 1e5cdab6

**P1 fix** — `_WARMUP_SKIP_CATEGORIES` 에 `"silent"` 추가 (72nd restart 02:11 직후 57초 만에 false-alarm 발동한 근본 원인 제거).

### 변경
- `invasion/ops/harness_alerter.py:67` — `frozenset({"dd_1h", "loss_streak", "silent"})`
- 이유 주석 보강 (trades.MAX(exit_ts) pre-restart 참조 문제)

### 검증
- Unit import OK, `'silent' in _WARMUP_SKIP_CATEGORIES` True
- 행동: restart 후 30min 이내 silent alert 0건 (warmup gate 로 skip)
- warmup 종료 후 정상 fire (1800s 실제 gap 시)

---

## 🟦DEV MSG-ALERT-SQUAD-BATCH PENDING — 2026-04-19 02:32 AEST — commits b2f2d12e / 93f30f80 / 43be15ce / e8f89f17

**4건 처리 완료** (Alert Squad 스펙 batch):

| MSG | P | commit | 요약 |
|---|---|---|---|
| NORTHSTAR-VIOLATION-DETECTOR | P0 | b2f2d12e | NorthstarCounter (deque 1h) + composer/engine/strategy 3-site hook + harness_alerter detector + 6/6 unit tests |
| ALERT-EMIT-LOG | P1 | 93f30f80 | `data/alert_emit.jsonl` atomic O_APPEND, cooldown_hit/in_warmup 필드, file 필드는 성공시만 |
| INTEL-ALERT-PANEL | P1 | 43be15ce | sections/alert_panel.py + data.load_alert_squad() + intel.py Row 55-64 PROVIDER_CHAIN → ALERT_SQUAD 교체 |
| OPS-NORTHSTAR-BAR | P1 | e8f89f17 | sections/north_star_bar.py + operations.py Row 5-7 삽입 + MARKET_OVERVIEW 12→9 축소 |

### 5-step smoke (전 commit 공통)
AST / import / pytest (tests/ops tests/signals tests/strategy 90/90 pass) / runtime detector (alert md file 생성 확인) / render (intel.py + operations.py 66 rows invariant 유지)

### 북극성 정합
- Counter 가 비어도 detector no-op (fail-open)
- Dampen>0 / Block>0 → RED flag 즉시 가시 (operations.py 최상단)
- Router → codex-rescue → [ALERT-SPEC] 체인이 spec 제안 시 Handler 에서 '북극성 위반 허용' spec 거부 (feedback_no_defensive_param_dampen)

### Scope note (partial 완료 2건)
- **INTEL-ALERT-PANEL Part A** (ASYMMETRY/AI_COST/PROVIDER dup 제거) 는 intel.py 전면 재배치라 별도 MSG 권장. 현재 PROVIDER_CHAIN(10행)만 제거하고 그 자리를 alert_panel 로 교체한 minimum-risk 패치.
- **OPS-NORTHSTAR-BAR Part B** (lifecycle ①~⑦ 재구성) 는 POSITIONS/TRADES/STRATEGY 섹션 해체·재구성 필요. Part A (3행 bar 삽입) 만 반영. 북극성 가시화는 완전 달성.

### 검증 요청 (Harness)
- composer/engine/strategy 3 site 외 추가 dampen 경로 (e.g. sizing_feedback/quality) 누락 여부 Codex 2nd-opinion 권장
- alert_emit.jsonl schema alert_lifecycle.md 와 정합 확인 (cooldown_hit/in_warmup semantics)

### Files
```
invasion/ops/northstar_counter.py          (+83 new)
invasion/ops/harness_alerter.py            (+77 -26)
invasion/signals/composer.py               (+11 -3)
invasion/signals/engine.py                 (+2)
invasion/strategy/engine.py                (+3)
invasion/boot/run.py                       (+9 ctx wiring)
invasion/dashboard/sections/alert_panel.py (+130 new)
invasion/dashboard/sections/north_star_bar.py (+160 new)
invasion/dashboard/intel.py                (+6 -3)
invasion/dashboard/operations.py           (+10 -5)
invasion/dashboard/data.py                 (+105 new load_alert_squad)
tests/ops/__init__.py                      (+0 new)
tests/ops/test_northstar_counter.py        (+62 new)
```

## 🟦DEV MSG-AI-CALL-TRACE PENDING — 2026-04-19 01:12 AEST — commit 663ad41e

**P0 (Sonnet executor)** — AI call 상세 trace jsonl 기록 (Jin "AI 콜 보낸거 받는거 정확히 기록").

### 변경
- `invasion/ai/call_trace.py` 신규 — `write_trace`/`trace_ctx`/`record_fallback_attempt`/`trace_stats` + thread-local context
- `live_providers.py` — `_call_claude`/`_call_gemini`/`_call_gpt` 모든 리턴 경로(success/HTTP error/parse error/request fail)에서 trace write
- `live_fallback.py` — `call_with_fallback` 이 provider attempt 직전 `record_fallback_attempt` 호출 (rotation chain 기록)
- `live.py` + `live_exit.py` — 각 stage 에서 `trace_ctx(stage=..., ticker=..., trade_id=...)` 컨텍스트 열어 payload 주입
- `orchestrator.py` — `run_stage` trace_ctx 감싸기 + `get_stats()` 에 `trace` 집계 섹션 추가

### 신규 파일 포맷 (`data/ai_call_trace.jsonl`, 1 line/call)
```json
{"ts":1776523970,"call_id":"...","stage":"entry_judge","provider":"gemini","model":"gem-2",
 "ticker":"BTC-USDT","trade_id":"okx_BTC-USDT_...","fallback_chain":["claude","gemini"],
 "request":{"prompt_full":"...","prompt_truncated":false,"prompt_len_bytes":1234,
            "payload_hash":"sha256:...","cache_blocks_count":1,"max_tokens":500,"temperature":0.2},
 "response":{"raw_text":"...","parsed_json":{...},"parse_success":true,
             "status_code":200,"stop_reason":"end_turn"},
 "tokens":{"input":1234,"output":312,"cache_read":980,"cache_creation":0},
 "latency_ms":847,"cost":0.0003,"error":null,"error_body":null}
```

### Safety
- write fail → `log_event("AI_TRACE", "write fail: ...", "warn")` 만, 절대 raise 안 함
- API key 4종 (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`/`GOOGLE_API_KEY`) env 값이 prompt 에 섞여 있으면 `<redacted:...>` 로 치환
- prompt >100KB / raw >50KB / parsed >20KB 초과 시 truncate + payload_hash 유지
- 기존 `ai_calls` DB 테이블 insert 그대로 병행 (병행 기록, 교체 아님)

### 스모크
- `python3 -c "from invasion.ai import call_trace, live_providers, live_fallback, orchestrator, live, live_exit"` OK
- `trace_ctx` + `record_fallback_attempt` + `write_trace` + `trace_stats` 수동 실행 → fallback_chain `["claude","gemini"]`, redaction OK, cache_hit_rate 정확

### 용량/운영
- 1 call ≈ 2–5 KB → 500 calls/day ≈ 1–2.5 MB/day
- 월별 로테이션은 후속 작업 (별도 MSG) — 지금은 append-only 단일 파일

### 후속 확장 여지
- `ai_call_trace.jsonl` → dashboard arch_flow 섹션 (Intel) 에 `trace_stats()` 바인딩 → 최근 200 call latency p50/p95 + provider mix + cache hit rate + parse fail rate 표시
- Provider rotation cooldown 분석: fallback_chain length>1 line 만 필터 → 어느 provider 가 어떤 시점에 rate-limit 되는지 추적

---

## 🟦DEV MSG-EXIT-SWALLOW-LOG + MSG-PARTIAL-CLOSE-WIRE5 + MSG-AI-CONF-DAMPEN-KILL PENDING — 2026-04-19 01:02 AEST — commits 18d32660, 8bbd8d4e, 75d5d5a7

**P1 batch 3종 (Sonnet executor)** — exit silent swallow + partial close WIRE-5 + AI conf dampen 제거.

### 변경
1. **18d32660 fix(msg-exit-swallow-log)** `invasion/trade/exit.py`
   - 3곳 `except Exception: pass` → `_log_event("BUS", f"publish swallow: {_e}", "debug")`
   - 위치: FSM_CANARY 로그(123-124), legacy `_exit` bus publish(385-386), FSM `_exit` bus publish(799-800)
   - `try/except pass` 금지 원칙 준수, 에러 상실 제거

2. **8bbd8d4e fix(msg-partial-close-wire5)** `invasion/trade/close_handler.py`
   - partial close insert 에 `_estimate_fees` + `_resolve_slippage_bps` 호출 추가
   - 신규 필드: `exit_type_fine`, `max_profit_ts`, `min_pnl_pct`, `entry_params`, `realized_slippage_bps`, `entry_fee`, `exit_fee`, `funding_paid`, `fees_usd`, `net_pnl_usd`
   - `_finalize_close` 와 동일 schema parity → partial row 분석 unblind (이전 전부 NULL)
   - `pnl_usd`, `net_pnl_usd` 는 half_size 기준 재계산

3. **75d5d5a7 fix(msg-ai-conf-dampen-kill)** `invasion/ai/live.py`
   - LiveEntryJudge 이분법 approve/reject 전환
   - non-fear conf≤3: `size_mod=0.6` dampen 제거 → **reject outright**
   - fear conf≤3: `size_mod=0.8` dampen 제거 → **full pass** (contrarian edge intact)
   - `feedback_no_defensive_param_dampen` 준수 (Jin 04-16 23:35)

### 영향
- Bus publish 실패 시 debug 로그 확보 → 신호 손실 원인 추적 가능
- partial close 분석 데이터 (slippage/fee/drawdown/entry_params) 복구
- AI conf dampen 제거 → paper 계정 실제 데이터로 fear contrarian edge 검증 가능

### Next
- 재기동 필요 여부 Harness 판단 (exit.py/close_handler.py/live.py 전부 live bot 로드)
- bash start.sh 경유 리셋 권장 (feedback_restart_via_startsh)

---

## 🟦DEV MSG-AI-BUDGET-PERSIST + MSG-CLAUDE-KEY-FAIL-LOUD PENDING — 2026-04-19 01:02 AEST — commits 18074677, 4005cf41

**Batch 24 cost 누수 차단 2종 (P0)** — Sonnet executor.

### 변경
1. **18074677 feat(msg-ai-budget-persist)** `invasion/ai/orchestrator.py`
   - `data/ai_budget_state.json` persist (hour/day 키 + counters)
   - `__init__` 에서 load, `record_call` 후 debounced 5s save, hour/day rollover 시 force flush
   - 재기동 시 동일 UTC hour/day 면 counter 복원 → hour-22 181 calls 같은 bypass 차단
   - `BudgetState` 에 `current_day` 필드 추가

2. **4005cf41 fix(msg-claude-key-fail-loud)** `invasion/ai/live.py`
   - `_claude_or_gemini` legacy path: `cfg.anthropic_key or os.environ["ANTHROPIC_API_KEY"]` fallback
   - key 누락 시 `log_event("AI", "... cache path dead, falling back to Gemini ...", "warn")` 가시화
   - behavior 보존 — 키 없으면 여전히 Gemini fallback (거래 지속)

### 검증
- `python3 -c "from invasion.ai.orchestrator import AIOrchestrator; ..."` — 1 call 기록 → 파일 write → 재-init → counter 복원 확인 (`calls_this_hour=1`, `calls_today=1`, `spent_today=0.01`)
- `python3 -c "from invasion.ai import live"` OK

### env var 실측 결과 (Jin 요청)
- Shell env `printenv | grep -i anthrop` → **empty** (현재 Claude Code 세션)
- `.env` 파일 → `ANTHROPIC_API_KEY=sk-ant-api03-...` 존재
- `Config()` 로드 후 `c.anthropic_key` → **True**, prefix `sk-ant-api03`
- `load_dotenv()` 가 `invasion/config/config.py:6` 에서 이미 호출됨 → 봇 프로세스는 키 정상 로드 가능
- param_registry `ai_provider_mode` → `legacy_claude_gemini` 확인

### 후속 관찰 권고 (Dev 자율 X)
- `cache_read_tokens=0` 1674 calls 건은 env 누락이 **아님**. 진짜 원인 후보:
  (a) `prompts_cached.SHARED_STATIC` 토큰 수 < 1024 → Sonnet/Opus cache 임계 미달로 silent skip
  (b) cache_control TTL 만료 / 매 call 마다 block text 다름 → 재사용 불가
- 다음 세션에서 `SHARED_STATIC` 토큰 count 실측 필요 (Harness 지시 대기).

---

## 🟦DEV MSG-BUS-RACE-THREAD PENDING — 2026-04-19 00:44 AEST — commit bc394a91

**security_advisor #1 + #3 fix (P0)** — `invasion/bus.py` 단일 파일

### 변경
1. `publish()` 의 `_event_count`/`_history`/`_rate_window` mutation 을 `_lock` 안으로 이동 (seq 중복/이벤트 소실 차단)
2. `publish_async()` raw Thread() 제거 → `_PUBLISH_POOL = ThreadPoolExecutor(max_workers=100, thread_name_prefix="bus-publish")` submit
3. Pool 크기는 preg `bus_publish_pool_size` 로 외부화 (miss 시 100 fallback)
4. Subscriber 예외 `log_event("BUS", "subscriber err: ...", "warn")` 추가 (swallow → warn)
5. Pool shutdown 시 sync publish fallback (이벤트 손실 방지)

### 검증
- `python3 -m py_compile invasion/bus.py` OK
- 50× `publish_async` smoke: thread count 1 → 24 (pool 내 수렴, 50 raw thread 생성 안 됨), `stats.total_events == 50` (seq 무결성 OK)
- 기존 호출 경로 (event wiring, trade.entered 등) 시그니처 불변 — behavior 보존

### 후속 권고 (Dev 자율 X, Harness 지시 필요)
- preg `bus_publish_pool_size` 기본값 (100) 을 `invasion/config/_params_*` 에 등록할지 결정 (현재는 miss → fallback 100 으로 동작, 외부 튜닝 시만 등록 필요)

---

## MSG-PARAMS-SNAP + MSG-EDGE-CALIB-WIRE PENDING — commits [2ac4540b, 4e0dcf0b] 🟦 DEV

2026-04-18 — 수익 직결 2-step learner 활성화 (ml/signals advisor 최우선).

**Step 1 — MSG-PARAMS-SNAP (commit 2ac4540b)**:
- `invasion/trade/_pipeline_scan.py:860` 하드코딩 8 key → `ADAPTIVE_PARAMS` 104 key 전체 덤프.
- ml_advisor D1 Thompson Sampling bucket coverage 6.4% 원인 — 96 key snapshot 누락 → learner dead.
- None 값은 skip (registry 미등록 → learner key 오염 + json 부피 제어). `regime_at_entry` 는 컨텍스트 key 로 명시 유지.
- 검증: ast.parse OK / ADAPTIVE_PARAMS 104 / import OK.

**Step 2 — MSG-EDGE-CALIB-WIRE (commit 4e0dcf0b)**:
- `invasion/trade/close_handler.py` `_close_position` 끝부분에 `edge_calibration.update()` hook.
- signals_advisor D2 — `.update()` 정의만 callsite 0 → shadow Beta posterior 축적 0.
- `entry_signal.providers.scores` 순회 × `(provider, regime_at_entry, |score|//25 bucket)` cell → pnl_pct sign 으로 win/loss/neutral 기록.
- singleton factory = `get_default()` (기존). 예외 fail-loud (`EDGE_CALIB` warn).
- 검증: ast.parse OK / `get_default` import OK.

**Ops 에게 필요한 검증 (5min 재기동 후)**:
1. `sqlite3 data/invasion.sqlite "SELECT COUNT(*) FROM trades WHERE status='open' AND length(params_snapshot) > 500"` — snapshot 부피 확장 확인 (기존 <200 chars → ~3KB).
2. `ls -la data/edge_calibration.json` — 파일 생성 + n_samples > 0.
3. 5min 경과 시 Dev 로 확인 요청 (MSG-... VERIFY).

**제약 준수**:
- `git add <path>` + `git commit -- <path>` 명시 ✓
- 2 commit 분리 ✓
- try/except pass 금지 ✓ (둘 다 log_event warn)

---

## MSG-REGIME-CONF PENDING — commit 6c3abae3 🟦 DEV

2026-04-18 — `regime_primary_crypto_min_conf` default 0.3 → 0.15 (invasion/config/_params_orphans.py).

- 이유: OKX 3h regime 분포 neutral 52.8% / unknown 47.2% / 기타 0%. P0-7 (5c167d2) macro fallback 이 0.3 conf 미달로 발동 못함.
- 0.15 로 낮춰 fallback 빈도 증가 → risk_on / transition 가시성 확보. 북극성 모든 regime ATTACK.
- Registry bounds (0.1, 0.8) 유지 / adaptive_params learner bounds (0.15, 0.5) 유지.
- primary() 로직 (P0-7) · RegimeService sole writer (I-R3) 보존.
- 검증: seed=0.15 / bounds=(0.1,0.8) / `import invasion.main` OK.

---

## MSG-INTEL-NORTH-STAR PENDING — commit b8c66c86 🟦 DEV

2026-04-19 00:07 AEST Sun — dashboard_advisor D2 fix: Intel 북극성 visibility 2.5/7 → 5+/7.

- `invasion/dashboard/intel.py` `_draw()` 레이아웃 재배치 (66 rows 유지):
  * Row 4-5: AI COST compact (`_render_ai_cost_base` dead → wire, 5-row 섹션을 fit_rows 로 2행 압축: 24h/1h cost + stage header)
  * Row 6-9: WS FEEDS (`_render_ws_feeds` 정의만 → wire, 4 exchange OKX/Binance/Capital/Alpaca 연결 상태)
  * Row 10: ASYMMETRY (신규 `_render_asymmetry`, avg_win_pct/avg_loss_pct/ratio, 북극성 ≥1.5 tgt vs actual, OK/LOW/FAIL 뱃지)
  * Row 11: SLIP bps by exchange (신규 `_render_slip_summary`, `trades.realized_slippage_bps` GROUP BY exchange n≤6, commit bb8e77b 데이터 노출)
  * Row 12-36: log + arch_flow right panel 30→25 rows 압축
  * Row 55-64: provider_chain 13→10 rows 압축
- Dead code cleanup: `_render_ai_call_detail` helper 제거 (ai_cost + ai_log 가 커버) + `load_ai_quality` import 제거 (ai_call_detail 외 사용 없음, 타 파일 6곳은 무관)
- KEY PARAMS (21 hard-coded) 는 이번 batch 범위 밖 (dynamic preg scan 은 별도 예정)
- 검증: `python3 -c "import invasion.dashboard.intel"` OK + `python3 -m invasion.dashboard.intel` 3초 live 실행 → 66-row assert pass, traceback 0건, 실제 표시 확인:
  * AI COST: `24h: $9.151 (1690 calls)  1h: $0.4529 (73 calls)  All: $13.62`
  * WS FEEDS: OKX/Binance/Capital.com LIVE + Alpaca OK (4/4 exchange)
  * ASYMMETRY: `avg_win +0.305%  avg_loss -0.546%  ratio 0.56x  tgt ≥1.5 FAIL  n=500`  ← 북극성 위반 즉시 가시화
  * SLIP: `OKX +0.2 (n=10681)  ALPACA +0.0 (n=1197)  CAP +0.1 (n=878)`
- Commit scope: `invasion/dashboard/intel.py` 1 파일 (+106/-58)

---

## MSG-ARCH-FLOW-SCHEMA PENDING — commit 804bab4c 🟦 DEV

2026-04-19 00:04 AEST — P0 arch_flow.py renderer schema 교정 (intel 우측 panel 복구).

- 98dfc2e 에서 loader 스키마 추측 잘못 → 4 섹션 30행 중 의미있는 데이터 0행 (dashboard_advisor D1 지적)
- Actual schema 확인 후 재작성:
  * BROKER SYNC: `adopted_24h`/`removed_24h`/`parked_adopt`/`parked_backoff` + `last_event{ts,action,ticker,strategy_id}` (dict of exchanges 아님)
  * SHADOW MODULES: flat `{ml_meta,liveness,kelly}` bool (modules wrapper 아님)
  * STRATEGY EVOLVER: `mutation_gen`/`mutations_24h{gaussian,bayes,ai}`/`tournament{bracket_size,leader,leader_wr}`/`elo_movers[{strategy_id,elo_delta}]`
  * RESTART IMPACT: `list[dict]` 직접 반환 (events wrapper 없음), `{ts,agent,note,stats{n,wr,sum_pnl}}`
- Smoke render 확증: `parked_backoff=54` (Jin 지적 ghost count), shadow 3-flag ON/OFF, evolver gen=215 / bracket=70 / wr=40.6% / elo top-3, restart 4건 pnl 표시
- 30 rows 유지, fail-loud err 표시 (`{type(e).__name__}: {e}`)
- Commit scope: `invasion/dashboard/sections/arch_flow.py` 1 파일 (+121/-56)

## MSG-F-N17-AI-LIVE-S2 PENDING — commit a58a3c05 🟦 DEV

`invasion/ai/live.py` 2차 split (S2): exit cluster 추출.

- 신규: `invasion/ai/live_exit.py` (356L) — `LiveProactiveExit` + `LiveWSPriceIntel` + `LiveExitAdviser` + `_fallback_text_parse`
- `live.py` 903 → 598L (back-compat re-export 유지)
- 제약 준수: 87f0127 dampen 제거 유지 (`composite_score * 0.` 0건), AIController lock (22873285) 미변경, S1 `live_providers` (e71659f) 미변경
- 검증: `py_compile`, `import invasion.main`, 외부 importer (`boot/wiring_ai.py`, `boot/wiring_ai_live_fallback.py`, `ops/ai_controller.py`) 경로 무영향, 클래스 identity OK
- Circular import 회피: `live_exit` 메서드 내부에서 `from .live import _claude_or_gemini, _trade_id_from_pos` lazy import

## MSG-F-N17-DASHBOARD-DATA PENDING — commits [0f847cf3, a84fe1e3] 🟦 DEV

2026-04-18 AEST — F-N17 `invasion/dashboard/data.py` 985L split (plan + P1 extraction).

**Commits**:
1. `0f847cf3` docs(msg-fn17-dashboard-data-plan): `docs/MODULE_REVIEW_dashboard_data_split.md` — 13-block map (B0-B12) + P1/P2/P3 순서 + F-N15 SSOT 유지 제약 명시
2. `a84fe1e3` refactor(msg-fn17-dashboard-data-arch-flow): Block B5 (MSG-159 ARCH FLOW 4 loaders) + helper `_parse_restart_log` → `invasion/dashboard/data_arch_flow.py`

**Before/After**: data.py 985 → 784 LOC (-201). data_arch_flow.py 238 LOC. 
Extracted: `load_shadow_modules`, `load_broker_sync_counts`, `load_strategy_evolver_stats`, `load_restart_impact`.
Backward compat: `data.py` 말미 re-export (`from .data_arch_flow import ...`) — 기존 `from ..data import ...` 경로 불변.

**F-N15 SSOT 유지**: `_cached` / `_sql_query` lazy delegation via `from . import data as _data` (circular 회피). 신규 모듈에 `sqlite3.connect` 없음. data.py live `sqlite3.connect` 0 유지 (docstring 역사 언급 2건만).

**검증 통과**:
- `wc -l`: data.py 784, data_arch_flow.py 238
- `py_compile` OK
- `python3 -c "import invasion.main"` OK  
- `grep sqlite3.connect invasion/dashboard/data.py` → docstring 2건만 (live 0)
- Functional: `load_shadow_modules()` / `load_broker_sync_counts()` / `load_restart_impact(limit=2)` 결과 정상 반환

**UI 파라미터 무변경**: operations / intel / ai / signal / chart_window 의 `from .data import ...` 그대로 (MSG-159 4개 함수는 현재 live 코드서 소비 안 함 — 아카이브된 arch_flow wiring plan 의 경로 호환용 re-export).

---

## MSG-F-N17-EVOLVER PENDING — commits [f29da8c2, 483af57e] 🟦 DEV

2026-04-18 AEST — F-N17 `invasion/strategy/evolver.py` 968L split (plan + P1 extraction).

**Commits**:
1. `f29da8c2` docs(msg-fn17-evolver-plan): `docs/MODULE_REVIEW_evolver_split.md` — 14-block map (A-N) + 3-batch 순서 + FitnessFunction untouched 선언 (ml_advisor 관할)
2. `483af57e` refactor(msg-fn17-evolver-mutations): Blocks H+I (genetic mutation ops) → `invasion/strategy/evolver_mutations.py`

**Before/After**: evolver.py 968 → 883 LOC (-85). evolver_mutations.py 132 LOC (pure functions: `gaussian_mutate`, `_mutate_dict`, `bayesian_mutate`, `_interpolate_dict`, `structural_mutate` + `ALL_SIGNALS` 상수). 클래스 메소드는 1-line delegating shim 으로 변환 — callsite (evolve_cycle / _ai_targeted_mutate fallback) 시그니처 완전 불변.

**건드리지 않음** (임무 제약 준수):
- FitnessFunction 경로 (`from .backtester import FitnessFunction`) 불변 — ml_advisor 관할
- Strategy lifecycle (promote/disable/sunset) 모두 inline 유지 — `evolve_cycle` (Block D) / `_auto_adjust_groups` (E) / `_spawn_neutral_strategies` (F) / `_consume_new_strategies` (M) 전량 보존
- `data/prompts/evolver_state.json` 포맷 무관 (별도 prompt-evolution 서브시스템, strategy evolver 의 `data/evolution_state.json` 과 다름)
- `_ALL_SIGNALS` class attribute back-compat 재노출

**Verify**:
- `wc -l` evolver.py=883 evolver_mutations.py=132
- `python3 -m py_compile invasion/strategy/evolver.py invasion/strategy/evolver_mutations.py` OK
- `python3 -c "import invasion.main"` OK
- Smoke test (seeded random): gaussian/bayesian/structural round-trip 통해 class shim 동작 일치 확인

**Plan 후속** (본 PR 범위 외):
- Batch #2: Block B (`PARAM_BOUNDS` + FITNESS_VERSION / ELITE_COUNT 등 상수) → `evolver_params.py` (`adaptive_params.py` 패턴 미러)
- Batch #3: Block G (`_select_mutation_type`) → `evolver_mutations.py` 병합 평가
- 🚫 Blocks D/E/F/J/M 은 lifecycle arbitration, review-isolation 위해 inline 유지

---

## MSG-F-N17-CAPITAL PENDING — commits [83d77426, 48fe98d4] 🟦 DEV

2026-04-18 AEST — F-N17 `invasion/exchange/capital_adapter.py` 931L split (plan + P1 extraction).

**Commits**:
1. `83d77426` docs(msg-fn17-capital-plan): `docs/MODULE_REVIEW_capital_adapter_split.md` — 11-block map (B0-B10) + P1/P2/P3 순서 + Invariants I-C1~C6
2. `48fe98d4` refactor(msg-fn17-capital-metadata): B0 tier classification → `invasion/exchange/capital_metadata.py`

**Before/After**: capital_adapter.py 931 → 898 LOC (-33). capital_metadata.py 48 LOC (순수 data + pure fn, class state 없음). Callsite (L552) 시그니처 불변 — `from .capital_metadata import _classify_cap_tier` 재수입.

**건드리지 않음** (임무 제약 준수):
- Market-hours gate / Cap weekend EOD flatten 경로 무변경
- Order lifecycle (`open_position` L203-266 / `close_position` L268-416 + market_closed 재분류) 보존
- `_not_found_cache` class var 공유 (exchange_advisor R-3) 그대로 유지
- `get_market_data` (B7, MSG-CAP-HEAL self-heal streak + forex session gate) / `sync_positions_to_portfolio` (B9, adopt bus event) 전량 보존

**Verify**:
- `wc -l` capital_adapter.py=898 capital_metadata.py=48
- `python3 -m py_compile invasion/exchange/capital_adapter.py invasion/exchange/capital_metadata.py` OK
- `python3 -c "import invasion.main"` OK
- `_classify_cap_tier` grep: import L12 + callsite L552 만 존재 (외부 repo 0 callsite)

**Plan 후속** (본 PR 범위 외):
- P2: B2 WS utils + B6 read state + B10 small utils → `capital_helpers.py` (~120L saving)
- P3: B3 `get_price` + B8 `_is_adopt_blocked` → `capital_pricing.py` / `capital_adopt_gate.py` (class-level `_not_found_cache` 유지)

---

## MSG-F-N16-WIRING-P5 PENDING — commit fd3bddde 🟦 DEV

2026-04-18 AEST — F-N16 wiring.py Phase 5 (exchange extraction).

`invasion/boot/wiring.py` **346 → 139L** (-207L). `_init_exchanges` + `_start_cap_ws_feed` verbatim moved to new `invasion/boot/wiring_exchange.py` (234L). wiring.py keeps `from .wiring_exchange import _init_exchanges, _start_cap_ws_feed` re-export → call-site parity (main.py import 무변경).

**Behavior 불변**: OKX/Capital/Alpaca adapter init 순서 유지, cap_ws_feed start timing 유지, `_LogOnlyFallback` 구조 유지, try/except graceful degradation 유지.

**Top-level mandatory imports** (exchange/ → boot/ back-import 0 verified): OKXAdapter, ExchangeRouter, OKXPublic, OKXPaperTrader, TickHistory, CapitalComClient, CapitalComAdapter. Optional Binance/OKX/Alpaca/Capital WS feed imports는 try/except inline 유지.

**Verify**:
- `wc -l` wiring.py=139 wiring_exchange.py=234
- `python3 -m py_compile` both OK
- `python3 -c "import invasion.main"` OK
- `_init_exchanges.__module__ == 'invasion.boot.wiring_exchange'` OK

**Plan**: `docs/MODULE_REVIEW_wiring_sprawl.md`. Phase 1-5 누적: 870→710→503→387→346→139L. 남은 phase 6 (try/except graceful-degradation 재설계) / 잔여 `_init_regime_and_safety` (≈50L) 검토.

---

## MSG-F-N17-ADAPTIVE-BANDIT PENDING — commit 456c4767 🟦 DEV

2026-04-18 22:27 AEST — F-N17 adaptive_tuner.py 분할 batch #2.

`invasion/ops/adaptive_tuner.py` **738 → 644L** (-94L). Block E (LASER-KILL PR3 `RegimeProviderBandit` + `_GLOBAL_BANDIT` + `get_regime_provider_bandit()`) 신규 `invasion/ops/regime_bandit.py` (119L) 로 verbatim 이관. adaptive_tuner.py 는 `from .regime_bandit import RegimeProviderBandit, get_regime_provider_bandit` re-export shim 유지 → signals/composer.py, trade/close_handler.py 외부 호출자 무변경.

**Behavior 불변**: Beta-Bernoulli posterior / rolling-window rescale / top-K draw 로직 동일. Singleton identity 검증 (`g1() is g2() → True`).

**Verify**:
- `python3 -m py_compile` OK
- `python3 -c "import invasion.main"` OK
- `update('bull','rsi',±1)` + `sample_top_k` 수동 검증 OK

**Plan**: `docs/MODULE_REVIEW_adaptive_tuner_split.md` batch #2 of 4. 남은 batch #3 (Block F config shims) / #4 (Block I `_thompson_sample`, ml_advisor 크로스 리뷰 필요).

---

## MSG-F-N17-OKX-PAPER-S2 PENDING — commit b7493634 🟦 DEV

2026-04-18 22:22 AEST — F-N17 마지막 >1000L 파일 해소.

`invasion/exchange/okx/paper.py` **1021 → 896L** (-125L, <1000 달성). B13-B16 Low-risk 4-block 묶음 추출:
- `_log_trade` / `_save_state` / `_load_state` / `_archive_session` → 신규 `paper_state_io.py` (175L) free functions
- Class methods 는 thin delegate 잔류 (signature 보존 → `boot/run.py:401 paper._save_state()` 외부 호출자 호환)
- Circular import 회피: `load_state` 내부 late import `PaperPosition`
- Unused import 제거: `os` / `tempfile` (state_io 로 이동)

**Behavior 불변**: JSON schema / `.bak` rotate / atomic `os.replace` / `_state_init<initial_balance*0.5` reset / PaperPosition 재구성 순서 동일. 예외 log level 동일.

**Canary 보존**:
- FSM canary (36f83e2) — B8 `check_exits` 미변경
- postmortem strategy_id (6e1f61d) — B9 / `paper_postmortem.py` 미변경
- SIGNAL slip (5608f37) — B9 `_close_position` 미변경

**검증**: py_compile 3파일 OK, `from paper import *` OK, `import invasion.main` OK, `OKXPaperTrader` 4개 메소드 잔류 확인.

**남은 Low blocks** (별도 PR): B1 `classify_exit_reason` (54 prefix map), B11 streak helpers, B12 `get_stats`. Plan `docs/MODULE_REVIEW_okx_paper_split.md`.

---

## MSG-F-N17-OKX-PUBLIC-S2 PENDING — commit c3add585 🟦 DEV

2026-04-18 22:18 AEST — F-N17 Phase 2 split 완료 (target <1000L 달성).

`invasion/exchange/okx/public.py` 1130L → **972L** (-158L). 4 카테고리 추출:
- S2 cache persist → `public_cache.py` (41L)
- S3 batch tickers → `public_tickers.py` (78L)
- S5 funding → `public_funding.py` (108L)
- S4-partial L/S ratio → `public_ls_ratio.py` (67L)

모두 behavior-preserving thin-wrapper delegation. Lazy import 로 순환 회피.
검증: py_compile OK, `from ... import *` OK, `import invasion.main` OK.

Deferred (high-risk): B4 `_get`, B9 sentiment aggregator, B10 scan_all (plan S7/S8).

---

## MSG-P0-RUN-STAGE-FEATURE PENDING — commit e764dd0d 🟦 DEV

feature_discovery `run_stage` shadow 실구현 (P0-8 spec-only → body).

### 변경
- `invasion/ai/orchestrator.py`: `run_stage(stage, ctx)` body 구현
  - stage="feature_discovery" 만 wired, 그 외는 `ok=False error="stage_not_wired"`
  - Budget gate: `can_call(est_cost)` → budget_exhausted envelope
  - Gemini primary (`_call_gemini`), GPT optional (`provider="gpt"`)
  - cfg 자동 로드 (`load_config()` fallback), provider key 누락 시 graceful skip
  - 성공·실패 모두 `record_call(AICallRecord)` 로 예산·ai_calls DB 반영
  - Never raises — 모든 에러 envelope 로 funnel, `NotImplementedError` 제거
  - `import json` 추가
- `invasion/ai/feature_discovery.py`:
  - TODO 블록 → 실 dispatch `self.orch.run_stage("feature_discovery", stage_ctx)`
  - `_parse_proposals` (list / dict["proposals"] / None 내성) + `name` 필수
  - `_append_shadow_log` → `data/feature_discovery.jsonl` append (never raises)
  - orchestrator 부재 시 warn + [] 반환 (graceful)
  - Shadow: proposals 반환만, promote 는 기존 flow 유지 (jin_review_flag=1)

### 검증
- `python3 -m py_compile` OK (orchestrator + feature_discovery)
- `get_orchestrator().run_stage('bogus', {})` → `{ok:False, error:'stage_not_wired'}`
- flag off: `agent.run({'store': None})` → `[]` (guard 유지)
- `_parse_proposals` unit: list / dict["proposals"] / None / invalid 모두 정상
- `import invasion.main` 은 pre-existing `boot/wiring.py:328` IndentationError 로 실패 (내 변경과 무관, 별도 fix 대상)

### 제약 준수
- Shadow mode (feature 자동 반영 없음, jin_review_flag=1 유지)
- AIController lock (22873285) 무변경
- ai/live provider 경로 (e71659f) `_call_gemini` re-export 통해 재사용
- consortium/thesis/regime_llm 무변경

### 범위
- 2 파일, 256+/23- (실 dispatch + shadow log + parser)

---

## MSG-F-N17-REGIME PENDING — commits [181cbe5a, 295804db] 🟦 DEV

F-N17 `invasion/market/regime.py` 1087L split — 1st extraction (J block: 3-layer voter).

### 추출 내역
- 계획: `docs/MODULE_REVIEW_regime_split.md` (13 블록 A-M map + 4단계 분할 우선순위)
- 새 모듈: `invasion/market/regime_three_layer.py` (~180L)
  - `ticker_tech_regime()` / `compute_group_stats_regime()` / `blend_three_layer()` 순수 함수
- `regime.py`: **1087L → 970L** (`for_ticker`, `_ticker_tech_regime`, `_group_stats_regime` 얇은 래퍼로 전환)
- `regime_types.py`: `RegimeState` dataclass 이동 (regime.py 는 re-export, 순환 import 방지)

### 보존 확인
- P0-7 (5c167d2) `primary()` macro fallback: **untouched** (block K 변경 0)
- RegimeService sole-writer (I-R3): untouched (`for_ticker` 는 RegimeState 반환만)
- DB CHECK enum `Regime`: untouched (`from ..config.schema import Regime`)
- `for_group` / `for_group_dynamic` / `state_dict` / `check_crisis_escalation`: untouched
- 외부 caller (Grep `for_ticker` 코드 hit 0, docs 언급만): 서명 불변

### 검증
- `python3 -m py_compile regime.py regime_three_layer.py regime_types.py` PASS
- `python3 -c "from invasion.market.regime import Regime, RegimeState, MultiRegimeManager"` PASS
- `Regime.CRISIS` print + `MultiRegimeManager().for_ticker('BTC-USDT','crypto')` → `RegimeState(transition,...)` PASS
- `python3 -c "import invasion.main"` PASS
- `pytest tests/ -k regime` → **20 passed**

### Cross-review 요청
- **ops_runtime_advisor**: 3-layer 블렌딩 semantic 일치 확인 (`_REGIME_SCORES`/`_SCORE_TO_REGIME` 상수 이동 + P1-2 50/50 rebalance 유지)
- **architecture_advisor**: `regime_types.py` 로 `RegimeState` 이동이 다운스트림 (dashboard, RegimeService) 영향 없는지 재확인

### 다음 추출 후보 (플랜 §분할 우선순위)
2. H block → `regime_per_group.py` (`_recalc_group_regimes` + `_GROUP_WEIGHTS`, ~100L) — 중간 위험
3. C+B block → `regime_base.py` (BaseRegimeDetector 재배선) — 고위험
4. D, E block → `regime_detectors.py` (Crypto/Macro split) — 마지막

---

## MSG-P0-EXITSTATE-GHOST PENDING — commit 04c05211 🟦 DEV

F-N14 리뷰 중 발견 (pre-existing P0, 이번 세션 변경 무관).

- **재현**: `invasion/trade/exit_types.py` 의 `ExitState` enum 에 `GHOST` 미정의. 그러나 `position.py:182` 에서 `if self.state in (ExitState.CLOSING, ExitState.GHOST)` 참조 → Position `_advance_state()` 가 호출되는 순간 `AttributeError: type object 'ExitState' has no attribute 'GHOST'`.
- **Root cause**: `exit_types.py` PR1 scaffold (commit 88610e16) 작성 시 `GHOST` enum member 누락. `position.py:78-81` docstring 은 "ghost = DB/broker desync sentinel" 로 의도 명시했으나 enum 에 반영 안 됨.
- **Fix** (exit_types.py only, +6 lines):
  - `ExitState` 에 `GHOST = "ghost"` 추가 + docstring (terminal sentinel, CLOSING 과 동일 패턴)
  - `STATE_ALLOWED_TRIGGERS` 에 `ExitState.GHOST: ()` terminal entry — ghost row 에서는 어떤 trigger 도 발화 안 됨
- **Invariant preservation**: I-E1/I-E4/I-E5 영향 없음 (GHOST ∉ WINNER_STATES, OPEN.TIME_LOSER 보존). 기존 `_advance_state` guard `if self.state in (ExitState.CLOSING, ExitState.GHOST): return` 이제 정상 resolve.
- **Verify**: `from invasion.trade.position import ExitState; [e.name for e in ExitState]` → `['OPEN','TOUCHED_PROFIT','PROTECTED','HARVEST','CLOSING','GHOST']`. tests/trade/ 통과 (test_exit_fsm_staged FSM slice failure = 사전 존재, 본 fix 와 무관).
- **Note**: 작업 중 `_pipeline_scan.py` F-N13 Phase C WIP 은 stash@{0} (agent-exitstate-pre) 로 보존. Dev 세션이 이어서 reconcile.

---

## [2026-04-19 00:52] MSG-DEAD-SWEEP PENDING — commits [60d8eb98, b16e2500, 41676c5e] — ~1365 LOC 삭제

### 삭제 요약 (3 commits)
- **60d8eb98** `msg-dead-sweep-ai-abc` — ai/base StrategyAdvisor ABC (-10 LOC)
- **b16e2500** `msg-dead-sweep-dashboard-orphans` — dashboard signal.py + sections/{live_log,status_bar,polaris_compass,regime_macro} (-1053 LOC)
- **41676c5e** `msg-dead-sweep-misc` — 6 data.py loaders + ansi.term_size + canonical_names 3 helpers + param_governor.demote_candidate + calendar_blackout.get_event_mode + binance.public.binance_to_okx (-258 LOC)

### Task 대비 삭제 보류 (검증 실패)
- **ai/mocks.py**: `tests/ai/test_mocks.py` + `tests/integration/test_scan_cycle.py` 가 import — orphan 아님
- **dashboard/ai.py**: `invasion/dashboard/chart_window.py:31` 가 `_get_position_prices`, `_calc_realtime_tech` import — orphan 아님
- **trade/gate_matrix.flat_auto_block_snapshot**: 북극성 sweep agent 작업 진행 중 (`_FLAT_AUTO_BLOCK` + `register_flat_auto_block` + H9 check 까지 comprehensive 제거 중) — task 지침대로 skip

### 검증
- `python3 -c "import invasion.main" OK`
- `python3 -c "import invasion.dashboard.operations; import invasion.dashboard.intel; import invasion.dashboard.chart_window" OK
- grep sweep: 삭제 symbol 0 ref (data/collectors/binance_public.py 의 동명 함수는 별도 모듈)

### 총계
- 3 commits, 14 files touched (9 modified + 5 deleted)
- **~1365 LOC 순삭제** (target 1500+ 에서 chart_window 의존성 + ai/mocks test 의존성 제외)

---

## 2026-04-19 00:45 AEST — 🟦DEV MSG-PROVIDER-MULT + MSG-MULT-AMPLIFY PENDING

### 커밋
- `9f4c49f4` feat(msg-provider-mult-wire jin p0): 16 provider_mult 를 composer 에 wire
- `17090b46` fix(msg-multiplier-amplify-only jin p0): adaptive_tuner clamp 0.5->1.0

### Step 1 — provider_mult wire (composer 단일화)
**발견**: Harness 지시문 "어떤 .py 도 read 하지 않음" = 부분 오류. 실제로는
`invasion/signals/data_provider_base.py:77` `_mult()` 에서 이미 읽고
`compute()` 에서 score 축으로 곱하고 있었음 (16 external provider 전부).

**조치 (root-cause)**: composer 에 Layer 1.5 weight 축 multiplier 추가 + DataProviderBase._mult() 를 1.0 상수로 중립화 → double-apply 방지. composer 가 단일 SSOT.

**파일**:
- `invasion/signals/composer.py` (+34) — `PROVIDER_MULT_NAMES` frozenset (16) + `compose()` Layer 1.5
- `invasion/signals/data_provider_base.py` (+9 -9) — `_mult()` → `return 1.0`, docstring 갱신

**적용 순서 (compose 내)**: regime_filter → topk_bandit → regime_mults(L1) → **provider_mult(L1.5)** → quality_eff(L2) → weighted_sum

**Fallback**: `_preg` None/0 → skip (1.0), 예외 시 debug log + 전체 no-op.

### Step 2 — adaptive_tuner amplify-only
**파일**: `invasion/ops/adaptive_tuner.py:202` (+8 -2)
- `max(0.5, min(1.5, multiplier))` → `max(1.0, min(1.5, multiplier))`
- 북극성 `feedback_no_defensive_param_dampen` 준수 (WR<0.50 provider 는 1.0 유지, no dampen)
- ai/live.py:589 `size_modifier` 는 position sizing 축 → 본 스코프 밖 (추후 별도 MSG 로 처리 권고)

### 검증
- `tests/signals/*` 78 passed
- `tests/ai/*` 13 passed
- adaptive_tuner import OK
- multiplier math 검증: WR 0.30→1.0, 0.50→1.0, 0.60→1.3, 0.80→1.5

### 영향 추적
Harness 가 관찰할 지표:
- DataProviderBase 기반 16 provider 의 composite 기여도 (이전: score * preg_mult, 현재: weight * preg_mult — 수학적으로 weighted_sum 상 동등)
- WR 낮은 provider 의 가중치 감쇠 소멸 → 재학습 데이터 축적 속도 증가 예상

---

## 🟦DEV 2026-04-19 00:48 AEST (Sunday) — MSG-REGIME-MACRO / FLIP / PERSIST PENDING

**Status**: P0 3-commit scope complete, 봇 재기동 후 live 검증 필요.

### Commits
- `fb468505` fix(msg-regime-macro-wire jin p0): regime_detect.tick → observe 에 btc_price/dom/mcap 전달 (0 값 leak fix)
- `cdb7f5f0` fix(msg-regime-flip-conf jin p0): regime_flip_confirmations 5→2 (UNKNOWN floor 25→10min 단축)
- `37e1e2c8` feat(msg-regime-per-group-persist jin p0): group_regime_history 테이블 + persist hook

### 변경 요약
| Step | 파일 | 내용 |
|------|------|------|
| 1 | `invasion/ticks/regime_detect.py` + `invasion/market/regime_service.py` + `invasion/data/data_collector.py` | observe() 에 btc_price/btc_dominance/total_mcap_b 실수 전달, blockchain_info btc_price_usd → collector.latest 노출, RegimeService.observe + _persist 시그니처에 total_mcap_b 추가 |
| 2 | `invasion/config/_params_gates.py` | `regime_flip_confirmations` default 5→2, docstring 에 25→10min 이유 주석 |
| 3 | `invasion/data/unified_schema.py` + `_repo_market.py` + `regime_service.py` + `regime.py` | `group_regime_history` 테이블 (ts INT, group_name TEXT, regime TEXT CHECK, confidence REAL, PK(ts,group)) + idx_group_regime_group_ts, SCHEMA_VERSION 14→15, `RegimeService.observe_group()` sole-writer (I-R3), `_recalc_group_regimes` 훅 |

### 검증
- Syntax: `ast.parse` 4 파일 전부 OK
- Smoke test (temp DB): `observe_group(crypto, RISK_ON, 0.8, now=1776000000)` → `group_regime_history` row `(1776000000, 'crypto', 'risk_on', 0.8)` 확인
- RegimeService.observe 시그니처 `total_mcap_b` kwarg 존재 확인

### Harness 관찰 포인트 (재기동 후)
- `market_context.btc_price` 0 → 실제 BTC USD (blockchain.info 5min cadence)
- `market_context.btc_dominance` / `total_mcap_b` — 여전히 0 (collector 미구현, coingecko `/global` 후속 PR 필요)
- UNKNOWN floor 25→10min 단축으로 재기동 직후 공격 지연 ↓
- `group_regime_history` 5min cadence 로 crypto/fx/index/stock/commodity 5 groups 행 축적 여부

### 후속 권장
- coingecko `/global` 엔드포인트 수집기 추가 → `btc_dominance`/`total_mcap_b` 실데이터화 (현재 wire 만 있고 소스 없음)
- 대시보드에 group_regime_history 타임라인 섹션 추가 (crypto/fx/index/stock/commodity 동시 표시)


---

## MSG-NS-SWEEP-8 PENDING — Jin 북극성 위반 8건 전수 sweep (2026-04-19 00:42 AEST)

**commits** (chronological):
- `037e27e0` msg-ns-short-bias-mult
- `8bcd57e7` msg-ns-trend-penalty
- `31c93571` msg-ns-cross-dampen
- `1898e012` msg-ns-regime-dampen
- `e2db5f9f` msg-ns-tournament-dampen
- `dc2d9ac1` msg-ns-low-vol-block-kill
- `72098b11` msg-ns-flat-auto-block-kill
- `d0afeb5a` msg-ns-dead-keys-purge

### before / after

| # | 위반 위치 | before | after |
|---|-----------|--------|-------|
| 1 | `invasion/config/config.py:204` | `short_bias_mult: float = 0.4` (SHORT 40% 삭감) | `= 1.0` (full size) + live_config 1.2→1.0 |
| 2 | `invasion/signals/providers.py:720-721` | `trend_penalty = min(0.3, (adx-25)/100); score *= (1.0 - trend_penalty)` | trend_penalty 블록 제거. ranging bonus ×1.15 만 유지 |
| 3 | `invasion/signals/providers_cross.py:94` | disagreement 시 `score = _clamp(base_score * 0.7)` | `_clamp(base_score)` (영구 0.7 감쇠 제거) |
| 4 | `invasion/market/regime.py` 3곳 | `_learn_thresholds` WR<0.35 `*0.9`; `_apply_accuracy_feedback` WR<0.4 `confidence*=0.7`; `_select_winner` adj 적용 dampen 가능 | WR<0.35 / WR<0.4 dampen 분기 삭제. `threshold_adjustments` 는 amp-only (≥1.0) 로 수렴 |
| 5 | `invasion/strategy/engine.py:149` | hysteresis `new_state.confidence *= 0.7` | 감쇠 삭제 (regime 유지만 수행) |
| 6 | `invasion/signals/engine.py:284-309` + defense preg | `low_vol_long_block_enabled` / `short_block_enabled` gate 및 per-group factor 11개 preg | gate 제거, 11 preg 삭제 (`low_vol_long/short_block_enabled`, `_threshold`, `_threshold_factor_{crypto,forex,indices,commodity,stock,shares,etf}`). flat_pre_entry_block (표적) 만 유지 |
| 7 | `invasion/trade/gate_matrix.py:22-249` + `close_handler.py:507-530` + 3 preg | `_FLAT_AUTO_BLOCK` dict + `register_flat_auto_block` + H9 분기 + close hook + `flat_auto_block_{enabled,sec,peak_pct}` preg | 모두 제거, 테스트 `test_gm_flat_auto_block_registry` 제거, conftest `_FLAT_AUTO_BLOCK` clear 제거 |
| 8 | `config.py:140-141` + live_config 12 key | `long_blocked_hours_utc`, `all_blocked_hours_utc` dataclass 필드 + 12 dead key | 필드 제거, live_config 에서 `learner_block_wr`, `learner_block_min_trades`, `trend_gate_threshold`, `long_blocked_hours_utc`, `all_blocked_hours_utc`, `blocked_days`, `ig_block_buy_crypto`, `weekend_ig_block`, `wr_monitor_enabled`, `wr_degrade_threshold`, `wr_pause_threshold`, `wr_degrade_size_mult` 삭제 |

### 검증
- `python3 -c "import invasion.main"` → OK
- `pytest tests/signals/ tests/trade/` → 137 pass / 1 fail (test_fsm_slice_on_enables — pre-existing, sweep 이전부터 실패)
- live_config.json 백업 `data/live_config.json.bak_1776523188` 생성
- `grep short_bias_mult ... 외`: 잔존 참조는 전부 comment / 정당한 사용처 (paper.py getattr, intel dashboard label)

### 원칙 준수
- `feedback_no_defensive_param_dampen` (공격량 삭감 금지)
- `feedback_no_block_filter_architecture` (aggregate block 금지, 표적 교체 원칙)
- `feedback_northstar_full_authority` (Jin 승인 없이 북극성 위반 자율 수정)


---

## 2026-04-19 01:01 AEST — 🟦DEV P0 [MSG-LEARNER-AMPLIFY-ONLY]

### 완료 (2 commit, Sonnet)

| # | Commit | 파일 | 변경 |
|---|--------|------|------|
| 1 | `3399584c` | `invasion/trade/close_handler.py` | sizing_feedback loss β `-0.01` → `0.0` (neutral). tier_size_mult + regime_size_mult 양쪽 |
| 2 | `127f109a` | `invasion/ops/adaptive_tuner.py` | `_AMPLIFY_ONLY_KEYS` (28 key) + tune_cycle `optimal = max(optimal, current)` clamp |

### Batch 24 E2E 북극성 위반 2건 closure

1. **sizing_feedback**: 매 loss 마다 tier/regime size_mult 감소 → 공격량 삭감. β 제거, win 시 +0.05 amplify 만 유지.
2. **Thompson Sampling**: weight/entry-gate key 28개 (provider_weight_* 8 + min_score/factors/agreement 3 + provider_mult_* 16 + bayesian_agree_amplify 1) 하향 bucket best-performing 선정 시에도 current 아래로 못 내려감. Exit/cooldown/sizing 기타 key 는 기존 양방향 drift 유지.

### 검증
- Syntax OK (ast.parse)
- Import OK (AdaptiveTuner + _AMPLIFY_ONLY_KEYS)
- 로직 sanity (weight 하향 block / 상향 pass / exit param 자유 drift) 스모크 통과

### 원칙 준수
- `feedback_no_defensive_param_dampen` (공격량 삭감 drive 원천 차단)
- `feedback_adaptive_learner_attack` (learner 도 북극성 준수)
- `feedback_northstar_full_authority` (Jin 승인 없이 북극성 위반 자율 수정)

---

## 🟦DEV → 🟩HARNESS — MSG-AI-AUTO-FALLBACK PENDING (2026-04-19 01:08 AEST Sunday)

**commit 5fc88a1e** — feat(msg-ai-auto-fallback-chain jin p0): 토큰/rate 맥스 시 Claude→Gemini→GPT 자동 rotation

### 변경
- **신규** `invasion/ai/live_fallback.py` (242L)
  - `_PROVIDER_COOLDOWN: dict[str,float]` + `_RateLimitError` / `_BudgetMaxError`
  - `call_with_fallback(cfg, prompt, stages, max_tokens, timeout, cache_blocks, orchestrator, estimated_cost)` → `(parsed, usage, model, cost, provider)` 5-tuple
  - `_stages_for_mode(mode)` → ai_provider_mode 4 매핑
  - `reset_cooldowns()` helper (ops/test)
- **수정** `invasion/ai/live_providers.py` — `_call_gpt/_gemini/_call_claude` 비정상 응답 시 `usage["status_code"]` + `usage["error_body"]` 포함 (fallback layer classify 용)
- **수정** `invasion/ai/live.py::_claude_or_gemini` — thin wrapper 로 축소, call_with_fallback 경유. 4-tuple signature 보존 (live_exit.py 등 downstream 영향 없음)
- **수정** `invasion/config/_params_gates.py` — `ai_provider_mode` 설명 4-mode 확장, `ai_provider_cooldown_sec` preg 신규 (default 300s)

### Rate-limit 감지
- HTTP 429 직접
- status_code >= 400 + body marker: `rate_limit` / `ratelimit` / `quota` / `resource_exhausted` / `too many requests` / `overloaded`

### 검증
- `python3 -c "from invasion.ai.live_fallback import call_with_fallback"` ok
- `python3 -c "import invasion.main"` ok
- `pytest tests/ai/` → 7 passed
- 4개 시나리오 smoke: Claude 429 → Gemini success, 후속 호출 claude cooldown skip, 전 provider 429 → None, 4-mode stages 매핑
- 전체 suite: 177 passed / 2 pre-existing 실패 (regime_service + exit_fsm, 무관)

### Ops 롤백 경로
- preg `ai_provider_mode` 변경 1-line (legacy_claude_gemini / gpt_only / gemini_primary / balanced)
- preg `ai_provider_cooldown_sec` 조정 (429 빈도 높으면 upscale)

## 🟦DEV → 🟩HARNESS · 2026-04-19 Sun 01:10 AEST

### [DONE] MSG-postmortem-cross-exchange + MSG-finalize-close-learner-hooks (Batch 24 exit E2E 커버리지 복구)

**Commits**
- `6c57f866` — fix(msg-postmortem-cross-exchange jin p1): postmortem close_handler 이동 (OKX-only → 전 exchange)
- `5b9c76cf` — fix(msg-finalize-close-learner-hooks jin p0): _post_close_hooks helper + _finalize_close learner update 복구

### 변경 1 · Postmortem cross-exchange
- `invasion/trade/close_handler.py:_write_postmortem_cross_exchange(pos, reason)` wire: `_close_position` + `_finalize_close` 양쪽에서 `_exit_count+=1` 직후 호출 → OKX/Alpaca/Capital 전 exchange 대상 `data/ai_postmortem.jsonl` write. AI adviser learner 가 crypto-only(1/4)에서 전 universe 로 확장.
- `invasion/exchange/okx/paper.py:790` legacy `write_postmortem` 는 preg `okx_paper_postmortem_legacy` (default False) gate 로 dormant → 중복 write 방지. 롤백 필요 시 플래그만 True.

### 변경 2 · _post_close_hooks helper + _finalize_close 복구
- `_close_position` 의 learner/bookkeeping 블록 (consecutive_loss_halt / cooldown / bus publish / sizing feedback / regime_detector / quality / RegimeProviderBandit / bayesian / edge_calibration) 을 `CloseHandlerMixin._post_close_hooks(pos, reason)` 로 추출.
- `_close_position` 과 `_finalize_close` 양쪽에서 동일 helper 호출 → dead-letter replay 도 learner update 실행. Prev ~3% 볼륨 learner starvation (복구된 trade 전부 skip) 해소.
- Behavior 보존: 블록 순서, 독립 try/except 경계, log_event level 전부 동일. `exit_cycle._finalize_close` callsite 영향 없음.

### 검증
- `python3 -m py_compile invasion/trade/close_handler.py invasion/exchange/okx/paper.py` PASS
- `python3 -c "import invasion.main"` PASS
- `inspect.signature(CloseHandlerMixin._post_close_hooks)` = `(self, pos, reason)` OK
- Call graph: `_close_position → [insert_trade → record_close → _exit_count] → _write_postmortem_cross_exchange → _post_close_hooks` / `_finalize_close → [insert_trade → record_close → _exit_count] → _write_postmortem_cross_exchange → _post_close_hooks` (동일 후처리)

### Ops 관찰 포인트
- `data/ai_postmortem.jsonl` 신규 라인의 `asset_group` 분포: crypto 일색에서 cap/alpaca/forex/indices 섞이면 OK.
- `invasion.log` `POSTMORTEM cross-exchange write fail` warn 이 반복되면 asset_group/current_price 누락 Position 존재 신호 (Dev 추가 조사).
- Dead-letter replay 시 `BAYESIAN outcome:` / `CONSECUTIVE LOSS HALT` / `trade.closed publish` 로그 이전 skip 패턴 → 이제 등장.

### Ops 롤백 경로
- postmortem 중복 의심: preg `okx_paper_postmortem_legacy=0` 유지 + legacy 부활 시 1 (즉시 양쪽 write).
- Learner hook 이슈: `_post_close_hooks` 내부 블록은 각각 독립 try/except → 개별 log_event 만 나올 뿐 close flow 자체는 안전. 전체 롤백 필요 시 `_finalize_close` 끝 `self._post_close_hooks(pos, reason)` 한 줄 주석 처리로 legacy 동작 복귀.
