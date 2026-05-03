# T13 D14 — composer drop site forensic (Phase 1)

**Scope**: `invasion/signals/composer.py` `CompositeScorer.score()` — 5 drop sites.
**Baseline (1h, pre-dedup)**: 143,519 `skipped` + 61,170 `lowconf` + others ≈ 205K/h
= 3,400 writes/min. Violates `feedback_flow_not_block` (silent 99%+ drop).

## Drop sites (composer.py line refs, post-edit)

| Line | Reason     | Trigger condition                                      | Volume (1h) | Classification (proposed)      |
|------|------------|--------------------------------------------------------|-------------|--------------------------------|
| L400 | `skipped`  | `provider.name not in allowed` (group whitelist)       | ~80K        | **noise** — group filter hit   |
| L410 | `skipped`  | `w <= 0` (weight override or learned=0)                | ~60K        | **quality_filter** — learner   |
| L419 | `error`    | `provider.safe_compute` raised                         | ~small      | **structural_defect** — bug    |
| L431 | `zero`     | provider returned `None`                               | ~varies     | **noise** (expected) or defect |
| L438 | `expired`  | `sig.is_expired` (age > TTL)                           | ~varies     | **noise** — dedup/TTL cleanup  |
| L445 | `lowconf`  | `sig.confidence <= 0` (post-relaxation floor)          | ~60K        | **quality_filter** — zero conf |

## Root-cause analysis (text)

- **skipped/group-whitelist (L400)**: `_GROUP_PROVIDERS[group]` 에 provider 없음.
  정상 — forex 티커에 `liquidation` provider 안 돌리는 식. forensic 가치 낮음.
- **skipped/w<=0 (L410)**: `weight_overrides[provider.name] <= 0`. learner 가
  provider 를 명시적으로 0으로 만든 경우. quality filter 쪽에 가까움.
- **error (L419)**: `provider.safe_compute` 가 raise — safe_compute wrapper 자체가
  try/except 인데도 뚫고 나오는 케이스. 진짜 defect 소지, 별도 fix 필요.
- **zero (L431)**: `provider.compute()` 가 `None` 반환. data 미비 (forex 티커에
  on-chain data 없음) 대부분이라 정상 noise.
- **expired (L438)**: `sig.is_expired` — provider 의 decay TTL 초과. lag 있으면
  당연, provider 레벨 TTL 조정 여부 후속 cycle.
- **lowconf (L445)**: `sig.confidence <= 0`. 공격적 완화 (0.1→0) 이후에도 hit.
  score 는 있는데 certainty 가 0 인 provider (fear_greed edge 경우 많음).

## Phase 4 dedup 효과 예상

- 쓰기 cardinality ≈ tickers × reasons (universe ~50 × reasons 5) = **250 entries/min**.
- 60s window → 분당 쓰기 ~250 / 60s × 60 = **~250/min 상한**, 실질 ~50-100/min.
- **1/30 ~ 1/60 감소** (3,400 → 50-100). DB write pressure 해소.
- jsonl 도 동일 dedup → 디스크 압박 완화.

## Phase 2/3 (후속 cycle)

- `_append_drop(reason, sig, classification=str)` 시그니처 확장.
- 각 site 호출부에 명시적 classification 전달.
- `signal_blocks.classification` column 추가 (idempotent ALTER).
- dashboard intel funnel 에 classification breakdown.

## 검증 체크리스트 (restart 후)

- [ ] `SELECT COUNT(*) FROM signal_blocks WHERE ts >= strftime('%s','now','-1 minute')` → 100 이하
- [ ] 1h 누적은 ticker×reason cardinality 수준 (수 K 이하)
- [ ] 특정 (ticker, reason) pair 의 간격이 60s 이상인지 spot-check
