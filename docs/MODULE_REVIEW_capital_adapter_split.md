# MODULE_REVIEW — `invasion/exchange/capital_adapter.py` 931L Split Plan (F-N17)

> exchange_advisor F-N17: `invasion/exchange/capital_adapter.py` = **931 LOC in one file**.
> Code discipline: 801-1000 권장 분할 (`.claude/docs/code_size_limits.md`).
> Capital.com CFD 어댑터. 주변 모듈 `capital/client.py` 564L + `capital/ws_feed.py` 318L 는 이미 서브패키지로 분리됨.
> 본 문서는 **파일 전체 block map + 저위험 extraction 순서** 담당.
> Market-hours gate / Weekend EOD flatten / Order lifecycle (entry/stop/trail/TP) / `_not_found_cache` 공유 class var 전부 보존.

---

## 1. File Block Map (capital_adapter.py 931L, as of 2026-04-18)

| # | Block | Lines | LOC | 역할 | Extract 난이도 | Risk | 우선순위 |
|---|-------|-------|-----|------|----------------|------|---------|
| **B0** | **Module header + `_CAP_MAJOR_TICKERS` / `_CAP_LARGE_TICKERS` / `_classify_cap_tier`** | **1-50** | **50** | **티어 분류 모듈 상수 + pure fn (forex/commodity/indices/stock major/large/mid/micro)** | **저 (pure data, 1 callsite)** | **Low** | **P1 (이 PR)** |
| B1 | `CapitalComAdapter.__init__` + class docstring + `_not_found_cache` class var | 53-135 | 83 | 어댑터 생성자 (cfg/portfolio/ws/epic map/skip log cache) + R-3 공유 class-level `_not_found_cache` | — (stay, wiring) | — | — |
| B2 | WS 연결 유틸 (`attach_ws_feed`, `prioritize_ws_subscriptions`, `_register_epic`, `feed_status`, `feed_stats`) | 95-128 | 34 | WS 피드 attach/priority/auto-register | 저 (thin pass-through) | Low | P2 |
| B3 | `get_price` | 137-201 | 65 | WS cache → REST cache → REST fallback + negative cache (404 24h→1h) + stale guard | 중 (class-level `_not_found_cache` 공유, STALE-GUARD 불변) | Med | P3 |
| B4 | `open_position` | 203-266 | 64 | Order lifecycle entry (direction, stop_distance, commodity min-stop) | 높 (**order lifecycle 보존**) | High | **건드리지 않음** |
| B5 | `close_position` | 268-416 | 149 | Multi-fill close + reject reclassify (market_closed) + spread slip bps | 높 (**order lifecycle 보존**) | High | **건드리지 않음** |
| B6 | `get_positions` + `get_upl_map` + `get_balance` | 418-469 | 52 | Read-only broker state | 저 (순수 read) | Low | P2 |
| B7 | `get_market_data` | 471-685 | 215 | Priority + batch + sentiment + forex session filter + silent-stale 자가치유 | 높 (**MSG-CAP-HEAL streak/recovery + forex session gate**) | High | **건드리지 않음 (별도 검증 필요)** |
| B8 | `_is_adopt_blocked` | 691-745 | 55 | JP exotic hint + ticker_conditional_blacklist + no-market-hours gate | 중 (adopt path, MSG-114/123 연관) | Med | P3 |
| B9 | `sync_positions_to_portfolio` | 747-894 | 148 | Adopt: fill aggregate + epic auto-register + strategy select + bus publish | 높 (adopt path + bus event) | High | **건드리지 않음** |
| B10 | `_log_adopt_skip` + `_calc_deal_size` + `_detect_group` | 900-931 | 32 | Rate-limited skip log + USD→deal-size + group detect | 저 (pure util) | Low | P2 |

**합계**: 11 블록 / 931 LOC.

---

## 2. Extraction 순서 (저위험 우선)

