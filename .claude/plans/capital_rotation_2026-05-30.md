# Capital Rotation — 유한 자본 opportunity-cost 회전 (설계 SSOT)

**Jin 2026-05-30**: 자본 무한 X(모든 venue). 잔고 차면 새 기회>기존 보유 수익률이면 약한 포지션 정리→자본 회수→더 나은 기회 진입. rotation=자본효율 NOT throttle, flow_not_block 정합, P&L halt X. [[project_capital_rotation]]

## 트리거
신규 신호가 **capital 사유로** 막힐 때: (a) `entry_sizer` KILL `reason=sizing_zero` + `binding_cap`∈{per_symbol/cluster/track/...}, 또는 (b) OKX `insufficient_balance`/51008 reject. → 막힌 신호를 `state.rotation_candidates`에 push(신호+proposed_risk_pct).

## 점수 (per-venue only — OKX↔OKX, Capital↔Capital, Alpaca↔Alpaca)
- **새 기회 score** = `SizingFinal.proposed.proposed_risk_pct`(conviction 전체 스택: strength×cell_routing×regime×session×triple×tier). 신뢰 gate: `learner_posterior.p_pos`(n≥N이면).
- **기존 포지션 forward score** = `learner_posterior.mu`(venue,strategy,ticker,regime; cost-adjusted expected R). fallback: `cell_matrix_p0.avg_pnl_r`. + **weakness**: `exit_state=='open'` & `pnl_r<0` & `held>MIN_HOLD`.
- **약한 후보** = 최저 mu(or 최부정 avg_pnl_r) & `exit_state=='open'`(이익 미접촉) & 현재 pnl_r<0.

## 비교 + 액션
- rotation 조건: `new_score > weak_forward_score*(1+IMPROVEMENT_THRESH) + ROTATION_COST_R`(close 슬립+fee+new spread, R 환산).
- ⚠️ **winner 면제**: `exit_state∈{protected,harvest}` 포지션은 rotation 절대 대상 X(정밀 엑싯 보호).
- 액션: `close_specific_position(weak_id)` → **`position_risk_state` 행 삭제(GAP fix)** → `reserve_and_submit(new_signal)`(또는 pipeline 재투입).
- `MAX_PER_TICK=1`(cascade 방지). `_run_tick` 끝(supervise 후/`_evaluate_swaps` 전) `evaluate_capital_rotation` hook.

## ⚠️ GAP fix (필수)
1. `close_specific_position` 시 `position_risk_state` 행 삭제/0(없으면 T4가 freed capital 못 봄).
2. capital-kill 신호를 `state.rotation_candidates`로 전파(현재 binding_cap 로그만).
3. `learner_posterior.mu/p_pos` 읽기(현재 사이징 미사용, rotation ranking 전용).

## 트레이딩 파라미터 (env-override, **/debate 교차검증 대상**)
1. `ROTATION_IMPROVEMENT_THRESH`=0.40 (새 기회가 40%↑ 우월해야)
2. `ROTATION_MIN_HOLD_SEC`=300 (신규 포지션 anti-churn)
3. `ROTATION_POSTERIOR_MIN_N`=10 (mu 신뢰 최소 표본)
4. `ROTATION_MAX_PER_TICK`=1
5. cross-venue=NO(per-venue only)
6. protected/harvest 면제=YES

## 빌드 단계
scoping(done) → /debate(GPT+Gemini 교차검증: 옳은 방향·파라미터·churn 리스크·aggressive 정합) → build(TDD: rotation 모듈 + position_risk_state fix + capital-kill 전파 + learner_posterior 읽기 → 적대 리뷰 → 게이트) → live verify(잔고 부족 시 rotation 발생 확인).

## AGGRESSIVE/mandate 정합
rotation=자본 효율(더 나은 EV로 재배치) NOT 방어 throttle. 정량 score 비교로만 close(blanket P&L limiter X). circuit_breaker(무결성-only) 무간섭(insufficient_balance=external non-fault). winner(protected/harvest) 면제. 9-stack 무관(사이징 체인 미변경, rotation은 close+open 오케스트레이션).
