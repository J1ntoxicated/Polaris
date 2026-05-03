---
entity_type: insight
entity_id: INSIGHT-007
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[ADR-009]]", "[[60_alpha/_README]]", "[[INSIGHT-003]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 12h 운영 측정 (68 closed trades) + OKX API 직접 query
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-007 — OKX SPOT Lv1 Fee 0.7% → Scalp 수학적 불가능 (모태 INSIGHT-032 인수)

> Polaris 60_alpha 워크플로의 fast-fail gate 핵심 근거.

## Evidence (모태 측정)

### 12h 운영 후 68 closed trades
- TIME 47 / TP 20 / HARD_STOP 1
- TP 20건 모두 **net 음수** (pnl_pct +0.09%인데 net -$0.65/trade)
- Total NET: -$48 / 12h

### OKX API 직접 query (paper Lv1)
```
GET /api/v5/account/trade-fee?instType=SPOT&instId=BTC-USDT
→ maker: 0.0700%, taker: 0.0700% (per-side)
→ round-trip: 1.4% (fee × 2)
```

## Root Cause

OKX paper Lv1 fee 0.7%/side. Scalp 전략의 expected TP가 0.5% 수준이면:
- Gross +0.5% - Fee 1.4% = **Net -0.9%** (매 trade 손실 보장)

수학적으로 net 양수 불가능 — TP > 1.4% (round-trip fee) 필수.

## Impact (Polaris)

### Phase 2 직접 영향
- HYPOTHESIS-001 BACKTEST 단계에서 fast-fail gate 발동 기준
- `expected_TP > fee × 2` 통과 의무 (60_alpha Promotion Gate)

### Polaris ADR-001 SPOT-only 재검토
- 모태는 이 INSIGHT로 PERP shift ([[ADR-009]])
- Polaris는 SPOT 유지하되 fee fast-fail gate 의무 — 통과 못 하면 PERP 검토

## Recommendation
- [ ] Phase 2a: HYPOTHESIS-001 BACKTEST `expected_TP > 1.4%` 통과 의무
- [ ] Phase 2b: entry_atr / sizing 컴포넌트에 fee 0.014 (round-trip) 명시 (INSIGHT-006 frozen_params 정합)
- [ ] Phase 4: 라이브 운영 시 Tier 상승으로 fee 감소 모니터링 (Lv2~Lv5: 0.06~0.04%)
- [ ] OKX SPOT 가설 5+ 연속 fast-fail = ADR-009 PERP 검토 트리거

## Related
- ADR-001 (SPOT-first)
- ADR-009 (SPOT vs PERP paradigm)
- INSIGHT-003 (edge calibration baseline)
- INSIGHT-006 (frozen_params boundary — fee 0.014 명시)
- INSIGHT-009 (fee floor miswire cascade)
- 60_alpha/_README (fast-fail gate)
- _INHERIT_QUEUE
