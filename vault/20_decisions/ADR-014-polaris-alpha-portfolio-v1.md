---
entity_type: adr
entity_id: ADR-014
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[ADR-011]]", "[[ADR-012]]", "[[ADR-013]]", "[[INSIGHT-030]]", "[[INSIGHT-015]]"]
mode: dev
reviewed_by: codex
tags: [type/adr, status/accepted, scope/alpha, priority/p1, polaris]
---

# ADR-014 — Polaris Alpha Portfolio v1 — Backtest 검증 viable strategies only

## Context

Phase 2g (Round 4~15)에서 tick-driven scalp 7개 HYPO를 실측 검증. 결과:
- tick-driven scalp 5개 (HYPO-010/013/014/016/017): EV 음수 또는 sample 부족 또는 trigger 빈도 0
- 1d trend 2개 (HYPO-003/004): backtest Sharpe viable, daily cron 정상 운영 중
- scalp 유지 2개 (HYPO-007/008): RSI15m cron-style + VolumeBurst 양수 EV

## Decision

**"backtest 검증 통과 + 실측 양수 EV" 전략만 portfolio에 유지.**

### Realtime Runner (tick-driven)

```
REALTIME_HYPOS = [HYPO-007-RT, HYPO-008-RT]  # 2개만
```

### Cron (1d trend)

```
ACTIVE_HYPOS = [
    HYPO-003-SMA50-200  (8 ticker: BTC/ETH/SOL/DOGE/ADA/XRP/ORDI/SUI),
    HYPO-004-DONCH-40-15 (BTC+ETH),
    HYPO-004-DONCH-20-10 (BTC+ETH+SOL),
]
```

## Rationale

1. **tick noise vs trend signal**: tick-driven scalp은 fee 0.0014 대비 alpha 생성 불가 확인. 1d candle = fee drag 최소화 + mean-reverting noise 제거.
2. **8 ticker basket**: 단일 ticker 집중 위험 분산. SMA 50/200 1D는 backtest 기간 (5~10년) 내 모든 8 ticker에서 Sharpe > 0 확인 (INSIGHT-015).
3. **Donchian 2 variants**: entry_period 다른 두 변형으로 breakout 시점 분산 (40일 vs 20일 high). exit_period도 비례 조정 (15일 vs 10일).
4. **scalp 2개 유지**: HYPO-007 RSI15m은 rare-trigger cron-style (day 1~2 오작동 없음), HYPO-008 VolumeBurst는 n=29 win 55% +$3.50 양수 EV 유일.

## Consequences

- realtime_runner 구독 ticker 감소: 9 OKX + 4 Binance → 8 OKX (HYPO-007/008 union)
- Binance WS 구독 0 (HYPO-014 deprecated) → `_binance_subscribe_tickers()` 빈 list 반환
- cron execution: 14 cycle/day (8 SMA + 2 Donch-40/15 + 3 Donch-20/10 → 총 13 ticker-strategy pair, 1 cron run)
- Paper log 갱신: HYPO-010/013/014/016/017 log 파일 유지 (historical record)

## Status

Accepted — 2026-05-04 (Jin 판단, Round 15)

## Linked

- [[INSIGHT-030]] tick-driven scalp 비활성 데이터 요약
- [[INSIGHT-015]] SMA 50/200 1D SPOT viable 근거
- [[ADR-011]] Promotion Gate timeframe-aware
- [[ADR-012]] Realtime WebSocket architecture
