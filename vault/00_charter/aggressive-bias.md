---
type: charter
status: active
date_created: 2026-05-06
tags: [charter, aggressive]
related: [[north-star]], [[active-autonomous-vision]]
---

# Aggressive Bias — 영속 원칙

## 원칙 1: Hi-risk Hi-return 우선
큰 사이즈 큰 리스크 OK (demo). 보수 논거 점수 감점. v1 9-stack collapse 재발 영구 봉쇄.

## 원칙 2: 묶지 마 (Don't bind)
- Drawdown auto-stop 없음 (snapshot만)
- Daily target hard limit 없음
- KPI auto-disable 없음
- Regime auto-throttle 없음
- Macro guard / news blackout 거부

## 원칙 3: 막지 마 (Don't block)
- Trade block / signal skip / signal reject 거부
- 자동 throttle / fail-closed 우선 거부 — fail-open default
- Strategy 자동 disable 거부 (Jin manual only)

## 원칙 4: 흐르게 (Flow, not block)
- 차단 / skip / reject 거부
- Sizing 결과 0 = 진짜 invalid input 만 (size_usd ≤ 0, fill_price ≤ 0)
- 실패 신호 = 즉시 retry, 영구 block X

## 원칙 5: Asymmetric payoff
- Winner 길게 / Loser 짧게
- 대칭 payoff = 위험 (역사적으로 -EV)
- Adaptive Exit AI 가 winner 길게 잡기 (default ATR×N 보다 멀게 OK)

## 원칙 6: 자가 진화 + 자가 correcting
- Learner network 7 (hourly auto-tune)
- Cell matrix routing (high score amplify, low suppress)
- Live recalc per-tick + regime flip 자동 조정
- Mid-trade strategy swap (AI 가 더 좋은 strategy 발견 시)

## Anti-pattern (재발 방지)
- v1 9-stack: kelly × conf³ × regime × dd × MAX_FRACTION × cold_start = 폭락
- v1 cold_start cap=$0: 모든 entry 0
- v1 OKX 401: base URL `www.okx.com` (international) — 실제 `us.okx.com`
- Codex round 1 ROLLBACK: demo context 누락 → real-money 보수 권고
- 정적 ticker (top 50 hardcoded) → Layer 0 dynamic universe 로 해결
- 정적 strategy lifecycle 4-method → signal generator only + AI gate 로 해결

## Cross-ref
- [[north-star]] / [[active-autonomous-vision]]
- `feedback_aggressive_always_profit.md` (memory)
- `feedback_no_defensive_param_dampen.md` (memory)
- `feedback_no_block_filter_architecture.md` (memory)
- `feedback_flow_not_block.md` (memory)
- `feedback_active_autonomous_vision.md` (memory, 신규)
