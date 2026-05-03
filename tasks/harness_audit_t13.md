# T13 Phase 3 — Harness 감사 (harness_audit_t13.md)

> **범위**: `.claude/agents/` (15개) + `.claude/commands/` (8개) + `.claude/settings.local.json` (hooks) + `.claude/docs/` (21개) 정합 감사.
> **목적**: E7 trace_id / E15 3대 원칙 auto-enforce / Plan v2.1 Per-Change Gate 4축 강제 의 구조 점검.
> **원칙**: 단정 X, 관찰 기반, spec 제안 (구현은 별도 iteration).

---

## 1. 현재 자산 목록

### Agent (15)
- Advisor: harness-drift-detector / harness-structure-advisor / dev-audit-advisor / dev-refactor-advisor / dev-wire-guardian / dev-smoke-runner / ops-log-advisor / ops-log-quality-auditor / ops-param-tuner / ops-regime-watcher / ops-trade-forensic / ops-exchange-registry
- Executor: dev-coder / ops-executor
- Specialist: dev-entry-gate-specialist

### Command / Skill (8)
- alert-triage / backtest / backtest_modes / debate / debate_apply / harness-mode / research / research_output

### Hook (settings.local.json 현재 1개)
- `PostToolUse` (Edit|Write): `invasion/*.py` 변경 시 `import invasion.main` async 검증.

### Docs (21) — 핵심
- audit_framework / advisor_dispatch_matrix / alert_lifecycle / alert_routing / alert_squad / alert_verification / canonical_files / coding_conventions / harness_periodic_maintenance / hooks / model_strategy / north_star / review_separation / logging / ops_audits / decisions / dashboard_redesign_mockup / ai_references / code_size_limits / dev_tasks / exchange_registry

---

## 2. 감사 발견

### 2.1 canonical_files.md drift (교정 완료)
- **발견**: canonical 에 `trade/pipeline.py` 단일 기재, 실제는 `_pipeline_scan.py` / `_pipeline_sizing.py` / `_pipeline_regime.py` 등 6 파일로 분할. Phase 0 agent 도 실제 파일명을 참조.
- **교정**: 본 커밋에서 canonical_files.md 업데이트 (Trade Pipeline scan/sizing/regime + close_handler + exit_fsm + cell_matrix 행 추가).
- **향후**: Phase 0.5 audit 기반으로 canonical 의 다른 entry 도 grep 검증 필요 (Phase 3 sub-task).

### 2.2 E7 trace_id — DB FK 활용 가능성 (Phase 2 재정의 반영)
- **발견**: Phase 0 agent 는 "bus.py:87-100 trace_id 부재 → forensic 전면 불가" 로 봤음. Phase 2 DB 실측 결과 `signals.trade_id` FK schema 존재 + `trade_events.trade_id` FK 존재. **구조적 부재 X, write 값 누락** (linkage 0.88%).
- **해석**: Plan H.1 의 "trace_id 도입" 은 DB level FK 를 이미 활용하는 쪽으로 축소 가능. 대신 **signals.trade_id write 경로 fix** 가 더 실용적 선결.
- **향후 spec**: 
  - `signals/composer.py` 의 acted_on=1 업데이트 site 에서 trade_id 함께 commit (같은 UPDATE)
  - 혹은 trade entry (`_pipeline_scan.py:1013-1042`) 이후 `UPDATE signals SET trade_id=? WHERE id=?` post-hook
  - 교차-프로세스 단계로 넘어가면 (Pillar 3 Tier 분리) bus payload trace_id 재검토

