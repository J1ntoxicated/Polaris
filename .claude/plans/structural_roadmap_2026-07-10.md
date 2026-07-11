---
status: active
date_created: 2026-07-10
supersedes: structural_roadmap_2026-06-22.md
---

# Structural Roadmap — 2026-07-10 (활발거래+배선 완결 이후)

## R1 — 검증 가속 (현행 중심축)
- 1.1 [DONE] 활발거래 언리시 — virtual 26전략 dispatch, 신호 13/일→수백/h, 체결 4/일→150+/일
- 1.2 [DONE] built-but-unwired 배선 6종 — strength_scalar·Kelly/tier·Capital 실사이즈·stale_book per-ticker·exit ruler SSOT·opposite-aware FLIP
- 1.3 [DONE] Capital qty 계약 회귀 적발·교정 (risk_usd 750× 오탬핑 4행 마이그레이션)
- 1.4 [BUILD] candidate factory — evolve/P0a 엔진 재개, 생존자→virtual PROVE 배선 + 1차 돌파 클로닝 캠페인 (wf_f4583e9e)
- 1.5 [BUILD] Kelly/tier 첫 증폭 관찰 — strategy_risk_state 표본 축적 중(11행), 승자 사이즈업 발화 대기
- 1.6 [DONE] connors_rsi2 1H 실험 실패 판정(-$588/일) → 1D 되돌림. 하향 실험은 per-전략 evidence로 판정
- 1.7 [DEBATE-DONE] 수수료 분리 판정 v0 배포(거동 0·섀도우 축적) → v1 flip은 섀도우 120 발산 기준
- 1.8 [BUILD] Capital 유령 노출 61× 수술 — sizing_zero 사유 스탬핑 포함 (wf_e77e4229)
- 1.9 [QUEUED] 레짐 v2 공장 — 6상태 섀도우 채널+분리력 채점(regime_factory 디베이트 R1 수렴, flip 전 R2 의무)
- 1.10 [BUILD] 틱 W2 — 단일-writer 마이그레이션(락 사망 소멸→초 단위 틱)

## R2 — 동면 정리 (안 헷갈리게)
- 2.1 [BUILD] 동면 전수조사+삼분류 — DB무덤·죽은모듈·env-inert·무소비 이벤트 → WAKE/DELETE/KEEP-DORMANT (wf_005f9051)
- 2.2 [DECISION] 판정 후 실행 웨이브 — WAKE 배선 + DELETE 정리 빌드
- 2.3 [DONE] 대시보드 정합 — NET 버추얼 단일자·chart/weekend 붕괴 픽스·모바일 라우팅·로드맵 소스 현행화(이 파일)
- 2.4 [DONE] 대시 화면 분리 — /flow 비주얼 월(글로브+파이프라인 강+디렉터 카메라) · / 풀와이드 워크보드 · AI탭 3섹션(bot/harness/cowork)

## R3 — 인프라 안정 (완결 유지)
- 3.1 [DONE] DB writer/reader 분리 3파 — WAL creep 근절·retention 폭풍 근절·recalc 락 50→1
- 3.2 [DONE] 모니터링 루프 — monitor_tick.sh 결정적 수집, 1h 케이던스, rail/락/픽스발효 감시
- 3.3 [BUILD] 잔여 관찰 — 락 산발 ~20/h(무해 degrade), 재발 클러스터 시 추가 이관

## R4 — 실-와이어 준비 (virtual 검증 완료 후)
- 4.1 [DEBATE] EARN 승격 세트 확정 — PtS FSM이 virtual 실사이즈 증거로 선별한 전략만
- 4.2 [DECISION] POLARIS_VIRTUAL_ACCOUNT=0 flip 조건 — 검증 기준·리콘 재연결·튜닝 (feedback_virtual_account_first_then_real_wire)
- 4.3 [BUILD] real-와이어 시 PROVE probe 경로 발효 확인 (5f3512e forward-fix, virtual에선 no-op)

## R6 — 대개편: 척후병+후방 소화계 (2026-07-11 착수, 역대 최대 구조 향상)
- 6.1 [BUILD] frontgate top10 behavior-0 섀도우 배선 — 검증→Sonnet 배선→Opus 리뷰 (wf_19d24629)
- 6.2 [DONE] 척후병 데이터 소싱 플랜 — EDGAR·DefiLlama 즉시, Finnhub(가입)·Coinglass(\$29 Jin 결정), vault frontgate-scan/data-sourcing-*
- 6.3 [DONE] 후방 마스터 블루프린트 — W1~W5 시퀀스+의존성 그래프, codex R1 수용 7 (vault backgate-plan/)
- 6.4 [QUEUED] /flow 익스포저 — 섀도우 채널+승격 진행률 게이지 (6.1 랜딩 후)
- 6.5 [QUEUED] 무키 피드 인제스트 빌드 — EDGAR·DefiLlama (altdata 패턴)
- 6.6 [QUEUED] W1 감시 그물 → W2 섀도우 확장 → W3 채점·승격 인프라 → W4 항목별 flip(각각 /debate R2) → W5 조건부 — master-sequence.md가 SSOT

## R5 — 이월 큐 (우선순위 낮음, 의도적 defer)
- 5.1 [DEBATE] counterfactual auto-reader — 계측→라이브결정 폐루프는 아키텍처 변경, stale_book이 실행가능분 흡수함
- 5.2 [DECISION] REFINE_TIMING consumer — 진입지연이라 활발 목표와 역행, ROI 낮음 판정
- 5.3 [BUILD] market_events reader — 거래직결 아님(텔레메트리), 동면 삼분류 결과 따름
- 5.4 [BUILD] R-budget flip 재검 — Kelly 활성화로 CAP_DOMINATED 해소된 뒤 재평가
