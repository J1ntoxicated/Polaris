---
entity_type: insight
entity_id: INSIGHT-019
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[ADR-012]]", "[[ADR-013]]", "[[ADR-004]]", "[[INSIGHT-018]]"]
mode: debate
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex DEBATE Round 3 — Phase 2c~e 통합 코드 + 운영 모델 v2 검증
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-019 — Codex Round 3 Phase 2c~e 통합 리뷰 (4 CRITICAL fix)

## Codex 합의 % (Round 3)
- Phase 2c~e 코드 + 배포: **82%** (FAIL — state race + fee 혼합)
- 운영 모델 v2 ADR-013: **74%** (WARN — enforce 미완)

## 4 CRITICAL Fix (즉시 적용)

### Fix 1: Intraday plist UNLOAD ✅
- 문제: ADR-012가 intraday "폐기" 선언 vs 실제 launchd plist 살아있음
- 결과: intraday cron + realtime runner가 같은 state file 공유
- 충돌: `paper_state_{ticker}_{strategy}.json` lost update 가능 (atomic rename으로 부분 손상은 막지만 동시 read-modify-write 보호 X)
- Fix: `launchctl unload com.polaris.paper.intraday.plist` + plist 파일 제거

### Fix 2: Fee migration (legacy 0.014 → 0.0014) ✅
- 문제: 새 entry 0.0014 (LV3), legacy/missing 0.014 (paper Lv1) 혼합
- Mathematical 위험: TP +0.6% / SL -0.35% 가정
  - fee 0.0014 → net TP +0.46%, net SL -0.49% (51.6% 승률 필요)
  - fee 0.014 → net TP -0.8%, net SL -1.75% (구조적 음수)
- Fix: legacy state file scan + fee_round_trip 통일 (이미 모두 0.0014였음)

### Fix 3: TickMomentum 24h guard ✅
- 문제: `high24h=0` 시 `last > high24 × 0.99` 항상 참 → false positive long
- 영향: 신규 listing 또는 데이터 부족 ticker에서 wrong entry
- Fix: `if last <= 0 or open24 <= 0 or high24 <= 0 or low24 <= 0: HOLD`

### Fix 4: Reconnect stale microstructure clear ✅
- 문제: WebSocket reconnect 시 _book_store / _trade_store 그대로 유지
- 영향: 이전 세션 stale order book + trade flow → 잘못된 신호
- Fix: reconnect 시 `_book_store.clear() + _trade_store.clear()` + exponential backoff (5s → 60s cap)

## 추가 발견 (low priority)

### Dead code: Binance + MTAConfluence
- `src/data/binance_history.py` REST 함수 정의됐지만 호출처 없음
- `src/strategies/mta_confluence.py` import만 됐고 ACTIVE_HYPOS 미등록
- → 활용 결정 필요 (Phase 2f 후속)

### 운영 모델 v2 enforce 부재
- ADR-013 HARNESS dispatch 의무 — pre_agent.py hook이 모드 매트릭스 enforce 안 함
- vault-first cycle (READ → seq → codex → UPDATE) 자동 monitoring 없음
- 현재는 "정책 문서 수준" — discipline 의존
- 후속: hook 강화 (Phase 2f)

## Polaris 적용

### 즉시 (이 commit)
1. Intraday plist removed
2. Fee migration (이미 0.0014로 통일 — 0 fixed)
3. TickMomentum guard
4. Reconnect stale clear + exponential backoff

### 후속 (Phase 2f)
1. MTAConfluence 활성 등록 또는 import 제거
2. Binance WebSocket + cross-exchange leading signal
3. Hook 강화 (HARNESS dispatch enforce)
4. Vault-first cycle 자동 monitoring

## 현재 운영 상태 (post-fix)

```
launchd:
- com.polaris.paper.realtime (KeepAlive, restarted with fixes)
- com.polaris.paper.daily (01:00 UTC)
- com.polaris.dashboard (login)
(intraday REMOVED)

Open positions (6, fee 0.0014):
- BTC-USDT TradeFlow @78713.80
- DOGE-USDT TradeFlow @0.10786
- ETH-USDT TradeFlow @2321.58
- ORDI-USDT TradeFlow @5.263
- TRUMP-USDT TradeFlow @2.334
- SUI-USDT VolumeBurst @0.9261 (legacy, fee 정합)
```

HYPO-012 TradeFlow 가장 active (taker buy ratio > 0.6 자주 발생).

## Related
- ADR-012 (Realtime architecture)
- ADR-013 (HARNESS Meta Mode)
- ADR-004 (Codex 외부 리뷰)
- INSIGHT-007 (fee 함정)
- INSIGHT-018 (tick-driven discovery)
