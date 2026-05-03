# Scale Normalization — Jin "단위별 다 다르니 정량화 공식"

## 문제

현재 `atr_expansion_threshold_pct=2.0` 같은 **절대값 threshold** 가 모든 ticker 에 동일 적용. 하지만:
- BTC avg atr_pct ≈ 0.3 (major, 안정)
- SHIB avg atr_pct ≈ 2.5 (alt, volatile)
- EUR/USD ≈ 0.05 (forex major)
- USD/JPY ≈ 0.06 (JPY cross)
- Corn ≈ 0.4 (commodity)
- TSLA ≈ 0.15 (stock)

**절대 threshold 2.0 은**:
- BTC 에겐 엄격 (6× avg)
- SHIB 에겐 느슨 (0.8× avg — 평범)
- Forex 에겐 도달 불가능

## 해결 — Normalized ATR (ticker baseline 대비)

```
normalized_atr = atr_pct / ticker_baseline_atr_7d_median

trigger: normalized_atr >= 1.5  (평균 대비 50% 높음)
```

**모든 ticker 에 공정** — 각자 기준 대비 expansion 여부만 본다.

## DB Schema

```sql
CREATE TABLE IF NOT EXISTS ticker_baseline (
    ticker TEXT PRIMARY KEY,
    asset_group TEXT NOT NULL,
    atr_pct_median REAL NOT NULL,    -- 7d rolling median
    atr_pct_p75 REAL NOT NULL,       -- p75 reference
    size_usd_median REAL,            -- typical size per ticker
    n_samples INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tbl_group ON ticker_baseline(asset_group);
```

## 신규 파일 `invasion/strategy/ticker_baseline.py` (~90줄)

```python
"""Per-ticker volatility baseline — scale normalization 구조.

모든 ticker 가 자기 baseline 대비 expansion 여부로 평가됨.
Scale mismatch 구조적 해소 (BTC $60K vs SHIB $0.00001).
"""
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional
import threading

_cache_lock = threading.Lock()
_cache: dict = {}
_cache_loaded_at: float = 0.0
_CACHE_TTL_SEC = 3600.0

@dataclass
class TickerBaseline:
    ticker: str
    asset_group: str
    atr_pct_median: float
    atr_pct_p75: float
    size_usd_median: float
    n_samples: int

def compute_baselines(conn: sqlite3.Connection, lookback_days: int = 7) -> list[dict]:
    """Per-ticker 7d atr_pct median / p75 / size_usd_median 집계."""
    cutoff = time.time() - lookback_days * 86400
    cur = conn.execute("""
        SELECT ticker, asset_group, atr_at_entry, size_usd
        FROM trades
        WHERE exit_ts >= ?
          AND atr_at_entry IS NOT NULL
          AND atr_at_entry > 0
          AND exit_type NOT IN ('orphan_cleanup','broker_removed',
                                'startup_orphan_cleanup','adopted_pending')
    """, (cutoff,))
    # ticker → list of (atr_pct, size_usd)
    bucket = {}
    for row in cur.fetchall():
        tkr, grp, atr, sz = row
        if not tkr or not grp:
            continue
        bucket.setdefault((tkr, grp), []).append((atr, sz or 0))
    rows = []
    now = time.time()
    for (tkr, grp), samples in bucket.items():
        if len(samples) < 5:  # 최소 5 trades
            continue
        atrs = sorted([s[0] for s in samples])
        szs = sorted([s[1] for s in samples if s[1] > 0])
        n = len(atrs)
        rows.append({
            "ticker": tkr,
            "asset_group": grp,
            "atr_pct_median": atrs[n // 2],
            "atr_pct_p75": atrs[int(n * 0.75)],
            "size_usd_median": szs[len(szs) // 2] if szs else 0,
            "n_samples": n,
            "updated_at": now,
        })
    return rows

def upsert_baselines(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    cur.execute("DELETE FROM ticker_baseline")
    cur.executemany("""
        INSERT INTO ticker_baseline
        (ticker, asset_group, atr_pct_median, atr_pct_p75,
         size_usd_median, n_samples, updated_at)
        VALUES (:ticker,:asset_group,:atr_pct_median,:atr_pct_p75,
                :size_usd_median,:n_samples,:updated_at)
    """, rows)
    conn.commit()
    return len(rows)

def _refresh_cache(conn):
    global _cache, _cache_loaded_at
    with _cache_lock:
        cur = conn.execute("""
            SELECT ticker, asset_group, atr_pct_median, atr_pct_p75,
                   size_usd_median, n_samples
            FROM ticker_baseline
        """)
        _cache = {
            r[0]: TickerBaseline(
                ticker=r[0], asset_group=r[1], atr_pct_median=r[2],
                atr_pct_p75=r[3], size_usd_median=r[4], n_samples=r[5]
            ) for r in cur.fetchall()
        }
        _cache_loaded_at = time.time()

def get_baseline(ticker: str, conn=None) -> Optional[TickerBaseline]:
    global _cache_loaded_at
    if conn and (time.time() - _cache_loaded_at) > _CACHE_TTL_SEC:
        try: _refresh_cache(conn)
        except: pass
    return _cache.get(ticker)

def normalized_atr(ticker: str, raw_atr_pct: float, conn=None) -> float:
    """raw atr_pct → ticker baseline 대비 배수.
    
    Return: 1.0 = baseline, 1.5 = 50% above, 2.0 = double.
    Fallback: baseline 없으면 raw value 반환.
    """
    b = get_baseline(ticker, conn)
    if b is None or b.atr_pct_median <= 0:
        return raw_atr_pct
    return raw_atr_pct / b.atr_pct_median

def refresh_and_store(conn, lookback_days: int = 7) -> dict:
    rows = compute_baselines(conn, lookback_days)
    n = upsert_baselines(conn, rows)
    _refresh_cache(conn)
    return {"rows": len(rows), "written": n}
```

## Wire

### 1. Cell matrix reviewer 에 ticker_baseline refresh 추가
- Hourly tick 에서 `ticker_baseline.refresh_and_store(conn)` 함께 호출
- Or 별도 `TickerBaselineReviewer` (14번째)

### 2. `_pipeline_sizing.py` atr_expansion 에 normalized 사용
```python
# 기존: if _atr_pct >= _atr_thr:
# 변경:
from ..strategy.ticker_baseline import normalized_atr
_norm_atr = normalized_atr(ticker, _atr_pct, conn=self._data_store._conn if self._data_store else None)
if _norm_atr >= _atr_thr:  # threshold 재해석: "baseline 대비 N배"
    ...
```

### 3. Preg bounds 재조정
```python
# 기존: atr_expansion_threshold_pct = 2.0 (절대값)
# 변경: atr_expansion_threshold_norm = 1.5 (ticker baseline 대비 배수)
# default 1.5 = 평균 대비 50% 높음
```

## 확장 가능

- **size normalization**: `size_usd / size_usd_median` 으로 "typical bet 대비" 표현
- **all preg 재해석**: threshold, cap 모두 per-ticker baseline 기반

## 구현 규모
- DB schema: 15줄
- `ticker_baseline.py`: 110줄
- Wire (sizing + reviewer): 30줄
- Preg: 5줄
- **총 ~160줄, 2-3 files**

## 북극성 정합
- ✅ Data-driven (ticker baseline 실측)
- ✅ Amplify-only (mult ≥ 1.0 유지)
- ✅ Scale noise 구조적 해소
- ✅ Micro-cap 도 공정 평가 (자기 기준 대비)
