---
entity_type: insight
entity_id: INSIGHT-005
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-003]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p1, polaris]
---

# INSIGHT-005 — Regime Presets Base (모태 인수)

## Evidence

`data/regime_presets.json` scoring thresholds + 4 regime 파라미터.

### Scoring Thresholds (모태 검증)

**VIX bands**: 12 (risk_on) / 17 (transition) / 22 (risk_off) / 30 (strong) / 40 (crisis)
**Fear-Greed**: 25 (extreme_low) / 40 (fear) / 60 (greed) / 70 (extreme_high)
**DXY**: 98 (weak) / 103 (neutral) / 107 (strong)
**HY (high yield)**: 300 (calm) / 400 (stress) / 500 (crisis)
**MOVE**: 100 (stress) / 130 (crisis)
**Funding**: -0.01 (bearish) / 0.03 (bullish)
**ADX**: 20 (weak) / 25 (strong)

### Regime별 (Polaris 초기값 후보)

**RISK_ON** (min_score 10): okx_margin 24%, hard_stop -2.0%, bep_activate 0.5, bep_distance 0.3, flat_kill 5400s, no max_hold

**NEUTRAL** (모태 _README 인용 — okx_margin 10%, max_hold 1800s, bep_activate 0.5)

**RISK_OFF** + **CRISIS**: 더 엄격한 sizing + 짧은 max_hold

## 활용 (Polaris)

### Phase 2b 컴포넌트 초기값
- regime 분류기 함수 작성 시 위 thresholds 그대로 base
- single source: `vault/60_alpha/HYPOTHESIS-NNN`에서 검증 후 ADR 승격 시 코드 config 정착

### SPOT 적응 필요
- okx_margin 24%는 perp leverage 가정 → Polaris SPOT은 leverage 1.0
- → 모태 24% margin = $1000 position. Polaris는 그냥 max_position_size_usd 직접 명시 (frozen_params.json 참조 [[INSIGHT-006]])

## Risk
- max_hold은 모태 cell-aware 학습값 — Polaris 초기값으로만, 라이브 학습 후 갱신
- VIX/FG/DXY는 macro regime — crypto SPOT 영향이 모태 측정과 다를 수 있음

## Recommendation
- [ ] Phase 2b: regime 분류기 코드 작성 시 INSIGHT-005 thresholds 인용
- [ ] Phase 4: 라이브 운영 시 cell별 max_hold 학습 갱신
- [ ] 모태 regime_presets.json 전체는 vault에 가져오지 X — 핵심 thresholds만 (메타 작업 한도)

## Related
- INSIGHT-003 (Bayesian baseline)
- INSIGHT-006 (frozen_params boundary)
- 60_alpha/_README
- _INHERIT_QUEUE
