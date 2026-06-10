---
type: charter
status: active
date_created: 2026-05-06
tags: [charter, vision, active-autonomous]
related: [[north-star]], [[aggressive-bias]], [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]]
---

# Active Autonomous Evolution Vision

Polaris 진짜 vision (Jin clarification 2026-05-06 21:30 + T11 archive 컨셉 통합).

## 8 핵심 컨셉

### 1. Dynamic Ticker Universe (Layer 0)
하드코드 ticker 거부. 거래 가능한 모든 ticker 동적 발견 + 동적 watchlist.
- OKX SPOT: `GET /market/tickers?instType=SPOT` → 510 instruments → 24h vol > $30M filter (learner-tunable) → ~100-150 active
- Capital CFD: `marketnavigation` tree → 5 카테고리 (forex/indices/commodity/crypto CFD/shares) → ~170 P0 universe
- Total ~270-320 instruments active (이전 정적 50 → 6× 확장)
- Refresh: OKX 5min, Capital 10min

### 2. Per-Gate AI Agent Supervisory (Layer 2)
Signal lifecycle 의 각 단계 마다 AI agent supervisory:
```
[Universe Scanner — Haiku] → [Strategy Signal Gen — Python] → 
[Signal Validator — Haiku] → [Pre-Entry Watcher — Haiku 30s] → 
[Entry Sizer — Sonnet] → [Position Monitor — Sonnet] → 
[Adaptive Exit — Sonnet] → [Post-Trade Reflector — Sonnet]
```
각 gate = LLM 결정 (PASS/KILL/MODIFY/HOLD/SWAP).
LangGraph-style state machine. Cost ~$6/day.

### 3. Cell Matrix 8-dim (Layer 4)
exchange × group × session × regime × strategy × direction × ticker × liquidity_tier
- 각 cell stat: n_trades, win_rate, avg_pnl, score = avg_pnl × √n / 70 (T11 공식)
- Routing: top quartile AMPLIFY ×1.3 / bottom quartile SUPPRESS ×0.5 / new (n<5) ×1.0
- P0 = 4-dim 압축 (exchange × strategy × ticker × regime), P1 = 8-dim full

### 4. Learner Network 7 (Layer 5)
Hourly auto-tune. T11 6 + 1 AI feedback:
1. session_mult / 2. regime_mult / 3. max_hold / 4. profit_target / 5. trail_mult / 6. bep_activate / 7. AI feedback
- adaptive_learner_attack 원칙: 관대 default, 일시 차단 (1h auto-unblock), specific triple, toggle

### 5. Live Recalc + Self-Correction (Layer 6)
- Per-tick: position exit_params 재계산 (Universal 3-layer formula)
- Regime flip: 활성 position size/exit 자동 조정
- Mid-trade strategy swap: AI Position Monitor 가 더 적합한 strategy 발견 시 swap

### 6. Strategy 역할 재정의
- 이전 (정적 lifecycle): `should_enter / compute_size / update_position / should_exit`
- 신: `generate_raw_signal(market_view) → RawSignal | None` 만
- Lifecycle 결정 (entry/exit/swap) = AI gate

### 7. Adaptive Exit AI
Default ATR×N exit floor only. AI 가 더 좋은 exit 발견 시 변경 (winner 길게).
- AI exit 더 멀면 채택 (aggressive)
- 더 가까우면 reject (default 보호)

### 8. Universal normalize() API (T11)
5 metric: ATR / size / signal / volume / pnl_std
- `normalize(ticker, metric, raw)` — percentile / z-score
- Cross-ticker comparable space

## Anti-pattern (재발 방지)
- 정적 ticker (top 50 hardcode) → Layer 0
- 정적 strategy lifecycle 4-method → signal generator only + AI gate
- 정적 ATR exit → Adaptive Exit AI
- Fixed agent count 5 (책임 분담만) → 13 agents (7 per-gate + 6 dev/ops)
- Learner / cell matrix / live recalc 누락 → Layer 4/5/6
- "portfolio management" framing → "active autonomous evolution"

## Cross-ref
- [[north-star]] / [[aggressive-bias]]
- [[ADR-003-8-layer-architecture|ADR-003]] 8-Layer Architecture / [[ADR-004-per-gate-ai-pipeline|ADR-004]] Per-Gate AI / [[ADR-006-cell-matrix|ADR-006]] Cell Matrix / [[ADR-007-learner-network|ADR-007]] Learner
- T11 archive: `~/.claude/archive/polaris_memory_pre_v2_2026-05-06/handoff_unified_2026_04_21_T11_northstar_dynamic.md`
- R4 리서치: LangGraph / TradingAgents / wen82fastik analyst→risk→executor 패턴
- Memory: `feedback_active_autonomous_vision.md`
