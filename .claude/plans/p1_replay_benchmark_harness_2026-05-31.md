# P1 Build Spec — Deterministic Replay / Benchmark / Walk-Forward Harness (2026-05-31)

Wave 2 빌드 SSOT. 마스터 플랜 P1. 설계 출처: Plan agent (read-only 검증). 불변: DEMO/PAPER, AGGRESSIVE(throttle X), 거부키워드 0(**벤치마크=edge 입증 게이트, 시간-게이트(12주/90d) 금지**), 9-stack/sizing 불가침. **거동 0**(전부 read-only/오프라인 replay).

## 목적
SQLite bars 위에서 봇의 신호→게이트→sizing→exit 파이프라인을 deterministic replay → real 0.10% OKX taker fee 기준 성과 측정. 모든 후속 변경(P0 cutover 포함) 검증대 + go-live 게이트.

## Data reality (probed `data/polaris_live.sqlite`)
`bars(instrument_id, underlying_group_id, venue, symbol, bar_interval, ts[sec], ohlcv, notional_usd, vwap, bid/ask_close, spread_bps_close, source)`, PK `(instrument_id, bar_interval, ts)`. 깊이: **1H=155 inst / 04-11→05-31 ~50d**, 15m 89, 5m 70, 1m 159. 가용 horizon ~50d(1H). 작은 표본 → CI로 정직 표기(pass/fail clock 아님).

## Reuse seams (verified pure / conn-injectable — 재구현 금지, 라이브 코드 재사용)
- `build_real_market_view(venue,symbol,timeframe,bars,...)` pure 지표 (`_production_indicators.py`) — windowed bars `[:i+1]` 주입.
- `compute_real_regime_signal(bars)` **pure** label/strength/evidence. ⚠ `compute_and_flip_regime(conn,...)`는 stateful(regime_state write) → replay는 호출 금지; pure signal + in-mem 2-close confirm.
- `strategy.generate_raw_signal(MarketView)` pure.
- `compute_size(conn, intent, risk_state, portfolio, now_ts)` — **sandbox conn 주입** → cell/session/regime 읽기가 snapshot에만.
- `exit_engine.py` pure FSM math — 시뮬 tick마다 verbatim 재사용.
- `fees.py::real_fee_usd / demo_fee_usd` 그대로.
- cell seam: `resolve_routing_for_cell`(read) / `update_on_trade_close`(write) 둘 다 conn-scoped → 격리는 conn 선택만.

## Cell-pollution 격리
별도 `:memory:` SQLite를 replay 시작 시 live DB에서 `ATTACH`+`INSERT…SELECT`(cell_stats/regime_state/learner)로 seed. replay의 `update_on_trade_close`는 throwaway conn에만 write → 라이브 cell 불변(by construction). live DB는 절대 writable open 안 함. walk-forward 윈도우 독립 re-seed 가능.

## 신규 파일 (전부 ≤500 LOC; pure math는 core/)
- `core/replay/engine.py` — `ReplayEngine`. bar loop: ts마다 window → `build_real_market_view` → regime signal(in-mem confirm) → 각 strategy `generate_raw_signal` → `SignalIntent` → `compute_size(sandbox_conn)` → **next-bar open** fill(same-bar look-ahead 금지) + `real_fee_usd` 양 leg → 이후 bar마다 `exit_engine` FSM → close 시 `update_on_trade_close(sandbox_conn)`. → `ReplayResult`.
- `core/replay/models.py` — `ReplayConfig(instrument_ids, bar_interval, start_ts, end_ts, fee_model='okx_real', starting_equity)`, `ReplayTrade(entry/exit_ts, symbol, strategy, regime, side, notional, gross_pnl_usd, rt_fee_usd, net_pnl_usd, pnl_r, exit_reason)`, `ReplayResult(trades, equity_curve_real_fee_net, metrics)`.
- `core/replay/fill_model.py` — pure: entry=next-bar open + half `spread_bps_close`; round-trip `real_fee_usd`; pnl USD+R(`PNL_R_USD_DENOM=50`).
- `core/replay/equity_curve.py` — pure accumulator → `real_fee_net`(+optional `demo_actual`), dual-curve shape 재사용.
- `core/replay/sandbox_db.py` — in-memory snapshot seed; **유일하게 live DB 접촉(read-only ATTACH)**.
- `core/benchmark/baselines.py` — pure baseline equity curves.
- `core/benchmark/statistics.py` — pure Sharpe, Sharpe-spread, PSR, deflated-Sharpe.
- `core/benchmark/walk_forward.py` — purged/embargo split generator.
- `core/benchmark/gate.py` — pure 3-tier evaluator → `{relative, risk_adjusted, statistical}` + CIs.
- `scripts/run_replay.py` — thin CLI(config→engine→gate→persist read-model).

