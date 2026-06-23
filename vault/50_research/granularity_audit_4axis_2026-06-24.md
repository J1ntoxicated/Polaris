---
type: research
date: 2026-06-24
axes: [regime, strategy, exit, learner]
verdict: 3-of-4-coarse-or-thin
backlinks: [[all_strategy_edge_diagnosis_2026-06-24]]
---

# 4-Axis Granularity Audit — taxonomy coarse/thin sweep

Jin 도전: "레짐 4개 맞아? 전략·엑싯·러너 저것만이 맞아?" → 전수 audit. **3/4 축이 coarse/thin.** 세분화는 정밀 진입·엑싯·라우팅용(flow_not_block, aggressive 강화) — 차단/축소 아님.

## Per-axis verdict
- **REGIME = COARSE.** 4 label → 3 bucket+unknown. chop = 159 심볼 중 108 (68%). 단일 label 안에서 WR ~4x 스프레드 (session_breakout 40% vs flow_pressure 11.7% n=614). 결정적: regime_mult 러너가 이미 bimodal 0.3-vs-1.0 demotion 으로 coarse label 과 싸우는 중 (flow_pressure:chop 0.3 n=1068 vs fx_breakout_basket:chop 1.0). 2D vol×efficiency 분할 부재 (regime_gate.py:45, _production_indicators.py:630).
- **STRATEGY = THIN.** 15 등록 / 12 live / **3 DARK** (cci_reversion·supertrend·connors_rsi2 — _production_tick.py:213 미인스턴스화). cci_reversion 은 commodity-reversion 공백 메우려 빌드됐는데 live 미가동 → 공백 그대로. crisis bucket = 모든 venue 0 전략. Alpaca 3/3 long-only. family 분류기 stale (score.py:56 최신 4 전략 누락 → silent-neutral).
- **EXIT = ADEQUATE.** 4축 중 가장 rich (5 sub-taxonomy, reversion=target / trend=trail 이미 코드화, grace+sustained gate). 잔여 enrich 만: binary bucket (BREAKOUT/EVENT/GAP 없음), partial scale-out 부재, profit_target_r flat 미스케일.
- **LEARNER = COARSE+BROKEN.** P0 러너 3개 전부 WR-ladder SIZE mult. **max_hold 러너 무력화** (_production_close_effects.py:151 holding_bars=20 하드코딩 → ratio 항상 1.0 → 1225+ trade 전부 bucket:1, 학습 0). EXIT 러너 = 미빌드 P1 stub (엑싯이 안 배움). meta_labels 1666행 수집 / 0 소비.

## Biggest gap
coarse REGIME 과 degenerate LEARNER 는 **커플링**돼 있다 — chop 82% n_eff 가 러너를 강제로 per-strategy 0.3 floor 로 demote 시키고, 그 demotion 이 곧 누락된 regime 구분의 대리물이다. 게다가 이미 존재하는 granularity 마저 일부 off (3 dark 전략, 죽은 max_hold 러너).

## Prioritized refinements (gap 큰 순, flow_not_block·aggressive)
1. **WIRE 3 dark 전략 + FIX max_hold 러너** — 순수 배선/1-line, taxonomy risk 0, 최고 ROI (이미 존재하는 edge 를 켜는 것).
2. **chop 2D 분할** (range_quiet / chop_volatile / coil_pre_breakout) — vol_pct·er 이미 계산됨. 러너의 0.3-vs-1.0 split 을 per-sub-regime ROUTING 으로 전환 = 방어적 demotion → 공격적 정밀. regime=아키텍처 → **/debate 게이트 필수**.
3. **stale family 분류기 sync + registry-완전성 테스트** (score.py:56, _sizer_payload.py:169) — silent-neutral 제거, dark 재발 방지.
4. **EXIT 러너 family 빌드** (trail/bep/profit_target per strategy×regime, realized MFE·giveback 이미 probe_outcome 적재) — 정밀 엑싯 mandate 직결.
5. **crisis-native + equity-short 추가** — crisis=biggest dislocation=aggressive edge. altdata vix/fear_greed 배관됐으나 미소비.
6. **NIG posterior tilt + expectancy-magnitude 학습을 단일 T4 scalar 에 fold** (신규 mult 아님, 9-stack 봉쇄 유지) + trend maturity 분할 + exit BREAKOUT/scale-out.
