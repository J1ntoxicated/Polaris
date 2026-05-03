---
entity_type: constitution
entity_id: operating_model
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[4_contracts]]", "[[code_review_workflow]]", "[[INDEX]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: ADR-002, ADR-005, codex-debate 3 라운드 합의
tags: [type/constitution, status/active, polaris, operating_model]
---

# Polaris 운영 모델 v1 — 8 섹션 (vault SSOT)

> Jin 2026-05-03 mandate: 운영 모델 메타-구조 먼저 정의 후 코드 진행. 이 파일이 모든 작업의 기반.

## §1. 하네스 4 모드

| 모드 | 트리거 | 활성 agent | 활성 스킬 | 외부 advisor | Vault 동작 |
|---|---|---|---|---|---|
| **DEV** | 코드/컴포넌트 작성·수정 | code-implementer | superpowers:TDD/brainstorm/plan/verify, polaris:vault-first-cycle | Codex (코드 리뷰 의무) | read 40_components → write code → write component note |
| **ALPHA** | 가설 검증·백테스트·페이퍼 분석 | vault-curator (가설 노트) | superpowers:brainstorm/plan | Codex (가설 검토) + Gemini (빠른 통계) | read 60_alpha → BACKTEST/PAPER 결과 → ADR provisional |
| **FORENSIC** | 운영 이상 감지·근본 원인 추적 | forensic-investigator | superpowers:systematic-debugging, sequential-thinking | Codex (디베이트 시) | read DB/logs → write 1 INSIGHT (메타 작업 한도) |
| **DEBATE** | "모르겠다" 결정 (Jin 명시 또는 high-stakes) | codex-debate-partner | sequential-thinking, superpowers:brainstorm | Codex (반드시) + Gemini (선택) | read 관련 ADR/INSIGHT → write ADR provisional |

**모드 전환 규칙**: 한 작업 = 한 모드. 모드 변경 시 explicit transition (이전 모드 산출물 closing → 새 모드 진입). 모드 혼합 금지.

## §2. 하네스 구조 (.claude/)

```
.claude/
├── settings.json           # hook 등록 + polaris config
├── settings.local.json     # 로컬 override
├── agents/                 # 4 agent definition
│   ├── vault-curator.md
│   ├── code-implementer.md
│   ├── forensic-investigator.md
│   ├── codex-debate-partner.md
│   └── _DEPRECATED/        # 모태 16 agent 보존 (참조, invoke X)
├── commands/               # 8 commands
├── hooks/                  # 4 hook
│   ├── pre_commit.py       # vault_lint 통과 검증
│   ├── post_edit.py        # 코드 변경 → 40_components 갱신 알림
│   ├── post_stop.py        # _NOW 갱신 점검
│   └── pre_agent.py        # 4 contract + 모드 책임 위반 검사
├── docs/                   # 운영 가이드
└── plugins/                # superpowers, codex 등
```

## §3. 4 에이전트 책임 매트릭스

| 작업 | vault-curator | code-implementer | forensic-investigator | codex-debate-partner |
|---|---|---|---|---|
| Vault 노트 read | ✅ | ✅ | ✅ | ✅ |
| Vault 노트 write/edit | ✅ | (40_components만) | (1 INSIGHT만) | (ADR provisional만) |
| 코드 read | (참조용) | ✅ | ✅ | (참조용) |
| 코드 write/edit | ❌ | ✅ | ❌ | ❌ |
| Test write/run | ❌ | ✅ (TDD 의무) | ❌ | ❌ |
| Codex 호출 | ❌ | (리뷰만 받음) | (debate 시) | ✅ (의무) |
| Constitution edit | ❌ | ❌ | ❌ | ❌ (Jin only) |
| ADR ack | ❌ | ❌ | ❌ | ❌ (Jin only) |
| **본인 코드 리뷰** | **❌** | **❌** | **❌** | **❌** |

위반 시 `pre_agent.py` hook 차단.

## §4. 스킬 사용 매트릭스

| 트리거 상황 | 스킬 | 비고 |
|---|---|---|
| 새 기능/구조 결정 전 | `superpowers:brainstorming` | spec → user 승인 → writing-plans |
| Spec 확정 후 | `superpowers:writing-plans` | plan → user 승인 |
| 코드 작성 (모든 신규/수정) | `superpowers:test-driven-development` | TDD 의무 |
| 작업 완료 직전 | `superpowers:verification-before-completion` | evidence 의무 |
| 버그/test 실패 | `superpowers:systematic-debugging` | fix 제안 전 |
| 작성된 코드 리뷰 받기 | `codex:rescue` 또는 codex agent | **모든 신규 코드 직후 의무** |
| 다층 사고 결정 | `mcp__sequential-thinking__sequentialthinking` | §7 트리거 |
| Vault 작업 시작/끝 | `polaris:vault-first-cycle` | read → plan → code → update → lint |

## §5. 리즈닝 슈퍼브레인 (메타-인지 layer)

```
[입력: 비-자명 결정]
  ↓
[1. Vault Read] 관련 ADR/INSIGHT/lessons/components 검색 (curator)
  ↓
[2. Sequential Thinking] mcp__sequential-thinking 5-15 thoughts
  ↓
[3. Codex Debate] 비판적 검토 → 합의까지 (max 3 라운드)
  ↓
[4. Vault Update] 합의 결정 → ADR provisional + 관련 INSIGHT
  ↓
[출력: 결정 + vault 영구 기록]
```

**발동 트리거**: 새 feature/architecture, 알파 가설 승격, 코드 리뷰 의견 차이, "모르겠다" 명시.
**우회 가능**: 자명한 변경, 이미 ADR 결정 단순 적용, 긴급 fix (24h 사후 사이클).

## §6. Vault 사용 사이클 (모든 작업 표준)

```
1. READ    _NOW + 관련 components MOC + 관련 ADR/INSIGHT
2. PLAN    INSIGHT/ADR stub 또는 update 계획 (백링크 ≥ 2 확보)
3. EXECUTE 모드별 작업 (DEV: 코드 / ALPHA: 백테스트 / FORENSIC: 추적 / DEBATE: 사고)
4. UPDATE  components 노트 갱신 + INSIGHT/ADR + _NOW 갱신
5. LINT    vault_lint --karpathy 통과 (0 violation)
6. (코드 시) CODE REVIEW codex 외부 → 합의 → commit
```

## §7. Sequential Thinking 사용 패턴

**의무**: 새 feature/architecture, 코드 리뷰 의견 통합, forensic 가설 분석, "모르겠다" 진입 전.
**선택**: TODO 추적, spec self-review.
**금지**: 자명한 변경, 이미 ADR 결정 재사고.
**깊이**: 5-15 thoughts. 마지막 thought = "결론 + 다음 액션". 결과는 최소 1 INSIGHT/ADR 기록.

## §8. 코드 리뷰 워크플로

상세는 [[code_review_workflow]]. 핵심: 작성 agent ≠ 리뷰 agent. codex 외부 리뷰 max 3 라운드 합의.
