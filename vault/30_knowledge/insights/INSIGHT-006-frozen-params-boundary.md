---
entity_type: insight
entity_id: INSIGHT-006
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-006 — Frozen Params Boundary (모태 인수)

> 모태에서 변경 시 ADR 필수로 표시된 동결 경계값. Polaris config 기본값으로 인수.

## Evidence (모태 frozen_params.json)

### Bots (자산별)

**spot_crypto** (Polaris 직접 적용):
- paper_balance_usdt: 5000
- max_open_positions: 20
- max_position_size_usd: 200 (단 ADR-009 paper sizing freedom으로 1000 후속 — [[ADR-007]] 참조)
- min_position_size_usd: 50
- tick_horizon_sec: 1
- strategy_reset_lookback_d: 7
- expectancy_floor_per_trade: 0.0

**spot_stock** (선택 — Alpaca paper):
- paper_balance_usd: 100000
- max_open_positions: 30
- max_position_size_usd: 1000
- tick_horizon_sec: 60

**cfd_main**: Polaris SPOT-only 결정 ([[ADR-001]])으로 N/A

### Global (전역 boundary)
- bot_reset_loss_pct: 15
- bot_reset_lookback_d: 7
- arch_escalate_lookback_d: 28
- arch_escalate_resets_threshold: 5
- ai_max_daily_cost_usd: 5.0
- ai_max_calls_per_hour: 30
- m2_paper_period_d: 7  ← Polaris promotion gate paper 기간 ([[60_alpha/_README]])
- m2_promotion_min_n_trades: 30  ← Polaris promotion gate 최소 trade
- m2_promotion_min_expectancy: 0.001  ← Polaris promotion gate expectancy
- okx_api_rps_max: 10
- alpaca_api_rps_max: 5

## 의미 해석

이 값들은 모태에서 다음 경험으로 boundary 정착:
- max_open_positions 20: 메모리/모니터링 한도
- max_position_size 200/1000: paper risk control
- bot_reset_loss_pct 15: 7일 누적 -15% 시 strategy 재학습
- m2_paper_period 7d + 30 trades: 통계적 유의성 최소 (Polaris promotion gate)

## Polaris 적용 (P1 + P3)

### Authority 분리 (P1)
- 이 INSIGHT는 frozen_params 값을 **설명**할 뿐, machine state로 작동 X
- 실제 config는 Polaris 코드의 `config/frozen_params.json` 또는 `config/__init__.py` (Phase 2b 작성)
- vault 노트에서 frozen_params write 금지

### 변경 protocol (P3)
- 변경 시 ADR 필수 + Jin ack 필수
- ADR proposed max 7일 (P2)
- 변경 사유 evidence-based (lessons #45 grep-before-guess)

## Recommendation
- [ ] Phase 2b: Polaris `config/frozen_params.json` 작성 시 위 값 인용
- [ ] config 변경 시 ADR + Jin ack
- [ ] Polaris는 **spot_crypto** 우선, spot_stock은 후속 ADR 결정 후

## Related
- ADR-001 (SPOT-first)
- ADR-007 (Spot trend N strategies — 모태 ADR-007 인수 후)
- principles P1 (Authority 분리)
- principles P3 (Write Path)
- 60_alpha/_README (m2_paper / promotion 값 사용)
- _INHERIT_QUEUE
