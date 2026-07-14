---
type: design
status: ready-to-build
date_created: 2026-07-14
tags: [storage, incident-fix, wal-contention]
---

# Storage Split Blueprint — market-data ↔ trading-state

**근본 원인** (2026-07-13 10h 프리즈): 단일 `polaris_live.sqlite` = 단일 WAL 쓰기락.
소방호스(bars 12.4M + ticker_baseline_samples 9.1M + watchlist_focus 149K 행)의
persist가 락을 물면 지연 못 견디는 거래쓰기(positions/fills/regime)가 `database
is locked`로 막혀 틱 정지. 대시보드도 같은 DB라 스냅샷 쿼리 스터터. → **관심사 분리.**

## 목표 아키텍처: 2 파일, 2 WAL 락
- `data/polaris_trading.sqlite` — 거래상태: positions, fills, order_intents,
  pending_opens, regime_state, strategy_class, score_f_events, signals,
  gate_events, ladder_*, learner_*, cell_matrix_*, risk_events, strategy_*,
  position_*, allocator_reservations, weekly_equity_curve. (수천 행, latency-critical)
- `data/polaris_marketdata.sqlite` — 시장관측 소방호스: **bars,
  ticker_baseline_samples, watchlist_focus**(핵심 3), + ticker_ground,
  ticker_technicals, ticker_baseline_state, quote_ticks, tick_inflow,
  altdata_snapshot, 전 `*_shadow` 테이블, altdata 피드(edgar/earnings/stablecoin).
- 원칙: **거래결정과 원자적 일관성이 불필요한 write-heavy 관측 = marketdata.**

## 배선 (이미 conn=인자라 침습 적음)
1. `schema.py`: `init_db`/`connect`를 trading·marketdata 각각 호출(DDL을 두 스키마로 분할).
2. `production_paper_loop.py:785+`: `trading_conn=init_db(trading_db)` +
   `md_conn=connect(marketdata_db)`. persist_bars/read_recent_bars/persist_focus/
   ground/baseline 호출부에 **md_conn 전달**(signal-gen·regime의 same-tick read-back도
   md_conn 내부로 격리 — 거래락 무접촉).
3. `db_writer` 2개 인스턴스: telemetry/ground/shadow → marketdata writer(fire-and-
   forget), 거래 sync 쓰기 → trading_conn 직접(큐 아님, 불변).
4. 대시보드(`polaris_graph.py`/snapshot): bars/signals-view/universe = marketdata
   ro conn, positions/fills = trading ro conn → 풀스캔이 거래 writer와 경합 0.

## Wipe = 마이그레이션 없음 (Jin 2026-07-14 목요일 리셋)
클린 슬레이트라 4GB 바 이전 불필요. 리셋 시 두 파일 fresh init, 신규 쓰기가 처음부터
올바른 DB로. 알파카 캡 400 → **1500 원복**(근본픽스가 넓이 받침, aggressive 복원).

## 핵심 리스크 = 크로스도메인 JOIN
빌더 의무 감사: trading·marketdata 테이블을 한 쿼리에서 JOIN하는 곳 전수
(score_f fills-recompute·sentry gate_events↔signals·dashboard snapshot 등).
해소: 읽기전용 `ATTACH`(핫패스 아닌 곳) 또는 파이썬 조인. 쓰기 경로엔 크로스 JOIN 금지.

## 검증 (before/after 실증)
락/h(현 149→목표 ~0)·틱 케이던스(40s 유지 under 1500 alpaca)·대시 렌더 스터터 소멸·
크로스도메인 감사 0 누락·풀 pytest green. Sonnet 빌더 → Opus 3렌즈(정합/크로스JOIN/회귀).

## 빌드 순서
Phase1(필수): 바 3종 분리 + conn 배선 + 대시 read 분리. Phase2: shadow/altdata 이관.
Phase1만으로 write량 ~99% 거래락에서 제거 = 프리즈 근절.
