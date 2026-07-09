---
type: research
title: Trading-Core Build Groups (spec)
date: 2026-07-09
tags: [build-plan, sizing, exit, gating, capital, spec]
related: ["[[trading-core-audit-plan_2026-07-09]]"]
status: active
---

# Trading-Core Build Groups — Spec (2026-07-09)

우선순위순(Jin 목표 직결도). 규칙: env-knob/magic 신설 금지 = 기존 상수·pure함수 재사용, 신규 임계값은 데이터유도. 중복금지: very-active-wave(cooldown/slot/focus/tf)·fix/dashboard-live-net·virtual/dbwriter-migrate-equity-dash. 신규코드 = fresh sub-agent 리뷰 의무.

## BG1 — G3 strength_scalar 배선 (안잃음·HIGH·rail LOW)
- **branch** `fix/g3-strength-scalar-wire`
- **goal** G3 MODIFY 산출 strength_scalar를 T4 continuous fold에 실도달 (MODIFY 60%가 사이징 영향 0 → edge-비례 트림 복원).
- **spec** `_sizer_payload.py:183` SignalIntent(...)에 `strength_scalar=`(G3 verdict scalar, 부재시 1.0) 추가 · `_production_tick_engine.py:198` 동일(2 intent 생성부 공통). G3 MODIFY 산출(`signal_validator.py:275` technical.scalar)을 payload→intent seam으로 전달. `engine.py:710 fold_strength_scalar` 무변경(이미 ONE-scalar 클램프-once, 9-stack 안전).
- **tests** G3 MODIFY(0.85)→intent.strength_scalar==0.85→cont 하향 반영 · PASS(1.0) 회귀 byte-identical · property 0.5≤scalar≤1.5 clamp-once.
- **effect** 약신호 60%가 실사이즈 하향. **risk** LOW: ≤1 fold지만 단일 continuous scalar 내부(엔진 주석 명시), 신규 dampen 레이어 X.

## BG2 — strategy_risk_state writer → Kelly/tier 활성 (활발+검증·HIGH·rail MED)
- **branch** `feat/strategy-risk-state-writer`
- **goal** 死 테이블에 writer 신설 → Kelly(n≥20)·tier amplifier(win-streak 1.5/2/3×) 최초 활성. 현재 全fill이 CS-3 cold cap($6000)·tier 1.0x에 핀고정 = 승률 사이즈업 全기간 미작동(602 누적체결에도).
- **spec** `post_trade_reflector.py`(이미 close마다 ai_lessons write) 안에 `strategy_risk_state` UPSERT 추가: 청산이력 집계→n_closed·win/loss→`kelly.kelly_fraction(p,q,k)`(기존 pure함수)·win_streak(연속승 tail)·hit_rate_10(최근10 승률). 신규 상수 X(전부 기존 kelly/amplifier 함수 입력 계산). write는 db-writer-reader-split serialized RW conn 경유(자체 conn 금지).
- **tests** 20승 청산→row.n_closed≥20→kelly_or_cold_start cold=False·tier>1.0x · 미달시 cold-start 회귀 · streak reset(패배시 0).
- **effect** 승리전략 사이즈업(aggressive), Kelly on=검증가속, fills 이산붕괴 해소. **risk** MED: 핫테이블 write(WAL 경합)→serialized writer 필수 조율. rail=sizing, 방향은 상방(pro-mandate).

## BG3 — Capital notional $10 붕괴 진단→수정 (검증가속·HIGH·rail MED, diagnose-first)
- **branch** `fix/capital-notional-collapse`
- **goal** Capital 6전략 전량 $10 flat(T4 산출 $9k–50k의 1/1000) → Capital 트랙 全체 실사이즈 검증불가. translate_capital_order는 배선됨(`_production_pipeline.py:435`)인데 fills는 이산상수 = 상시 legacy fallback 의심.
- **spec** (진단 우선, 게싱금지) ① `state.capital_constraint_fallbacks` 카운터·회전로그로 translate가 None 강등중인지 확정 ② 강등이면 원인(constraint fetch 실패 / quote-rate None / caller 미도달)별 수정 ③ tick경로(`_production_tick_engine`→`reserve_and_submit`)가 translate를 우회하는지 확인(유력가설). 근본원인 확정 후에만 라인수정.
- **tests** 진단재현 회귀 + 수정후 Capital fill size_usd가 T4 notional 추종(±lot 반올림), $10 flat 소멸.
- **effect** Capital 트랙 검증 해금(6전략). **risk** MED: 베뉴 constraint 변환 인접, flow_not_block(실패시 legacy 유지) 계약 보존.

## BG4 — stale_book KILL→FLAG 강등 + per-ticker 케이던스 (안잃음+활발+per-ticker·MED·rail LOW-MED)
- **branch** `fix/stale-book-per-ticker`
- **goal** G4 stale_book KILL 역선별 해소(counterfactual: KILL코호트 +0.39R@24h vs PASS −0.15R = 좋은신호를 죽임). 글로벌 60s는 per-ticker 위반.
- **spec** `_shadow_rules.py:193` stale KILL을 crossed_book처럼 유지하되 (a) 임계값을 심볼별 median tick-interval baseline(universe stat, `baseline_p50_spread_bps` 패턴)로 대체 (b) 초과시 KILL→FLAG 강등(spread/drift와 동일 등급, `flow_not_block`). crossed_book KILL은 존치(마이크로구조 무결성).
- **tests** 저케이던스 심볼 정상틱→PROCEED(구60s면 KILL이던 케이스) · crossed_book 여전 KILL · baseline 부재시 안전 폴백.
- **effect** 좋은신호 통과(안잃음), 블록완화(활발). **risk** LOW-MED: G4 결정 인접이나 KILL→FLAG=블록완화(pro-mandate), auto-flip 아님(1회 근거기반).

## BG5 — exit peak-lock giveback 진단→수정 (안잃음/winner-capture·MED·rail MED, diagnose-first)
- **branch** `fix/exit-peak-lock-bind`
- **goal** TREND winner MFE 3.2R가 0.2R로 실현(capture −0.06). peak-fraction floor 코드는 존재·정상(`exit_engine.py:409-426`)이므로 param plumbing/bind 실패 의심(logic 결여 아님).
- **spec** (진단 우선) ① 라이브 BAR TREND 경로의 `MfeProtectSchedule.peak_lock_arm_r`가 실제 >0인지(=0이면 floor 전건 스킵, fixed rung만) ② atr_r 확장이 R-환산 floor를 잠식하는지 추적 → 근본원인 확정 후 schedule 배선/스케줄 수정. + 곁다리: `exit_thesis.py:49 _REVERSION_GROUP_SUBSTRINGS`에 `"reversion"` 추가(`cci_reversion`/`connors_rsi2` TREND 오분류 잠재결함).
- **tests** peak 3R armed→floor≈frac×peak 잠금(0.2R 아님) · reversion group→Bucket.REVERSION 재분류 · profit_target_r 미설정 reversion 회귀.
- **effect** 대형 winner 잠금(안잃음=이익보존). **risk** MED: 라이브 exit ratchet 인접, tighten-only 계약 보존(loosening 금지).
