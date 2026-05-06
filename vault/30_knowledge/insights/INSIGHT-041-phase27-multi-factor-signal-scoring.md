---
entity_type: insight
entity_id: INSIGHT-041
auto: false
last_modified: 2026-05-06
expires: 2026-11-06
editable: true
back_links: ["[[INSIGHT-040]]", "[[ADR-003]]", "[[_NOW]]"]
mode: debate
reviewed_by: codex
tags: [type/insight, status/active, scope/strategy, priority/p1, polaris]
---

# INSIGHT-041 — Phase 27: Multi-factor signal scoring 포팅 plan (codex debate 합의)

> Codex debate 합의: composer-lite (가중 합산 scorer) + Bayesian calibration only. 전체 ML 파이프라인 도입 거부. Polaris 30 ticker × N strategy에 즉시 적용 가능한 최소 경로.

## Context

Phase 26 완료 후 Jin 제안: 모태(auto_invasion_mk1) 의 multi-factor signal scoring을 Polaris로 포팅. Codex debate 1 라운드 합의(ADR-003 프로토콜: max 3라운드, 1라운드 합의 = 즉시 적용).

**Debate 쟁점**: 모태 scorer가 XGBoost + feature engineering pipeline을 포함하고 있어 Polaris P6 pure 원칙(전략 = 순수 함수)과 충돌 가능. Polaris에 적합한 scope를 어디까지 가져올 것인가.

## Codex Debate 합의 내용

### 합의 A: Composer-lite 채택

```python
# src/signal/composer.py (P6 pure — 상태 없음)
def compose_signal(signals: list[WeightedSignal]) -> ComposedSignal:
    """
    signals: [(strategy_id, direction, confidence, weight), ...]
    return: net_score ∈ [-1, 1], direction, conviction_pct
    """
    long_score  = sum(w * c for _, d, c, w in signals if d == "LONG")
    short_score = sum(w * c for _, d, c, w in signals if d == "SHORT")
    net = long_score - short_score
    direction = "LONG" if net > 0 else "SHORT" if net < 0 else "HOLD"
    conviction = abs(net) / max(sum(w for *_, w in signals), 1e-9)
    return ComposedSignal(net_score=net, direction=direction, conviction_pct=conviction)
```

- 가중치는 Elo 점수 기반 자동 갱신: `weight_i = elo_i / sum(elo_j)`
- 순수 함수 → P6 위반 없음. 1 파일, 0 external dependency.

### 합의 B: Bayesian calibration only (ML 전체 파이프라인 거부)

모태 XGBoost scorer는 거부. 이유:

| 항목 | 모태 XGBoost | Polaris Bayesian |
|------|-------------|-----------------|
| Training data 요구 | 10k+ labeled trades | 20+ trades per cell |
| 업데이트 주기 | 오프라인 재훈련 | 온라인 (매 trade) |
| P6 위반 여부 | 위반 (sklearn state) | 위반 없음 (Beta 분포 파라미터) |
| Polaris 적합성 | 불가 (초기 데이터 부족) | 즉시 적용 가능 |

```python
# src/signal/bayesian_calibrator.py
class BayesianCalibrator:
    """Beta(alpha, beta) per (strategy_id, regime) cell."""
    def update(self, strategy_id: str, regime: str, won: bool) -> None:
        cell = (strategy_id, regime)
        self._alpha[cell] += 1 if won else 0
        self._beta[cell]  += 0 if won else 1

    def win_prob(self, strategy_id: str, regime: str) -> float:
        a = self._alpha.get((strategy_id, regime), 1.0)
        b = self._beta.get((strategy_id, regime), 1.0)
        return a / (a + b)   # Beta mean
```

state는 `data/bayes_calibration.json` 에 persist (SQLite 아님 — 경량 JSON, 100 cells × 2 floats = ~2KB).

### 합의 C: PortfolioManager 연결 방식

```
Strategy.evaluate() → Signal(direction, confidence)
           ↓
BayesianCalibrator.win_prob(strategy, regime) → calibrated_confidence
           ↓
ComposerLite.compose_signal([WeightedSignal(...)]) → ComposedSignal
           ↓
PortfolioManager.on_signal(ComposedSignal)
  if conviction_pct >= CONVICTION_THRESHOLD (env, default 0.55):
      add_contribution(...)
```

CONVICTION_THRESHOLD=0.55 는 env override (`POLARIS_CONVICTION_THRESHOLD`). 낮추면 신호 빈도 증가, 높이면 필터 강화.

### 거부된 제안

- ❌ XGBoost / LightGBM online learning — training data 요구량 불가
- ❌ Feature engineering (OHLCV window 특징 추출) — P6 위반 + 과적합 위험
- ❌ Neural net confidence scorer — 운영 복잡도 과대

## Evidence (debate basis)

- Polaris P6 pure 원칙: `vault/10_constitution/principles.md` §P6
- ADR-003 debate 프로토콜: max 3 라운드, 1라운드 합의 즉시 적용
- 모태 scorer 위치: `/Users/jinyoon/Projects/auto_invasion_mk1-main/invasion/ai/` (read-only 참조)
- Elo weight 시스템: `vault/30_knowledge/insights/INSIGHT-004.md` (tournament ELO top strategies)

## Root Cause (왜 포팅 필요)

Phase 26 까지 Polaris는 각 strategy signal을 독립으로 처리. 동일 ticker에 LONG 2개 + SHORT 1개 겹치면 각각 contribution 생성 → 반대 방향 포지션 동시 보유 가능. Composer가 없으면 net conviction을 모르고 PortfolioManager가 방향 충돌을 묵인.

## Impact

- 직접: 신규 파일 2개 (`src/signal/composer.py`, `src/signal/bayesian_calibrator.py`), `src/risk/portfolio_manager.py` on_signal 연결.
- 간접: 방향 충돌 포지션 제거 → realized PnL 개선 예상. Elo 자동 가중 → 고성능 전략 자동 강화.

## Recommendation

- [ ] Phase 27.1 — `composer.py` + TDD ≥ 15 tests (P6 pure)
- [ ] Phase 27.2 — `bayesian_calibrator.py` + TDD ≥ 10 tests
- [ ] Phase 27.3 — PortfolioManager 연결 + conviction_threshold gate
- [ ] Phase 27.4 — `data/bayes_calibration.json` persist + load on startup
- [ ] DEV dispatch → code-implementer agent (ADR-004 의무)

## Related

- 직전 Phase: [[INSIGHT-040]] (Phase 26 dashboard + reconcile)
- Debate 프로토콜: [[ADR-003]]
- 현재 상태: [[_NOW]]
