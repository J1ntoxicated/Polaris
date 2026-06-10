---
type: lesson
status: active
date_created: 2026-06-04
tags: [close, session-gate, zombie-drain, capital, fx_indices_cal, review]
related: [[MOC-A1-design-dev]], [[capital-pnl-cross-instrument-match_2026-06-04]]
---

# 좀비-청산 drain: off-session 보호가 Capital 좀비에 실제로는 없음

REJECT (적대 리뷰). `_production_close.py` zombie-drain fix.

## 핵심 결함
fix는 `stream_session_gate_active(resolve_stream(venue).session_calendar)` 로 in-session
판정 → `in_session = not stream_session_gate_active(...)`. 그러나 이 함수는 **us_equity_cal
(Alpaca track C) 만** True 를 돌려준다. 좀비 5종(J225/US30/OIL_BRENT/LIVECATTLE/GASOIL)은
전부 **Capital `fx_indices_cal` (track B)** → 항상 False → `in_session = True` **24/7
(주말·장마감 포함)**. 코드 주석과 `_production_state.py` docstring 이 약속한
"off-session reject 는 tally 에 안 쌓인다"는 보호가 **타깃 심볼엔 존재하지 않음**.
주말 내내 MARKET_CLOSED 거부가 쌓여 살아있는 포지션이 ZOMBIE 로 오인·terminal 될 수 있음.

## 올바른 예측자
`core/live_recalc/session_exit_rail.py::_fx_in_session(ts)` 가 fx_indices_cal 의 실제
세션 경계(금 22:00 UTC ~ 일 22:00 UTC closed)를 안다. fix 는 session_gate(엔트리 RTH 게이트)
와 session_exit_rail(캘린더 플랫) 두 추상화를 혼동했다.

## 부차 결함
- `close_reject_counts` 가 partial-close 경로(`_persist_partial_close`)에서 reset 안 됨 →
  stale tally 누적. docstring "Reset on a successful close" 는 미구현.
- "consecutive" 라고 적었으나 실제로는 누적(reset 없음) — 라벨 오기.
- `_reconcile_orphan(available=0.0)` 재사용 → audit `orphan_reconciled` + reason
  "available~0 qty over-count" 는 좀비(venue auto-close/deal expired)와 의미 불일치, forensics 오도.

## 교훈
세션 판정은 **track 별 calendar** 로 분기해야 한다. 단일 venue gate(Alpaca 전용)를
다른 track 에 재사용 금지. fix 의 검증 코멘트가 코드와 모순될 때 = builder self-review bias.
