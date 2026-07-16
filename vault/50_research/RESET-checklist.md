---
type: plan
status: next-session
date_created: 2026-07-15
tags: [reset, master-checklist, index]
---

# 마스터 리셋 체크리스트 (토큰 리셋 후 착수 순서)

Jin 2026-07-15 "클린업 할 시기" — 마스터 리셋 = **아키텍처 재설계 + 코드 클린업 +
널리지 클린업 + 데이터 클린업** 통째로. 목요일 wipe = 클린 슬레이트 착수 신호.
설계는 다 서 있음(아래 청사진). **🚨 실행 금지 규칙 (Jin 2026-07-15): Jin이 "리셋 됐어"
할 때까지 전부 목록 추가만 — 코드 빌드 착수 금지.** 신호 오면 P0부터. 빌드 = Sonnet + Opus 3렌즈.

## P0 — 프리즈 근절 (최우선)
- [x] **storage-split Phase1** ✅2026-07-16 랜딩(락0 실증): bars/ticker_baseline_samples/watchlist_focus를 별도
      marketdata DB(자체 WAL락)로. 대시도 그 DB 읽기(스터터 소멸). 크로스도메인 JOIN 감사.
      → `storage-split-blueprint.md`. wipe라 마이그레이션 0.
- [x] **원복** ✅ 캡 1500 복원: 랜딩 후 알파카 캡 `POLARIS_UNIVERSE_RANK_TOP_N` 400→1500 (aggressive 복원).
      왓치독 틱-생존은 유지(keeper).

## P1 — 안전/정합 (작고 독립)
- [x] **엑싯 백스톱 + 마크 폴백** ✅: ①시간/하드 스톱 신설 → stopless 무한정박 근절. ②**held 포지션
      마크가 quote_ticks(mid)에만 의존 → 알파카 held는 mid=null이라 진입가에 7일 정지(DIA/CNC
      실측: last_seen_mid null, 바는 신선한데 마크 안 됨) → 엑싯 로직이 얼어붙은 가격 평가해 영영
      청산 안 됨. mid 없으면 신선한 바로 마크 폴백 의무.**
- [x] **전략↔티커 정렬** ✅(cci는 virtual_loosen 의도로 판정정정): cci_reversion(commodity 선언인데 FX/지수 물림)·weekend_*(세션게이트 0)·
      connors_rsi2(alpaca→capital 누수) 교정 + 미등록 발화(session_breakout 등) 레지스트리 재조정.

## P2 — 게이트 재편 (7 골격) + 델리게이션
- [x] **게이트 재편** ✅(G4폐지·GPT다이어트·새니티가드 — G6G7 물리통합은 보류→백로그): G4 폐지·G6+G7→Exit/Monitor 합침·G3+리스크→Admission·리스크 가시게이트 승격·
      G3/G4/G7 GPT 라이브경로 제거·G2 GPT 검증(유일 40% divergent)·G2=소스/G8=학습기 정명.
- [x] **델리게이션 게이트 신설** ✅(v1 섀도우 코어): 진입 전 전략↔티커 지능 배정. 결정론 fit-score fast-path +
      애매시 gpt-5-mini 타이브레이크(섀도우-후-승격). → `delegation-gate-blueprint.md`.

## P3 — Built-not-wired 감사 → 승격 (전수)
- [x] **감사** ✅: env OFF·applied=False·observe·미등록·소비자0 전 코드 sweep(loop-until-dry).
- [x] **프로브 승격** ✅(applied 248+/1.5h 실증): protect(TIGHTEN/HARVEST) 켜기(giveback 1R 실측 근거), WIDEN 제외, 검증 붙여.
- [x] **고아 피드→프로브 축** ✅(liquidity·funding v1 — COT/macro/event 후속): liquidity·event-proximity·funding·macro·COT·correlation 배선.
- [x] **미등록 전략 등록** ✅(31종·capital_macro shadow-first): capital_macro_riskoff_catalyst 등. maker/price_through 섀도우 승격경로.
- [x] **프로브 리텐션** ✅(기존 스펙 확인·wipe로 초기화) — 프로브 11.7GB → 섀도우 DB + 리텐션**. → `built-not-wired-audit.md`.

## P4 — 대시보드
- [x] **오픈 포지션 인스펙트** ✅: 줄 스파크라인 + 액티비티 절반축소 + 클릭선택 차트(기본=최근거래
      자동추종). 딥차트는 보드 것 활용. → `dashboard-todo.md`.
- [x] **최근 거래 아래 차트 섹션** ✅(데스크 /flow): 액티비티 아래 CHART 섹션 신설, 차트 볼 수 있게.
      (dashboard-todo 스펙 그대로 — 기본=최근거래 자동추종, 클릭=핀.)
