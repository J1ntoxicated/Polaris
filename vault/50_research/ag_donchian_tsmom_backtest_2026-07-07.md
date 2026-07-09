---
type: research
status: rejected
date_created: 2026-07-07
tags: [research, backtest, generalization, donchian, tsmom, agriculturals, capital, overfit, oos, reject, data-integrity]
---

# Capital agriculturals × D-55 breakout + TSMOM 백테스트 — VERDICT: REJECT

DEMO/PAPER · aggressive 보존 · flow_not_block · Capital CFD long. 후보 #5 (strategy-expansion MAP).
backlink: [[okx_donchian_55_breakout]] (survivor +97bps) · [[donchian55_fx_generalization_backtest_2026-06-27]] (동일 overfit 지문) · [[project_validated_edge_is_slow_trend_not_scalp]].

## 셋업 (실데이터, archetype 정확 복제)
- Capital 1D bars, `data/polaris_live.sqlite` (read-only). 진입/엑싯 = `okx_donchian_55_breakout` EXACT 복제 (D-55 prior-high + ROC-20>0), + TSMOM-100 변형. 엑싯=let-run 고정 horizon (fwd-return proxy).
- fee = Capital CFD per-instrument RT 스프레드(8~12bps) + slip 10/15/20bps stress, full RT 차감. R≈200bps.

## 🚨 데이터 무결성 결함 (선행 발견)
- **COFFEEARABICA = 완전 오염** — bar-to-bar 점프 >15% 가 **98.9%** (~300 scale ↔ ~3 scale 두 instrument 교차 삽입). **드롭**. 백테스트 시 가짜 돌파 양산.
- LEANHOGS >15% 점프 7.3% (hog limit-move, 허용), SOYBEANOIL/LIVECATTLE 0% (clean). 사용 = 3종 (SOYBEANOIL·LIVECATTLE·LEANHOGS), >40% 틱 scrub 적용.
- "1D" bar 간격 median 0.79d(~19h) — session bar. bar-index 백테스트엔 무해하나 캘린더 horizon 해석 주의.

## 결과 — 3중 overfit 지문 명확
- **정식 게이트(harness)**: donchian55+roc20 OOS net-R Sharpe **−0.686**, DSR **0.000**, PBO **0.333** → ADMIT=False. tsmom100 Sharpe −0.286, PBO **1.000** → ADMIT=False.
- **horizon sign-flip (노이즈 지문)**: 全 Donchian(20/30/40/55)에서 H=15 = **−327~−389bps / ~30% pos** 일률 붕괴, H=10/20/30 은 양수. 단조 아님 = 안정 엣지 아니라 phase artifact.
- **IS→OOS sign-flip (phantom)**: "best-looking" D=40 H=30 조차 **全 심볼 IS 음수**(SOY −257 / CATTLE −199 / HOGS −247) 인데 OOS만 양수 — OOS 창이 2026 ag 랠리를 우연히 포착. IS 손실 + OOS 승 = 엣지 아님.
- **breadth 없음 (winner-carried)**: OOS 양수는 SOYBEANOIL(+324)·LEANHOGS(+221) 2종이 견인, LIVECATTLE OOS **−105bps / 44% pos**. autopsy 패턴 재발.
- **cost sensitivity FLAT**: slip 0→30 에서 190→130bps 미동, 부호 불변 = 비용 binding 아님. gross direction 문제이지 fee 문제 아님 (FX-generalization REJECT 와 동일 진단).

## 진단
크립토 D-55 trend-persistence 가 Capital ag class 에 **이전 안 됨**. Ag 는 55일 신고가 돌파 후 mean-revert (raw fwd H=15: LEANHOGS −728 / LIVECATTLE −276bps). "이정도면 됐다" 없음. 무차별 fan-out = 가짜엣지 confirmed.

## VERDICT: REJECT
positive-looking OOS 는 전부 IS→OOS sign-flip + winner-carried + fee-non-binding 아티팩트. harness 게이트 DSR 0 / PBO ≥0.33 / ADMIT=False 양 변형 모두. **STRATEGY_REGISTRY 등록 금지.**
교훈: archetype 은 class trend-persistence 종속. ag CFD 는 D-55 돌파 부적합. 향후 ag 재검증 시 (1) 오염 심볼 선-scrub 필수, (2) IS/OOS 부호 일관성 먼저, (3) horizon sign-flip 스윕으로 phase artifact 배제.
스크립트: `scratchpad/ag_backtest.py` · `ag_probe.py` (research-only, non-committed).
