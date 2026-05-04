---
entity_type: insight
entity_id: INSIGHT-027
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[HYPO-010]]", "[[INSIGHT-022]]", "[[INSIGHT-018]]"]
mode: forensic
reviewed_by: forensic-investigator
tags: [type/insight, status/active, scope/alpha, priority/p2, polaris]
---

# INSIGHT-027 — HYPO-010 / HYPO-017 신호 직교성 확인

> HYPO-017 BTC cascade는 HYPO-010 TickMomentum과 실측 11.1% raw overlap, 인과 cascade ~0% — 구조적으로 orthogonal, 즉시 구현 정당화.

## Context

Phase 2g Round 11 — Codex Round 11 우려 "HYPO-010이 이미 24h_change + high_proximity로 유사 momentum 커버, HYPO-017 redundant 위험" 검증 요청.

## Evidence

### HYPO-010 신호 구조 (코드 확인)
파일: `src/strategies/tick_momentum.py:57-82`
- 사용 필드: `last`, `open24h`, `high24h`, `low24h`, `bid`, `ask` — **BTC 참조 없음**
- ENTER_LONG: `(last - open24) / open24 ≥ 0.015 AND last > high24 * 0.99 AND spread_bps ≤ 50`
- ticker 자신의 24h OHLC 기반 slow momentum filter

### paper_log BTC ±60s overlap 측정
출처: `vault/50_runtime/paper_log_*_tick_momentum.md` 전체 10파일

| BTC OPEN | 시각 | ±60s non-BTC | 인과 방향 |
|---|---|---|---|
| #1 | 11:55:57 | 3건 | 부팅 cluster (SOL·DOGE·PEPE·ORDI는 BTC보다 15~24분 앞서 독립 진입) |
| #2 | 12:01:20 | 5건 | SOL -60s, PEPE -9s (BTC보다 앞) — cooldown 만료 우연 중복 |
| #3 | 12:41:59 | 0건 | 완전 독립 |

전체: 8/72 = **11.1%** raw overlap. 인과 cascade (BTC lead → 알트 follow): **~0건**

### HYPO-017 vault 미존재 확인
`vault/60_alpha/active/`, `archived/` — HYPO-017 파일 없음. 현재 제안 단계.

## Root Cause

HYPO-010 vs HYPO-017은 신호 레이어가 다른 orthogonal alpha:

| 차원 | HYPO-010 | HYPO-017 (제안) |
|---|---|---|
| 신호 소스 | 알트 자체 24h OHLC | BTC + ETH 1min Δ (cross-ticker) |
| 시간축 | 24h 누적 slow momentum | 1분 fast impulse |
| 진입 trigger | 알트가 이미 +1.5% + high 근처 | BTC가 지금 막 움직이는 순간 |
| 포착 alpha | 알트 independently 강할 때 | BTC impulse → 알트 lag gap |

Raw overlap 11.1%는 cooldown 만료 타이밍 우연 중복으로 설명됨. BTC cascade 인과 signal: ~0%.

## Impact

- 직접: Codex Round 11 overlap 우려 = false positive. 두 전략 중복 없음.
- 간접: HYPO-017이 "알트 24h_change 이미 높은 상태"에서 진입한다면 HYPO-010과 중복 가능 — 이 경우만 위험.

## Recommendation

- [ ] HYPO-017 즉시 구현 — overlap 우려 불식됨 (code-implementer)
- [ ] HYPO-017 진입 조건에 `alt_24h_change < +0.5%` orthogonality guard 추가 권고 — "아직 알트 반응 없는 상태" 선택적 포착, HYPO-010 집합과 zero 겹침 (code-implementer 설계 시 반영)
- [ ] HYPO-017 1min BTC Δ 측정 data feed 설계 확인 — 현재 WS tick `open24h` 기반이 아닌 별도 window 필요 (code-implementer)

## Related

- [[HYPO-010]] tick_momentum.py
- [[INSIGHT-022]] Phase 2g MTA + Binance spec
- [[INSIGHT-018]] realtime tick-driven discovery
- [[ADR-012]] realtime shift
