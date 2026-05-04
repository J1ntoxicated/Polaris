---
pure: true
code_path: src/risk/performance_tracker.py
test_path: tests/risk/test_performance_tracker.py
created: 2026-05-04
phase: 2j
status: active
tags: [performance, winrate, kelly, pure, phase2j]
entity_type: component
entity_id: performance_tracker
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[dynamic_sizing]]", "[[INSIGHT-032]]"]
---

# performance_tracker

최근 N 거래 성과 통계 계산 (Phase 2j).

## 책임

closed Position 목록에서 win_rate / avg_win_pct / avg_loss_pct 추출. dynamic_sizing의 Kelly 계산에 공급.

## 핵심 함수

`compute_recent_stats(closed_positions: list[Position], lookback: int = 20) -> dict`

### 반환 dict

| 키 | 타입 | 의미 |
|----|------|------|
| `win_rate` | float 0-1 | 최근 N거래 승률 |
| `avg_win_pct` | float | 평균 gross % (win trades) |
| `avg_loss_pct` | float | 평균 gross |%| (loss trades) |

### Win 정의

`net_pct = gross_pct - fee_round_trip > 0` (fee 차감 후 양수만 win).

### Cold start

거래 수 < 5 → 보수적 defaults: `{win_rate: 0.5, avg_win_pct: 0.6, avg_loss_pct: 0.5}`

## P6 분류

Pure core — Position list 읽기만 (state mutation 없음).

## 관련

- [[dynamic_sizing]] — win_rate / avg_pct 소비
- [[state]] — Position.gross_pct, Position.fee_round_trip
