# MODULE_REVIEW — `invasion/market/regime.py` split plan (F-N17)

**Size**: 1087 L (> 1000 → P0 분할, [.claude/docs/code_size_limits.md])
**Owner**: ops_runtime_advisor
**P0-7 (5c167d2) primary() macro fallback** 경로 유지 필수 — 회귀 금지.
**Sole-writer**: `RegimeService` (I-R3) — detector 는 계산만.

## 블록 map (line ranges)

| Block | Lines | 역할 | P0-7? | caller |
|---|---|---|---|---|
| A. imports + constants | 1–34 | module-level | no | internal |
| B. `RegimeState` + `_regime_confidence` | 36–59 | shared types | no | detectors |
| C. `BaseRegimeDetector` | 62–263 | 공통 스코어/학습 | no | Crypto/Macro parent |
| D. `CryptoRegimeDetector.update` | 265–362 | alt F&G + funding + OI + ADX | no | `update_crypto` |
| E. `MacroRegimeDetector.update` | 365–508 | CNN F&G + HY + MOVE + VIX + DXY | no | `update_macro` |
| F. Manager ctor + `_GROUP_WEIGHTS` | 511–559 | entry | no | RegimeService |
| G. `check_crisis_escalation` | 561–623 | global override | no | RegimeService.tick |
| H. `_recalc_group_regimes` + alias | 625–723 | per-group weighted | no | internal |
| I. `for_group` / `for_group_dynamic` | 725–809 | group dispatch | no | strategy, dashboard |
| J. `for_ticker` 3-layer + helpers | 811–964 | ticker/group/global blend | no | docs 만 언급 |
| K. **`primary()` macro fallback (P0-7)** + `current()` | 966–1031 | SSOT | **YES** | market_context |
| L. `record_trade_outcome`, `state_dict` | 1033–1082 | learning + dashboard | no | RegimeService |
| M. `RegimeDetector` alias | 1085–1087 | back-compat | no | legacy |

## 분할 우선순위

1. **J → `regime_three_layer.py`** (~160L, P0-7 무관) ← **이번 batch**
2. H → `regime_per_group.py` (~100L) — 중간 위험
3. C + B → `regime_base.py` — 광범위 재배선
4. D, E → `regime_detectors.py` — 마지막

## 1 extraction — `regime_three_layer.py`

**범위**: J block (`for_ticker`, `_ticker_tech_regime`, `_group_stats_regime`) + 상수
**이유**:
- P0-7 `primary()` 경로와 독립
- Sole-writer (RegimeService) 무관 — `for_ticker` 는 RegimeState 리턴만
- 외부 caller 0 (Grep `for_ticker` → 코드 hit 없음, docs 언급만)
- DB CHECK enum (Regime) 유지

**구현**:
- 새 파일 `invasion/market/regime_three_layer.py` 순수 함수:
  - `ticker_tech_regime(tech_data) -> Optional[Regime]`
  - `compute_group_stats_regime(group, cache, cache_ts) -> (Optional[Regime], cache, ts)`
  - `blend_three_layer(ticker_r, group_r, global_state) -> RegimeState`
- `regime.py` 메서드는 얇은 래퍼 유지
- `Regime`, `RegimeState` 는 `regime.py` 에서 재-import (순환 없음)

## Preservation checks

- `from invasion.market.regime import primary, Regime` 유지
- `python3 -c "import invasion.main"` 통과
- `wc -l regime.py` > 600 시 2차 batch 필요 (H → per-group)
