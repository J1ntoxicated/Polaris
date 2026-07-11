---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, roadmap, experiments, shadow, promotion]
---

# Frontgate 실험 로드맵 — 우선순위 10 (섀도우→PROVE→승격)

공통 절차: ① 섀도우(거동 0, `gate_shadow_events` 병기) → ② PROVE virtual(가상계정
실주기 측정) → ③ 숫자 기준 충족 시 승격. 승격 형태 = 연속 스코어/컨텍스트/기존
scalar 값 입력만(차단 0, 신규 multiplier 0). 실행 주체: #7만 기존 gpt-5-mini 피드
소비(신규 콜 0), 나머지 전부 결정적 코드.

| # | 항목(게이트) | 섀도우(거동 0) | 승격 기준(숫자) | 데이터/의존성 |
|---|---|---|---|---|
| 1 | 로스터 활성화 (G2) | dispatch 발화 이벤트 로그 — 등록 후 실발화 카운트 | 전략별 발화 ≥20건 + 발화경로 적대검증 PASS | 있음 · INERT 함정 검증 의무 |
| 2 | TSMOM 12-1 (G2) | 문헌 고정 파라미터 신호 vs 기존 신호 delta 로그 | 섀도우 신호 ≥30건 · forward-return 부호 일치 ≥55% | 있음 · #1 dispatch 검증 선행 |
| 3 | XS+52wk 통합 랭크 (G1) | 후보랭크 vs 현행랭크 delta 컬럼 | 20 거래일 기록 + 상위 tier forward-return 스프레드 >0 (t≥1.5) | 수정주가 패널 · 결측=중립 z |
| 4 | 확률 캘리브레이션 (G5) | calibrated-vs-raw 병기 로그 | Platt: pair ≥500 · Brier 개선 ≥3% → isotonic: pair ≥1.5k + 전 bin ≥30 | 자가 축적(gate_event_log) |
| 5 | DFII10 골드 컨텍스트 (G2) | DFII10-골드 상관 시계열 + 붕괴 플래그 로그 | 60 거래일 rolling corr 안정 산출 + conviction delta 기록 | `_SERIES` 1줄 · release-time-aware |
| 6 | VWAP/AVWAP 타이밍 (G4) | 타이밍 개선폭(진입가 delta bps) 로그 | 섀도우 진입 ≥50건 · 평균 개선 >0 bps (지연≠거부 유지) | 1m bars · known-at-time 앵커만 |
| 7 | 뉴스 conviction (G2/G3) | sentiment vs forward-return 상관 로그 | 타임스탬프 감사+dedup 완료 후 IC >0.02 (ingestion 기준, n≥300) | 가동 피드 재사용 · GPT 신규 콜 0 |
| 8 | 섹터 로테이션 (G1) | 섹터 랭킹 delta + 유니버스 편입 후보 로그 | 리밸런스 3주기 기록 + 편입 후보 forward-return 스프레드 >0 | 섹터 ETF bars 있음 |
| 9 | TTM Squeeze (G2+G4) | squeeze-on/release 이벤트 로그 | release ≥40건 · release 후 방향 hit ≥52% | OHLC 있음 · deterministic |
| 10 | 메타라벨 모델 (G3/G5) | 예측확률 vs 실결과 병기 | 라벨 pooled ≥2k(비중첩) · 전략군 ≥500 · purged CV AUC ≥0.55 | 자가 축적 — 라벨 카운트 게이트 |

## 단계 노트
- #1→#2 순서 의존: TSMOM shadow-delta는 로스터 dispatch 검증 후에만 의미.
- #4는 #10의 전제 — 메타라벨 확률도 캘리브레이션 통과 후 T4 값 설정.
- #3·#8 동시 가동 시 방향 가중 중첩 금지 — #8은 라우팅 전용 유지.
- 미달 구간 = 전부 섀도우 지속. 캘린더 기한 없음 — 데이터 축적량 기준 발동.

## 리스트 밖
PEAD = 타임스탬프 검증된 이벤트 피드 확보 시 재상정(섀도우 이벤트 로그는 즉시 가능).
AQR = 오프라인 벤치마크 · HMM/RVOL = 저순위 섀도우 유지.

블루프린트 → [[integration-blueprint]] · 카탈로그 → [[scan-validator-models]] 외 4편
