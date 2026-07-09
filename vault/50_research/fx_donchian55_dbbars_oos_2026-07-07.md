---
type: research
status: validated
date_created: 2026-07-07
tags: [research, backtest, oos, donchian, fx, capital, reject, candidate2, fee-hurdle]
---

# candidate#2 CADJPY+USDJPY D-55 breakout — REAL DB bars OOS — VERDICT: REJECT

DEMO/PAPER · aggressive 보존 · flow_not_block · Capital CFD long-only. Strategy-expansion wave candidate#2.
backlink: [[donchian55_fx_generalization_backtest_2026-06-27]] (prior PARTIAL verdict, 10y yfinance) · [[okx_donchian_55_breakout]].

## 질문
prior FX 일반화 노트가 yen-pocket 강건 survivor로 지목한 CADJPY/USDJPY(D-55+ROC-20 let-run)를
**실제 DB 백테스트 서피스**(data/polaris_live.sqlite, Capital 1D)에서 formal OOS+overfit+fee-hurdle 게이트로 검증.

## 셋업
- 실데이터: USDJPY 426 · CADJPY 417 1D bars (~1.4yr, 중복0). okx_donchian_55 진입 EXACT 복제
  (D-55 prior-high `bars[i-55:i]` no-lookahead + ROC-20>0) + 검증 let-run 엑싯(entry-2·ATR20 stop + close<D-20 harvest + 25b backstop).
- fee: Capital per-pair RT 스프레드(USDJPY 2.5 / CADJPY 3.5bps) + slip 0/5/10/15/side. long-only.
- gate: walk_forward_splits + pbo + admit_strategy + 2-half OOS + frictionless fwd-return.

## 결과 — 명확한 REJECT
- **거래 극소**: D-55는 ~420bar에서 USDJPY 5 · CADJPY 4 trades만 발화. formal walk-forward는 embargo(101b)로
  **fold 0개 반환**(harness 정직 degenerate) → PBO/admit 게이트 **도달 불가**. 샘플이 formal OOS admission에 미달.
- **net 전부 음수**: mean_net@slip15 = USDJPY −84bps · CADJPY −87bps. win 1/5 · 1/4. 2-half OOS 둘 다 음수/불안정.
- **frictionless fwd-return(엣지 존재 여부, 비용 0)**:
  - USDJPY: +10b −24 / +20b +20 / +25b +14 / +40b +43bps, win 4~5/9 — 미미하게 양수지만 coin-flip, n=9.
  - CADJPY: **전 horizon 음수**(−29/−77/−56/−20), win 1/4 — 진입 엣지 **부재**. 엑싯 튜닝으로 구제 불가(recipe rule).

## 진단
prior +542bps(CADJPY) OOS는 10y yfinance 표본의 소수 lucky trade. **DB 서피스(~1.4yr)에는 breakout event가
4~9개뿐** → (a) formal OOS admission 불가(fold 0), (b) frictionless 엣지 weak(USDJPY)~negative(CADJPY).
비용 문제 아님(Capital 스프레드 2.5bps는 tiny) — **신호 부재/희소**가 dominant. D-55 채널은 ~420bar 위에서 너무 느려 sample-starved.

## VERDICT: REJECT
- ❌ candidate#2 등록 금지. STRATEGY_REGISTRY 미투입.
- 교훈: prior 볼트 survivor 지위는 **10y yfinance 표본** 기반. 실 DB 백테스트 서피스에서는 D-55 FX가 sample-starved →
  formal 게이트 통과 불가. slow-trend archetype이라도 **채널 폭 vs 가용 bar 수** 정합이 선결. 
  FX yen-trend은 더 짧은 채널(D-20/D-30) 또는 더 깊은 데이터 확보 후 재검토 대상이지, 현 서피스 D-55로는 아님.
