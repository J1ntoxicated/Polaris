---
type: plan
status: active
supersedes: [[system_design_audit_2026-06-22]]
date_created: 2026-06-22
date_updated: 2026-06-23
tags: [plan, structural, roadmap, measurement, profitability, probes, architecture]
---

> ✅ **LIVE SSOT (2026-06-23)**. M→S→D→R 측정 프로그램 **완료** (loop_state.md PROGRESS). 헤드라인 −266R/−209.7R은 **측정 아티팩트**였음 (cross-venue R 합산 무의미 + reconciled-mae를 realized로 오기록 −211R) — `risk_unit.py`가 SSOT로 화해. 정직 $ ledger: capital +$431 / okx −$646 / alpaca −$1881. 데이터 리셋 실행됨(2026-06-22, 클린 슬레이트 equity $130k).
> 이 문서 = 대시보드 `/api/roadmap` ladder 소스. 측정은 끝났고, 이제 **profitability forward edge-readout + 새 아키텍처(probes)**가 본선.

# Structural Roadmap — Polaris v2 (2026-06-23) [LIVE]

근거: 세션 종합(diagnosis · 운영봇 리서치 · correlation · LIVE-1 진단 · drift 백필 자기정정 · profitability overhaul · living dashboard/globe). **한 줄: 상류(regime/AI/evidence/유니버스)는 정교. 하류 3층 — ①P&L 측정 ②실제 크기 ③실행 — 을 이번에 단단히 했고, 측정이 정직해졌으니 이제 forward로 PF>1을 친다.**

DEMO/PAPER · aggressive · flow_not_block (probe는 판단·튜닝만, 사이즈컷/차단 없음). 분류: **[BUILD]**=버그/텔레메트리/정리(TDD+적대검증 builder≠reviewer) · **[DEBATE]**=sizing/strategy 트레이딩파라미터(/debate 먼저→결정→BUILD) · **[DONE]**=랜딩+증거.

## P0 — 측정 정직화 (최하부; 거짓이면 위 판단 다 거짓)
- 0.1 [DONE] reconcile drift→realized R 오기록 **정정**(자기정정, builder≠reviewer). reconciled-mae를 realized R에서 **제외** → 별도 drift 카운터. `risk_unit.py` 헤더 L6-42가 아티팩트로 문서화, ±100R 클램프. 옛 −211R 부풀림 제거.
- 0.2 [BUILD] PF/WR/confidence/digest가 `fills.pnl_usd`만 봄 → reconciled drift를 **별도 카운터로 surface**(realized P&L에 합산 X). confidence.py 일부 손댐(5067679), 두-장부 split는 risk_unit.py에 정의됐으나 **digest 전반 노출 미완**(loop_state GOAL '[ ] P0.2').
- 0.3 [DONE] per-ticker/strategy/regime/session 귀속 대시보드 노출. ticker_stats(positions.pnl_r, drift 포함) 라이브+TDD. loop_state '✅ P0.3'.

## P1 — 사이징→노출 가시성 (LIVE-1 판단 전제)
- 1.1 [BUILD] 사이징→노출 경로 계측: `compute_size` notional vs 실제 venue 표현 notional(lots×price) 로깅·대조. '의도 vs 표현' 갭 가시화. loop_state GOAL '[ ] P1.1' 미완 — backend-pure loop 큐.
- 1.2 [DEBATE] LIVE-1 tick-engine leverage SSOT. D3(tick-engine→OKX) APPROVED+APPLIED(2026-06-22 08:25, PID 35563)로 leverage-SSOT framing은 D-param에 흡수됨. 원래 Jin-surface 잔여만 deferred.
- 1.3 [DEBATE] two-producer(bar/tick) 사이징 정합. D3에서 부분 해소(Capital=fade-only quote-size 없음).

## P2 — 멀티스트림 SSOT 강제 (2→3 leaf)
- 2.1 [DONE] `_production_bars.py` baseline asset_class를 focus 튜플에서 유도. loop_state '✅ P2.1(bars baseline)'.
- 2.2 [DONE] `_production_layers.py` `or "crypto"` → group_id prefix 폴백. loop_state '✅ P2.2(layers 폴백)'.
- 2.3 [BUILD] `cluster_cap.py` equity 클러스터 정의(StreamConfig 선언했으나 누락). loop_state GOAL '[ ] P2.3' — backend-pure loop 큐.
- 2.4 [DEBATE] Capital equity-CFD whitelist 67종 포함 여부. STOP & SURFACE(Jin 결정), deferred.
- 2.5 [BUILD] CI 테스트: StreamConfig 선언키(cluster_id/asset_class) ↔ 소비테이블 키 정합. loop_state GOAL '[ ] P2.5' — backend-pure loop 큐 첫 항목.
- 2.6 [BUILD] asset_class Literal/enum 폐쇄(신규 클래스 시 mypy 강제). 미착수.