## 수정 파일
- `core/pipeline/agents/confidence.py` — `confidence_summary`에 `replay` block 병합(거동 0; read-only). `_nig_lcb` 재사용.
- dashboard `snapshot_models.py`/`snapshot_queries.py`/`render.py` — confidence/EDGE 탭에 replay/benchmark 필드.
- `storage/schema_ddl_*.py` — `DDL_REPLAY_RUNS`+`DDL_BENCHMARK_RESULTS`(read-model only; 라이브 트레이딩 절대 안 읽음).

## Interface
In `ReplayConfig`. Out `ReplayResult` — ordered `ReplayTrade[]`, `equity_curve_real_fee_net[(ts,equity)]`, `metrics{net_pnl, sharpe, max_dd, win_rate, profit_factor, turnover, fee_drag_real_r, n}`. baseline과 동일 clock(공유 start/end/interval).

## Walk-forward (anti-overfit, NOT 시간-게이트)
anchored/rolling **purged** split(bar index). params FROZEN이라 IS는 어떤 cell/strategy가 "live"인지 선택, OOS는 held-out edge 측정. IS↔OOS **embargo = warmup_bars + max_ttl**(leakage 차단). IS vs OOS spread 보고(큰 gap=overfit flag). split=윈도우 *count*(달력 게이트 아님) — 판정은 OOS edge, "주 경과" 아님.

## 3-tier benchmark (동일 bars·동일 real_fee_usd·동일 clock)
1. **Relative** — baselines 동일 bars: buy&hold(첫 bar 진입·hold·1 round-trip fee), naive TSMOM(N-bar 수익 부호, flip=2 leg fee), naive Bollinger(하단밴드 long/mid flat). 각 equity curve. pass=봇 net이 **모든 baseline 대비 Sharpe spread>0**.
2. **Risk-adjusted** — `Sharpe=mean(r)/std(r)·√periods`; `sharpe_spread=sharpe_bot−max(sharpe_baseline)`.
3. **Statistical** — `PSR=Φ((SR−SR*)·√(n−1)/√(1−γ3·SR+(γ4−1)/4·SR²))`(γ3 skew,γ4 kurt,SR*=0 default). `Deflated Sharpe`=PSR with SR* trial-inflated(`SR*=√Var(SR)·((1−ε)Φ⁻¹(1−1/T)+ε·Φ⁻¹(1−1/(T·e)))`). 입력: per-trade net-fee returns, n, skew, kurt, trial count T.

## Divergence guard
replay가 **동일** `compute_real_regime_signal`/`generate_raw_signal`/`compute_size`/`exit_engine`/`fees` import — 포크 0. replay 전용 코드는 driver(bar loop·fill·sandbox conn)만. parity test로 live↔replay 고정 — 고정 fixture에 둘 다 돌려 동일 SignalIntent/notional 단언.

## Dashboard (confidence/EDGE 탭, display-only)
run당: `net_pnl_real_fee`, `sharpe`, `sharpe_spread_vs_{bh_btc,bh_spy,tsmom,bollinger}`, `psr`, `deflated_sharpe`, `max_dd`, `turnover`, `fee_drag_real_r`, `n`, `IS_vs_OOS_spread`, 3-tier 판정 chip + 모든 점추정 옆 **CI**.

## TDD
1. Determinism: 같은 config→byte-identical ReplayResult ×2. 2. No look-ahead(property): entry 이후 bar 셔플/0 → entry 결정·가격 불변. 3. Fee: round-trip==2·real_fee_usd, net==gross−that. 4. Baselines: 단조 series buy&hold==close/open−1−round-trip fee. 5. Stats: hand-computed fixture, deflated≤PSR(T>1). 6. Cell 격리: replay 후 live cell_stats 행 불변(pre==post). 7. Walk-forward: embargo bar IS/OOS 양쪽 제외·index 미겹침. 8. Parity: shared fixture replay SignalIntent/notional==live compute_size.

## 정직 framing
짧은 OOS(~50d/1H) → 점추정 + 명시적 CI(NIG LCB+PSR); lower bound에서 유의 아니면 "edge present, CI wide"(pass 아님). overfit=walk-forward IS/OOS spread + deflated-Sharpe trial 보정. throttle·시간-게이트 없음 — 게이트=held-out bar의 edge 유의성, real-fee-net, 동일 clock.

핵심 파일: `core/sizing/engine.py` · `scripts/_production_indicators.py` · `core/cell_matrix/routing.py` · `core/economics/fees.py` · `core/pipeline/agents/confidence.py`
