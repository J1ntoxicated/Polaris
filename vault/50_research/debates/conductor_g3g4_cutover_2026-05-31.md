---
type: debate
status: resolved
date_created: 2026-05-31
tags: [debate, conductor, g3, g4, shadow, cutover, regime]
verdict: PROCEED_WITH_CHANGES (G3 cutover DEFER · G4 cutover after tick wiring)
---

# /debate — G3/G4 per-signal GPT→deterministic 컷오버 (conductor P4/P5)

**질문**: shadow acceptance gate 통과했으니 G3/G4 GPT를 deterministic 규칙으로 컷오버할까?
**방법**: sequential-thinking(5) + vault/code/DB read + codex 2-round 적대검증. 봇 PID 24948, 3183 shadow events(6.3h, `polaris_live.sqlite`).

## 결정적 발견 (전제 falsify + dead-letter)
1. **전제 붕괴** — 설계시 G3 GPT KILL=8%(rubber-stamp, plan line 10). STEP5(`5b2aac6`, regime 동적화) 이후 **44% 라이브**(레짐별 chop 90/crisis 92/bear 88/bull 76%). GPT가 rubber-stamp→능동 regime-aware 필터로 전환 → "GPT 무용하니 싸게 대체" 전제가 G3에서 무효.
2. **두 deterministic 규칙 모두 production dead-letter** (codex 정정 + 직접 검증):
   - **G3** warm-KILL은 `quartile=='bottom'` 필요 → quartile 활성화가 `CELL_MIN_POOL_SIZE=20` **pool cardinality gate** 뒤(`payload_builder._cell_summary`+`classify_quartile`). warm cell 11개<20 → 전부 `quartile="cold"` → warm-KILL **0/3183 fire**.
   - **G4** `tick_window` G4 payload **0/4018건**(`_production_run_signal.py:225` `tick_window=[]` 하드코딩) → 빈 윈도우=fresh(`_shadow_rules.py:254`) → **무조건 PROCEED**. plan line 15 "30s tick-stream" 미배선.
   - ⇒ shadow "0 mismatch-toward-KILL"은 잘 캘리브가 아니라 **물리적으로 KILL 불가**여서. 지금 컷오버 = GPT 필터(G3 44%/G4 13%)를 deterministic 0으로 제거.
3. **mandate 긴장** — GPT 76-92% entry-KILL은 `flow_not_block/no_block_filter` 위반 성격(제거가 방향상 옳음)이나, 대체물 dead-letter라 naive 컷오버=무필터 진입(특히 chop=현 레짐 90%). flow_not_block의 핵심은 "모호하면 통과 + proven-loser만 narrow KILL".

## 코드/DB 검증 결과
- quartile gate = pool cardinality(≥20 warm cells), per-cell n_eff 아님 — codex 정정 채택(내 초기 root-cause 오진).
- `quote_ticks` 테이블 0 rows(미populated). `bars`에 bid_close/ask_close/spread_bps_close 존재 → tick source 후보.
- safety(deterministic KILL ⊆ GPT KILL) = 충족(0 flow-breaking) — 단 dead-letter라 trivially.

## Verdict — PROCEED_WITH_CHANGES (codex 2-round 수렴, reject 0)
- **G3**: GPT-live 유지, **컷오버 DEFER** — deterministic discriminator가 GPT edge를 실증 매칭할 때까지. 서두를 grounded 근거 없음.
- **G4**: tick source 확정(freshness 계약: bid/ask/mid/ts가 `ctx.started_ts`와 동일 clock basis) → `_production_run_signal.py:222` wire → re-shadow(cross/stale KILL·spread/drift flag 실제 fire 확인) → 컷오버.
- **G3 discriminator**(`n_eff≥5 + avg_pnl_r<0 + bottom-of-current-warm-pool`): **신규 named shadow feature**로 명명·shadow 검증. aggressive 위반 아님(n≥5 negative-EV=정직한 신호, thin-sample 제조 아님). cold=pass-through 보존.
- **acceptance 지표**(컷오버 전 필수): chop-churn(entries/closed-trade·loser-timeout closes·avg hold·same-sym repeat-loss) + **GPT-KILL counterfactual** + **fee/slippage-adjusted realized R**(gross로 가려진 churn 포착).
- **계속 진행**: G1(완료)/G6/G8(완료) 절감 유효 · P6 batch conductor 별개 진행 가능.

## 후속 (next build)
1. acceptance-metrics 모듈(read-only 분석, 위 지표) — 미래 컷오버 데이터 grounding.
2. G3 warm-pool-local discriminator = 신규 shadow variant(behavior 0).
3. G4 tick source + wire + re-shadow.
관련: [[ai_conductor_transition_2026-05-30]] · [[regime_layered_synthesis_2026-05-31]]