### P1 — 본 PR: B0 Tier Classification → `capital_metadata.py`
- 이동 대상: `_CAP_MAJOR_TICKERS` / `_CAP_LARGE_TICKERS` (module consts) + `_classify_cap_tier(ticker, group)` (pure fn)
- `capital_adapter.py` 는 `from .capital_metadata import _classify_cap_tier` 로 재수입 → L585 callsite 시그니처 불변
- **Saving**: ~32 LOC → capital_adapter.py 가 ~899 LOC 로 하락
- **Rationale**: 외부 callsite 0 (grep 전체 repo: capital_adapter.py 본인만), class 상태 의존 없음, Dict/set 상수 + 순수 함수 → 가장 낮은 risk.

### P2 — 후속 PR: B2 WS utils + B6 read state + B10 small utils → `capital_helpers.py`
- `_register_epic`, `feed_status`, `feed_stats`, `get_positions`, `get_upl_map`, `get_balance`, `_log_adopt_skip`, `_calc_deal_size`, `_detect_group`
- 전부 thin pass-through or pure util. Self-bound method 는 helpers module fn + adapter delegator 로 유지.
- **Saving**: ~120 LOC 추가 → adapter 780 LOC 대로 하락.

### P3 — 후속 PR: B3 get_price + B8 adopt gate → `capital_pricing.py` / `capital_adopt_gate.py`
- `get_price` 는 `_not_found_cache` (class-level R-3 공유) 유지해야 하므로 classmethod 보존. 모듈 fn 으로는 부분 추출만 (STALE-GUARD 블록 등).
- `_is_adopt_blocked` 는 param_registry + market_hours 단일 lookup fn 이라 독립 fn 으로 안전.
- Risk: preg 호출 순서 + adopt 경로 동작 보존 테스트 필요.

### 건드리지 않음 (본 split 범위 외)
- **B4 `open_position` / B5 `close_position`**: Order lifecycle (entry/stop/trail/TP + market_closed 재분류) 보존 절대 원칙.
- **B7 `get_market_data`**: MSG-CAP-HEAL 자가치유 streak + weekend gate + forex session filter — 분할 시 회귀 위험 큼. 별도 F-N17 P2 plan 에서 다룸.
- **B9 `sync_positions_to_portfolio`**: Adopt 경로 + bus publish — 전량 보존.

---

## 3. Invariants (I-C1~C6) — 전부 보존 대상

1. **I-C1 Order lifecycle atomicity**: `open_position` / `close_position` 시그니처/경로/reclassify 규칙 보존 (market_closed, currently closed, reject:rejected: 패턴).
2. **I-C2 `_not_found_cache` class-level shared**: R-3 원칙. 인스턴스 re-create 해도 404 ban 공유.
3. **I-C3 Market-hours gate off-limits**: entry.py:198 SSOT, PRE_CLOSE_FLAT, adopt force-close 경로 유지. Adapter 는 pre-check 하지 않는다 (MSG-114 원칙).
4. **I-C4 MSG-CAP-HEAL self-heal streak**: `_zero_total_streak` 3-warn / 10-recover 경계 + WS stop/start + force re-login 순서 보존.
5. **I-C5 Silent-stale TTL**: `capital_price_staleness_sec` preg (default 30s), `cache_only` 경로에서 stale 반환 금지.
6. **I-C6 Adopt bus event**: `sync_positions_to_portfolio` 가 `trade.entered` publish 하는 계약 유지 (ai_controller.evaluate_adopt 진입점).

---

## 4. 본 PR 체크리스트 (P1)

- [x] `_CAP_MAJOR_TICKERS` / `_CAP_LARGE_TICKERS` / `_classify_cap_tier` → `invasion/exchange/capital_metadata.py`
- [x] `capital_adapter.py` 에서 `from .capital_metadata import _classify_cap_tier` 재수입
- [x] L585 callsite (`tier = _classify_cap_tier(ticker, group)`) 불변
- [x] `py_compile` 통과 + `python3 -c "import invasion.main"` 통과
- [x] Order lifecycle / market-hours / `_not_found_cache` / WS feed / adopt path 건드리지 않음
