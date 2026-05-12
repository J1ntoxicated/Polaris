---
type: charter
status: active
date_created: 2026-05-06
tags: [charter, north-star]
related: [[aggressive-bias]], [[active-autonomous-vision]], [[ADR-002]]
---

# 북극성 — Polaris North Star

## Vision
**Active autonomous evolution system** for crypto + CFD demo trading. Dual-track concurrent (OKX SPOT + Capital CFD), per-gate AI supervisory, 자가 진화 + 자가 correcting.

## Targets (DEMO ONLY)
- **Primary**: 일 평균 +0.75% (compounding 252 → +560%/yr; $130k → $865k 가상)
- **Stretch**: 일 평균 +1.25% (~5,400%/yr 이론치)
- Daily intraday band: soft ±5% / stretch ±8% / ±8%+ tag-only

## Demo unlock
- OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) — 가상 USDT $79k
- Capital CFD demo (`demo-api-capital.backend-capital.com`) — 가상 AUD $78k
- 합산 ≈ USD $130k. **실제 자금 손실 = 0**. real-money 보수 논거 무효.
- Live 진입 결정 = 별도 ADR (Jin 단독, 본 plan 책임 X)

## Aggressive bias preserved
- "안전" 논거 = 점수 감점 / "Hi-risk hi-return" = 가산
- Daily target hard limit 없음 / monthly review 없음
- Auto-stop 없음 (Jin manual only) / Demo 자금 0 도달 → DB reset → restart
- 9-stack defensive collapse (v1) 영구 봉쇄 (T4 sizing — 1 scalar BEFORE clip + tier amplifier + hard MAX)

## 거부된 전제 (등장 시 무효)
- 12주 / 60d / 90d demo gate · KPI auto-disable · regulatory cap · capital protection
- ASIC retail / professional risk / fund mandate
- macro guard / news blackout / regime auto-throttle
- regrets/ 디렉토리 (anti-stealth-conservatism log = 보수 위장)
- monthly review / 30일 lock-in
- Posture standard / defensive

## Key decisions (cross-ref)
- [[ADR-001]] Vault Structure / [[ADR-002]] Vision / [[ADR-003]] 8-Layer Architecture
- [[ADR-004]] Per-Gate AI Pipeline / [[ADR-005]] Sizing + Cell Routing
- [[ADR-006]] Cell Matrix / [[ADR-007]] Learner Network / [[ADR-008]] 7 Strategies
