---
type: research
status: built
date_created: 2026-06-26
date_updated: 2026-06-26
tags: [exit, harvest, recalib, let-run, flow-not-block, asymmetry, frac-ab]
related: [[ab_letrun_maker_2026-06-24]], [[strategy_vs_execution_partA_2026-06-24]]
---

# #47 엑싯/하베스트 재캘리브 (디베이트 수렴 + Jin sign-off)

DEMO/PAPER · aggressive · flow_not_block. 손실방어 = 정밀 엑싯 TIMING만 —
진입차단/사이즈컷/throttle 0. -1.0R hard rail·scalp -0.4R rail 불변. 9-stack 불변
(≤1 mult 신규 0). 전부 EXIT-timing. base = b20fe11 (de-inflated mfe_r 전제, #51).

## 3 스펙 (전부 빌드+TDD)
① **tick peak-lock arm 0.45→0.30** (`_production_tick_mfe.py:161`,
   `POLARIS_TICK_PEAK_LOCK_ARM_R`). 32.9%가 +0.30R 도달 vs 18.4%만 +0.45R —
   기존 0.45 arm이 common case 1/3을 굶김. frac 0.50 불변(arm서 lock +0.15R = OKX
   spread+fee 위, fee-safe). burst_rider+flow_pressure 둘 다 적용.
② **peak≥1.0R binary disarm** (`exit_params.py:EXIT_PEAK_GIVEBACK_DISARM_R=1.0` +
   `exit_thesis.py:mode_to_exit_params` 1-line: `thesis_harvest = gb>=2 and
   mfe_r < EXIT_PEAK_GIVEBACK_DISARM_R`). 포지션 peak가 +1.0R 넘으면 HARD give-back
   force-close OFF → 드문 runner는 peak-fraction floor + 와이드 ATR trail로 흐르게.
   peak<1.0R = 기존 give-back harvest 유지(흔한 작은 peak는 반납 catch). 비대칭:
   작은 peak lock / 드문 runner run. floor·trail·loss rail 다 살아있음(naked 아님).
③ **`_SCALP_PEAK_FRAC` env-knob read-fresh** (`_scalp_peak_frac()`,
   `POLARIS_SCALP_PEAK_FRAC` 기본 0.60, import-time 캐시X — 매 scalp-exit call 신선
   read). 라이브 A/B용 0.75 무재시작 주입 가능. `_SCALP_PEAK_ARM_R` 0.25 불변.
   profit-side only, loss rail 무관.

## frac A/B (Jin "둘다 해봐") → harness_found = FALSE (게싱 금지)
조사: bar-replay 하네스(`polaris/core/replay/engine.py`)는 `evaluate_exit`만 구동 —
스칼프 give-back(`_scalp_exit_decision`)은 **건드리지 않음**. tick-level 스칼프
리플레이 하네스 = 0 (grep `_scalp_exit_decision` in tools/scripts/replay = 0건).
DB는 포지션당 **FINAL** mfe_r(peak)+pnl_r(realized)만 — give-back은 live pnl_r이
`peak*frac` 교차하는 **순간** 발화 → intra-life 틱 trajectory 필요한데 **미저장**
(`quote_ticks`=single-row LWW, position-tick path 테이블 없음). 게다가 historical
mfe_r은 #51 de-inflation 이전(재스탬프=#52 pending) → 오프라인 근사도 inflated ruler
측정 = 부정직. **결론: 스칼프 frac 0.60 vs 0.75 정확 오프라인 A/B 불가.**

## 라이브 A/B 계획 (배포 후)
env-knob 빌드 완료. 배포 시 frac=0.60 기본. 0.75는 `POLARIS_SCALP_PEAK_FRAC=0.75`
무재시작 주입. since-reset capture 비교: micro_reversion(tick) 평균 실현R · giveback
발화율 · trade수 · avg peak(de-inflated)로 winner 결정. 모집단: tick micro_reversion
338 closed 중 give-back-armable(peak 0.25-1.0R) 94건이 비교 표본 토대.

## 검증
TDD: `tests/test_exit_recalib_47.py` 신규(arm=0.30·disarm 분기·boundary·frac
read-fresh·loss rail 비회귀). 기존 `test_evaluate_exit_thesis_mode`(disarm
integration 추가)·`test_peak_giveback_fix_19`·`test_letrun_peak_fraction_wiring`
recalib 반영. mypy --strict OK · ruff clean · py_compile OK. 전체 스위트:
사전존재 3 실패(layer0×2·run_debate×1, 내 모듈과 무관·base서 동일)만, 회귀 0.
