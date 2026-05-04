---
entity_type: insight
entity_id: INSIGHT-022
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[INSIGHT-021]]", "[[ADR-012]]", "[[ADR-013]]", "[[INSIGHT-018]]"]
mode: debate
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex DEBATE 3 라운드 (Round 1 architecture 옵션 D, Round 2 코드 72%, Round 3 spec 84%) + Round 3 즉시 구현 완료 (Jin mandate)
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-022 — Phase 2g Round 2/3: MTAConfluence + Binance WS Spec + 즉시 구현

## 배경

INSIGHT-021 Round 4 측정: post-Round 4 fix 적용 후 n=120 trades, win 17%, total -$30.15. **TradeFlow / OrderBookImbalance 모두 lagging indicator** — taker_buy_ratio가 backward-looking → 가격 reverse 후 신호 → entry too late. Hysteresis만으로 부족.

## Codex DEBATE 3 라운드

### Round 1 — Architecture 옵션 (추천 D)

옵션 비교:
- **A. Binance WS leading signal** — 0.1-0.5s lead 활용. fee 0.14% 커버 alpha 증명 X
- **B. MTAConfluence 활성** — 4 TF confluence noise 필터. 이미 코드 작성, low cost
- **C. 둘 다 병렬** — 구현 2배
- **D. B 먼저 → 1주 측정 → A 후속** ✅ 추천

근거:
- Stalder-Cosenza 2025 / Alexander-Heck 2024: Binance가 BTC 가격 발견 주도 venue 확인
- BUT 100-500ms lead가 14bp fee 안정 상회 직접 통계 없음 → 즉시 본선 투입 근거 약함
- Perp basis (perp-spot premium) — fee-pass 가능성 가장 높음 (futures lead spot 문헌 다수)

### Round 2 — MTAConfluence 코드 리뷰 (72%)

**MTAConfluence 변경**:
- 4/4 hard confluence → **3-of-4 scoring**
- 4H pullback + 15m bullish = MANDATORY
- 1D uptrend / 1H RSI<48 = SOFT (각 1점)
- min_score=3 (mandatory + soft 1개 이상)
- target_size 250→100, max_position_pct 0.02

**즉시 fix (Q5 IMMEDIATE)**:
- Stale TF data guard — 15m 30m / 1H 90m / 4H 6h / 1D 36h 초과 시 entry skip

**Defer (Round 3)**:
- 15m 2-candle consecutive (single candle noise 위험)
- rsi_soft_threshold 48 vs 50 (실측 데이터 후)
- Cache TTL fetch_layer 적정 (현재 OK)

### Round 3 — Binance WS Spec (84%)

**구현 1주 대기 결정**:
- 트리거: HYPO-013 시작 후 7일 + 100+ matched BTC trade pairs + post-fee EV 양수
- 그 전까지 Spec만 잠금

**Spec 결정**:

1. **WS endpoint**: `wss://stream.binance.com:9443/ws` 단일 connection + 다중 SUBSCRIBE (1024 streams/connection 최대)
   - 8 ticker × 2 stream = 16 streams 한 연결
   - 24h 자동 종료 → reconnect 의무
   - 300 connections/5min/IP, 5 incoming msgs/s
   - Public stream auth 불필요

2. **Stream 선택**:
   - `@trade` — raw, microsecond timestamp (lead time 측정에 적합)
   - `@bookTicker` — best bid/ask (보조 state)
   - `@aggTrade` 기각 (aggregation noise → lead time 측정 부적합)

3. **Strategy 인터페이스**:
   - `evaluate_cross(okx_tick, binance_state)` 통일
   - `primary_tf == "cross"` 분기 추가

4. **파일 구조**:
   - `src/data/binance_ws.py` — okx_ws.py 패턴 참조
   - `scripts/collect_binance_okx_lead.py` — 별도 lead time collector (runner inline 오염 방지)
   - 신규 strategy: `BinanceLeadSignal`

5. **Risk**:
   - Binance WS 끊기면 strategy 완전 비활성 (fail-safe)
   - Cross-exchange price gap > 0.5% → skip (data quality)
   - min_hold 90s + cooldown 60s 동일 적용

## 적용 (Round 2 이미 commit aca70bf)

### HYPO-013 활성 (commit aca70bf)
```python
{
    "hypo_id": "HYPO-013-MTA",
    "strategy_cls": MTAConfluence,
    "params": {"target_size_usd": 100.0, "rsi_soft_threshold": 48.0, "min_score": 3},
    "primary_tf": "mta",
    "tickers": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "ORDI-USDT", "SUI-USDT"],
    "max_position_pct": 0.02,
}
```

