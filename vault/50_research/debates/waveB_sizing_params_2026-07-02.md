---
type: research
status: active
date_created: 2026-07-02
tags: [debate, codex, sizing, r-budget, strength-scalar, exit, maturity-gate]
related: ["[[trade_mess_full_audit_2026-07-02_fixplan]]", "[[post-deploy-verification-audit-2026-07-02]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]", "[[layer-3-sizing-risk]]"]
---

# Wave B 디베이트 — R-budget 사이징 · strength_scalar 산식 · 성숙도 frac (2026-07-02)

> codex CLI(GPT, effort=high) 3라운드, 합의 조기 종료. **Gemini CLI UNAVAILABLE**(IneligibleTierError — free-tier 폐지, Antigravity 마이그레이션 필요; 대체 시도 안 함). DEMO/PAPER 컨텍스트 명시, 거부 키워드 0건.

## 라운드 요약
- **R1** (초안 제시): codex ①AGREE(+flip은 시간 아닌 이벤트수 게이트, property test clipped/unclipped 분리) ②AGREE(a 직접곱) ③MODIFY(timeframe 차등 + 2-native-bar floor + wall-clock 아닌 bar-seen 계수).
- **R2** (적대 교차, fresh 세션): 4건 BREAK — ①margin 지배 시 R-조준 허구화(측정 오염) ②clamp 후 곱 = 경계 비대칭 정보손실 ③short-horizon daily에 2-bar floor 과대(5d horizon의 40%) ④cap-bound에서 strength가 하방만 작동하는 비대칭. 전부 측정·서열 정련이며 근본 기각 아님.
- **R3** (최종 스펙): CONFIRM ×3 (+required_bars ceil 명세).

## 안건 ① R-budget 조준 사이징 — 합의
- **PRIMARY**: `qty_risk = R_budget × T4_mult ÷ stop_dist_usd` (stop = fee-floored stop_atr_mult×ATR×fx, risk_unit.py 동일 소스). tier 3× = 3R 직접 표현.
- 기존 cap 전부(심볼/클러스터/트랙/마진 `avail_margin×lev÷price`/env-set notional ceiling) **qty 단위 환산 → 단일 min()**. 선제 축소 없음(flow).
- **CAP_DOMINATED 측정 무결성**(R2 유효 반박): `target_realization = qty_after_caps×stop_dist÷(R_budget×T4_mult)` 로그, <0.33 태그 → property-test 모집단·R-캘리브 통계에서 제외. 로그 전용, 주문 불변. fee-floor가 stop_frac을 들어올려 극단 margin 폭증은 완화됨.
- ATR==0/stale/fx-mismatch = 데이터 무결성 오류 경로(사이징 판단 아님).
- **배포**: shadow-compute 병렬 → N≥100 sizing decisions 또는 24h 先도달 시 flip 100%. k% 램프 없음(배선 검증 목적만). Property: unclipped 풀스톱 ≈ −R_budget×T4_mult ±10% / clipped는 binding cap 로그 단언.

## 안건 ② strength_scalar → T4 — 합의
- **산식 (a)**: `cont_final = clamp(raw_cont_preclip × strength_scalar, 0.75, 1.5)` — **clamp 최종 1회**(R2 유효 반박: clamp된 값에 곱하면 cont≈0.75/1.5 경계에서 비대칭 정보손실). regime/vol fold와 동일 단일 cont 슬롯, 9-stack 무결(신규 mult 슬롯 0).
- 부재/non-MODIFY = 1.0, 동일 decision cycle 내만 유효(stale 재사용 금지).
- 로그: strength 버킷별 floor/ceiling saturation rate + 트리플(`qty_unclipped_no_strength`/`with_strength`/`after_caps`) → cap-bound 표본은 strength 효과 평가에서 분리(R2 ④).
- (b) 압축·(c) 기하평균 기각 — LLM 콜로 얻은 신호 희석.

## 안건 ③ EXIT_THESIS_BREAK_HOLD_FRAC — 합의
- **per-timeframe 차등**: daily-or-slower native bar = **0.10** / intraday = **0.05 유지**. 15% 기각(thesis_cut 정보가치 소실·과대 연장).
- `required_bars = max(ceil(horizon_seconds×hold_frac ÷ native_bar_interval), floor_bars)`, **floor_bars = 2 if horizon_bars≥10 else 1**(R2 유효 반박: 5d horizon에 2-bar 강제 = horizon 40% 억제) — **native_bars_seen 계수**(휴장/갭 wall-clock 왜곡 방지), `elapsed_time·native_bars_seen·required_bars` 로그.
- 21d tsmom → ceil(2.1)=3 bars ≈ 2.1d 발달 보장. 손실 방어 계층(-1.0R rail/ATR trail/G6 crisis) 불변.

## 빌드 스펙 (1줄씩)
1. G5 qty_risk PRIMARY + 전 cap qty-환산 단일 min() + CAP_DOMINATED 로그 + shadow-verify(N≥100∨24h)→flip.
2. G3 strength_scalar를 raw preclip cont에 곱한 뒤 단일 clamp[0.75,1.5] + saturation/트리플 로그.
3. exit_params hold_frac timeframe-차등(0.10/0.05) + required_bars ceil·floor_bars(2|1) + bars-seen 계수.
