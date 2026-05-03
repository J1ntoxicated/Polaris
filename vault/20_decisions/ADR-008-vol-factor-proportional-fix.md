---
entity_type: adr
entity_id: ADR-008
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[ADR-007]]", "[[INSIGHT-007]]", "[[LESSON-005]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/spot, polaris]
---

# ADR-008 — vol_factor PROPORTIONAL Fix (모태 ADR-010 인수, CRITICAL)

## Status
- proposed: 2026-05-03 (모태 ADR-010 인수)
- provisional: 2026-05-03

## Context (모태 ADR-010 root cause)

모태 ADR-009 (paper sizing freedom) 적용 후:
- 7d 0/64 wins (모든 close loss)
- ATR<0.3% 86% 점유 (USDC/TRX/BNB dead pair dominant)

**Root cause**: `entry_atr.py:117` vol_factor 식이 수학적으로 반전:
```python
vol_factor = max(0.5, min(4.0, 0.005 / atr_pct))  # ❌ INVERSE
```
- atr_pct=0.012% (USDC dead) → 0.005/0.00012 = 41.6 → clamp 4.0 → **$4000 size on dead pair**
- atr_pct=0.6% (high-vol) → 0.005/0.006 = 0.83 → clamp 1.0 → 정상 size

**weight_resolver `atr_w` (0.10 floor) 와 40× 모순** — 두 layer 정반대 방향. 결과: dead pair에 큰 size, 정상 pair에 작은 size = fee 잠식 + 0% WR cascade.

## Decision

### Polaris vol_factor (PROPORTIONAL)
```python
vol_factor = max(0.10, min(4.0, atr_pct / 0.0015))
```
- atr_pct=0.012% (dead) → 0.012/0.15 = 0.08 → clamp 0.10 → **$100** ✓
- atr_pct=0.15% (BTC 5m) → 1.0 → **$1000** baseline
- atr_pct=0.6% (high-vol) → 4.0 → **$4000** scaling

weight_resolver atr_w 0.10 floor와 같은 방향 → no contradiction.

## Consequences

### 긍정
- dead pair (USDC/TRX 등) 자동 size 축소 ($100 minimum, fee 손실 한도 $1.4)
- high-vol pair에 size 집중 (true alpha 영역)
- ADR-009/010 cascade lesson 사전 차단

### 부정
- BTC/ETH 같은 mid-vol에 정확한 sizing 까지 시간 필요 (atr 계산 누적 후)

## Verification
- [ ] Phase 2b: entry_atr 또는 sizing 컴포넌트 작성 시 PROPORTIONAL 식 적용
- [ ] property-based test (P7): atr_pct 0~10% 범위에서 vol_factor monotone increasing 검증

## Rollback Path
- proportional이 mid-vol에서 over-size 발견 시 → cap 조정 (max 3.0 등, 별도 ADR)

## Related
- ADR-007 (Paper sizing freedom — 이 fix가 보완)
- INSIGHT-007 (OKX SPOT fee 수학)
- LESSON-005 (소비자 grep 증거 — vol_factor 변경 시 weight_resolver 정합 의무)
