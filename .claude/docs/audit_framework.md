# Harness 주기 감사 프레임워크

이벤트 트리거 기반 자가학습 메타레이어. 의미 있는 변화 누적 시만 실행.

## 아키텍처: 시간 루프 + 이벤트 태스크 (하이브리드)

- **세션 루프**: dynamic 주기 (자기 깨움)
- **감사 실행**: 이벤트 트리거 (commits/trades/errors 누적)
- 매 주기 Harness가 카운터 체크 → 임계치 넘은 감사 실행

## Session Bootstrap

- `/harness-mode` → [harness-mode.md](../commands/harness-mode.md) (통합 세션 진입점)

**Harness 관리 책임**: loop.md / harness-mode.md 동기화. 새 agent 추가 시 .claude/agents/ 업데이트. 주 1회 감사. 구식 지침 즉시 수정.

## 감사 카탈로그 (이벤트 트리거)

**원칙**: 커밋 수 ≠ 신호. 변경 **볼륨**으로 판단.

| 감사 | 트리거 | 측정 | 실행 agent | 라우팅 |
|------|-------|------|-----------|--------|
| 하드코딩 | invasion/* 500 lines | `git log --numstat` 합산 | dev-refactor-advisor | dev-coder dispatch |
| 파라미터 적정성 | 50 trades | `SELECT COUNT(*)` delta | ops-param-tuner | ops-executor dispatch |
| 로그 커버리지 | 5+ 파일 수정 | `git diff --name-only` | ops-log-quality-auditor | dev-coder dispatch |
| 에러 패턴 | 20 errors 누적 | `grep -c ERROR` delta | ops-log-advisor | dev-coder or ops-executor |
| 전략 성과 분포 | 50 trades OR >60% 점유 | trades + strategy | ops-trade-forensic | ops-executor |
| Evolver 작동 | 100 trades OR Elo entropy ↓ | Elo metrics | ops-trade-forensic | dev-coder |
| 대시보드 규격 | dashboard/*.py commit | path match | dev-audit-advisor | dev-coder |
| harness_items 정합 | 50 items OR daily | `wc -l` delta | Harness 자체 | — |
| .claude 정합 | .claude/* commit OR daily | commit path | harness-drift-detector | Harness 자체 |
| 모듈 구조 | invasion 3000 lines or 새 모듈 | git + find | dev-audit-advisor | dev-coder |
| Quarantine 분포 | 주간 OR signal_blocks write ↑ | `quarantined_*` + `signal_blocks` rows | ops-quarantine-reviewer | Harness |
| Session × Exchange | cell_matrix write OR subsystem_sizing alert | cell score × session dim | dev-session-axis-auditor | Harness |
| FK Linkage ratio | signals write 변경 OR daily | `COUNT(trade_id IS NULL)/COUNT(acted_on=1)` | dev-trace-linker | dev-coder |
| Cell SSOT | 매 commit OR 새 mult layer 감지 | mult chain depth + hardcode decision site scan | dev-audit-advisor / dev-refactor-advisor | dev-coder |

## 카운터 추적

저장소: [`tasks/audit_log.md`](../../tasks/audit_log.md)
- 각 감사별 **기준점 스냅샷** (commit hash, trade count, error count)
- 현재 값 + 차이 = 진행도
- 차이 ≥ 트리거 → 실행 + 기준점 리셋

## 자가학습 루프

```
이벤트 누적 → 트리거 → Harness 가 agent dispatch → 의심 발견 → dev-coder/ops-executor dispatch → 다음 누적
```

## Fallback 안전망

- 7일 미실행 시 강제 실행 (정체 감지)
- 에러 급증 시 카운터 무시 즉시 실행
- 감사 우선순위 (바쁜 때): [BUG] > [REQUEST] > [AUDIT]
- 감사 쌓이면 Jin 에스컬

## 참조
- [audit_log.md](../../tasks/audit_log.md) · [loop.md](../loop.md) · [audit_cell_ssot.md](audit_cell_ssot.md) (Cell SSOT 상세) · [cell-matrix-100pct-pivot.md](../plans/cell-matrix-100pct-pivot.md)
