# MODULE_REVIEW — `invasion/data/data_collector.py` split plan (F-N17)

**Size**: 1022 L (> 1000 → P0 분할, [.claude/docs/code_size_limits.md])
**Owner**: data_advisor
**Boot race**: `wiring.py:55-57` 은 `collector.collect_slow(force=True)` 호출 — 이 signature 절대 유지.
**SSOT F-N2**: raw `sqlite3.connect` 0건 — 유지 필수.
**Providers wiring**: `wiring_signals.py:83-` 이 `collector._coinglass`, `_binance`, `_fred`, `_myfxbook`, `_alt_fg`, `_defillama`, `_blockchain`, `_yfinance` 를 property / 내부 attr 로 참조. 공개 property 명 + attr 명 유지.

## 현재 블록 map (line ranges)

| Block | Lines | 역할 | 외부 caller | 위험도 |
|---|---|---|---|---|
| A. Lazy imports (20 collectors) | 19–168 | `try: from .collectors.X import Y` 블록 20개 (ImportError → None) | internal only | low |
| B. `__init__` + `_safe_init` | 171–242 | 20 client instantiation + lock + cache + load | `wiring.py`, tests | medium (attr naming 고정) |
| C. `collect_fast` | 246–416 | CNN F&G / CoinGlass funding·OI·liq_heatmap·oi_candles / Binance funding·positioning / Alt F&G + **3 jsonl writers** (funding_rate_log, liquidation_log, fng_log) + sentiment_history.jsonl | main loop | medium |
| D. `collect_slow(force=False)` | 418–555 | FRED / yFinance (dxy/vix promotion) / DeFiLlama TVL+stables / Blockchain.info / Myfxbook + trackb | `wiring.py:57`(force=True), `regime_detect.py:93`, main loop | **high (boot race signature)** |
| E. `_collect_trackb_lazy` | 557–717 | Track B Phase 2 shadow-mode 17 collectors (edgar/apewisdom/finviz/finra/alpaca_news/santiment/cryptopanic+llm/gtrends/ffcal/oanda_pb/eia/baker/wasde/vix_term/put_call/sent_weekly) | `collect_slow` 내부 only | **low (순수 내부)** |
| F. `collect_weekly` | 719–750 | COT report | main loop | low |
| G. `collect_all` | 752–758 | 3-tier fan-out (startup) | startup | low |
| H. Ticker getters | 762–890 | `latest`/`get`/`get_coinglass_ticker`/`get_cot`/`get_myfxbook`/`get_binance_*`/`get_alt_fear_greed`/`get_yfinance`/`get_defi_tvl`/`get_btc_onchain` | signal providers | medium (이름 고정) |
| I. Status + 10 client props | 892–960 | `available_sources`, `collection_status`, `@property` 10개 | `wiring_signals.py`, dashboard | **high (provider wiring 직접 참조)** |
| J. Cache persist | 964–1023 | `_save_cache` + `_load_cache` (`data/extended_data_cache.json`) | internal | low |

## 분할 우선순위 (저위험 → 고위험)

1. **E → `collector_trackb.py`** (Track B Phase 2 shadow loop, ~160L, 순수 내부) ← **이번 batch**
2. C 내 aux-log writers 3개 + sentiment_history writer → `collector_sentiment_log.py` (~60L, jsonl only, 추후)
3. J → `collector_persist.py` (~60L, 순수 IO, 추후)
4. A(lazy imports) → `collector_registry.py` (~135L, name 보존 위해 `from .collector_registry import *`, 추후)
5. H(ticker getters) → `collector_access.py` (~130L, method → module function + thin wrapper, 고위험, 추후)

## 1 extraction — `collector_trackb.py`

**범위**: E (`_collect_trackb_lazy` method body + 17 collector 호출)
**이유**:
- `collect_slow` 내부 1회 호출만 있음 (외부 caller 0)
- Boot race (wiring.py collect_slow force) 경로 무관 — `collect_slow` signature 불변
- `sqlite3.connect` 없음 (F-N2 유지)
- 모든 attr 접근이 `self._<xyz>` — 모듈 함수로 이동 시 `collector` 인자 1개로 흡수 가능
- 17개 `try/except`+`log_event` 반복 — 모듈화 이득 큼

**Behavior 보존 전략**:
- 새 파일: `def collect_trackb_lazy(collector) -> dict` — 기존 method body 그대로 (`self` → `collector`)
- 기존 method 는 래퍼 유지: `return collect_trackb_lazy(self)` (1줄)
- `_trackb_ts` dict mutation 도 `collector._trackb_ts` 로 동일하게 유지
- `from .collectors.*` 계열은 이미 `data_collector.py` 상단에서 import → 새 파일에서는 import 불필요 (collector 의 attribute 로 접근)
- `log_event` 는 신규 import

## 검증

- `wc -l invasion/data/data_collector.py invasion/data/collector_trackb.py` — 전후 비교
- `python3 -m py_compile` 두 파일
- `grep -n "sqlite3.connect" invasion/data/data_collector.py` → 0 유지
- `python3 -c "import invasion.main"` — import graph 정상
- `grep -n "_collect_trackb_lazy\|collect_trackb" invasion/` — 외부 caller 0 재확인

## 추후 작업 (이번 batch 범위 밖)

- aux-log writers (collect_fast 말미 3 block) jsonl writer 모듈화
- H 구간 ticker getter 는 providers 가 `.get_coinglass_ticker()` 형태로 호출하므로 method 유지 필수 — 분리 시 wrapper 보존
- I 구간 10 property 는 signals/wiring 에서 직접 참조 → 최후 분할
