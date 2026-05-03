---
entity_type: insight
entity_id: INSIGHT-017
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[INSIGHT-016]]", "[[HYPO-003|60_alpha/active/HYPOTHESIS-003-sma-crossover-1d]]", "[[ADR-004]]", "[[ADR-010]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: codex Round 1 review (78% 합의) + HYPO-003 재backtest
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-017 — Look-ahead Bias Fix + HYPO-003 Robust 재확인

> Codex Phase 2/2c 코드 review (78% 합의)에서 backtest engine **look-ahead bias** 발견. Fix 후 HYPO-003 재backtest — 결과 동일 (1d trend는 same-bar vs next-bar 차이 미미).

## Codex 발견 (Round 1)

### CRITICAL: Look-ahead bias
모태 backtest engine:
- 같은 bar `i` close에서 strategy.evaluate(window[:i+1]) 호출
- 신호 발생 시 **같은 bar `i` close 가격에 체결**
- = "확정된 close 보고 같은 close에 진입" = 미래 정보 활용

→ HYPO-003 +47% expectancy 결과 과대평가 가능성.

## Fix (engine.py)

```python
# Before (look-ahead bias):
for i in range(strategy.min_window - 1, len(candles)):
    signal = strategy.evaluate(candles[:i+1])
    current_close = candles[i].close
    # entry/exit at current_close ← BUG

# After (fix):
for i in range(strategy.min_window - 1, len(candles) - 1):
    signal = strategy.evaluate(candles[:i+1])
    next_open = candles[i+1].open  # ← next bar open
    # entry/exit at next_open
# Last bar signal not executed (next bar 없음, 실제 운영 정합)
```

## 재검증 결과 (HYPO-003 SMA(50, 200) BTC 1d)

### Before (look-ahead bias 있음)
- 8 trades, hit 62.5%, exp +47.69%, Sharpe +0.475

### After (next bar open 체결)
- 8 trades, hit 62.5%, **exp +47.69%, Sharpe +0.475** ← 동일

### Walk-forward 재검증
| Set | trades | hit | expectancy | Sharpe |
|---|---|---|---|---|
| TRAIN (5년) | 4 | 50% | +74.6% | +0.524 |
| TEST (3년) | 3 | 67% | +23.4% | +0.525 |

→ 동일.

## Why HYPO-003 영향 미미

1d candle은 daily open/close 차이 작음 (gap 작음, 큰 trend는 며칠+). SMA crossover 신호 → 다음 day open 체결도 같은 trend 안. 즉 1d trend following은 look-ahead bias 영향 거의 없음.

→ HYPO-001/002 (1h scalp) 영향은 아직 재검증 X (모두 fast-fail이라 결과 동일 예상).

## 추가 Codex Fix

### Position.close — already CLOSED 차단
```python
def close(self, exit_price, close_ts_ms):
    if not self.is_open:
        raise ValueError(f"already closed (status={self.status.value})")
    ...
```

### Daily loss limit (TODO)
ADR-010 일일 손실 5% 제한 — runner.py 미구현. 향후 plan.

## Codex 합의 % (Round 1)

**78%** — 22% gap:
- Look-ahead bias (FAIL → fix 완료)
- Position closed invariant (WARN → fix 완료)
- Daily loss limit 미구현 (WARN → TODO)
- 4h timeframe 자동 분류 단순화 (WARN → TODO)
- vault paper log P1 narrative 충돌 (WARN → 정합 검토 필요)

→ Fix 후 합의 % 재평가는 Round 2에서.

## Polaris 영향

### HYPO-003 신뢰도 향상
- Look-ahead bias 없는 결과로 +47% 재확인
- Walk-forward 양수 일관 robust
- → **Polaris 첫 viable strategy 확정 (재확인)**

### Backtest engine 신뢰도
- INSIGHT-012 (백테스트 신뢰도 한계) + look-ahead fix → 한계 인식 + 정확도 향상

## Recommendation
- [x] engine.py fix 완료
- [x] Position.close fix 완료
- [x] HYPO-003 재backtest 완료
- [ ] daily loss limit 구현 (Phase 2c 후속)
- [ ] codex Round 2 — fix 검증 (87 tests pass 후)

## Related
- ADR-004 (코드 리뷰 codex 외부 의무)
- ADR-010 (Backtest + Paper parallel)
- INSIGHT-012 (백테스트 신뢰도 한계)
- INSIGHT-016 (HYPO-003 walk-forward robustness)
- HYPO-003 (active)
