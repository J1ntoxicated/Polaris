---
type: research
status: active
date_created: 2026-07-10
tags: [bg3, p0a, evolve, short, carry, event, thesis-first]
related: ["[[step2_candidate_sweep_design_2026-06-25]]"]
---

# BG3 parallel manual archetype track — thesis-first base classes (2026-07-10)

DEMO/PAPER. Aggressive=capital routing, flow_not_block. 9-stack 봉쇄·-1.0R
rail 무접촉 (P0a offline, sizing/T4 미접촉). vx_squeeze_1h_crypto4 등 검증됨
-미구현 backtest 후보는 실 DB 재검증 REJECT(IS 음수/yfinance flip)로 hand-build
0건 — 대신 롱-only 서바이버 로스터의 실제 구조 갭(short/carry/event) 3후보를
thesis-first base class로 저작, blind grid sweep 없이 P0a 엔진
(`polaris.core.evolve`) 그리드에 등록만 했다.

## 3 candidates (신규 파일, `polaris/strategies/`)

1. **`cfd_fx_range_fade_short`** — Capital FX majors 1H, SHORT-only ADX-range
   fade (bb_upper 오버슈트 → bb_middle 회귀). fx_range_fade(dead module, 유일
   가격-양 전략+41% MFE, taker fee/leverage로 KILL — signal 문제 아님)의 SHORT
   절반만 신규 strategy_id로 재저작. Varyable: `adx_range_max` (20/25/30).
2. **`okx_funding_carry_persist`** — OKX SPOT long-only, ABSOLUTE 임계
   (weekend_funding_capitulation_maker의 SYMBOL-RELATIVE p10 percentile +
   주말게이트 + bounded +1R와 구조적으로 다름: 전주 지속, let-run TREND 버킷).
   Varyable: `funding_threshold` (-0.0005/-0.0003/-0.0001).
3. **`capital_macro_riskoff_catalyst`** — Capital GOLD, VIX+HY_spread를
   ENTRY TRIGGER로(gold_riskoff_trend_amplify는 AMPLIFIER 전용, gate 아님).
   두 필드 모두 기존 AltDataView 배선되어 있으나 소비자 0. Varyable:
   `vix_threshold`(24/26/28) × `hy_spread_threshold`(450/500/550).

## PENDING — 등록 안 함

`STRATEGY_REGISTRY` 무접촉(`dispatch_eligible=False` 문서용). 승격 절차:
P0a `enumerate_grid`→`evaluate_variant` honest-N 게이트(real DB bars) 통과 →
virtual PROVE 레인(schema default, EARN 직행 없음) → fresh Claude sub-agent
외부 리뷰 → 라이브 발화경로 적대검증(등록≠발화). 이 브랜치는 골격+그리드
SSOT만; 실 DB 스윕은 별도 단계(BG1 P0a 엔진 invocation).

## 테스트 증거

- `tests/test_p0a_variants_archetype.py` (15): behavior-0, variant threshold
  changes entry set, grid bounds, make_variant seam — 3 ids 전부.
- `tests/test_p0a_no_inert_knob.py` 확장: `adx_range_max`는 실 ReplayEngine
  trade-SET 증명(MIN=20.0→0 trades, MAX=30.0→1 trade, empirically tuned bar
  fixture); `funding_threshold`/`vix_threshold`/`hy_spread_threshold`는
  entry-SET 증명(altdata가 generic replay에 배선 안 됨 — session_breakout과
  동일 사유). `test_fix3_covers_every_param_bounds_knob` 갱신(guard-the-guard
  exact-set 불변 유지).
- `tests/test_p0a_evaluator.py` 신규
  `test_is_negative_reject_reproduces_vx_squeeze_rationale`: SHORT 후보를
  지속 상승추세에 강제 발화(adx_range_max=30.0, ADX guard 최이완)시켜
  IS Sharpe 음수·is_pass False·oos_pass False 재현 — vx_squeeze REJECT
  근거("IS 음수")를 evaluator가 수동 eyeball 없이 자동 검출함을 증명.

전체 스위트: 5038 pass 변동없음(사전 3 fail+4 error는 stash로 무관계 확인,
`test_tf_downgrade_firing_smoke`/`test_correct_close_pnl_stamping`/
`test_run_debate` — 본 변경 무접촉). mypy --strict + ruff clean(변경 파일).
