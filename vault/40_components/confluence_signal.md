---
entity_type: component
entity_id: confluence_signal
pure: true
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[HYPO-020]]", "[[HYPO-022]]", "[[INSIGHT-031]]"]
mode: dev
reviewed_by: codex
code_path: src/strategies/confluence_signal.py
test_path: tests/strategies/test_confluence_signal.py
tags: [type/component, scope/strategy, pure/true, polaris]
---

# ConfluenceSignal — Meta-Strategy Component

> P6 pure. No I/O, deterministic. N-of-M sub-strategy combination meta-strategy.

## Purpose

단일 indicator 음수 EV 문제 해결: 여러 sub-strategy 동시 만족 시에만 ENTER_LONG.
Phase 2h 신규 구현 (2026-05-04). [[INSIGHT-031]] grid backtest에서 viable alpha 발굴.

## Interface

```python
class ConfluenceSignal(Strategy):
    def __init__(
        self,
        sub_strategies: list[Strategy],  # 1+ required
        require_all: bool = True,         # True = AND, False = N-of-M
        min_confluence: int | None = None, # N-of-M threshold (default=len(subs) if require_all)
        target_size_usd: float = 200.0,   # meta entry size (overrides sub sizes)
    ) -> None: ...

    def evaluate(self, window: list[Candle]) -> Signal: ...
```

## Logic

1. EXIT from any sub → meta EXIT (position safety first).
2. Count ENTER_LONG subs.
3. require_all=True (AND): n_long == len(subs) → ENTER_LONG.
4. require_all=False (N-of-M): n_long >= min_confluence → ENTER_LONG.
5. confidence = mean(confidence of agreeing subs), capped at 1.0.
6. target_size_usd = ConfluenceSignal's own, not sub's.
7. min_window = max(sub.min_window for sub in subs).

## Invariants (P6 Pure)

- No I/O, no state mutation.
- Deterministic: same window → same output.
- sub_strategies non-empty guard.
- min_confluence >= 1 guard.
- target_size_usd > 0 guard.

## Test Coverage (23 tests — 2026-05-04)

- require_all AND: 2/2, 1/2, 0/2, 3/3, 2/3
- N-of-M: boundary exactly min, below min, all hold
- EXIT: any sub exit → propagate, overrides LONG
- Confidence: mean of agreeing subs
- Size: confluence overrides sub size
- min_window: max of subs, enforce
- Guards: empty subs, min_confluence=0, target_size<=0
- Reason: includes N/total string
- Integration: real VolumeBurst + DonchianBreakout + SMACrossover min_window

## Discovered Viable Configs (INSIGHT-031)

| Config | Ticker | TF | IS Sharpe | IS exp | OOS exp |
|--------|--------|-----|-----------|--------|---------|
| VB(20) AND Donchain(20,10) | DOGE | 1D | 0.42 | +11.81% | +2.78% |
| VB(20) AND Donchain(20,10) | ORDI | 1D | 0.33 | +44.97%* | +55.51%* |
| 3-way N-of-M 2/3 | DOGE | 1D | 0.40 | +10.85% | +2.78% |

*ORDI: outlier-driven (+435% single trade). Low confidence.
