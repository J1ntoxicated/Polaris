---
entity_type: insight
entity_id: INSIGHT-004
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-003]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-004 — Tournament ELO Top Strategies (모태 인수)

## Evidence

`data/tournament_elo.json`: round_count=6176, 200+ 전략 ELO.

### Top 5 ELO
| Rank | ELO | Strategy |
|---|---|---|
| 1 | 4481.4 | crypto_specialist_g193_g338_ai |
| 2 | 4391.5 | **volatility_spike** ← Polaris 후보 (간단한 named strategy) |
| 3 | 4371.0 | crypto_specialist_g193_g350_bayes |
| 4 | 4213.3 | crypto_specialist_g193_g302_struct |
| 5 | 4205.5 | crypto_specialist_g193_g343_struct |

## 활용 (Polaris)

### HYPOTHESIS 후보
- **HYPO-002**: `volatility_spike` (ELO 4391, top named) — Polaris SPOT-only 환경에서 재검증

### 진화 strategy (specialist_g*) 처리
- ELO 상위 4/5가 모태 evolver 산물 — Polaris evolver 폐기로 직접 이식 불가
- Polaris 정책: 60_alpha 워크플로로 명시적 수동 진화 (자동 X)
- 이름이 의미 없는 specialist_g193_g338_ai 같은 strategy = Polaris에 가치 X

## Risk
- ELO는 모태 환경 (멀티 거래소, perp) 측정 — SPOT-only 재검증 필수
- volatility_spike도 fee 가정이 모태와 다르면 SPOT에서 음 expectancy 가능 ([[INSIGHT-007]])
- 6176 round의 결과 — 일부 ELO는 noise 가능 (특히 round 적은 strategy)

## Recommendation
- [ ] HYPOTHESIS-002: volatility_spike SPOT-only 검증 + fee 수학 통과 시 BACKTEST
- [ ] specialist_g* 직접 이식 금지 — Polaris는 vault-driven 명시 진화

## Related
- INSIGHT-003 (Bayesian baseline)
- INSIGHT-007 (OKX SPOT fee 수학)
- 60_alpha/_README
- _INHERIT_QUEUE
