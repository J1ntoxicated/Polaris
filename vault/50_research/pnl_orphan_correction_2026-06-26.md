---
type: research
status: recorded
date_created: 2026-06-26
tags: [research, backfilled-frontmatter]
---

# PnL 측정 무결성 — cross-instrument orphan + capped mfe_r 보정 (#46)

2026-06-26 · DEMO/PAPER · 측정 전용(거동 불변, flow_not_block 무관)

## 증거 (게싱 아님 — 실 DB 재계산)
- raw `Σfills.pnl_usd(is_close=1)` = **+$2,589.30 (가짜 흑자)**.
- 실제 = **-$2,196.57 적자** (alpaca -$1,921.62 · okx ~-$187 · capital ~-$88).
- 차이 전부 = **단일** 오염 fill 1건: `capital:NL25` close `pnl=+4794.46` (실제 -$0.12).

## 근본 원인 (1개 root, 보고된 #1·#3 동시 설명)
오염 close fill의 `contribution_id = pos_…_US100_…` (NL25 fill인데 **US100 포지션 id**) — instrument 교차링크.
- pre-`7652647`(instrument-unique position_id) 구코드가 US100 entry를 NL25 close에 교차매칭 → pnl 폭발(+4794.46) + cache-miss fallback `size_usd=3.1`.
- 같은 US100 포지션 `mfe_r=100`(±100 cap artefact, pre-floor 구코드) — 동일 교차링크 산물.
- **런타임은 이미 봉쇄**: instrument-unique id + 모든 fill matcher `AND instrument_id=?`; FX는 `contract_factor_usd`에 quote→USD 포함(EUR/JPY 정확). 전 DB 통틀어 오염 fill 1건뿐 — 재발 불가, 잔존 데이터만 문제.

## 기존 도구의 사각
`correct_close_pnl_stamping.py`(slice 보정)는 **instrument-scoped** → orphan fill(instrument_id ≠ 포지션 instrument)을 0건으로 놓침.

## 수정 (단일 커밋, TDD, builder≠reviewer)
신규 `correct_close_pnl_orphans.py` (split, ≤500 LOC):
1. **cross-instrument orphan** 탐지+보정 — 동일 instrument·동일 `order_id` open fill로 재계산.
   런타임 close 공식 그대로 `(Δpx/entry_px)×entry_size_usd×frac` (entry size_usd가 EUR→USD 보유 → **FX-correct USD**). size_usd/quote_qty도 entry USD-per-unit로 재설정.
2. **capped mfe_r/mae_r** 재계산 — row 자체 `entry_atr_pct`+peak/trough로 현 excursion 공식 적용(100→13.82). 입력 결손 row는 cap 유지(정직한 'unknowable').
- per-row `BEGIN IMMEDIATE`+`risk_events` audit(`pnl_orphan_correction`/`mfe_r_cap_recompute`), dry-run byte-identical, 멱등(2회차=0).

## 적대 리뷰 캐치 (수정 반영)
- **side-source 버그**: close fill side(`buy`/`sell`)를 `_true_slice_pnl(side=="long")`에 넣어 항상 short 분기 → long orphan 부호 반전. **OPEN fill side로 교정**(buy→long). long orphan fixture 추가(가짜 +330 → 부호 가드).
- size_usd over-close clamp(`min(close,entry)`)로 pnl frac과 일치.

## 운영
봇 정지 후 `python3 -m polaris.scripts.correct_close_pnl_stamping --db data/polaris_live.sqlite --apply --fix-status` (백업 자동). 적용 시 capital ≈ +$4,706 가짜 → ≈-$88 실제, 총 → 음수.
