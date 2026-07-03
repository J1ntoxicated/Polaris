---
type: research
status: active
date_created: 2026-07-03
tags: [debate, codex, prove-then-scale, classes, sizing, r-budget, capital-reallocation]
related: ["[[ADR-005-sizing-formula-cell-routing|ADR-005]]", "[[layer-3-sizing-risk]]", "[[project_validated_edge_is_slow_trend_not_scalp]]", "[[profit_sweep_ladder_2026-07-03]]", "[[trade_mess_full_audit_2026-07-02_verdict]]"]
---

# Prove-then-Scale 3계급 포트폴리오 디베이트 (2026-07-03)

> codex CLI(gpt-5.5, xhigh) 5라운드, R3=fresh-세션 적대검증. FINAL CONSENSUS, 잔여 이견 0. DEMO/PAPER 명시, 거부 키워드 0건. 프레임 = 실적 기반 자본 재배치(잃는 곳 회수→버는 곳 집중 = aggressive 완성), 계급 이동 = 실증 승격/강등 자동(수동 개입 0).

## 라운드
- R1: ①②③⑤ MODIFY(W30·probe 15closes/+2F·Schmitt 세분) + 신규 3(closed-trade 정의 / non-fill 편향 / size-regime epoch).
- R2: 타임프레임-스케일 윈도우·1D+ 승격 8·intra-track 한정·3계급 유지(4계급 기각) CONFIRM, ③ BREAK→24h cap은 상승 전이만(하강 무제한·tripwire 최우선).
- R3(fresh 적대): BREAK 8 — median-F 취약→per-trade fee-정규화 score_F · probe fee bleed→동시/24h cap · optimistic shadow 루프→pessimistic fill · 저빈도 fill-gate deadlock→계층화 · zero-EARN idle · epoch blend=allocation 전용 · persistence 확장 · probe에도 cell eligibility+learner anti-edge 라우팅.
- R4: M1 재입장 사다리 산식 역진 적발(+3/+6/+9 수정) · M2 CONFIRM(사다리 충족자만 랭킹) · PROVE 정체 exit 부재 · F_track_cap 정의 2 BREAK.
- R5: 전부 수용 → FINAL CONSENSUS.

## 쟁점별 합의
| 쟁점 | 합의 | 값 |
|---|---|---|
| ① 판정 | 거래수 윈도우(캘린더 X — 저빈도 earner에 타임프레임 스케일), per-trade fee-정규화 score_F=Σ(net_i/max(abs(fee_i),1bp×notional_i)), strategy×venue 단위, 청산 이벤트 산정+일일 rollup | W: intraday 30 / swing 20 / 1D+ 12 |
| ② PROVE | 미니 실거래 채택(shadow는 체결/슬리피지 미실증 — maker fill 6% 선례). 사이징=고정 probe notional(T4 체인 밖 상수 = <1.0 mult 아님) | max(fee-floor K=3×왕복fee, venue min) · 승격 15/10/8 closes AND score_F>+2 |
| ③ 히스테리시스 | Schmitt 비대칭+dwell+tripwire. 상승 1회/24h, 강등 무제한(오승격 당일 회수) | EARN→PROVE 0.4W<-1 · →BENCH 0.4W<-4 ∨ W<-3 · tripwire W8<-4(1D+ W5<-3) 즉시 · dwell PROVE10/EARN5 |
| ④ flow 정합 | BENCH=자본 라우팅(차단 아님) — 신호·학습·shadow fill 지속. autopsy-KILL(상위 우선·자동부활 금지)·cell rotation의 전략 레벨 확장으로 검증. 재진입=실증 사다리(pessimistic shadow) | step0 1.0W&+3 / step1 2.0W&+6 / step2 3.0W&+9(cap) · decay 1.0W&+3 |
| ⑤ 결합 | 계급≠multiplier: EARN=풀 T4 불변 / PROVE=바이패스 상수 / BENCH=R 0. freed R=동일 track 내 EARN 가중 배분(cross-track=profit-sweep 사다리 전담). 9-stack 무결 | weight=max(score_F_W,1) · cap=min(alloc, 기존cap, 0.5×track_R) — min() 항 확장(허용 패턴) |

## 유효 반박 (설계 반영)
1. median-F 임계값은 venue/symbol mix shift에 표류 → score_F per-trade 정규화로 폐기 (spread-only venue는 modeled spread를 fee_i에 포함).
2. probe fee bleed = 이 설계가 고치는 병의 재현 → 동시 probe cap 3/2/1 per track + 24h probe fee cap 6/4/2×F_track_cap(07:30 동결 median), 초과 intent=shadow.
3. optimistic shadow가 loser 재입장 churn 유발 → pessimistic fill(limit=1-tick trade-through, market=max(1tick,5bp) 양측, taker fee) + 실증 사다리.
4. PROVE 정체=slot 영구 점유 → 1.0W live closes에 score_F≤+2면 BENCH(사다리 증가로 카운트).
5. 저빈도 fill-gate deadlock → n<10 off / 10–49 fills≥max(3,⌈0.2n⌉) / ≥50 last-50 rate≥20% + 미달=전이 없음+EXEC_STARVED→실행 RCA 큐.
6. probe도 cell eligibility 준수: cell_mult=0 셀 또는 learner anti-edge(p_pos≤0.20 & n≥20)면 shadow 라우팅 — 휴면 학습망(p_pos 0.154 학습·행동 0)의 첫 행동 배선.
7. size-regime 증거 재사용: 승격/1.5× 증액=new epoch, allocation blend 50/50(8 closes까지) — 강등·tripwire 판단은 항상 live 신규 100%.

## 빌드 스펙 (1줄씩)
1. `strategy_class` SQLite: class/W/F_track_cap/dwell/epoch_id/last_transition_ts/kill_state/ladder_step + lifecycle(qty·cum_fees·cum_pnl) + intent ring-50 + shadow ring-W + probe_fee_24h, 부팅 hydrate(재기동 리셋 금지).
2. score_F 산정기: closed trade=flat→nonzero→flat lifecycle 집계(부분체결·scale-in/out 내부 합산), 청산 이벤트마다 갱신.
3. 전이 엔진: Schmitt+dwell+tripwire+재입장 사다리, 상승 1/24h·강등 무제한, KILL 우선순위 상위·자동부활 불가.
4. G5 분기: EARN=기존 T4 체인 그대로 / PROVE=probe notional 상수(admission: stop_dist_pct>3×왕복fee_rate, 미달=shadow) / BENCH=shadow.
5. R-pool: BENCH freed R→intra-track만, probe overhead 선차감→잔여 100% EARN 가중 배분, 기존 hard min()에 0.5×track_R 항 추가. zero-EARN track=잔여 idle(강제 probe 확대 금지).
6. probe slot 랭커: 07:30 일일 rerank, pessimistic-shadow score_F desc(사다리 충족자만), tie-break fill_rate→expected fee→대기 age.
7. 부트스트랩: 기존 5주 라이브 히스토리를 동일 score_F 규칙으로 replay해 초기 계급 산정(현 earner=EARN 즉시 — all-PROVE 리셋 금지).
8. 텔레메트리: EXEC_STARVED·binding cap 항·probe fee 소진 로그 — 모든 파라미터 데이터 반증 가능하게.
