---
type: research
status: active
date_created: 2026-06-22
tags: [debate, research-mesh, ai-conductor, evidence-fuser, architecture]
---

# Debate — Research-Agent Mesh → Bot Absorption (2026-06-22)

GPT-5.2 + Gemini-2.5-pro 적대검증. 안건: 리서치 에이전트(뉴스·매크로·포지션리뷰어·전략마이너) 결과를 메인 봇이 흡수. DEMO/aggressive/surgical-strike, in-loop AI-free, 봇 LLM=GPT.

## D1 — 단일 seam vs 분리 seam → **수렴: 단일 seam + schema 보강**
- GPT: 단일 seam, `ResearchSignal`에 `targets{regime|entry|exit|asset_specific}`·`half_life_sec`·`failure_mode_tags` + `compose_exit_evidence_candidate` 소비자 추가.
- Gemini: 단일 seam, `target_asset_keys[]` → fuser가 regime score + per-asset alpha score 동시 가산 → 개별 뉴스가 regime 안 흔들고 G7 exit만 정밀 타격.
- 양쪽 독립적으로 같은 약점(개별 자산 idiosyncratic risk → regime로만 환원) 지적. **결론: 단일 seam, ResearchSignal에 asset_keys+view target 보강, exit-evidence는 같은 seam 위 별도 소비자.**

## D2 — MVP: 뉴스(P1) vs 포지션리뷰어(P2) → **분기 (Jin 결정)**
- GPT → **P1**: P2는 결정론 FSM vs LLM '권위 충돌' 위험. P1은 cross-sectional edge 측정 명확.
- Gemini → **P2**: aggressive 봇 최대 리스크=엉성한 엑싯. P2는 extend-only라 방어화 불가(공격성↑만). KPI=`연장구간 PnL`(FSM 엑싯신호~실제청산 사이 PnL>0 = 가치 입증).
- 종합: shadow-first면 '권위 충돌' 소멸(미행동) → GPT 우려는 promotion에서만. Gemini KPI가 counterfactual 근사. **P2-shadow-extend-only 권고, 단 Jin 결정.**

## D3 — 정규화 충분성 → **수렴: 불충분, 자동 가드 3 (차단 X)**
- GPT: ① event_id dedup(`weight/√dup`) ② claim-grounding(`evidence_span` 비면 confidence=0) ③ confidence_bucket→실현edge 재스케일.
- Gemini: ① evidence 자동 역참조(경량 워커가 ref 스크랩+2차 LLM "근거 뒷받침?" → 아니면 confidence=0) ② per-source 동적 신뢰도 캘리브레이션.
- 수렴: **claim-grounding(confidence→0) + event_id dedup + per-source 동적 캘리브레이션. 인간 sign-off/차단 없음(flow_not_block).**

## 확정 플랜 (디베이트 반영)
- **P0 계약**: `ResearchSignal{label|score, confidence, freshness_ts, half_life_sec, target_asset_keys[], view_targets[], evidence_span, failure_mode_tags}` + event_id dedup + claim-grounding(conf→0) + per-source 동적 캘리브레이션 + source clamp 상속 + shadow Behavior-0 + acceptance metric.
- **MVP**: D2 결정 후 (P2-shadow 권고).
- **이후**: 나머지 에이전트 fuser collector로 순차 shadow→promote. P6 conductor / dev-time 전략마이너(오프라인) 후순위.

## 결정 (Jin 2026-06-22)
D2 = **둘 다 shadow 병렬** — P1·P2 collector 동시 shadow(무행동·무위험), edge 데이터(P2 연장PnL vs P1 cross-sectional)로 promotion 순서 결정. 가장 데이터 주도. 관련: [[system-architecture-map]] · [[research_agent_mesh_2026-06-22 plan]]
