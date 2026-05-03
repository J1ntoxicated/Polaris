---
entity_type: index
entity_id: master_index
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[_NOW]]"]
mode: meta
reviewed_by: jin
tags: [meta, index, polaris]
---

# INDEX — Polaris Vault Master Index

> 마스터 카탈로그. 매 작업 시작 시 [[_NOW]] 다음 read.

## 📜 Constitution (10_constitution/)

- [[north_star]] — Polaris 철학 (북극성 + SPOT-first 재정의)
- [[principles]] — 7 영속 원칙 (P1~P7)
- [[4_contracts]] — Authority / Lifecycle / Write Path / Validation Boundary
- [[governance]] — DRAFT / VERIFIED / AUTHORITATIVE 3단계 성숙도
- [[emergency_bypass]] — 긴급 fix 조건 + 24h 사후 산출물
- [[operating_model]] — 8 섹션 운영 모델 (모드/구조/agent/스킬/슈퍼브레인/볼트/seq thinking/리뷰)
- [[code_review_workflow]] — codex 외부 리뷰 의무 사이클

## 🏛️ Decisions (20_decisions/)

| ADR | 제목 | 상태 |
|---|---|---|
| [[ADR-001]] | SPOT-first fresh start (옵션 Y 확정) | applied |
| [[ADR-002]] | Vault-first architecture (v4 7계층) | applied |
| [[ADR-003]] | Codex debate protocol (max 3 라운드 합의) | applied |
| [[ADR-004]] | Code review codex external (Jin mandate) | applied |
| [[ADR-005]] | Harness 4 modes (DEV/ALPHA/FORENSIC/DEBATE) | applied |
| [[ADR-006]] | SPOT trend N-strategies architecture (모태 ADR-007 인수) | provisional |
| [[ADR-007]] | Paper sizing freedom + fee floor (모태 ADR-009 인수) | provisional |
| [[ADR-008]] | vol_factor PROPORTIONAL fix CRITICAL (모태 ADR-010 인수) | provisional |
| [[ADR-009]] | SPOT-only 유지 + PERP 검토 3개월 (모태 ADR-011 인수) | provisional |
| [[ADR-010]] | Backtest + Paper parallel (백테스트 신뢰도 한계 대응) | provisional |
| [[ADR-011]] | Promotion Gate Timeframe-aware (1h scalp / 1d trend 분리) | provisional |

## 💡 Insights (30_knowledge/insights/)

| INSIGHT | 제목 | 상태 |
|---|---|---|
| [[INSIGHT-001]] | Legacy spot pollution (6,263 라인 누더기) | active |
| [[INSIGHT-002]] | MTTR-alpha KPI 정의 | active |
| [[INSIGHT-003]] | Edge calibration baseline (Bayesian 132 cells) | active |
| [[INSIGHT-004]] | Tournament ELO top strategies (volatility_spike 4391) | active |
| [[INSIGHT-005]] | Regime presets base (VIX/FG/DXY thresholds) | active |
| [[INSIGHT-006]] | Frozen params boundary (spot_crypto/global) | active |
| [[INSIGHT-007]] | OKX SPOT fee 0.7% scalp 수학적 불가능 (P0) | active |
| [[INSIGHT-008]] | Taker fallback unwired (lessons #44 사례) | active |
| [[INSIGHT-009]] | Fee floor miswire 4-fold cascade (P0) | active |
| [[INSIGHT-010]] | fee_paid base units corruption (P7 적용) | active |
| [[INSIGHT-011]] | Demo WS URL risk (Phase 2b 적용 의무, P0) | active |
| [[INSIGHT-012]] | Backtest 신뢰도 한계 정량 (Sharpe CI/regime/overfitting, P0) | active |
| [[INSIGHT-013]] | RSI mean reversion BTC fast-fail (HYPO-001 6 params, P0) | active |
| [[INSIGHT-014]] | BB breakout multi-ticker fast-fail (HYPO-002 ETH+BTC, P0) | active |
| [[INSIGHT-015]] | SMA crossover 1d = SPOT viable (HYPO-003 fast-fail 통과, P0) | active |
| [[INSIGHT-016]] | HYPO-003 walk-forward robustness (3-fold + TRAIN/TEST 일관 양수) | active |
| [[INSIGHT-017]] | Look-ahead bias fix + HYPO-003 robust 재확인 (codex Round 1, P0) | active |
| [[INSIGHT-018]] | Codex Round 2 88% — 5 fix 적용 (look-ahead/Position guard/daily loss/auto category/vault contract) | active |

## 📚 Lessons (30_knowledge/lessons/)

| LESSON | 제목 | 상태 |
|---|---|---|
| [[LESSON-001]] | NULL cascade prevention (모태 #78) | active |
| [[LESSON-002]] | Paper vs Live divergence (모태 #47) | active |
| [[LESSON-003]] | Runtime verify mandatory (모태 #46) | active |
| [[LESSON-004]] | Grep-before-guess (모태 #45) | active |
| [[LESSON-005]] | 소비자 grep 증거 (모태 #44) | active |

## 📚 Lessons + Patterns

- [[30_knowledge/lessons/_README]] — 모태 5 핵심 lesson 인수 가이드
- [[30_knowledge/patterns/_README]] — anti-pattern 카탈로그 (작성 예정)

## 🧱 Components (40_components/)

- [[40_components/_README]] — curated summary 작성 가이드
- _자동 생성_: `vault/generated/components/` (gitignore, untracked)

## 📊 Runtime (50_runtime/)

- [[50_runtime/_README]] — daily log + audit append-only 가이드

## 🔬 Alpha (60_alpha/)

- [[60_alpha/_README]] — HYPO → BACKTEST → PAPER → Promotion Gate → ADR 워크플로
- [[60_alpha/_alpha_index]] — 가설 dataview 인덱스 (status별)
- `active/` `graduated/` `archived/`

## 📥 인수 큐

- [[_INHERIT_QUEUE]] — Codex 식별 8개 모태 인수 stub (Phase D 이후 추출)

## 🏷️ 태그 표준

- [[.tag_taxonomy]]
