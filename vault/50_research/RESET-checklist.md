---
type: plan
status: next-session
date_created: 2026-07-15
tags: [reset, master-checklist, index]
---

# 마스터 리셋 체크리스트 (토큰 리셋 후 착수 순서)

Jin 2026-07-15 "클린업 할 시기" — 마스터 리셋 = **아키텍처 재설계 + 코드 클린업 +
널리지 클린업 + 데이터 클린업** 통째로. 목요일 wipe = 클린 슬레이트 착수 신호.
설계는 다 서 있음(아래 청사진). "이어서" 한 마디면 P0부터. 빌드 = Sonnet + Opus 3렌즈.

## P0 — 프리즈 근절 (최우선)
- [ ] **storage-split Phase1**: bars/ticker_baseline_samples/watchlist_focus를 별도
      marketdata DB(자체 WAL락)로. 대시도 그 DB 읽기(스터터 소멸). 크로스도메인 JOIN 감사.
      → `storage-split-blueprint.md`. wipe라 마이그레이션 0.
- [ ] **원복**: 랜딩 후 알파카 캡 `POLARIS_UNIVERSE_RANK_TOP_N` 400→1500 (aggressive 복원).
      왓치독 틱-생존은 유지(keeper).

## P1 — 안전/정합 (작고 독립)
- [ ] **엑싯 백스톱**: 시간/하드 스톱 신설 → stopless 무한정박(DIA/CNC 6일 클래스) 근절.
- [ ] **전략↔티커 정렬**: cci_reversion(commodity 선언인데 FX/지수 물림)·weekend_*(세션게이트 0)·
      connors_rsi2(alpaca→capital 누수) 교정 + 미등록 발화(session_breakout 등) 레지스트리 재조정.

## P2 — 게이트 재편 (7 골격) + 델리게이션
- [ ] **게이트 재편**: G4 폐지·G6+G7→Exit/Monitor 합침·G3+리스크→Admission·리스크 가시게이트 승격·
      G3/G4/G7 GPT 라이브경로 제거·G2 GPT 검증(유일 40% divergent)·G2=소스/G8=학습기 정명.
- [ ] **델리게이션 게이트 신설**: 진입 전 전략↔티커 지능 배정. 결정론 fit-score fast-path +
      애매시 gpt-5-mini 타이브레이크(섀도우-후-승격). → `delegation-gate-blueprint.md`.

## P3 — Built-not-wired 감사 → 승격 (전수)
- [ ] **감사**: env OFF·applied=False·observe·미등록·소비자0 전 코드 sweep(loop-until-dry).
- [ ] **프로브 승격**: protect(TIGHTEN/HARVEST) 켜기(giveback 1R 실측 근거), WIDEN 제외, 검증 붙여.
- [ ] **고아 피드→프로브 축**: liquidity·event-proximity·funding·macro·COT·correlation 배선.
- [ ] **미등록 전략 등록**: capital_macro_riskoff_catalyst 등. maker/price_through 섀도우 승격경로.
- [ ] **프로브 11.7GB → 섀도우 DB + 리텐션**. → `built-not-wired-audit.md`.

## P4 — 대시보드
- [ ] **오픈 포지션 인스펙트**: 줄 스파크라인 + 액티비티 절반축소 + 클릭선택 차트(기본=최근거래
      자동추종). 딥차트는 보드 것 활용. → `dashboard-todo.md`.

## P5 — 코드 + 하네스 클린업 (전수 조사)
- [x] .claude 루트 죽은 loose 스크립트 14개(wf_*.js·run_debate) 삭제 (2026-07-15, 참조 0).
- [ ] **하네스 감사**: `.claude/agents/` 14개 — **게이트-에이전트 8개(entry-sizer·position-monitor·
      pre-entry-watcher·signal-validator·universe-scanner·adaptive-exit·post-trade-reflector·risk-officer)
      = vestigial 의심**(파이프라인은 Python, per-gate AI 에이전트 구상 폐기됨). 런타임 name 호출
      확인 후 미사용시 제거. 유틸 에이전트(code-reviewer·codex-debate·vault-curator·forensicist·analyst) = 유지.
- [ ] **skills 8개** 실사용 확인(Skill tool 호출 로그), 미사용 제거.
- [ ] **`.claude/plans/` 29개** 대부분 슈퍼시드(polaris_v2_plan ×4·roadmap 06-22 등) → 현행(07-10)+결정가치만 남기고 정리(P6 연계).
- [ ] **codebase-memory 그래프** 디스크에 없음 → ADR-014 참조 stale? 재생성 or ADR 갱신.
- [ ] **죽은/안 쓴 코드 sweep**: graph-first LOCATE → 실파일 검증 → 제거. 미참조 함수·orphan·dead import. mypy/ruff/테스트 green.
- [ ] **판정표**: 모듈별 "쓰임/죽음/승격대기" 3분류.

## P6 — 널리지 클린업 (vault + memory)
- [ ] **vault Karpathy 3-ops**: `tools/vault_lint.py --karpathy --report` → keep/compress/delete +
      backlink 정합 + lifecycle. 이번 세션 청사진 다수 → 중복·stale 정리.
- [ ] **memory 통합**: `consolidate-memory` — 중복 병합·stale 픽스·index 정돈.
- [ ] **lessons 84개·weekend 11개·plans 29개** 감사: keep/compress/delete + 슈퍼시드 제거.
- [ ] 리셋 후 실제 반영된 것 vault/memory에 확정(설계→구현 상태 갱신).

## 데이터 클린업
- [x] 아카이브 2GB 삭제 (2026-07-15). 백업/잡DB 8.4GB 삭제 완료.
- [ ] probes 11.7GB → 섀도우 DB + 리텐션 (P3). WAL 위생.
