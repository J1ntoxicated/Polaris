---
type: digest
status: active
date_created: 2026-05-07
tags: [digest, audit, p1, day8, vision-vs-impl]
related: [[_NOW]], [[active-autonomous-vision]], [[aggressive-bias]], [[ADR-003]], [[ADR-004]], [[ADR-005]], [[ADR-006]], [[ADR-008]]
---

# Polaris v2 — 전수 Audit (Vision vs Implementation, Day 8 P1)

## Trigger

Jin 2026-05-07 mandate: "전수 리뷰 한번 하자 대시보드는 돌아가고 있으니 전수 리뷰 한번 해봐 이게 지금 맞게 된 구조인지 내가 원하는게 맞는지." 새 Claude session 진입 → Vision (clean slate 2026-05-06) vs Day 8 production wire 정합 audit. 24h paper loop PID 26451 가동 중 (kill X, file 변경만).

## Method

1. Vault charter (active-autonomous-vision / aggressive-bias / north-star) read.
2. Production wire read: `polaris/scripts/{ignite_p1, production_paper_loop, _production_layers, _production_pipeline, _production_close}.py`.
3. Per-gate agent code: `polaris/core/pipeline/agents/{universe_scanner, signal_validator, pre_entry_watcher, entry_sizer, position_monitor, adaptive_exit, post_trade_reflector}.py`.
4. DB live snapshot (PID 26451 2h13m elapsed): fills / gate_events / cell_matrix_p0 / regime_state / positions / bars.
5. ADR cross-ref + 거부 키워드 sweep (charter + ADRs + code).
6. Memory feedback file existence verify.

## A. Architecture 정합 — **PARTIAL**

8 layer 모두 file 존재 + Day 8 production wire 적용:
- L0 dynamic universe (refresh OKX 5m / Capital 10m, focus 30) ✓
- L1 canonical bars + baseline ingest per-tick ✓ (bars=34735)
- L2 8-gate orchestrator G1-G8 ✓
- L3 T4 sizing + cold start CS-3 + cell routing ✓
- L4 cell_matrix_p0 4-dim (124 cells, 9 eligible n_eff≥20) ✓
- L5 LearnerScheduler 3 P0 (session/regime/max_hold, 16 snapshots) ✓
- L6 dirty-mark + regime flip 2-consec ✓ (4 regimes incl. 2 crisis)
- L7 AllocatorFence + idempotent order_keys + record_fault ✓ (2404 reservations)

ADR patches 적용:
- ADR-005 top ×1.5 ✓
- ADR-006 EWMA 7d / warmup 5-19 / quartile gate ≥20 ✓
- ADR-008 strategies all signal-generator only (def 1, generate_raw_signal 만) ✓

**Gap**:
- **F1 (P0)**: G6 (position_monitor) + G7 (adaptive_exit) = pure Python (model_used="python"). Vision §2 + §7 ("Position Monitor — Sonnet" / "Adaptive Exit — Sonnet") 위반. 26451 30min 동안 G6/G7 = python 260/260 회 (gpt 0회). _NOW Day 9 backlog 상태 그대로.
- **F2 (P0)**: G6/G7 entry 시점 1회만 호출 (production_pipeline.py:391-419). 5초 tick 마다 재호출하는 wire 없음. Live recalc cycle 은 mark_dirty 만 — gate 재호출 X. Vision §5 "mid-trade strategy swap + Adaptive Exit AI winner 길게" 사실상 dead code.
- **F5 (P1)**: A3 supervisor TaskGroup wrapper 미 wire — `asyncio.gather(return_exceptions=True)` 만 사용. record_fault 는 wire ✓. 부분 적용.
- **F6 (P1)**: orders/signals/quote_ticks DB = 0 rows. fills 만 persist. 디버깅 + L4/L5 cross-ref 약화.

## B. Aggressive Bias 일관성 — **PASS**

