---
type: plan
status: active
date_created: 2026-06-22
tags: [plan, research-mesh, ai-conductor, evidence-fuser]
---

# Plan — Research-Agent Mesh (evidence → fuser absorption)

근거: `vault/50_research/debates/research_agent_mesh_2026-06-22.md` (GPT-5.2+Gemini-2.5-pro debate). 원칙: DEMO·aggressive·flow_not_block·surgical-strike·in-loop AI-free·봇 LLM=GPT·결정론 보존(틱 동기 LLM 호출 금지). 모든 에이전트 = fuser의 **비동기 collector**(새 control path 금지).

## 2 계급
- **런타임 evidence** (GPT·비동기·shadow→promote): 뉴스/이벤트 sentiment · 매크로 · 포지션 적정성 리뷰어
- **dev-time research** (오프라인·제안형): GitHub/유튜브/블로그 전략 마이너 → 전략 스펙 제안 → 리뷰 → 결정론 전략 구현 (거래루프 분리, 후순위)

## P0 — 계약 + 자동 가드 (레일, 코드 최소)
- `ResearchSignal{label|score, confidence∈[0,1], freshness_ts, half_life_sec, target_asset_keys[], view_targets[](regime|entry|exit), evidence_span, failure_mode_tags[]}`
- 자동 가드(차단 X, 신호 품질 점수화): ① event_id dedup `effective_weight=base/√dup` ② claim-grounding: evidence_span 비거나 근거 미뒷받침 → confidence=0 무력화 ③ per-source 동적 캘리브레이션(confidence_bucket→실현 edge 재스케일)
- source-weight clamp(0.75~1.25) 상속 · shadow Behavior-0(logged only) · acceptance metric 정의
- seam = 단일 fuser. `compose_exit_evidence_candidate` 소비자 추가(regime score + per-asset alpha score 분리 집계)

## P1+P2 — 병렬 shadow (Jin 2026-06-22: 둘 다 shadow)
- **P1 뉴스/이벤트 sentiment** collector(regime-evidence seam) — KPI: 이벤트후 30/120/360분 방향 hit-rate + cross-sectional(고/저 sentiment 진입군 PnL)
- **P2 포지션 적정성 리뷰어**(exit-evidence seam, **extend-only**) — KPI: 연장구간 PnL(FSM 엑싯신호~실제청산). 축소/조기청산은 결정론 FSM 유지
- 둘 다 logged-only → edge 입증된 쪽부터 conviction 실가중 promote

## 이후
- 매크로 evidence collector · P6 conductor(에이전트 3+ 교차합성) · dev-time 전략 마이너 트랙

## 검증
각 신규 = builder→fresh Claude 적대 리뷰(builder≠reviewer). P0=TDD(계약·가드 property test). promote는 acceptance metric 충족 시에만.
