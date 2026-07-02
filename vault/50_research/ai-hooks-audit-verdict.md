---
type: research
status: active
date_created: 2026-07-02
tags: [audit, ai-judge, gates, altdata, metrics, forensic]
related: ["[[ai-hooks-audit-fixplan]]", "[[trade_mess_full_audit_2026-07-02_verdict]]", "[[ADR-004-per-gate-ai-pipeline|ADR-004]]", "[[ADR-011-ai-free-cutover|ADR-011]]"]
---

# AI 후킹 전수 감사 — 종합 판정 (2026-07-02)

> 6개 병렬 감사(judge 프로브/G1-G8/metrics-canon/altdata/스토어/AI 경계), Workflow `wf_c66a0f03` — find(Fable5)→적대검증(Sonnet5, confirmed 20)→합성. DEMO/PAPER. 쌍둥이 감사: [[trade_mess_full_audit_2026-07-02_verdict]].

**한줄**: 죽지 않았으나 환류하지 않는 지능 — 엔트리 judge·altdata 실도달, 엑싯 judge 0콜(lockout), 게이트 선별가치 실측 음(-), 표시층 GROSS/NET 이중 자, 관찰 데이터는 소비자 없는 축적.

## 가설 스코어카드 (사전등록 5)
- **H1** judge ACTIVE인데 실거동≈0 → **부분**: SIZE_UP 115건 실사이징(×1.15) 반증 / G7 0콜 확정 / REFINE_TIMING 1,487건 INERT
- **H2** altdata write-only 무덤 → **틀림(부분만)**: 6소스 4경로 실도달; 잔여 = binance 100% BULL 포화·news age drop·비crypto/equity news 0
- **H3** 표시층 gross/net 혼재 → **맞음**: digest GROSS(+6.05 vs 실 -108.9), win_rate 27.6% vs net 8.6%, since-reset dormant
- **H4** 게이트 무죄-무익 → **부분**: 무익 확인(생존율 97.6%, G3 no-op, G4가 +R 신호 킬), '비용만'은 틀림(토큰 0, [[ADR-011-ai-free-cutover|ADR-011]] 무죄)
- **H5** 스토어 소비처 근소 → **부분**: ALIVE 3 / display 2 / 무덤 3 / 0행 인프라 3

## 근본 원인 (rank순)
1. **배선 silent 갭** — 존재≠도달 검증 부재 (strength_scalar·REFINE_TIMING·counterfactuals·news_max_age_h·market_events 동일 패턴)
2. **정적 임계 vs 라이브 분포 미대조** — G7 0.41<0.50 / binance 1.30<min 1.401 / maker_fill_shadow 순환 데드락
3. **측정 자 이원화** — 코어 NET vs 표시층 GROSS (자 3개)
4. **피드백 레그 사망** — judge flags·counterfactual 15k행 리더 전무
5. **게이트 선별가치 음(-)** — KILL +0.203R vs PASS -0.679R
6. **judge wall-clock 무상한** — 성공콜 4.7% >8s, max 495s

상세: [[judge-probe-reality]] · [[gate-pipeline-value-audit]] · [[altdata-reachability-map]] · [[store-graveyard-census]] · [[metrics-canon-status]] · fix: [[ai-hooks-audit-fixplan]]