- 거부 키워드 sweep: vault charter / ADR / 30_components 모두 anti-pattern documenting hits 만 (real-money 보수 논거 무효 / regulatory cap 거부 / professional risk 거부 / monthly review 거부 등 명시 reject).
- 코드: `_gpt_client.py` line 101 = "real-money safety bias and over-KILLs" (G3 prompt fix 의 reject context, 정상). `signal_validator.py` line 13 = "no defensive throttle on PASS rate" (positive bias 명시).
- Hard cap = headroom min() 1회 (T4 cell mult clip-전 + tier amplifier preserve) ✓.
- Drawdown 자동정지 / daily target hard limit / KPI 기준 자동 비활성 전환(당시 설계 후보) / regime 기준 자동 감속류 — 모두 X (미도입 확인).
- Strategy 자동 비활성 전환 X (Jin manual only, learner_blocks 1h auto-unblock).

## C. DEMO Unlock 모든 영역 — **PASS**

- G3 prompt: `make_system_prefix` DEMO/PAPER 명시 → KILL 44%→9.8% / PASS 32%→80% (5min post-restart).
- 모든 gate prompts: DEMO unlock 적용 (signal_validator + universe_scanner + pre_entry_watcher + post_trade_reflector 모두 GPT migration 시 reroute).
- Cold Start CS-3: 6%/7% (demo aggressive — north-star 정합).
- Vault: `north-star.md` "real-money 보수 논거 무효" / `aggressive-bias.md` 거부 anti-pattern 6건.
- Dashboard v1: row 1 banner ` [DEMO·PAPER] ` 표기 + WARNING+BOLD color.

## D. Production Reality vs 의도 — **FAIL** (G6/G7 + Capital + PnL)

- **D1 G6/G7 always HOLD**: F1+F2 의 직접 결과. 9682 HOLD / 0 EXIT_NOW / 0 ADJUST_EXIT / 0 SWAP_STRATEGY. close path = `close_oldest_with_real_pnl` (oldest 1개씩, AI 결정 무관). positions_open=1707 누적 backlog.
- **D2 Capital fills 거의 0**: total 10 (session_breakout 6 + xau_indices 2 + smoke 2). fx_breakout_basket = 0. Capital focus 10 instruments + bars 정상 ingest. 원인 = 1m bar 에서 Donchian 40 / ADX>20 / 4-sym session ATR×1.5 break 조건 매우 드물게 만족 (지표 timeframe mismatch — 전략 spec 은 1H/5m 인데 production loop bars limit=240 이지만 generate_raw_signal 호출은 1m focus). 7 strategies 의 timeframe assumption 검증 필요.
- **D3 PnL 음수 회귀**: pnl_close_total = +$1885 누적 / pnl_last_1h = **-$839** (PID 9417 -$569/1h7m → PID 26451 G3 fix 후도 -$839/1h). G3 PASS 80% over-correction 가능 + close path AI 미작동 (D1) → noise close 누적.
- **D4 Mid-trade swap 0 발생**: SwapCandidate 가 from_strategy_id == to_strategy_id 로 evaluate (production_paper_loop.py:177-184 = self-swap stub). 진짜 candidate generation 로직 미 wire. _NOW X3 backlog 그대로.
- **D5 G8 phase="P0"**: production_paper_loop default phase="P0" → G8 deterministic Python template만, GPT lesson generation 미발동. ai_lessons=726 = template generation. P1 phase upgrade 필요 (CLI flag 또는 ENV).

## E. Vault + Memory 정합 — **PASS**

- 영속 원칙: feedback_aggressive_always_profit / feedback_no_defensive_param_dampen / feedback_no_block_filter_architecture / feedback_flow_not_block 모두 memory 존재 ✓.
- vault/00_charter (5) / 10_decisions (8 ADR) / 20_strategies (7 spec) / 30_components (8 layer spec) 모두 backlinks ≥2.
- vault_lint 199→0 issues (P1 wave 1 완료, _NOW 기록).
- log.md 261 dupe 정리 ✓ (P1 wave 1).
- _NOW Tier 0 latest update 2026-05-07 + Day 1-7 + cumulative review + Day 8 P0 list.

