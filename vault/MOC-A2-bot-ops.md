---
type: moc
status: active
date_created: 2026-06-02
tags: [moc, axis-a2, strategy, ops, trading]
---

# MOC-A2 — 봇 전략 & 운영 (the BOT)

A2 축 = **봇 자신** — signal 생성 전략 7종, 라이브 운영 다이제스트, 대시보드. A1 설계가 실제로 굴러가며 거래·기록을 만들어내는 run-side. 거래 결과는 A3(DB)로 흘러간다.

## 전략 (7 signal generators)
- [[volume_burst]] — OKX SPOT 1m, vol z>2.5 + prior-high break
- [[tsmom]] — OKX SPOT 1H, 20-bar momentum basket
- [[rsi_bb_pullback]] — OKX SPOT 15m, RSI<30 + BB lower + ma200
- [[spot_donchian]] — OKX SPOT 1H, Donchian 40 + ADX>20
- [[fx_breakout_basket]] — Capital CFD 1H, FX 5-pair 30× lev
- [[xau_indices_trend]] — Capital CFD 1H, XAU/indices 20× lev
- [[session_breakout]] — Capital CFD 5m, open ATR×1.5 break 20× lev

## 운영 다이제스트 (key few)
- [[2026-05-30_3stream_live_cutover]] — 3-스트림 라이브 전환 + 5-axis 최종검증
- [[2026-05-30_handover_3stream]] — 자율 3-스트림 핸드오버
- [[2026-05-29_loss_forensic_fee_overtrading]] — fee 폭주 × 과매매 포렌식
- [[2026-05-28_5axis_audit]] — 5-axis P0 venue wire-miss + fix
- 나머지 ~40 다이제스트: `40_ops/digests/` 폴더 · 일일로그 `40_ops/daily/`

## 대시보드
- [[dashboard]] — Neural Cloud sphere + 분석보드, `/api/snapshot` feed (WEB :8770)

---
## 축 연결
- [[MOC-A1-design-dev]] — 이 봇을 만든 설계·결정
- [[MOC-A3-raw-data]] — 봇이 기록하는 DB 로우데이터 (trades/positions/ai_lessons)
