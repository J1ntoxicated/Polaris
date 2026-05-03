---
entity_type: adr
entity_id: ADR-007
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-006]]", "[[INSIGHT-007]]", "[[ADR-008]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/spot, polaris]
---

# ADR-007 — Paper Sizing Freedom + Fee Floor 정합 (모태 ADR-009 인수)

## Status
- proposed: 2026-05-03 (모태 ADR-009 인수)
- provisional: 2026-05-03

## Context (모태 ADR-009 진단)

모태 INSIGHT-034 4-fold cascade:
1. `spot_position_size_usd = 200` — paper 무한 자본인데 0.2%만 활용
2. `spot_fee_round_trip = 0.0025` — INSIGHT-032 식별값 0.014 미반영 (5.6×)
3. `vol_factor` cap 2.0 — 추가 dampen
4. sizing 결정 vault SSOT 부재 → 토큰 낭비 재발견

## Decision (Polaris 적응)

### 1. Base Size
- spot_crypto: paper $1000 (frozen_params [[INSIGHT-006]] 정합)
- 미래: pilot 검증 후 $5000 까지 상향 가능

### 2. Fee Floor — 실측 정합
- spot_fee_round_trip: 0.014 (OKX paper Lv1 0.7%/side × 2)
- 모든 알파 가설은 fee 가정 명시 ([[INSIGHT-007]])

### 3. Vol Factor
- entry_atr.py vol_factor cap **2.0 → 4.0**
- 단 [[ADR-008]] proportional fix가 추가 — 모태 ADR-010이 cascade 발견

### 4. Vault SSOT
- 이 ADR이 sizing 결정 SSOT
- 변경 시 ADR + Jin ack (P3)
- frozen_params.json은 machine SSOT (P1) — vault는 설명만

## Consequences

### 긍정
- paper 자본 충분히 활용 (모태 0.2% → Polaris 1%)
- fee 정합 (gross < fee 함정 차단)

### 부정
- $1000 size = paper risk control 의미 약화 (자본은 가상이라 OK)
- vol cap 4.0이 dead pair 4×까지 확대 위험 → ADR-008로 보완

### Mitigations
- ADR-008 vol_factor proportional fix 동시 적용 의무
- pilot framing: 검증되기 전까지 손실 = 검증 데이터 ([[north_star]])

## Verification
- [ ] Phase 2b config/frozen_params.json에 spot_position_size_usd=1000, spot_fee_round_trip=0.014 명시
- [ ] entry_atr.py vol cap=4.0 + [[ADR-008]] 적용

## Rollback Path
- $1000이 paper에서도 무리면 $500 또는 $200 복귀 (별도 ADR)

## Related
- INSIGHT-006 (frozen params boundary)
- INSIGHT-007 (OKX SPOT fee 수학)
- ADR-008 (vol_factor proportional fix)
