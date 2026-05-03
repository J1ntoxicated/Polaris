---
entity_type: adr
entity_id: ADR-010
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-012]]", "[[INSIGHT-007]]", "[[60_alpha/_README]]", "[[ADR-009]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/alpha, priority/p0, polaris]
---

# ADR-010 — Backtest + Paper Parallel (백테스트 신뢰도 한계 대응)

## Status
- proposed: 2026-05-03 (Jin "백테스트를 얼마나 믿을 수 있는건데?" 의문)
- provisional: 2026-05-03 (codex 정량 분석 → 방향 B 합의 82%)

## Context

[[INSIGHT-012]] 정량: 백테스트 90일/50 trades 통계 신뢰도 낮음 (Sharpe CI 넓음, regime bias 70%, overfitting 45-55%).

원래 60_alpha 워크플로: HYPO → BACKTEST → PAPER → Promotion Gate → ADR
- BACKTEST를 alpha 검증 1차 게이트로 가정
- 그러나 BACKTEST = 신뢰 불가능한 alpha 예측

## Decision

**방향 B**: 백테스트 + 페이퍼 트레이딩 **병행** (parallel), 백테스트 = fast-fail gate + 버그 탐지만.

### 새 60_alpha 워크플로
```
HYPOTHESIS-NNN
  ↓
BACKTEST 24h (fast-fail + sanity check)
  ├─ fast-fail (expectancy > fee_rt) → archived
  ├─ Sharpe < 0.5 → archived
  └─ pass → 즉시 PAPER 시작
  ↓
PAPER 30일 (regime 샘플 추가, 진짜 검증)
  ├─ n_trades < 30 → 14일 연장
  ├─ paper Sharpe < 0.3 → archived
  └─ pass → Promotion Gate
  ↓
Promotion Gate (paper/live diff audit + sizing cap + kill criteria + rollback)
  ↓
ADR 승격 (라이브 결정) — Jin ack
```

### 백테스트 역할 변경
- 알파 검증 1차 게이트 X
- **버그 탐지 + fast-fail + sanity check** 만
- Sharpe / hit rate는 reference로만 (페이퍼 결과가 진짜)

### 리스크 관리 강화 (백테스트 신뢰도 낮음 보수적)
- 단일 포지션 ≤ 2% balance
- 일일 손실 한도 ≤ 5% balance
- weekly review with Jin

## Consequences

### 긍정
- 백테스트 한계 인지 + 페이퍼 우선
- 30일 페이퍼 → regime 샘플 추가 + 실제 broker behavior
- INSIGHT-007 fee 함정은 백테스트로 사전 차단 (시간 절약)
- 매몰비용 회피 (백테스트 fast-fail 시 페이퍼 진입 X)

### 부정
- 페이퍼 인프라 작성 즉시 시작 → 작업량 증가
- 30일 페이퍼 = 시간 비용 (HYPO 1개당 1개월+)
- 운영 인프라 미완성 시 페이퍼 시작 불가

### Mitigations
- Phase 2c 신설 (페이퍼 인프라) — 백테스트 + 페이퍼 인프라 병렬
- 페이퍼 인프라는 최소 (WS feed + simulated order book + position tracker)
- HYPOTHESIS 동시 N=2-3 진행 (페이퍼 30일 동시)

## Codex Debate Summary

- Round 1: 방향 A (백테스트 우선) 88% 합의
- Round 2 (백테스트 신뢰도 정량): 방향 B 82% 합의 (Sharpe CI/regime bias/overfitting 정량 근거)

## Verification
- [ ] promotion_gate.py: MIN_SHARPE 0.5, MIN_WIN_RATE 0.52 추가
- [ ] Phase 2c 신설 (페이퍼 인프라)
- [ ] HYPOTHESIS-001 노트 워크플로 update (fast-fail + 페이퍼 30일)

## Rollback Path
- 페이퍼 인프라 작업이 너무 무거우면 → 모태 OKX SPOT history 90일+ pagination 강화 + walk-forward + 다중 regime backtest로 fallback
- 단 모태 코드 인수 X (ADR-001) — 백테스트 데이터만 또는 새로 작성

## Related
- INSIGHT-007 (OKX SPOT fee 수학)
- INSIGHT-012 (백테스트 신뢰도 한계)
- ADR-009 (SPOT vs PERP — 페이퍼 결과로 트리거)
- 60_alpha/_README (워크플로 update)