## P3 — 실행/유니버스 품질 (profitability 직결)
- 3.1 [DONE] 유동성 등급 Layer-0 사이징(**차단 아닌 유니버스 floor**, flow_not_block 보존). 5067679 'universe liquidity floor — 175bp-spread junk / sub-$1 pennies 발화 중단'. universe/discovery.py + _ranking.py.
- 3.2 [DONE] volume_burst 극성 뒤집기(fade-first/exhaustion-aware). D4 'volume_burst fade-first' APPROVED+APPLIED(2026-06-22). volume_burst.py.
- 3.3 [BUILD] ATR 정규화 스톱(저유동 알트 체결). S-stabilize에서 OKX venue-resting conditional 스톱 + flow_pressure trail 2→4 ATR 랜딩(970fdb2, 8fc9aa1). 코어 ATR-스톱 착지; alt별 정규화 잔여.
- 3.4 [DEBATE] 분할 엑싯(TWAP/트랜치) + 동적 슬리피지. 미착수.
- 3.5 [DEBATE] 검증된 흑자 자본 재집중. 리셋으로 winner 귀속 재시작 → micro_reversion retune 후보만, 재배분 미적용. STOP & SURFACE.

## P4 — 테스트/프로덕션 신뢰
- 4.1 [DONE] `tick_engine` burst→order 테스트 실패 진단. loop_state '✅ P4.1 진단(틱엔진 정상=fixture 드리프트)' — 틱엔진 프로덕션 정상, fixture 드리프트로 판정.
- 4.2 [BUILD] smoke-vs-production 위장 정리(SSOT 통일 또는 죽은 smoke 삭제). 미착수.

## P5 — 측정 토대 (edge 검증)
- 5.1 [DONE] replay/backtest 하네스 부활. loop_state '🟢 replay harness 부활(present_unwired, default DB fix → OOS 검증 가능, bar전략용)'. nightly ops 등록만 Jin.
- 5.2 [BUILD] `signals.correlation_group` populate + regime cross-asset evidence. signal_persist.py 추가(5067679)됐으나 correlation_group populate 미완. loop_state GOAL '[ ] P5.2' — backend-pure loop 큐.

## P6 — 운영 위생
- 6.1 [DONE] `/Users` vs `/Volumes` canonical 확정. MEMORY.md + CLAUDE.md 둘 다 `/Users/jinyoon/Projects/Polaris` = 메인(Jin 2026-06-21). pycache 정리 minor.
- 6.2 [BUILD] stale docstring(2-venue·smoke_paper_loop 참조) 정리. loop_state GOAL '[ ] P6 위생' 미완.

