# Wave 7 — Signal Generation Trend Layer (INSIGHT-024 Root Fix)

**Date**: 2026-04-28
**Status**: Draft (architectural large — Jin 결정 영역)
**Vault refs**: [[INSIGHT-024-cap-commodity-fitness-deficit-2026-04-27]] [[INSIGHT-021]] [[2026-04-27-polaris-wave-2b-3a-design]]

---

## 1. Context

INSIGHT-024 escalated. Wave 2B `_remap_trend_score` (commit `f7d4da3b`) 적용 후 commodity NET 효과 0:
- 24h commodity NET **-$853** (이전 -$552 → 악화)
- 7d Palladium short **16/-$581/0% WR** (chronic sustained)
- 6 trend strategies trade_count 0-1 (signal source 부족)

## 1.5. Root Cause 정정 (2026-04-28 09:50 forensic)

**진짜 root cause = Strategy entry_params weights 영역**:
- `DualThrustSignal` + `SessionBreakoutSignal` providers 모두 **이미 wired** (boot/wiring_signals.py:118, composer.py `_GROUP_PROVIDERS["commodity"]`, themes.py)
- **6 trend strategies 모두 weights 에 `dual_thrust`/`session_breakout` 무**:
  - crypto_momentum_reversal_g1_gauss: liquidation/taker/technical/funding (crypto 영역)
  - contrarian_commodity_g*: sentiment/funding/ls_ratio/taker/fear_greed/technical/liquidation (crypto-style)
- `contrarian: True` flag 잔존 (이름은 trend 인데 logic 은 fade)
- 24h commodity entries 180/0 score >= 60 (trend signal entry threshold 못 넘음 — weights 에 trend providers 없으니 composite score 가 trend 영역 도달 못 함)

**원래 spec (Wave 7 Phase 1 = 새 providers 추가)는 redundant** — 진짜 fix = strategy weights template.

---

## 2. Decision Matrix

| Option | Scope | 북극성 | Risk | 단계 |
|---|---|---|---|---|
| **A1. 신규 trend providers (momentum_breakout, trend_persistence)** | provider layer 추가 | amplify (새 source) | medium | Phase 1 |
| A2. Existing providers tune (mode flag) | technical/momentum mode | bug regression | high | Skip |
| **A3. Group-aware composite weighting** | commodity 영역 momentum weight ↑ | composite reweight | low | Phase 1 |
| A4. New strategy class (CommodityTrendFollow) | code-level new class | structural | very high | Wave 8 |
| A5. Composer remap threshold tune | sweet 60-80 | minimal change | low ROI | Skip |

**권장: A1 + A3 phased** (Phase 1 — minimum viable). A4 (new strategy class) 는 새 providers 학습 후 Wave 8.

---

## 3. Phase 1 Implementation

### Step 1 — `momentum_breakout_provider` 추가

**File**: `invasion/signals/providers/momentum_breakout.py` (신규)

**Logic** (vault-grounded):
- `n-bar high breakout` (예: 20-bar high) + ATR-normalized
- Score: 0~100 (high breakout magnitude)
- contrarian fade 와 정반대 — **momentum amplification** 

```python
def score(market_data: dict) -> dict:
    """Momentum breakout signal — long if price breaks N-bar high.
    
    Trend market 정합 (commodity, momentum-driven). Contrarian fade
    의 sweet-spot mean reversion 과 정반대 logic.
    """
    high_n = market_data.get(f"high_{N}", 0)
    price = market_data.get("price", 0)
    atr = market_data.get("atr_pct", 0)
    if not high_n or not atr:
        return {"score": 0, "tag": "no_data"}
    breakout_pct = (price - high_n) / high_n if high_n > 0 else 0
    score = min(100, max(0, breakout_pct * 1000))  # tune
    return {"score": score, "tag": "momentum_breakout"}
```

### Step 2 — `trend_persistence_provider` 추가

**File**: `invasion/signals/providers/trend_persistence.py` (신규)

**Logic**:
- N-bar 동안 동일 방향 momentum 유지 비율
- Score: 0~100 (consecutive directional bars %)

