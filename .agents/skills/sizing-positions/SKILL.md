---
name: sizing-positions
description: Use to compute final notional for a validated trading signal via the T4 formula (base x continuous_scalar(0.75-1.5x) x tier_amplifier(1.5/2/3x) x cell_routing_mult), apply hard caps (per-symbol, per-strategy, per-track, total), enforce symbol-cluster caps and Cold Start CS-3 (n<20 Kelly off, single 6%/7%), and emit a sized order intent.
---

# sizing-positions (P0 skill)

## When to use
- Gate 5 (entry-sizer) invocation
- Manual sizing audit (Jin debug)

## Inputs
- validated_signal (from gate 4)
- portfolio_state (positions + cash + risk fill-rate)
- cell_matrix score (gate 5 input)
- ticker_baseline (5-metric)
- streak_state (per-strategy win/loss streak)

## Formula ([[ADR-005]])

```
notional = base × continuous_scalar(0.75-1.5×)
                × tier_amplifier(1.0/1.5/2.0/3.0×)
                × cell_routing_mult(0.5/1.0/1.3)
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```

## Hard caps (DEMO aggressive)

| Cap | Value |
|---|---|
| per-symbol spot (OKX) | 50% |
| per-symbol CFD (Capital) | 35% |
| Track A gross | 60% |
| Track B gross | 80% |
| Track A daily venue risk | 8% |
| Track B daily venue risk | 9% |
| Total daily risk | 10% |
| single-trade default | 8% |
| single-trade amplifier on | 9% |
| Kelly k | 0.50 |

## Cold Start CS-3
- `n < 20` per strategy: Kelly off, single 6% / amplifier 7%
- `n >= 20`: Kelly on, single 8% / amplifier 9%

## Symbol-cluster cap (allocator pre-cut)
- BTC/ETH cluster (spot): 40%
- XAU+indices cluster (CFD): 50%
- FX majors cluster (CFD): 60%

## Risk-budget fill-rate cut
- venue daily fill-rate ≥ 70% → weakest signal_strength 즉시 컷

## Tier amplifier trigger gate
- `n < 8` → amp off
- `n=8-9 AND hit-rate ≥75%` → max 1.5×
- `n>=10 AND hit-rate ≥70%` → full tier (1.5/2/3×)
- 1 loss → R1 reset (binary)

## Outputs
- sized_order: {notional, entry_type, slippage_tier, reason}
- event: `order_intent_created` (SQLite events)

## Failure handling
- Sonnet timeout → Python deterministic (formula 그대로)
- Hard cap violation in AI output → clip + log + alert
- Cluster cap exhausted → reject signal + log

## Cross-ref
- [[ADR-005]] full formula + caps
- [[ADR-006]] cell routing mult
- agent: entry-sizer (gate 5)
- skill `governing-risk` (parallel enforcement)
