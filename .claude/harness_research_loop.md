# Harness Research-Improvement Loop

**Mandate** (Jin 2026-05-03 03:00 AEST): 일 **+0.5%** 수익 날 때까지 쉬지 말고
모든 paradigm / 모든 strategy / 모든 superpower (advisor + skill + sequential
thinking + debate + codex + research) 동원해 reasoning + 개선 + 시도 반복.
**모든 승인 위임** — 자율 판단 + 진행. 타임프레임 짧게. 북극성 정합 (block
보단 공격 우선).

## North Star
- **Daily PnL ≥ +0.5%** (capital base $1000 paper → $5/day)
- 7d rolling avg ≥ +0.3%
- WR ≥ 35% AND avg net_pct > 0
- 모든 strategy gross WR > 50% (random 우월)

## Loop Structure (1h cycle)

```
Loop step                    Tool / Skill
─────────────────────────────────────────────────
1. Measure                   sqlite + log forensic
   - WR / gross WR / pnl
   - by strategy / cell / tier
   - cell_matrix_spot drift
2. Diagnose                  sequential-thinking
   - bottleneck root cause
   - paradigm vs reality gap
3. Hypothesize               brainstorming / research
   - new param / strategy / structure
   - debate cross-check
4. Implement                 dev-coder + edit
   - param tune (ops-executor)
   - new strategy code
   - structure refactor
5. Validate                  codex:rescue + dev-audit
   - smoke test
   - hidden bug review
6. Apply + Restart           bash start.sh
7. Vault write               INSIGHT / ADR / log.md
─────────────────────────────────────────────────
```

## Active Levers (priority order)

### L1: Strategy edge (gross WR < 50% = false signal)
- bb_break_momentum 89% 점유 → cooldown ↑, 가중 ↓
- vol_compression / funding_decoupling 가중 ↑ (100% gross small sample)
- 새 strategy research: orderbook imbalance / taker volume surge /
  funding flip / liquidation cascade / mean reversion / VWAP deviation

### L2: Fee economic (paper Lv1 1.4% > avg gross 0.13%)
- spot_fee_round_trip 0.014 → 0.0 (paper 학습 시뮬 모드)
- 또는 OKX VIP path / Alpaca crypto (Jin 결정 영역)

### L3: Universe filter (ATR<0.1% noise)
- ATR_min 0.15% gate (50% cull, BTC/ETH/DOGE/PEPE keep)
- liquidity_tier filter (large/mid only, dead pair 차단)

### L4: Direction 양면 (long-only mean reversion 함정)
- short side strategy (false breakout 반전 진입)
- ADR-011 design

### L5: Timeframe (5m random walk)
- multi-timeframe confirmation (5m signal × 15m trend)
- 1m / 3m scalp 시도

### L6: Paradigm (scalping vs swing)
- swing 5-15% TP, 2-7d hold (마지막 carrot)

## Auto Cycle Schedule
- **ScheduleWakeup 1h** — 매 cycle measurement + 다음 변경
- **Vault write 매 cycle**: INSIGHT or ADR or log.md
- **종료 조건**: 7d rolling pnl ≥ +0.5%/day OR Jin halt

## Self-Check (매 cycle 시작)
- 북극성 위반? (block / defensive / dampen 잔재)
- 60줄 상한 (split 정합)
- 토큰 효율 (advisor 중복 X)
- vault SSOT (재발견 방지)

---

**Ref**: [[ADR-009]] [[ADR-010]] [[INSIGHT-034]] [[INSIGHT-035]] [[INSIGHT-036]]
