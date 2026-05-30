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

---
## ⚠️ /debate cross-verify (w1iz37hwt) = PROCEED_WITH_CHANGES — 아래가 BUILD 확정 설계 (위 초안의 비교부 SUPERSEDE)

**🔴 FATAL fix — 비교 단위 일치 (expected dollar edge, 양쪽 동일 단위)**:
- `proposed_risk_pct`는 stake 크기(%equity)이지 return 아님 / `mu`는 expected R. 직접 비교 금지(bet크기 랭킹됨).
- **NEW score** = `E$_new = fwd_mu(new_cell) × proposed_risk_pct × equity` — fwd_mu = **NEW 신호 자신의** (venue,strategy,ticker,regime) cell의 learner_posterior.mu(=edge; fallback cell avg_pnl_r). proposed_risk_pct는 **capital scale로만** 진입.
- **HELD score** = `E$_held = fwd_R(position) × open_risk_pct_held × equity`.
- **rotate iff** `E$_new − E$_held > IMPROVEMENT_MARGIN + ROTATION_COST` — **ADDITIVE margin**(곱셈 (1+THRESH) 금지: weak는 음수-by-selection이라 곱셈은 역방향), 같은 $단위.
- forward score = **posterior lower credible bound** (mu − z·posterior_sd, df=2·alpha_n) at **n≥20**, point estimate 금지(winner's/loser's curse).

**필수 변경 (8)**:
1. 비교 = expected $edge (위). IMPROVEMENT_MARGIN = additive $ (env, /debate; 시작값 build agent 제안).
2. **GAP-fix #1 재설계**: `position_risk_state`는 production writer 없음(read-only) → free-capital을 **venue availBal path**로(OKX `fetch_okx_available_usdt` 재조회; close 후 settle된 USDT). position_risk_state 삭제는 no-op이므로 폐기.
3. **close→settle→reopen ordering**: freed 자본은 OKX close sell **settle 후** availBal에 반영 → same-tick 즉시 reserve_and_submit 금지; 다음 recalc/짧은 confirm 후 new entry(또는 settle 확인 게이트).
4. **vacated-side anti-churn**: 막 청산한 victim에 post-close cooldown(reentry backdoor 닫기 — reentry.py:67/76 strength≥0.x exempt 우회 차단). MIN_HOLD는 victim age floor.
5. `ROTATION_POSTERIOR_MIN_N` 10→**20** (codebase trust floor 일치).
6. **cold-start candidate gate**: 새 후보 cell n<N면 prior mu~0 → 항상 measured-negative held 이김(잘못). 후보도 n≥N(또는 보수적 prior shrink)일 때만 rotation 자격.
7. `ROTATION_COST_R` per-name 실값 = close-leg(A)+open-leg(B) round-trip(slip+fee) in R/$ (cost_adjusted_pnl_r 패턴 재사용).
8. **per-rotation telemetry**(DEMO run 전 필수): `state.rotations`(victim_id/victim_fwd/pnl_r/E$_new/E$_held/margin/cost + same-symbol-reopen counter + rotations/hour).

**확정 유지**: MAX_PER_TICK=1(구조적, env X) · cross-venue=NO(**invariant** 아님 tunable X; OKX USDT↔Capital margin 비-fungible) · winner-exempt{protected,harvest}=YES · MIN_HOLD 300s(victim age floor, +symmetric cooldown) · trigger=capital-block only(sizing_zero/51008; 매 fire에 pending entry → net deploy↑) · greedy single-weakest/single-best(assignment 과설계 X, per-tick 수렴).

**잔여 리스크(빌드 시 주석/telemetry로 추적)**: horizon 비정규화(per-trade R; E$/expected_holding_bars 고려 가능) · mu unconditional(held 라이브상태 미조건; mfe/pnl_r 보강 여지) · self-referential survivorship(loser 조기청산이 mu 상향편향) · estimator mixing(mu vs avg_pnl_r 한 축 혼용 — 한 family 고정).