```python
def score(market_data: dict) -> dict:
    """Trend persistence — N consecutive same-direction bars."""
    bars = market_data.get("recent_bars", [])
    if len(bars) < 5:
        return {"score": 0, "tag": "no_data"}
    last_dir = 1 if bars[-1]["close"] > bars[-1]["open"] else -1
    consecutive = sum(1 for b in reversed(bars[:-1])
                       if (1 if b["close"] > b["open"] else -1) == last_dir)
    score = min(100, consecutive * 20)  # 5 consecutive = 100
    return {"score": score * last_dir, "tag": "trend_persistence"}
```

### Step 3 — composer.py `_GROUP_PROVIDERS` commodity 추가

```python
"commodity": {
    "technical", "momentum", "volatility", "price_action", "macro_regime",
    # WAVE-7 PHASE-1: trend signal generators (INSIGHT-024)
    "momentum_breakout", "trend_persistence",
},
```

### Step 4 — Group-aware composite weighting (Phase 1)

composer.py `CompositeScorer.score()` 의 weighted aggregation 에 group context 추가:

```python
def score(self, market_data: dict, group: str = "crypto") -> CompositeResult:
    # group-aware weighting
    if group == "commodity":
        # Trend market: momentum_breakout / trend_persistence 가중
        provider_weights["momentum_breakout"] = 1.5
        provider_weights["trend_persistence"] = 1.5
        provider_weights["technical"] = 0.7  # mean reversion 약화
    # ... existing aggregation
```

또는 preg 기반 (`composite_weight_commodity_momentum_breakout`).

### Step 5 — Wiring (boot/wiring_signals.py)

새 providers 등록.

### Step 6 — Preg

```python
_reg("wave7_trend_providers_enabled", 1, (0, 1), "signal",
     "signals/composer.py:CompositeScorer (Wave 7)",
     "Trend signal generators 활성화 master switch")
_reg("composite_weight_commodity_momentum", 1.5, (1.0, 3.0), "signal",
     "signals/composer.py:score (Wave 7)",
     "Commodity 영역 momentum_breakout weight (amplify-only)")
```

---

## 4. North Star alignment

- ✅ Block 0 (provider 추가, weighting 변경)
- ✅ Amplify-only (commodity 영역 trend providers 가중 ↑, technical 약화는 weight 변경 ≥ 0.5)
- ⚠️ Technical weight 0.7 = dampen 가능성 → preg lower bound 1.0 mandate vs group calibration tradeoff
- ✅ INSIGHT-024 root fix
- ✅ `feedback_no_block_filter_architecture` (block 추가 X)

---

## 5. Verification Plan

### Per-batch
- AST + import smoke
- Provider register verification (boot)
- composer.py group dispatch unit test

### 1-7 days observe
- 24h commodity NET 변화 (-$853 → target)
- Palladium short pattern 변화 (chronic sustained 해소?)
- 6 trend strategies trade_count 증가 (signal source 충분 시)
- TP/TRAIL ratio commodity 영역

---

## 6. Wave 8 후속 (별도 spec)

- New strategy class `CommodityTrendFollow` (signal logic per class)
- Asset_class 별 fitness mult 학습 (cell_matrix 8-dim 활용)
- Wave 7 효과 측정 후 결정

---

## 7. Risk

1. **Provider 추가 = signal evaluation cost 증가** — Phase 1 2개 만, perf 영향 작음
2. **Trend signal regression** — 기존 contrarian winners 영향 가능 (commodity 영역 한정)
3. **Empirical 학습 필요** — 새 strategies 가 trend signals 통합 학습 시간
4. **Technical weight 약화 (0.7)** — ADR-003 amplify-only mandate vs group calibration tradeoff

---

## 8. References

- Vault: [[INSIGHT-024]] [[INSIGHT-021]] [[ADR-003]]
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_aggressive_always_profit]] [[feedback_loss_profit_asymmetry]]
- Code: `invasion/signals/composer.py`, `invasion/signals/providers/`, `invasion/boot/wiring_signals.py`

---

## 9. Jin 결정 영역

이 spec 은 draft. architectural 큰 redesign — 자율 dispatch X.
- Phase 1 (A1+A3) 진행/보류?
- A4 (new strategy class) Wave 8 직접 진행?
- 다른 방향?
