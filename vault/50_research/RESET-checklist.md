---
type: plan
status: next-session
date_created: 2026-07-15
tags: [reset, master-checklist, index]
---

# 리셋 마스터 체크리스트 (토큰 리셋 후 착수 순서)

목요일 wipe 리셋 = 클린 슬레이트 재설계 착수 신호. 설계는 다 서 있음(아래 청사진 참조).
"이어서" 한 마디면 P0부터 순서대로. 빌드 = Sonnet 빌더 + Opus 3렌즈.

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

## 정리 대기 (Jin 판단)
- [ ] 아카이브 2GB(polaris_live_archive 6/26·27) 삭제 여부. probes 11.7GB는 P3에서 처리.
