# P5 Tick-Decision Engine — Build Spec (2026-06-03)

Jin 결정: **실시간-틱 의사결정 = 봇 1차 결정자, 바는 컨텍스트.** "딜레이 프라이스 의미없다 / 틱 기반 새로 / 실시간 들어오는 애만 거래 / 모든 상황 수익(양방향) / 확실히 거래 발생." 엑싯 호라이즌 = **신호별 하이브리드**(모멘텀→ATR-트레일, 평균회귀→빠른 스칼프). 불변: DEMO/PAPER · AGGRESSIVE(degrade never halt, no defensive throttle) · 거부키워드 0 · **9-stack 미접촉**(sizing=기존 `compute_size` 재사용, 새 mult 체인 X). 빌드=TDD + 적대적 리뷰(builder≠reviewer).

P5 = **결정 레이어**, P4(WS 데이터 파운데이션=`quote_writer.live_px`/`recent_ticks`, 3venue 라이브)는 **데이터 소스**. P5는 그 위에 얹힘.

## 데이터 소스 (재사용, 검증됨)
- `state.quote_writer.live_px(instrument_id) → (mid, last_ws_monotonic)` (0 DB).
- `fetch_regime(conn, venue, underlying_group_id) → str|None` (regime_state, per-tick flip).
- entry=`_real_open_fill(...)`/`real_okx_open_fill`, close=`close_specific_position(conn,state,position_id,...)`.
- 모멘텀 엑싯=`run_precise_exit(...)`(ATR/protected-BEP/loser-timeout, 단독 per-tick 호출 가능).
- sizing=`compute_size(conn,*,intent,risk_state,portfolio)→SizingFinal`.
- freshness 전부 `time.monotonic()` 기준(venue ts 금지), fresh<35s.

## 갭 2개 (먼저)
- **갭-a `quote_writer`**: `RING_BUFFER_DEPTH 30→600`(~60-120s) + 신규 `feature_window(iid)→list[TickSample]`에 `ts/bid/ask/mid/bid_size/ask_size/last_trade_price/last_trade_size/spread_bps` 전체 노출(기존 `recent_ticks` 4필드=G4 호환 유지). 링은 이미 `deque[QuoteTick]`.
- **갭-b `position_risk_state` persist on fill**: fill 후(`_production_pipeline`/`_production_close` persist_fill 뒤) open position risk row INSERT → `compute_size`의 PortfolioState 캡이 바인딩(over-deploy/churn fix). read 경로(`_read_portfolio_state`)는 이미 존재.

## 신규 모듈 (pure는 core/, ≤500 LOC)
- `core/ticks/features.py` (pure, hypothesis): `compute_tick_features(window, now_mono) → TickFeatures{velocity, accel, burst_z, ofi, aggr_flow, overshoot_z, spread_bps, n_ticks, age_sec}`. EWMA 1/3/10s. n_ticks/age 부족 시 `None` 피처(안전).
- `core/ticks/signals.py` (pure, 양방향, 각 `(TickFeatures, regime, cfg) → TickIntent|None`):
  - `burst_rider`: burst_z>θ_b ∧ aggr_flow 동조 ∧ spread<θ_s → side=sign(velocity)
  - `flow_pressure`: |ofi|>θ_o 지속 ∧ aggr_flow 동조 → side=sign(ofi)
  - `micro_reversion`: |overshoot_z|>θ_r ∧ flow 소진(역행) → side=−sign(overshoot)
  - `TickIntent{venue,symbol,side('long'/'short'),conviction(0-1),signal_id,ref_price}`
- `core/ticks/regime_gate.py` (pure): `active_signals(regime)→frozenset`(trend→burst+flow, range/chop→reversion+flow, crisis→reversion만), `direction_bias(regime)→int`.
- `core/ticks/config.py`: 임계/쿨다운/링깊이/fresh_sec/shadow flag. AGGRESSIVE 디폴트(민감).
- `scripts/_production_tick_engine.py` (impure 통합): `async run_tick_decision_loop(conn,state,stop_evt,*,okx_adapter,capital_session,alpaca_adapter,phase,real_roundtrip)`. ~500ms cadence:
  1. fresh WS 종목만(freshness 게이트="실시간 들어오는 애만").
  2. `feature_window`→`compute_tick_features`→regime_gate 필터→active signals→TickIntent.
  3. **쿨다운**(symbol×signal) + open dedup.
  4. RiskGate: `compute_size`(+갭-b 캡) + spread/cost 게이트 + 양방향(OKX/Alpaca long-only→short 드롭, Capital long/short).
  5. Executor: `_real_open_fill`. 진입 메타에 signal_id·signal_family(momentum/reversion) 기록.
  6. 매틱 엑싯: 모멘텀 포지션→`run_precise_exit`(ATR-트레일), reversion 포지션→신규 빠른 스칼프(flow 역전/마이크로 스톱/작은 R 타겟).
  - **M1/M2**: feature/signal eval=in-mem only, DB write=주문 시에만(거래 빈도, 틱 빈도 X). 무거운 write 오프로드.

## 스코프 / 공존 / 훅
- **Phase 1 = OKX**(24/7 최리치, long-only) 라이브 증명 → Phase 2 = Capital(양방향, 가장 활발)+Alpaca(RTH).
- 틱-적격 종목=틱-엔진 결정 독점, 바-전략 그 종목 비활성(이중거래 방지: open dedup + 진입 소스 태그).
- 훅: `run_production_paper_loop`에서 producer 옆 `asyncio.create_task(run_tick_decision_loop(...))`, `stop_evt` 협조 종료, finally teardown(layer0/altdata/ws 패턴).
- **shadow flag**(`TICK_ENGINE_SHADOW`): on=결정 로깅만(주문 X), off=라이브. 초기 1틱 shadow 검증 후 live cutover.

## 빌드 순서 (TDD)
1. 갭-a(quote_writer 링+feature_window) + 갭-b(position_risk_state persist) — 테스트 선행, 기존 G4/recent_ticks 회귀 0.
2. features(hypothesis) + signals(합성 시퀀스: 버스트/불균형/스파이크→intent) + regime_gate.
3. _production_tick_engine + 통합(executor/exit 하이브리드/쿨다운/freshness) + 통합테스트(틱 리플레이→결정/이중거래0).
4. run_production_paper_loop 훅 + teardown.
5. 적대적 리뷰(builder≠reviewer): 불변(DEMO/aggressive/9-stack/no-throttle) + M1/M2 loop-stall + 이중거래 + 양방향 venue 제약. 전체 스위트 green + mypy/ruff.

## 리스크/완화
WS 끊김→freshness 게이트 skip+엑싯 보호 폴백 · 진입 스팸→쿨다운 · 이중거래→open dedup+소스태그 · loop stall→eval in-mem·write 주문한정 · OKX short 불가→Capital만 short(Phase2) · edge 미검증→shadow 선행+소액 conviction sizing.