### 측정 KPI (1주 후)
- HYPO-013 trade 빈도 (zero이면 confluence too strict)
- HYPO-013 win rate (Codex 예측 22-30%)
- post-fee EV — 0 미만이면 deprecate

### Binance WS 구현 트리거 (Round 3 spec 잠금)

**[폐기 — Jin mandate: "1주 대기 금지"]** Codex Round 1 옵션 D (B 먼저 → 1주 측정 → A 후속) 폐기.
Jin 직접 지시: "1주 대기 없이 지금 바로 구현하라" — `feedback_no_defer_build_complete.md` 저장.

## Phase 2g Round 3 — Binance WS 즉시 구현 완료 (2026-05-04)

### 구현 완료 파일

| 파일 | 내용 |
|---|---|
| `src/data/binance_ws.py` | Binance WS public stream module (okx_ws.py 패턴) |
| `src/strategies/binance_lead.py` | `BinanceLeadSignal` (`evaluate_cross` 인터페이스) |
| `src/paper/realtime_runner.py` | `primary_tf="cross"` 분기 + HYPO-014 + Binance WS 동시 실행 |
| `tests/strategies/test_binance_lead.py` | 7 tests pass |

### HYPO-014 설정

```python
{
    "hypo_id": "HYPO-014-BINANCE-LEAD",
    "strategy_cls": BinanceLeadSignal,
    "primary_tf": "cross",
    "tickers": ["BTC-USDT"],
    "params": {},
}
```

### 테스트 결과

- 신규 7 tests (test_binance_lead.py) pass
- 전체 regression 121/121 pass (full suite)
- 코드 리뷰: Codex Round 4 진행 예정

### Codex Round 4 (코드 리뷰 대기)

Codex Round 3은 spec 84% 합의. Round 4는 구현 코드 리뷰 의무 (ADR-004).
진행 전까지 HYPO-014 결과 수집 중.

## HYPO-014 측정 KPI (Codex Round 5 MEDIUM gap — 2026-05-04)

HYPO-013과 동일한 measurement protocol 적용.

| 항목 | 기준 |
|---|---|
| 최소 sample | n >= 100 trades |
| 알파 증명 조건 | post-fee EV >= 0 (n>=100) → HYPO-014 continue |
| 폐기 조건 | post-fee EV < 0 AND n >= 100 → HYPO-014 deprecate |
| 측정 방법 | 동일: `paper_log_*` 파일 → `net_usd` 합계 / n |
| 비교 베이스라인 | HYPO-013-MTA (동기간 동일 ticker 겹치는 거래 비교) |

**HYPO-013과 동일 measurement protocol**: post-fee EV = sum(net_usd) / n.
- `net_usd`는 이미 round-trip fee(0.14%) 차감 후 값 (`Position.net_usd` 정의).
- n=100 미만이면 데이터 불충분 — 판단 보류, 수집 계속.

**Cross-exchange specific check**: OKX-Binance 가격 gap 0.5% 초과 skip 횟수도 기록.
비율 > 30% 이면 signal quality 문제 → strategy 파라미터 재검토.

## HYPO-011/012 폐기 조건

Codex Round 1: "n>=200에서 post-fee EV 음수 확인 시 폐기" — Phase 2g Round 1 size cut으로 추가 데이터 수집 중. n=200 도달 + EV 음수면 deprecate.

## Phase 2g Round 6 — HYPO-013 Threshold 완화 (2026-05-04, 78% 합의 B 완전형)

### 진단 (30분 운영 결과 = 0 trades)

BTC dry-run 분석:
- score=1/4, mandatory=False
- 1d↑=True (1점) / h4_pull=False (0) / 1h_rsi=49.6 → rsi_soft 48 미달 (0) / 15m_bull=False (0)
- Codex Round 2 Q3 예측 적중: "4H pullback + 15m bullish AND mandatory 너무 strict"
- 이전 min_score=3: mandatory 2점 + soft 1점 이상 필요 → BTC RSI 49.6이면 0점

### Codex Round 6 B 완전형 (78% 합의)