**Minor**:
- F8 (P2): vault/log.md ignite_p1 bootstrap 14회 중복 (10:04, 10:06, 10:08 등 동일 분 multi-restart) — log noise. Day 9 dedup hook 후속 backlog.

## F. Dashboard v1 — **PASS**

- 220×55 trader-grade 10 panels: header DEMO·PAPER + equity / exposed / uPnL / Daily / DD / Sharpe / Open / Cells / Focus + sparkline + 6 positions + 7 strategies + 8 gates funnel + 5 top + 5 bottom cells + 4 regimes heatmap + 2 closed trades + 3 learners + GPT $/h + alerts.
- start_dashboard.sh target = `polaris.scripts.dashboard_v1` (line 12 명시).
- snapshot.py 943 LOC + render.py 588 LOC + ansi_palette.py 245 LOC.
- v0 deprecation 명시 X (dashboard_v0.py 449 LOC 잔존, 삭제 또는 archive 필요).

**Minor (P2)**: G6/G7/G8 panel breakdown 추가 (model_used="python" vs "gpt") = F1 가시화. 현재 gate funnel decision 만 표시 (PASS/KILL/HOLD count) — model_used 컬럼 없음.

## G. GPT Migration 정합 — **PASS**

- _gpt_client.py: GPT_P0_MODEL="gpt-5-mini" / GPT_P1_MODEL="gpt-5.5" / reasoning_effort=minimal default / max_completion_tokens / Anthropic-shape wrapper.
- 호출 site 8 files: universe_scanner / signal_validator / pre_entry_watcher / position_monitor (P1만) / adaptive_exit (P1만) / post_trade_reflector / entry_sizer (Python) / strategy_signal_gen (Python) — 모두 _gpt_client 통과.
- recent 30min gate model_used: G1=gpt 467 / G3=gpt 474 / G4=gpt 188 / G6=python 260 / G7=python 260 / G8=python 75 — F1 P0 대로 G6/G7 P0=Python 정상이지만 vision §2 와 충돌.
- API key never logged ✓ (sanitize 확인).
- secret manage `_gpt_client.py` line 130 — env-driven only.

**Caveat**: gpt-5.5 모델 ID 가 OpenAI 공식 list 에 있는지 검증 필요 — Jin verify 2026-05-07 기록 (line 54). 외부 회귀 시 `gpt-5.5-2026-04-23` fallback 도 wire (line 54).

## H. Workflow 정합 — **PASS**

- Day 1-7 + Day 8 cumulative review 모두 codex round 1+ R2/R3 review 적용 + APPROVE 후 merge.
- vault digest write: Day 8 = `2026-05-07_p1_day8_production_loop.md` 외 9 digests this week.
- _NOW Tier 0 갱신 매일.
- log.md chronological 1-line append 매 작업.
- 5-axis review (Day 8 cumulative coherence, codex CONDITIONAL PASS) ✓.

**Caveat**: ignite_p1 bootstrap log 14 dupe (F8) = process restart noise; debounce hook P2 backlog.

## Critical Gap List

### P0 (this week — Day 9 priority)
1. **G6 AI wire** (F1+F2): position_monitor.py P1 branch (`gpt-5.5` Sonnet equiv) + per-tick re-invocation while position open. ADR-004 spec 의 Position Monitor LLM (HOLD/ADJUST_EXIT/EXIT_NOW/SWAP_STRATEGY) 진짜 활성. Vision §2 + §5 + §7 정합.
2. **G7 AI wire** (F1+F2): adaptive_exit.py P1 branch + winner 길게 (proposed_stop_price farther 자동 채택). G6 EXIT_NOW 와 paired.
3. **Close path ↔ G6 EXIT_NOW link**: `close_oldest_with_real_pnl` 가 oldest pop 만 — G6 가 EXIT_NOW 결정한 position 만 close 하도록 join 변경. positions_open=1707 누적 해소.
4. **G8 phase="P1" default for paper loop**: GPT lesson generation 활성화. 현재 ai_lessons=726 = python template, real lesson 생성 미발동.

