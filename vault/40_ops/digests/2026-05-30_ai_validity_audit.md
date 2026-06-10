---
type: digest
status: active
date_created: 2026-05-30
tags: [digest, ai-validity, forensic, p0-bugs]
related: [[north-star]], [[_NOW]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], `project_operating_thesis_surgical_strike`
---

# AI 사용 타당성·정당성 감사 (Jin 명시 요청, #27)

read-only 포렌식 (live DB `polaris_live.sqlite` 4.1h/5,692 gate_events + 코드 + auto_invasion mk1 참조). 5-facet 워크플로우 `w42z5byx3`.

## 판정: PARTIAL / INVERTED
AI 구조는 건전하나(state-machine 격리·fail-mode split·G5 pure-python risk·P1 cost-gating), **AI 배분이 surgical-strike 목적과 역전**. GPT 99.3%(2,597/2,615 call/4.1h)가 entry-side housekeeping(G1/G3/G4)에 — OKX에선 변별력 0, Capital에선 payload 버그. Jin이 명시한 정밀 lane(엑싯·레짐)은 inert/미구축.

## 정당 (유지)
- **G6/G7/G8 P1 cost-gating** (gpt-5.5, 0.8% call rate, price-delta/regime/PnL-band 트리거) = 모델배치 gold standard
- 모델배치 방향 (mini P0 / 5.5 P1, 33x 가격차 vs 143:1 call비)
- **G2/G5 pure Python** (0ms, 9-stack 보존)
- **G8 Reflector→ai_lessons→learner posterior** = 유일하게 작동하는 AI 피드백 루프
- 총 비용 trivial (~$109/mo, $0.11/round-trip)

## 낭비 (컷 = 효율/속도, 방어 아님)
- **G1 Universe Scanner = 56.5% 비용**인데 구조상 항상 PASS(focus selector, non-decision), 출력 malformed(혼합 ID/중복). → deterministic_top_n default + on-change GPT
- **G3/G4 OKX = rubber-stamp** (0 KILL/~1,654 call). → python pre-check 게이팅
- **G7 = HOLD-stamp** (~100% HOLD, 프롬프트에 시장 컨텍스트 0)
- prompt bloat (SCANNER_CAP=300행 vs focus 12-48)

## 부족 (강화 = 정밀도, Jin 목적)
- **엑싯 G7** (Jin 1순위): 프롬프트에 stop+pnl_r만, 시장 컨텍스트 0 → 정밀 엑싯 불가
- **레짐**: price-only(100bar return+Kaufman ER+DD), evidence_json='{}', alt-data 0
- **멀티소스 fusion / Bayesian realized-edge calibration / multi-TF consensus** 부재

## 🔴 P0 트레이딩 버그 (말이 되는 봇의 전제 — AI보다 우선)
1. **FIX-3 OKX zero-fill**: 752 OKX 신호 모든 게이트 통과+SIZED인데 fill 0 (intent가 venue 미도달 추정) — 진단 필요
2. **FIX-4 same-bar close**: 모든 close `holding_bars=0` (SL 너무 타이트 or bar-clock 버그) → 멀티바 엑싯 AI 영구 bypass — 진단 필요
3. **FIX-1 Capital payload-wiring**: payload_builder가 G3/G4에 빈 cell/baseline(n_eff=0) 먹임, 데이터는 존재(ticker_baseline_state 105 forex/bars 35 Capital) → Capital baseline·cell wire
4. **FIX-2 G6 degraded**: `_production_recalc._evaluate_position` volume_now=0.0/recent_ticks=[] 하드코딩 → 실 bars 전달
5. **INSTR**: (1) signal_id→position_id 링크 부재(AI 효과 측정 불가) (2) 토큰 미기록(GPTCallResult엔 있음) (3) G3 raw GPT reason 미기록

## Build backlog (우선순위)
FIX-3/FIX-4 진단+수정 → FIX-1/FIX-2/INSTR → #26 G7 rich-context+MFE/MAE counterfactual+tiered trailing FSM → alt-data regime evidence layer(auto_invasion 가중스코어 패턴, signal not throttle) → AI 효율(G1 determ default/G3·G4 python pre-check) → 최종 재기동+live-audit.

## auto_invasion mk1 차용 (북극성 정합)
weighted-scoring regime engine · alt-data provider layer(F&G/funding/OI/VIX/sentiment, TTL캐시) · 멀티소스 evidence fusion(GPT가 rubber-stamp 아닌 arbitrate) · multi-TF consensus · Bayesian edge calibration · tiered trailing exit FSM.

## Caveat
4.1h/6 fill(전부 Capital US30 same-second ~$0) 소표본 · OKX fill-side 미관측 · 비용 추정치(토큰 미기록) · entry-AI↔outcome 측정불가(링크 부재) · archive(383k)는 구 아키텍처(Anthropic-credit KILL — 현재 차단됨). 권고 전부 precision/evidence/efficiency, 거부키워드 0·방어throttle 0.
