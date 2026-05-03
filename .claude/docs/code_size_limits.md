# Code Size Limits — 파일 길이 상한

> Jin 2026-04-16 지시. "너무 길면 맨날 헷갈리잖아." 헷갈림 = AI/Jin 판단 저하 = 비용.

## 기본 상한 (invasion/**/*.py)

| 구간 | 라인 | 조치 |
|------|------|------|
| 🟢 Clean | ≤ 400 | OK, 유지 |
| 🟡 Warn | 401-600 | 다음 refactor 시점에 분할 검토 |
| 🟠 Split | 601-800 | **신기능 추가 금지**, 먼저 모듈 분리 |
| 🔴 Violate | > 800 | P1 태스크 자동 생성 (분할) |
| ⛔ Critical | > 1000 | P0 태스크 (즉시 분할) |

## 현재 위반 현황 (2026-04-16 실측)

- ⛔ `pipeline.py` 1735, `main.py` 1520, `providers_extended.py` 1374
- ⛔ `store.py` 1371, `okx/public.py` 1168, `engine.py` 1129
- ⛔ `param_registry.py` 1073, `data_collector.py` 1022
- 🔴 `regime.py` 994, `dashboard/data.py` 967, `okx/paper.py` 947, `candle_cache.py` 939

→ `dev_tasks.md` 에 `[SIZE-SPLIT]` P1 batch 로 큐레이션.

## 분할 원칙

1. **기능 경계로 분할** — 단순 줄 자르기 금지. "responsibility" 단위
2. **기존 import 경로 유지** — `from invasion.trade.pipeline import X` 살리는 `__init__.py` re-export
3. **behavior change 0** — 분할 전/후 smoke 5-step 동일 결과
4. **분할 후** — 각 파일 ≤ 600 목표, 최소 ≤ 800
5. **commit**: `refactor: split <orig> into <new_a>/<new_b> (behavior 0)`

## MD/문서

- 모든 MD ≤ 60 줄 (`feedback_md_max_60_lines_split`)
- 예외: IPC 저널 (`tasks/*_to_*.md`), `dev_tasks.md` (Living Catalog)
- 초과 시 분리 + 상호 참조

## 측정

```bash
wc -l invasion/**/*.py | sort -rn | head -20
```

## 참조

- `feedback_code_integrity` — 덧대기 금지, 통합만
- `feedback_md_max_60_lines_split` — 문서 분리 원칙
