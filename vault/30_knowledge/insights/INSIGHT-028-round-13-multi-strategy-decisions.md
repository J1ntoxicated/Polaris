---
entity_type: insight
entity_id: INSIGHT-028
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[INSIGHT-027]]", "[[INSIGHT-022]]", "[[ADR-013]]"]
mode: alpha
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-028 — Round 13 Multi-Strategy Sizing Decisions (Codex 85% 합의)

> Round 12 deploy 후 60분 실측 기반 Codex Round 13 (85% 합의) — HYPO-010 size ×1.5, HYPO-016 즉시 deprecate trigger, HYPO-014 vol 완화, 시간당 $15~18 추정.

## Context

Phase 2g Round 12 deploy 후 60분 실측 측정 → 6개 active HYPO 데이터 수집 → Codex Round 13 submit (85% 합의 도달).

## Evidence

### 60분 실측 데이터

| HYPO | n | win% | CI_low | 총 PnL |
|---|---|---|---|---|
| HYPO-010 TickMomentum | 70 | 57% | 45.5% | +$8.25 |
| HYPO-008 VolumeBurst 1H | 29 | 55% | — | +$3.50 |
| HYPO-013 MTAConfluence | 1 | — | — | 1 TP 확인 |
| HYPO-014 BinanceLead | 1 | — | — | BLEAD-HOLD vol 미충족 다수 |
| HYPO-016 OFI Momentum | 5 | 20% | — | 100% signal_exit |
| HYPO-017 BTC Cascade | 0 | — | — | BTC trigger 미발생 |

### Codex Round 13 결정 (85% 합의)

| HYPO | 결정 | 근거 |
|---|---|---|
| HYPO-010 | **size 200→300** (×1.5) | 57% win CI_low 45.5% > 44.4% (n=70 양면검정 최소 경계), 즉시 scale-up 정당 |
| HYPO-010 | full ×2 (200→400) 보류 | n>=100 + CI_low > 51.6% 달성 후 재논의 |
| HYPO-016 | **n=10 TP=0 시 즉시 deprecate** | n=5 win 20% signal_exit 100% — HYPO-011 패턴 반복 (book imbalance 구조적 실패) |
| HYPO-014 | **vol 8→5 bps** 완화 | BLEAD-HOLD 다수 = threshold 너무 엄격, n 확보를 위해 완화 |
| HYPO-017 | 현행 유지 | BTC trigger 미발생 — 데이터 부족, 판단 보류 |
| HYPO-013 | 현행 유지 | n=1 — 판단 보류, 더 많은 trade 필요 |
| HYPO-008 | 현행 유지 | 55% win +$3.50 — 긍정적, 변경 불필요 |
| HYPO-015 Funding Rate (P2) | 구현 예약 | HYPO-016 n=20 후 슬롯 확보 시 구현 |

## Root Cause

HYPO-010 (57% win, n=70)은 실증 기반 scale-up 조건 충족. HYPO-016 signal_exit 100%는 HYPO-011 실패 패턴 동일 — OFI signed volume cumulation 방식도 tick 수준 noise에 취약. HYPO-014 vol 임계값 8 bps는 너무 보수적 → 5 bps로 낮춰 신호 빈도 확보 필요.

## Impact

### 시간당 PnL 추정

| 시나리오 | 추정 |
|---|---|
| HYPO-010 size ×1.5 단독 | +$12.375/h (기존 +$8.25 × 1.5) |
| HYPO-008 유지 | +$3.50/h |
| **합산** | **$15~18/h** |

### 북극성 "팍팍 따기" 진척

- 일간: $360~432/day
- 주간: $2,500~3,000/week
- 1개월: $10,000~13,000/month (×1.5 이후 안정 가정)

**전제**: n=70 win 57% 지속. CI_low 45.5% → 표본 증가 시 수렴 방향 모니터링 필수.

## Recommendation

- [x] HYPO-010 target_size_usd 200→300 적용 (code-implementer)
- [x] HYPO-014 vol_threshold 8→5 bps 적용 (code-implementer)
- [ ] HYPO-016 n=10 도달 시 TP==0 확인 → deprecate 실행 (vault-curator INSIGHT + INDEX 갱신)
- [ ] HYPO-010 n>=100 + CI_low > 51.6% 달성 시 ×2 (200→400) 재논의 (vault-curator)
- [ ] HYPO-015 Funding Rate — HYPO-016 n=20 후 구현 검토 (code-implementer)
- [ ] HYPO-017/013 지속 측정 — n>=10 후 다음 round (vault-curator)

## Related

- [[INSIGHT-027]] HYPO-010/017 신호 직교성 확인
- [[INSIGHT-022]] Phase 2g MTA + Binance spec (Round 2/3)
- [[ADR-013]] HARNESS Meta Mode — 모든 결정 dispatch
- [[ADR-003]] Codex debate protocol (85% ≥ 80% 기준 ADR-004)
