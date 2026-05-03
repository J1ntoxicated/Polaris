---
entity_type: insight
entity_id: INSIGHT-018
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[ADR-012]]", "[[INSIGHT-007]]", "[[60_alpha/_README]]", "[[INSIGHT-022]]"]
mode: alpha
reviewed_by: codex
maturity: verified
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-018 — Realtime Tick-driven 발견 (cron 한계)

## Evidence (직접 측정)

### Cron-based 5h 운영 (HYPO-007/008/009 intraday cron 15min)
- 20+ cycles, 0 trades opened
- 모든 ticker RSI 50-72 (overbought) — mean reversion entry 조건 안 맞음
- candle close wait → next bar open entry = inactive

### Realtime tick-driven 1h 운영
- WebSocket OKX SPOT (tickers/books5/trades) public 3 channels
- 10 ticker subscribed
- **즉시 3 entry 발생**: SOL VolumeBurst, DOGE OrderBookImbalance, DOGE TradeFlow

## Root Cause

Cron-based의 한계:
1. Candle close 기다림 → entry 발생 시점 latency 1-15분
2. Indicator 변화 시점에만 평가 → 매 candle 변화 X (HOLD signal 지속)
3. TP/SL hit 시점 candle close까지 wait → drawdown 누적

Tick-driven 우월:
1. Tick payload (24h change/high/low) = 추가 indicator (candle 무관)
2. Order book / Trade flow = 직접 supply/demand 신호
3. Tick price = 즉시 TP/SL 체크

## OKX WebSocket Public Channels 한계 발견

- `tickers` ✅ (last/bid/ask/24h)
- `books5` ✅ (top 5 levels depth)
- `trades` ✅ (체결 stream)
- `candle{period}` ❌ → **business endpoint** (별도 connection 필요, public X)
  - error: 60018 "Wrong URL or channel:candle1m doesn't exist"
  - 해결: REST `/api/v5/market/history-candles` 60s poll로 indicator cache

## 활용 가능 추가 지표 (Brainstorm 결과)

### Tick payload (현재 활용)
- last/bid/ask/spread (유동성)
- 24h open/high/low/change/volume
- bidSz/askSz/lastSz

### Order book (books5 — 현재 활용)
- bid/ask top 5 levels depth
- imbalance = bid_sum / total
- wall detection (특정 price level 큰 order)

### Trade flow (trades — 현재 활용)
- Taker buy vs sell ratio (sentiment)
- Trade frequency
- Average trade size (whale vs retail)

### Cross-exchange (Binance integration — 부분 구현)
- Binance REST klines (cross-validation candle, 더 큰 데이터셋)
- Binance ticker (price difference vs OKX)
- Binance WebSocket (TODO)

### 미활용 (후속)
- BTC dominance / ETH-BTC ratio (cross-asset)
- Macro (Fear/Greed alternative.me, DXY/VIX)
- On-chain (premium API 필요)

## Polaris 적용

### ADR-012 채택
- 신규 long-running realtime runner
- 6 신규 strategies (HYPO-007 ~ HYPO-012)
- launchd KeepAlive=true

### 첫 trade entries (vault 기록)
| HYPO | Strategy | Ticker | Entry | Size | TS |
|---|---|---|---|---|---|
| HYPO-008-RT | VolumeBurst | SOL-USDT | $84.37 | $250 | 2026-05-04 07:34 |
| HYPO-011-BOOK | OrderBookImbalance | DOGE-USDT | $0.10851 | $200 | 2026-05-04 07:34 |
| HYPO-012-FLOW | TradeFlow | DOGE-USDT | $0.10851 | $200 | 2026-05-04 07:35 |
| HYPO-008 (legacy) | VolumeBurst | SUI-USDT | $0.9261 | $250 | 2026-05-04 (어제) |

## Recommendation
- [ ] ADR-012 codex 외부 리뷰 (Round 1)
- [ ] HYPO-007/008/009/010/011/012 active 노트 작성 (현재 작성 중)
- [ ] Binance WebSocket 추가 (cross-exchange leading signal)
- [ ] 24h 운영 후 trade 빈도 + 수익 평가
- [ ] 운영 모델 정합 (4 모드 명시 + vault-first cycle 의무화)

## Related
- ADR-012 (Realtime architecture shift)
- INSIGHT-007 (fee 함정 — tick-driven은 우회 가능)
- INSIGHT-014 (BB fast-fail — candle 한계)
- [[INSIGHT-022]] (Binance WS spec + 즉시 구현 — "Binance WebSocket TODO" 이행)
- 60_alpha/_README
