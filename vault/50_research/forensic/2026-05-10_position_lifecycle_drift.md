---
type: forensic
status: open
date_created: 2026-05-10
date_updated: 2026-05-10
trigger: manual_jin (dashboard "활성 포지션 4개 / +2037" 이상)
tags: [forensic, lifecycle, dedupe]
---

# Position lifecycle drift — Topic A

## Incident
`positions.status='open'` 2037 row, `closed` 4365. 동일 `(venue, symbol, strategy, side)` 조합당 다중 row (ENJ-USDT/tsmom/long 233, …). **현재진행형** — 2026-05-10 paper loop 6h 동안에도 DOT/ETH/SUI tsmom long 각 5 row.

## Smoking guns
1. `polaris/scripts/_production_pipeline.py:183`
   `position_id = f"pos_{sig.signal_id[:16]}_{now_ts}"` — signal × tick 함수. `INSERT OR REPLACE` 무력화.
2. `polaris/core/isolation/order_keys.py:58`
   `f"{strategy_id}:{venue}:{symbol}:{timeframe}:{int(signal_ts)}:{side_norm}"` — `signal_ts` 포함. AllocatorFence reservation dedupe 도 동일 결함.
3. close path 작동하긴 하나 같은 키 다중 row 중 **1개만 close**. 나머지 OPEN 잔존.
4. startup stale-OPEN GC 부재. paper loop 재시작 시 직전 OPEN 청소 안 됨.

## 시간 시그니처
- 5/7 16:45~21:20: OPEN 폭주 (production_pipeline 호출 시점). close 대비 OPEN 잔존.
- 5/7 22:00~5/8 22:00: opened **AND** closed 윈도우 동률. close 작동 중 일부 잔존.
- 5/9: 0 (paper loop 안 돔)
- 5/10 12:40~18:38: 117 close + 17 새 OPEN. 같은 키 5 row × 3 심볼 — 결함 진행 중.

## Fix 후보 (Topic A 디베이트 대상)
- **F1** `position_id` → `pos_{venue}_{symbol}_{strategy}_{side}` 결정적 key
- **F2** `build_order_key` 의 `signal_ts` 처리 — 제거 / timeframe bucket / OPEN dedupe pre-check 위임
- **F3** 같은 키 OPEN 존재 시 정책 — **scale-in vs swap vs reject** (`flow_not_block` mandate 제약)
- **F4** startup stale-OPEN GC — 책임 위치 (ignite_p1 / production_paper_loop / 별도 모듈)

## 정책 충돌 점검
- `feedback_no_block_filter_architecture` "막지 마" → 단순 reject 금지
- `feedback_flow_not_block` "흐르게" → scale-in 또는 swap 이 정합
- `feedback_aggressive_always_profit` → dedupe 명목으로 entry 막으면 안 됨
- `feedback_no_quick_patch_ever` → INSERT 4줄 추가식 패치 금지, 구조 결정

## Status
open · evidence 1차 완료. F1~F4 디자인 결정은 **codex debate 필수** (`feedback_reasoning_superbrain`). /dev 단독 즉시패치 금지.
