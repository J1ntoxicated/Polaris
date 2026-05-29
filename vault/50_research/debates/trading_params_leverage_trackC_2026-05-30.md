---
type: debate
date: 2026-05-30
round: 1
topic: Capital CFD per-market leverage + Track C (Alpaca US-equity spot) caps
verdict: partial-consensus (round 2 needed on #2 gross-cap basis)
context: DEMO/PAPER · AGGRESSIVE bias · 9-stack 영구봉쇄 · hard-MAX headroom_min() 불변
---

# 트레이딩 파라미터 — Capital leverage + Track C 캡 (round 1)

## 결정 #1 — Capital CFD flat 30.0 → per-market. **합의: 전환 권고 (강)**
A·B 완전 일치. flat 30은 "공격적"이 아니라 **오사이징**이다.
`engine.py:453 notional = final_risk_pct × equity × intent.leverage` — leverage는 clip chain
**이후** 순수 notional 배수 (9-stack 곱셈 mult 추가 0, 확인). flat 30 결함:
- 지수/원자재 30/20 = **1.5× 과대**, crypto-CFD 30/2 = **15× 과대 notional**
- venue 실 marginFactor 불일치 → insufficient-margin reject(51008류, 핸들러 grep 0건)
  → fault path 무체결 + fill_normalizer notional 1.5~15× 왜곡 → edge/learner 오염
- 즉 flat 30 = 체결 못 하는 공격성 = 약함. per-market은 체결률(분자)을 올린다.

**권고값 (합의):** `_production_run_signal.py:124` flat 제거 → translator가 이미 읽는
per-epic `intent.leverage` 사용 (`constraint_translator.py:129-133` 권위값).
fill_normalizer에도 동일 per-epic leverage 전달 의무(`:169` 기록 정확성).
**폴백(둘 다 0/누락 시 현재 0.0 반환 = 무체결 버그):** instrument_type 매핑
CURRENCIES 30 / INDICES 20 / COMMODITIES 20 / CRYPTOCURRENCIES 2. **0 폴백 금지.**
불변 확인: 9-stack 0 추가, hard-MAX·headroom_min·0.09 ceiling 불변, aggressive는 risk_pct/cap에서 유지.

## 결정 #2 — Track C (Alpaca US-equity spot) 캡. **부분 합의 + 1개 핵심 이견**
**합의:**
- Track C는 코드 미존재 (`schema.py:202 Track=Literal["A","B"]`) → schema/cluster/매핑 신규 확장 필요.
- spot 무레버리지 `leverage=1.0` → notional=margin → 캡% = 현금 동원율.
- daily 0.99 / total 0.99 불변(across-track min) / 단일거래 0.09 ceiling 불변 / hard-MAX 불변.
- PDT(일 4 day-trade)는 캡이 **아니라** 별도 day-trade 카운터 게이트로 처리 — 캡%와 직교. 캡 깎지 말 것.
- 신규 cluster: 미국주식은 BTC/ETH·XAU·FX와 무관 → 별도/무바인딩 0.99.

**이견 (round 2 필요):**
| 항목 | A (Growth Quant) | B (Microstructure) |
|---|---|---|
| equity 기준 | buying_power 활용($318.8k 4×) | cash $79.7k 고정, BP 배제 |
| gross cap | 3.0~3.5 (BP 풀가동) | 0.99 (100%서 마진누수 차단) |
| per-symbol | 0.99 (일관성) | 0.50 (단일 갭 노출 상한) |
근원: gross 기준 equity가 cash냐 BP냐 = engine `equity_usd` 정의 문제.
spot에 BP(마진)를 쓰면 4× 암묵 레버리지 → overnight 갭 노출이 spot 전제 위반(B).
그러나 무레버리지면 청산 연쇄 없음 → BP 활용이 신규 트랙 데이터 capacity를 푼다(A).

## Jin 추가결정 필요
1. **#2 gross 기준**: Track C `equity_usd`를 cash($79.7k)로 둘지 buying_power($318.8k)로 둘지.
   → 이게 정해지면 gross 0.99(cash) vs 3.0(BP)가 자동 귀결. **round 2 핵심.**
2. **per-symbol spot**: 현 코드 0.99(설계문서 0.50 아님). Track C도 0.99 일관 vs 0.50 갭상한.
3. PDT day-trade 카운터를 캡과 별도 구조로 신규 구현할지(둘 다 권고).

## 즉시 합의 적용 가능 (round 2 불요)
#1 per-market leverage 전환 + 폴백 매핑 + fill_normalizer 전달.
#2 daily 0.99 / total 0.99 / 0.09 ceiling 불변, PDT는 별도 카운터.

## verify
read-only 준수. 거부키워드 sweep 0건(방어/축소/real-money 보수 논거·권고 없음).
9-stack 곱셈 mult 추가 0(#1 leverage=notional 배수, #2 캡=headroom_min 멤버). hard-MAX 불변.
파일: `_production_run_signal.py:124,149` · `constraint_translator.py:129-133` ·
`engine.py:453` · `fill_normalizer.py:169` · `schema.py:36,54,60-85,202`.
