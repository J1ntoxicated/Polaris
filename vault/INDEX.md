---
entity_type: index
entity_id: master_index
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[_NOW]]"]
mode: meta
reviewed_by: jin
tags: [meta, index, polaris]
---

# INDEX — Polaris Vault Master Index

> 마스터 카탈로그. 매 작업 시작 시 [[_NOW]] 다음 read.

## 📜 Constitution (10_constitution/)

- [[north_star]] — Polaris 철학 (북극성 + SPOT-first 재정의)
- [[principles]] — 7 영속 원칙 (P1~P7)
- [[4_contracts]] — Authority / Lifecycle / Write Path / Validation Boundary
- [[governance]] — DRAFT / VERIFIED / AUTHORITATIVE 3단계 성숙도
- [[emergency_bypass]] — 긴급 fix 조건 + 24h 사후 산출물
- [[operating_model]] — 8 섹션 운영 모델 (모드/구조/agent/스킬/슈퍼브레인/볼트/seq thinking/리뷰)
- [[code_review_workflow]] — codex 외부 리뷰 의무 사이클

## 🏛️ Decisions (20_decisions/)

| ADR | 제목 | 상태 |
|---|---|---|
| [[ADR-001]] | SPOT-first fresh start (옵션 Y 확정) | applied |
| [[ADR-002]] | Vault-first architecture (v4 7계층) | applied |
| [[ADR-003]] | Codex debate protocol (max 3 라운드 합의) | applied |
| [[ADR-004]] | Code review codex external (Jin mandate) | applied |
| [[ADR-005]] | Harness 4 modes (DEV/ALPHA/FORENSIC/DEBATE) | applied |

## 💡 Insights (30_knowledge/insights/)

| INSIGHT | 제목 | 상태 |
|---|---|---|
| [[INSIGHT-001]] | Legacy spot pollution (6,263 라인 누더기) | active |
| [[INSIGHT-002]] | MTTR-alpha KPI 정의 | active |

## 📚 Lessons + Patterns

- [[30_knowledge/lessons/_README]] — 모태 5 핵심 lesson 인수 가이드
- [[30_knowledge/patterns/_README]] — anti-pattern 카탈로그 (작성 예정)

## 🧱 Components (40_components/)

- [[40_components/_README]] — curated summary 작성 가이드
- _자동 생성_: `vault/generated/components/` (gitignore, untracked)

## 📊 Runtime (50_runtime/)

- [[50_runtime/_README]] — daily log + audit append-only 가이드

## 🔬 Alpha (60_alpha/)

- [[60_alpha/_README]] — HYPO → BACKTEST → PAPER → Promotion Gate → ADR 워크플로
- [[60_alpha/_alpha_index]] — 가설 dataview 인덱스 (status별)
- `active/` `graduated/` `archived/`

## 📥 인수 큐

- [[_INHERIT_QUEUE]] — Codex 식별 8개 모태 인수 stub (Phase D 이후 추출)

## 🏷️ 태그 표준

- [[.tag_taxonomy]]
