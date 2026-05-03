---
entity_type: adr
entity_id: ADR-009
auto: false
last_modified: 2026-05-03
expires: 2026-08-03
editable: true
back_links: ["[[ADR-001]]", "[[INSIGHT-007]]", "[[ADR-007]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/spot, priority/p0, polaris]
---

# ADR-009 — SPOT-only 유지 결정 + PERP 검토 (모태 ADR-011 인수)

## Status
- proposed: 2026-05-03 (모태 ADR-011 인수)
- provisional: 2026-05-03 (Polaris 적응)
- expires: 2026-08-03 (3개월 내 PERP 검토 결정 또는 재validate)

## Context (모태 ADR-011 발견)

모태 데이터 증명 — **SPOT cash long-only paper Lv1 = 수학적 net 양수 불가능**:

| Variable | 측정 | 분석 |
|---|---|---|
| `avg_gross_pct` | -0.107% (228 closed) | entry 후 가격 자체 음수 drift |
| `gross_wr` | 47.4% | random보다 worse |
| `best_hold_bucket` | 10-30min: +0.034% | 유일 양수, 매우 작음 |
| `paper_Lv1_fee` | 1.4% round-trip | gross의 41배 |
| `gap` | gross 0.034% < fee 1.4% | **부등식 못 풀음** |

→ 모태 결론: **PERP paradigm shift** (SWAP + leverage 5-10x + short 양면).

## Polaris ADR-001 모순 가능성

Polaris ADR-001은 "SPOT-first fresh start" 결정. 모태 ADR-011은 "PERP로 전환"이 답이라 함. **잠재 모순**.

## Decision (Polaris 입장)

**Phase 2-3은 SPOT-only 유지** (ADR-001 보존). 단 다음 의무:

### 1. SPOT 알파 가설은 fee 수학 fast-fail gate 필수 ([[INSIGHT-007]])
- 모든 HYPOTHESIS-NNN BACKTEST 단계에서 `fee × 2 < expected_TP` 검증
- 통과 못 하면 즉시 archived + INSIGHT 작성

### 2. SPOT-only가 수학적 불가능으로 확인되면 PERP 검토 ADR 별도
- 모태 ADR-011 같은 데이터 (228+ closed trades) 누적 후 평가
- Phase 4 점진 확장 시점에 evidence-based 결정
- 결정은 codex-debate + Jin ack (P3)

### 3. PERP 도입 가능성은 옵션으로 vault에 보존 (이 ADR-009)
- 즉시 도입 X
- 3개월 후 (2026-08-03) 재validate — Polaris SPOT 운영 결과로 결정

## Consequences

### 긍정
- ADR-001 (SPOT-first) 일관성 유지
- 모태 발견 (PERP 가능성) 보존 — 미래 옵션 인지
- 수학적 위험 명시 (BACKTEST fast-fail gate 의무)

### 부정
- Polaris도 같은 SPOT 함정 빠질 수 있음 (모태 12 cycle ad-hoc tuning 반복)
- PERP가 답일 수도 있는데 시작 전부터 차단

### Mitigations
- INSIGHT-007 fee 수학 fast-fail gate 의무 → 함정 사전 차단
- 3개월 재validate (이 ADR expires) → PERP 진지 검토
- Pilot framing ([[north_star]]) — 손실 = 검증 데이터

## Codex Debate Summary

이 ADR 자체는 Polaris bootstrap 단계 결정 — codex 외부 리뷰 후 합의 필요.
Round 1 (이 노트 작성 시점): 미실행.

## Verification
- [ ] Phase 2a HYPOTHESIS-001 BACKTEST 시 fee fast-fail gate 적용
- [ ] 3개월 후 (2026-08-03) Polaris SPOT 누적 데이터로 PERP 검토 ADR-NNN 작성 또는 이 ADR 갱신

## Rollback Path
- SPOT 운영이 명백히 수학적 불가능 (60_alpha 가설 5+ 연속 archived) 발견 시 → PERP 검토 ADR 즉시 작성
- ADR-001 폐기 또는 보강 (별도 codex-debate + Jin ack)

## Related
- ADR-001 (SPOT-first fresh start — 본 ADR이 잠재 모순 명시)
- INSIGHT-007 (OKX SPOT fee 수학 — fast-fail gate 근거)
- ADR-007 (Paper sizing freedom)
- 60_alpha/_README (Promotion Gate fee 검증)
