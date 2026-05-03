# _DEPRECATED — 모태 20 agent (참조 read-only, invoke 금지)

> ADR-005 (Harness 4 modes) 적용으로 모태 20 agent → Polaris 4 agent 압축. 여기 있는 20 파일은 **참조용**이며 **`pre_agent.py` hook이 invoke 시 차단**.

## 흡수 매핑

### vault-curator 흡수
- `ops-cell-lifecycle.md` — cell 만료/upsert/persistence
- `harness-structure-advisor.md` — vault 구조 가이드
- `dev-trace-linker.md` — 백링크 관리

### code-implementer 흡수
- `dev-coder.md` — 코드 작성 base
- `dev-unit-contract-validator.md` — TDD 검증
- `dev-smoke-runner.md` — runtime verify
- `dev-entry-gate-specialist.md` — entry signal 검증
- `dev-wire-guardian.md` — 신규 wire 안전성 (getattr guard 등)
- `dev-refactor-advisor.md` — refactoring 가이드

### forensic-investigator 흡수
- `ops-trade-forensic.md` — 거래 forensic base
- `harness-drift-detector.md` — canonical drift 감지
- `dev-audit-advisor.md` — audit
- `dev-session-axis-auditor.md` — session 정합성
- `ops-log-quality-auditor.md` — log audit
- `ops-quarantine-reviewer.md` — quarantine

### 비흡수 (자율 forensic loop 폐기)
- `ops-executor.md` — 자율 실행 (ADR-005 폐기)
- `ops-exchange-registry.md` — 멀티 거래소 (Polaris SPOT-only)
- `ops-param-tuner.md` — 자율 파라미터 튜닝 (Jin/codex 의무)
- `ops-regime-watcher.md` — 자율 regime 감시 (FORENSIC 모드 통합)
- `ops-log-advisor.md` — log advisor (forensic-investigator 흡수)

## 왜 폐기

- 모태 20 agent + 1,431 alert dirs + 자율 forensic loop = M4 메타 작업 무한 증식
- Polaris는 4 agent + 4 모드 + 명시 트리거로 단순화 (ADR-005)
- agent 추가도 ADR 필수 (P2 lifecycle 적용)

## Invoke 차단

`.claude/hooks/pre_agent.py`가 `subagent_type` 검사:
- 여기 있는 20 agent 중 하나 호출 시 → exit 2 (block)
- Polaris 4 agent (`vault-curator`, `code-implementer`, `forensic-investigator`, `codex-debate-partner`) 만 허용

## 신규 agent 추가 시

ADR 필수 (예: `ADR-NNN-add-<name>-agent.md`). codex-debate 통과 + Jin ack 후 `.claude/agents/`로 이동.
