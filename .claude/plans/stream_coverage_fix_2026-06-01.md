# Stream Coverage Fix — Capital + Alpaca P0 (2026-06-01)

2건 포렌식(Capital `w0h6pfwih` · Alpaca `w9gq4ueep`) → **2/3 스트림이 거의/전혀 거래 안 함**. Jin 승인 = P0 묶음 착수. 관련: P3 REFRAME("장치보다 coverage/feature 먼저")과 정합. 라이브 봇 PID 51824 무중단(최종 graceful restart만).

## 공통 root
non-OKX 유니버스 active-ranking이 "맞는 종목"을 seat 못 함 (Alpaca=placeholder 동일값→알파벳 slab·메가캡 0 / Capital=ATR가중→이그조틱 크로스) + ingest/discovery 배선 갭 (Alpaca 바 0, Capital 금/원자재 미fetch).

## P0 (착수, 전부 ⚠거동변경 — Jin 승인됨)
### Alpaca (🔴 time-critical: 오늘 밤 13:30 UTC RTH open 전)
- **A1**: `fetch_bars_one`(`polaris/scripts/_production_bars.py:46-100`)에 **venue=='alpaca' 브랜치 추가** → `AlpacaAdapter.fetch_bars`(`adapter.py:296`, GET /v2/stocks/{symbol}/bars) 배선, canonical Bars newest-last. (현재 L100 `return []`로 떨어져 바 0개.)
- **A2**: **equity 바 timeframe end-to-end 지원**. 결정 = **'1D' 등록**(전략 의미 보존; retime 1H는 momentum/MA200 의미 바뀜) → `BAR_INTERVALS`(`core/data/schema.py:19`) + `TIMEFRAME_FETCH_CADENCE_SEC`/Alpaca resolution map(`_production_bars.py:29-43`)에 daily 추가. **첫 ingest 시 200+일 backfill**(equity_rsi_bb MA200 warmup=205 충족). Alpaca bars API date-range 지원.
- verify: 알파카 바 ingest>0 → equity 전략 30-bar 게이트 통과 → RTH open 시 signal/order. (장 닫힘엔 T13 hold=정상.)

### Capital
- **C1**: **27h 1심볼(EURUSD_W) active 붕괴 refresh-health 디버그+하드닝** (`_production_layers.py` refresh_capital_universe_once + `_capital.py` fetch). FX 24/5인데 1개로 zero-out 금지 — stale/failed fetch가 active book을 비우지 못하게. (먼저 왜 1개로 떨어졌나 root → harden.)
- **C2**: 오일 회사 EQUITY(BP/CVX/XOM…)를 `commodity` 오태깅 → **'equity'로 재태깅 or 제외** (`_capital.py` `_classify_capital_node`). + 진짜 금/Metals/Energy commodity CFD 노드 **live nav-tree walk로 매핑**(creds 필요) → discovery 추가. (nav-walk는 라이브 API 조사 선행.)

## P1 (다음, deferred)
- 공통: 실 유동성 주입 후 ranking (Alpaca placeholder 제거→메가캡 seat / Capital majors 서브쿼터→메이저 seat) — `_alpaca.py`·`_ranking.py`·`schema.py`. per-venue 공정 focus 창 — `watchlist.py`.
- Capital: 전략 SUPPORTED_SYMBOLS 실 네임스페이스 정렬.

## P2 (cosmetic/secondary)
- Capital 로그-라벨 하드코딩(`_production_bars.py:220` 지수→forex 오기) · Alpaca top_n 캡 상향/회전 · Alpaca IEX WS 구독 한도.

## 작업 방식
worktree 빌드(live 미접촉) → TDD(실패→코드→pass) → fresh-Claude 적대리뷰(builder≠reviewer, 개발 GPT 0) → behavior-gate(가능한 곳 byte-diff/shadow) → ruff/mypy clean → 전체 pytest → **Jin 승인 후 graceful restart**(SIGTERM→재기동, OKX 무중단). mandate: flow_not_block(coverage↑, throttle 아님) · 9-stack 불변 · 거부키워드 0.
