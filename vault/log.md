---
entity_type: chronological_log
entity_id: log
auto: true
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[_NOW]]", "[[INDEX]]"]
mode: meta
reviewed_by: jin
tags: [meta, log, append_only, polaris]
---

# log — Polaris Chronological Log

> Append-only. 모든 모드 작업 마감 시 1 줄 추가.

## 2026-05-03

- **Polaris bootstrap 시작** — auto_invasion_mk1 인수 결정. 모태 .env, .claude, docs/tasks/tools/agents/tests/scripts 카피 완료. vault는 새로 시작 (모태 vault 참조 read-only).
- **Codex 디베이트 1라운드** — 진단 검증. 알파 미검증 1차 / M1~M4 = 4 contract 표상 / vault=view not SSOT (lessons #80) / C·F 과소평가 4개 비판 수용.
- **Codex 디베이트 2라운드** — v2 통합 진단 95% 합의. 5% gap = vault 운영 규칙 명문화 + 40_components delta-only + Jin only 완화 3개.
- **Codex 디베이트 3라운드** — v3 모태 직접 read 검증. INSIGHT 35 / ADR 12 / agent 20 카운트 정정. 빠진 소스 8개 식별 (학습값 JSON 4 + ADR 4 + INSIGHT 4 + lessons 5 + WS URL fix). P6 Pure Core + P7 Property-based test 신규 추가. 95% 합의 → v4 보강으로 100%.
- **옵션 Y 확정** — invasion/spot 6,263 라인 인벤토리 (perp 198 + alpaca 277 + stock 200 + 잔재 115 라인 = SPOT-first 아닌 누더기). 코드 처음부터, 학습 노하우는 INSIGHT/lessons/JSON 19 소스로 보존.
- **Plan 승인** — `valiant-baking-sutton.md` Phase A~F. Phase A 시작.
- **A2 메모리 4개 신규** — feedback_code_review_codex_external / feedback_reasoning_superbrain / feedback_harness_4_modes / polaris_operating_model. MEMORY.md 인덱스 갱신.
- **A3 디렉토리 구조** — vault 7계층 + .templates + generated/components + .claude/hooks + .claude/agents/_DEPRECATED + docs/superpowers/{specs,plans} 생성.
- **A4 vault 핵심 콘텐츠 작성 중** — _NOW + INDEX + log + tag_taxonomy + 5 templates + 7 constitution + 5 ADR + 2 INSIGHT + _README들.
- **Phase A/B/C/D 완료** — vault 31 md / 2,290 라인, vault_lint 0 FAIL, 4 agent active + 20 _DEPRECATED, hooks 4 + git pre-commit symlink, implementation plan (1041 lines).
- **첫 commit `1cd3aba`** — feat(polaris): bootstrap v4 (310 files, 122,812 insertions).
- **Phase 0 완료 (Codex 리뷰 ADR-004 첫 사례)** — Round 1 80% → 5 fix 적용 → Round 2 93% 합의. Round 3 불필요. 잔여 gap 10개는 Phase 1+ plan. ([[codex_review_phase_abc]])
- **Phase 0 commit `24d8569`** — feat(polaris): Phase 0 verified [reviewed-by: codex(2 rounds)] (6 files, 256 insertions).
- **Phase 1 완료 (8 인수 소스 추출)** — 모태 vault에서 학습값 4 + ADR 4 + INSIGHT 4 + lessons 5 + WS URL 위험 1 = 18 노트 신규 작성 (INSIGHT-003~011, ADR-006~009, LESSON-001~005). _INHERIT_QUEUE archived.

- **Phase 2 — Backtest engine + HYPO-001 fast-fail** — 백테스트 인프라 작성 (Pure P6 + Property-based P7, 68 tests pass). HYPO-001 (RSI mean reversion BTC 1h) 직접 측정: 6 파라미터 × 4 timeframe 모두 expectancy < fee → archived. INSIGHT-013 + ADR-010 (Backtest+Paper parallel) + ADR-009 PERP counter 1/5. [[INSIGHT-013]]

- **HYPO-002 BB Breakout multi-ticker fast-fail** — BTC 1h/4h × 6 BB 파라미터 + ETH 1h cross-check 모두 expectancy < fee. Momentum도 mean-reversion과 동일 결과. INSIGHT-014. PERP counter 2/5. [[INSIGHT-014]]

- **HYPO-003 SMA Crossover 1d = first fast-fail PASS** — BTC 1d 8.5년 데이터 (3127 candles) SMA(10/20/50/200) 모든 파라미터 expectancy 양수 (+3.5% ~ +47%). SMA(50,200) hit 62.5%, Sharpe 0.475. 멀티 ticker (ETH/SOL/BNB) 일관. **결정적 발견**: 1d trend following으로 INSIGHT-007 fee 함정 우회. ADR-009 PERP counter 보류. ADR-011 (Promotion Gate Timeframe-aware) 신설. [[INSIGHT-015]]

- **HYPO-003 Walk-forward + 3-fold robustness 검증** — TRAIN 5년 exp +74%, TEST 3년 out-of-sample +23%, 3-fold (3 다른 cycle) 모두 양수 일관. Overfitting 위험 낮음, regime robust 입증. INSIGHT-016. **HYPO-003 Polaris 첫 viable strategy 확정** (BACKTEST 단계). 다음: Phase 2c 페이퍼 인프라.

- **Phase 2c paper infra + HYPO-003 multi-ticker (BTC/ETH/SOL) 시작** — paper layer (state/runner/logger) 작성, 87 tests pass, 3 ticker 첫 cycle (모두 HOLD trend-down).
- **Codex Round 1 review (78% 합의) + Look-ahead bias CRITICAL fix** — backtest engine i+1 next bar open 체결로 변경, Position.close already-closed 차단 추가. HYPO-003 재backtest = 동일 결과 (1d trend은 same-bar vs next-bar 영향 미미, robust 재확인). [[INSIGHT-017]]

- **Codex Round 2 (88% 합의) — 5 fix 적용** — Look-ahead PASS / Position.close guard WARN(race) / Daily loss limit WARN(state 단위) / Timeframe auto PASS / vault paper log FAIL→4_contracts narrative 보강. atomic rename for state save (race protection). 잔여 gap 5: sizing/stop-loss/dedup/partial/short. Round 3 권장.

- **HYPO-004 Donchian Breakout 1d = 두 번째 viable** — BTC Donchian(40/15) swing PASS (26 trades, exp +18%, Sharpe 0.31, MDD 41%), ETH Donchian(20/10) swing PASS (35 trades, exp +15%, Sharpe 0.33). Cron에 추가. Polaris alpha 다양화 시작 (SMA crossover + Donchian breakout 다른 메커니즘).

- **HYPO-005 MACD trend 1d archived** — MACD(12,26,9) BTC 1d expectancy +0.0009 fast-fail (whipsaw로 fee 잠식). Pattern 발견: 1d trend도 신호 빈도 낮아야 fee 통과 — SMA(50,200) 8 trades vs MACD 58 trades. viable 1d trend = long-cycle 신호만.
