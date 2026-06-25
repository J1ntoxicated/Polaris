---
type: research
status: built-reviewed
date: 2026-06-25
tags: [logging, observability, ops, datastream, retention]
---

# Log architecture audit + operational↔data 3-way split (2026-06-25)

DEMO/PAPER. Behaviour-change 0 (logging-only; flow_not_block 자명 보존). builder≠reviewer.

## Problem (실측 live `data/paper/polaris_runtime.log`)
612,944줄 / 128 MB / **회전 0**(무한증식, ENOSPC 위기). INFO 472k(77%, 전부 per-tick 데이터)
가 진짜 운영신호 **ERROR 9개**를 파묻음. 상위 소스 = tick-gate/regime-active 108k ·
seam2-confirm 108k · tick-gate/signal 91k · cell update 84k · learner update 46k ·
recency WARNING 34k · asset_class WARNING 14k.

## Design — 3-way 분류
- **OPERATIONAL → runtime.log (lean)**: ERROR·ops WARNING·fills·gate open/close·
  lifecycle·telemetry(~30s)·STALL·tick FIRE verdict·learner hourly-commit/rollback.
- **DATA → `data/paper/data_stream.jsonl` (신규 sink)**: per-tick/update 데이터 —
  regime-active·seam2-confirm·NO_FIRE·cell update·learner update·recency 상세.
- **NOISE 억제**: regime-active/seam2-confirm = **log-on-CHANGE**(매틱 X, 상태변화만);
  NO_FIRE = verdict-change dedup; recency 34k = fresh→stale **transition당 1 WARN**;
  asset_class 14k = distinct instrument당 1 WARN(DB row는 이미 idempotent).

## Mechanism
- 신규 `polaris/datastream.py`: 전용 `polaris.datastream` 로거(`propagate=False`→runtime
  root 무유출) + `RotatingFileHandler`(JSONL, 128MB×6=768MB cap) + `emit(event,**f)`.
  미설정 시 no-op(tests/replay 안전).
- `logging_config.py`: runtime FileHandler→**RotatingFileHandler**(64MB×6=384MB cap).
  기존 시그니처/6 테스트 보존(RotatingFileHandler는 FileHandler 서브클래스).
- log-on-change/dedup 캐시 = `TickEngineState` 필드(tick) + 모듈 set(bars/asset_class,
  conftest autouse가 per-test clear). **결정/intent/DB write/return 전부 불변.**
- `ignite_p1`이 `setup_datastream()` 배선.

## 로그 소비자 무손상 (검증)
log_scan(STALL·db-lock·ws markers)·live_monitor(Traceback·CRITICAL·rejected, tail 300)·
visualizer `_tail_botlog`(320) — 전부 OPERATIONAL만 소비. 라우팅한 데이터/dedup 라인은
어느 마커 패턴도 안 가짐 → runtime.log에서 빠져도 소비자 영향 0. 마커 라인
(`_production_tick_engine.py`)은 미접촉, runtime.log 유지.

## 분류 인벤토리 (호출처:라인 → 판정)
- `_production_tick_decision:186` regime-active 108k → DATA+log-on-change
- `_production_tick_decision:155` seam2-confirm 108k → DATA+log-on-change
- `_production_tick_decision:205` signal NO_FIRE 91k → DATA+verdict-dedup
- `_production_tick_decision:211` signal FIRE 희소 → OPERATIONAL 유지
- `cell_matrix/update:60` 84k → DATA · `learners/base:136` 46k → DATA
- `_production_bars:563` recency 34k → NOISE: transition WARN + DATA
- `_production_asset_class:65` fallback 14k → NOISE: distinct WARN + DATA

## Retention/rotation: runtime 384MB cap · datastream 768MB cap — 무한증식 차단.

## Verify: 신규 24 테스트 + 172 회귀 green · mypy --strict 9파일·ruff clean · 거부키워드 0
· fresh-Claude 적대리뷰 → (verdict). deferred: data_stream 대시보드 탭 · cell/learner
84k/46k 동시발생(replay/reconcile?) = 거동분석 별건(로깅 범위 밖).
