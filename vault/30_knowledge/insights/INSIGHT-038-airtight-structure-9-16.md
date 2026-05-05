---
entity_type: insight
entity_id: INSIGHT-038
auto: false
last_modified: 2026-05-05
expires: 2026-11-05
editable: true
back_links: ["[[INSIGHT-035]]", "[[INSIGHT-036]]", "[[INSIGHT-037]]", "[[ADR-013]]", "[[ADR-014]]", "[[_NOW]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, status/active, scope/architecture, priority/p0, polaris]
---

# INSIGHT-038 — Airtight 구조 개선 8 phase (Phase 9-16)

## Trigger
Jin: "구조개선 완벽해질때까지 계속 진행해줘. 토큰 무한대야 리셋 다가와서."

## Codex 합의 우선순위 (debate 1 round)
**A → D → C → E → B → F**: 데이터 SSOT → 전략 제어 단순화 → EV 즉시 개선 →
실행 모듈화 → 글로벌 리스크 → 라이브.

## 8 Phase 작업

| Phase | 작업 | LOC | 테스트 | 핵심 산출물 |
|-------|------|-----|--------|------------|
| **9** | Unified Trade Ledger (SQLite) | 600+ | 27 | `src/persist/{schema,ledger,migrations}.py` — 270 JSON → 1 SQL SSOT, 143 files migrated, +$12.41 verified |
| **10** | Strategy Registry pattern | 250+ | 6 | `src/paper/dispatchers.py` — `@register_dispatcher`, carry migrated as proof |
| **11** | Regime Activation Matrix | 215+ | 29 | `src/risk/regime_activation.py` — TSMOM 횡보 차단, GridBot 트렌드 차단 |
| **12** | Realtime_runner 분해 (12.1+12.2) | 250+ | 24 | `state_manager.py` (cache 추출) + `entry_gates.py` (8-gate composition P6 pure) |
| **13** | Portfolio Risk + Correlation | 490+ | 14 | `src/risk/portfolio.py` — drawdown cap 5%, Pearson 매트릭스, attribution |
| **14.1** | Live Execution Adapter | 530+ | 34 | `src/exec/{broker,paper_broker,okx_broker,kill_switch}.py` — Phase 14.1 dry-run skeleton |
| **15** | Codex P0/P1 critical fixes | 220+ | (existing) | Shadow write health counter, MTM with current_prices, snapshot writer, broker wiring |
| **16** | SQL Primary Read | 65+ | (existing) | `_load_from_ledger` first, JSON fallback. JSON-SQL drift risk 차단 |

## Codex Audit Cycles

**Round 1 (Phase 15)**: 3 critical (P0-1 ledger SSOT, P0-2 portfolio MTM, P1-3 broker wiring) — 모두 수용.
**Round 2 (Phase 15 verification)**: 1 remaining — portfolio_snapshots write 종속성 (ENTER_LONG only).
   → Phase 15 round-2 fix: tick handler에서 독립 write.
**Round 3 (예정)**: 모든 6 phase 종합 검증.

## 결과

- **941 tests pass** (시작: 833, +108 신규)
- **9 active HYPO** (8 → 9, HYPO-036 추가)
- **Live readiness 41/100 MARGINAL** (시작: 22, +19)
- **Paper PnL +$11.82** (시작: -$59, EV 양수 전환)
- **Total realized $12.41** SQL ledger 검증
- **NFI hidden alpha 확인**: n=22, win 86%, +$45.34 (SSOT 조회)

## 구조 변화

```
이전 (전략만 추가):
  realtime_runner.py 1,400 LOC god module
  if/elif primary chain (19 branches)
  270 JSON 파일 SSOT
  Per-HYPO independent balance ($450k virtual)
  Direct compute_fill_price calls
  No regime gating
  No broker abstraction
  No correlation tracking

이후 (Phase 9-16):
  src/persist/ — SQL SSOT (단일 sqlite + WAL)
  src/exec/ — Broker abstraction (paper/live unified)
  src/risk/portfolio.py — global drawdown + correlation
  src/risk/regime_activation.py — fee bleed 차단
  src/paper/dispatchers.py — registry pattern
  src/paper/state_manager.py — cache 추출
  src/paper/entry_gates.py — 8-gate P6 pure
  realtime_runner — broker.place_order routing, [PORTFOLIO-SNAP] log
  9 active HYPO (HYPO-036 FundingCarry 추가)
```

## Live transition path (Phase 14.2 — 추후 Jin 권한)

```bash
export POLARIS_LIVE_MODE=1
export OKX_API_KEY=...
export OKX_API_SECRET=...
export OKX_API_PASSPHRASE=...
# OKXBroker.place_order REST 구현
# rt.set_broker(OKXBroker(max_size_usd=100))   # 작은 size 시작
```

기존 `_eval_and_act`는 변경 0 — 실제로 broker singleton 만 swap.

## Remaining gaps

1. **Phase 12.3+**: 17 dispatchers 여전히 if/elif 체인 (carry만 migrated)
2. **Phase 14.2**: OKX REST 실제 구현 (Jin 권한 필요)
3. **Phase 9.3**: JSON write deprecate (충분 SQL-only 운영 후)
4. **Multi-process safety**: cron + realtime + daily 동시 실행 시 SQL 락 검증
5. **Dashboard real-time SQL feed**: 현재 file polling — SQL primary 활용 가능
