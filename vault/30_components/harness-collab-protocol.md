---
type: component
component: harness-collab-protocol
status: active
date_created: 2026-05-28
date_updated: 2026-06-11
tags: [harness, collaboration, multi-agent, orchestration, builder-not-reviewer, workflow, loop]
related: [[ADR-001-vault-structure|ADR-001]], [[ADR-003-8-layer-architecture|ADR-003]]
---

# Harness Collaboration Protocol (Fable 구조)

메인 Claude(Fable) = **orchestrator + synthesizer**. 실무는 Workflow 스크립트의 sub-agent에 위임(context 오염 방지 + brain contribution). 본 문서 = 상위 orchestration SSOT.

## 작업 모드 (Jin 2026-05-29 mandate — Fable Workflow 도구로 구현)
기본 = **다이나믹 멀티에이전트 Workflow 스크립트** (단발 Agent 수동 멀티 호출 대체):
- **fan-out** — parallel(): 차원/항목 병렬 분해 (예: 서브시스템 구조매핑 8-reader)
- **pipeline** — pipeline(): design → build(TDD) → adversarial review, 항목별 무배리어 흐름
- **루프** — 아래 § 루프 3계층
- **adversarial verify** — 발견별 반박 agent. 실증: 2026-06-10 진단서 1000× 단위오독·오진 다수 적발
- **schema 출력** — 구조화 강제로 메인 종합 비용 최소화
직접/단발 = trivial·대화·단일 known target·즉각 1-edit만. 토큰 비제약, 큰 웨이브는 Jin 사전 1줄 고지.
위임 agent = 자율 실행 주체: 하위 agent spawn · skill · vault r·w · sequential-thinking 자유 소환(CLAUDE.md agent-definition과 동일).

## 루프 3계층
- **Workflow 내 루프**: loop-until-dry(K회 연속 무소득까지 탐색) · loop-until-count · **FixLoop**(적대리뷰 블로커→빌더 수정→3렌즈 재검증, 블로커 0까지, 기본 2라운드 캡)
- **/loop 세션 루프**: 프롬프트/스킬을 인터벌 or self-paced(ScheduleWakeup) 반복 — 봇 라이브 베이비시팅·CI/배포 등 외부 상태 폴링용
- **cron 루틴**: 스케줄 원격 agent — 일일 재기동(WAL reset)·주기 vault 리뷰 등 운영 자동화 후보

## Agent roster (Fable)
| 주체 | 역할 | reviewer? |
|---|---|---|
| (main) | 위임 결정 · 종합 · vault 기록 · 비가역 작업(DB 변형 등) 승인 게이트 | — |
| Workflow 빌더 | TDD 구현. self-review 금지 | ✗ |
| Workflow 리뷰어 (fresh Claude) | 설계검토 + 3렌즈(technical/policy/livepath) 적대 리뷰 | ✓ |
| Explore / Plan / general-purpose / code-simplifier | 탐색 · 설계 · 다단계 · 리팩토링 | — |
| codex | on-demand만 — dev 리뷰/디베이트 금지(Jin 2026-05-31 no-dev-GPT, 메모리 영속) | △ |

## Builder ≠ Reviewer (개정 2026-05-31)
작성 주체 self-review 금지(confirmation bias). 신규 코드/spec/rule → **fresh Claude sub-agent 리뷰 의무**(pipeline review 단계 내장). 구 "codex 외부 review 의무" 조항은 Jin 2026-05-31 no-dev-GPT 결정으로 대체(CLAUDE.md 동기 갱신 2026-06-11).

## Handoff triggers
- 5+ 파일 read / codebase-wide search → **graph-first**(codebase-memory `search_graph`/`trace_path`/`get_architecture`로 LOCATE), 부족 시 Explore·general-purpose·Workflow reader fan-out + 실 read
- 신규 코드·거동 변경 → Workflow pipeline(design→build→adversarial review→FixLoop)
- 큰 wave 검수 → **5-axis**(technical / 4-axis policy / cumulative coherence / functional / live audit) — functional/live audit은 배포 후 라이브로 완결
- 거부 키워드 hit / 9-stack·sizing 변경 / vault write 충돌 → 전담 단계 · 오염 신호(Read 5+/grep 100+) → **graph-first** 후 위임 전환 · 단일 known target → 직접
- **Graph↔Vault 라우팅** ([[ADR-014-graph-index-reference-bridge|ADR-014]]): **그래프로 LOCATE, 볼트로 JUDGE.** investigator=그래프 먼저(`search_graph`/`trace_path`)→볼트 why · builder=볼트 mandate 먼저→그래프 blast-radius(`detect_changes`) · reviewer=`detect_changes`+`trace_path` 발화경로 증명→볼트 policy. 🚨 그래프=**CACHE·dev-time-only**(실행 봇 미접촉·trade path 무관), 의심 시 `get_code_snippet`/실파일 재확인. 충돌 시 볼트 승.

## Sub-agent 프롬프트 헤더 (의무)
DEMO/PAPER 명시 + aggressive bias + 거부 키워드 sweep(목록 SSOT = CLAUDE.md rejection-keywords 블록) + vault r·w 권한(brain contribution) + 증거 의무(file:line/SQL 숫자) + 라이브 DB는 ro URI+명시 close(hung-reader 교훈) + length cap.

## Super-brain 합주 (비-자명·trade-param 결정)
vault read → sequential-thinking → /debate(GPT+Gemini — dev 작업 제외) → vault update. 증거 자명한 버그픽스는 비대상, /debate 후보는 플래그·보류(예: min-deal 정책, trail 캘리브, COT 임계).

## Brain contribution (의무)
wave 종료 시 vault append(lesson/digest/ADR + log.md 1줄 무해석). sub-agent vault write 가능하되 병렬 충돌은 자기 namespace draft 또는 **메인 종합 기록(기본)**으로 회피. 테스트의 실 vault 오염(bootstrap 노이즈)은 결함 — 격리 follow-up.

## Format 규약
agent instruction = 간결 md+XML 태그 · vault = md+frontmatter+wikilink(XML/HTML 금지, graph 보존) · config/state = JSON/SQLite · rendered report = HTML(대시보드만).
