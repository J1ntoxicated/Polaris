# P4 WS Real-Time Price Foundation — Build Spec (2026-06-01)

Jin 결정: **WebSocket tick-level 실시간 = 3 venue 기본, 지연 피드 무의미, REST=fallback만.** P4 데이터 파운데이션 + G4 conductor 컷오버 선행. 설계 출처: P4 design agent (read-only). 불변: DEMO/PAPER, AGGRESSIVE(degrade never halt), 거부키워드 0, 9-stack 미접촉. ⚠Python·snapshot_queries 편집 → P1 커밋 후. WS 신규 아키텍처라 빌드 전 Claude 설계리뷰 1회.

## 확정 venue WS (문서 검증)
- OKX `wss://ws.okx.com:8443/ws/v5/public` (데모 wspap.okx.com, US ws.us.okx.com — resolve_okx_base_url와 동일 env로 선택), `tickers` 무인증, ping<25s, 30s idle 재연결.
- Capital `wss://api-streaming-capital.backend-capital.com/connect`, **loop 소유 CapitalSession의 CST+X-SECURITY-TOKEN 재사용**(재인증 X), `marketData.subscribe` epics[](≤40 chunk), 10분 keepalive.
- Alpaca `wss://stream.data.alpaca.markets/v2/iex`(페이퍼 무료 실시간), creds=resolve_alpaca_credentials, quotes/trades 구독, RTH 게이트(equity_session_gate.stream_session_gate_active).

## 기존 자산 (재사용)
- 변환기 `okx_ticker_to_quote_tick`/`capital_market_to_quote_tick`(core/data/canonical.py, pure, 미호출) + `QuoteTick` dataclass + `quote_ticks` 테이블(PK (instrument_id, ts), 비어있음).
- 봇 루프 `run_production_paper_loop`가 이미 background async task(`_layer0_producer`/`_altdata_producer`) 패턴 + shared `stop_evt`/finally teardown 보유 → WS 클라이언트 동일 패턴.
- DB WAL+synchronous NORMAL+busy_timeout 5000(1 writer+N readers). 봇=유일 writer 유지.

## 신규 파일 (≤500 LOC, dep `websockets` 추가)
- `polaris/venues/ws_common.py` — `WSStreamClient` base: connect→subscribe→recv loop→on-msg, **full-jitter exp backoff(0.5→30s cap)**, heartbeat ping task, injectable `is_gated()`, `stop_evt` 협조 종료, idle staleness watchdog(force-reconnect), QuoteTick 콜백. venue-무관.
- `polaris/venues/okx/ws.py` `OKXTickerWS` — 엔드포인트 env 선택(REST와 일관), tickers per instId, app ping<25s, okx_ticker_to_quote_tick(source="okx_ws").
- `polaris/venues/capital/ws.py` `CapitalMarketWS` — 세션 토큰 재사용, marketData.subscribe epics chunk≤40, fx/주말 게이트, capital_market_to_quote_tick(source="capital_ws").
- `polaris/venues/alpaca/ws.py` `AlpacaQuoteWS` — iex 엔드포인트, auth msg, quotes/trades, RTH 게이트, 신규 `alpaca_quote_to_quote_tick`(canonical.py, source="alpaca_ws").
- `polaris/core/data/quote_writer.py` `QuoteTickWriter` — in-mem dict(instrument_id→latest, last-write-wins coalesce) + **~1Hz asyncio flush**(executemany INSERT OR REPLACE, 1 txn), + process-shared `live_px` dict(봇 in-mem read, 0 DB hit), `last_ws_monotonic` per instrument.
- `polaris/scripts/_production_ws.py` `start_ws_producers()→list[Task]` — 3 클라이언트 빌드(focus/universe+env) → 공유 QuoteTickWriter, stop_evt/finally teardown, universe 변경 시 re-subscribe.

## 코엑시스턴스 + fallback
WS 클라이언트 = **봇 이벤트루프 내 async task**(별 프로세스 X, 2nd writer 회피). `_altdata_task` 뒤 spawn, 기존 finally teardown. 루프 소유 conn으로 직렬 write(이벤트루프 직렬→intra-process race 0). REST bar ingest **불변=fallback**: 소비측은 live_px tick이 fresh(<10s)면 WS, 아니면 bar close. 끊김→backoff→bars로 graceful degrade(거동 0, halt 절대 X=AGGRESSIVE 불변).

## 소비측 3곳 + 거동 구분
- **#1 대시보드(거동 0, 먼저 ship)**: `snapshot_queries.py:370 _last_prices`를 quote_ticks MAX(ts) mid(없으면 bar close)로 → 실시간 px-flash.
- **#2 exit(거동 변경, shadow 후)**: `_production_recalc.py:132` last_price를 live_px(fresh) else bar close. exit/G6 timing 변경 → **shadow 선행**(WS-mark vs bar-mark 양쪽 로깅, env flag gate, divergence 검증 후 cutover).
- **#3 G4(거동 변경, shadow 후)**: `_production_run_signal.py:307` `tick_window=[]`를 (venue,symbol) 최근 ~30 tick(live_px history)으로. G4 watcher 입력 변경 → shadow/검증(conductor 컷오버 선행).