### P1 (next week — Day 10-12)
5. **A1 session × regime in T4**: sizing 에서 session_mult × regime_mult 곱 누락 (_NOW backlog).
6. **A3 supervisor TaskGroup wrapper**: `asyncio.gather` → `supervise_strategies` (ADR-007 spec) 로 변경. 1 strategy 예외가 다른 6 strategy 흐름 영향 X 보장.
7. **Capital signal trigger 빈도 조사**: fx_breakout_basket=0 fills. 1m focus 와 strategy spec 1H/5m timeframe assumption mismatch 확인. Day 8 production_paper_loop:241 `timeframe="1m"` 하드코드 — strategy.metadata.timeframe 별 분기.
8. **Mid-trade swap 진짜 candidate generation**: production_paper_loop._evaluate_swaps 현재 self-swap stub. Vision §5 "AI Position Monitor 가 더 적합한 strategy 발견" 으로 확장.
9. **D2 Capital P1 wave**: signal trigger / depth proxy / FX session 가속 → fills 분포 OKX 99.9% / Capital 0.1% 시정.

### P2 (backlog)
10. **F6 persist orders/signals/quote_ticks**: 디버깅 + L4/L5 cross-ref 강화.
11. **F8 ignite_p1 bootstrap dedup hook**: vault/log.md noise 감소.
12. **F9 dashboard_v0 deprecation**: archive `dashboard_v0.py` (449 LOC) 또는 명시.
13. **Dashboard panel: G6/G7/G8 model_used breakdown**: python vs gpt 가시화 (F1 monitoring 가속).

## Vision Deviation 요약

- §2 Per-Gate AI G1-G8: G6/G7 = Python only (P0 spec OK 였지만 P1 transition 미완 — Day 9 critical).
- §5 Live Recalc Self-Correction: dirty mark + regime flip ✓ / **mid-trade swap + adaptive exit AI** = 미완 (entry 1회 호출 후 dead).
- §7 Adaptive Exit AI: 모듈 존재 + can_widen_exit 로직 OK / AI 호출 0 / per-tick 재호출 X.

## Aggressive Bias 누적 위반

- 0 detected. defensive throttle / daily limit / 자동 비활성 전환 / dampen 모두 미 도입. F4 (D2 Capital 0) 도 block 이 아닌 timeframe mismatch (지표 trigger frequency 약함) — strategy spec 자체 변경 또는 trigger relax 후 검증.

## Day 9+ Recommendation

**P0 quad bundle**: G6 AI + G7 AI + close-path G6 link + G8 P1 phase. 한 PR 로 묶어서 codex R1 review → merge → 30-min smoke 검증. 24h loop PID 26451 kill X (file 변경만 → next restart 반영).

**검증 KPI**: G6 EXIT_NOW count > 0 / G7 ADJUST_EXIT count > 0 / G6 model_used="gpt" ratio > 50% / positions_open backlog 1707 → < 50 / pnl_last_1h positive 회귀.

## Codex External Review 추가 발견 (gpt-5.4 high reasoning)

본 audit 의 F1-F9 외에 codex 가 추가로 짚은 5 건:

