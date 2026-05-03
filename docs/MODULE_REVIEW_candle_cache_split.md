# MODULE_REVIEW — `invasion/data/candle_cache.py` split plan (F-N17)

**Size**: 1063 L (> 1000 → P0 분할, [.claude/docs/code_size_limits.md])
**Owner**: data_advisor
**Fwd PR5 ts 의무화 (commit b7549b0)** 경로 유지 필수 — 회귀 금지.
**Security gap**: 8 `try/except pass` silent failure (별도 batch).

## 현재 블록 map (line ranges)

| Block | Lines | 역할 | Fwd PR5 ts? | 외부 caller |
|---|---|---|---|---|
| A. YAHOO_TICKERS static dict | 22–136 | Yahoo 심볼 lookup table (forex/crypto/commodity/indices/equities/ETF) | no | `candle_cache.YAHOO_TICKERS` (wiring 주석만) |
| B. DB/File mapping loader | 139–212 | `load_db_yahoo_symbols` + `load_file_yahoo_symbols` (additive merge) | no | `boot/wiring.py` |
| C. register + resolve | 215–307 | `register_yahoo_symbol`, `resolve_yahoo_ticker` (OKX reverse map, passthrough) | no | `instrument_enricher`, internal |
| D. AI symbol resolver | 310–430 | `_ai_resolve_yahoo_symbol` (Gemini + yfinance validation), `_append_mapping_file` | no | internal only |
| E. RESOLUTION_MAP + `_file` | 434–446 | timeframe → (interval, period), file path | no | internal |
| F. `load` / `save` (+ warn helpers) | 449–587 | JSON cache load/save, **Fwd PR5 ts 필터/저장**, DataStore candles mirror | **YES** | internal |
| G. `_fetch_alpaca` | 590–657 | Alpaca bars API (US stock/ETF), **ISO-8601 ts 변환** | **YES** | internal |
| H. yfinance session + `_fetch_yahoo` | 660–758 | `_get_yf_session` (curl_cffi), `_fetch_yahoo` (DatetimeIndex → ts, 4H resample) | **YES** | internal |
| I. Yahoo fail cache | 761–812 | `_yahoo_fail_cooldown`, persistence, `_log_yahoo_fail` | no | internal |
| J. `get_candles` router | 815–1005 | 멀티-exchange 라우팅 (OKX → Binance → Capital → Alpaca → Yahoo → WS) | indirect (passes candles through) | **모든 provider/ticks** |
| K. `_build_candles_from_ls` | 1008–1063 | WS tick → synthetic 60s buckets, **ts stamp per bucket** | **YES** | internal |

## 분할 우선순위 (저위험 → 고위험)

1. **A+B+C+D → `candle_symbols.py`** (심볼 라우팅, ~400L, Fwd PR5 ts 없음) ← **이번 batch**
2. E+I → `candle_meta.py` (timeframe, fail cache, ~60L) — 추후
3. G+H → `candle_fetch.py` (Alpaca + Yahoo fetch, Fwd PR5 ts 포함) — Fwd PR5 회귀 위험, 별도 검증 필요
4. J 잔존 → `candle_cache.py` router 역할 유지 (save/load + get_candles)

## 1 extraction — `candle_symbols.py`

**범위**: A(static dict) + B(loader) + C(register/resolve) + D(AI resolver)
**이유**:
- Fwd PR5 ts 로직 미포함 (회귀 위험 0)
- `sqlite3.connect` 없음 (F-N2 유지)
- 외부 caller 2곳 (`boot/wiring.py`, `instrument_enricher`) — 이름 안 바꾸고 재export 로 breakage 0
- `_resolved_yahoo` / `_ai_resolve_attempt` 모듈 state 는 새 파일로 이동, candle_cache 는 `from .candle_symbols import *` 재export

**보존 대상 이름** (candle_cache 에서 계속 접근 가능):
- `YAHOO_TICKERS` (dict, mutable — register_yahoo_symbol 이 mutate)
- `resolve_yahoo_ticker`
- `register_yahoo_symbol`
- `load_db_yahoo_symbols`
- `load_file_yahoo_symbols`

## 검증

- `wc -l` 전후 비교 (symbols.py ~400 + candle_cache.py ~670)
- `python3 -m py_compile` 두 파일
- `grep sqlite3.connect` = 0
- `python3 -c "import invasion.main"` 로 wiring import 회귀 체크
- Fwd PR5 ts 경로 (F/G/H/K) 건드리지 않음 — diff 에서 해당 라인 0

## 후속 (out-of-scope)

- 8 try/except pass (silent failure) 수정 — security_advisor batch
- G+H 분할 (Fwd PR5 회귀 위험) — 별도 MSG
- `get_candles` 라우터 내부 사용 import 체인 (`groups.get_group`, `okx.public`) 은 순환 피하려 lazy import 유지
