---
name: signaling-strategies
description: Use to invoke per-strategy raw-signal generation across the active strategies registered in STRATEGY_REGISTRY (polaris/strategies/__init__.py — currently 11 across OKX spot, Capital CFD, and Alpaca equity tracks). Each strategy emits RawSignal | None given a market_view; lifecycle decisions (entry/exit/swap) are owned by the AI gate pipeline, not the strategy.
---

# signaling-strategies (P0 skill)

## When to use
- Gate orchestrator tick (G1 focus watchlist 이후 G2 입력 생성)
- Smoke test (single strategy / single ticker)

## SSOT 포인터 (전략 수·ID 하드코딩 금지 — 여기서 읽는다)
- **등록 전략 = `polaris/strategies/__init__.py` `STRATEGY_REGISTRY`** —
  전략 수 / strategy_id / correlation_group 은 항상 레지스트리에서 확인
- 현재 구성 (2026-06-11 검증): Track A OKX spot 4 + Track B Capital CFD 4
  (fx_range_fade 포함) + Track C Alpaca equity 3 = 11종
- 전략별 트리거 파라미터: 각 `polaris/strategies/<strategy_id>.py` docstring
- 계약: `BaseStrategy.generate_raw_signal(market_view) -> RawSignal | None`
  (`polaris/strategies/base.py`)

## Process
1. focus watchlist × 전략 cross-product 각 `(strategy, symbol)`:
   - warmup_bars 충족 확인
   - correlation_group 동시 진입 cap 확인
   - `generate_raw_signal(market_view)` 호출
2. non-None signal 수집 → G3 (signal-validator) 큐로 emit

## Outputs
- `RawSignal` list ([[ADR-008]])
- per-strategy emit count metric

## Anti-pattern
- Strategy 가 sizing 결정 (NO — G5 entry-sizer, [[ADR-005]])
- Strategy 가 exit 결정 (NO — G7 + precise-exit engine
  `polaris/core/live_recalc/exit_engine.py`)
- Strategy 가 cell matrix update (NO — G8 reflector)

## Failure handling
- Strategy 예외 → 해당 strategy_id 만 HALT (Layer 7 isolation), 나머지 continue
- Stale market data → signal drop

## Cross-ref
- [[ADR-008]] strategy = signal generator only
- [[ADR-003]] Layer 7 isolation
- skill: gating-pipeline (G2 → G3 다음 단계)
