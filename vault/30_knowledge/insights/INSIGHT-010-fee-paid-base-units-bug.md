---
entity_type: insight
entity_id: INSIGHT-010
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-007]]", "[[40_components/_README]]", "[[LESSON-001]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p1, polaris]
---

# INSIGHT-010 — `fee_paid` 컬럼 Base Coin Units Corruption (모태 INSIGHT-035 인수)

> P7 Property-based Testing 직접 적용 영역 — 단위 검증 의무.

## Evidence (모태 forensic Cycle 2)

PEPE trade id=239 row: `fee_paid = -2,811,397` (비현실적 음수, base coin units).

## Root Cause

OKX V5 `/api/v5/trade/order` 응답의 `fee` field 가 **instrument 따라 단위 다름**:
- `feeCcy='USDT'` (대부분 SPOT) → quote ccy USD
- `feeCcy='PEPE'` (sub-cent token 일부) → base coin units

모태 `okx_spot_client.py:166` 의 `fee=float(d.get("fee", 0))` 가 feeCcy 무시하고 raw 저장 → DB의 trades.fee_paid가 instrument 따라 mixed unit.

## Impact (Polaris)

### Phase 2b 직접 영향
- OKX SPOT 클라이언트 컴포넌트 작성 시 feeCcy 명시 처리 의무
- numeric column 정합성 검증 (P7 property-based test)

### LESSON-001 (NULL cascade) 직접 연관
- 잘못된 unit이 downstream computation에 cascade → fee_pct, expectancy, ELO 모두 오염

## Recommendation
- [ ] Phase 2b OKX 클라이언트: `fee_ccy + fee_amount` 두 column 분리, USDT 변환은 명시 conversion
- [ ] Property-based test (P7): fee_paid가 항상 USDT unit + 양수 또는 0 (Polaris quote ccy 통일)
- [ ] DB schema: `fee_paid_usdt REAL NOT NULL DEFAULT 0`, `fee_ccy TEXT NOT NULL DEFAULT 'USDT'` 분리

## Related
- INSIGHT-007 (OKX SPOT fee 수학 — fee 정합)
- LESSON-001 (NULL cascade)
- 40_components/_README
- _INHERIT_QUEUE