## 빌드 순서
1. `websockets` dep + ws_common + quote_writer. 2. OKX ws→_production_ws→quote_ticks 적재(행 검증). 3. Capital+Alpaca ws(세션/creds/게이트 재사용). 4. **#1 대시보드 read 전환(거동 0)**. 5. **그 다음** #2 exit·#3 G4 shadow 배선→divergence 검증→live cutover.

## 리스크/완화
reconnect storm→full-jitter backoff+게이트 venue off-session 미연결 · dup tick→PK+INSERT OR REPLACE+coalesce · clock skew→venue ts 우선, staleness=monotonic · DB 경합→단일 writer 1Hz batch+WAL · 데모 엔드포인트 가용성→REST fallback 상시(WS 실패 non-fatal).

## ⚠ 설계리뷰 교정 (5관점 적대리뷰 wrm9vs7ew, verdict=needs-changes — 빌드 전 필수)
**M1 이벤트루프 stall = 최우선.** conn은 `init_db`(schema.py:202)에서 `isolation_level=None`(autocommit)+check_same_thread=False **단일 공유**. (a) autocommit이라 `executemany INSERT OR REPLACE`는 행마다 commit(WAL frame flush)=N fsync → "1 txn" 전제 붕괴 → 명시적 `conn.execute('BEGIN'); executemany; conn.execute('COMMIT')`로 감싼다. (b) 코드베이스에 run_in_executor/to_thread 0건 → 모든 sqlite write가 루프 스레드 동기 실행. WS recv loop + 1Hz flush + _run_tick write가 같은 conn 경합 → 루프 통째 stall(직렬화≠비블록). **fix: QuoteTickWriter.flush = 스냅샷(`dict(self._buf)` 복사 후 clear, await 경계 없이) → `await loop.run_in_executor(None, self._flush_blocking)`로 BEGIN;executemany;COMMIT 동기블록 오프로드.** WS writer 전용 별도 conn(같은 WAL DB, busy_timeout 유지) 권장(메인 conn과 분리; 단일-writer 불변은 "봇 프로세스만 RW, dashboard mode=ro 별프로세스"로 유지).
**M2 recv on-msg = in-mem만**(coalesce dict + live_px + `last_ws_monotonic[inst]=monotonic()`), DB 접근 절대 금지(write는 1Hz flush task 전담). burst 시 batch drain에 `await asyncio.sleep(0)` starvation-guard.
**M3 OKX WS region 미배선.** `resolve_okx_base_url`(_smoke_roundtrip_shared.py:88)은 REST host(us.okx.com)만, WS host 파생 없음. Jin demo 키=US region 전용 → US/simulated public tickers WS 가용성 **빌드 전 1회 wss 핸드셰이크+tickers 스모크 검증**. 영구 연결실패 시 backoff cap 도달 후 **"연결 포기→REST-only 모드" 상태 전이**(무한 재시도 storm 금지).
**M4 Capital 토큰 (교정 — Jin 지적 + 문서 재확인).** Capital 토큰(CST/X-SECURITY-TOKEN)=**마지막 사용 후 10분 inactivity 만료**(고정 수명 아님). 공식 문서: "Session is active for 10 minutes. … inactivity longer than this period → error" + "ping service at least once every 10 minutes." → **WS keepalive ping(<10분)이 활동으로 카운트 → 스트리밍 중엔 토큰 만료 안 됨.** 리뷰의 "540s silent drop"은 **틀림**: 봇 내부 *선제 refresh 정책*(TOKEN_REFRESH_DEADLINE_SEC=540s=9분 보수 갱신)을 실제 만료로 오인한 것. 실제 요구: (a) WS keepalive ping 간격 **<10분**(예 ~9분/540s) 보장 → steady-state 만료 0(별도 ensure_tokens 불필요). (b) **>10분 idle 후 재연결**(장기 disconnect / 세션게이트 off→on, 주말 등)에서만 토큰 만료 가능 → **그 경로에서만** `ensure_tokens()` 재발급(POST /session, 401 재로그인 기존 존재). 즉 steady-state는 ping으로 해결, ensure_tokens는 long-gap 재연결 한정.
**M5 teardown.** start_ws_producers→ws_tasks: list[Task]를 run_production_paper_loop 스코프에 보관 → finally(현 :393-406 layer0/altdata cancel 뒤)에서 stop_evt.set()+일괄 cancel+`gather(*, return_exceptions=True)`. WSStreamClient.aclose()가 자기 child(ping/watchdog/reconnect)+websocket(aclose) 닫음. reconnect는 이전 recv task 교체·cancel(이중 recv 방지).
**M6 staleness/flap.** fresh 판정 전부 `time.monotonic()` 기준(venue ts 혼용 금지). fresh<10s < backoff cap 30s라 1회 재연결만으로 flap(WS↔bar, 특히 exit #2) → fresh 임계를 재연결 worst-case 위로 올리거나 hysteresis. backoff/heartbeat 대기는 `asyncio.wait_for(stop_evt.wait(), timeout=...)`로 통일(teardown 지연 0).
**빌드 순서 보정**: 단계1에 ws_common의 run_in_executor flush 계약 + asyncio.wait_for backoff 포함, 단계2(OKX) 착수 전 wss 스모크 검증 게이트, Capital ensure_tokens·OKX region·REST-only 전이까지 venue ws에 내장.
