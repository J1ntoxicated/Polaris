# MODULE_REVIEW — `invasion/trade/exit.py` 999L Split Plan (F-N17)

> architecture_advisor F-N17: `invasion/trade/exit.py` = **999 LOC in one file**
> (원 감사값 948 이후 F-N14 P3 helper 등 추가로 살짝 증가).
> Code discipline: > 1000 = P0 split (`.claude/docs/code_size_limits.md`).
> F-N14 (`check()` 442L) plan 은 완료 (caa1232 + 17cd1d0), 본 문서는 **파일 전체 block map + 저위험 extraction 순서** 담당.
> FSM S2-S7 에서 legacy `check()` retire 가 별도 진행 중 — 본 split 은 FSM 경로 건드리지 않는다.

---

## 1. File Block Map (exit.py 999L, as of 2026-04-18)

| # | Block | Lines | LOC | 역할 | Extract 난이도 | Risk | 우선순위 |
|---|-------|-------|-----|------|----------------|------|---------|
| B0 | Module header + imports | 1-20 | 20 | docstring, `_SCHEMA_TRAIL_TIERS` cache | — (stay) | — | — |
| B1 | `ExitEngine.__init__` + class constants | 22-62 | 41 | `_GROUP_ALIAS`, `_VALID_GROUPS`, FSM flag aliases, canary set | — (stay, thin) | — | — |
| B2 | `_is_fsm_enabled_for` (classmethod) | 63-127 | 65 | Two-layer FSM gate + canary partition + log | 중 (class-state `_FSM_CANARY_LOGGED`) | Med | P2 |
| **B3** | **`_maybe_warn_stop_slippage` + `_SLIPPAGE_WARN_LAST`** | **129-184** | **56** | **Slippage WARN + `slippage_tracker` feed** | **저 (순수 util, 2 callsite)** | **Low** | **P1 (이 PR)** |
| B4 | `_apply_regime_stop_adjust` (F-N14 P3 helper) | 186-225 | 40 | ep 딕셔너리 in-place 조정 | 저 | Low | P3 |
| B5 | `_get_group_profile` | 227-240 | 14 | preg lookup → dict | — (stay, trivial) | — | — |
| B6 | `calc_entry_exits` | 242-364 | 123 | entry-time exit param resolve (strategy > group > ATR) | 중 (self.config dep) | Med | P3 |
| B7 | `check` (orchestrator + legacy ladder) | 366-779 | 414 | **F-N14 plan 대상**, 별도 retire flow | — (F-N14 관할) | — | F-N14 |
| B8 | `_check_via_fsm` | 781-846 | 66 | FSM delegation + telemetry | — (FSM 경로, 건드리지 않음) | — | — |
| B9 | `_trail_distance` (staticmethod) | 848-884 | 37 | N-tier linear interp | 저 (pure fn, FSM 도 외부 호출) | Low | P2 |
| B10 | `_vol_window_mult` | 886-900 | 15 | vol-window stop multiplier (UTC hour) | 저 (self.config dep, FSM reuses) | Low | P2 |
| B11 | `_profit_taker_check` | 902-999 | 98 | 4-signal composite peak detector | 저 (pure ep+preg) | Low | P2 |

**합계**: 11 블록 / 999 LOC.

---

## 2. Extraction 순서 (저위험 우선)

### P1 — 본 PR: B3 Slippage Utils → `exit_slippage.py`
- `_SLIPPAGE_WARN_LAST` (module-level dict) + `maybe_warn_stop_slippage(position, pnl, bound, tag)` (module fn)
- `ExitEngine._maybe_warn_stop_slippage` classmethod 는 **thin delegator** 로 보존 → 외부 호출 시그니처 유지
- **Callsite 수정**: `exit.py:508`, `exit.py:520` → `self._maybe_warn_stop_slippage(...)` 그대로 (delegator 경유)
- **Saving**: ~56 LOC → exit.py 가 ~943 LOC 로 하락 (아직 P0 상한 > 1000 아래)

### P2 — 후속 PR (별도): B9 + B10 + B11 → `exit_helpers.py`
- `_trail_distance` (static), `_vol_window_mult` (self-bound → needs self.config), `_profit_taker_check`
- FSM (`exit_fsm.py`) 가 `legacy_engine._trail_distance` / `legacy_engine._vol_window_mult` 로 접근 중 → classmethod/wrapper 보존 필수
- Risk: FSM 경로 telemetry parity 검증 필요

### P3 — 후속 PR: B2 FSM gate + B4 regime adjust + B6 calc_entry_exits → `exit_config.py` or `exit_params.py`
- canary log 상태 + regime adjust helper + entry-time param resolve 묶음
- calc_entry_exits 는 pipeline 진입 경로 → behaviour 보존 테스트 필수

### F-N14 — B7 `check()` retire
- FSM S2-S7 별도 track. 본 split 은 건드리지 않음.

---

## 3. Invariants (I-E1~E5) — 전부 보존 대상

1. **I-E1 3-category STOP/TRAIL/TIME**: 카테고리별 분기 순서 유지
2. **I-E2 STOP always active**: min_hold ignored, 경로 단축 금지
3. **I-E3 Deterministic FSM canary**: `abs(hash(pos_id)) % 100` — 동일 pos 동일 route
4. **I-E4 Slippage debounce survives ExitEngine re-instantiation**: class-level (or module-level) 상태
5. **I-E5 bus publish + log_event telemetry parity**: legacy/FSM 양쪽 동일 키셋

B3 extraction 은 **I-E4 보존이 유일 관심사** — module-level dict 로 이동 시 프로세스 수명 동안 유지 (class-level 과 동일 효과).

---

## 4. 본 PR (B3) 수용 기준

- [x] `exit_slippage.py` 신규 생성, `maybe_warn_stop_slippage` + `_SLIPPAGE_WARN_LAST`
- [x] `exit.py` 의 classmethod 는 thin wrapper 로 보존 (2 callsite 변경 無)
- [x] `python3 -m py_compile invasion/trade/exit.py invasion/trade/exit_slippage.py`
- [x] `python3 -c "import invasion.main"`
- [x] `pytest tests/trade/test_exit_fsm.py` (있으면)

---

## 5. 참조

- F-N14 (check 442L) plan: `docs/MODULE_REVIEW_exit_check_split.md`
- Code size policy: `.claude/docs/code_size_limits.md`
- FSM 구현: `invasion/trade/exit_fsm.py`
- Slippage tracker: `invasion/trade/slippage_tracker.py`
