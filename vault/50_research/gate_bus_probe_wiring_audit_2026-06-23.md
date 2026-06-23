---
type: research
status: active
date_created: 2026-06-23
date_updated: 2026-06-23
tags: [research, audit, wiring, gates, probes, bus, regime, read-only]
---

# Gate-Bus-Probe Wiring Audit (READ-ONLY)

GATES=결정 container, PROBES=연속 관찰 function, BUS=배선. 모델이 코드에서 어디까지 실재하나. file:line 증거, 추측 0. DEMO/PAPER · aggressive · flow_not_block(판단·shaping·tuning만) 전제. 동시 진행 BUILD(ws69kxed4 = C2)는 in-flight 감안. backlink [[structure_hardening_2026-06-23]] [[ADR-003-8-layer-architecture]] [[ADR-004-per-gate-ai-pipeline]] [[g1g2_build_program_2026-06-23]].

## 현재 배선맵 (verified)
- ProbeBus = 단일 인스턴스, attach 정확히 2곳: `_production_recalc.py:407`(bar/G6) + `_production_tick_engine.py:930`(tick/G6). 둘 다 run_precise_exit **직전**. observe-only sidecar → `data/probes.sqlite` (applied=0, byte-identical exit 보장).
- catalog 4 probe 전부 Exit/Position role (`roles.py` registry: profit_taking/loss_defense/session=Exit, technical=Position). **Eligibility/Signal/Validate = 예약-빈좌석** (registry 미등재).
- entry agent에 `core.probes` import **0건** (`polaris/core/pipeline/agents/` grep 무매치). → probe-function→gate 배선은 **G6/G7(exit/monitor)에만** 존재.
- 진입경로 2개: bar = full G1-G8 orchestrator(`gate_orchestrator.py` gate별 로그) / tick = orchestrator **우회**(G3/G4 tick 진입엔 미실행, regime은 `active_signals(regime)` 침묵 멤버십 `_production_tick_engine.py:490`만).
- 실측: `probe_decisions` mark_source = bar|7145, **tick=0** (C2 커밋됨, 아직 라이브 0행). `FROM ai_lessons` reader **0건** (write-only).

## Critical 갭
1. **진입게이트 G1-G5 probe 입력 0** — probe-function layer가 진입측에 구조적 부재. ProbeContext는 position-shape(pnl_r/mfe/mae) → 진입 candidate/signal/eligibility shape 없음. entry twin = 새 context + 새 attach site 필요(재구독 아님).
2. **레짐-적합 판단 부재 (최고가치 갭)** — "이 진입이 지금 레짐에 맞나"를 아무도 안 봄. G1=vol/cell, G2=present/absent, G3=cell-perf(regime은 `_production_run_signal.py:361` "display/log key only — no gate branches"), G4=microstructure, G5=regime은 lookup key만. tick은 regime을 신호 활성화에만 silent 사용.
3. **knowledge loop G8/Vault 단절** — ai_lessons SELECT reader 0, meta_labels write-only(trainer 없음), probe tuning_log는 dashboard만 읽음(`snapshot.py:173`), Vault .md writer는 설계상 제거(`post_trade_reflector.py:25-27`). 실학습(L4 cell/L5 learner/NIG posterior)은 pnl_r 직공급, G8 우회.
4. **tick 경로 미관측·검증우회** — tick 진입 G1-G5 결정 로그 없음(terminal open만), G3/G4 미실행, probe 0행. 가장 자주 발화하는 케이던스의 진입측이 깜깜.
5. **Sizer 기아** — realized_vol 미설정 → vol_targeted_scalar branch dead; strategy_risk_state production writer 0 → tier_amplifier 1.0 고정 + Kelly CS-3 고정. T4 amplifier 슬롯이 코드엔 있으나 무효과.

## 역할별 재배선 청사진 (BUILD 우선 → JIN-SURFACE)
- **R1 BUILD G6/G7**: C2 tick attach 라이브 랜딩 + tick pos dict enrich(peak/trough/recent_ticks, row_mfe/mae_r `_production_tick_engine.py:817`) → tick mark_source 실readings. behavior 0.
- **R2 BUILD all**: Jin "log everything" — regime gate(`:490`)·tick 진입체인 G1-G5·drop사유(DEBUG→INFO)·G3/G4 AI-free deterministic shadow row 구조화 로그. behavior 0.
- **R3 BUILD G1-G4**: 새 EntryProbeContext(candidate/signal-shape) + attach G1(post-focus)/G2(post-signal)/G3·G4(pre-validate), 예약좌석 점유, **observe-only 먼저**(G6 sidecar 패턴 미러). behavior 0.
- **R4 BUILD G8**: ai_lessons/probe tuning_log에 read consumer(knowledge-sink) 추가 → write-only ledger 해소(소비는 후속 gate). behavior 0.
- **R5 JIN-SURFACE G2/G3**: RegimeFit probe LEAN(conviction shaping, never block/skip — flow_not_block). 진입결정 변경 → Jin 선고지.
- **R6 JIN-SURFACE G5**: strategy_risk_state writer → tier_amplifier/Kelly 활성(기존 T4 슬롯 부활, ≤1 stack 추가 0). sizing 변경 → Jin.
- **R7 JIN-SURFACE G5**: SignalIntent realized_vol 활성 → vol_targeted_scalar 가동. sizing 변경 → Jin.

## 로깅 계획
regime gate 결정라인(symbol+regime+active_set+chosen, 현재 0줄) · tick 진입체인 per-stage verdict + drop사유 INFO 승격 · bus publish/consume(reading+lean+action+mark_source+abstention 사유) · G3/G4 AI-free deterministic shadow row(현재 `_log_g3_shadow` 전 return) · tick G5 standalone sizing 라인(binding_cap/sizing_zero) · G8 lesson emission+consumption · R3 entry-probe verdict(mode=observe, gate_id 스탬프).

## 다이어그램 정정
probe→gate edge = G6/G7만 실선. 진입 G1-G5 = dashed "reserved seat"(Eligibility/Signal/Validate 빈좌석). tick 진입 = G-orchestrator 우회(G3/G4 미실행, regime은 `:490` dotted "activation" 선, gate 아님). G8→Vault = dotted dead-end. 실학습 L4/L5/NIG = pnl_r 직공급 실선(G8 우회). probe attach = OBSERVE-ONLY tap(별도 probes.sqlite, applied=0), control wire 아님.

mandate_ok · aggressive 유지 · shaping-not-blocking · 9-stack 추가 0 · in-loop GPT=0.
