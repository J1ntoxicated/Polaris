# LESSONS LEARNED

Last cleaned: 2026-04-08. Kept 15 most impactful patterns. Full history in git.

## 🔴 #78 Backfill NULL cascade — Harness 교훈 (2026-04-17)

Harness 가 `trades.status='open'` 585건 중 508건을 `UNKNOWN_BACKFILL` 처리하면서 `pnl_pct=NULL` 으로 둠 → 기존 orphan 122 합쳐 **629 NULL row** → 다음 3 downstream 연쇄 crash:

1. `strategy/backtester.py tier1_replay` — `pnl -= _slip_bps/100` (None -= float) → **evolver daily tick 전체 dead** (Dev `201f0ff` fix)
2. `dashboard/operations.py:222` — `f'${_lp:+.1f}'` None format → **Operations dashboard crash** (Harness DB NULL→0 patch)
3. `position.py:195 from_dict` — `d.get("pnl_pct", 0)` 가 key+NULL 시 None 반환 → `ai_controller.py` sum/arithmetic 잠재 crash (Dev `8211132` 2-layer coerce fix)

**Rule**:
(a) Harness backfill SQL 작성 시 numeric column 은 반드시 `pnl_pct=0, pnl_usd=0` 명시 (NULL 금지)
(b) 모든 DB numeric read boundary (Position.from_dict / store.load_trades / dashboard.load_trades / backtester._load_trades) 에서 None coerce (Layer 1+2 defense)
(c) `exit_type='UNKNOWN_BACKFILL'` 는 SSOT filter 규칙 — 모든 downstream 집계 SQL 에 `exit_type != 'UNKNOWN_BACKFILL'` 필터 강제
(d) 교훈 감사 = **ops_audits #21 Backfill safety** 로 영속 편입

## Import / Deletion Safety
**#4 Relative imports missed by grep** — `grep -rn "from invasion.shared"` missed `from .shared.` (58 hits).
Rule: always use `grep -rn "MODULE_NAME" invasion/ --include="*.py" | grep import`

**#47 Paper vs Live behavior gap = catastrophic risk** (2026-04-13 Jin "라이브였다 생각하면 개끔찍")
Capital paper "spread fill" simulate가 close 거짓 success 응답 → bot이 close 성공으로 인식 → 단 broker side에 position 그대로 → 30min cycle 무한 churn (Estee Lauder 7+ cycle, $60 paper loss). Live broker는 진짜 reject 응답 → MSG-124 backoff + MSG-128 PARK 정상 작동 (paradox: live가 더 stable).
Rule:
(a) Paper에서 통과한 broker interaction logic은 **live behavior diff audit 의무**
(b) Live 전환 전 P0 checklist:
    - Broker reject response code (paper vs live diff)
    - Slippage simulation (paper에 강제 inject)
    - Commission cost (paper 0, live 진짜 fee)
    - PDT rule (alpaca live enforce)
    - Liquidity (specific stock pre-market 거래 가능)
    - Failure recovery (close fail / partial / canceled)
(c) 오늘 5h+ patch cycle도 paper 환경 limit — live면 token cost + 시간 + 진짜 손실 동시
(d) **Live readiness audit는 미장 안정 후 P0 task 분리** (별도 sprint, 충분 시간)

**#46 Harness ACK = runtime critical path verify 의무** (2026-04-13 Dev MSG-114 dashboard regression — Jin "매번 이래")
Dev MSG-091 commit 후 Harness ACK + restart 만 하고 runtime verify 안 함 → operations dashboard down 상태 Jin이 발견. AST/import smoke만으로는 부족 (정의 없는 변수 사용은 NameError가 import 시점 안 잡힘).
**패턴 (반복)**:
- MSG-067 regression-2 (hour_rows undefined) — Dev self-catch 후
- MSG-079 Phase 2 regressions 2회 (writer + downstream variable)
- MSG-114 dashboard `_mkt_closed` undefined — 본 case
Rule: Dev commit + restart 후 Harness ACK 전에 **runtime critical path verify 의무**:
1. **Dashboard render test** (변경 시): `python3 -c "from invasion.dashboard.sections.<X> import render; render(<minimum args>)"` 또는 `python3 -m invasion.dashboard.<X>` 실 launch + 5s 안 crash 확인
2. **Bot restart + 60s ERROR/Traceback grep** 추가 (현재 ERROR count만 확인 → grep 패턴 강화)
3. **변경된 함수의 minimum 1 호출 직접 시도** (특히 schema/field 변경 시)
4. **Verify FAILED → Dev에 즉시 [URGENT-REGRESSION] MSG** + ACK 보류
"끝까지 처리" 원칙 — Harness ACK = "검증 완료" 약속이지 단순 통보 아님.

