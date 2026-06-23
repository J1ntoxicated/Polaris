---
type: research
status: decided
date_created: 2026-06-23
tags: [debate, flow_pressure, calibration, ai-intervention]
---

# Debate — flow_pressure 정밀 캘리브 + AI 개입 (2026-06-23)

## 안건 (Jin)
flow_pressure(−24.7R, 손실 거의 전부) 정밀 캘리브 + "AI 개입 거기 시키는건 어떤지까지".

## 증거 (실측, 게싱 아님)
- OKX SPOT order-flow(theta_ofi=0.40), **LONG-ONLY**(spot 숏 불가). 59 closed, **−24.7R**, 승률 15%, avg −0.42R, hold 515s.
- avg MFE +0.67R vs MAE −1.13R(비대칭). **59% mfe<0.2 = 진입이 OFI 스파이크 꼭대기 매수→즉시 반전**(exhaustion을 continuation 오인). 17% 이익 났다 반납(엑싯 늦음). 전부 OKX(데이터 정상).

## GPT (codex)
- D1: (d)조합 > (a)진입확인 > (c)엑싯 > (b)fade. raw OFI 스파이크 진입을 **post-spike 3-6틱 follow-through 확인**(OFI 유지/재가속·bid 추종·microprice 스파이크중점 위 유지·spread 안정) 후 진입(theta 상향) + 엑싯 +0.35/0.50R 보호·OFI decay 트레일. fade=sibling 테스트.
- D2: **in-loop GPT 금지**. continuation-vs-exhaustion = microstructure 분류(deterministic 충분/우월), semantic 아님. AI는 async advisory(regime·post-trade clustering·param)만.

## Gemini (direct API)
- D1: (d)조합, 진입 **(b)fade 우선**(실패 스파이크 후 pullback 진입) + (c)엑싯.
- D2: **in-loop GPT 절대 금지**(AI-free cutover 최우선, latency/determinism/cost). async advisory만.

## 합의 + 종합 결정
- **D2 만장일치: in-loop AI 금지, AI-free 실행경로 유지. AI는 async advisory(regime/param)만, 진입 승인엔 X.** → Jin "AI 개입?" 답 = **in-loop NO**.
- **D1: (d)조합 합의.** 유일 분기 = 진입(GPT 확인 vs Gemini fade). GPT 프레이밍(확인 먼저 + fade는 sibling 테스트)이 둘을 포괄.
- **결정: (a)진입확인 + (c)엑싯정밀 먼저**(저위험·동일 thesis). **fade(b) = 후속 병렬 실험.** in-loop AI 없음.
- caveat: 데이터 리셋 전 적용이라 새 캘리브 측정은 리셋 후 클린(M→S→D→R).

관련: [[system_design_audit_2026-06-22]] · [[_NOW]] (D-params)
