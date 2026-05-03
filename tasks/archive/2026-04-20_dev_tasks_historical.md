# Dev 작업 큐 (Living Document)

Harness가 능동 관리. Ops/Jin/Dev 발견 사항을 Harness가 평가 후 여기에 추가/삭제/재정렬.

대칭 구조: `ops_audits.md` (감사 카탈로그) ↔ `dev_tasks.md` (작업 카탈로그).

## 관리 규약 (Jin 2026-04-13)

- **추가**: Ops `[TASK-REQUEST]` / Jin 지시 / Harness 판단 → Harness가 추가 (중복 평가 + 우선순위 지정)
- **삭제**: 완료 task archive 섹션으로 이동 / stale/deprecated 제거
- **재정렬**: ERROR spike / issue / deadline 기반 Harness 동적 조정
- **Dev 참조**: 매 wake 이 파일 읽어 다음 P0 선택
- **상태 추적**: PENDING / IN-PROGRESS / DONE / BLOCKED / CANCELLED / MONITOR / NOT-NEEDED

## 🔴 P0 (Critical, 즉시) — Active Only

### 🔴🔴 2026-04-17 20:25 ARCH-REVIEW (Harness + Codex 2nd-opinion)

| ID | Task | Status | 비고 |
|----|------|--------|------|
| ARCH-F1 | **[🔴 P0 북극성 직접 위반]** `engine.py:492-509` stock downtrend `score × 0.7` + `min_score` reject. 주석 "BUY opportunity" 선언 vs 실제 damping+reject 모순. Fix: damping 제거 + boost (`aggressive_contrarian_stock_dip_boost` preg 1.15 default) | PENDING | Opus. MSG-ARCH-REVIEW spec 참조. 24h observe |
| ARCH-F2 | **[🔴 P0 북극성 직접 위반]** `dpm.py:160-173` signal reversed → KILL. Codex: reversal 은 contrarian re-entry 기회. Fix: TIGHTEN downgrade, hard KILL 은 pnl<-2% AND structure fail 양조건 | PENDING | Opus. `dpm_reversal_default_action` + `dpm_hard_kill_pnl_floor` preg 신규 |
| ARCH-F4 | **[🔴 P0 NULL 3rd-order]** `position.py:195 from_dict` + `ai_controller.py` 의 pnl_pct/size_usd 등 numeric 필드 None-guard 없음. 오늘 backtester+dashboard crash 의 3rd 지점. 2-layer defense (position.from_dict + store.py load) | PENDING | Opus. integration smoke 필수 |
| ARCH-F5 | **[🟡 P1 adaptive 확장]** 12 high-ROI 키 (min_score_session × 3, direction_weight × 6, position_size_mult × 3) 정적 → adaptive_tuner Thompson Sampling. bound: min_score 15-60, direction_weight 0.8-1.3 (0.5 하한 제거 — 북극성), position_size_mult 0.8-1.5 | PENDING | Sonnet. adaptive_tuner infrastructure 기존 |
| ARCH-F7 | **[🟢 P2 IPC rot]** dev_to_harness 6642L / harness_to_dev 9273L / 합 27,679L. ACKED MSG 를 `tasks/archive/YYYY-MM-DD_<name>.md` 로 이동 + AGENTS.md 삭제된 claude_to_codex / codex_to_claude / harness_debate ref 정리 | **DONE 20:33** | Harness 직접 수행 — 27,679L→4,697L (83%↓), AGENTS.md stale ref 정리. `.claude/agent-memory/harness/codex_arch_review_2026_04_17.md` 영속 기록 |
| ARCH-F8 | **[🔴 P0 NEW]** max_concurrent enforce 파손 — live_config=200 / trades open=222 / ps live=299 / 70th 이후 28min 134 entry 폭주. `pipeline.py:484` `portfolio.positions()` 가 broker adopt 미포함 가설. Fix: positions() + adopted_count 통합 OR `max_concurrent_enforce_mode` preg ("memory"/"broker_sync"/"both") | PENDING | Opus. harness_to_dev MSG-ARCH-F8-NEW spec 참조. 24h empirical 이후 fix |
| ARCH-F9 | ~~family_max_allocation_pct default 0~~ | **REFUTED-CLOSED 22:40** | Harness 가설 틀림. `param_registry.py:553` default=30 확인. 0 fires = bot_positions family 당 60 미만 = 의도대로. Dev empirical 승 |
| ARCH-F10 | `time_exit_max_negative_pct=-1.0` 미트리거 | **DONE `73dcb6d`** | Root-cause: `exit_cycle.py:281-296` no-price neutral_timeout 이 loss_cap gate 우회 (alpaca no-price 경로). Dev fix: loss_cap 우선 판정 삽입, `TIME LOSS_CAP (no-price)` 신규 reason. 1 file +25 -6 |

### 🔴🔴 2026-04-16 23:00 Ops MSG-OPS-107 empirical 기반 Harness curated (BATCH 구조)

