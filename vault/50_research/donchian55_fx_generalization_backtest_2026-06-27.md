---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, generalization, donchian, fx, capital, overfit, fan-out, oos, partial]
---

# donchian_55_breakout × FX majors+crosses 일반화 백테스트 — VERDICT: PARTIAL (선별 fan-out)

DEMO/PAPER · aggressive 보존 · flow_not_block · Capital CFD long/short. Phase2 (task#78) archetype×class 일반화 검증.
backlink: [[okx_donchian_55_breakout]] (검증 survivor +97bps crypto) · [[strategy_expansion_roadmap_2026-06-25]] · candidate class = `capital_fx_majors_crosses`.

## 핵심 질문
검증된 archetype donchian_55_breakout(D-55 prior-high + ROC-20>0, 2×ATR20 stop + D-20 trailing harvest)이
**FX majors+crosses 여러 instrument에 일반화**되나, 소수만 우연 fit(overfit)인가? 무작정 fan-out=가짜엣지 수천개.

## 셋업 (실데이터, 전략 정확 복제)
- yfinance `PAIR=X` 일봉, **10y, 21 instrument** (majors 7 + yen-crosses 6 + other-crosses 8).
- 진입/엑싯 = `okx_donchian_55_breakout.py` **EXACT 복제**: D-55 prior-high breakout(`bars[-56:-1]`, no look-ahead) + ROC-20>0;
  stop = entry−2.0·ATR(20) intrabar, harvest = close<D-20 prior-low. side=long(크립토 충실) + long+short(CFD 대칭) 둘 다.
- net = Capital demo per-pair 스프레드(task 명시: EURUSD 2.1 / USDJPY 2.5 / GBPJPY 6.3…) + 0.5bp/side slip, full-RT 차감.
- OOS = chronological 2분할(IS/OOS) + **3-fold 독립 분할**(stability). 스크립트: `/tmp/donchian55_fx_oos.py` · `/tmp/donchian55_fx_robust.py`.

## 결과 — overfit 지문이 명확
- **OOS net-positive: 8/21 (38%) long-only / 6/21 (29%) long+short**. median OOS net = **−57bps/trade**. 대다수 음수.
- **3-fold 전부 양수 = 단 3/21**: EURUSD(미미 +7~33), **USDJPY**, **CADJPY**(+16/141/301 안정). 나머지는 한 두 fold만 우연.
- **비대칭 winner 끌어올림**: CADJPY +542 / USDJPY +198 / CHFJPY +129 / EURJPY +101 OOS — 소수가 평균 견인, 본체는 음수 (autopsy 재발 패턴).
- short 추가 = **더 악화**(29%, yen consistency 3→2). breakout 방향에 대칭 엣지 없음 → 일부 long-yen-weakness directional artifact.

## 치명 진단: 비용 문제 아님 = 신호 부재
**cost sensitivity FLAT** — half/base/double cost 전부 38% positive, median −55/−57/−61bps. weekend-maker REJECT(cost가 binding)와 정반대:
**FX majors/EUR-crosses는 trend-persist 안 함(mean-revert) → 55일 돌파가 천장 매수→stop**. gross direction이 틀린 것이지 fee가 먹은 게 아님.
크립토 trend-persistence(엣지 원천)가 FX major class에 **이전 안 됨**. 무차별 fan-out = 가짜엣지 양산 confirmed.

## 단 하나 살아있는 pocket: yen-trend/carry
- yen-crosses ≥2/3 fold positive = **3/6** (pooled fold median ≈ −3bps ≈ 0 — class-wide 아님, 집중됨).
- 강건 survivor = **CADJPY**(all-3-fold, OOS +542, E[R] 2.59) + **USDJPY**(all-3-fold, OOS +198, E[R] 1.41).
  보조 = CHFJPY(2/3, +129), EURJPY(2/3, +101). carry-unwind/yen-trend 지속이 real이나 **소수 pair 집중**.
- per-instrument 분산 거대(OOS std 158bps, −224~+542) → learner가 튜닝할 여지보다 **애초 class 부적합**이 dominant.

## VERDICT: PARTIAL — 선별 fan-out ONLY
GENERALIZES 아님(38%만 양수, 강건 3/21). 전면 OVERFIT-REJECT도 아님(USDJPY/CADJPY pocket은 fold-stable real edge).
- ✅ **선별 port**: USDJPY + CADJPY(강건) ± CHFJPY/EURJPY(보조), **long-only**, 4-pair watch. yen-trend sub-theme만.
- ❌ **금지**: majors(EUR/GBP/AUD/USD·CAD·CHF·NZD) + EUR/GBP-crosses 전면 fan-out — gross 음수, class 부적합.
- ❌ short leg 금지(대칭 엣지 부재).
교훈: archetype은 **클래스 trend-persistence 특성에 종속**. 크립토 survivor라고 FX major에 복사 = overfit. donchian-55는 FX에서 **yen-trend 4-pair pocket 전략**이지 majors-class 전략 아님. learner 튜닝 전제 = 클래스 적합부터.
다음: per-pair learner는 yen-pocket 내에서만 의미. 나머지 Capital class는 다른 archetype(reversion=EURGBP/EURCHF range, range-fade) 별도 검증.
