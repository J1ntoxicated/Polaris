# Model Strategy — Opus 단일 + Effort 가변 (Jin 2026-04-20)

> 모든 agent / advisor / harness = **claude-opus-4-7 단일 고정**.
> 사고 깊이는 모델이 아닌 **reasoning effort** 로 제어.

## 단일 모델 정책

| Component | 모델 | 비고 |
|---|---|---|
| Harness | **opus-4-7** | 1M context, 항시 |
| 모든 advisor (15개) | **opus-4-7** | frontmatter `model: opus` 통일 완료 |
| dev-coder / ops-executor | **opus-4-7** | 직접 편집 + commit |
| smoke-runner / log-tail 등 단순 task | **opus-4-7** | effort=low 로 비용 절감 |

## Effort 선택 가이드 (자율)

| Effort | 사용 시 | 예시 |
|---|---|---|
| **low** | 단순 lookup / count / file read 1건 | `tail -5 log`, `SELECT COUNT(*)`, frontmatter 확인 |
| **medium** | 일반 batch / patch / commit (default) | 30min batch, 1-hunk fix, smoke 5-step |
| **high** | root-cause / architecture / forensic / debate | 다층 SQL forensic, regime 공식 audit, 전수조사 |

## 적용 방법

agent dispatch 시 prompt 마지막에 명시 (선택):
```
[effort: low]   # 단순 task
[effort: high]  # 복잡 root-cause
```

명시 없으면 medium default. 비용:
- low ≈ 0.3x medium
- high ≈ 3-5x medium

## 변경 이력

- **2026-04-20 17:15**: Sonnet/Haiku 폐기 → Opus 단일. effort 가변으로 사고 깊이 제어.
- 2026-04-16: 3-tier (Sonnet default / Opus deep / Haiku fast) — DEPRECATED.

## 참조

- `.claude/agents/*.md` — 15개 모두 `model: opus`
- `.claude/commands/harness-mode.md` — 단일 모델 정책 명시
