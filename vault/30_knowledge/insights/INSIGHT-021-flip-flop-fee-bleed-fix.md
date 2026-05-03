---
entity_type: insight
entity_id: INSIGHT-021
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[INSIGHT-019]]", "[[INSIGHT-007]]", "[[ADR-012]]", "[[ADR-010]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex DEBATE Round 4 — flip-flop fee bleed fix 검증 (74% agreement, ACCEPT WITH CONDITIONS, 2/3 gap 즉시 보강)
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-021 — Flip-flop Fee Bleed Fix (Codex Round 4)

## 발견 (직접 측정 — 5분 운영)

Phase 2c~e 4 CRITICAL fix 적용 후 realtime runner 재시작 — 5분 후 측정:
- **26 closed trades** 중 100% 손실
- **Total realized PnL = -$10.45**
- 모든 close reason = `signal_exit` (TP/SL hit 거의 없음)
- 가장 active = HYPO-012 TradeFlow

## Root Cause 분석

### 패턴
ENTER → 1-30초 안에 EXIT → fee 0.0014 round-trip > 가격 변동 = **수학적 음수 EV**

### 구조적 원인
1. **No min hold time** — entry 직후 다음 tick에 즉시 exit 가능
2. **Narrow signal band**:
   - TradeFlow buy=0.60/sell=0.40 → 0.45-0.55 영역 jitter
   - OrderBookImbalance buy=0.62/sell=0.38 → 같은 문제
3. **No re-entry cooldown** — 같은 ticker 즉시 재진입 → 누적 fee bleed

### Math (확인)
- Tick 가격 변동 1-30s: ±0.05-0.20%
- Fee round-trip: 0.14%
- → micro-movement < fee = 모든 trade EV 음수

## 적용된 Fix (Round 4)

### 1. Min hold time (`src/paper/realtime_runner.py`)
```python
MIN_HOLD_MS = 90_000          # entry 후 90s signal_exit lockout
RE_ENTRY_COOLDOWN_MS = 60_000 # close 후 60s re-entry 차단
```

Exit 우선순위:
- TP/SL hit → 항상 close (긴급 브레이크 유지)
- signal_exit → `held_ms >= 90_000ms` 만 활성

### 2. Hysteresis 강화
- `trade_flow.py`: BUY 0.60→0.65, SELL 0.40→0.45 (deadzone 0.45-0.65)
- `orderbook_imbalance.py`: BUY 0.62→0.68, SELL 0.38→0.42 (deadzone 0.42-0.68)

### 3. Cooldown — strategy + ticker dual layer
- `_last_close_ms[(ticker, sname)]` — 같은 strategy 60s 차단
- `_last_close_ms_ticker[ticker]` — **모든 strategy 60s 차단** (계좌 fee 보호)

### 4. Tests (107/107 pass — 18 신규)
- `tests/strategies/test_trade_flow.py` — hysteresis deadzone
- `tests/strategies/test_orderbook_imbalance.py` — 같음
- `tests/paper/test_realtime_runner.py` — min hold + cooldown + 경계값 + cross-strategy

## Codex Round 4 합의

- VERDICT: ACCEPT WITH CONDITIONS
- CONFIDENCE: 87%
- AGREEMENT: 74%

### 3 Critical Gaps (Codex 지적)
1. ✅ **경계값 테스트** (`==90_000`, `==60_000`) — **즉시 보강** (2 신규 test)
2. ✅ **ticker-global cooldown** — **즉시 보강** (`_last_close_ms_ticker` + 1 신규 cross-strategy test)
3. ⏳ **Post-fee EV 양수 증명** — **후속 sprint** (26 losing trade의 MFE/MAE 5/15/30/60/90s 버킷 분석 필요)

### Codex 추가 의견
- 90s는 휴리스틱 — 신호 half-life 데이터로 calibration 필요 (gap #3과 묶음)
- TickMomentum은 event-driven 구조라 hysteresis 불필요 — churn 테스트만 권고
- VolumeBurst SUI -$1.34: 1H burst entry + intraday SL의 timeframe mismatch (별도 진단)

## 후속 액션 (Phase 2g+)

### 즉시 (이 commit 이후)
- [ ] Runner 재시작 + 24h 운영 측정
- [ ] HYPO-012 TradeFlow trade 빈도 변화 관찰 (감소 예상)
- [ ] 첫 signal_exit close net PnL > 0인지 확인

### Phase 2g
- [ ] 26 losing trade replay → MFE/MAE 5/15/30/60/90s 버킷 분석
- [ ] MIN_HOLD_MS calibration (현재 90s가 최적인지)
- [ ] Post-fee EV 양수 증명 (Codex gap #3)
- [ ] VolumeBurst 1H thesis vs intraday SL timeframe mismatch 진단
- [ ] TickMomentum churn 테스트 (Codex 권고)

### Phase 2h (이전 plan에서 보류)
- MTAConfluence 활성 등록 또는 import 제거
- Binance WebSocket cross-exchange leading signal

## Polaris 적용 — 운영 모드

이번 fix = **HARNESS direct** (자명한 hysteresis + min hold 변경) + **DEBATE** (codex Round 4 review).

ADR-013 위반 사례 인정 후 첫 정합 cycle:
- ✅ HARNESS dispatch — codex DEBATE
- ✅ 코드 변경 직후 codex review
- ✅ Vault note (이 INSIGHT)

## Related

- INSIGHT-019 (Codex Round 3 — 4 CRITICAL fix)
- INSIGHT-007 (fee 함정 — 0.0014 LV3+ 가정)
- ADR-012 (realtime architecture)
- ADR-010 (risk management — daily loss 5%)
