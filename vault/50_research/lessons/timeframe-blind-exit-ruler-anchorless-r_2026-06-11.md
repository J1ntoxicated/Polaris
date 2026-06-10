---
type: lesson
status: active
date_created: 2026-06-11
tags: [exit-engine, atr, timeframe, excursion, telemetry, recalc, vault-isolation]
related: [[MOC-A1-design-dev]], [[layer-6-live-recalc]], [[capital-pnl-cross-instrument-match_2026-06-04]]
---

# 엑싯 자(ruler)의 2중 결함: 타임프레임-블라인드 + 앵커 부재 R 분모

DEMO/PAPER. 버그 교정 — 전략별 타임프레임 정밀 엑싯이라는 설계 의도 복원.
trail_mult(2.0/1.0)·FSM 임계(0.5/1.0/2.0R)·진입·사이징(T4) 무접촉.

## 증상 → 원인
- 1H tsmom 평균 11.7분 청산(승률 18-19%): 모든 전략의 trail 폭·R 분모가
  "최근 20×1m bar"로 계산됨(`load_active_position_rows`의 단일 1m 윈도).
  `StrategyMetadata.timeframe`은 존재했으나 엑싯 경로가 읽지 않았다.
- mfe/mae 4~8배 과장(-92.58R=실제 -11.45%), -463,734R 폭주: atr_pct가 매 틱
  재계산 → 변동성 수축 시 분모 축소. `_ATR_USD_FLOOR=1e-6` 절대 플로어가
  고가 인스트루먼트에서 분모 붕괴를 허용.

## 수리 구조 (한 포지션에 한 자)
- `_production_atr.py` 신규: `strategy_timeframe`(미등록 tick 시그널→"1m"
  = 현행 동일) + `timeframe_atr_pct`(tf→1m fallback, 퇴화 가드 mean>1e-5,
  TTL 캐시=TIMEFRAME_FETCH_CADENCE_SEC). **MIN_TF_BARS=5 — LIMIT 20 쿼리에
  "<21" 임계를 쓰면 1D 트랙 전체가 영원히 1m로 무음 고착(리뷰 블로커)**.
- 분모 = entry 앵커: open 시 `positions.entry_atr_pct/entry_atr_timeframe`
  스탬프(퇴화/무바 → NULL, 가짜 0.005 영속 금지). `evaluate_exit`는
  `entry_atr_pct`로 mfe/mae/FSM 분모 고정; close 경로(`real_pnl_r_from_fills`/
  `_close_excursion_r`)도 앵커 우선 — pnl_r과 mfe/mae가 한 분모 공유.
- trail 폭 = 타임프레임 정합 '현재' ATR(앵커 아님): Chandelier 계열의 의도된
  적응 + ratchet 불변식이 loosen 차단. 측정(R)과 거리(trail)는 다른 물건.
- 플로어: 절대 1e-6 → 상대 `entry×1e-4`(close 경로 선례 미러). mfe/mae
  ±100 캡(텔레메트리 전용 — FSM 최대 2.0R, 거동 0).
- legacy 행(NULL 앵커) → 현재 tf-ATR 분모 fallback + `anchor_missing` 플래그.

## 과거 보정
`recalc_excursions.py` (dry-run 기본, `--db` 필수, ro URI): 라이브 DB 셀프테스트
corrected=403 / skipped_no_extremes=13 / no_fill·no_bars=0. -463,734R→-0.035R,
MOBX -92.58R→-0.336R(=-11.4% 이동, 설계 검증값 일치). `--apply`는 앵커 동시
스탬프로 멱등. 일부 tick 행은 극값 자체가 cross-instrument 오염(기존 lesson)
이라 캡(-100)으로만 바운드 — 분모 교정과 별개 채널.

## 테스트 vault 오염 (반복 3회) 동시 차단
ignite_p1의 vault 쓰기 2곳이 CWD 상대경로 → `POLARIS_VAULT_DIR` 경유로 변경
(미설정=byte-identical). conftest autouse 픽스처가 **무조건** per-test tmp
vault로 setenv(ambient 값이 격리를 무음 해제 못 함). 메타 테스트가 실제
vault 해시 불변 검증.

## 교훈
1. 자(측정 단위)가 흔들리면 그 위의 모든 학습(reflector/learner)이 오염된다 —
   분모는 entry에 앵커, 거리는 현재에 적응. 둘을 한 변수로 쓰지 말 것.
2. fallback 임계는 쿼리 LIMIT와 교차 검증하라("<21" vs LIMIT 20 = 항상 참).
3. 절대 플로어는 가격 스케일을 모른다 — R-unit 가드는 항상 상대(×entry).
4. 신규 포지션(앵커 행)과 legacy 행은 `entry_atr_timeframe` NULL 여부로 구분
   — learner가 혼재 학습할 때의 식별자.
