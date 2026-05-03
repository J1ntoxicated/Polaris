---
entity_type: insight
entity_id: INSIGHT-008
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[ADR-006]]", "[[40_components/_README]]", "[[LESSON-005]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p1, polaris]
---

# INSIGHT-008 — SPOT Taker Fallback Unwired → 57/58 Abandoned (모태 INSIGHT-033 인수)

> Phase 2b execution 컴포넌트 작성 시 stub vs production 명시 의무 근거.

## Evidence (모태 forensic)

2026-05-02 03:13 AEST 모태 forensic. SPOT bot trading 정상으로 보였으나:
- abandoned: 57
- closed: 1 (HARD_STOP)
- fill_type: 빈 문자열 = maker only fill 시도 후 5s timeout

## Root Cause

`invasion/spot/router_spot.py:65-66` taker fallback 분기가 stub:
```python
if taker_score >= taker_threshold:
    logger.info("taker fallback path not yet wired (Phase 3); abandoning")
    return {"fill_type": "abandoned", "reason": "taker_pending"}
```

ADR-007 Phase α 구현 시 maker-only + Phase 3 wire 예정 명시했으나 Phase 진행 누락. 추가:
- `spot_taker_score_threshold=0.85`
- 실제 PASS signals max score = 0.765 (RLUSD)
- → 모든 PASS signal score < threshold → taker fallback 자체 미발동 → 100% abandon

## Impact (Polaris)

### Phase 2b 컴포넌트 작성 시 영향
- execution 컴포넌트 stub 작성 금지 — production 단계 직접 wire
- 또는 stub 시 "Phase X 의존" 주석 + property-based test (P7)로 stub branch 진입 시 fail 강제

### lessons #44 (소비자 grep 증거 없는 commit 금지) 직접 사례
- ADR-007 Phase α 적용 시 Phase 3 wire 검증 안 함 → prod fire → 57/58 loss

## Recommendation
- [ ] Phase 2b execution 컴포넌트는 stub 금지 (또는 stub fail-fast)
- [ ] taker_score_threshold 같은 magic number는 frozen_params + ADR
- [ ] LESSON-005 (소비자 grep) 적용 — 신규 함수 wire 시 모든 caller path 검증

## Related
- ADR-006 (Spot trend N strategies)
- LESSON-005 (consumer grep evidence)
- 40_components/_README (component note 의무)
- _INHERIT_QUEUE
