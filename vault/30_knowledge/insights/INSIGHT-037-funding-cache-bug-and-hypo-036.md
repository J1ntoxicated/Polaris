---
entity_type: insight
entity_id: INSIGHT-037
auto: false
last_modified: 2026-05-05
expires: 2026-08-05
editable: true
back_links: ["[[INSIGHT-035]]", "[[INSIGHT-036]]", "[[ADR-014]]", "[[_NOW]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, status/active, scope/strategy, priority/p1, polaris]
---

# INSIGHT-037 — Funding cache latent bug + HYPO-036 Carry 활성화

## Latent bug — `_funding_rate_cache` write 부재 (Phase 8 fix)

**증상**: HYPO-027 FundingFilter가 항상 `funding=None` (실제론 0.0 fallback) 본 상태로
운영. AI advisor도 동일하게 `funding_8h=0.0` 으로 평가.

**근본 원인** (`src/paper/realtime_runner.py:111`): `_funding_rate_cache` 모듈 변수
선언 + 두 곳 read (line 704 AI, line 869 filter). **Write site 없음.**
즉, 캐시는 영원히 빈 dict.

**Fix**: 60s 주기 background task `_poll_funding_rates(symbols)` 추가.
Codex round-1 critical: `requests.get` × N × 8s timeout이 async task
내부에서 sync 실행되면 event loop block (최악 24s 정지). →
`loop.run_in_executor(None, fetch_funding_rates_bulk, ...)` 로 thread offload.

**검증**: live boot log
```
[FUNDING-POLL] populated 3/3 symbols sample={
  'BTCUSDT': -2.901e-05, 'ETHUSDT': -4.4e-05, 'SOLUSDT': 0.0001
}
```

## HYPO-036 — Funding Carry 활성화 (Codex priority B)

**Codex debate** (1 round 합의): A/B/C/D/E 5 후보 중 **B** 선정.
근거: 즉시성 (코드+테스트 존재) + 알파 다양성 (funding-derivative 기반 새 축) +
빈도/EV 균형 (3 메이저 ticker, 12h max hold).

**스펙**:
- Liu & Yu (2024) "Funding Rates and Cryptocurrency Returns" — funding ≤ -0.05% (8h)
  precedes SPOT rally (~70% hit rate empirical, CoinGlass)
- threshold -0.0005 (decimal), exit on funding ≥ 0 또는 12h max
- 3 ticker: BTC/ETH/SOL (주요 perp 시장)

**`primary_tf="carry"`** branch in `_eval_and_act`:
1. `funding = _funding_rate_cache.get(binance_sym)` (None 시 HOLD)
2. `bal_check.open_positions` 에서 ticker 매칭 → in_position + age_hours
3. `strategy.evaluate_funding(funding_8h, ts_ms, age_h, in_position)` 호출

## 안전성 (Codex round-1 verified)

- `funding=None` → strategy HOLD (P6 pure 분기 안전)
- supervisor restart 시 funding task `_run_okx_and_binance` 안에서 매 loop 재생성
- single-loop dict read/write (race 없음)
- `(ticker, strategy.name)` 단일 hypo이므로 collision 없음

## 결과

- 9 active HYPO (007/008/023/027/028/032/036/040/NFI-001)
- HYPO-027 latent bug 해결 → 진짜 funding-aware
- HYPO-036 wakeful (대기 중, 정상 시장이라 -0.05% threshold 미달)
- 812 tests pass

## Next (다음 priority — Codex 합의 진단)

- C (HYPO-023 LiqCascade fix): network handshake timeout intermittent — code 아닌 인프라 이슈
- A (CSmom HYPO-035): 이미 daily_paper_runner 운영 중 (cron-style 1D)
- D (NFI 2-of-5): Codex 경고 — 86% WR 희석 위험. 미적용
- E (Maker-only Layer 4): live 전환 시점만 효과