- [x] **모바일 대시 전체 칼리브레이션** ✅: 지금 메인 /flow 개편을 안 따라가 "따로 놈"
      — 자산군색·게이트색·마퀴·액티비티 디스크립티브·포지션 그룹핑 전부 미반영. 모바일 UX 전면 재정렬 + 데이터 정합.
- [x] **모바일 글로브 리뷰 + 리와이어링** ✅: 설계 바뀐 뒤 데이터소스 재배선. **글로브 = 이제 모바일 온리라
      모바일에 맞게 전용 최적화**(데스크 고려 X). globe-core/flows/satellites.js.
- [x] **모바일에 인스펙트 차트 넣기** ✅: 데스크 차트(위)의 모바일 버전.
- [x] **보드 레퍼런스 탭 칼리브레이션** ✅(P4a): /api/buildlog·roadmap·lessons가
      읽는 소스를 리셋 후 현행으로 — 로드맵 SSOT=RESET-checklist+청사진 5종(plans 6 archive 반영),
      lessons 새 세트(12 keep), 작업보드(P0~P6) 진행 상태 노출, 스테일 glob 제거.

## 백로그 (P3 뒤, 검증 선행 필요)
- [ ] **아시아 세션 커버리지 확장**: J225/HK50 1H 돌파 검증(us100_breakout_1h 패턴 백테스트 OOS)
      + 도쿄/차이나 오픈 세션 엣지 리서치. AU200/CN50은 OOS 음성 명시 제외 상태(재검 불요).
      캐피탈 CFD 브레드스 확장(개별주식 3405종)과 같은 줄기. (Jin 2026-07-16 "아시아·호주 세션 무시?")

## P5 — 코드 + 하네스 클린업 (전수 조사)
- [x] .claude 루트 죽은 loose 스크립트 14개(wf_*.js·run_debate) 삭제 (2026-07-15, 참조 0).
- [x] **하네스 감사** ✅(게이트-에이전트 8 archive): `.claude/agents/` 14개 — **게이트-에이전트 8개(entry-sizer·position-monitor·
      pre-entry-watcher·signal-validator·universe-scanner·adaptive-exit·post-trade-reflector·risk-officer)
      = vestigial 의심**(파이프라인은 Python, per-gate AI 에이전트 구상 폐기됨). 런타임 name 호출
      확인 후 미사용시 제거. 유틸 에이전트(code-reviewer·codex-debate·vault-curator·forensicist·analyst) = 유지.
- [ ] **skills 8개** 실사용 확인(Skill tool 호출 로그), 미사용 제거.
- [ ] **`.claude/plans/` 29개** 대부분 슈퍼시드(polaris_v2_plan ×4·roadmap 06-22 등) → 현행(07-10)+결정가치만 남기고 정리(P6 연계).
- [ ] **codebase-memory 그래프** 디스크에 없음 → ADR-014 참조 stale? 재생성 or ADR 갱신.
- [ ] **죽은/안 쓴 코드 sweep**: graph-first LOCATE → 실파일 검증 → 제거. 미참조 함수·orphan·dead import. mypy/ruff/테스트 green.
- [ ] **판정표**: 모듈별 "쓰임/죽음/승격대기" 3분류.

## P6 — 널리지 클린업 (vault + memory)
- [x] **vault Karpathy 3-ops** ✅(lint error 0·lessons 73 삭제·digest 롤업): `tools/vault_lint.py --karpathy --report` → keep/compress/delete +
      backlink 정합 + lifecycle. 이번 세션 청사진 다수 → 중복·stale 정리.
- [x] **memory 통합** ✅(리셋 상태 현행화): `consolidate-memory` — 중복 병합·stale 픽스·index 정돈.
- [x] **lessons/weekend/plans 감사** ✅ — lessons 84개·weekend 11개·plans 29개: keep/compress/delete + 슈퍼시드 제거.
- [ ] 리셋 후 실제 반영된 것 vault/memory에 확정(설계→구현 상태 갱신).

## 잔여 (accepted debt / 후속)
- [x] 테스트 부채 14건 수리 ✅ (풀스위트 6022/0 fail, 프로덕션 버그 2 발굴). wikilink 114 정리 = 후속.
- [ ] repo-wide ruff 부채 166(research 스크립트 E702/E402) = 후속 정리 태스크.
- [ ] skills 실사용 확인·plans 1회성 15 내용대조 = 후속. .agents 미러 = 동기화됨(2026-07-16).

## 데이터 클린업
- [x] 아카이브 2GB 삭제 (2026-07-15). 백업/잡DB 8.4GB 삭제 완료.
- [ ] probes 11.7GB → 섀도우 DB + 리텐션 (P3). WAL 위생.
