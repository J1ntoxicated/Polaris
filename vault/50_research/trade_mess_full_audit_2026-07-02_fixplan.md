---
type: research
status: active
date_created: 2026-07-02
tags: [forensic, audit, fixplan, exit, fees, sizing]
related: ["[[trade_mess_full_audit_2026-07-02_verdict]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]", "[[layer-3-sizing-risk]]", "[[capital_weekend_fx_edge_2026-06-28]]"]
---

# 거래 개판 전수조사 2026-07-02 — Fix Plan (2/2)

전제: DEMO/PAPER · aggressive 보존(막기 금지, 수익 기하를 키우는 방향) · -1.0R rail/9-stack 불변. Verdict: [[trade_mess_full_audit_2026-07-02_verdict]].

## P0 (기하·화력 복구)
1. **fee-aware R-unit floor 전 전략 일반화** — frozenset 철폐, R-unit ≥ k×왕복fee floor를 G6 레일·exit-FSM·BEP/lock rung 공유. 모든 엑싯 타겟이 구조적으로 fee 위에. (rail: 진입차단 0, profit geometry fix)
2. **엑싯 horizon-scope** — drift bar 1m→전략 timeframe(`_production_recalc.py:173-180`) · corroborated-break에 horizon floor 의무(`exit_thesis.py:194-208`) · loser_timeout ∝ expected_holding_bars · drift floor 실측 노이즈 quantile 재캘리브. (rail: 비대칭 payoff 강화 — winner 흐르게)
3. **R ruler 단일화 + 사이징 상향 정렬** — 실 staked risk($24) vs R_budget($1,020) 42배 갭을 aggressive 방향 해소: T4가 의도된 예산을 실배치하도록 스톱거리 기반 qty 재정렬. USDJPY quote-ccy(`risk_unit.py:187-189`)·shadow cost-in-R 단위 버그 동시 수정. **/debate 대상**(트레이딩 파라미터).
4. **Alpaca 원장-베뉴 재동기화 + reject-anchor 쿨다운** — 베뉴 미추적 6포지션($73.6k) reconcile·BP 복원, 쿨다운 앵커를 영속 reject stamp로(`reentry.py:139-151`). (rail: 막힌 트랙 흐름 복원)

## P1 (엣지 도달·학습 활성)
5. **Capital emit 정지(06-30 16:30~) 근본원인** — 유일 흑자 트랙 침묵. wave2 5종 CAPITAL_BAR_STRATEGY_SYMBOLS union 누락 수정 + firing 검증 의무.
6. **학습→행동 배선** — cell cold-lock(풀최소 20)을 per-strategy/track 풀로 완화 → 학습된 anti-edge 셀이 T4 cell mult로 실반영 + rotation을 posterior에 연결. (rail: T4 승인 체인 내, 재배치=flow)
7. **DB 락 아키텍처 분리** — quote/bar writer 분리(별도 DB 또는 단일 writer 큐), 2.06GB 아카이브. 핫패치 금지.
8. **.env maker knob 복원** — REPOST_STEP_BPS=4·MAX_REPOSTS=6 (log.md 기록 有, .env 재편집으로 소실). ops_config 검증 대상에 .env 추가.

## P2 (관측성·위생)
9. **주문 라이프사이클 SSOT** — order_intents 3,123행 100% 'created'·orders writer 0 → 상태전이 배선, G6 EXIT_NOW close_reason 전달(`_production_recalc.py:550`).
10. **reconcile_orphans 더스트 + SOL pile-on** — min-size 미달 스킵·집계(51020/51201 루프), 동일 심볼 다중전략 동시오픈은 cluster cap 상관-인지 배분.

## 미해결 질문 (다음 조사)
- Capital 정전 근본원인: 피드/세션게이트/dispatch 중 어디? (P1-5에서 판정)
- OKX demo fee 70bps/leg vs real 10bps — 기하 판정을 demo fee로 유지 시 fee-in-R 7배 과대. real-fee 병행 ledger?
- weekend_shadow_orders 11,089행 would-be P&L 오프라인 분석 — 지금 가능, shadow 해제 판정 기준 수립.
- per-ticker AI-JUDGE(#32 active) 액티브 주문 구간 0 발화 여부 — 2차 AI 후킹 감사에서 판정.
- Alpaca 베뉴 잔류 6포지션 처분 — 베뉴 수동 청산 vs reconciler 자동 인수, **Jin 선호 확인 필요**.
- 재기동 daily-bar refire — bar 식별자를 boot wall-clock 아닌 bar-close ts로 발급하면 해소되는지 설계 검증.
