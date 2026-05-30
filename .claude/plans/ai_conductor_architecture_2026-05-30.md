# AI Conductor 아키텍처 — technical-decides + AI-conducts (Jin 질문 답, Workflow wrx4mdhof + 4-lens 적대검증)

**Jin 질문**: "테크니컬하게 데이터 스트림으로 결정 가능하게 만들고 AI는 총괄 지휘 — 구조상 가능한가?"
**답 = YES (feasible).** 6 gate 전부 `fully` technical-replaceable. 4 lens 적대검증 endorse-with-changes(reject 0). 코드가 이미 절반 와있음.

## 결정적 증거 (진짜 결정은 이미 deterministic, GPT는 rubber-stamp)
- G7 실제 close 100% = FSM `evaluate_exit`(GPT 우회, `_production_recalc_exit.py:175-189`); GPT 46/46 HOLD.
- G6 hard stop + swap = GPT 전 Python fast-path(`position_monitor.py:92-105`); GPT 3434 HOLD / EXIT_NOW **1**(99.97%).
- G8 실제 학습 = posterior NIG μ/p_pos + cell EWMA(pnl_r/won만 소비); `ai_lessons` SELECT 0건(inert, 전역 grep 확인).
- G1 PASS 100%(selector), G3 PASS 90%/KILL 8%/MODIFY 2.4%, G4 PROCEED 90% — 입력 100% 숫자, 출력 enum+clamp scalar → LLM 언어능력 기여 0.

## Technical decision layer (deterministic, 데이터 스트림/지표/임계)
- **G1** focus → `deterministic_top_n`을 scored ranker로: `w1·log(vol)+w2·cell_quartile+w3·ATR/realized_vol+w4·per-sym hit-rate`, clamp 12-48. (eliminate-ai)
- **G3** validator → KILL/MODIFY 명시규칙(전부 payload 기존 필드). ⚠blocking 2·3 적용.
- **G4** watcher → `is_fast_path_eligible` 확장 + 30s tick-stream. ⚠codex: **spread/drift = MODIFY/flag default**(KILL 아님, shadow 캘리브 후만 승격), **realized-vol KILL 금지**("expanding=기회" 충돌 + `vol_target.py:57` 중복), **stale/crossed book만 KILL**.
- **G6** monitor → per-position GPT 분기 삭제, fast-path 유지 + winner-widen/momentum-failure 결정트리.
- **G7** exit → FSM 단일 소유. fading-regime MFE-giveback EXIT_NOW 분기 추가(⚠ protected_bep 부분중복 — 차이만 코드화).
- **G8** reflector → P0 Python 템플릿 영구화(거동 0).

## AI Conductor layer (per-signal 아님, 배치/트리거)
- **레짐 확정**(per-regime-change/5-15min) → G4 k/X/Y·G6 momentum 임계·G7 fading 정의 주입.
- **전략 선택/배분**(per-N-min/posterior 임계) → amplify/retire + 자본회전 후보.
- **주기 캘리브**(per-session/50-commit) → 모든 deterministic 임계를 실현 MFE/MAE로 재추정.
- **이상감지**(per-N-min/트리거) → focus churn·KILL blind-spot·overtrade·novel microstructure flag.
- **G8 synthesis**(per-N-closes/session-end) → cross-trade 패턴 → retire/amplify + knob 캘리브.
- 절감: per-signal in-token **95-99%**(G3 820k+G4 433k+G6 195k=1.45M→0), P1 호출 전부 제거($ 효과 더 큼), ~13000 calls/24h → 수십.

