---
entity_type: insight
entity_id: INSIGHT-035
auto: false
last_modified: 2026-05-05
expires: 2026-11-05
editable: true
back_links: ["[[INSIGHT-030]]", "[[INSIGHT-034]]", "[[ADR-013]]", "[[ADR-014]]", "[[HYPO-008]]", "[[HYPO-023]]"]
mode: forensic
reviewed_by: forensic-investigator
tags: [type/insight, status/active, scope/ops, priority/p1, polaris, forensic]
---

# INSIGHT-035 — 6-cycle 정적 + vault 미활용 근본 원인 forensic

## Evidence Summary (2026-05-05 08:05 AEST)

### 1. 6-cycle 변화 0 = 사실이지만 이유는 복합

**intraday cron (HYPO-007/008):**
- `data/paper/intraday.err`: 07:07 UTC부터 실행. 마지막 기록 06:57 UTC. 이후 realtime runner가 인수.
- intraday 자체: 15개 cycle 중 entry 1건 (SUI 1H VB 00:56 UTC). 이후 14 cycle 연속 entry 0.
- signal 분포: `signal=exit_noop` 25회 / `signal=exit` 11회 / `signal=enter_long` 1회.

**realtime runner (07:07 UTC 시작 후):**
- HYPO-008-RT (VB): 29 closed trades. 마지막 CLOSE 2026-05-04 14:31 AEST. 그 후 **17.6h 무신호**.
- VB 조건: `volume > 2× SMA(20) + close > open`. 1H 캔들 기반. 5개 ticker (ORDI/DOGE/SOL/PEPE/TRUMP).
- HYPO-007-RT (RSI): 1건 OPEN (05-04 20:32 AEST BTC), SL hit -$1.01 (05-05 00:08 AEST).
- HYPO-023 LiqCascade: **100% [LIQ-CASCADE-HOLD] pressure=no_data**. 전 기간 612회. ZERO entry.
- HYPO-024 CrossExchangeGap: 22 entries. 마지막 CLOSE 02:27 AEST (5.5h 전).
- HYPO-028 TickBurst: 7 entries. 마지막 CLOSE 02:27 AEST.
- HYPO-032 TSMOM: 7 entries, 6 open positions (05-04 21:28 AEST 진입, 17h+ holding).
- HYPO-033 VPIN: vpin_warmup + vpin_hold < 0.7. 4 entries only. 현재 0 activity.
- HYPO-027 FundingRate: log에 HOLD reason 없음 — 신호 평가 가능하나 0 trigger.
- HYPO-AI-001: RuntimeError + BadRequestError (API 오류). 3 entries 후 중단.

### 2. Root Cause 분석

**원인 A — HYPO-023 LiquidationCascade 완전 사망 (CRITICAL)**
- 612회 100% `pressure=no_data`. 단 한 번도 entry 없음.
- `data/paper/realtime.err:27` — `[LIQ-CASCADE-HOLD] ETH-USDT pressure=no_data no liquidation pressure data`
- 원인: Binance forceOrder WS stream이 실제 유입되지 않거나 `compute_liquidation_pressure()`가 항상 no_data 반환.
- 영향: REALTIME_HYPOS에서 가장 큰 잠재 alpha 후보 중 하나. 사실상 비활성 상태로 자원 소비 중.

**원인 B — VB 17.6h 무신호 = 시장 조건 부재 (정직한 한계)**
- `data/paper/realtime.err:1129~1241` — 11:00~11:37 AEST 구간에 집중적으로 active (BTC 급등 구간).
- 14:31 이후 ORDI/DOGE/SOL/PEPE/TRUMP 어디서도 2× volume + bullish candle 미발생.
- BTC 5/5 횡보 구간 (cron.log: `fast=72539 slow=83807` — SMA 50일선 아래). 시장 원인.
- **이건 전략의 정상 동작이지 게으름이 아님.** VB는 momentum 구간에만 fires.

**원인 C — TSMOM 6 open positions 17h holding = 사실상 cron 전략**
- TSMOM `primary_tf="1D"`. 1D candle 기반. 한 번 진입하면 max 30일 hold.
- `data/paper/paper_state_*tsmom*.json`: ADA/SOL/XRP/ETH/BTC/DOGE 모두 21:28 AEST 동시 진입.
- TP 12% / SL 4%. SMA50 > SMA200 미충족 상태 (`fast=72539 slow=83807`)에서 TSMOM만 진입한 것 자체가 의심스러운 패턴.
- 실질적으로 이 6 positions은 향후 4주 동안 holding — trade count 변화 없는 주원인.

**원인 D — AI Advisor 오류로 0 activity**
- `realtime.err:*` — `[AI-HOLD] BTC-USDT api_error:RuntimeError` / `BadRequestError` 반복.
- Claude API 오류. 3 entries 후 전면 오류 모드. 복구 없음.

