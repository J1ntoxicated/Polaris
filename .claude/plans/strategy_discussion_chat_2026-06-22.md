---
type: plan
status: draft
date_created: 2026-06-22
tags: [plan, dashboard, chat, second-brain, ai-conductor]
---

# Plan — Strategy Discussion Chat (3-axis 위 대화 레이어)

Jin 아이디어(2026-06-22): 3축 세컨브레인(A1 설계·A2 봇·A3 데이터)에 **전략·거래내용·봇 거동을 디스커션하는 챗**을 붙임. **계획만 — 빌드 보류.**

## 목적
운영자(Jin)+AI가 자연어로: (a) 전략 추가/제거/가중, (b) 거래 리뷰("왜 이 거래, 적정했나"), (c) 봇 거동 결정("어떻게 움직이게") 을 논의 → 결정이 vault(ADR/plan)·봇 config로 흐름. 진단(−56R, volume_burst 누수 등)을 두고 "그래서 뭘 바꿀까"를 데이터 옆에서 바로.

## 컨텍스트 소스 (읽기)
- vault 3축: MOC-A1/A2/A3 + ADR + 전략 spec + 진단/디베이트 문서
- DB: positions·trades·strategy_stats·confidence·cell_matrix·learner posterior
- 라이브 스냅샷: `/api/snapshot` (PF·WR·전략별 손익·슬리피지·alerts)
- 진단 산출물(per-ticker 퍼포먼스·누수)

## AI 제공자
**GPT 전용**(봇 LLM 일관, Anthropic 금지). 컨텍스트 = vault retrieval + 스냅샷 inject.

## 형태 후보
1. **대시보드 우측 패널 챗** (스냅샷+vault context inject) — 운영 흐름과 가장 가까움 ← 유력
2. vault 내 챗 노트(Obsidian)
3. 별도 CLI/웹

## 출력 → 액션 (경계)
- 챗 결정 → vault append(ADR/plan/lesson) + (승인 시) 전략 config **변경 제안 생성**
- **결정론 거래루프는 직접 안 건드림**: 제안→리뷰→반영, shadow→promote 일관
- 챗은 '논의·결정 보조'지 in-loop 거래 결정자 아님. aggressive·flow_not_block 보존. 거부키워드 0

## 선행조건
**진단 fix(텔레메트리 정직화) 후 착수** — 거짓 데이터 위 논의 방지.

## 미해결 (빌드 전 결정)
형태 선택 · context window/retrieval 관리 · vault write 권한 범위 · GPT 비용. 관련: [[research_agent_mesh_2026-06-22]](AI-conductor evidence seam과 연계 가능)