| ID | Task | Status | 비고 |
|----|------|--------|------|
| MSG-OPS107-P0-BATCH | **[🔴🔴 P0 통합]** Ops 32h42m 4,985 trades empirical 분석 (MSG-OPS-107 22:50) 기반 4-Component Dev spec. ~~A: STOP slippage fix~~ **CANCELLED 04-17 18:00** (Harness SQL 재검증: pnl_pct scale-bug, 실제 STOP 93% 는 -1% 정상 hard_stop, <-2% 47건 3.1%만 의심. empirical 근거 없음) / B: TIME symmetry fix → **OPS live_config 대체** 04-17 17:58 (time_exit_max_negative_pct -1.0 + early_flat 99999 × 2, pipeline.py 3-tier ladder 불요) / C: strategy-triple-block **DONE `15d3c45`** 22 entries LIVE / D(🟡 P1): score reverse (MSG-012 schema 선행) | **B+C DONE / A CANCELLED / D PENDING** | Harness empirical 재평가 + Ops 치환. D 만 잔존 |
| MSG-SILENT-DEATH-54 | **[🔴🔴 P0 LONGEVITY]** 4-layer defense: Layer 1 LaunchAgent KeepAlive (plist + Jin install 가이드) / Layer 2 signal handler 확장 (SIGHUP + atexit) / Layer 3 watchdog_thread.py (180s stall → os._exit(3)) / Layer 4 psutil memory/FD log. 총 5 file ~155 라인. `harness_to_dev MSG-SILENT-DEATH-54` spec 참조 | PENDING | 🔵 Opus. 33h 재발 가능성 ↑. Harness 직접 설계 (Codex 미호출). MSG-OPS107-P0-BATCH 와 병렬 가능 |
| MSG-185 | **[🔴 P0 CRYPTO CONCENTRATION]** family cap 30% 구현 | **DONE `ea6c506`** | 22:50 Dev commit, 55th 22:51:41 live 반영 (MSG-185-FOLLOWUP 동시 해소). Ops 30min post-measure 예정. dev_tasks archive 이동 |
| MSG-183 | TIME-EXIT 구조 손실 (단독) | **MERGED → MSG-OPS107-P0-BATCH Component B** | 통합으로 흡수, 단독 track 종료 |
| MSG-184 | SHORT BIAS (단독) | **MERGED → MSG-OPS107-P0-BATCH Component C** | 통합으로 흡수, strategy-triple-block 으로 형태 진화 |
| MSG-DATA-STALE-STATUS | **[🟡 P1 신규 23:00]** `trades.status='open'` 435건 stale (live 실제는 51개). Dev MSG-179-RECHECK 에서 발견. close-event write 경로 누락. `_close_position` 이 exit_type/pnl 만 update, status 미업데이트 의심. backfill SQL + `_close_position` 수정. 🟡 P1 — 실시간 중복 방지는 이미 2중 가드 작동 중이므로 긴급 아님 | PENDING | Dev MSG-179-RECHECK 증거 기반. MSG-070 A exit_type enum migration 과 schema 맥락 공유 |
| MSG-CODEX-P0-T1 | Composite score validity audit | **MERGED → MSG-OPS107-P0-BATCH Component D** (🟡 P1) | MSG-012 schema 선행 후 empirical audit. Jin 04-16 22:47 Codex 자중 정책에 따라 implementer 도 Dev (Codex 미호출) |
| MSG-SIZE-SPLIT | **[🟡 P1 batch] 파일 분할** — pipeline.py 1735 / main.py 1520 / providers_extended.py 1374 / store.py 1371 / okx/public.py 1168 / engine.py 1129 / param_registry.py 1073 / data_collector.py 1022 (8개 > 1000 라인). 순차 분할. behavior change 0 필수, `__init__.py` re-export 로 import 경로 유지. commit: `refactor: split <orig>` | PENDING | [.claude/docs/code_size_limits.md](code_size_limits.md). 8개 중 pipeline.py 먼저 (가장 비대 + 가장 버그 잦음). 각 분할 완료 후 `dev_to_harness.md [REVIEW-REQUEST-CODEX]` |
| MSG-179 | 중복 entry gate 복원 | **CLOSED-AS-INVALID** | Dev MSG-179-RECHECK (22:45) — 실시간 2중 가드 정상 작동 확증. 원인 오해. 대체 task = MSG-DATA-STALE-STATUS |
| MSG-SIZE-SPLIT | **[🟡 P1 batch] 파일 분할** — pipeline.py 1735 / main.py 1520 / providers_extended.py 1374 / store.py 1371 / okx/public.py 1168 / engine.py 1129 / param_registry.py 1073 / data_collector.py 1022 (8개 > 1000 라인). 순차 분할. behavior change 0 필수, `__init__.py` re-export 로 import 경로 유지. commit: `refactor: split <orig>` | PENDING | [.claude/docs/code_size_limits.md](code_size_limits.md). 8개 중 pipeline.py 먼저 (가장 비대 + 가장 버그 잦음). 각 분할 완료 후 `dev_to_harness.md [REVIEW-REQUEST-CODEX]` (Harness 가 Codex 호출, Jin 04-16 22:27 정책) |
| MSG-DRIFT | **[🟢 P2 정기]** Canonical Files drift check — `.claude/docs/canonical_files.md` + `.codex/docs/canonical_files.md` 실제 경로와 비교. Codex 와 monthly synthesis. 다음: 2026-05-01 | SCHEDULED | Harness 주도, Dev 는 grep 검증만 담당 |

