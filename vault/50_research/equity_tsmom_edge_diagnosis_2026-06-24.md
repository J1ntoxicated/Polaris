---
type: research
status: design
date_created: 2026-06-24
date_updated: 2026-06-24
tags: [research, equity, alpha, exit, slippage, flow-not-block]
---

# equity_tsmom 0%승 진단 — 정밀엑싯 round-trip + 진입 슬리피지

DEMO/PAPER · aggressive 보존 · flow_not_block(차단/사이즈컷/진입블록 아님). read-only 진단.
backlink: [[flow_pressure_crypto_profit_tuning_2026-06-24]]

## 진단 (증거 기반, file:line+실수치)
- **표본**: reset#3 cohort=8 (XLK,QQQ,SOXL,MU,AMD,SMH,IWM,INTC; opened_ts≥1782225529).
  realized close pnl=**−$8.94** + 진입 슬리피지 **$10.35** = 전 strat 최대 $손실. 0/8 win.
- **0%승 근본 = 엣지붕괴 아님, 엑싯 조기절단**. 7/8 green peak(MFE>0). avg MFE **+0.020R** /
  avg MAE **−0.015R** = 양방향 미동. 1D thesis(`expected_holding_bars=24`)가 **29–387초**에 종료
  (`positions.exit_cadence='bar'`). cohort 전부 MFE 양수 peak 찍고 breakeven−cost로 반납.
- **절단 경로**: G6/G7 gate_events 전부 HOLD(`below_widen_window`) → −1R rail 아님. cut=
  thesis-BROKEN. bar 파이프라인이 `broken_streak=2`(=BROKEN_TICKS) 전달
  (`_production_recalc_exit.py:308`) → bar-close 1회 `momentum_drift<0`(DEADBAND=1e-3 초과,
  `exit_engine.py:179`)가 GRACE 25s(`exit_engine.py:178`) 통과 후 즉 CONFIRMED → red+BROKEN=CUT
  (`exit_engine.py:495-561`). chop/crisis의 분단위 드리프트가 1e-3을 즉시 횡단 → 30–400초 컷.
- **MFE-protect 미발동**: `EXIT_EQUITY_MFE_BEP_R=0.30R`(`exit_engine.py:98`). peak 최대 MU+0.041R
  < 0.30R → BEP floor 한 번도 arm 안 됨. 1D-ATR denominator(risk_usd $10–323)라 초단위 이동이
  1R의 미세분수 → 어떤 floor도 못 잡음.

## 진입 타이밍 (오해 정정)
- 진입 = US RTH(cohort 10:39 ET 화). G8 'session=asia'는 봇 로컬 Sydney 라벨, US-세션 버그 아님.
- **timeframe 정상**: `_production_tick.py:460-484`가 `bar_interval=timeframe`로 1D bars read →
  `momentum_20bar`=**20거래일**(20분 아님). thesis 실값 MU+0.4550·SOXL+0.2663·AMD+0.1197 =
  수주간 3x-ETF/반도체 런(20분 blow-off 아님). 즉 **진입은 진짜 다주(multi-week) 모멘텀 추격**.
- 병인 = **연장(extended) 위너를 chop/crisis 진입**(entry_regime: chop 6 / crisis 2,
  `positions.entry_regime`) → 분단위 mean-revert. 전략은 진입에서 regime 무시
  (`equity_tsmom.py:58-90`, bare momentum>0, breakout/rank/regime gate 0). 엑싯만 regime read
  (`exit_engine.py:539-547`). 시스템 자체도 `ai_lessons` 25행이 lesson_type='entry_timing'
  delta `equity_tsmom_x_chop=−0.0075`/`_x_crisis` 음수로 손실을 진입타이밍에 귀속.
- **슬리피지**: market-order 진입이 10.35$(MU 55.6bps=$6.59 + AMD 20.4bps=$2.42 = 87%).
  universe=유동 majors/ETF(잡주/저유동 아님) → 자산품질 아닌 **execution 스타일**(스파이크 시장가).

## flow_pressure 대비 (역병인)
- flow_pressure(n=1533): MFE+0.27R/MAE−0.91R = 위너 사고 **너무 늦게** 손절(꼭대기 매수 후 라이드다운).
- equity_tsmom(n=8): MFE+0.020R/MAE−0.015R = thesis-BROKEN 타이머가 **너무 빨리** breakeven 컷.
- 같은 표면증상(미실현 엣지)·**역 엑싯 병리**. 공유 부수요인=둘 다 mean-revert 미세구조에 슬리피지 추격.

## 후보 수정 방향 (flow_not_block — 설계only, 빌드/적용 X)
1. **엑싯 GRACE를 timeframe 스케일**: 24일 thesis가 25s에 BROKEN-컷 X. 기존 bar-scaling 선례
   `_loser_timeout_for_strategy`(`_production_recalc_exit.py:219-234`) 미러 → 1D thesis는 분∼시 grace.
   expectancy 확장, throttle 아님(정밀엑싯 유지).
2. **slow strat의 bar-path broken-streak/DEADBAND 상향**: chop 분단위 1회 `momentum_drift<0`이 즉
   confirm 못하게 — 1D-스케일 지속 read 또는 regime-aware band(>1e-3) 요구. fresh 위너 보호.
3. **MFE-protect를 절대이동 표현**(또는 1D 스케일에 BEP 하향): +0.02–0.04R 미세 peak가 floor lock —
   현 0.30R/1D-ATR은 초단위 hold에서 도달불가.
4. **진입 마케터블-리밋/패시브 전환**: 10–55bps 슬리피지 제거(손실 큰 비중). per-trade 엣지가 작아
   슬리피지가 지배. aggressive 보존(여전히 全신호 진입), 더 나은 가격. 차단 0.

flow_not_block·DEMO·GPT=0·rejection_keyword=0. Jin 승인 후 빌드.
