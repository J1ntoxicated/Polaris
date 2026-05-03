# MODULE_REVIEW — `ExitEngine.check()` 442L 분할 Plan (F-N14)

> architecture_advisor 발견: `invasion/trade/exit.py:325-766` = **442 LOC in one method**.
> T2-0/T2-1 FSM landed 했으나 legacy check() retire 미완 → parallel exit paths 병존.
> Cross-review: **trading_advisor + security_advisor pending external** (batch에서 호출하지 않음).
> 본 문서는 저위험 incremental split + legacy retire 순서 제안.

---

## 1. 442L Phase Map

| Phase | 라인 | 역할 | 추출 난이도 | Risk |
|-------|------|-----|------------|-----|
| P0 FSM Delegation | 343-357 | `_is_fsm_enabled_for` 분기 + `_check_via_fsm` 위임 | — (이미 분리) | — |
| P1 Telemetry Closure (`_exit`) | 367-391 | log_event + bus publish | 중 (closure → method) | Low |
| P2 Exit Params Prep | 393-423 | `position.exit_params` merge + `_exit_keys` preg overlay + trail_activate recalc | 저 | Low |
| **P3 Regime Adjust** | **425-455** | **crisis/neutral/risk_on stop/flat/hold multiplier** | **저 (pure ep-mutator)** | **Low** |
| P4 ExitIntel Feedback | 457-472 | `_exit_intel.get_recommendation` 반영 | 저 | Low |
| P5 Catastrophic + STOP | 474-509 | catastrophic_loss_cap + vol-widened stop | 중 (returns early) | Med |
| P6 Guard Gates | 511-530 | `_mkt_closed`, min_hold, warm-up | 저 | Low |
| P7 Never-Positive Probe | 532-558 | SIGNAL_EARLY (`preg never_positive_probe_enabled`) | 저 | Low |
| P8 FSM Profit-Floor | 560-582 | FSM state harvest/protected floor | 저 | Low |
| P9 TRAIL (BEP + Full + Cap) | 584-657 | WS intel + bep/full trail + profit_cap tight-trail | 높 (WS intel side-effect) | Med |
| P10 Profit Taker | 659-666 | `_profit_taker_check` delegation | — (이미 분리) | — |
| P11 Partial Close | 668-672 | partial_close threshold | 저 | Low |
| P12 TIME Early Flat | 674-690 | early_flat_sec dynamic by signal strength + group | 중 | Low |
| P13 TIME Flat Kill | 692-705 | flat_kill graduated | 중 | Low |
| P14 TIME Max Hard Cutoff | 707-721 | `time_max_enabled` (default 0) | 저 | Low |
| P15 TIME Neutral Zombie | 723-739 | `neutral_timeout_enabled` dead-weight slot recovery | 저 | Low |
| P16 TIME Decay | 741-749 | profit decay | 저 | Low |
| P17 TIME Stagnant | 751-760 | stagnant_min band | 저 | Low |

**총 17 phases. ≤ Low 11개 / Med 5개 / High 1개 (WS intel, side-effect).**

---

## 2. Legacy vs FSM 경로 차이 (I-E1~E5 Ground Truth)

| Invariant | Legacy check() | FSM `_check_via_fsm` | Ground Truth |
|-----------|---------------|---------------------|--------------|
| I-E1 winner_no_time_loser | P12-P15 TIME 분기 혼재, max_pnl-gated | `ExitFSM.evaluate` state-machine | **FSM** (test_inv_E1) |
| I-E2 catastrophic bypass | P5 (L492-497) | `_Trg.SAFETY` bypasses guards | 양쪽 parity (FSM docstring L774) |
| I-E3 protected BEP lock | P8 (L570-581) | State `protected` floor | **FSM** (test_inv_E3) |
| I-E4 touched_profit_no_time | 혼재 (P12-17 각기 다른 gate) | State guard | **FSM** (test_inv_E4) |
| I-E5 open_time_loser_only | P7 never-positive + P12 early_flat | State `open` + time-bucket | **FSM** (test_inv_E5) |

**결론**: FSM = ground truth 5/5. Legacy 는 legacy_engine hook (`_vol_window_mult`) 재사용 중이라 즉시 삭제 불가.

---

## 3. Retire 순서 (Phased Rollout)

| Step | 작업 | 완료 조건 |
|------|-----|----------|
| S1 | **저위험 helper 추출** (P3 regime adjust) — behavior 보존 verify | `pytest tests/trade/test_exit.py` pass |
| S2 | P2 exit params prep → `_resolve_exit_params(position, regime)` | parity test (ep dict identity across legacy/FSM slice) |
| S3 | P1 `_exit` closure → `_emit_exit_trigger(position, reason, ...)` method, legacy + FSM 공유 | bus event schema parity |
| S4 | P7 / P14 / P15 / P16 / P17 → flag-gated helpers (already preg-gated, extract as methods) | preg toggle parity |
| S5 | P9 TRAIL full → FSM `TRAIL` trigger handler 로 이관 (WS intel → FSM handler) | test_exit_trail_* all pass on FSM path |
| S6 | P5 STOP + P12-13 TIME → FSM `STOP`/`TIME` handler 로 최종 이관 | test_inv_E*, test_exit_hard_stop all pass on FSM |
| S7 | **Feature flag retire**: `exit_legacy_enabled=0` default; 1주 paper obs → legacy 삭제 | zero legacy path hits in `trade_events` 1 week |

**Risk grade per step**: S1-S4 Low, S5 Med (WS intel side-effect), S6 High (semantic divergence possible), S7 Low (data-driven).

---

## 4. 본 batch 저위험 Extraction: P3 Regime Adjust

**Extracted**: `_apply_regime_stop_adjust(ep: dict, regime: str, position: Position) -> None`

- 입력: ep dict (P2 에서 조립), regime string, position (adopted flag + ticker for log)
- 출력: **ep 인 place mutation** (hard_stop_pct, flat_kill_sec, max_hold_sec)
- 부작용: adopted 시 log_event("EXIT", ..., "debug")
- Behavior 보존: elif 순서 유지 (crisis/risk_off > neutral > risk_on), preg 키 동일, max floor 동일

**Cross-review pending (external)**:
- [ ] trading_advisor — stop multiplier 방향성 (Contrarian invariant 유지)
- [ ] security_advisor — preg() failure fallback (None → TypeError 가능성?)

---

## 5. Next Actions

1. 본 PR: P3 extract + `git commit`
2. Next sprint (F-N14 PR2): P2 `_resolve_exit_params` + parity test
3. Next sprint (F-N14 PR3): P1 `_emit_exit_trigger` shared telemetry
4. Cross-review 요청: Harness → trading_advisor + security_advisor (S5/S6 승인 선행)