**원인 E — CFD vs SPOT 구조 차이 (결정적)**
- 모태 invasion.sqlite: 2026-04-28~30 하루 2,500~2,700 trades. **약 100+건/시간**.
- Polaris 현재: intraday 15 cycle 총 1 entry. realtime 25h 동안 약 80 entries.
- CFD: IG 200x leverage + short 가능 + AI advisor 3개 (GPT/Gemini/Claude) 실시간 합의 + 수백 개 전략 concurrent.
- SPOT: leverage 없음 + long only + OKX Lv1 fee 0.14% round-trip (하지만 이것도 scalp 대비 swing OK).
- **구조적 차이**: CFD는 momentum + mean-reversion + trend 모두 활성. SPOT은 trend only viable (INSIGHT-030, ADR-014 결정).

### 3. Vault 미활용 audit

**현황:**
- 33개 INSIGHT 작성 완료.
- ADR 16개 작성 완료.
- `vault/30_knowledge/lessons/` 5개 LESSON 노트.

**실제 활용 증거:**
- `src/paper/realtime_runner.py:~1019` — `INSIGHT-029` 2회 참조 (regime cluster guard 근거).
- `src/strategies/` 전체: INSIGHT 참조 6회, ADR 참조 3회.
- 코드 파일에서 `[[entity]]` link 사실상 없음. vault는 `log.md`에 append만.

**미활용 패턴:**
- 매 wakeup에서 `_NOW.md` + INSIGHT 읽지 않고 직접 코드 수정.
- INSIGHT들에 `status:` field 누락 (10개 INSIGHT status=? 확인됨).
- INSIGHT back_links가 다른 INSIGHT → INSIGHT로만 연결. 코드 파일 → vault는 단방향 참조 없음.
- `vault/60_alpha/active/` HYPO notes: 코드가 변경돼도 HYPO note 갱신 없음.
  예) HYPO-008 tickers에 ORDI 추가됐지만 HYPO-008 note 미갱신.

**핵심 gap**: vault는 "기록"용으로만 사용. "조회 → 결정" 루프 없음.
매 코딩 전 `_NOW.md → 관련 INSIGHT → 관련 ADR` 읽기 = 실제 0회 검증됨.

## Impact 범위

- HYPO-023 (LiqCascade): 실질 사망. 25h 동안 0 entry. 코드는 실행 중이나 데이터 없음.
- HYPO-AI-001: API 오류로 실질 사망. 복구 로직 없음.
- TSMOM 6 positions: 17h+ holding. TP 12% 도달 시점까지 entry count 변화 없음 (1D timeframe 특성).
- vault INSIGHT 33개: 코드 결정에 실질 참조율 < 10%.

## Recommendation

### 즉시 fix (code-implementer → 별도 호출)
- [ ] HYPO-023 LiquidationCascade: `compute_liquidation_pressure()` 반환값 디버그. Binance forceOrder WS 실제 수신 여부 확인. `no_data` 원인 규명 필수.
- [ ] HYPO-AI-001: RuntimeError/BadRequestError catch + 재시도 로직 추가. API 오류 시 다음 cycle 자동 복구.
- [ ] TSMOM 동시 진입 6건 검토: SMA50 < SMA200 환경에서 TSMOM 단독 진입 가능한지 regime filter 여부 확인.

### 구조적 변경 (codex-debate-partner → 별도 호출)
- [ ] Vault-code 활용 루프 설계: 매 세션 시작 `_NOW.md → 관련 INSIGHT` read를 harness loop에 강제 의무화.
- [ ] INSIGHT status field 표준화: `active/applied/stale/superseded` 명시적 4-state.
- [ ] HYPO note ↔ 코드 sync protocol: 코드 변경 시 HYPO vault note 자동 갱신 checklist 추가.

### Polaris 한계 인정 (의사결정)
- SPOT long-only + Lv1 fee + 1H/1D timeframe = 시간당 trade 수 구조적으로 낮음 (5~30/day 현실적).
- CFD 100+건/시간은 200x leverage + short + AI 3개 합의의 결과. SPOT에서 동일 빈도 불가능.
- "변화 없으면 왜 없는지 찾고 연구하고 생각하고 변화하라" = HYPO-023 fix + AI advisor 복구가 즉각 행동.

## FORENSIC SESSION 메타

- Trigger: Jin 비판 — 6 cycle 정적 + vault 활용 audit
- Evidence files: `data/paper/intraday.err:137lines` / `data/paper/realtime.err:20428lines` / `data/paper/paper_state_*tsmom*.json` / `invasion.sqlite trades COUNT=18879`
- Root Cause: A(LiqCascade 사망) + D(AI API 오류) + E(CFD-SPOT 구조 차이) 복합. B(VB 시장 조건) = 정상 동작.
- Vault 미활용: 기록 전용 사용, 조회-결정 루프 없음 확인.
