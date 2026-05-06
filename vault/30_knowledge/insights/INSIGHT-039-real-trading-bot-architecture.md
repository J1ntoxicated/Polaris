---
entity_type: insight
entity_id: INSIGHT-039
auto: false
last_modified: 2026-05-06
expires: 2026-11-06
editable: true
back_links: ["[[INSIGHT-038]]", "[[ADR-013]]", "[[ADR-014]]", "[[_NOW]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, status/active, scope/architecture, priority/p0, polaris]
---

# INSIGHT-039 — Phase 20: Real trading bot architecture (testing rig 졸업)

## Trigger
Jin: "전략 테스팅기냐고..." — Polaris가 9 strategy × 30 ticker = 270 독립
paper 봇 모드로 운영되어 진짜 trading bot이 아니라 strategy testing rig.

## 진단

**현재 (Phase 20 이전)**:
- realtime_runner: 9 strategies × 30 ticker → 270 PaperBalance state file
- 같은 BTC를 9 봇이 각자 cash $5k-$50k로 사고팔고 비교
- Strategy class에 entry signal + exit signal + position holding 다 박힘
- Live OKX = 1 ticker 1 position이므로 attribution 불가능
- Multi-exit (TP + trailing + time) per position 불가능

## Architecture (Phase 20)

```
SignalGenerator (Strategy.evaluate)
  ↓ Signal (의견만, 포지션 모름)
  
PortfolioManager (single account)
  ↓ contribution 추가 per ticker
  
AggregatedPosition[ticker]
  contributions = [
    Contribution(strategy="vb", size=100, exits=[TP(0.6%), SL(0.35%), MaxHold(4h), SigRev]),
    Contribution(strategy="grid", size=200, exits=[TP(0.8%), SL(0.5%), MaxHold(4h)]),
  ]

PositionManager (per tick)
  ↓ contribution × exit_strategies 평가
  ↓ first fire → partial close (그 slice base_qty 만큼만)

ExecutionEngine (Broker abstraction)
  ↓ PaperBroker / OKXBroker
```

## 7 Sub-phases (Phase 20.1 ~ 20.6)

| Phase | 작업 | 파일 | 테스트 |
|-------|------|------|-------|
| 20.1 | ExitStrategy framework | `src/exec/exit_strategies.py` | 29 |
| 20.2 | Contribution + AggregatedPosition | `src/risk/portfolio_state.py` | 20 |
| 20.3 | PortfolioManager (single account) | `src/risk/portfolio_manager.py` | 17 |
| 20.4 | PositionManager (real-time monitor) | `src/risk/position_manager.py` | 13 |
| 20.5 | realtime_runner restructure | `src/paper/realtime_runner.py` | 38 (existing) |
| 20.6 | Migration + backtest preservation | (vault docs) | — |

**총 84 신규 tests, 1027 tests pass (이전 941에서 +86).**

## 6 Composable Exit Strategies

```python
TakeProfit(pct)              # +pct → close 100%
StopLoss(pct)                # -pct → close 100%
TrailingStop(activation, trail)  # peak - trail → close 100%
TimeBasedHold(max_hours)     # held > max → close 100%
SignalReversal(strat, min_hold_ms=90s)  # signal=EXIT after min_hold → close 100%
PartialTP(levels=((pct, frac),...))  # staged 청산
```

각 contribution에 list[ExitStrategy] attached. PositionManager 매 tick 평가, first-fire wins.

## Multi-strategy on same ticker — 검증된 시나리오

```
BTC-USDT 가격 $80000:
  vb (HYPO-008, scalp profile) ENTER → contribution#1: $100 + [TP(0.6%), SL(0.35%), MaxHold(4h), SigRev]
  nfi (HYPO-NFI-001, swing profile) ENTER → contribution#2: $100 + [TP(5%), SL(2%), MaxHold(7d), SigRev]

가격 $80800 (+1%):
  vb's TP(0.6%) fires → contribution#1 closed (+$0.8 net)
  nfi's TP(5%) HOLD → contribution#2 still open

가격 $84000 (+5%):
  nfi's TP fires → contribution#2 closed (+$2.8 net for swing-sized profile fees)
```

각 strategy independent attribution. 동일 ticker 거래소 1 position이지만 Polaris는 contribution 단위 PnL 정확 추적.

## Backward compat 보존

- `_sync_legacy_state`: portfolio 상태를 (ticker, strategy) PaperBalance JSON으로 mirror
- 기존 dashboard / daily_paper_runner / cron 그대로 작동
- SQL ledger도 contribution → positions 매핑 (hypo_id, strategy_name 그대로)
- `src/backtest/` 영향 0 (강력하게 분리됨, per-strategy 비교 검증용 그대로)

## Live transition path

```bash
export POLARIS_LIVE_MODE=1
export OKX_API_KEY=...                      # Phase 14.2 done
export OKX_API_SECRET=...
export OKX_API_PASSPHRASE=...
export POLARIS_OKX_DEMO=1                   # demo 권장
export POLARIS_LIVE_MAX_USD=100             # 작게 시작
export POLARIS_PORTFOLIO_USD=5000           # 시작 cash
export POLARIS_MAX_PER_TICKER_USD=1500      # 티커 cap
launchctl kickstart -k gui/$UID/com.polaris.paper.realtime
```

OKXBroker auto-armed. Entries flow same path. Multi-strategy contributions
share single OKX account position. Each strategy's exit fires independently
via PositionManager — partial sell at exchange.

## 결과 검증

```
[PORTFOLIO] initialized cash=$5000 per_ticker_cap=$1500
[PORTFOLIO-SNAP] equity=$5000 cash=$5000 open=0 dd=0.00% hwm=$5000 realized=$+0.00
[BROKER] OKXBroker armed mode=DEMO max_size=$100
```

Polaris는 이제 strategy testing rig가 아니라 진짜 multi-strategy
single-account trading bot.