**#45 Harness Decision approve / 신규 모듈 제안 전 baseline grep + SQL 의무** (2026-04-13 MSG-109 + MSG-110 + MSG-111 동일 wake **3회 위반**)
**Case 1 (MSG-109)**: MSG-104 옵션 C → MSG-106 P1 (low_vol_short_block) 부작용 미점검 → Jin "Europe entry 0건" 발견 → Harness 가설 "Capital ws static" jump → Dev DB 38+ signals/h refute, 진짜는 본인 승인한 MSG-106 P1.
**Case 2 (MSG-110)**: Harness "신규 `market_hours.py` 만들어야" 제안 → Dev grep으로 **이미 존재** 발견 (`invasion/utils/market_hours.py`). TICKER_MARKET 누락만 fix.
**Case 3 (MSG-111)**: Harness "신규 `time_to_close()` 헬퍼 만들어야" + "TICKER_MARKET 매핑 누락 root-cause" 둘 다 제안 → Dev grep으로 **`minutes_to_close()` 이미 존재** + 진짜 root-cause = `get_group()` fallback이 OKX exotic 토큰을 stock 분류 (`entry.py:204 isupper && len ≤5 → stock`).
**같은 wake 1h 안에 3회 게싱** = urgency 압박 시 self-discipline 결함 패턴 명백 (MSG-109 가설 / MSG-110 모듈 / MSG-111 헬퍼 + root-cause 둘 다).
Rule:
(a) Decision approve MSG 발송 시 부작용 KPI 명시 + 1h 후 self-audit SQL 의무
(b) **신규 모듈/함수 제안 전 `grep -rn "module_name" invasion/` 1회 필수** — 이미 존재 시 위치만 알려주기
(c) Escalation MSG 발송 전 baseline DB SQL 1회 (signals/candidates per ticker per hour 등)
(d) Urgency 압박 시 verify 단축 금지 — 잘못된 MSG가 더 큰 시간 낭비
**Harness Decision/제안도 Triple-Perspective verify 영역**. Dev refute가 시스템 self-correction 정상 작동 — 환영.