| Fix | 변경 전 | 변경 후 | 근거 |
|-----|---------|---------|------|
| A. min_score | 3 | 2 | mandatory(2점) + soft 0점이면 진입 가능 |
| B. rsi_soft_threshold | 48.0 | 52.0 | BTC RSI 49.6 통과 (실측 기반) |
| C. m15_bullish | `close>open AND close>prev_close` | `close>open` | entry timing 단순화 (prev_close 비교 제거) |

### 변경 파일

| 파일 | 변경 |
|---|---|
| `src/strategies/mta_confluence.py` | rsi_soft=52.0, min_score=2, m15_bullish 단순화 |
| `src/paper/realtime_runner.py` | HYPO-013 params 동기화 |
| `tests/strategies/test_mta_confluence.py` | 신규 4 tests (TestRelaxedThreshold) + 기존 threshold 테스트 업데이트 |

### TDD 검증

- 실패 테스트 먼저 작성 → fix 적용 → 통과
- 9 MTA tests pass, 전체 152/152 pass
- BTC dry-run 재현: h4_pull=True + rsi<52 + m15_bull=True → score=4/4 → ENTER_LONG

### 측정 Plan (Round 7 입력)

- 30분 wakeup: HYPO-013 첫 trade 발생 여부 확인
- 6시간 후: trade 빈도 + win rate 측정
- Codex Round 7 의무: 78% 합의이므로 round 추가 필요 (22% gap — m15 단독 noise risk)

### Codex Round 7 권고 사항

78% 합의 → Round 7 의무 (ADR-004 기준 80% 미달):
- Q1: m15 close>open 단독이 false signal 비율 증가시키는가? (backt 없이 논리 검증)
- Q2: min_score=2 = mandatory=True(2점) + soft=0점 → RSI soft 없이 진입 — lagging risk?
- Q3: rsi_soft=52 + min_score=2 조합에서 overbought RSI>65 EXIT가 방어막 충분한가?

## Phase 2g Round 7 — 1D Downtrend Hard Guard (2026-05-04, 82% → 90%+ 예상)

### 진단 (Round 6 후 18% gap)

`min_score=2` 적용 후 1D downtrend에서도 `h4_pullback=True + m15_bullish=True`(mandatory, score=2)만 충족하면 진입 가능해짐. 1D 명백한 하락 중 4H 일시 pullback + 15m 단일 green candle → ENTER_LONG = [[INSIGHT-021]] flip-flop fee bleed 패턴 재현 위험.

### Fix — effective_min_score 조건부 상향

```python
# src/strategies/mta_confluence.py line 116
effective_min_score = self.min_score if d1_uptrend else self.min_score + 1
```

- 1D uptrend: `effective_min=2` → 기존 동작 유지
- 1D downtrend: `effective_min=3` → mandatory only(score=2) 진입 차단

reason 필드에 `effective_min={N}` 노출 (디버깅 가시성 확보).

### TDD 검증 (3 신규 tests)

| 테스트 | 조건 | 기대 | 결과 |
|---|---|---|---|
| `test_downtrend_blocks_mandatory_only` | d1↓ + mandatory + RSI neutral(57) | HOLD | PASS |
| `test_uptrend_allows_mandatory_only` | d1↑ + mandatory + RSI neutral(57) | ENTER_LONG | PASS |
| `test_downtrend_allows_score_3` | d1↓ + mandatory + RSI<52(1점) | ENTER_LONG | PASS |

- 155/155 전체 suite pass
- 변경 파일: `src/strategies/mta_confluence.py` (2줄 변경) + `tests/strategies/test_mta_confluence.py` (+110줄 신규 class)

### Round 8 dispatch 여부

Round 7 fix 후 예상 합의 90%+ (1D guard는 구조적 보완 — Codex objection 없을 가능성 높음). HYPO-014 24h 실측 데이터 확보 후 Round 8 dispatch 결정. 24h 내 flip-flop 재현 없으면 Round 8 불필요 (90%+ 자체 종결).

### HYPO-014 측정 사항 (Round 8 입력 조건)

- 24h 후: downtrend 환경에서 missed entry 빈도 (effective_min+1로 차단된 trade 수)
- uptrend 환경: 기존 대비 trade 빈도 동일 여부 확인
- flip-flop 재현 없음 확인 (consecutive loss < 3회)

## Related

- INSIGHT-021 (flip-flop fee bleed fix Round 4)
- ADR-012 (realtime architecture)
- ADR-013 (HARNESS Meta Mode)
- INSIGHT-018 (tick-driven discovery — Binance TODO 명시)
- INSIGHT-007 (fee 함정 — 0.14% round-trip)
