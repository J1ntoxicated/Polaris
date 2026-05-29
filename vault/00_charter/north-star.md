---
type: charter
status: active
date_created: 2026-05-06
date_updated: 2026-05-30
tags: [charter, north-star]
related: [[aggressive-bias]], [[active-autonomous-vision]], [[ADR-002]]
---

# 북극성 — Polaris North Star

## Vision
**Active autonomous evolution system** for crypto + CFD demo trading. Multi-stream concurrent (OKX SPOT + Capital CFD + Alpaca equity), per-gate AI supervisory, 자가 진화 + 자가 correcting.

## Operating thesis (Jin 2026-05-30) — surgical-strike, evidence-based profit
- **오직 수익.** 정확한 타이밍 진입 + 정확한 타이밍 엑싯 = "서지컬 스트라이크". 근거 없는 거래 금지.
- **데이터 보강 → 판단 정밀·신속**: 부족한 데이터(뉴스·매크로·CoinGlass 펀딩/OI/청산·FRED·Quandl·MyFxBook 센티먼트) 충당 + 기술계산/AI콜로 더 정확하고 빠른 판단.
- **근거 있는 거래**: 각종 데이터 → 레짐 확정 → 전략 확정 → 실시간 라이브 포지션 모니터 → 엑싯 확장.
- **손실방어 + 공격적 수익 동시**: 손실방어는 **정밀 엑싯**(적응형 stop/타이밍)으로 달성한다 — 사이징 축소·진입 차단으로가 아니다.
- ⚠️ **구분(중요)**: 매크로/뉴스/alt-data 는 **진입 근거 SIGNAL · 레짐 evidence** 로 쓴다 (권장). 방어적 **차단·축소**(blackout/throttle)로 쓰는 것은 아래 "거부된 전제" 그대로 무효.

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
- macro guard / news blackout / regime auto-throttle (= 방어적 **차단·축소**로서만 무효; alt-data 를 진입 근거 SIGNAL·레짐 evidence 로 쓰는 것은 권장 — ↑ Operating thesis)
- regrets/ 디렉토리 (anti-stealth-conservatism log = 보수 위장)
- monthly review / 30일 lock-in
- Posture standard / defensive

## Key decisions (cross-ref)
- [[ADR-001]] Vault Structure / [[ADR-002]] Vision / [[ADR-003]] 8-Layer Architecture
- [[ADR-004]] Per-Gate AI Pipeline / [[ADR-005]] Sizing + Cell Routing
- [[ADR-006]] Cell Matrix / [[ADR-007]] Learner Network / [[ADR-008]] 7 Strategies
