---
pure: true
code_path: src/risk/regime_detector.py
test_path: tests/risk/test_regime_detector.py
created: 2026-05-04
phase: 2j
status: active
tags: [regime, btc, sma, crisis, pure, phase2j]
entity_type: component
entity_id: regime_detector
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[dynamic_sizing]]", "[[INSIGHT-032]]"]
---

# regime_detector

BTC 1D SMA 기반 시장 regime 감지 (Phase 2j).

## 책임

BTC 일봉 candle 목록으로 현재 시장 regime을 분류. Pure function.

## 핵심 함수

`detect_regime(btc_candles_1d: list) -> str`

### 판별 로직 (우선순위 순)

1. **Crisis** — 24h drop >= 8% → "crisis" (북극성 mandate: fear = max bet, 먼저 체크)
2. **Uptrend** — sma20 >= sma50 * 1.02 AND last >= sma20 → "uptrend"
3. **Downtrend** — sma20 <= sma50 * 0.98 AND last <= sma20 → "downtrend"
4. **Flat** — 기본값 (< 50 candles 포함)

### 반환값

| 값 | 의미 | sizing mult |
|----|------|-------------|
| "crisis" | 급락 (-8%+) | 1.5x (북극성) |
| "uptrend" | 상승 추세 | 1.0x |
| "flat" | 횡보 | 0.7x |
| "downtrend" | 하락 추세 | 0.3x |

## P6 분류

Pure core — Duck-type candle 프로토콜 (`.close` attribute만 필요). Caller가 candle 제공.

## 관련

- [[dynamic_sizing]] — regime 문자열 소비
- realtime_runner: `_get_btc_1d_candles()` shell이 60s 캐시 관리
- north_star.md: crisis escalation 원칙