## 🔴 BLOCKING 3 (빌드 전 필수 해결)
1. **regime classifier 미구현(P0 stub)** — `regime_flip.py classify_regime`은 caller candidate 그대로 신뢰 → **confidence 전부 0.5 고정**(껍데기). ⚠**Jin 2026-05-30**: 레짐은 단일 글로벌 아니라 **per-(venue,symbol) 다이나믹**(구조 이미 존재: `regime_state(venue,underlying_group_id)` okx53/capital69, `compute_underlying_group_id` asset-class 계층 분기 crypto/forex/index/commodity, fuser prefix 라우팅). 빠진 건 두뇌. 실구현 = **계층 합성**:
   - **L1 global macro** (VIX/FRED risk tilt) — 배치, 전 자산 공통 베이스.
   - **L2 asset-class evidence** (fuser prefix 분기: crypto→funding/F&G, forex/commodity→macro, equity→gap) — **자산군별 민감도 차등 가중**(Jin thesis "매크로 민감한 아이는 매크로로").
   - **L3 per-ticker price action** (4h EMA20/50 cross + 24h ATR ratio + 5m-1h efficiency) — 개별 거동, 매 recalc.
   - `effective_regime[venue,symbol] = combine(L1,L2,L3)`, confidence = 축 일치도(0.5→동적), 2-consecutive confirm 유지. conductor 전 선행, 모든 technical 분기가 레짐 의존.
   - ⚠별개 universe 이슈: 현재 Capital이 crypto 알트 CFD(LIT/XLM/FET) 거래 중 → 의도한 FX/지수/금과 불일치(전부 `crypto:`). regime 구조 문제 아님, universe selection 별도 추적.
2. **net_edge_r gating 금지** — `net_edge.py` 자기부정("TRANSPARENT placeholder, NOT alpha, Do not trade off this number"). G3/G4 KILL/MODIFY 근거로 쓰면 안 됨. surface-only 유지(SKIP_ON_NEGATIVE=False). 대신 cell hit-rate/recent-loss.
3. **G3 cold-start KILL 근거 공백** — cell_matrix 8셀 중 7셀 n_eff<5 → 제안 KILL(quartile=='bottom' AND n_eff≥5 AND avg<0) 거의 미발동. ⚠codex 확정: **cold cell = KILL 절대 금지(pass-through, "모호하면 통과")**, warm(n_eff≥5)만 narrow KILL. cell-독립 booster(recent same-symbol 연속손실·spread/baseline·listing-age)는 optional 아님 — 미보강 시 day-1 deterministic KILL이 dead letter. P5 live 전 cold=pass-through lock(미준수=silent aggressive 위반).

**/debate 결론**: PROCEED_WITH_CHANGES (Claude 4-lens + codex 5관점 수렴, reject 0). 상세=[[ai_conductor_transition_2026-05-30]].

## Phased path (additive 무중단)
- **P0** shadow: technical 규칙을 GPT와 병행 계산·불일치 log(실결정은 GPT, 거동 0). 정량 acceptance gate + **복수 레짐 윈도우**(G3 KILL률 레짐별 8%~73% 변동 실측). + **regime classifier 실구현 선행**.
- **P1** G1 deterministic ranker 컷오버(2850콜→0, 최저위험). **P2** G8 P0 영구화 + conductor synthesis 신설. **P3** G6 분기 삭제(316 P1콜→0). **P4** G4 tick-watcher(3429→0). **P5** G3 컷오버(820k→0, 최종·최엄격 shadow). **P6** conductor tier 정식 가동.

## needs_debate (아키텍처 대규모 → /debate GPT+Gemini 의무)
1. G3 fail-closed→fail-open 전환(entry 안전성 거동). 2. net_edge_r gating 여부(BLOCK 2 — 금지 확정 교차검증). 3. deterministic 임계 캘리브(G4 k/X/Y·G7 FSM_R·trail·giveback·G1 w1-4 = 트레이딩 파라미터). 4. conductor tier 호출주기·SSOT·9-stack 비침범. 5. G6 momentum-EXIT_NOW + G7 fading-giveback 신규 exit 거동(실패모드 적대검증).

## 검증된 안전(blocking 아님, 코드 확인)
9-stack 비침범(G3 scalar[0.5,1.5]→continuous_scalar 단일 mult, `engine.py:124-145`) · hard-MAX 무변 · flow_not_block(FSM이 G6 GPT 전 실행) · aggressive 보존 · ai_lessons inert · FSM 정밀도 소유.