**#44 feat() 시 grep-proven consumer 증거 필수** (2026-04-13 MSG-070 B `78b63aa` → MSG-086 revert `0a9e180`)
`Position.regime` 추가 시 "향후 close-event/portfolio_state.json reader 위해" 계획으로 wire. 실제 consumer 구현 없이 2 session 방치 → Harness architecture audit MSG-086 에서 dead-write 지적 → Dev revert.
Rule: 새 필드/함수/feature commit 前 **reader grep ≥1** 증거. 증거 없으면 consumer 구현을 같은 commit 또는 즉시 후속 commit 에 포함. "계획된 소비자" 는 증거 아님.
Harness ACK 시에도 동일: feature 승인 전 consumer 존재 확인 의무 (#42/#43 writer/reader 독립 grep 과 자매 규칙).

**#43 Dead-code 제거 시 "변수 자체" 도 downstream consumer** (2026-04-13 MSG-079 Phase 2 2차 regression, commit `d83b941` → `3c6219e`)
SELECT+INSERT block 제거 시 SELECT 결과 변수 `hour_rows` 가 같은 함수 내 `_learn_session_mult(hour_rows)` + `log_event(f"{len(hour_rows)}...")` 에 여전히 사용 중 → `NameError` 매 tick. #42 규칙을 자기 fix 에 미적용한 case (meta-failure).
Rule: block 제거 시 **produced 변수**도 consumer 추적:
```bash
# block 안에서 생성된 변수 V 추출 후
grep -nE "\bV\b" <file> | grep -v "^<block>"  # 블록 외부 사용처
```
Smoke test 강화: `python3 -c "import"` 로는 부족. scheduler tick / cycle 함수 직접 호출 (`from X import tick; tick()`) smoke 필수.

**#42 Writer grep 누락 — reader=0 ≠ writer=0** (2026-04-13 MSG-079 Phase 2 regression)
DB table drop 전 audit 시 `FROM/JOIN/SELECT` 만 확인하고 `INSERT` 쪽 누락 → `hour_stats` DROP 후 매 hour `hourly_stats.py:107` `INSERT OR REPLACE INTO hour_stats` 에서 `no such table` OperationalError. Non-fatal (try/except) 이지만 로그 clatter.
Rule: DB 객체 삭제 전 reader/writer **양쪽 독립 grep** 필수:
```bash
grep -rnE "FROM\s+$T\b|JOIN\s+$T\b" invasion/ --include="*.py"   # reader
grep -rnE "INSERT\s+(OR\s+(REPLACE|IGNORE|ROLLBACK)\s+)?INTO\s+$T\b|UPDATE\s+$T\b|DELETE\s+FROM\s+$T\b" invasion/ --include="*.py"  # writer
```
3-perspective review (Dev self + Harness arch + Ops empirical) 중 Dev self-audit이 runtime ERROR로 가장 먼저 감지 — Triple-Perspective 가치 증명.

**#7 Stale __init__.py after file deletion** — Deleted files but __init__.py still imported them.
Rule: `grep -rn "DELETED_MODULE" invasion/ --include="__init__.py"` after any deletion.

## Data Pipeline
**#8 Key name mismatch config vs code** — `regime_presets.json` stores `v7_min_signal_score`, code reads `signal_min_score`. Silently falls to defaults.
Rule: grep exact key in JSON when code reads config. Adapter normalizations must propagate downstream.

**#9 Missing fields in lifecycle** — Position.entry_signal missing "score", insert_trade missing "entry_strength", bus event using wrong key.
Rule: trace full lifecycle: creation -> storage -> read -> display.

**#37 Key name mismatch between layers** — pipeline sends "group", DB column "asset_group" -> NULL. groups.py returns "indices", strategy uses "index".
Rule: trace field names end-to-end: creator -> transport -> storage -> consumer.

## Trading Logic
**#12 Contrarian short vs strong uptrend** — Corn 26t WR 12%, Netflix 7t WR 0%. No trend confirmation.
Rule: contrarian signals need trend context — dampen, don't reject.

**#13 AI "always approve"** — S1 skip 0%, S3 reject 0%. CONTRARIAN OVERRIDE at score>=20 overrode ALL skips.
Rule: AI prompts need WHEN TO ACT and WHEN NOT TO act. Bias-only prompts = bias-only output.

**#24 Profit protection triple failure** — AMC peak +17.5% closed -66%. Trail too high, ProfitTaker too high, AI HOLD override blocked exit.
Rule: profit protection must be independent of AI. If peak > +10%, mechanical trail.

**#30 FundingSignal direction reversed** — Double negation in copysign formula. Scale saturated at +/-2.
Rule: always unit-test signal direction with known inputs before deploying.

## AI Controller
**#17 ai_skipped bypasses deeply negative** — BZ at -35.5% never got AI review despite severity.
Rule: force-review any position beyond -10% pnl regardless of skip conditions.

**#19 AI conf=5.0 = fallback** — BZ at -139%: AI returned HOLD conf=5.0 on every review. Neutral default, not real analysis.
Rule: consecutive conf 4.5-5.5 -> "AI degraded" flag -> fall back to rules.

**#27 CRITICAL trigger infinite loop** — AMC: 48 triggers in 6min = 24% of API budget. CRITICAL bypass didn't update _last_review.
Rule: ALL trigger types must update _last_review with minimum 30s cooldown.

## Exit / Safety
**#20 Kill switch: unrealized loss not counted** — Unrealized DD -25%, kill switch only checked cash equity.
Rule: kill_switch MUST include unrealized P&L. If DD > threshold, emergency close all.

**#28 TIME_STALE = #1 loss source** — 256 trades, -$1,442. No price feed -> stop loss blind -> 30min accumulating loss.
Rule: stale-position emergency stop if pnl < -0.3% AND no price update 5min.

**#35 elif branch order** — exit.py: `if max_pnl > 0.05` before `elif max_pnl > 0.1` -> 3x branch unreachable.
Rule: if/elif numeric thresholds must be ordered largest to smallest.

## Multi-Exchange
**#38 Ticker collision across exchanges** — MET = OKX crypto ($0.14) vs MetLife stock ($73). exit_monitor fell through OKX→Alpaca, got wrong price → +54000% PnL.
Rule: ALL price lookups MUST route by position.exchange. Never fall through to another exchange for the same ticker name.

## Debugging Discipline
**#26 MISDIAGNOSIS** — Reported "pnl_pct x100 bug" but it was our monitoring SQL doubling.
Rule: verify by reading source code, not just output. Check your own query first.

## Wave 1-4 Patterns (2026-04-10)
**#40 hasattr guard silent kill** — log_candidate_event hasattr guard left 3 call paths dead for months due to method rename. hasattr guards create silent fail. After any rename, grep callers exhaustively.

**#41 DPM vs exit.py opposite regime adjust** — Two modules applying opposite direction adjustments to the same position neutralize contrarian philosophy. Verify inter-module directional consistency before merging.

**#42 Shadow write flood** — regime_presets.json had 15/21 keys misnamed, causing dead writes. Preset file keys must exactly match preg() call keys — verify with grep.

**#43 adaptive_tuner no-op** — FLAT_TO_NESTED map mismatched live_config actual structure. Tuner was writing to non-existent nested paths. Tuner must use flat key direct access.

**#44 Signal agreement sign convention reversed** — long signal counted `v<0` as agreeing component. S3 min_agreement gate operated inverted. Unit-test signal direction with known inputs.

**#45 canonical_names.py SSOT duplication** — Two files diverged (Oil=CL vs WTI). Single SSOT only — never maintain two canonical name files.

**#46 Dashboard row count assert vs comment diverged** — assert 66 but actual render 64 → AssertionError on every render. Comments and asserts must be updated in same commit as layout change.

## Session 2026-04-10
**#47 paper.py group hardcoded "crypto"** — trade record always wrote group="crypto" for all OKX paper trades, including PLTR/TSM tokenized equity perps. Postmortem stats and strategy routing silently skewed crypto.
Rule: group field in trade records MUST use get_group(ticker) — never hardcode exchange name as group.

**#48 _SHARES ticker symbol gaps → OKX cache fallback → crypto mislabel** — PLTR, TSM, COIN, META etc. not in _SHARES whitelist, so groups.py fell through to OKX cache → classified as "crypto". Routed to OKX strategy instead of Alpaca.
Rule: whenever a new stock ticker is added to Alpaca universe, add its canonical symbol to _SHARES in groups.py in the same commit.

**#49 ProcessPoolExecutor PIPE FD leak in backtester** — BacktestRunner.run_parallel used ProcessPoolExecutor; spawning new processes in a long-running bot leaks file descriptors. After 1000+ trades, process ran out of FDs.
Rule: use ThreadPoolExecutor for in-process parallel work in long-running bots. ProcessPool only for truly CPU-bound, short-lived scripts.

**#50 Log spam without rate-limit causes noise and hides signal** — AI HOLD override logged every 1s, API recovered logged on every price tick, repeat_entry anomaly logged every 5s. Within 1 hour, 3600+ redundant log lines drowned real events.
Rule: any log inside a tight loop (1s-30s) MUST have a rate-limit guard (last_logged timestamp). Minimum cooldowns: 30s for AI decisions, 60s for API status, 5min for anomaly detection.

## Session 2026-04-12

**#67 TickHistory `_get_ticks` returned live deque → race with WS `record()`** — `_get_ticks`의 주석은 "snapshot under lock"이지만 실제로는 `self._data.get(ticker, deque())` 로 **deque 참조 자체**를 반환. 호출자는 lock 밖에서 iteration/슬라이스/`list()`를 수행하고, 동시에 WS feed 스레드가 `record()`에서 `append()` → `RuntimeError: deque mutated during iteration`. 24시간에 3건, 매 발생마다 `unified_scan` 한 tick 전체 실패 = 신호 손실.
Rule: 스레드 간 공유 컬렉션의 참조를 lock 밖으로 반환하지 말 것. lock 안에서 `list(...)` / `copy()`로 **진짜 snapshot**을 만든 뒤 반환. 주석과 실제 동작이 "snapshot"으로 일치하도록. Concurrent race 스모크 테스트(writer+reader 스레드)로 회귀 방지 검증.

**#66 except:pass bulk sweep deleted try body and left orphan `except`** — 이전 Dev 세션이 `except: pass` → `log_event()` 일괄 전환 중 `paper.py:_load_state` 안의 `for line in all_lines[-500:]:` 내부 `try: self.trade_history.append(json.loads(...))` 블록 본문을 통째로 삭제. `for` 루프 직하에 고아 `except json.JSONDecodeError:`만 남아 SyntaxError. 봇이 `_init_exchanges` 단계에서 죽어 재시작 실패.
Rule: 대량 치환(bulk sed/regex 스위핑) 후에는 **필수로** 수정된 각 파일에 `python3 -m py_compile <파일>`을 돌려 구문 검증할 것. 스위핑 완료 = commit 전 16개 파일 전부 py_compile pass. Dev 세션 종료 전 한 번 더 `python3 -c "import invasion.main"` 으로 import 체인 검증.

## Session 2026-04-11

**#51 _CRYPTO set missing 80+ OKX token symbols → stock fallback** — groups.py _CRYPTO lacked most OKX-listed token ticker symbols (BTC-USDT, ETH-USDT format not matched). Any ticker not in _CRYPTO fell through to OKX cache check, which could then misclassify or route incorrectly.
Rule: _CRYPTO set must include all OKX ticker symbols in the format the exchange returns them. When OKX adds new perps, update _CRYPTO in the same session.

**#52 computed.py auto-adjust violated Contrarian philosophy** — computed.py automatically raised min_score and reduced max_hold when recent performance was poor, making the bot defensively conservative during drawdowns — the exact opposite of Aggressive Contrarian (crisis = buy more).
Rule: auto-adjustment of entry thresholds based on recent performance is a defensive pattern. For Aggressive Contrarian bots, computed overrides must be explicitly disabled. Any "performance-adaptive tightening" must be reviewed against core philosophy before enabling.

**#53 DPM signal_reversed gate = direct Contrarian violation** — DPM kill threshold was active at 35, blocking entries when signal_reversed triggered. This is a second-order defensive filter that cuts contrarian entries during reversals — exactly when the strategy wants to enter.
Rule: defensive kill thresholds (DPM, ML meta filter, stagnant exit, early flat) must be reviewed for Contrarian alignment before activation. Default: OFF unless data proves they add edge.

**#54 Yahoo candle failures required multi-layer solution** — Yahoo candle fetch failures cascaded: CLOSE_WAIT FD exhaustion from per-request sessions, no cooldown for failed tickers, no fallback to Capital.com, no market-hours awareness (fetching candles when market closed returns empty).
Rule: candle data sources need 4 defenses in order: (1) shared session to avoid FD leak, (2) per-ticker fail cooldown (min 1h), (3) secondary source fallback, (4) market-hours skip before request. Any single defense alone is insufficient.

## Expert Review Session 2026-04-11

**#55 extreme_damp was Contrarian anti-pattern** — extreme_damp=0.70 reduced signal strength by 30% at extreme fear (score 80+). For Aggressive Contrarian, extreme fear is exactly when max bet should occur. Fixed: extreme_damp=1.10 (boost, not dampen).
Rule: any signal processing that reduces conviction at extremes contradicts Contrarian philosophy. Score remapping must BOOST, not dampen, at extreme fear/greed.

**#56 Kill switch equity source was OKX-only** — pipeline.update_equity() received only OKX paper balance. Capital.com and Alpaca losses did not trigger kill switch or affect sizing calculations.
Rule: kill switch, sizing, and all safety gates must use cross-exchange total equity. Single-exchange balance is never sufficient for multi-exchange bots.

**#57 max_daily_loss_pct=50% was effectively disabled** — 50% daily loss allowed = no daily loss protection. Fixed to 8%.
Rule: daily loss cap must be set to a meaningful value (5-10%). Never set safety parameters to extreme values that effectively disable them.

**#58 Router fallback referenced deleted exchange "ig"** — router.py fallback for unknown groups pointed to removed IG exchange. New groups would silently fail to route.
Rule: after exchange removal, grep all routing/adapter references and update fallbacks.

**#59 Evolver PARAM_BOUNDS diverged from live_config** — evolver min_score bounds (35-65) did not include live min_score=20. Evolution optimized a different parameter space than live trading.
Rule: strategy evolution bounds must encompass the actual live parameter range. Check bounds vs live_config after any parameter change.

**#60 Profit cap as hard exit cuts upside in crisis** — profit_cap=10% in crisis forced exit at 10% gain. Crisis recoveries can run 30%+. Fixed: converted to tight trailing (30% of normal trail distance) to protect + let winners run.
Rule: use trailing mechanisms instead of hard caps to protect profits. Hard caps impose arbitrary upside limits.

**#61 Safety param hot-reload + stale daily_start = false HALT** — Changed max_daily_loss_pct from 50→8 via live_config hot-reload. Bot's _daily_start_equity was set hours ago at higher value → instant 26.3% "loss" → HALT + close loop on market-closed Alpaca positions every 2s.
Rule: safety parameter changes that lower thresholds must be paired with bot restart (resets _daily_start_equity). Never lower safety thresholds via hot-reload on a running bot.

**#62 Multi-caller SafetyGuard equity desync** — safety_check.py used portfolio.total_equity() ($463K), pipeline.py used self._equity ($275K OKX-only). Same SafetyGuard instance → _daily_start_equity set to $463K → pipeline check with $275K → 40.6% false daily loss.
Rule: ALL callers of safety.check() must pass the same equity source. Use portfolio.total_equity() everywhere — never mix adapter-level balance with portfolio-level balance.

**#63 Adapter balance returns 0 when market closed → false equity crash** — safety_check.py queried cap_adapter.get_balance() and alpaca_adapter.get_balance() live. Market closed → returns 0 → equity drops from $463K to $275K → spurious SAFETY HALT. Fixed: use portfolio._balances (persisted, never 0).
Rule: never use live adapter balance queries for safety-critical equity calculations. Use persisted portfolio balances which survive market close.

**#64 save() full-dump cascading overwrite** — ParamRegistry.save() dumped ALL 362 keys to live_config.json on every psave() call. 10 modules call psave(). Module A sets max_concurrent=20; Module B sets min_score=25 + psave() → save() writes ALL keys including B's stale max_concurrent=8 → user's 20 is gone. Fixed: dirty-tracking — set() marks key as dirty, save() reads file, merges only dirty keys, writes back.
Rule: save() must be incremental (merge dirty keys only). Full-dump save + multiple callers = cascading overwrites. Operational params (max_concurrent) must not appear in regime presets.

**#65 Code inspection: hardcoded GROUP_PROFILES candidates** — exit.py _GROUP_PROFILES has per-group vol_mult/hold_mult/trail_mult hardcoded. These could be in ParamRegistry for auto-tuning but increases complexity. Decision: keep as code constants until sufficient trade data per group to validate tuning benefit. Monitor stock/forex hold_mult effectiveness.
Rule: hardcoded constants → ParamRegistry migration only when data proves auto-tuning value.

**#68 Bot restart — pgrep pattern trap** — MSG-041 Dev 자동 재시작 프로토콜 첫 적용 시 `pgrep "python3 -m invasion"`로 프로세스 확인했으나 macOS가 `Python.app` wrapper로 래핑해서 `python3` 리터럴이 ps명에 없음 (실제 `/Python.framework/.../Python -m invasion`). 5개 중복 프로세스 있는데 grep 0건 판정 → Jin에게 sandbox 제약이라 오보. 정답: `pgrep -f "python.*invasion"` (case-aware, path-agnostic).
Rule: macOS에서 Python 프로세스 검색은 `-f` + 케이스 유연한 regex (`[Pp]ython`) 사용. `grep 'python3 -m invasion'`은 fail.

**#69 Bot restart — start.sh GUI dependency** — start.sh는 AppleScript `tell application "Terminal"`로 대시보드 3개 창 열기. 봇만 재가동하려면 `nohup python3 -m invasion --headless > log 2>&1 &` 으로 충분. start.sh 실패 시 Jin 수동 개입 불필요, nohup + kill-before-launch 패턴으로 Dev 자율 재시작 가능.
Rule: 봇 재시작 = (1) `pgrep -f "python.*invasion" | xargs kill -9` (2) `nohup python3 -m invasion --headless > log 2>&1 &` — 3줄이면 충분.


**#70 TIME MAX off → neutral dormant 부작용** — 커밋 d5241df (TIME MAX default=0, winner TRAIL 위임) 후 open 491 중 429 (87%) 가 >6h dormant. stagnant 분기가 `abs(pnl)<0.1%` tight band 전용이라 slight-profit neutral (peak 0.25%, pnl +0.2%) 은 어떤 분기도 안 잡음. f99429e 에서 `neutral_timeout` 분기 신규 추가 (peak<0.5% + age>30min → close).
Rule: gate/분기 off/제거 전에 **기존 guard 가 빈 공간 안 남기는지** 코드 grep 으로 전수 확인. coverage gap 발견 시 동시 보강 후 배포.

**#71 JSON key 가정 실수 (strategy_id vs strategy)** — Harness 가 `strategy_direction_regime_block` triple 을 `{"strategy_id":"...","direction":"...","regime":"..."}` 로 지시했으나 실제 `family_utils.py:124` 코드는 `rule.get("strategy", ...)` 만 read. Ops 가 자율 정정하여 `"strategy"` 로 저장. 만약 그대로 apply 됐으면 9 triple 전부 inert no-op.
Rule: IPC MSG 에 JSON/dict schema 지시 전 **실제 read 코드 grep 으로 key 이름 확인 필수**. `feedback_root_cause_evidence_based` 와 동일 원칙.

**#72 Monitor pattern 과대 매칭 (false positive noise)** — 초기 Monitor pattern `ERROR|CRITICAL|KILL` 너무 느슨 → AI controller 의 "CRITICAL" routine log + "DPM KILL: signal_reversed" 정상 exit + CRYPTOPANIC HTTP 404 + WATCHDOG started 전부 매칭. 노이즈 폭주.
Rule: Monitor grep pattern 은 **action-required 이벤트만** 매칭. 정상 운영 log 제외. test: "if this crashed now, would my filter emit?" 와 "if bot is healthy, would my filter be silent?" 둘 다 YES 일 때만 ship.

**#73 Defensive param = 무조건 로스 (Jin 04-16 23:35 재확인)** — Harness 가 북극성 위반 수정하며 `direction_weight_*_short=0.5`, `min_score 50` 등 공격량 삭감 방향으로 push. Jin 지적 "디펜시브 하지마, 디펜시브 무조건 로스". 이후 rollback + **specific pattern 차단** (strategy_direction_regime_block) 으로 전환 — 공격량 유지하며 승자 표적 재선택.
Rule: 손실 대응 시 **aggregate 억제 금지** (weight dampen, score 상향, cap 하향). **표적 교체 + exit 비대칭** 만 허용. 조치 전 검증: ① 공격량 감소? ② winner 수익 상한? → YES 면 디펜시브.

**#74 Adapter update_pnl 누락 가능성 (alpaca zombie 424)** — 58th restart 후 neutral_timeout 127건 발동 확증 but alpaca long 424 >6h 여전히 open + 전부 peak<0.5% + pnl_pct MINUS. 의심: `alpaca_adapter.py` 에 `update_pnl` 호출 0건 (grep) → alpaca position 의 max_profit_pct stale. 조사 중 (Dev FIX-REQUEST).
Rule: 신규 exchange adapter 추가 시 `update_pnl()` wiring + portfolio.positions() inclusion + exit_cycle coverage 확증 필수. Adapter 간 기능 parity 체크리스트 운영.

**#75 neutral_timeout max_peak 너무 넓어 winner-killer** — Harness 가 MSG-REVIEW-FINDING-ZOMBIE 에서 `neutral_timeout_max_peak=0.5` 권고 (TRAIL activate 기준과 동일). 하지만 실제로 **TRAIL 발동 전 neutral_timeout 이 먼저 작동** → slight-winner (peak 0.15-0.5% 찍고 현재 plus) 포지션 30min 경과 시 pnl 무관 일괄 close. 01:43-01:44 indices/commodity/etf long 12건 동시 exit. Ops WR 87% → 56% 급락 trigger. Codex 2nd-opinion (Jin 04-17 01:50 flow 첫 사례) 로 정확 식별, fix = `neutral_timeout_max_peak 0.5 → 0.15`, `sec 1800 → 3600`.
Rule: "TRAIL activate 기준" 을 cut 기준으로 재사용하기 전에 **cut 분기 ordering** 확인 필수. TRAIL 보다 먼저 발동하는 분기가 winner 를 선제로 죽일 수 있음. `max_peak` 류 winner-보호 기준은 TRAIL activate 보다 훨씬 낮게 (0.1-0.2% 범위) 설정.

**#76 세션 섞임 race condition 누적** — 이번 세션에서 파일 "modified since read" race 다수 발생 (Ops 세션이 동시 edit). JSON key 가정 실수 (strategy_id vs strategy), neutral_timeout max_peak 판단 오류, Monitor pattern 과대 매칭 반복. Complex task (북극성 감사 / bus 감사 / 전략 리뷰) 와 routine (ACK / header 수정) 이 한 turn 에 섞이면서 context 오염 → 판단 저하.
Rule: Complex task 전 세션 정리 (handoff save + 선언적 scope). 세션 꽉 차면 (token 80%+ / race 3회+) 자율 `/clear` 준비. Complex turn = 해당 task 만, routine turn = 짧게 처리 후 종료. `.claude/policies/policy_context_hygiene.md` 준수.

**#77 Timestamp 반복 위반 (3세션 공통)** — Harness 가 13:50 이라고 추정 찍었으나 실측 14:46 (56분 오차). Dev/Ops MSG 도 미래 시각 기록 반복. `feedback_datetime_verify_always` 메모리 있지만 실 준수 X. Jin "시간이 트레이딩에서 얼마나 중요한데 내가 확인시켜야 하냐" 04-17 14:48 지적.
Rule: **MSG push / commit / log 직전 반드시 `date` 실측 실행 + 그 값만 사용**. 추정/반올림/미래시각 전면 금지. 3 세션 mode 부팅 절차에 "매 turn 시작 시 date 실측" 명시. 위반 = 거래 시간 오판 위험.

**#78 SIZE_CAP wire unreachable — exit_fsm 분기 split 누락** — exit_fsm_enabled=1 배포 후 SIZE_CAP 발동 0건. dev-wire-guardian 추적: SIZE_CAP block 이 `_fsm_routed=False` legacy path 안에만 있음 (exit_cycle.py:700-740). FSM 활성 시 코드 도달 불가. 5h ITEM-145 패턴 (mid-cap altcoin × max_hold drift) 9+ 발현 누적 → `58f112c5` mirror block 신설 (line 631-674) FSM 분기에. 즉시 BCH+BTC+USD/JPY 3 fire 재개.
Rule: legacy → FSM split 시 **모든 exit decision block** (SIZE_CAP, MAX_HOLD, DEMOTE_LOSS) 이 양 분기에 mirror 됐는지 grep 으로 전수 확인. `if _fsm_routed:` / `else:` 블록을 줄별로 대조. dev-wire-guardian 정기 sweep 으로 가드.

**#79 BZ × OKX × risk_off 9-fail 패턴 (ITEM-145)** — 3.5h 동안 mid-cap altcoin 같은 cell (BZ short / RIVER short / ETH long / GIGGLE short / TAO long / BCH long) 9 trade 전부 TIME exit -2% 부근. size_usd 5000+ × max_hold 1800s+ 결합 → drift loss. `cum_pnl_24h ≤ -$30` cell 단위 24h block (DEMOTE_LOSS) + size_usd ≥ 5000 force-close (SIZE_CAP) 두 wire 결합으로 차단. ITEM-145 처리 → INSIGHT-001 + ADR-001.
Rule: 같은 8-dim cell 에서 3+ 동일 exit_type 손실 발견 시 **cell 단위 lockdown** (cum_pnl_24h block) + **trigger 사이즈 cap** 결합. session 내 grep 1회로 패턴 검출 가능 → forensic 자동화 권고.

**#80 Vault git-excluded — auto-sync 용량 폭발 방지** — 2026-04-26 vault MVP 배포 시 hourly db_views_export → 8개 ticker entity / 30 strategy / 425 cell 매시간 rewrite. 만약 git 추적 시 일주일 후 .git/objects 수GB → push 불가능. Jin 즉시 `.gitignore vault/` 명령 + pre-commit guard. SSOT 는 sqlite, vault 는 view layer (regenerable from db).
Rule: hourly auto-sync 산출물은 **항상 git 제외**. git 추적 대상 = source of truth (코드 / preg / 설계 docs). regenerable view (snapshot, embedding cache, AST export) 는 derived → ignore. 결정 시점에 .gitignore 동시 commit.

**#81 cell_matrix `_normalized_score` `*100` regression (BUG-7 sibling)** — `_ticker_vol_pct` 의 `*100.0` 은 BUG-7 (2026-04-25) fix 시 제거됐으나 **같은 파일 line 196** `_normalized_score()` 의 `avg_group_pnl_std * 100.0` 잔존. avg_pnl 은 USD, pnl_pct_std 는 percent → `USD / (percent*100)` = 무의미 단위. Phase 0.5 `normalize_reward` 가 primary path 라 fallback 영향 제한적이지만 sparse 8d cell (ticker_baseline coverage <50%) 에서 score magnitude 100× 왜곡. dev-unit-contract-validator 2026-04-26 audit 발견.
Rule: BUG-7 같은 unit-mix 패턴 fix 시 **같은 파일 / 같은 변수명 인접 사이트 전수조사** 필수. `grep -n "pnl_pct_std" file.py` 로 모든 use site 검토. taxonomy `percent_already — consumer must NOT *100` invariant 위반.

**#82 Harness 자율 7-agent parallel audit dispatch 패턴** — 2026-04-26 vault MVP 배포 후 Jin "하네스 전체 오딧좀 해야하는거 아니냐? 볼트 한김에?" → 7 advisor (drift-detector / wire-guardian / harness-structure / log-quality / dev-audit / dev-refactor / unit-contract) parallel dispatch. 25분 내 종합 18개 finding (CRITICAL 1, HIGH 4, MED 6, LOW 7). 작은 deliberate scope + 절대경로 prompt + 병렬 실행으로 single-session forensic 효율 5x. session token 압박 없이 각 advisor 독립 context.
Rule: 시스템 전반 audit 필요 시 **agent 영역별 specialist 병렬 dispatch** 가 single agent sequential 대비 5x 빠름. prompt 에 절대경로 + commit hash + 구체 hypothesis 포함. 결과 종합은 Harness 가 priority + 북극성 정합 검증 후 fix 분배.

**#83 preg bound 전수조사 — `_mult` 로 끝나는 모든 preg 의 lower bound 검증 의무** — 2026-04-26 NEW-1 audit 발견: `_params_exit.py` 안 39 `_mult` preg 중 `fsm_trail_mid_mult` (0.4-2.0), `fsm_harvest_trail_mult_okx` (0.1-2.0), `fsm_harvest_trail_mult_cap` (0.1-2.0, default 0.5), `fsm_harvest_trail_mult_alpaca` (0.1-2.0, default 0.3), `profit_cap_regime_mult_neutral` (0.8-1.5) 등 lower<1.0 다수. hourly learner 가 자동 dampen 시 mult<1.0 sustained → `feedback_no_defensive_param_dampen` 위반 누적 (subsystem_preg_dampen alert 12회). `_registry_core.py` 에 invariant family 도입 권고 — `category="dampen-eligible"` 명시 + 자동 reject.
Rule: 신규 `_mult` preg 등록 시 **lower bound ≥ 1.0 default 의무** (amplify-only). dampen 의도 시 명시적 `dampen_allowed: True` flag + Harness sanction. `_registry_core.py` invariant 자동 enforce. mult chain 누적 위반 monitoring (subsystem_preg_dampen alert).

**#84 forensic 작업 시 vault write 의무 — chat-only = 휘발성 위반** — 2026-04-26 Track A NEW-2/3 sequential 진행 중 forensic 결과 (AI exit_advise -$6210 / disabled_engine_bypass / arch_gap 5 cells / cell drift) chat 만 남기고 vault 미기록. Jin trigger: "결정들 전부 볼트 기반 인거고 볼트에 기록하고 하는거지?" 자기 audit 인정. INSIGHT-005 즉시 작성 + lessons #84 도출. `vault_mandatory_protocol` section 3 (write 의무 매트릭스) 위반.
Rule: forensic / advisor dispatch 결과 / SQL query result / wire 분석 etc. 모든 진단 작업 종료 시 **즉시 vault write 의무**. chat 만 남기면 = 휘발성 = 다음 세션 손실. minimum: ITEM 추가 + INSIGHT 후보 + entity wikilink. self-check checkpoint: "본 turn 종료 시 vault 에 무엇 write 했나?" 0 = violation → `04_ops/self_inspection/{date}.md` 기록.
