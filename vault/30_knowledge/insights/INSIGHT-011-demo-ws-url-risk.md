---
entity_type: insight
entity_id: INSIGHT-011
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-001]]", "[[ADR-001]]", "[[40_components/_README]]", "[[LESSON-002]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-011 — Demo WS URL Risk (모태 코드 인수 시 즉시 조치)

> Polaris 첫 컴포넌트 (OKX SPOT WS feed) 작성 시 적용 의무.

## Evidence

모태 `auto_invasion_mk1-main/invasion/spot/ws_feed_spot.py`에 demo URL 하드코딩:
```python
# wss://wsuspap.okx.com:8443  (demo URL — paper trading only)
```

Polaris ADR-001 옵션 Y로 코드 인수 X — 그러나 첫 컴포넌트 작성 시 같은 실수 위험.

## Root Cause

모태가 paper 모드로 시작 → demo URL 하드코딩 → live 전환 시 교체 누락 위험. config로 분리 안 됨.

## Impact (Polaris)

### Phase 2b 첫 컴포넌트 작성 시 의무
- OKX SPOT WS feed 컴포넌트는 URL을 config 분리 (P1 Authority — config는 machine SSOT)
- 환경변수 `OKX_DEMO=true|false` 또는 `OKX_TRADING_MODE=paper|live` 분기
- 두 모드 모두 property-based test로 URL 정확 검증

### LESSON-002 (Paper vs Live 격차) 직접 연관
- demo URL → paper만 작동, live 전환 시 silent fail
- LESSON-002 행동 규범: "Paper 통과 logic은 live behavior diff audit 의무"

## Recommendation
- [ ] Phase 2b: OKX SPOT WS feed 컴포넌트
  - URL을 `config/okx_endpoints.json` 또는 환경변수로 분리
  - WS factory 함수에서 mode parameter 받기
  - property-based test: 모든 OKX_DEMO 값 (true/false/empty/invalid)에서 URL 정확 분기
- [ ] commit pre-check: hardcoded URL grep 검출 (또는 별도 lint)
- [ ] live 전환 P0 checklist 작성 (LESSON-002 적용)

## Related
- INSIGHT-001 (모태 spot 누더기)
- ADR-001 (SPOT-first fresh start)
- LESSON-002 (Paper vs Live divergence)
- 40_components/_README (컴포넌트 작성 가이드)
