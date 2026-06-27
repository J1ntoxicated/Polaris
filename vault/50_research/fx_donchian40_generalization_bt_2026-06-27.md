---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, fx, donchian, breakout, capital, generalization, overfit, reject, flow-not-block]
---

# donchian_40_breakout_1H × capital_fx_majors_crosses 일반화 — VERDICT: OVERFIT

DEMO/PAPER · aggressive 보존 · flow_not_block (진입 차단 아님, 비용은 실현) · 심메트릭 long/short CFD.
backlink: Phase2 Capital 다양화 (task #78) · archetype = `fx_breakout_basket` (live 5 majors).

## 핵심 질문
검증 archetype(돌파)을 Capital FX 클래스 여러 instrument에 fan-out 하면 일반화되나, 소수만 우연 fit(overfit)인가?

## 셋업
실데이터 yfinance 1H, **18 pair** (USD majors 7 + yen-crosses 5 + high-ATR/range crosses 6),
**730일 span (2023-09~2026-06, pair당 ~17k bars / ~700 trades)**. OOS = 시간순 후반 절반.
진입 = `fx_breakout_basket.py` 그대로: `close > donchian_high_40` (or `< low_40`) AND `adx_14 > 10.5`,
**동일 파라미터 전 instrument** (= 일반화 테스트, per-instrument 튜닝 0). 엑싯 = 시스템 모델(let-run + -1.0R ATR rail + peak-protect arm0.30/giveback0.5).
비용 = per-instrument spread(2.1~7.5bps) RT + 진입 spread-cross slippage.
스크립트: `data/fx_donchian40_generalize_bt.py` (full BT) · `data/fx_donchian40_probe.py` (raw fwd-return + chandelier let-run probe).

## 결과 = 만장일치 음수
- **0/18 OOS net-positive** (FULL도 0/18). 5개 코호트 전부 0/N. live 5 majors 조차 0/5 — 라이브 5가 "검증된" 게 아니라 엣지 측정 없이 돌던 것.
- pooled N=12,571 trade, net **-0.564R/trade**, win 17.3%, PF 대부분 <0.5.
- per-instrument OOS net_mean: min -1.06R(GBPCAD) / median -0.57R / max **-0.26R(EURUSD)** — **최선조차 음수**. std 0.23 = 분산은 있으나 전부 음수 구간 → learner 튜닝 여지 없음 (튜닝은 음수를 0으로 못 올림).

## 엑싯 모델 탓 아님 — 신호 자체가 음수 (2중 독립 확인)
1. **raw frictionless fwd-return**: 돌파 발화 후 fwd-24h median **-2.15bps across pairs** (방향 무관 부호조정). 즉 1H FX 돌파는 평균적으로 **돌파 방향과 반대로 드리프트** = 교과서적 intraday mean-reversion. fwd48h-in-R 12/18 음수.
2. **chandelier-3ATR let-run** (관대한 추세추종 엑싯): 역시 **0/18 net-positive**, pooled -0.76R. peak-protect든 trail이든 둘 다 짐 → 엑싯 캘리브 문제가 아니라 **진입 엣지 부재**가 binding.
3. **ADX 강화로도 안 살아남**: thr 10.5→20→30→40 올릴수록 fwd24h가 **더 음수로** (AUDJPY -3.6→-20.2, GBPJPY -3.4→-12.8). 추세필터 강화가 더 세게 되돌리는 돌파를 선별. USDJPY/USDCHF만 +몇bps(노이즈, 최저 spread도 못넘고 thr에 비강건).

## 근본 = intraday-vs-daily archetype 불일치
밤샘 리서치가 검증한 돌파 엣지는 **일봉(1D) gold/index**. 같은 archetype의 **1H FX** 인스턴스는 다른 동물 — 1H FX는 microstructure mean-reversion 지배, 돌파는 음의 드리프트. ADX>10.5는 1000~1600 sig/pair로 사실상 필터 안함(노이즈 돌파 admit). fan-out 했다면 음의 기대값을 10+ instrument로 곱했을 것 = autopsy 재발.

## 결론
**OVERFIT → REJECT fan-out.** donchian_40_breakout_1H를 capital_fx_majors_crosses로 넓히지 않는다. live 5 majors 자체도 1H FX 돌파 = 음수 엣지이므로 별도 재검토 대상(이 archetype은 FX 1H에서 KILL/재설계 후보).
교훈: archetype 일반화는 **horizon×asset-class 짝이 보존될 때만** — 1D commodity/index 돌파 ≠ 1H FX 돌파. fan-out 전 클래스 raw fwd-return 부호 확인이 가짜엣지 수천개 방지의 1차 게이트.
거동 변경: fan-out 빌드 불필요(REJECT). 후속 = FX는 1H 돌파 대신 reversion/range 형태 또는 일봉 horizon으로 재검증해야 엣지 가능성.
