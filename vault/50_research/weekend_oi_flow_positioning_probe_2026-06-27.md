---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, weekend, open-interest, positioning, flow, maker, okx-spot, weak, flow-not-block]
---

# weekend_oi_flow_positioning — 주말 OI/테이커/롱숏 포지셔닝 2nd-axis probe — VERDICT: WEAK (deploy 불가, 데이터 천장)

DEMO/PAPER · aggressive 보존 · flow_not_block · -1.0R rail · OKX SPOT long-only · OI/flow=SIGNAL only.
backlink: [[weekend_funding_capitulation_probe_2026-06-27]] (유일 PASS, positioning축) ·
[[weekend_thin_book_microstructure_variants_2026-06-27]] · [[weekend_gap_drift_backtest_2026-06-27]] ·
[[weekend_dip_maker_revert_backtest_2026-06-27]] (전부 REJECT, price축).

## 동기 — 검증된 엣지는 positioning축, REJECT는 전부 price축
유일 PASS(funding-capitulation)는 perp **funding level**(포지셔닝축). REJECT 전부(dip/fade/breakout/persistence/
vol-cycle/4 microstructure)는 **price축**. 자연스러운 다음 질문: funding과 **직교하는 2번째 positioning/flow축**
(open-interest 동역학·taker buy/sell 압력·long/short 계정 군집)이 funding이 못 잡는 주말-고유 엣지를 갖는가?
4 가설(전부 OKX SPOT long-only maker, RAW frictionless 먼저):
- H1 OI-FLUSH: 주말 OI 급락(대형 −dOI)=레버리지 디리스킹/청산소진 → spot relief long.
- H2 OI-BUILD+px↓: OI 상승 + 가격 하락=thin book 트랩 롱 → 강제언와인드 relief long.
- H3 TAKER-CAP: 주말 taker buy-fraction 저극단=공격 매도 소진 → revert long.
- H4 CROWD-SHORT: 주말 long/short **계정** 비율 저극단=리테일 과밀 숏 → 스퀴즈 relief long (funding의 계정판 쌍둥이).

데이터: OKX rubik public(`open-interest-volume`/`taker-volume`/`long-short-account-ratio`, ccy-aggregate)
+ SPOT 1H candles, 12 ccy. 스크립트 `/tmp/weekend_oi_flow_probe.py`(raw cohort) · `/tmp/weekend_oi_control.py`(artifact 검증).

## 🔑 load-bearing 데이터 천장 (정직 — verdict의 근본)
rubik OI/taker/LS 1H = **30일 롤링 720pt 하드캡, pagination 무효**(`after`/`before` 무시, 항상 최신 720 반환).
= **단 4개 주말**의 1H 데이터. 1D 시리즈는 180d(~25주말-day) 도달하나 intraweekend maker 진입엔 너무 거침.
→ 이 probe는 **deployment-grade 백테스트가 아니라 signal-existence scout**. 더 많은 1H OI 주말은
history로 못 얻음(라이브 shadow 누적만이 유일 경로).

## 1차 raw cohort — 4 신호 전부 화려한 weekend≫weekday 분리 (너무 좋아서 의심)
주말=강한 +드리프트 / 평일=강한 −드리프트, 4 신호 전부. 최강 H4(+48h): 주말 med **+3.94 ATR**/57% vs 평일 −1.42.
funding-capitulation cohort-separation을 그대로 닮음 — **그러나 4주말짜리에서 이 정도 깨끗함은 confound 적신호.**

## 🔪 DECISIVE CONTROL — "엣지"는 대부분 30일 trend artifact + 단일 주말
1차 결과를 3 control로 해부:
- **C1 unconditional(신호 無, 전 바)**: 랜덤 주말 바가 이미 **+1.97 ATR @24h(65%pos)**, 랜덤 평일 바 **−2.26 ATR(32%pos)**.
  = 이 30일 윈도우에서 주말이 우연히 up-leg, 평일이 down-leg. **달력이 일을 다 함, OI/taker/LS 아님.**
- **C2 signal lift(conditional − unconditional, 同 day-type)**: H4 lift는 +24h에서 겨우 **+1.26 ATR**(3.23 vs 1.97),
  +48h만 +4.11. lift는 존재하나 raw +3.23이 암시한 것의 일부 + trend 배경과 분리 불가.
- **C3 per-weekend 분해 = kill shot**: H4 +24h 신호에 **distinct 주말 단 4개**, 그중 **W24 혼자 mean +6.00**가 전부 운반.
  나머지 셋 = +0.13 / +2.75(n=8 바뿐) / −0.08. W24 제거 시 엣지 ≈ flat. **교과서적 single-event artifact.**

## 결론: WEAK (0 deploy, 데이터 천장이 binding)
OI/taker/LS positioning축은 OKX가 주는 유일 intraday 데이터(30일 롤링, ~4주말)에서 **정직히 검증 불가**.
겉보기 엣지 = (a) 30일 directional trend 우연 + (b) 단일 주말(W24) 지배. binding constraint가 이번엔
신호부재도 fee도 아니라 **데이터 천장**(funding은 funding-rate-history로 95d/13.5주말 확보, OI는 30d/4주말).
- funding-capitulation(검증 PASS)이 여전히 유일 주말 positioning 엣지. 이 probe는 그걸 **대체/보강 못함**.
- lift(C2)가 0은 아니므로 완전 null은 아님 — 그래서 REJECT 아니라 **WEAK**. 그러나 4주말로는 overfit/trend
  분리 불가 → **빌드 금지**. 유일 길 = funding 전략 라이브 shadow에 OI/LS를 **부수 evidence 피처로만**
  병기 누적(거래 트리거 X, AI judge 보조 컨텍스트), 수개월 뒤 weekend N 충분 시 재평가.

교훈 누적(4회째 독립 수렴): 주말 OKX long-only maker 추가 엣지는 (price축 6변종 REJECT)·(positioning축
funding=PASS / OI·taker·LS=데이터천장 WEAK). **검증된 weekend_thin_book_flush_maker(#77 배포) + weekend_funding_
capitulation(candidate) 2개가 주말 OKX의 실증 엣지 전부.** OI축은 "신호 가능성은 보이나 입증할 데이터가 없다"는
정직한 WEAK — 거짓 빌드보다 보류가 mandate(aggressive≠reckless, 게싱X 증거기반)에 부합. /debate·빌드 불필요,
라이브 shadow 피처 누적만 권고.
```
StructuredOutput: name=weekend_oi_flow_positioning_maker, verdict=WEAK (데이터천장 — 4주말, single-event artifact)
```