### 이전 P0 (04-15 이전 — 토큰 부족 전 미해결, Jin 복구 후 순차)

| ID | Task | Status | 비고 |
|----|------|--------|------|
| MSG-109 | **[Jin 발견] Europe ticker 미활성화 root-cause** — Harness 가설 REFUTED, 진짜는 MSG-106 P1 부작용 + min_score cap 누락 | DONE `086383a` | 17:42 restart, low_vol_threshold_factor 7 group + min_score cap 확장, lessons #45 encode |
| MSG-110 | Closed market entry gate (TICKER_MARKET 40+ ticker) | DONE `c5ca7ec` | 17:53 restart, ASX/TSE/HKEX closed 정확 |
| MSG-111 P0 | B+C 결합 + crypto fallthrough fix (PRE_CLOSE_FLAT + CLOSED_MARKET_LOSS_CAP + entry.py crypto skip) | DONE `00650e0` | 18:04 restart, EXIT_CODE_MAP 추가, minutes_to_close 기존 활용 |
| MSG-111 P1 (Ops) | 3 stock forex 오분류 backfill (Estee Lauder/Global Payments/Novo Nordisk) | PENDING | UPDATE positions_snapshots + trades, portfolio_state.json 동기화 |
| MSG-175 | **[P0] anti_contrarian regime 조건 제거** — 현재 crisis+neutral만 차단, risk_off에서 crypto short 통과. 전 regime 무조건 차단으로 변경 | PENDING | pipeline.py regime 조건 삭제 |
| MSG-177 | **[P0] crypto_contrarian_swing short 차단 추가** — 100건 WR 44% -623 pnl. `_CRISIS_FAMILY_BLOCK`에 추가 | PENDING | MSG-175와 함께 반영 |
| MSG-178 | **[P1] session_breakout_london 세션 시간 + asset scope 수정** — (a) London 03:00 BST에 firing 중 (open 08:00) — session hour gate 필요. (b) forex 전용이어야 하는데 indices 52% / commodity 47% / forex 1%. strategy JSON에 `asset_group` 제한 or engine routing 수정 | PENDING | strategy JSON + engine.py 조사 필요 |
| MSG-180-OKX | **[🔴 P0 UNBLOCKED 18:14]** OKX 사실상 전면 차단 상태 — blacklist 74개 + regime filter + pre-signal gate 조합으로 288 candidates → 1-2 pass. Crypto long WR 62-65% 수익 전략이 진입 불가. **Fix**: (a) blacklist long edge ticker 복원, (b) regime filter crypto 과도 검토, (c) pre-signal gate 임계값 검토. **UNBLOCK**: Ops MSG-OPS-095 (13:28) RESTORE 5건 (SIGN/ZRX/VANA/SPACE/NMR) + bl 74→69 완료. Harness MSG-181 push 18:14 (Jin 직접 지시, 1-commit 통합 권고) | IN-PROGRESS | Dev MSG-181 ACK 대기 |
| MSG-179 | **[🔴 P0] 중복 entry gate 복원** — `pipeline.py:249` 주석 "removing the duplicate scan" 으로 gate 제거됨. SPY ×11, QCOM ×9, UNH ×7 등 동일 ticker 다중 open. 자본 집중 리스크 극심. **즉시 복원 필요**: ticker당 max open positions gate (예: 2-3). EU Stocks 50은 long+short 동시 = 자기 헤징 (북극성 위반) | PENDING | pipeline.py:249 duplicate scan 복원 + gate_matrix.py max_per_ticker |
| MSG-176 | Alpaca stop order ↔ broker_sync 충돌 (qty_available) | **MONITOR** | 55th 재시작 22:51 이후 14min 관찰 — 기존 qty_available 에러 **0건 관측**. Partial 해소 가능성. 단, restart 시 pending stop order 남아있을 수 있어 장기 모니터링 필요. 48h 재발 없으면 CLOSED 전환 |
| MSG-ALPACA-FRACTIONAL-GATE | **[🟡 P1 신규 23:05]** Alpaca 별개 ERROR pattern (MSG-176 과 다른 root-cause) — 55th 14min 관찰: (a) "fractional orders cannot be sold short" (UEC/SOFI short, 422) — Alpaca 는 fractional short 금지 (b) "asset not fractionable" (VSA/VRAX, 403) — 일부 asset fractional 제외 (c) "potential wash trade detected" (MRVL stop, 400) — day trading 빈도 + 같은 ticker 재진입 의심. **Fix**: `alpaca_adapter.open_position` 에 pre-check — asset.fractionable 필드 + side=short 조합 차단 (기존 assets 캐시 활용 가능). Wash trade 는 별도 order class (complex bracket) 검토. 30+ ticker 반복 실패 = 자원 낭비 | PENDING | 🟢 Sonnet. Alpaca `assets.get(symbol)` 로 fractionable flag 조회, cache + pre-filter. 14 파일 중 alpaca_adapter.py 만 수정 (~30 라인 예상) |
| MSG-132 | **[🟪 Jin 승인] 일반 close-fail PARK 확대** — `pipeline.py:1202-1212` `broker_sync.mark_close_failed(pos.exchange, pos.ticker, str(_e), portfolio=self.portfolio)` 삽입 → Alpaca/OKX/Capital 봇 자체 entry close-fail 시 `parked_backoff` flip + exit_cycle skip + dashboard dim | DONE `40c4d04` | 20:19 restart (23rd), 1 file +11 lines. 호출 지점 2→3. Static OK (circular 없음, concern 분리). Ops runtime verify 진행 (MSG-OPS-071). MSG-122~130 전선 MSG-132 로 완결 |
| MSG-134 | **[🟪 Jin 승인 "권고대로"][P0-CRITICAL] AI controller PARK bypass fix** — IBN case 실측: 20:19:42 parked_backoff flip OK, 20:20:13 `AI_CTRL DANGER` → `_close_position` 직접 호출로 pipeline:996 skip 우회, 20:20:17 AI KILL → dead letter 재진입 (attempt 1/3 재설정). `ai_controller.py` DANGER/CRITICAL trigger 및 `_execute_danger`/`_execute_critical` 에 `pos.strategy_id.startswith("parked")` pre-check 추가. 또는 `pipeline._close_position` 진입부에 `if self._is_parked(pos): return` 통합 (2-layer defense) | PENDING | **🔴 MSG-132 연장 — AI layer scope 확장**. 실측 증거 `ops_to_harness MSG-OPS-017` §1-1. Dev 이미 Ops 로부터 MSG-013 CC-FINDINGS 받음 |
| MSG-135 | **[🟪 Jin 승인 "권고대로"][P0] anti_contrarian scope 확대 Tier 1 (Dual-Track Synthesis §3 Tier 1)** — `invasion/signals/engine.py:727-735` `anti_contrarian_vol_short_crisis` reject scope 확대: (a) `indices_specialist` family × short × crisis, (b) `contrarian_commodity` family × long × crisis, (c) `volatility_spike` family × long × crisis. 7d 실측 +7.61 절감 / 43 trades. 외부 literature (Nagel 2012, Daniel-Moskowitz 2016) + 내부 empirical 양쪽 HIGH support | PENDING | **🔴 Dual-Track Synthesis**: `research_crisis_direction_synth_20260413.md`. 확대 시 label 변경 or 신규 reject key 추가 Dev 판단 (기존 `anti_contrarian_vol_short_crisis` 확대 vs `anti_contrarian_crisis_fit` 신규). 북극성 정합: 잘못된 방향 제거 = 공격 강화 |
| MSG-136 | **[🟪 Jin 승인 "권고대로"][P0] Winners 증량 Tier 4 (Dual-Track Synthesis §3 Tier 4)** — `whale_fade` long (WR 87.5% 8 trades) / `choppy` long (WR 77.8% 9 trades) capital allocation 증대. 구현 후보: (a) ParamRegistry `size_mult_whale_fade=1.3`/`size_mult_choppy=1.3` 신규, (b) Elo tournament 인센티브 강화 (force Top strategy size boost), (c) strategy-level `size_mult` 필드 도입. Dev 구조 판단 + 북극성 정합 (winners 집중 = 공격 강화) | PENDING | **🔴 내부 empirical strong + 외부 간접 지원** (contrarian family 맥락). sample n=8/9 작지만 winner 명확, 점진적 증량 권고 (size×1.15 → 1.3 단계적) |
| MSG-012 | **[Ops 요청][P1] composite.score LOG-REQUEST** — `trades` 테이블에 `entry_score` 또는 `composite_score` 컬럼 신규. adaptive_tuner_crisis min_score 튜닝 효과 사후 분석 필수. Ops MSG-OPS-016 §2 가설 A 에서 `trades.entry_strength` 만 존재 확인 → composite.score 유실 | PENDING | DB schema migration + engine.py 에서 기록 + DBWriter 확장. Dev 주도 |
| MSG-139 | **[🟪 Jin "엉 해줘"][P1] Dashboard strategy 섹션 개선** — Fix 1/2/3 | DONE `6b3c581` | 21:34 restart (26th), 1 file +70 -23, family_utils 2nd consumer |
| MSG-140 | **[🟪 Jin "하이브리드로해"][🔴 P0 북극성] anti_contrarian regime 확장 crisis→crisis+neutral** — `pipeline.py` post-strategy gate 1-line: `regime in ("crisis", "neutral")`. Ops MSG-OPS-024 실증 neutral WR 8.5% + MSG-135 +20.9%p 증명. 동일 3 family (indices_short/contrarian_commodity_long/volatility_spike_long) block. 21:59 Section 2 neutral 교차 WR 결과로 family 리스트 refine | PENDING | 즉시 단독 restart P0. family_utils 불변, caller 1-line. 하이브리드 — 후속 refine 대응 준비 |
| MSG-141 | **[🟪 Jin "회색 뭐가 뭔지 알수가 없잖아"][P2] Signal Radar Providers 컬럼 refactor** — `signal_flow.py:156` P_DIM + raw CSV slice → `_PROV_MAP` dict + `_prov_badge()` helper. 5-char 색상 badge (MmPtv, 활성=색+B / 비활성=dim 소문자). COL_PRV 20→8. Header fill text legend 추가 | PENDING | dashboard render only, entry/exit 영향 0. 다음 P0 batch 흡수. 팔레트 재사용 (신규 색 0) |
| MSG-156 | **[🔴 P1 Ops urgent-fix follow-up] `loader.py:113-116` list union/extend merge** — 현재 `dump[k] = v` REPLACE → list 타입은 union 또는 신규 key `*_extra` 패턴. okx_blacklist/cfd_untradeable/cfd_instrument_blacklist 등 list field 가 live_config override 시 wipeout 되는 위험 영구 차단 | PENDING | Ops MSG-OPS-035 60+EDGE 복원 사례 — 1min 만에 fix 했지만 미래 동일 패턴 재발 방지. dict/list 타입 분기 처리 권고 |
| MSG-113 | Adopt-AI disconnect adopt 시 force close | DONE `8e65dd4` | MSG-090 commit, 효과 미미 → MSG-115 unwind 예정 |
| MSG-115 | 100% Market hours 제거 — Phase 1만 완료 (MSG-114 SIMPLIFY 8 file -92 lines) | PARTIAL DONE `310758e`+`358ab95` | Phase B/C MSG-117 의존 |
| MSG-116 | Dashboard regression `_mkt_closed` undefined fix (lessons #43 강화) | DONE `7788449` | 18:39 restart, lesson #46 verify 5/5 PASS |
| MSG-117 Phase A | Pending Closure Queue 신규 | DONE `bb65a3f` | MSG-119 broker SSOT 전환 시 unwind 예정 |
| MSG-118 | adopt-block path → pending_closure 보강 | DONE `0bfa71e` | MSG-119 unwind |
| MSG-115 | 100% Market hours 제거 | DONE `3ae5d76` | 일부 유지 (PRE_CLOSE_FLAT + eod_flatten + minutes_to_close) |
| MSG-119 Phase A | broker_sync.py 신규 + 60s scheduler (ai_hold-only) | DONE `f4fcffe` | 19:15 restart, 18th, mock smoke OK, 기존 path DORMANT |
| MSG-121 | **[Jin] Candle fetch 좁히기** — Per-exchange 30 폐기, positions+signal only | PENDING | **🔴 P0 73% 감축, 1 commit 11-line 삭제** |
| MSG-123 | **[Jin ABC 다해] Phase B+C 통합** — AI evaluate_adopt + 구 path 폐기 (single batch) | PENDING | **🔴 P0-CRITICAL Jin 짜증 후 통합 결정** |
| MSG-121 | Candle fetch 73% 감축 (per-exchange 30 폐기) | DONE `9c141e4` | 19:21 restart, ~370 → ~99 목표 |
| MSG-122 | Adopted positions dim grey color | DONE `f8615a5` | 19:21 restart, render OK |
| MSG-070 A | exit_type enum migration + exit_reason 컬럼 (full) | PENDING | 대시보드 OTHER 해소, 대규모 schema |
| OPS-034 | regime-aware gate_stale_price_sec (neutral=10s, fear/greed 60s) | DONE `dce1726` | 17:12 restart, 5 regime param, gate_matrix.py:H11 |
| OPS-033-A1 | STOP BLIND stale fallback | **CLOSED** | post-fix phantom 0, Ops monitoring 이관 |
| OPS-033-A2 | Yahoo resolver (ALL-CAPS heuristic + "=" suffix) | DONE `7ad756c` | MongoDB/Ingersoll display name 분류 + CC=F/EURUSD=X 선물 표기 |
| OPS-033-A3 | score_below_min canonical | DONE `79bfea8` | 17:12 restart 함께 반영 |
| MSG-093 | US session 차등 `min_score_us=25`, `position_size_mult_us=1.2` | DONE `5e79993` | 16:40 restart, 6 신규 param (asia/europe/us × min_score+size_mult) |
| MSG-104 P0 | Anti-vol short crisis guard (VIX/UVXY/VXX/SVXY/XIV short crisis = reject) | DONE `76ec79f` | engine.py:694-698 wired, 16:46 restart |
| MSG-104 P1 | contrarian_commodity_* 9 strategy LONG-only (json direction enforce) | DONE | 9 json (g1/g8/g18/g53/g54/g55/g56/g57+본체) direction:['LONG'] 확증 |
| MSG-106 P0-1 | atr_pct=0 reject (entry.py:188 atr_unavailable) | DONE `d3787c1` | **🟪 Jin 발견 6분 fix → 9분 wire 검증** |
| MSG-106 P0-2 | atr_mult_indices=0.8 + atr_mult_etf=0.7 디커플 신규 param | DONE `d3787c1` | _ATR_MULT_KEY 자체 param, group 튜닝 자유도 ↑ |
| MSG-106 P1 | low_vol_short_block 자매 gate (대칭화) | DONE `d3787c1` | engine.py:614, asymmetry intentional 디자인 데이터 근거 변경 |

## 🔴 전략 방향 제한 목록 (Jin 04-14 "디테일하게 목록화" — 실측 기반 direction lock)

**원칙**: clean epoch(04-11) 이후 n>=3 + WR<35% + 누적 손실 → 해당 방향 비활성화. Evolver JSON `direction` 필드 또는 family-level gate.

### 🚫 즉시 차단 (HIGH — 누적 손실 심각)

| Strategy | Direction | n | WR | 누적 PnL | 조치 |
|----------|-----------|---|-----|---------|------|
| **whale_fade** | short | 13 | 30.8% | **-869.7** | JSON direction:["LONG"] (long WR 87.5% +수익) |
| **crypto_momentum_reversal_g215_ai** | long | 25 | 28.0% | **-475.6** | 이 variant만 long 차단 (다른 variant long은 WR 62-65% 수익) |
| **etf_specialist_g16** | short | 10 | 20.0% | -92.3 | short 차단 |
| **indices_specialist_g11** | short | 4 | 0.0% | -89.1 | short 차단 (WR 0%) |
| **indices_specialist_g11** | long | 3 | 33.3% | -81.6 | 관찰 (n 부족) |
| **stock_specialist_g18_g22_ai** | short | 6 | 33.3% | -128.4 | short 차단 |
| **stock_specialist_g18_g23_bayes** | long | 6 | 16.7% | -75.3 | long 차단 |

### ⚠️ 관찰 후 판단 (MED — n 적거나 borderline)

| Strategy | Direction | n | WR | 누적 PnL | 비고 |
|----------|-----------|---|-----|---------|------|
| contrarian_commodity_g57_bayes | long | 3 | 33.3% | -112.8 | n=3 부족, 관찰 |
| volatility_spike | short | 15 | 33.3% | -46.0 | 이미 _CRISIS_FAMILY_BLOCK 포함 |
| indices_specialist_g11_g23_bayes | short | 4 | 25.0% | -49.3 | g11 short 전체 차단으로 흡수 |
| contrarian_commodity_g8_bayes | short | 3 | 0.0% | -33.2 | commodity short 전체 패턴 |
| stock_specialist_g18_g22_ai | long | 6 | 0.0% | -12.3 | WR 0% 이지만 손실 작음 |

### ✅ 수익 전략 (보호 대상 — size 증량 후보)

| Strategy | Direction | n | WR | 누적 PnL |
|----------|-----------|---|-----|---------|
| crypto_momentum_reversal_g4_gauss | **long** | 23 | 65.2% | +332.8 |
| crypto_momentum_reversal_g3_gauss | **long** | 21 | 61.9% | +301.7 |
| crypto_momentum_reversal | **long** | 24 | 62.5% | +252.4 |
| crypto_momentum_reversal_g4_ai | **short** | 11 | 63.6% | +147.3 |
| session_breakout_london | **long** | 24 | 62.5% | +41.4 |

### 구현 방법 (Dev 참고)
1. **JSON direction lock**: 각 전략 JSON에 `"direction": ["LONG"]` 또는 `["SHORT"]` 추가
2. **Family-level gate**: `_CRISIS_FAMILY_BLOCK` 확장 BUT regime 조건 제거 (MSG-175)
3. **Evolver 방향 제한**: variant 생성 시 parent의 direction lock 상속
4. **anti_contrarian regime 제거**: MSG-175 — 전 regime 무조건 차단

### 핵심 발견
- **같은 family도 variant별로 방향 edge 다름**: g4_ai short +147 vs g215_ai long -475
- **family 통째 차단은 위험** — variant 단위 실측 필요
- **long이 전반적으로 유리**: crypto/stock/etf 전부 long WR > short WR

## 🟡 P1 (월요일 오픈 후 순차) — Active Only

| ID | Task | Status | 비고 |
|----|------|--------|------|
| MSG-071 B | Signal provider fires 집계 복구 (Active 0) | DONE (verify-only) | MSG-072+30 ticker fix 후 자동 해소, 코드 변경 0 |
| MSG-071 C | Provider 컬럼 표준화 → Sig Fire%/Trade Conv%/Win Rate | DONE `3840714` | UI label only, 다음 batch와 묶음 |
| MSG-070 A (스코프 재정의) | DB migration + dashboard backfill | PENDING | MSG-078 prefix fix 후속 full migration |

## 🟠 북극성 위반 감사 목록 (Jin 04-14 "다 잡아서 목록화" — 토큰 리셋 후 일괄 수정)

| # | 파일:라인 | 위반 패턴 | 설명 | 심각도 |
|---|-----------|----------|------|--------|
| NS-1 | `config.py:223-226` | profit_cap 하드캡 | major 3.0 / large 4.0 / mid 5.0 / meme 6.0 — 상승 잘라냄 (lessons #60 "trailing으로 대체") | HIGH |
| NS-2 | `config.py:140-141` | long/all_blocked_hours_utc | 시간대별 방향 차단 인프라 — 현재 빈 리스트지만 구조 자체가 방어적 | LOW |
| NS-3 | `config.py:213` | short_ls_max=2.0 | crowd 이미 short일 때 short 차단 — contrarian 위반 (crowd 반대가 원칙) | MED |
| NS-4 | `exit.py:331` | restart STALE/TIME 즉시 exit | restart 시 position 무조건 청산 — 수익 중인 포지션도 강제 종료 | MED |
| NS-5 | `safety_check.py:156` | SAFETY HALT | equity drop 시 전체 거래 중단 — 위기=기회 철학과 충돌 | MED |
| NS-6 | `paper.py:486-493` | hold-time trail tightening | 보유 시간 길어지면 trailing 좁힘 — winners 일찍 자름 | MED |
| NS-7 | `dpm.py:181` | HOLD tighten_pending_confirm | DPM이 profitable 포지션 trail tighten — 수익 방어적 축소 | LOW |
| NS-8 | `gate_matrix.py:252` | regime stale freshness bar | fragile regime에서 가격 freshness 강화 — 위기 시 entry 억제 | MED |
| NS-9 | `engine.py:608-623` | low_vol_long/short_block | 저변동성 방향 차단 — 저변동=기회 축적기인데 차단 | LOW |
| NS-10 | `param_registry.py:601` | KILL→TIGHTEN protection | profitable 포지션 kill 대신 tighten — 이건 OK일 수도 (조사 필요) | LOW |

### 처리 원칙
- **HIGH**: 토큰 리셋 즉시 Dev FIX (profit_cap → trailing 전환)
- **MED**: 코드 경로 조사 후 제거 or 반전 (contrarian 방향)
- **LOW**: 현재 비활성이거나 영향 미미 — 마지막 batch
- 각 건 수정 시 lessons.md #55 #52 #53 참조 (기존 contrarian 위반 패턴)

## 🟢 P2 (장기) — Active Only

| ID | Task | Status | 비고 |
|----|------|--------|------|
| MSG-105 | European indices pattern catch + ark startswith fix | DONE `901d987` | " 20"/" 25"/" 30"/" 35" 패턴 + Denmark 25 false-positive 정정 |
| MSG-056 A1 | label 중립화 (risk_on/off → fear/neutral/greed) + DB migration | PENDING | 큰 스코프, US 시장 안정 후 |
| MSG-043 AI Top 5 | Bull-Bear Debate / CVRF / FinMem / Drift Monitor | PENDING | research_ai_brain 참조 |
| MSG-047 B | operations.py 13-row layout 확장 | PENDING | UI |
| DATA P0-4 | FK mismatch close/fix 판정 | PENDING | Dev 조사 |
| DATA P0-5 | market_snapshots DROP vs retention | PENDING | |

## 📝 Pending Strategy Decision (Jin /debate 안건)

| ID | Topic | 근거 | Status |
|----|-------|------|--------|
| MSG-102 | Mixed 모델 dispatcher `_claude_or_gemini` (4 stage Claude+cache, proactive_exit Gemini) | DONE `0ef5d58` | claude-sonnet-4-5 hardcode (4.6 cache broken), anthropic_key fallback |
| MSG-038 | TDK = forex 오분류 자동 해소 (groups.py fix), 1-2h 후 SQL 검증 | DONE (auto via `1ee88d5`+`5ac48f2`) | TDK 신규 entry stock 분류 검증 trigger |
| MSG-040+077 | groups.py 30 ticker 재분류 (stock 12 + indices 3 + commodity 5 + etf 5 + bond as etf 1) | DONE `1ee88d5`+`5ac48f2` | smoke 전수 통과 |

## 🔍 Monitor / Not-Needed (변경 보류 결정)

| ID | Decision | 근거 | Re-eval Trigger |
|----|----------|------|-----------------|
| MSG-099 | MONITOR | session_breakout_ny n=31 borderline (lessons #52 임계 부족) | n≥60 누적 후 (US session에서 자연 증가) |
| MSG-100 | NOT-NEEDED | max_hold layering additive 의도대로 (1800 × hm_stock = let winners run) | exit pattern anomaly 발견 시 |

## 🚧 Blocked / Scheduled

| ID | Task | Block 이유 / 일정 |
|----|------|-----------------|
| MSG-069 C | Capital ASX catalog 확장 | Jin action 필요 (Capital 웹 UI 검색/watchlist) |
| MSG-079 Phase 3 | VACUUM (주말 03-06 AEST) | Harness 수행, Dev action 불필요 |

## 🧹 Idle Audit (Trigger: PENDING=0 + 30min 커밋 없음)

| Audit | Scope |
|-------|-------|
| DB 감사 | 테이블/컬럼 사용처 grep → 미사용 식별 |
| 파일 감사 | Dead code / legacy / orphaned files |
| Wire 감사 | define vs caller 불일치 함수 (lessons #44) |

## 📦 Archive (2026-04-13 Sprint Done)

### 13:00~14:30 (Initial sprint)
- MSG-067 Reopen gap policy `46bb97b`
- MSG-070 B Position.regime `78b63aa` (revert MSG-086 `0a9e180`)
- MSG-051 pipeline._regime_detector wiring `0ddd6ac`
- MSG-053 P0-2 Indices regime alias `edd7088`
- MSG-053 P0-4 FX/CFD cache-warm `47a1a32`
- MSG-059 P1 AI brain Phase 1 `fbb7444`
- MSG-059 Sonnet 4.5 downgrade `943d043`
- MSG-064 90s warm-up guard `c1f5890`
- MSG-073 #1 VIX 재분류 `a5abb56`
- OPS-032 Indices min_providers `2dcd093`
- OPS-033 exit_cycle NameError `210cdca`
- MSG-078 _EXIT_CODE_MAP prefix `28ba7d4`

### 13:30~14:00 (DB/File audit cleanup)
- MSG-079 Phase 1 backup files `6c9bbc9` (14.7MB 회수)
- MSG-079 Phase 2 5 unused tables `8fb0885` (27→22)
- MSG-079 Phase 2 followup1 hour_stats writer `d83b941`
- MSG-079 Phase 2 followup2 hour_rows SELECT `3c6219e`

### 14:30~15:30 (Architecture audit 1차)
- MSG-083 crypto RSI 0.10 + BB skip `1611e86`
- MSG-084 AI prompt PROBING `b431023`
- MSG-085 stock short F&G≥75 `023b35f`
- MSG-086 Position.regime revert `0a9e180`
- MSG-087 reopen_gap_pct dashboard `ad3f6bb`

### 15:00~15:30 (MSG-072 + MSG-073)
- MSG-072 Phase 1 yahoo_symbol_mapping `8b8582c` (24 entries + load_db_yahoo_symbols 복구)
- MSG-073 #3 session-aware max_hold `eb44a63`

### 15:55~16:30 (US session prep mega-batch)
- MSG-088 stock orphan cascade `1afec7f` (market-closed guard)
- MSG-072 Phase 2 AI Yahoo resolve `def0efa`
- MSG-073 #2 session×direction infra `594dc3b` (1.0 no-op)
- MSG-037/092 ai_calls.cache_* `ff7a087`
- MSG-094 12 stock_specialist regime expansion `f445ab8`
- MSG-095 cooldown 5 group `f73a739`
- MSG-091 positions_snapshots `61437c3`
- MSG-090 empty strategy_id (자가 해결 via f445ab8)
- Ops MSG-039 `_COMMODITY` XAG/XAU/XPT/XPD `8654b...` (groups.py:46-50)
- MSG-097 strategy_performance writer fix
- MSG-098 mean_reversion_bbands inactive
- MSG-096 Burry stock examples `11d4984` (NVDA/TSLA/SPY/QQQ)

## 📚 Lessons Encoded (이번 sprint)

- **#42** Writer/reader 독립 grep (MSG-079 Phase 2 regression)
- **#43** Dead-code 제거 시 produced 변수도 consumer 추적 (MSG-079 P2 followup)
- **#44** feat() 시 grep-proven consumer 증거 필수 (MSG-086 revert)

## Update Log

- 2026-04-13 13:15 — 초기 생성 (Jin 2026-04-13 "유기적 관리" 지시, Harness 공식화)
- 2026-04-13 16:32 — Active만 남김 + Archive 이관 + Strategy Decision/Monitor 섹션 신설 (Sprint 35min 11 commit DONE 정리)
