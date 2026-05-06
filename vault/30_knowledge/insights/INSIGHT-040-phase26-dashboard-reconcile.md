---
entity_type: insight
entity_id: INSIGHT-040
auto: false
last_modified: 2026-05-06
expires: 2026-11-06
editable: true
back_links: ["[[INSIGHT-039]]", "[[ADR-013]]", "[[_NOW]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, status/active, scope/dashboard, priority/p1, polaris]
---

# INSIGHT-040 — Phase 26: Dashboard reconcile + strict 220×55 layout 완성

> Phase 26 에서 broker reconcile 5min cadence + startup_sync 도입, 8-section 220×55 fixed-size dashboard 완성, aggressive sizing 파라미터 확정. Phase 27 multi-factor signal scoring 포팅 계획 codex debate 합의 도출.

## Context

Phase 20~25 에서 real trading bot 아키텍처(PortfolioManager + Contribution + PositionManager)가 완성됐으나 두 가지 결함이 잔존했다.

1. **Broker reconcile 누락**: OKXBroker 실제 포지션과 Polaris 내부 `AggregatedPosition` 간 drift 감지 로직 없음. Live 전환 시 포지션 불일치 → silent P&L 오류 위험.
2. **Dashboard 크기 불일치**: 2-window 레이아웃(operations.py LEFT + intel.py RIGHT)이 terminal 크기에 따라 흐트러짐. 220×55 고정 spec 필요.

Phase 26 에서 두 문제 모두 해결됐다.

## Evidence

### Dashboard 8-section 220×55 spec

```
┌─ HEADER ──────────────────────── 220 cols ─┐  row 0~1
│ Open Positions                              │  row 2~14
│ Auto Manager │ Strategy Performance         │  row 15~28  (side-by-side)
│ Closed Trades                               │  row 29~40
│ Live Log                                    │  row 41~51
│ FOOTER                                      │  row 52~54
└─────────────────────────────────────────────┘  total 55 rows
```

| Section | 담당 | 내용 |
|---------|------|------|
| Header | `sections/header.py` | 타임스탬프, phase, equity, regime |
| Open Positions | `sections/open_positions.py` | ticker·strategy·pnl·duration |
| Auto Manager | `sections/auto_manager.py` | HYPO 활성 수, deprecate 큐 |
| Strategy Performance | `sections/strategy_perf.py` | HYPO별 win%·EV·n_trades |
| Closed Trades | `sections/closed_trades.py` | 최근 20개 closes, realized PnL |
| Live Log | `sections/live_log.py` | tail 10 lines, color-coded level |
| Footer | `sections/footer.py` | latency, broker status, lint tag |

W=220 H=55 는 env override 가능: `POLARIS_DASH_W` / `POLARIS_DASH_H`.

### Reconcile 5min cadence

```
startup_sync()               # 봇 시작 시 1회 — broker positions 끌어오기
reconcile_loop()             # 5min cadence (300s sleep)
  broker.get_positions()     # OKXBroker REST /api/v5/account/positions
  diff(internal, broker)     # size_delta + pnl_delta
  if abs(size_delta) > 0.001 or abs(pnl_delta) > 1.0:
      log_event("RECONCILE-DRIFT", ...)
      internal.sync(broker)  # broker를 SSOT로 덮어씀
```

startup_sync: 봇 재시작 시 OKX demo/live 잔여 포지션을 Polaris `AggregatedPosition`으로 자동 임포트. Cold start 시 내부 상태 0이더라도 broker에 포지션이 있으면 복구됨.

### Aggressive sizing 확정

| 파라미터 | 값 | 근거 |
|----------|----|------|
| `POLARIS_PORTFOLIO_USD` | 50,000 | 목표 포트폴리오 규모 |
| `POLARIS_MAX_PER_TICKER_USD` | 10,000 | 단일 ticker 20% cap |
| `POLARIS_BROKER_MAX_USD` | 2,000 | 단일 주문 최대 (OKX Lv1 제약 고려) |
| `cold_start_cap` | 0 (off) | n_trades=0 cap 폐지 — warm signal 우선 |

`cold_start_cap=0` 은 Phase 2N+ 에서 도입된 cold start 보수 캡($300)을 제거. 이제 warm signal이 있으면 full sizing. 신규 전략은 auto_deprecate min_n=20 gate 가 보호.

## Root Cause

- Reconcile 누락: Phase 20 PortfolioManager 설계 시 paper-only 가정 → broker drift 감지 범위 외.
- Dashboard 크기 불일치: `intel.py` 작성 시 terminal W/H을 `os.get_terminal_size()` 로 읽었고, CI/launchd 환경에서 기본값(80×24) 적용 → 레이아웃 붕괴.

## Impact

- 직접: `src/dashboard/intel.py` + `src/dashboard/sections/` 8개 파일, `src/risk/portfolio_manager.py` reconcile loop 추가.
- 간접: startup_sync → Live 전환 시 포지션 복구 경로 확보. 220×55 fix → LG 모니터 2-window 고정 레이아웃.

## Recommendation

- [x] Phase 26.1 — 8-section 220×55 dashboard 구현 (완료)
- [x] Phase 26.2 — reconcile 5min loop + startup_sync (완료)
- [x] Phase 26.3 — aggressive sizing 파라미터 적용 + cold_start_cap=0 (완료)
- [ ] Phase 27 — multi-factor signal scoring 포팅 (INSIGHT-041 참조)
- [ ] Live 전환 시 startup_sync 로그 검증 (`[STARTUP-SYNC]` grep)

## Related

- 선행 architecture: [[INSIGHT-039]] (Phase 20 real trading bot 구조)
- 하네스 모드: [[ADR-013]] (HARNESS Meta Mode)
- 현재 상태: [[_NOW]]
