# P3 Self-Evolve — Design Spec (2026-06-01, REVISED post-debate)

Parent SSOT = `.claude/plans/master_overhaul_2026-05-31.md` (#13). Grounded by code-map + research workflows. **2-source debate (Claude 6-lens=RECONSIDER, codex=CONFIRM_WITH_CHANGES) → Jin accepted REFRAME "증명 먼저, 장치는 나중".** Debate record = `vault/50_research/debates/p3_self_evolve_2026-06-01.md`. Related: [[project_self_evolving_vision]] · [[project_ai_conductor_direction]].

## 0. Goal & Non-Goals
**Goal**: 봇 + Claude + Jin 셋이 수익. 봇=자율진화, Claude=개발, Jin=관찰·steering.
**Non-goal**: 독립 지식-상품/RAG 플랫폼 ❌. 기존(replay·altdata·posterior·cell-matrix·swap실행기·vault_lint) 위에 얇게.
**🔴 REFRAME (debate)**: near-term 최대 레버 = **fee/churn 제어 + 새 feature(alt-data) + exit 정밀도**지, config-변이 생성기가 아니다. **생성기는 "edge를 찾을 수 있다"가 증명된 뒤** 짓는다(KILL-스파이크 게이트).

## 1. 정직한 진단 (debate, 코드검증)
- **🔴 alt-data가 전략에 0개 도달** (grep 검증): funding/OI/COT/sentiment는 **게이트만** 소비, `MarketView`=고정 TA ~15개, 전략은 그것만 봄. → config 변이는 **이미 FAIL 난 고정 피처 공간 재탐색** = "약한 전략 재발견". *없는 edge는 생성기도 못 만듦.*
- **"전략=config dict는 얇은 seam"이 거짓** — config 구동 전략 런타임 부재(params/from_config/PARAM_BOUNDS=0). greenfield 서브시스템.
- **검증 스택 전부 greenfield + 굶음 위험** — honest-N 레지스트리/CPCV/corr-dedup 미구현, 단일후보 시 DSR=PSR 자가비활성, walk-forward OOS(`is_oos_spread`)는 어디서도 게이팅 안 함(hardcode 0). 전역 FAIL 데이터에서 per-cell DSR≥0.95 거의 안 통과.
- **fee/churn** = 구조적 우려(taker fee floor vs 작은 per-trade alpha, 짧은 hold). **데이터 caveat**: profit-skeptic의 "net -$4,382 / fees 3.2x / 19min"은 **stale `polaris.sqlite`(5/6~10, simulate-only 가능)**; 라이브 `polaris_live.sqlite`=973 fills(too small). **그 수치는 미채택** — 단 fee/churn 분석 부재 + 구조적 우려는 유효.
- **"+1.18R n=87"(부모 SSOT) ↔ _NOW 정직값 "+0.07R, p_pos<0.5" 모순** — 정당화는 정직값(edge marginal)에 의거.
- **과설계** — RAG 의미검색·키퍼 T1/T2·3축 형식화·밴딧·C1 라이브 = edge 증명 전 cut/defer (Jin "상품 아님" steer 일치).

## 2. Mandate-fit (모순 수정 포함)
- **9-stack 봉쇄**: 생성기/residual은 **T4 사이징 슬롯 0 추가.** C1 residual(빌드 시)=routing **score INPUT만 tilt**, 새 mult factor 아님. 사이징 레버=signal strength만, **`sizing_hint`는 변이 대상 아님**.
- **flow_not_block**: P3는 **새 dampen 0.** 기존 {1.5/1.0/0.5} quartile·`REGIME_ALIGN_DAMPEN=0.8`은 **선재(pre-existing, 미접촉, 재배분이지 block 아님)**. ~~"amplify-only floor 1.0"(mk1 어조)~~ 철회 — 모순 제거; P3는 기존 envelope 안에서 재랭크, min은 기존 0.5(≠0). **veto→"deprioritize"(풀에서 제거 아님)**.
- DEMO/PAPER · 거부키워드 0 · dev=Claude/봇=GPT · **OKX 무중단 + 새 write는 single-writer 동시성** 모델.
- **behavior-0 → shadow-first**, replay 게이트=유일 판사, 자동승격은 **증명 + decay-demotion 게이트** 뒤.

## 3. REVISED Phasing (증명 먼저; 각: Workflow design→TDD→fresh-Claude 적대리뷰→behavior-gate→커밋, real-fee)
- **P0a — KILL-스파이크 (가설 싸게 검증)**: seam `ReplayEngine(strategies=)` + 기존 7~10 전략의 **bounded numeric config 변종** + 외부 **per-cell honest-N 레지스트리** + 기존 게이트(real-fee, OOS 배선)에 **오프라인** 통과율 측정. **🚦DECISION GATE: ~0 통과 → 피처 공간이 병목 → 생성기 안 짓고 feature/fee로 redirect. >0 → 탐색공간 존재.**
- **P0b — fee/churn + exit (near-term 진짜 레버)**: 이미 빌드된 **entry_admission regime-gating + anti-churn B 켜기** + turnover/fee-drag 측정(라이브 DB 누적分) + **live exit recompute 배선(stub→live)** + **exit 파라미터 진화**(trail/BEP/max-hold/session, replay 검증) = surgical-strike. + **키퍼 T0**(vault findability: cell-key 색인·backlink·lint·banned 청소 — Jin findability ask).
- **P1 — features (진짜 edge 레버)**: **alt-data→MarketView 전략가시 피처**(crypto funding/OI/liquidation, FX COT/positioning; 기존 altdata 프레임워크). 확장 공간에서 KILL-스파이크 재실행.
- **P2 — 생성기 (P0a/P1 유망할 때만)**: config-dict 런타임 + 변이(bounded numeric 먼저, structural 나중) + AlphaAgent 반-decay critic(pre-filter, **로깅**) + 승격게이트(honest-N DSR + CPCV + corr-dedup + marginal-pool + OOS) + **decay-demotion** + cold-start seed(기존 config + Alpha101).
- **DEFER (edge 증명 & 생성기 가동 후)**: RAG 의미검색 · 키퍼 T1/T2 · 3축 ADR-009 형식화 · Thompson 밴딧(그전엔 deterministic largest-posterior-gap picker) · C1 라이브 routing residual · portfolio construction/Black-Litterman(P5).

## 4. Validation 정직성 (greenfield 현실)
- **honest-N 레지스트리 = greenfield**: per-cell 후보 카운트, 재시작 across 영속, DSR에 **모든** trial 반영(생성기 search breadth).
- **walk-forward OOS를 pass 결정에 배선**(현 `is_oos_spread` 미게이팅) + **CPCV/purge/embargo**(embargo=exit horizon) 실구현(principle-only 금지) + corr-dedup + marginal-pool.
- **promotion-rate 기대치 + dry-loop 예산 cap**: 게이트가 N후보 동안 비면 → Jin escalate(피처가 병목). LLM batch-spend 상한.

## 5. 누락 방향 (이제 in-scope)
exit 진화(P0b) · decay-triggered auto-demotion(P2) · cold-start seed(P2) · **OKX SPOT long-only 명시**(direction축 생성은 CFD/short family만) · 3축 관측면(P0b: 기존 대시보드에 evolve snapshot) · 키퍼 실패모드(T0: 트랜잭션/스냅샷 write, backlink 무결성) · 단일-writer DB 동시성(자동승격 swap + 키퍼 batch + 라이브 틱 write).

## 6. Borrow / Skip (리서치)
**Borrow(원리만, 생성기 단계서)**: RD-Agent 루프 · QuantaAlpha 국소변이 · AlphaAgent 반-decay 정규화 · DSR/CPCV honest-N · marginal-pool(AlphaGen) · Alpha101 seed · 무료 collector(P1, 갭 기반 1개씩).
**Skip**: RD-Agent/qlib/nautilus/Lean 통째 · 새 백테스터 · 독립 임베딩 store · 임의 코드 exec · per-signal LLM · 유료/지연 데이터 · FinRL RL.

## 7. 3-Axis (개념 모델, 형식화는 defer)
A1 봇 / A2 키퍼(현재 T0만) / A3 우리. vault=공유 버스. 관측/제어는 P0b서 문서화하되 **ADR-009 형식화·하네스 1급 구조는 edge 증명 후로 defer**(Jin "상품 아님").

## 8. Decisions log + Debate verdict
시작=LLM 생성기 → **debate REFRAME=증명 먼저** · 메트릭스=계층풀링(C1 라이브 defer) · AI=제안+비판(판사 아님, replay가 판사) · 권한=완전자율 replay-게이트+decay-demotion · vault=2-tier(키퍼 T0만 now) · **생성기는 KILL-스파이크 게이트 뒤** · mandate 모순(amplify-only/sizing_hint/veto) 수정 · fee/churn 수치는 stale DB라 미채택(구조적 우려만).
**Debate**: Claude 6-lens RECONSIDER(weak/broken/weak/adequate/weak/weak) + codex CONFIRM_WITH_CHANGES → 수렴: 장치 전 edge 증명, fee/churn·feature·exit 우선. Jin 수용 2026-06-01.