## PROFIT — 왜 안 벌었나 → 친 레버 → forward (이 세션의 본선)
- WHY-1 [DONE-진단] **유니버스**: edge를 175bp-spread junk/sub-$1 pennies에 발화 → 61.7% dead entries. 가장 load-bearing.
- WHY-2 [DONE-진단] **harvest give-back**: winner가 평균 +0.278R MFE 찍고 −0.947R로 realize = 1.225R 반납(거래의 29.2%). exit_engine.py L108-114.
- WHY-3 [DONE-진단] **broken R-metric**: risk_usd가 venue-skew, cross-venue 비교 불가. risk_unit.py L6-42.
- LEVER-1 [DONE] 유니버스 liquidity floor(P3.1) — junk/penny 발화 중단.
- LEVER-2 [DONE] **MFE-protect harvest를 14개 bar 전략 전부로 일반화**(EXIT_BAR_MFE_BEP/PROTECT/LOCK_R, exit_engine.py L106-122) + 1D equity schedule(L87-103). winner 반납 봉쇄.
- LEVER-3 [DONE] **stream-common R = R_budget denominator**(cross-venue 비교가능). risk_usd는 display로 강등, ABS floor $0.50(measurement-only, 사이즈/게이트 절대 X). risk_unit.py L38-140.
- LEVER-4 [DONE] **Alpaca BP 해방**: cross-venue orphan reconcile + EOD-flatten 전 venue 기본(streams/alpaca_health.py, session_exit_rail.py). OKX base-fee dust fix(fill_normalizer.py L124-128). live-price execution. OKX sub-min notional.
- LEVER-5 [DONE] **flow_pressure 틱-edge retune**(theta_ofi 0.20→0.32, trail 2→4 ATR, maker fallback): per-trade net −$0.858→−$0.282(67%↓), PF 0.19→0.366. fill_rate_cut(마지막 방어바) 제거(Jin).
- REMAP-1 [DONE 2026-06-23] **adaptive thesis RE-MAPPING ENGINE**(exit_engine.py L124-198): HARVEST fading winner / CUT broken-thesis loser, off일 때 byte-identical, no LLM. 신선 포지션 0-1s cut REGRESSION 발견(73 거래 0-2s) → **grace+sustain fix**(EXIT_THESIS_GRACE_SEC=25 + 노이즈 1틱 deadband·2틱 연속 streak, 구조적 regime flip만 즉시) → **.env ADAPTIVE_THESIS 0→1 flip + 봇 재기동(PID 15031) = 라이브 ON**. instant-cut 사망, give-back 레버 복구. = Position-probe(P7.3) 가동.
- REMAP-2 [DONE 2026-06-23] **OKX flow_pressure orphan / 생존편향 fix**(pooled-wallet-aware close): 같은 ccy N개 동시 포지션이 1개 fungible 지갑을 N개로 추적 → 첫 청산이 지갑 비우면 형제들 orphan(open_fills=1/close=0). 이게 flow_pressure 포지션 **~32%(주로 패자)를 ledger에서 누락 = PF/edge 생존편향**. fix: 형제 drain은 orphan 아닌 정상으로 + 0.7% dust idempotency 가드. 측정 무편향화.
- STRAT [DONE 2026-06-23] **신규 전략 STRATEGY_REGISTRY 등록**(봇 로드): ema_crossover + connors_rsi2/supertrend/cci_reversion(worktree→main 깔끔 재구현+적용). registry **총 15**. researched 전부 APPROVE.
- RESET [DONE 2026-06-23] **측정 베이스라인 리셋 메커니즘**(Jin: 메인로직 바뀌면 PnL 리셋): measurement_resets 테이블 + stamp_measurement_reset CLI + snapshot.since_reset(opened_ts≥reset만, 무편향 forward) + 대시보드 SINCE RESET. **스탬프 #1 @ 2026-06-23 08:42**(profitability-batch, baseline $126,913) — 새 로직 forward 측정 시작. 과거 데이터 보존(미삭제).
- FORWARD [BUILD] post-reset(08:42 #1) since_reset ledger에 거래 누적 → **고친 시스템이 PF>1 print 하는지** 무편향 관측 + edge-readout retune(flow_pressure 0-크로싱 theta 0.32→0.40?, micro_reversion/tsmom retune, US장 Alpaca+bar edge). flow_pressure 과다노출(58 concurrent same-ccy) 측정 정직해진 뒤 Jin-surface.

## M→S→D→R — 측정 프로그램 (완료)
- M [DONE] **측정 재설계**. risk_unit.py SSOT: R_budget cross-venue denominator(risk_usd 강등), reconciled-mae R 제외→drift 카운터, ±100R 클램프, risk_usd $0.50 floor(measurement-only). loop_state '✅ M 측정 완료(BNT −108R 아티팩트 제거, 두 장부 화해, 19 테스트)'. M은 2차 audit에서 venue-incomparable 재발견 → R_budget로 재정정, 두 번 iterate 후 착지.
- S [DONE] **안정화(APPROVE)**: OKX bounded auto-resume + 400-정밀도 fix + 심볼-skip, Alpaca recency guard + halt + 좀비 reconcile, regime bar-close 5m(1233 flip 해소). flow_not_block 보존, 2318 테스트.
- D [DONE] **/debate D-param 4개 APPLIED**(2026-06-22 08:25, PID 35563): D1 crisis 적응형+cap(crypto frozen) / D2 venue-native session+expectancy / D3 틱엔진→OKX / D4 volume_burst fade-first. 전부 aggressive/flow_not_block/no-throttle.
- R [DONE] **데이터 리셋 실행됨**(2026-06-22 07:58): 봇 정지 → archive 361M + tag pre-reset-2026-06-22 → 1.18M행 wipe(38 테이블; bars/quote/universe/blocklist KEEP) → 재기동 M+S 코드 로드 + risk_usd 마이그레이션 → 클린 슬레이트(equity $130k, PF 0, positions 0, drift 0). 증거: data/archive/polaris_live_pre-reset-2026-06-22.sqlite(378MB).

## P7 — Gates(decision) + Probes(continuous judges) + AI-escalation + Knowledge loop [ARCH]
> Jin이 이번 세션 그린 새 아키텍처. seed 코드 존재+프로덕션 wired(polaris/core/probes/ + _production_probe_attach.py). 정식 phase로 격상.
- 7.1 [BUILD] **GATES = 이산 결정 로직**(기존 G1-G8 deterministic/AI 파이프라인 그대로). **PROBES = 연속 모니터+심판**, **역할별 SPLIT으로 단일 병목 없음** — Eligibility / Signal / Validate / Position / Exit probe가 **병렬** 가동.
- 7.2 [BUILD] 각 probe는 명료 케이스를 deterministic 처리, **AMBIGUOUS 케이스만 AI(Agent RAG + Vault)로 ESCALATE** — AI는 sparse arbiter, hot path 아님(AI-free in-loop core 일관, commit aafb635).
- 7.3 [BUILD] **G6/Position-probe는 우리 OPEN POSITION만 모니터**(전체 유니버스 X). Position-probe ≈ 이미 exit_engine.py에 있는 adaptive thesis RE-MAPPING ENGINE(HARVEST fading / CUT broken-thesis).
- 7.4 [BUILD] 모든 probe verdict/escalation/outcome → **KNOWLEDGE(Vault) loop**, learn-from-live 사이클 닫기.
- 7.5 [BUILD] **5 canonical role 명명 + 기존 probe 매핑**: 현재 catalog 4개 = ProfitTaking(Exit/Position) / LossDefense(Exit) / Technical(Signal/Validate) / SessionHours(Eligibility). role-split 스켈레톤 일부 구축됨. **잔여**: 5 role 완성 + AI-escalation 계약(ambiguous→Agent RAG+Vault) + Knowledge writeback 정식화. flow_not_block/DEMO/aggressive 보존(probe는 판단·튜닝, 사이즈컷/차단 X). 증거: polaris/core/probes/{catalog,engine,bus,tuning_log}.py.

## NEXT STRIKES — 즉시 큐 (순서)
- NS-1 [BUILD] **adaptive thesis 재활성**: grace+sustain fix(EXIT_THESIS_GRACE_SEC=25) 라이브 검증 → .env:104 POLARIS_EXIT_ADAPTIVE_THESIS=0 → 1 flip.
- NS-2 [BUILD] **4 신규 전략 라이브 운용 관측**(ema_crossover/connors_rsi2/supertrend/cci_reversion — 등록됨, 클린 ledger에 edge 누적).
- NS-3 [BUILD] **대시보드 clean-tables + probe-section + 모바일**: Bloomberg-dense restyle(wb02l90vv a-l), :8770/m 모바일, ★ twinkle, 데이터기반 종목명. SSE gate-decision 피드 라이브.
- NS-4 [BUILD] **probe-layer 정식화(P7.5)**: 5 canonical role 완성 + 매핑.
- NS-5 [BUILD] **AI advisor 계약(P7.2)**: ambiguous→Agent RAG+Vault escalation + Knowledge writeback.
- NS-6 [BUILD] **backend-pure loop**: P2.5(키정합 CI) → P5.2(correlation_group) → P1.1(intended-vs-expressed) → P2.3(cluster_cap equity).
- NS-7 [BUILD] **P0.2 dual-ledger surface** PF/WR/digest.
- NS-8 [BUILD] **forward edge-readout**: flow_pressure 0-크로싱(theta 0.32→0.40?), micro_reversion/tsmom retune, US장 Alpaca+bar edge → **PF>1 관측**(미해결 경험 질문, 데이터 누적 중).
- NS-9 [DEBATE] **Jin-surface deferred**: P1.2 LIVE-1 leverage 잔여, P2.4 Capital equity-CFD whitelist, P3.5 자본 재배분, replay nightly ops 등록.

## 실행 원칙·순서
측정(M)은 끝났다 → 이제 **forward(PROFIT/NEXT STRIKES)** 가 본선. 각 BUILD=TDD+적대검증(builder≠reviewer), 각 DEBATE=/debate 먼저→Jin 결정. 거동변경(sizing/execution)=Jin 결정 STOP 유지. DEMO·aggressive·flow_not_block·9-stack 봉쇄 유지. 거부키워드 0. 관련: [[system_design_audit_2026-06-22]] · [[research_agenda_2026-06-22]] · loop_state.md
