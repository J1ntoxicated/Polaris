# Polaris Bootstrap Design — 2026-05-03

> Brainstorming 스킬 spec (superpowers). 진단 + 방향 + 운영 모델 + 시작 시퀀스.
>
> 상세는 `vault/` 노트 SSOT (이 spec은 entry point + link 위주, 콘텐츠 중복 방지).

## 1. Context (왜 Polaris)

Jin이 모태 `auto_invasion_mk1-main` (442 .py + 1,785 vault md + 27GB SQLite, 6개월) 운영 중 **"수익이 안 나고 컨텍스트 폴루션이 심해서 개판났다"**며 새 프로젝트 시작.

핵심 진단 (Codex 디베이트 3 라운드 95→100% 합의):
```
L1 수익 안 남 ← L2 알파 미검증 ← L3 검증 체계 부재 ← L4 4 contract 미정의 ← L5 멀티-에이전트 토폴로지 조정비용
```

→ Polaris는 "더 좋은 봇"이 아니라 **"컨텍스트 폴루션이 구조적으로 불가능한 SPOT 거래 알파 검증·진화 시스템"**.

## 2. 의사결정 — 옵션 Y 확정

모태 invasion/spot/ 6,263 라인 인벤토리 결과 (자세히는 [[INSIGHT-001]]):
- perp 198 + alpaca 277 + stock 200 + 잔재 115 라인 = SPOT-first 아닌 누더기
- TODO/FIXME 0건 (정리 표시인데 잔재 가득 = 더 위험)

→ **옵션 Y**: 코드 처음부터 SPOT-first 새로 작성, 학습 노하우는 INSIGHT/lessons/JSON 19 소스로 보존. 자세히는 [[ADR-001]].

## 3. 운영 모델 (vault SSOT — 8 섹션)

자세히는 vault Constitution:
- [[north_star]] — Polaris 철학 (북극성 + SPOT-first 재정의)
- [[principles]] — 7 영속 원칙 (P1~P7)
- [[4_contracts]] — Authority / Lifecycle / Write Path / Validation Boundary
- [[governance]] — DRAFT / VERIFIED / AUTHORITATIVE 3단계
- [[emergency_bypass]] — 긴급 fix 조건 + 24h 사후 산출물
- [[operating_model]] — 4 모드 / 4 agent / 스킬 매트릭스 / 슈퍼브레인 / vault 사이클 / seq thinking 패턴 / 코드 리뷰
- [[code_review_workflow]] — codex 외부 리뷰 의무 사이클

핵심 ADR:
- [[ADR-001]] SPOT-first fresh start
- [[ADR-002]] Vault-first architecture (v4 7계층)
- [[ADR-003]] Codex debate protocol (max 3 라운드)
- [[ADR-004]] Code review codex external (Jin mandate)
- [[ADR-005]] Harness 4 modes

## 4. 시작 시퀀스 (Phase A~F)

### Phase A — Spec + Vault Bootstrap + 운영 모델 vault 정착 (오늘)
- A1 spec 문서 (이 파일)
- A2 메모리 4개 (Polaris 운영 정책)
- A3 vault 7계층 디렉토리
- A4 핵심 콘텐츠 (7 constitution + 5 ADR + 2 INSIGHT + 5 templates + tag_taxonomy + _NOW + INDEX + log + _README들)
- A5 인수 stub ([[_INHERIT_QUEUE]])
- A6 .gitignore 업데이트

### Phase B — Hooks & Lint v4 (오늘)
- B1 vault_lint v4 적응 (machine-state-leak / expires / reviewed-by / pure 검사 추가)
- B2 4 hook 신설 (pre_commit / post_edit / post_stop / pre_agent)
- B3 settings.json polaris config

### Phase C — Agent Consolidation 20→4 + 코드 리뷰 워크플로 (오늘)
- C1~C4: vault-curator / code-implementer / forensic-investigator / codex-debate-partner
- C3 코드 리뷰 workflow (codex-debate-partner 책임)
- C5 commands 8 유지 / 4 폐기

### Phase D — Implementation Plan via writing-plans (오늘)
- superpowers:writing-plans 스킬 호출
- Phase 0~4 구체 단계 (Constitution 검증 → 인수 추출 → 첫 알파+컴포넌트 → 점진 확장)

### Phase E — 첫 액션 (Phase D plan 승인 후)
- HYPOTHESIS-001 (24h 내 결과) OR 첫 컴포넌트 (1주)

### Phase F — Visualizer + Dashboard (코어 코드 완성 후 별도 plan)
- 모태 cloud visualizer / 3-window TUI / 웹 대시보드 / Grafana 중 결정

## 5. Brainstorming Spec Self-Review

- ✅ Placeholder 없음 (모든 섹션 콘텐츠 완비, 상세는 vault 링크)
- ✅ Internal consistency: 운영 모델 8 섹션 vs 7 영속 원칙 vs 4 contract vs 4 모드 vs 4 agent 모순 없음
- ✅ Scope: bootstrap 단계만. 코드 작성/알파 검증은 Phase D plan에서 분리
- ✅ Ambiguity: 8 인수 소스 stub은 _INHERIT_QUEUE에 명시, Phase D에서 추출 단계 정의

## 6. Risks (자세히는 vault)

- 6개월 후 vault 비대화 → P5 derived metric으로 차단 (vault 품질 = MTTR-alpha 단축 효과)
- 4 agent 병목 → agent 추가도 ADR (P2 lifecycle 적용)
- vault hook이 긴급 fix 차단 → emergency_bypass 명문화 (G1)
- Codex API cost → max 3 라운드 디베이트 + 자명 변경 리뷰 생략

## 7. 다음 액션

이 spec 승인 후 Phase B, C, D 순차 진행. Phase E/F는 후속 plan에서.

---

## Linked Vault Notes (Critical)

### Constitution
- [[north_star]] [[principles]] [[4_contracts]] [[governance]] [[emergency_bypass]] [[operating_model]] [[code_review_workflow]]

### Decisions
- [[ADR-001]] [[ADR-002]] [[ADR-003]] [[ADR-004]] [[ADR-005]]

### Knowledge
- [[INSIGHT-001]] [[INSIGHT-002]] [[_INHERIT_QUEUE]]

### Index
- [[_NOW]] [[INDEX]] [[log]] [[.tag_taxonomy]]

### Templates
- [[.templates/INSIGHT]] [[.templates/ADR]] [[.templates/HYPOTHESIS]] [[.templates/COMPONENT]] [[.templates/LESSON]]
