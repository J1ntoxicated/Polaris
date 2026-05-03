# Audit T13 — Unit BUG 전수 (Plan v2.2 D2)

> **Scope**: percent ↔ fraction 혼용, `*100` / `/100` 중복 곱, unit tag 불일치.
> **Source**: `docs/metric_taxonomy.yaml` (D0) 기준 + grep 전수.

## 1. 실행 fix (D-A, commit `8e87f803`)

`invasion/ticks/hourly_stats.py:655` `float(_pp or 0) * 100` → `float(_pp or 0)`.
DB `max_profit_pct` 컬럼 이미 percent 단위 (실측 crypto max 119.9 / avg 0.41) → 100× 중복곱 제거. profit_target learner stuck 해소.

## 2. 전수 grep 결과 — Legitimate 변환 (fix 대상 아님)

`*100` / `/100` 패턴 ~40건 검출. 전부 정당한 용법 확인:

| 파일 | 패턴 | 용도 |
|---|---|---|
| `exchange/adapter.py:201` | `(ask-bid)/mid *100` | fraction→percent 변환 (계산 직후) |
| `exchange/adapter.py:217` | `(high-low)/price *100` | daily_range_pct 계산 |
| `exchange/alpaca_adapter.py:388,391,542` | `*10000` | bps 변환 (fraction→bps) |
| `exchange/okx/paper.py:210,216,687,689` | `pnl_pct / 100 * size` | percent→fraction→usd 변환 |
| `exchange/binance/public.py:376,379` | `gls["long_account"] * 100` | fraction→percent (API returns 0-1) |
| `exchange/*.py` (전부) | `_chg_pct = (p-p0)/p0 *100` | 정당한 percent 계산 |

→ 전부 D0 yaml `base_unit_conventions` 와 정합. **D-A 외 신규 BUG 없음**.

## 3. Unit contract validator — 스텁 생략 (premature)

Plan v2.2 에 `_metric_contract.py` 예정. 그러나 cell_resolve / cell_learn API (D16a) 가 아직 미구현 → validator entrypoints 가 없음. `feedback_no_feature_bloat` 준수, 스텁 생성 보류.

실제 구현 시점:
- Cell API 신설 (D16a) 시 `_metric_contract.py` 동반 생성
- regression 테스트: D-A / D-J 2 케이스 포함 필수

## 4. 재발 방지 invariant (D0 yaml 에 기록됨)

- `max_profit_pct` / `pnl_pct` → **percent_already, DO NOT *100**
- `strength` / `confidence` → **fraction [0,1], NOT percent**
- `mult` preg → **bounds low >= 1.0 (amplify-only)**

## 5. 결론

- D-A 외 Unit BUG 없음
- D0 yaml 이 validator spec 역할
- 실제 validator 구현은 Cell API (D16a) 동반 시점

---

**상태**: T13 MVP 완료. D0 yaml + audit 결과 → D16a 구현 시 validator 생성.
