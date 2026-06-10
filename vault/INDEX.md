---
type: runtime
status: active
date_created: 2026-05-06
tags: [index, catalog, bus-map]
---

# Polaris Vault INDEX — 3-Axis Bus Map

Vault = **세컨브레인 버스(bus)**. RAG 처럼 가운데 앉아 세 축을 잇는다:
**A1**(Claude + Jin — 설계/개발) ↔ **A2**(봇 — 전략/운영/거래) ↔ **A3**(DB — 로우데이터).
각 축의 허브 MOC 가 진입점이고, 세 MOC 는 서로를 backlink 로 연결한다.

## 3축 허브 (먼저 여기로)
- [[MOC-A1-design-dev]] — **A1** 설계 & 개발: 헌장 / ADR / 8-layer 컴포넌트 / 디베이트
- [[MOC-A2-bot-ops]] — **A2** 봇 전략 & 운영: 7 전략 / 다이제스트 / 대시보드
- [[MOC-A3-raw-data]] — **A3** DB 로우데이터: ai_lessons / trades / positions / quote_ticks (vault↔SQLite 다리)

## Tier-0 진입점 (mandatory first read)
- [[_NOW]] — 라이브 상태 (Tier 0, 세션 시작 필독)
- [[north-star]] — 0.75% primary / 1.25% stretch / aggressive bias
- [[ADR-003-8-layer-architecture|ADR-003]] — 8-Layer Architecture (아키텍처 척추)
- [[log]] — chronological 1-line append (NO interpretation)

## 폴더 지도
- `00_charter/` 헌장 · `10_decisions/` ADR-001..010 · `30_components/` layer-0..7 → **A1**
- `20_strategies/` 7전략 · `40_ops/` digests·daily·handover ([[MOC-digests]]) · `30_components/dashboard` → **A2**
- `data/polaris*.sqlite` (vault 밖) + `data/lessons_archive/` → **A3**
- `50_research/` debates·forensic·lessons ([[MOC-lessons]]) → A1 리서치
- `.templates/` ADR·INSIGHT·STRATEGY·COMPONENT·LESSON · [[.tag_taxonomy]] 태그
