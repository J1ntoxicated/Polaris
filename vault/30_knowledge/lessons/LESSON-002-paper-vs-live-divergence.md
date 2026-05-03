---
entity_type: lesson
entity_id: LESSON-002
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[INSIGHT-011]]", "[[60_alpha/_README]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 lessons #47 (Capital paper vs live divergence — Estee Lauder 7+ cycle, $60 paper loss)
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-002 — Paper vs Live Behavior Gap = Catastrophic Risk (모태 lessons #47 인수)

## Trigger (모태 사건, 2026-04-13 Jin "라이브였다 생각하면 개끔찍")

Capital paper "spread fill" simulate가 close 거짓 success 응답 → bot이 close 성공으로 인식 → 단 broker side에 position 그대로 → 30min cycle 무한 churn (Estee Lauder 7+ cycle, $60 paper loss). Live broker는 진짜 reject 응답 → backoff + PARK 정상 작동 (paradox: live가 더 stable).

## Rule

**Paper에서 통과한 broker interaction logic은 live behavior diff audit 의무.**

## Why

Paper 환경은 단순화된 시뮬레이션 — 실제 broker는 다음 측면에서 다름:
- Reject response code (paper "지연" vs live "permission denied")
- Slippage (paper 0 vs live 0.05~0.5%)
- Commission (paper $0 vs live 진짜 fee)
- PDT rule (alpaca live enforce)
- Liquidity (specific stock pre-market 가능 vs 불가)
- Failure recovery (close fail / partial / canceled)

Paper 통과만 보고 live 전환 시 silent fail 또는 cascade.

## How to Apply (Polaris)

### Phase 2b 컴포넌트 작성 시
- Paper/Live URL/key 분리 (INSIGHT-011 적용)
- broker interaction은 모두 mode-aware (`OKX_DEMO=true|false`)
- close/cancel 같은 destructive action은 sync 의무 (broker side 검증)

### 60_alpha Promotion Gate (Phase 2a)
- HYPO PAPER 통과 후 ADR 승격 전 **paper/live diff audit** 필수
- audit 항목:
  - reject response 비교 (모든 cancel/close)
  - slippage 측정 (paper에 강제 inject vs live 실측)
  - commission cost 모델 (live = 실제 fee 0.014 round-trip [[INSIGHT-007]])
  - PDT/liquidity/failure recovery 시나리오 검증

### Live 전환 P0 Checklist (별도 sprint, 충분 시간)
- 미장 안정 후
- 모든 component note에 "paper-only" → "verified-on-live" 표시
- codex 외부 리뷰 의무 (ADR-004) 추가 라운드

## Related
- INSIGHT-011 (demo WS URL 위험)
- 60_alpha/_README (Promotion Gate)
- principles P4 (Validation Boundary)
