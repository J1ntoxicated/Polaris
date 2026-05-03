---
entity_type: insight
entity_id: INSIGHT-009
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-007]]", "[[ADR-007]]", "[[ADR-008]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 7d 226 trades 측정 + vault/DB/code 3-way audit
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-009 — Fee Floor Miswire + Sizing Cascade (모태 INSIGHT-034 인수)

> Polaris config SSOT 검증 의무 + ADR-007/008 적용 근거.

## Evidence (모태 측정 — 7d 226 trades)

- avg size_usd = $334 (min $9 / max $400)
- total notional = $75,522
- 7d fee 손실 (1.4% round-trip) ≈ $1,057 — edge 잠식 주범

## 4-fold Cascade

1. `spot_position_size_usd = 200` — paper 무한 자본인데 0.2% 활용
2. `spot_fee_round_trip = 0.0025` — INSIGHT-007 (모태 INSIGHT-032) 식별값 0.014 미반영 (5.6×)
3. `vol_factor` cap 2.0 — 추가 dampen
4. **vault sizing 결정 SSOT 부재** → 매번 재발견 + 토큰 낭비

## Root Cause

config 값과 vault SSOT 부재의 cascade:
- config 작성자가 INSIGHT 알지 못함 → 모태 default 그대로
- INSIGHT 발견 후에도 config update 안 됨 (vault에 결정 기록 없음)
- 매 세션마다 같은 발견 반복

## Polaris 적용

### ADR-007 (Paper sizing freedom) 채택
- spot_position_size_usd: 1000
- spot_fee_round_trip: 0.014
- vol_factor cap: 4.0

### ADR-008 (vol_factor PROPORTIONAL) 동시 적용
- ADR-007 cap 확대로 dead pair 4× 위험 → ADR-008 proportional fix로 보완

### Vault SSOT 강화 (P1 + P3)
- INSIGHT-006 (frozen params boundary) — config 값 모두 vault에 명시
- 변경 시 ADR + Jin ack
- vault_lint가 config 값 변경 검출 (Phase 1+ 확장)

## Recommendation
- [ ] Phase 2b config/frozen_params.json에 INSIGHT-006/007 정합 적용
- [ ] config 변경 protocol (P3 Write Path) 강제

## Related
- INSIGHT-006 (frozen params)
- INSIGHT-007 (OKX SPOT fee 수학)
- ADR-007 (Paper sizing freedom)
- ADR-008 (vol_factor proportional fix)
- _INHERIT_QUEUE
