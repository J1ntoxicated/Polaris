---
type: debate
status: design
topic: exit-harvest-redesign-tick-scalp
date_created: 2026-06-26
participants: [claude-opus-4-8, gpt-5.5, gemini-2.5-pro]
verdict: DESIGN-CONVERGED (1 residual frac fork; build pending Jin sign-off)
related: [[peak_giveback_fix_19_2026-06-25]], [[g7_tick_trail_atr_scale_2026-06-25]], [[g7_reversion_scalp_ruler_2026-06-25]], [[ab_letrun_maker_2026-06-24]], [[strategy_vs_execution_partA_2026-06-24]]
tags: [debate, exit, harvest, peak-protect, tick-scalp, flow-not-block, asymmetry, demo-paper]
---

# 엑싯/하베스트 재설계 — 틱엔진 스캘프 peak harvest (D1/D2/D3)

DEMO/PAPER · aggressive · flow_not_block(엑싯 타이밍 정밀, 차단/사이즈컷/rail확대 X) ·
asymmetry(−1.0R/−0.4R rail 불변) · 9-stack 봉쇄 · 거부키워드 sweep=0. GPT-5.5 + Gemini-2.5-pro
2-라운드 적대 + 3-라운드 타이브레이크. **이번 디베이트 = G7 ruler-fix(entry자 통일) 이후의 재캘리브 레이어**
(기존 #19 peak-protect/scalp-giveback 위에 얹음, 재발명 아님).

## 병형 (실측, 게싱 X)
881 closed 중 favorable peak 도달 236, **92건(39%) BE/적자 청산**(SUI/SOL peak→0). 슬리피지 아님.
MFE-capture: burst_rider 4% · session 1% · flow_pressure 19% · micro_reversion 28% · fx_range_fade 41%(유일 net+, 전용 fade-exit 보유). avg peak +0.39R; reach +0.30R 32.9% / +0.45R 18.4% / **+1.0R 7.9%(runner 희소)**.

## 수렴 (양측 합의 — load-bearing)
- **D1 family-keyed harvest**: 리버전/모멘텀 같은 틱엔진에 공존하는 반대 edge-shape를 **family로 분기**해 해소.
  reversion=tight snap-capture(흐름 banked fast), momentum=looser give-back(run에 air). 동일 rule 강제 X.
- **D2 one-mechanism (Point1=A)**: 모멘텀에 별도 give-back knob 추가 안 함. **peak-protect FLOOR arm 0.45R→0.30R**
  하나로 충분(ratchet floor가 +0.40R peak→+0.20R lock, +0.10R 붕괴 캐치). 2-catch는 redundant.
- **D2 arm 0.30R**: 0.45R는 common-case(peak~0.39R, 32.9%만 0.30R 도달)를 굶김 → 0.30R로 하향(절대-R 유지,
  MFE-상대 변환은 give-back과 conflate되어 기각; venue/horizon grid는 overfit으로 기각).
- **D3 hard-binary (Point2=A)**: peak<1.0R harvest 활성 / **peak≥1.0R harvest OFF → 기존 0.50 peak-protect floor+ATR trail만**.
  progressive(0.75/1.50 밴드)는 **runner 8%/near-zero 표본에 fitting=overfit** → 양측 기각. 1.0R 위는 기존 ratchet floor가
  이미 보호(convexity 보존, right-tail 흐르게). 이것이 진짜 비대칭 amplification(D3): common peak는 banked, rare runner는 floor로 run.
- **reversion arm 0.25R 불변**(0.5R target 직하, 거의 bank 직전 peak만 캐치) + min-bank 0.10R(fee-safe) 유지.

## 발산 (1건 잔존 — Point3 reversion frac)
- **GPT=0.60**: 0.75는 0.5R target 도달 전 snap-back을 clip(over-trigger). 0.60이 +0.30R peak→+0.18R bank로 충분.
- **Gemini=0.75**: bounded snap이므로 더 tight하게 captured profit 보호.
- **권고 default = 0.60**(flow_not_block 정합: tighter frac=조기 banking=favorable move에 throttle 근접; 이미 deployed 값).
  0.75는 클립 위험. 라이브 재측정(reversion give-back realized vs scalp_target reach) 후 재판정.

## BUILD SPEC (env-knob, 빌드는 Jin 확정 후 — builder≠reviewer)
- `_TICK_PEAK_LOCK_ARM_R` 0.45 → **0.30** (`POLARIS_TICK_PEAK_LOCK_ARM_R`)
- `_TICK_PEAK_LOCK_FRAC` **0.50** (불변)
- D3 binary: peak≥1.0R 시 give-back 분기 disarm → peak-protect floor+ATR trail만 (신규 1-line gate, `_production_tick_mfe.py`)
- `_SCALP_PEAK_ARM_R` **0.25** (불변) · `_SCALP_PEAK_FRAC` **0.60**(권고; 0.75 fork open) · `_SCALP_PEAK_MIN_BANK_R` 0.10 불변
- 신규 momentum-give-back knob **없음**(one-mechanism). 모멘텀 EXIT_PEAK_LOCK_ARM_R도 0.30 동조 검토(bar는 0.45 유지).

flow_not_block 증명: stop을 수익쪽 ratchet/조기 bank만(rail/size/entry 불변). in-loop GPT=0(deterministic).
다음: 빌드前 fresh-Claude 리뷰 + TDD repro(arm 0.30 lock / binary 1.0R cutoff / reversion frac guard / loss-rail byte-id).
