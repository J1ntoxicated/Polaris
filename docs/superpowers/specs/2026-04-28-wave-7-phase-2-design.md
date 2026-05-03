# Wave 7 Phase 2 — Live Signal Scoring Strategy Weights Override

**Date**: 2026-04-28 11:10
**Vault refs**: [[INSIGHT-024]] [[2026-04-28-wave-7-signal-generation-trend-design.md]]

---

## 1. Context

Wave 7 Phase 1 (commit `ea7fca6c`) deploy 후 1h **+$87.16 / 75.8% WR** 강력 효과 입증. 단 Phase 1 의 wire gap (dev-coder 보고):
> Group-level weight resolution gap — `signals/engine.py:_resolve_weights(group)` 는 group level 만, per-strategy `entry_params.weights` override 는 backtester + dashboard 만. Live entry scoring 시점 strategy weights override 는 Phase 2 future work

**진짜 architectural fix**: Live signal scoring 이 strategy entry_params.weights 사용 (현재 group default fallback).

---

## 2. Implementation

### Step 1 — Live engine `_resolve_weights` 확장

**File**: `invasion/signals/engine.py`

기존 `_resolve_weights(group)` → group default weights return.

확장: `_resolve_weights(group, strategy_id=None)` → strategy entry_params.weights 있으면 우선 사용, 없으면 group default fallback.

```python
def _resolve_weights(group: str, strategy_id: str | None = None) -> dict[str, float]:
    """Resolve provider weights — strategy override or group default.
    
    WAVE-7-PHASE-2: strategy_id 가 주어지면 strategies.entry_params.weights
    우선 사용 (per-strategy custom weights). 없으면 group default fallback.
    Live scoring 영역 — Phase 1 의 weight gap 해소 (INSIGHT-024 진짜 fix).
    """
    if strategy_id:
        try:
            from ..strategy.engine import StrategyStore
            store = StrategyStore.instance()
            s = store.get(strategy_id)
            if s and (entry := s.get("entry_params")):
                weights = entry.get("weights", {})
                if weights:
                    return weights
        except Exception as e:
            log_event("SIGNAL", f"strategy weights resolve err: {e}", "debug")
    # Group default fallback
    return _DEFAULT_GROUP_WEIGHTS.get(group, _DEFAULT_GROUP_WEIGHTS["crypto"])
```

### Step 2 — Caller update (composer.py + signal evaluation)

`composer.py` `score(...)` 에 `strategy_id` 인자 추가:
```python
def score(self, market_data, group="crypto", strategy_id=None):
    weights = _resolve_weights(group, strategy_id)
    # ... existing aggregation
```

`engine.py:evaluate(ticker, market_data)` 의 caller 가 strategy_id 전달:
- 현재 `composite = self.composer.score(market_data)` — strategy 정보 없이 호출
- 수정: strategy 선택 후 score 재계산 또는 lookup

**Architectural challenge**: 현재 evaluate flow 는 (1) signal score 계산 → (2) strategy match. 즉 score 가 먼저 계산됨, strategy 는 그 후 선택. Strategy weights override = score 자체가 strategy-dependent = flow 변경 필요.

**Option B (간단)**: 모든 strategy 가 group default weights 사용 → group default 자체를 group-aware (commodity 면 trend 영역) 정합 (이미 P1 일부 적용). Strategy override 폐기.

**Option A (재설계)**: Score-strategy decoupled — score 계산 시 모든 strategy weights 시도 후 best match.

→ Phase 2 = **Option B 권장** (간단, INSIGHT-024 root 충분 fix). Option A 는 Wave 8.

### Step 3 — Group default weights commodity 영역 강화

**File**: `invasion/signals/composer.py` `_DEFAULT_GROUP_WEIGHTS` (또는 `_default_weights`)

```python
_DEFAULT_GROUP_WEIGHTS = {
    "crypto": {  # contrarian fade (mean reversion)
        "sentiment": 30, "funding": 20, "ls_ratio": 15,
        "taker": 10, "fear_greed": 10, "technical": 15,
    },
    "commodity": {  # trend market (Wave 7 INSIGHT-024)
        "dual_thrust": 30, "session_breakout": 25,
        "technical": 15, "macro_regime": 15,
        "volatility": 10, "price_action": 5,
    },
    "forex": {
        "technical": 25, "momentum": 20, "macro_regime": 15,
        "sentiment": 15, "volatility": 15, "price_action": 10,
    },
    # stock / etf / indices ...
}
```

기존 fallback dict 가 이미 일부 wire (Wave 7 P1). 정식 group-aware mapping.

### Step 4 — Evolver mutation group-aware weights (Phase 2의 일부)

**File**: `invasion/strategy/evolver_mutations.py`

새 strategy mutation 시 group 따라 weights template 다르게:
```python
def _generate_initial_weights(group: str) -> dict[str, float]:
    """Generate initial weights template for new strategy mutation.
    
    Group-aware: commodity → trend providers, crypto → contrarian, etc.
    """
    return _DEFAULT_GROUP_WEIGHTS.get(group, _DEFAULT_GROUP_WEIGHTS["crypto"])
```

기존 mutation logic 의 weights generation 영역 변경.

---

## 3. North Star alignment

- ✅ Block 0 (weights template 변경, 추가 X)
- ✅ Amplify-only (모든 weights >= 0, lower bound 적정)
- ✅ INSIGHT-024 root fix (commodity strategies 자동 trend providers weight)
- ✅ `feedback_no_quick_patch_ever` (architectural 정합)
- ⚠️ Strategy override 폐기 (Option B) = 모든 strategies group default 사용 → 학습 자유도 ↓

---

## 4. Verification

- AST + import smoke
- composer score test (commodity group → trend weights 적용)
- Live entry: commodity entry 시 dual_thrust + session_breakout weight 사용 verify
- 1-7 days: commodity NET 추세 변화

---

## 5. Risk

1. **Strategy weights override 폐기 (Option B)** — 학습 자유도 손실. 단 group default 가 정합한 template 면 수용.
2. **178 commodity_specialist strategies 영향** — 모두 commodity group default 사용 = trend providers 자동 weight. 큰 영향 (긍정/부정 둘 다).
3. **Crypto/forex 영역 영향** — group default 변경 = 모든 영역 영향. crypto 는 기존 weights 정합 (변경 X).

---

## 6. References

- Vault: [[INSIGHT-024]] [[2026-04-28-wave-7-signal-generation-trend-design.md]]
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_overhaul_over_incremental]]
- Code: `invasion/signals/engine.py`, `invasion/signals/composer.py`, `invasion/strategy/evolver_mutations.py`