- **F10 (P0)**: `production_paper_loop:241` `timeframe="1m"` 하드코드 — 모든 strategy 가 1m bar 만 받음. metadata 가 5m/1H 인 strategy (TSMOM 1H / RSI-BB 15m / Donchian 1H / FX 1H / XAU 1H / Session 5m) 전부 timeframe mismatch. **Capital fills 0 의 진짜 root cause** (D2 보강). 1m focus + Donchian(40) → resample 부재로 trigger 거의 불가. Day 9 P0: per-strategy bar resample / feed 분리.
- **F11 (P0)**: production fan-out 이 `polaris/core/isolation/worker.py` (supervise_strategies SSOT) 미사용 — `asyncio.create_task` 직접 호출. ADR-007 Layer 7 spec 의 supervisor pattern 명시 미준수. 본 audit F5 과 일치 보강.
- **F12 (P0)**: Dashboard `STARTING_CAPITAL=10000` 하드코드 (snapshot.py:31) vs trading loop `EQUITY_USD_DEMO_DEFAULT=79000` (_production_pipeline.py:74) — equity/DD/exposure semantic **수치 왜곡**. trader-grade 신뢰성 약화.
- **F13 (P1)**: _NOW.md line 81-82 ADR-005/006 unchecked checkbox 인데 ADR files 이미 patched. _NOW stale state — operator confusion 위험.
- **F14 (P2)**: ignite_p1.py docstring (line 10-31) 이 여전히 "smoke_paper_loop" / "dashboard_v0" 언급. Day 8 production 전환 후 doc/runbook sweep 미완. CLAUDE.md / digests / _NOW 모두 GPT-era reality 로 통일 필요.

## Codex Verdict (외부 review 결론)

- **이게 지금 맞게 된 구조인지** (codex): "뼈대는 맞지만, 현재 실체는 'active autonomous'라기보다 'entry-heavy paper harness + weak live-management'에 더 가깝습니다."
- **Jin 이 원하는게 맞는지** (codex): "방향은 맞지만 핵심은 아직 아닙니다; Jin이 원한 건 살아있는 포지션을 AI가 계속 감독하는 구조인데, 현재 그 심장부(G6/G7/L6)가 아직 비어 있습니다."

**Per-section verdict (codex)**: A PARTIAL / B PASS / C PARTIAL / D **FAIL** / E PARTIAL / F PARTIAL / G PARTIAL / H PARTIAL.

본 Claude audit verdict 와 비교: 본 audit 가 PASS 로 본 C / E / F 영역을 codex 는 PARTIAL — _NOW stale + dashboard hardcoded equity + ignite docstring 이 추가로 잡힘 (more rigorous).

## Verdict

- **이게 지금 맞게 된 구조인지**: **PARTIAL** — 8 layer skeleton + production wire + GPT migration + dashboard v1 + aggressive bias preserve 모두 vision 정합. **G6/G7 AI 미작동 + close path AI link 미완** = vision §2 + §5 + §7 의 핵심 누락.
- **Jin 이 원하는게 맞는지**: **거의 맞다, 1개 큰 미완 존재** — DEMO unlock + 투트랙 + active autonomous + 자가 진화 + signal generator only + aggressive + GPT/Anthropic 분리 + CLAUDE.md mandate 모두 구현. **Adaptive Exit AI + Position Monitor AI = vision 의 differentiator** 인데 P0 stub 그대로 → Day 9 P0 bundle 후 진짜 Polaris 작동.

## Cross-ref

- [[active-autonomous-vision]] §2 §5 §7 / [[aggressive-bias]] / [[north-star]]
- [[ADR-003]] 8-layer / [[ADR-004]] per-gate AI / [[ADR-005]] sizing / [[ADR-006]] cell matrix / [[ADR-007]] learner / [[ADR-008]] strategies signal-only
- [[2026-05-07_p1_day8_production_loop]] / [[2026-05-07_p1_g3_prompt_mockup_fix]] / [[2026-05-07_p1_dashboard_v1_redesign]] / [[2026-05-07_p1_haiku_to_gpt_migration]]
- production_paper_loop.py:391-419 (G6/G7 entry-only invocation)
- position_monitor.py:50-99 (model_used="python")
- adaptive_exit.py:103-156 (model_used="python")
- _NOW Day 9+ pending (lines 92-94)