### 2.3 E15 3대 원칙 auto-enforce — code-level hook 부재
- **발견**: 현재 Hook 은 `import invasion.main` 체크 1개. 3대 원칙 (`no_single_review_verdict` / `no_quick_patch_ever` / `flow_not_block`) 은 문서 원칙만 존재.
- **향후 spec** (hooks.md 의 "향후 설치 후보" 확장):
  - **PreToolUse (Edit|Write) — Plan/doc 내 magic number 검출 hook**: `no_hardcode_in_plans` 자동 enforce. tasks/*.md 에 숫자/threshold 리터럴 + preg 언급 없음 → reject.
  - **PostToolUse (Edit|Write) — invasion/*.py block-filter 패턴 검출**: `if ... : skip` / `return None` / `continue` 통계 전/후 diff 증가 > 0 → warn.
  - **SessionStart — CLAUDE.md + MEMORY.md + T13_START_HERE load 확인 (auto-inject)**.
  - **PreCommit (git)**: MD 파일 60 줄 상한 체크 (`feedback_md_max_60_lines_split`).
  - **Stop / SubagentStop — Per-Change Gate 4축 통과 체크** (git diff 보고 auto 검토).

### 2.4 Agent gap 5개 (Plan D5) — 구체화
Plan v2.1 D5 에서 "Agent gap 5개 spec 작성" 을 언급했으나 구체 정의 없음. Phase 0/2 근거로 도출:

1. **dev-unit-contract-validator** (신규) — Pillar 1 Taxonomy 기반 unit 정합 감사. hourly_stats.py:655 같은 잔존 bug 재발 방지. preg 신규/수정 시 dispatch.
2. **dev-trace-linker** (신규) — signals.trade_id write 누락 감시 + 생성 경로 감사. E5/E7 forensic 대응.
3. **ops-cell-lifecycle** (신규) — Pillar 2 cell lifecycle (seed → active → promote → dormant → retire) 상태 전이 감사. cell_matrix 에 쓰기 발생 시 dispatch.
4. **ops-quarantine-reviewer** (신규) — quarantined_structural_defect / quarantined_noise 주간 검토 + 해제 조건. 결함 5 대응.
5. **dev-session-axis-auditor** (신규 or ops-trade-forensic 확장) — E16 Session × Exchange 이상치 감시. cell_matrix 의 session 축 일관성.

### 2.5 audit_framework.md 와 실제 주기 감사 drift
- **발견**: `audit_framework.md` 존재 — 주기 감사 카탈로그. Phase 2 에서 발견한 신규 축 (quarantined / session × exchange / linkage ratio) 이 카탈로그에 포함되었는지 확인 필요.
- **향후**: audit_framework.md read + 업데이트 spec (별도 iteration, 본 Phase 범위 밖).

---

## 3. Per-Change Validation Gate 4축 — 자동화 설계

Plan v2.1 E.1 Per-Change Gate 4축 (A 북극성 / B 타당성 / C Feedback / D 구조결함) 을 hook / skill 로 자동화 spec:

### A 북극성 (aggressive / amplify / flow / asymm / data-driven / no-block)
- **Hook**: PreCommit. git diff 로 `if cond: skip` / `dampen_mult < 1.0` / `filter -= X` 패턴 검출.
- **Skill**: 자가 체크 리스트 인라인 출력 (A 6 축 + 1줄 근거).

### B 타당성 (목적 / 효과 / 부작용 / rollback)
- **Skill**: commit message 포맷 lint — 4 요소 부재 시 reject 또는 warn.
- **Hook**: PostCommit git log --oneline tail → 4 요소 regex 검증.

### C Feedback 위반 (7종)
- **Skill**: `feedback_*` memory 파일명 규칙 enforce (ghost-pattern 스캔).
- **Hook**: Edit/Write 시 파일 내 magic number ≥ 3 자리 숫자 count + `# preg:` / `ParamRegistry` 언급 없으면 warn.

### D 구조 결함 (24h 후 의미 / preg 튠 / contract break / 원복 / 근본 원인)
- **Skill**: 변경 내용 요약 → 5 질문 인라인 자가 답변.
- **Hook**: PostToolUse log 기록 (`data/per_change_gate.jsonl`) — Jin 사후 감사 가능.

### MVP 범위 (Phase 3 실제 설치 대상 1-2개)
1. **PreCommit MD 60 줄 상한** — 즉시 설치 가능, 저비용.
2. **PostToolUse magic number 검출** — invasion/*.py 대상 warn (no reject).
3. 나머지 (A/B/D 자동) 는 iteration 분할.

---

## 4. Phase 3 산출 + 실제 수정

| 항목 | 상태 |
|---|---|
| canonical_files.md drift 교정 | ✅ 본 Phase commit |
| harness_audit_t13.md 작성 | ✅ 본 Phase commit |
| MVP hook 2개 실제 설치 | ⏳ 본 Phase 에서 할지 말지 — scope 고려, 보수적으로 spec 만 하고 실제 설치는 Jin 승인 대기 |
| Agent gap 5 spec → 파일 생성 | ⏳ 본 Phase 에서는 이름/목적만 정의. 실제 md 는 T13 이후 |

---

## 5. 다음 (Phase 4) 입력

- 본 파일 + audit 3종 → handoff memory 최종 update
- Plan v2.1 → v2.2 update 권장 (trace_id 재정의 / trade_events 재정의 / gap 5 agent 명칭)
- T13 debate 항목 합계 **19항** (원안 10 + Phase 0 신규 6 + Phase 2 신규 3)
- Jin 브리핑 요점: Phase 0/0.5/1/2/3 5 phase 완료. debate 19항. MVP 설치 대기.
