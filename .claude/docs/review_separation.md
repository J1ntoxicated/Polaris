# Review Separation — 작성자 ≠ 리뷰어

> Jin 2026-04-16 원칙. 자기 코드 자기 리뷰 = 눈감음. 통합 세션 (2026-04-19) 에선 **agent 단위**로 적용.

## 핵심 원칙

**작성한 주체는 리뷰 불가.** 품질·구조·논리 검증은 **다른 주체** 가 수행.

## 리뷰 경로

| 작성자 | 1차 리뷰 (필수) | 2차 리뷰 (옵션) |
|--------|----------------|----------------|
| dev-coder (코드 편집) | Harness (architectural) | codex:codex-rescue |
| ops-executor (config) | Harness | dev-audit-advisor |
| Harness 직접 (.claude/*, docs/*) | dev-audit-advisor | codex:codex-rescue |

- **dev-coder 자기 리뷰 금지** — commit 직후 Harness 가 dev-audit-advisor / dev-wire-guardian dispatch 로 검증
- **Harness 는 dev-coder 를 리뷰 가능** (Harness 가 직접 편집 안 함, 자연 분리)
- **Codex 2nd-opinion** 트리거 (Harness 자율): 확신 부족 / 2hop+ root-cause / 판단 충돌 / architecture 급 변경

## Codex 호출 (Harness 인라인)

```
Skill(skill="codex:rescue", args="<scope>")
# 또는
Agent(subagent_type="codex:codex-rescue", prompt="...")
```

결과는 Harness 세션 내 직접 소비 (구 `harness_to_*.md [CODEX-RESULT]` 파일 IPC 폐기).

## 금지

- ❌ dev-coder 가 자기 commit 에 `self-review: OK` 붙이기 (Harness 몫)
- ❌ 동일 agent 재호출로 "리뷰 완료" 간주
- ❌ Codex 미호출 + Harness 스킵 → Jin 직접 merge 요청 금지

## 예외 (경미)

- **1-line config flip** (live_config.json 값) — ops-executor 단독 OK
- **Typo / comment fix** — 리뷰 skip 허용
- **문서 편집** (.claude/docs/**) — Harness 단독 OK

## 참조

- `feedback_codex_harness_mediated` — Jin 04-16 22:27 Codex IPC 정책
- `feedback_agent_crossreview_mandatory` — agent cross-review 의무
- [harness-mode.md](../commands/harness-mode.md) — 통합 세션 운영
