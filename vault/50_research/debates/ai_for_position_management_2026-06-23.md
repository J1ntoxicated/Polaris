---
type: research
status: decided
date_created: 2026-06-23
tags: [debate, ai-intervention, position-management, exit, vault-rag, live-remap-engine]
---

# Debate — AI for live position management / exit re-mapping (2026-06-23)

## 안건 (Jin)
틱진입 말고 **포지션 관리(전략선택·모니터링·엑싯 리매핑)에 gpt-mini + 볼트(세컨브레인)를 유기적으로** 써야 하나?

## 맥락
W3 AI-free cutover(in-loop GPT=0). 이전 디베이트: 틱진입(flow_pressure)은 microstructure 분류·500ms라 deterministic 우월 → in-loop AI X. **관리/엑싯은 다른 결정**: 느린 케이던스(포지션당 초~분), 다요인 thesis-validity 판단. 측정: MFE +0.28R 도달→실현 −0.95R(give-back 1.23R).

## GPT + Gemini — 만장일치 수렴
- **D1 엑싯/관리 리매핑 = AI YES** (틱진입과 달리 AI가 진짜 우월한 곳; give-back 상처를 직격).
- **D2 전략선택 = AI는 모호/복잡 setup만**(명확한 건 deterministic cell-matrix/regime router).
- **D3 볼트 RAG = YES, 단 큐레이트** — setup/regime/failure-mode 태그된 압축 lesson 3-7개/결정, 포지션당 캐시. 거대 blob·매 recalc 강제 = over-engineering. 검색 실패해도 AI는 라이브 피처로 동작.
- **D4 AI-free 화해 = 원칙적 재도입(반전 아님)**. AI-free는 *틱진입의 잘못된 AI*를 고친 것. 관리는 느리고·콜 적고·맥락 풍부·판단가치 높음.
- **SAFE 패턴**: AI=async advisor(포지션당 10-30s + 이벤트: 신규 MFE고점·giveback임계·order-flow flip·regime변화·time-in-trade·profit-lock근접). 캐시 TTL. **결정론적 floor 항상 1차·비-override**(하드스톱·profit-lock·리스크캡). AI는 액션코드(HARVEST/CUT/RUN/TIGHTEN/WIDEN-bounded/RE-MODE)만, fresh+valid+guard통과시만 실행, 실패시 deterministic fallback. **AI는 진입차단·사이즈컷·throttle·loss-defense무력화 절대 X**(flow_not_block).
- **GPT 반론(채택)**: deterministic 관리 매트릭스가 대부분 잡을 수도 → **floor 먼저, AI는 그 위에만**.

## 결정
**floor(결정론 관리 매트릭스) → 측정 → AI advisor(gpt-mini async + 큐레이트 볼트 RAG, guarded).** 데이터 보강 4개 선행: 진입 thesis 기록 · MFE 궤적 · 큐레이트 볼트 RAG · per-position 컨텍스트 조립.

관련: [[flow_pressure_calibration_ai_2026-06-23]] · [[ADR-003-8-layer-architecture]] (L6 Live Recalc) · [[feedback_anthropic_dev_only_openai_runtime]]
