"""Neural Cloud — Polaris pipeline as nested spheres (Jin 2026-04-27).

A안 mixup cleanup (Jin 2026-04-27):
- BRAIN sphere = AI decision RESULTS data (10 dynamic nodes from ai_calls + ai_decisions)
- 10 AI judges (functions) MOVED → ORBIT 'ai_judge' kind (위성)
- group cluster 제거 (Phase 2.5 잔재; tier 4 left empty for index compat)
- EXIT_TALLY outer satellite (8 nodes, tier 13, dynamic exit_type counts)

Hierarchy (8 main + 5 outer ring):
- T0 POS    — live open positions (heart)
- T1 EXIT   — exit pattern data
- T2 EXEC   — execution layer (gates + routers data)
- T3 REG    — regime states (5)
- T4 (empty — group cluster removed)
- T5 STRAT  — strategies
- T6 BRAIN  — AI decision results data (10 dynamic)
- T7 WATCH  — signal watchlist
- T8 MKT    — market universe (full tradable shell)
- T9  OBS   — system health (outer ring)
- T10 ACTION — alert queue (outer ring)
- T11 ORBIT — function satellites (inter-tier + AI judges as ai_judge kind)
- T12 AXIS  — dimension axis satellites (session × liq × crisis)
- T13 EXIT_TALLY — exit_type counts (outer ring, 8 dynamic)

Cluster = tier (1:1). Color by pipeline role.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
# Jin 2026-05-02 ADR-007: galaxy snapshot reads main bot DB (historical
# structure of strategies/signal_blocks/etc) — the main bot is permanently
# disabled but its tables are still the schema this module is built for.
# Live events (events.py) read SPOT log + SPOT DB separately.  ``_SPOT_DB``
# remains the SPOT-specific overlay for SPOT-tagged graph elements.
DB = ROOT / "data" / "invasion.sqlite"
OUT = Path(__file__).parent / "static" / "graph.json"
_SPOT_DB = str(ROOT / "data" / "invasion_spot.sqlite")

# Pipeline-tier clusters — 8 main + 5 outer ring (Jin 2026-04-27 mixup cleanup)
# Removed: 'group' (Phase 2.5 잔재 — Jin 분류 룰 위배). Added: 'exit_tally' (T13 outer).
CLUSTERS = [
    {"id": "pos",   "label": "live positions",  "color": "#87d7ff", "tier": 0},
    {"id": "exit",  "label": "exit patterns",   "color": "#ff87d7", "tier": 1},
    {"id": "exec",  "label": "execution",       "color": "#87afd7", "tier": 2},
    {"id": "reg",   "label": "regime context",  "color": "#d7d787", "tier": 3},
    # tier 4 left empty (group cluster removed for index compat with frame functions)
    {"id": "strat", "label": "strategies",      "color": "#ff9f87", "tier": 5},
    {"id": "brain", "label": "ai decisions",    "color": "#d7afff", "tier": 6},  # results data, dynamic
    {"id": "watch", "label": "signal watch",    "color": "#87ffd7", "tier": 7},
    {"id": "mkt",   "label": "market shell",    "color": "#ffaf87", "tier": 8},
    {"id": "exit_tally", "label": "exit tally", "color": "#ff87af", "tier": 13},  # NEW outer ring
]
CLUSTER_BY_TIER = {c["tier"]: c["id"] for c in CLUSTERS}

TIER_SHARE = {
    0:  60,    # live positions (dynamic, padding skipped now)
    1:   8,    # exit types (8 real)
    2:  10,    # gates (7) + routers (3) — Jin cleanup: tools 위성으로
    3:   5,    # regime states (5 real) — Jin cleanup: axis 위성으로
    4:   0,    # GROUP cluster 제거 (Phase 2.5 잔재) — tier 4 empty for index compat
    5:  60,    # strategies
    6:  10,    # AI decision results data (dynamic, 10 nodes)
    7: 120,    # signal watchlist
    8: 200,    # MKT (Jin 2026-04-30 랙 fix: 2200 → 200, base node cost 90% ↓)
               # 2200 was "full tradable universe" but 99% dormant. 200 = active + lit + dormant sample.
}
NODE_COUNT = sum(TIER_SHARE.values())  # ~473 (Jin 2026-04-30 reduced from 2473)

# Tier 1 — EXIT (Jin 2026-04-27 cleanup: data only, engines 위성으로 이동)
# Real exit_types (DB-verified 7d count) — exit "types" 는 결과 데이터
EXIT_COMPONENTS = [
    "exit_TP", "exit_STOP", "exit_TRAIL", "exit_TIME", "exit_BEP",
    "exit_SIGNAL", "exit_orphan_cleanup", "exit_broker_removed",
]
# (engines/handlers/wires 는 ORBIT_EXIT_ENGINES 로 이동)

# Tier 2 — EXEC (Jin 2026-04-27 cleanup: gates + routers data only, tools 위성으로)
EXEC_COMPONENTS = [
    "gate_h0_universe", "gate_h1_signal", "gate_h2_regime",
    "gate_h3_correlation", "gate_h4_concentration",
    "gate_h5_throttle", "gate_h6_kill_switch",
    "router_okx", "router_capital", "router_alpaca",
]
# (pipeline_sizing/param_registry/direction_modifier/cell_pooling 는 ORBIT_EXEC_TOOLS 로 이동)

# Tier 3 — REGIME (Jin 2026-04-27 cleanup: data only, 펑션은 위성으로 이동)
# REG tier 안에는 ONLY regime states 5개 — 진짜 regime
REGIME_STATES = ["risk_off", "risk_on", "neutral", "transition", "crisis"]
REG_COMPONENTS = [f"regime_{r}" for r in REGIME_STATES]   # 5 노드만

# Tier 4 — REMOVED (mixup cleanup, Jin 2026-04-27)
# Was: GROUP (asset_group main sphere). Phase 2.5 잔재 → 제거.
# Asset group 정보는 cell_matrix axis (T12 AXIS) 또는 POS node 의 asset_group 메타로 충분.
# tier index 4 는 frame functions (CLUSTER_BY_TIER, TIER_RADIUS, tierClusters arrays) 호환을
# 위해 보존하되 0 nodes — 빈 tier (CLUSTERS list 에서도 제거됨).

# === 함수/구조 → 별도 위성 (T10 ORBITS / T11 AXIS) ===
# T10 ORBITS — 시스템 함수 위성 (regime 의 펑션 + AI tools + signal providers)
# Jin: "각 레이어마다 펑션이 있고 그건 위성이고 실제 데이터는 구가 되는거지"
ORBIT_REGIME_INFRA = ["regime_history", "regime_hysteresis"]
# Phase 2.6 (Jin 2026-04-27): manual_legacy = 기존 placeholder 보존 + auto-enum 으로
# invasion/signals/providers*.py 의 실제 SignalProvider/DataProviderBase 자식 모두 추가
# (Jin "저것들도 클라우드에 들어가야" — MyfxbookProvider 등 16+ 누락 회복)
MANUAL_LEGACY_SENSORS = [
    "fear_intensity", "volatility_index", "macro_regime",
    "prov_dxy", "prov_vix", "prov_move", "prov_fear_greed",
]
MANUAL_LEGACY_PROVIDERS = [
    # 기존 placeholder — 실제 invasion code 매칭 class 없으나 future feature 가능성으로 보존
    "prov_orderflow", "prov_vwap", "prov_onchain_val", "prov_basis_spread",
    "prov_liq_cascade", "prov_google_trends", "prov_llm_sentiment",
    "prov_funding", "prov_taker", "prov_ls_ratio", "prov_basis",
    "prov_oi", "prov_ml_meta",
]


def _camel_to_snake(name: str) -> str:
    """CamelCase → snake_case (acronym safe: HTTPServer → http_server)."""
    import re as _re
    s = _re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = _re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
    return s.lower()


def _enumerate_provider_classes() -> dict[str, list[tuple[str, str]]]:
    """invasion/signals/providers*.py 에서 SignalProvider/DataProviderBase 자식 class + register name 자동 enum.

    Returns: {"signal": [(class_name, register_name), ...], "data": [...]}

    register_name = class 안 self.name = "X" 또는 super().__init__("X", ...) 의 첫 string arg
    없으면 camel_to_snake(class_name without Signal/Provider suffix) fallback.
    """
    import re as _re
    result: dict[str, list[tuple[str, str]]] = {"signal": [], "data": []}
    candidates = [
        ROOT / "invasion" / "signals",
        Path(__file__).parent.parent.parent / "invasion" / "signals",
    ]
    provider_dir = None
    for c in candidates:
        if c.exists():
            provider_dir = c
            break
    if provider_dir is None:
        return result
    cls_pattern = _re.compile(r'class\s+(\w+)\s*\(\s*(SignalProvider|DataProviderBase)\s*\)\s*:')
    # name 패턴: self.name = "X" 또는 super().__init__("X", ...)
    name_self_pat = _re.compile(r'self\.name\s*=\s*[\"\']([\w_]+)[\"\']')
    name_super_pat = _re.compile(r'super\(\)\.__init__\(\s*[\"\']([\w_]+)[\"\']')
    name_ctor_pat = _re.compile(r'__init__\(self[^)]*\):\s*[^\n]*\n\s*super\(\)\.__init__\(\s*[\"\']([\w_]+)[\"\']')
    for py in sorted(provider_dir.glob("providers*.py")):
        try:
            s = py.read_text()
        except Exception:
            continue
        # Each class: split body to find __init__ name argument
        matches = list(cls_pattern.finditer(s))
        for i, m in enumerate(matches):
            cls, base = m.group(1), m.group(2)
            if cls in ("DataProviderBase", "SignalProvider"):
                continue
            # body = from this class start to next class start (or EOF)
            body_start = m.start()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
            body = s[body_start:body_end]
            # Find register name: self.name first, then super().__init__("X", ...)
            reg = None
            m2 = name_self_pat.search(body)
            if m2:
                reg = m2.group(1)
            else:
                m3 = name_super_pat.search(body)
                if m3:
                    reg = m3.group(1)
            if not reg:
                # fallback: camel_to_snake(class_name without Signal/Provider suffix)
                stripped = cls.replace("Signal", "").replace("Provider", "")
                reg = _camel_to_snake(stripped)
            key = "signal" if base == "SignalProvider" else "data"
            result[key].append((cls, reg))
    return result


# SignalProvider 자식 중 sensor 로 분류할 keyword (low-frequency regime context)
# 나머지 SignalProvider 자식 + 모든 DataProviderBase 자식 → provider
_SENSOR_KEYWORDS = ("Sentiment", "FearGreed", "Funding", "LSRatio", "Taker")


def _is_sensor(cls: str) -> bool:
    return any(kw in cls for kw in _SENSOR_KEYWORDS)


_PROV = _enumerate_provider_classes()
# label → register_name 매핑 (DB signals.providers CSV 매칭용)
PROVIDER_REGISTER_NAME: dict[str, str] = {}
_AUTO_SENSORS: list[str] = []
_AUTO_PROVIDERS: list[str] = []
for _cls, _reg in _PROV["signal"]:
    _label = "sig_" + _reg   # use register name directly (DB matchable)
    PROVIDER_REGISTER_NAME[_label] = _reg
    if _is_sensor(_cls):
        _AUTO_SENSORS.append(_label)
    else:
        _AUTO_PROVIDERS.append(_label)
for _cls, _reg in _PROV["data"]:
    _label = "data_" + _reg
    PROVIDER_REGISTER_NAME[_label] = _reg
    _AUTO_PROVIDERS.append(_label)

# 최종 ORBIT lists — manual_legacy ∪ auto-enum (중복 제거 + 정렬)
ORBIT_SENSORS = sorted(set(MANUAL_LEGACY_SENSORS) | set(_AUTO_SENSORS))
ORBIT_PROVIDERS = sorted(set(MANUAL_LEGACY_PROVIDERS) | set(_AUTO_PROVIDERS))

ORBIT_HOURLY_LEARNERS = ["hourly_learner_wr", "hourly_learner_streak",
                         "hourly_learner_dd", "hourly_learner_exit",
                         "hourly_learner_volume", "hourly_learner_phs"]
ORBIT_BRAIN_TOOLS = ["composer", "signal_engine", "cell_matrix", "gate_matrix",
                     "hourly_stats", "evolver", "phs_factor", "loss_attribution"]
ORBIT_EXIT_ENGINES = ["exit_cycle", "close_handler", "exit_advisor", "exit_learner",
                      "size_cap", "demote_loss", "fsm_harvest_trail",
                      "profit_cap_regime", "harness_alerter"]
ORBIT_EXEC_TOOLS = ["pipeline_sizing", "param_registry", "direction_modifier",
                    "cell_pooling"]
# AI judges (Jin 2026-04-27 mixup cleanup): 함수 = 위성. BRAIN sphere 에서 분리.
# 위치: T5-T6 mid (BRAIN 인근 visible, 적도 0.0 near-equator)
ORBIT_AI_JUDGES = ["openai_judge", "claude_critic", "gemini_conviction",
                   "ai_advisor", "ai_controller", "ai_modulator", "ml_filter",
                   "cusum_drift", "cell_learn", "cell_factor_composer"]
ORBIT_COMPONENTS = (ORBIT_REGIME_INFRA + ORBIT_SENSORS + ORBIT_PROVIDERS
                    + ORBIT_HOURLY_LEARNERS + ORBIT_BRAIN_TOOLS
                    + ORBIT_EXIT_ENGINES + ORBIT_EXEC_TOOLS
                    + ORBIT_AI_JUDGES)
# ---- Phase 2 (Jin 2026-04-27): own-axis rotation helpers ----
# 위성이 자기 축으로 회전 (메인 sphere 자전과 독립).
# spin_axis: deterministic id-hash 기반 normalized 3-vector (x,y,z)
# spin_speed: state-tier (firing 빠름 / lit 보통 / dormant 느림, rad/s)
def _spin_axis_from_id(node_id: str) -> list[float]:
    """Return deterministic normalized (x,y,z) from id hash. 같은 id = 같은 axis."""
    h = 0
    for ch in node_id or "":
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    # split hash into 3 components, range [-1, 1]
    cx = ((h & 0x3FF) / 0x3FF) * 2 - 1
    cy = (((h >> 10) & 0x3FF) / 0x3FF) * 2 - 1
    cz = (((h >> 20) & 0x3FF) / 0x3FF) * 2 - 1
    n = (cx * cx + cy * cy + cz * cz) ** 0.5
    if n < 1e-6:
        return [0.0, 1.0, 0.0]  # fallback up-axis
    return [round(cx / n, 4), round(cy / n, 4), round(cz / n, 4)]


def _spin_speed_for_state(state: str, node_id: str = "") -> float:
    """state 기반 spin speed (rad/s) + per-id jitter (같은 state 안에서도 미세 차이)."""
    h = 0
    for ch in node_id or "":
        h = (h * 17 + ord(ch)) & 0xFFFF
    jitter = (h / 0xFFFF)  # 0..1
    if state == "firing":
        return round(1.0 + 0.5 * jitter, 4)   # 1.0 .. 1.5
    if state == "lit":
        return round(0.5 + 0.5 * jitter, 4)   # 0.5 .. 1.0
    # dormant / default
    return round(0.2 + 0.3 * jitter, 4)        # 0.2 .. 0.5


def _orbit_kind(label: str) -> str:
    if label in ORBIT_REGIME_INFRA:    return "regime_infra"
    if label in ORBIT_SENSORS:         return "sensor"
    if label in ORBIT_PROVIDERS:       return "provider"
    if label in ORBIT_HOURLY_LEARNERS: return "learner"
    if label in ORBIT_BRAIN_TOOLS:     return "brain_tool"
    if label in ORBIT_EXIT_ENGINES:    return "exit_engine"
    if label in ORBIT_EXEC_TOOLS:      return "exec_tool"
    if label in ORBIT_AI_JUDGES:       return "ai_judge"   # NEW (mixup cleanup)
    return "unknown"

# Phase 2.5 (Jin 2026-04-27): GROUP main sphere 신설로 tier shift
# 9 main sphere (T0-T8) + 4 outer ring (T9-T12)
# inter-tier 위성 mid radius = adjacent TIER_RADIUS 의 평균
#   TIER_RADIUS = [0.115, 0.215, 0.32, 0.42, 0.515, 0.61, 0.71, 0.81, 0.91, ...]
#   T0↔T1 mid ≈ (0.115+0.215)/2 = 0.165 → 0.185 spec (close enough, use spec value)
#   T1↔T2 mid ≈ (0.215+0.32)/2 = 0.27  → 0.30 spec
#   T2↔T3 mid ≈ (0.32+0.42)/2  = 0.37  (비움)
#   T3↔T4 mid ≈ (0.42+0.515)/2 = 0.47  → 0.42 spec (REG↔GROUP, regime_infra/sensor)
#   T4↔T5 mid ≈ (0.515+0.61)/2 = 0.56  (비움 — 향후 group_strategy_filter 자리)
#   T5↔T6 mid ≈ (0.61+0.71)/2  = 0.66  → 0.61 spec (STRAT↔BRAIN, learner)
#   T6↔T7 mid ≈ (0.71+0.81)/2  = 0.76  → 0.74 spec (BRAIN↔WATCH, brain_tool)
#   T7↔T8 mid ≈ (0.81+0.91)/2  = 0.86  → 0.87 spec (WATCH↔MKT, provider)
ORBIT_TRANSITION = {
    # Jin 2026-04-27 (위성 layer 시각 분리):
    #   inter_radius 충돌 제거 — 8 카테고리가 8 transition gap 에 distributed.
    #   sensor/regime_infra 둘다 0.46 → 분리 (sensor 0.43 inner, regime_infra 0.50 outer).
    #   learner/ai_judge 인접 0.66/0.68 → 분리 (ai_judge 0.71, brain_tool 0.76 와도 분리).
    # Jin 2026-04-28 v16: EXEC (gate/router) + BRAIN (AI decision data) 위성 이동.
    #   "데이터 = 표면, 함수 = 위성" + "한 ticker 고정 X = 함수" mandate.
    "exit_engine":  (0, 1, 0.185),  # POS ↔ EXIT
    "exec_router":  (0, 1, 0.16),   # POS ↔ EXIT inner (execution path 가장 가까움)
    "brain_exit":   (0, 1, 0.20),   # POS ↔ EXIT outer (exit_advise AI)
    "exec_tool":    (1, 2, 0.30),   # EXIT ↔ EXEC
    "exec_gate":    (1, 3, 0.36),   # EXIT ↔ REG mid (gate filter — signal evaluation 직전)
    "brain_entry":  (3, 5, 0.46),   # REG ↔ STRAT (entry decision — entry_judge / signal_augment)
    "sensor":       (3, 5, 0.43),   # REG ↔ STRAT inner (regime sensing → strat)
    "regime_infra": (3, 5, 0.50),   # REG ↔ STRAT outer (regime infra)
    "learner":      (5, 6, 0.63),   # STRAT ↔ BRAIN gap inner
    "ai_judge":     (5, 6, 0.68),   # STRAT ↔ BRAIN gap outer
    "brain_data":   (5, 7, 0.71),   # STRAT ↔ WATCH (AI decision results — generic)
    "brain_tool":   (6, 7, 0.76),   # BRAIN ↔ WATCH
    "provider":     (7, 8, 0.87),   # WATCH ↔ MKT
}

# T12 AXIS — 8-dim 차원 axis 위성 (cell_matrix dimensional context)
# Phase 2.5 (Jin 2026-04-27): GROUP main sphere 격상으로 axis_groups 제거
# 남은 axis: session × liquidity_tier × crisis (regime/exchange/strategy/direction/ticker/group 는 본 sphere)
AXIS_SESSIONS = ["session_asia_open", "session_asia_late", "session_eu_open",
                 "session_eu_late", "session_us_core", "session_us_late"]
AXIS_LIQ_TIERS = ["liq_tier_small", "liq_tier_mid", "liq_tier_large"]
AXIS_CRISIS = ["crisis_l1", "crisis_l2", "crisis_l3"]
AXIS_COMPONENTS = AXIS_SESSIONS + AXIS_LIQ_TIERS + AXIS_CRISIS    # 12 (was 19, group 7 제거)
def _axis_kind(label: str) -> str:
    if label in AXIS_SESSIONS:  return "session"
    if label in AXIS_LIQ_TIERS: return "liq"
    if label in AXIS_CRISIS:    return "crisis"
    return "unknown"


# ---- Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 vector + angular speed ----
# 위성 카테고리별 (orbit kind / axis kind / outer ring cluster) 별도 궤도 회전축 + 속도.
# 메인 sphere 자전과 독립; 각 위성 그룹이 시각적으로 분리된 면 위에서 회전.
# axis = normalized 3D unit vector (x,y,z). speed = rad/s (signed: + CCW around axis, - CW).
SAT_ORBIT = {
    # 14 카테고리 axis 명백히 분산 — Jin "다 같은 방향으로 가는데" fix
    # 각 카테고리가 시각적으로 구분되는 회전 평면 (3D 다른 방향)
    # Jin "위성 계속 자전 방향으로 도는데" fix — Y component 최소화 (메인 yaw 와 분리)
    # 자전축 = Y, autoRotate 속도 0.03 rad/s — 위성은 X/Z 축 dominated 로 명백 분리
    "regime_infra": {"axis": [1.0, 0.0, 0.0],          "speed":  0.18},   # X 축 (수평 회전, vs Y 자전)
    "sensor":       {"axis": [0.0, 0.0, 1.0],          "speed": -0.22},   # Z 축 (앞뒤, 반대)
    "provider":     {"axis": [0.7071, 0.0, 0.7071],    "speed":  0.14},   # X-Z 대각
    "learner":      {"axis": [-0.7071, 0.0, 0.7071],   "speed": -0.20},   # X-Z mirror 대각 (반대)
    "brain_tool":   {"axis": [0.5, 0.3, 0.8],          "speed":  0.12},   # X-Z + slight Y
    "exit_engine":  {"axis": [0.5774, 0.5774, 0.5774], "speed": -0.26},   # 3D 등각 (Y 1/3)
    "exec_tool":    {"axis": [0.0, 0.0, -1.0],         "speed":  0.24},   # Z 축 (반대 Z)
    "ai_judge":     {"axis": [1.0, 0.0, 0.0],          "speed":  0.32},   # X 축 (정면 tumble)
    "session":      {"axis": [0.5, -0.3, 0.8],         "speed": -0.10},   # X-Z + neg Y
    "liq":          {"axis": [-0.5, 0.3, 0.8],         "speed":  0.16},   # X-Z mirror + Y
    "crisis":       {"axis": [0.7071, 0.0, -0.7071],   "speed": -0.18},   # X-Z mirror
    "obs":          {"axis": [0.8, 0.0, 0.6],          "speed": -0.08},   # X-Z mostly
    "action":       {"axis": [-0.6, 0.3, -0.7],        "speed":  0.10},   # X-Z + slight Y
    "exit_tally":   {"axis": [0.0, 1.0, 0.0],          "speed":  0.12},   # Y 축 (메인 자전과 같은 방향, 그러나 4x 빠르게)
    # Jin 2026-04-28 v16 — EXEC/BRAIN 위성 이동 (chain gap 에 ring 형성)
    "exec_gate":    {"axis": [0.3, 0.5, 0.8],          "speed":  0.20},   # gate 위성 — EXIT↔REG gap
    "exec_router":  {"axis": [-0.4, 0.2, 0.9],         "speed": -0.16},   # router — POS↔EXIT gap
    "brain_entry":  {"axis": [0.6, 0.4, 0.7],          "speed":  0.18},   # entry decision — REG↔STRAT gap
    "brain_exit":   {"axis": [-0.5, -0.3, 0.8],        "speed": -0.22},   # exit decision — POS↔EXIT gap
    "brain_data":   {"axis": [0.7, -0.2, 0.7],         "speed":  0.14},   # generic AI data — STRAT↔WATCH
}


# ---- Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible): per-node phase + axis perturb ----
# 같은 카테고리 위성도 다른 phase + 다른 trajectory 갖게 → 동시에 같은 위치에 모이지 않음.
# deterministic (node.id hash 기반) — refresh 후에도 동일 값 유지.
def _seed_rand_py(node_id: str, salt: str) -> float:
    """Deterministic 0..1 hash from (node_id, salt). Mirror of sphere-render.js _seedRand."""
    s = f"{node_id or 'x'}::{salt or ''}"
    h = 2166136261
    for ch in s:
        h = ((h * 16777619) ^ ord(ch)) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


def _initial_orbit_angle(node_id: str) -> float:
    """deterministic 0..2π initial angle from node.id (Jin: 같은 카테고리 위성 phase 분산)."""
    h = 2166136261
    for ch in (node_id or 'x'):
        h = ((h * 16777619) ^ ord(ch)) & 0xFFFFFFFF
    return round((h % 10000) / 10000.0 * (2 * math.pi), 6)


def _perturb_axis(base_axis, node_id: str, magnitude: float = 0.40):
    """deterministic 작은 perturbation (vector 합 후 renormalize). 같은 카테고리도 약간 다른 trajectory."""
    ax = base_axis[0] + (_seed_rand_py(node_id, 'ax') - 0.5) * magnitude
    ay = base_axis[1] + (_seed_rand_py(node_id, 'ay') - 0.5) * magnitude
    az = base_axis[2] + (_seed_rand_py(node_id, 'az') - 0.5) * magnitude
    norm = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
    return [round(ax / norm, 4), round(ay / norm, 4), round(az / norm, 4)]


def _jitter_speed(base_speed: float, node_id: str) -> float:
    """deterministic speed jitter 0.85x ~ 1.15x (same category 도 약간 다른 angular speed)."""
    return round(base_speed * (0.85 + 0.30 * _seed_rand_py(node_id, 'speed')), 5)


def _normalize_axis(v):
    n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if n < 1e-6:
        return [0.0, 1.0, 0.0]
    return [round(v[0] / n, 4), round(v[1] / n, 4), round(v[2] / n, 4)]


def _orbit_vector_for(kind: str) -> tuple[list[float], float]:
    """Return (axis_vec_normalized, speed_rad_per_s) for satellite category kind."""
    spec = SAT_ORBIT.get(kind)
    if spec is None:
        return ([0.0, 1.0, 0.0], 0.0)
    return (_normalize_axis(spec["axis"]), float(spec["speed"]))

# Tier 6 BRAIN — A안 mixup cleanup (Jin 2026-04-27):
# 함수 (AI judge 10개) → ORBIT 위성으로 이동.
# BRAIN sphere = AI decision RESULTS data (sphere 표면 = 데이터, 함수 = 위성)
# 10 nodes (dynamic via ai_calls + ai_decisions counts last 30m):
BRAIN_DATA_NODES = [
    "entry_judge_pass",     # ai_calls stage=entry_judge result contains 'approve=True'
    "entry_judge_reject",   # ai_calls stage=entry_judge result contains 'approve=False'
    "exit_advise_warn",     # ai_calls stage=exit_advise trigger=DANGER
    "exit_advise_critical", # ai_calls stage=exit_advise trigger=CRITICAL
    "signal_augment_count", # ai_calls stage=signal_augment count
    "portfolio_intel_count",# ai_calls stage=portfolio_intel count
    "drift_alert_count",    # ai_calls stage=drift / cusum alert count
    "ai_hold_count",        # ai_decisions action=HOLD
    "ai_traded_count",      # ai_decisions executed=1 (any action)
    "ai_blocked_count",     # ai_decisions blocked_reason IS NOT NULL
]
BRAIN_COMPONENTS = BRAIN_DATA_NODES   # 10 nodes

# External satellite tiers (NOT inside main 8-tier sphere, ring outside)
# Jin 2026-04-27: v4 시안 차용 — OBS (system health watcher) + ACTION (alert queue)
# Sources:
#   OBS: candle fetch fail / LIVENESS_SHADOW / OKX API recover / heartbeat silence
#   ACTION: harness_alerts queue / T13 wire fires / DEMOTE_LOSS fires


def open_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---- ORBIT firing event capture (Jin "펑션 이벤트는 로그에서 가져오던지" 2026-04-27) ----
# 함수 위성 state 를 실제 event 기반으로 결정 (provider/sensor=firing 하드코드 → log+DB 기반)
def _query_provider_calls(conn) -> dict[str, int]:
    """recent 30m signals.providers CSV 에 등장 횟수 (provider 이름 → count)."""
    try:
        rows = conn.execute("""
            SELECT providers FROM signals
            WHERE ts >= strftime('%s','now') - 1800
              AND providers IS NOT NULL
        """).fetchall()
    except sqlite3.OperationalError:
        return {}
    from collections import Counter
    ct: Counter = Counter()
    for r in rows:
        for p in (r["providers"] or "").split(","):
            p = p.strip()
            if p:
                ct[p] += 1
    return dict(ct)


def _query_ai_calls_by_model(conn) -> dict[str, int]:
    """recent 30m ai_calls model count (label 매칭용)."""
    try:
        rows = conn.execute("""
            SELECT model, COUNT(*) c FROM ai_calls
            WHERE ts >= strftime('%s','now') - 1800
            GROUP BY model
        """).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(r["model"] or ""): r["c"] for r in rows}


def _query_ai_calls_by_stage(conn) -> dict[str, int]:
    """recent 30m ai_calls stage count — ai_judge label fallback (model 단일이라 stage 필요)."""
    try:
        rows = conn.execute("""
            SELECT stage, COUNT(*) c FROM ai_calls
            WHERE ts >= strftime('%s','now') - 1800
            GROUP BY stage
        """).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(r["stage"] or ""): r["c"] for r in rows}


def _probe_invasion_log_events(window_sec: int = 1800) -> dict[str, int]:
    """invasion.log tail ~30m, pattern count 일괄 (1 read, 모든 ORBIT 카테고리 매칭).

    Returns: {pattern: count} — sensor / brain_tool / exit_engine / exec_tool / regime_infra
    + Jin 2026-04-27 log-full-mapping: gate_pass / gate_reject / gate_clamp / broker_tick /
      broker_liveness / heartbeat / liveness_pass / liveness_fail / ws_reconnect.
    """
    log_path = ROOT / "data" / "invasion.log"
    PATTERNS = [
        # sensor (label 또는 prov_X 의 X uppercase)
        "fear_intensity", "volatility_index", "macro_regime",
        "DXY", "VIX", "MOVE", "FEAR", "FUNDING", "TAKER", "LS_RATIO",
        # brain_tool
        "[COMPOSER]", "[CELL_MATRIX]", "[EVOLVER]", "[PHS]",
        "[LOSS_ATTR]", "[GATE_MATRIX]", "[HOURLY_STATS]",
        "[SIGNAL_ENGINE]", "[PHS_FACTOR]",
        # exit_engine
        "[EXIT]", "[CLOSE]", "[SIZE_CAP]", "[DEMOTE_LOSS]", "[T13]",
        "[FSM_TRAIL]", "[PROFIT_CAP]", "[HARNESS_ALERT]",
        "[EXIT_CYCLE]", "[CLOSE_HANDLER]", "[EXIT_ADVISOR]",
        "[EXIT_LEARNER]", "[FSM_HARVEST_TRAIL]", "[PROFIT_CAP_REGIME]",
        "[HARNESS_ALERTER]",
        # exec_tool
        "[SIZING]", "[PARAM_REGISTRY]", "[DIRECTION_MOD]", "[CELL_POOLING]",
        "[PIPELINE_SIZING]",
        # learner
        "[HOURLY_LEARNER_WR]", "[HOURLY_LEARNER_STREAK]",
        "[HOURLY_LEARNER_DD]", "[HOURLY_LEARNER_EXIT]",
        "[HOURLY_LEARNER_VOLUME]", "[HOURLY_LEARNER_PHS]",
        # regime_infra
        "[REGIME]", "[REGIME_HISTORY]", "[REGIME_HYSTERESIS]",
        # === Jin 2026-04-27 log-full-mapping (신규) ===
        # GATE (entry gate — invasion/entry.py)
        "[GATE]",                  # 모든 gate 활동 (PASS + REJECT + CLAMP)
        "GATE_PASS",               # 제로 (현 log 에 PASS 없음 — REJECT only)
        "REJECT",                  # gate REJECT count
        "LIQUIDITY_CLAMP",         # gate sizing clamp
        # BROKER_SYNC
        "[BROKER_SYNC]",
        "DB_INSERT_ADOPTED",
        "tick_done",
        "liveness_1h",
        # HEART
        "[   HEART]",              # heartbeat tick
        # LIVENESS
        "[LIVENESS_SHADOW]",
        # WS reconnect / recover
        "resubscrib", "reconnect", "recovered",
        # CAP_WS / OKX / ALP — ws activity
        "[  CAP_WS]", "[     OKX]", "[     ALP]", "[  ALP_WS]",
        # SCHED — scheduler tick
        "[   SCHED]",
        # SIGNAL — engine evaluate (PASS/REJECT)
        "[  SIGNAL]",
        "PASS",                    # signal PASS
        # CANDLE
        "[  CANDLE]",
        # BUS — trade event bus
        "[     BUS]",
        # ML_META / ML_FILTER
        "[ ML_META]", "[ML_FILTER]",
        # ANOMALY / CUSUM
        "[ ANOMALY]", "[CUSUM_DRIFT]",
        # AI controller
        "[ AI_CTRL]", "[      AI]",
        # STATS
        "[   STATS]",
        # NEW (signals new ticker discovery)
        "[NEW]",
        # PARAM / TUNE
        "[   PARAM]", "[    TUNE]",
        # STRATEGY
        "[STRATEGY]",
    ]
    counts = {p: 0 for p in PATTERNS}
    if not log_path.exists():
        return counts
    try:
        with log_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # ~200KB tail ≈ 30m (보수적; conservative budget)
            f.seek(max(0, size - 200_000))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return counts
    for line in tail.splitlines():
        for p in PATTERNS:
            if p in line:
                counts[p] += 1
    return counts


# Jin 2026-04-27 (log-full-mapping): SIGNAL PASS/REJECT 의 ticker 를 log 에서 직접 추출
# (DB signals table 외에도 — log 가 더 빠르고 fresh)
_SIGNAL_PASS_REJECT_RE = re.compile(
    r"\[\s*SIGNAL\] engine\.py:evaluate:\d+ (?:PASS|REJECT) (\S+) (?:long|short) score="
)
_GATE_TICKERS_RE = re.compile(
    # Trailing ':' (REJECT case) 또는 공백 (LIQUIDITY_CLAMP case) 둘 다 strip
    r"\[\s*GATE\] entry\.py:_(?:reject|check):\d+ (?:REJECT|LIQUIDITY_CLAMP) ([^\s:]+)"
)


def _probe_signal_tickers_from_log(window_sec: int = 1800) -> tuple[set[str], set[str]]:
    """invasion.log tail → recent SIGNAL PASS/REJECT ticker set + GATE activity ticker set.

    Returns: (signal_tickers, gate_tickers) — ticker activity 추가 source for
    firing/lit highlighting in MKT shell.

    Jin: "log 에 signal 가장 많은데 ticker 에 signal activity 안 보임" → log 에서 ticker
    직접 grep 하여 visualizer 에 노출.
    """
    log_path = ROOT / "data" / "invasion.log"
    if not log_path.exists():
        return set(), set()
    try:
        with log_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return set(), set()
    sig_tickers: set[str] = set()
    gate_tickers: set[str] = set()
    for line in tail.splitlines():
        m = _SIGNAL_PASS_REJECT_RE.search(line)
        if m:
            sig_tickers.add(m.group(1))
            continue
        m2 = _GATE_TICKERS_RE.search(line)
        if m2:
            gate_tickers.add(m2.group(1))
    return sig_tickers, gate_tickers


def _resolve_orbit_state(kind: str, label: str,
                          provider_calls: dict[str, int],
                          ai_calls: dict[str, int],
                          ai_calls_stage: dict[str, int],
                          log_events: dict[str, int]) -> tuple[str, float, int]:
    """카테고리별 firing event count → (state, intensity, cnt).

    firing: count >= threshold, intensity 0.50 + count*0.03 (cap 0.85)
    lit:    count > 0,           intensity 0.40
    dormant:count == 0,          intensity 0.20

    Jin 2026-04-27: cnt 도 반환 (size_mul = min(1.3, 0.8 + cnt/100) 계산용).
    """
    cnt = 0
    threshold_firing = 2  # default

    if kind == "provider":
        # label 형태: sig_funding / data_myfxbook → register name 직접 사용 (PROVIDER_REGISTER_NAME)
        prov = PROVIDER_REGISTER_NAME.get(label)
        if not prov:
            # fallback: strip prefix
            prov = label
            if prov.startswith("sig_"):
                prov = prov[4:]
            elif prov.startswith("data_"):
                prov = prov[5:]
        cnt = provider_calls.get(prov, 0)
        threshold_firing = 50  # 30m 안 수백~수천 호출 typical

    elif kind == "sensor":
        # sensor SignalProvider 도 signals.providers CSV 등장 (funding/taker/fear_greed 등)
        cnt = 0
        prov = PROVIDER_REGISTER_NAME.get(label)
        if prov:
            cnt = provider_calls.get(prov, 0)
        if not cnt:
            cnt = log_events.get(label, 0)
        if not cnt and label.startswith("prov_"):
            short = label.replace("prov_", "").upper()
            cnt = log_events.get(short, 0)
        if not cnt and label.startswith("sig_"):
            cnt = provider_calls.get(label[4:], 0)
        threshold_firing = 50  # provider 와 같은 scale

    elif kind == "ai_judge":
        # spec: ai_calls.model 매칭. 실 환경 model 단일 ("gpt-5.4") → stage fallback.
        cnt = ai_calls.get(label, 0)
        if not cnt:
            # stage 매칭 (openai_judge → entry_judge / claude_critic → exit_advise 등)
            cnt = ai_calls_stage.get(label, 0)
        if not cnt:
            # Jin 2026-04-28 매핑 fix: AI judge 노드 label → stage 직접 매핑.
            # 이전: openai_judge label 직접 lookup → stage 와 매칭 X → call_count=0
            # dormant. DB ai_calls 의 stage 값 (entry_judge / exit_advise / etc)
            # 와 visualizer label 의 명시 매핑 추가.
            # Note: exit_advisor 는 ORBIT_EXIT_ENGINES → kind="exit_engine"
            # branch 사용. AI Judge 매핑은 openai_judge/claude_critic/gemini만.
            _stage_map = {
                "openai_judge": "entry_judge",
                "claude_critic": "exit_advise",
                "gemini_conviction": "signal_augment",
            }
            mapped_stage = _stage_map.get(label)
            if mapped_stage:
                cnt = ai_calls_stage.get(mapped_stage, 0)
        if not cnt:
            # generic ai 활동량 (model count 합) — ai_advisor/controller/modulator 같이
            # 직접 매칭 없는 일반 wrapper 는 전체 ai 활동 = lit
            if label in ("ai_advisor", "ai_controller", "ai_modulator"):
                cnt = sum(ai_calls.values())
                threshold_firing = 10
            else:
                threshold_firing = 3
        else:
            threshold_firing = 3

    elif kind == "learner":
        # hourly_learner_X → [HOURLY_LEARNER_X] log
        cnt = log_events.get(f"[{label.upper()}]", 0)
        threshold_firing = 1

    elif kind == "brain_tool":
        cnt = log_events.get(f"[{label.upper()}]", 0)
        if not cnt and label == "loss_attribution":
            cnt = log_events.get("[LOSS_ATTR]", 0)
        threshold_firing = 2

    elif kind == "exit_engine":
        cnt = log_events.get(f"[{label.upper()}]", 0)
        if not cnt and label == "exit_cycle":
            cnt = log_events.get("[EXIT]", 0)
        if not cnt and label == "close_handler":
            cnt = log_events.get("[CLOSE]", 0)
        if not cnt and label == "size_cap":
            cnt = log_events.get("[SIZE_CAP]", 0)
        if not cnt and label == "demote_loss":
            cnt = log_events.get("[DEMOTE_LOSS]", 0)
        if not cnt and label == "fsm_harvest_trail":
            cnt = log_events.get("[FSM_TRAIL]", 0) + log_events.get("[T13]", 0)
        if not cnt and label == "profit_cap_regime":
            cnt = log_events.get("[PROFIT_CAP]", 0)
        if not cnt and label == "harness_alerter":
            cnt = log_events.get("[HARNESS_ALERT]", 0)
        threshold_firing = 2

    elif kind == "exec_tool":
        cnt = log_events.get(f"[{label.upper()}]", 0)
        if not cnt and label == "pipeline_sizing":
            cnt = log_events.get("[SIZING]", 0)
        threshold_firing = 2

    elif kind == "regime_infra":
        cnt = log_events.get(f"[{label.upper()}]", 0)
        if not cnt:
            cnt = log_events.get("[REGIME]", 0)
        threshold_firing = 1

    if cnt >= threshold_firing:
        return "firing", round(min(0.85, 0.50 + cnt * 0.03), 4), cnt
    if cnt > 0:
        return "lit", 0.40, cnt
    return "dormant", 0.20, cnt


def _build_external_satellites(conn) -> tuple[list[dict], list[dict]]:
    """OBS (system health) + ACTION (harness alert queue) for outer ring (Jin v4 차용).

    Returns (obs_checks, action_queue).
    OBS: log-tail + sqlite probes — recent LIVENESS_SHADOW failures, candle fetch fails,
         OKX API recover events, CAP_WS heartbeat, sqlite age.
    ACTION: .claude/harness_alerts/*.md unprocessed list + recent T13/DEMOTE fires.
    """
    import time as _t
    obs: list[dict] = []
    action: list[dict] = []
    log_path = ROOT / "data" / "invasion.log"

    # OBS — recent log probes (cheap tail of last ~3000 lines = ~30min)
    if log_path.exists():
        try:
            with log_path.open("rb") as f:
                f.seek(0, 2); size = f.tell()
                f.seek(max(0, size - 200_000)); tail = f.read().decode("utf-8", errors="replace")
            lines = tail.splitlines()[-2000:]
            liveness_fail = sum(1 for l in lines if "LIVENESS_SHADOW" in l and "FAIL" in l)
            heartbeat = sum(1 for l in lines if "Heartbeat silence" in l)
            okx_recover = sum(1 for l in lines if "OKX" in l and "API recovered" in l)
            candle_fail = sum(1 for l in lines if "Candle fetch" in l and "failed" in l and "0 failed" not in l)
            yahoo_fail = sum(1 for l in lines if "Yahoo fail" in l)
            exit_adv_fail = sum(1 for l in lines if "ExitAdviser failed" in l)
            # Jin 2026-04-27 log-full-mapping: heartbeat tick / broker_sync / ws_reconnect 추가
            heart_tick = sum(1 for l in lines if "[   HEART]" in l and "heartbeat.py:tick" in l)
            broker_tick = sum(1 for l in lines if "[BROKER_SYNC]" in l)
            ws_recover = sum(1 for l in lines if ("resubscrib" in l or "reconnect" in l or "recovered" in l)
                              and ("CAP_WS" in l or "OKX" in l or "ALP" in l))
            obs.extend([
                {"id": "liveness_shadow", "label": "LIVENESS",
                 "value": liveness_fail, "unit": "/30m", "ok": liveness_fail < 5},
                {"id": "cap_heartbeat", "label": "CAP HEARTBEAT",
                 "value": heartbeat, "unit": "/30m", "ok": heartbeat < 30},
                {"id": "okx_recover", "label": "OKX RECOVER",
                 "value": okx_recover, "unit": "/30m", "ok": okx_recover < 10},
                {"id": "candle_fail", "label": "CANDLE FAIL",
                 "value": candle_fail, "unit": "/30m", "ok": candle_fail < 30},
                {"id": "yahoo_fail", "label": "YAHOO FAIL",
                 "value": yahoo_fail, "unit": "/30m", "ok": yahoo_fail < 5},
                {"id": "exit_adviser", "label": "EXIT ADVISER",
                 "value": exit_adv_fail, "unit": "fail/30m", "ok": exit_adv_fail < 5},
                # Jin 2026-04-27 log-full-mapping: heartbeat / broker_sync / ws_reconnect
                {"id": "heart_tick", "label": "HEART TICK",
                 "value": heart_tick, "unit": "/30m", "ok": heart_tick > 0},
                {"id": "broker_sync", "label": "BROKER SYNC",
                 "value": broker_tick, "unit": "/30m", "ok": broker_tick > 0},
                {"id": "ws_recover", "label": "WS RECOVER",
                 "value": ws_recover, "unit": "/30m", "ok": ws_recover < 30},
            ])
        except Exception:
            pass
    # SQLite WAL freshness
    try:
        max_exit = conn.execute(
            "SELECT MAX(exit_ts) FROM trades WHERE status='closed'"
        ).fetchone()[0] or 0
        age_sec = int(_t.time() - max_exit) if max_exit else 99999
        obs.append({"id": "trade_age", "label": "LAST TRADE",
                    "value": age_sec, "unit": "s", "ok": age_sec < 600})
    except Exception:
        pass
    # Open positions count (lower bound health)
    try:
        open_n = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open'"
        ).fetchone()[0] or 0
        obs.append({"id": "open_count", "label": "OPEN POS",
                    "value": open_n, "unit": "", "ok": 1 <= open_n <= 300})
    except Exception:
        pass

    # ACTION — harness alerts queue
    alert_dir = ROOT / ".claude" / "harness_alerts"
    if alert_dir.exists():
        try:
            now = _t.time()
            files = sorted(alert_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[:25]:
                age_min = int((now - p.stat().st_mtime) / 60)
                # category from filename like "1776990104_subsystem_cost.md"
                parts = p.stem.split("_", 1)
                category = parts[1] if len(parts) > 1 else parts[0]
                # severity heuristic
                if "critical" in category.lower() or "crit" in category.lower():
                    sev = "CRIT"
                elif "high" in category.lower() or "loss_streak" in category.lower():
                    sev = "HIGH"
                elif "silent" in category.lower():
                    sev = "WARN"
                else:
                    sev = "INFO"
                # only recent (< 4h) shown as active
                if age_min > 240:
                    continue
                action.append({
                    "id": p.stem, "label": category.upper().replace("_", " ")[:18],
                    "sev": sev, "since_min": age_min,
                })
        except Exception:
            pass
    return obs, action


def _load_portfolio_unrealized() -> dict:
    """Live unrealized snapshot — SPOT bot state takes precedence (main bot
    permanently disabled 2026-05-01). Falls back to legacy
    ``data/portfolio_state.json`` only when its positions map is dict-shaped.

    Returns: {ticker: {pnl_usd, pnl_pct, size_usd, direction, exchange}}
    """
    out: dict = {}
    try:
        with Path("/tmp/invasion_spot_state.json").open() as f:
            spot = json.load(f)
        spot_pos = spot.get("positions") or {}
        if isinstance(spot_pos, dict):
            for ticker, p in spot_pos.items():
                size = float(p.get("size_usd") or p.get("entry_value_usd") or 0)
                pct = float(p.get("unrealized_pnl_pct") or p.get("pnl_pct") or 0)
                upl = float(p.get("unrealized_pnl_usd") or (size * pct / 100.0))
                out[ticker] = {
                    "pnl_usd": round(upl, 2),
                    "pnl_pct": round(pct, 4),
                    "size_usd": size,
                    "direction": (p.get("direction") or "long").lower(),
                    "exchange": (p.get("exchange") or "okx").lower(),
                }
    except Exception:
        pass
    try:
        with (ROOT / "data" / "portfolio_state.json").open() as f:
            state = json.load(f)
    except Exception:
        return out
    legacy_pos = state.get("positions")
    if isinstance(legacy_pos, dict):
        for ticker, p in legacy_pos.items():
            if ticker in out:
                continue
            size = p.get("size_usd", 0) or 0
            pct = p.get("pnl_pct", 0) or 0
            upl = size * pct / 100.0
            out[ticker] = {
                "pnl_usd": round(upl, 2),
                "pnl_pct": round(pct, 4),
                "size_usd": size,
                "direction": (p.get("direction") or "long").lower(),
                "exchange": (p.get("exchange") or "okx").lower(),
            }
    return out


def _fetch_spot_open_positions() -> list[dict]:
    """SPOT bot's open trades from invasion_spot.sqlite — main DB has no
    SPOT rows since the main bot is permanently disabled.  Schema differs
    (entry_px / net_pnl_usd / no max_profit_pct), so we map columns here.
    """
    out: list[dict] = []
    try:
        # WAL-aware: mode=ro fails when -shm/-wal companion files exist
        # because read-only mode cannot create them.  Plain connect is fine
        # for read queries — SQLite handles shared locks transparently.
        c = sqlite3.connect(_SPOT_DB, timeout=2.0)
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, ticker, strategy_id, side,
                   entry_ts, entry_px, size_usd, asset_group, tier,
                   net_pnl_usd, pnl_pct, peak_px
            FROM trades
            WHERE status = 'open' AND ticker IS NOT NULL
            ORDER BY entry_ts DESC
            LIMIT 200
        """).fetchall()
        c.close()
        now = int(time.time())
        for r in rows:
            entry_ts = r["entry_ts"] or 0
            entry_px = r["entry_px"] or 0
            peak_px = r["peak_px"] or entry_px
            max_profit_pct = (
                ((peak_px - entry_px) / entry_px * 100.0)
                if entry_px else 0
            )
            out.append({
                "trade_id": r["id"],
                "ticker": r["ticker"],
                "strategy_id": r["strategy_id"] or "unknown",
                "exchange": "okx_spot",
                "direction": "long",  # SPOT Phase α: long-only
                "entry_ts": entry_ts,
                "entry_price": entry_px,
                "current_price": peak_px,
                "size_usd": r["size_usd"] or 0,
                "asset_group": (r["asset_group"] or "crypto").lower(),
                "regime": "neutral",  # Phase α stub
                "pnl_usd": r["net_pnl_usd"] or 0,
                "pnl_pct": r["pnl_pct"] or 0,
                "max_profit_pct": round(max_profit_pct, 4),
                "hold_seconds": max(0, now - entry_ts),
                "tier": (r["tier"] or "mid").lower(),
            })
    except Exception:
        pass
    return out


def fetch_pipeline_state(conn) -> dict:
    """Return everything needed to populate tiers."""
    portfolio_live = _load_portfolio_unrealized()
    # Tier 0 — open positions (모든 open trade 별도 노드, dedup 제거).
    # Jin 2026-04-28 v23: ticker dedup 제거 — 같은 ticker multi-position 모두
    # visible (Cocoa US 36 short 등). 사용자 "포지션 제대로 안 되는데" mandate.
    # Jin 2026-05-02: SPOT bot's invasion_spot.sqlite carries the live trades.
    open_positions = _fetch_spot_open_positions()
    rows = conn.execute("""
        SELECT id, ticker, strategy_id, exchange, direction,
               entry_ts, entry_price, size_usd, asset_group, regime,
               pnl_usd, pnl_pct, max_profit_pct, hold_seconds
        FROM trades
        WHERE status = 'open' AND ticker IS NOT NULL
        ORDER BY entry_ts DESC
    """).fetchall()
    for r in rows:
        entry_price = r["entry_price"] or 0
        # Override DB pnl/direction/size with portfolio_state.json live (broker truth)
        live = portfolio_live.get(r["ticker"], {})
        pnl_pct = live.get("pnl_pct") if live else (r["pnl_pct"] or 0)
        direction = live.get("direction") if live else ((r["direction"] or "long").lower())
        # Current price derive: long → entry*(1+pct/100); short → entry*(1-pct/100)
        # (pnl_pct signed by profit direction; for short, profit when price drops)
        if direction == "short":
            current_price = entry_price * (1 - pnl_pct / 100) if entry_price else 0
        else:
            current_price = entry_price * (1 + pnl_pct / 100) if entry_price else 0
        open_positions.append({
            "trade_id": r["id"],
            "ticker": r["ticker"],
            "strategy_id": r["strategy_id"] or "unknown",
            "exchange": (r["exchange"] or "okx").lower(),
            "direction": direction,
            "entry_ts": r["entry_ts"] or 0,
            "entry_price": entry_price,
            "current_price": round(current_price, 6) if current_price < 10 else round(current_price, 2),
            "size_usd": (live.get("size_usd") if live else r["size_usd"]) or 0,
            "asset_group": r["asset_group"] or "crypto",
            "regime": (r["regime"] or "neutral").lower(),
            "pnl_usd": (live.get("pnl_usd") if live else r["pnl_usd"]) or 0,
            "pnl_pct": pnl_pct,
            "max_profit_pct": r["max_profit_pct"] or 0,
            "hold_seconds": r["hold_seconds"] or 0,
        })

    # Tier 4 — top strategies from strategy_performance (canonical, matches trades.strategy_id)
    top_strategies = []
    try:
        rows = conn.execute("""
            SELECT sp.strategy_id,
                   AVG(sp.win_rate) avg_wr,
                   SUM(sp.trade_count) total_tc,
                   AVG(sp.profit_factor) avg_pf
            FROM strategy_performance sp
            WHERE sp.trade_count > 0
            GROUP BY sp.strategy_id
            ORDER BY total_tc DESC
            LIMIT 70
        """).fetchall()
        for r in rows:
            wr = r["avg_wr"] or 0
            # strategy_performance.win_rate is 0-100 percent
            wr_norm = wr / 100.0 if wr > 1 else wr
            top_strategies.append({
                "id": r["strategy_id"],
                "status": "active",
                "fitness": 0,
                "n_trades_24h": r["total_tc"] or 0,   # use long-term as activity proxy
                "pnl_24h": 0,
                "win_rate_24h": wr_norm,
                "win_rate_long": wr_norm,
                "trade_count_long": r["total_tc"] or 0,
                "profit_factor": r["avg_pf"] or 0,
            })
    except sqlite3.OperationalError:
        pass

    # Tier 3 — top tickers by 24h volume
    top_tickers = []
    rows = conn.execute("""
        SELECT ticker, exchange, asset_group, COUNT(*) n
        FROM trades
        WHERE ticker IS NOT NULL
          AND entry_ts >= strftime('%s','now') - 86400
        GROUP BY ticker
        ORDER BY n DESC
        LIMIT 200
    """).fetchall()
    for r in rows:
        top_tickers.append({
            "ticker": r["ticker"],
            "exchange": (r["exchange"] or "okx").lower(),
            "asset_group": r["asset_group"] or "crypto",
            "n": r["n"],
        })

    # Recent activity sets
    lit_tickers = set()
    rows = conn.execute("""
        SELECT DISTINCT ticker FROM trades
        WHERE ticker IS NOT NULL
          AND exit_ts >= strftime('%s','now') - 3600
    """).fetchall()
    for r in rows:
        lit_tickers.add(r["ticker"])

    firing_tickers = {p["ticker"] for p in open_positions}

    # Jin 2026-04-27 (log-full-mapping): firing_tickers 확장 — recent 30m signal_pass +
    # gate REJECT + DB signals (Jin: "signal 가장 많은데 ticker 에 signal activity 안 보임").
    # signal_pass = strong activity (firing). gate REJECT = weaker (lit).
    log_sig_tickers, log_gate_tickers = _probe_signal_tickers_from_log()
    # DB signals (last 30m) — supplement log probe
    db_sig_tickers: set[str] = set()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM signals
            WHERE ts >= strftime('%s','now') - 1800
              AND ticker IS NOT NULL
        """).fetchall()
        for r in rows:
            db_sig_tickers.add(r["ticker"])
    except sqlite3.OperationalError:
        pass
    # signal_pass tickers (log) → firing (signal-led activity)
    firing_tickers |= log_sig_tickers
    # gate REJECT + DB signals → lit (touched but not entered)
    lit_tickers |= log_gate_tickers
    lit_tickers |= db_sig_tickers

    # Galaxy universe — UNION of ALL tradable tickers (broadest possible cosmic cloud)
    # Sources: trades + ticker_stats + ticker_baseline + strategy_cell_matrix
    galaxy_universe = []
    seen_g = set()
    # Primary: trades (have exchange + asset_group)
    rows = conn.execute("""
        SELECT ticker, exchange, asset_group, COUNT(*) n
        FROM trades
        WHERE ticker IS NOT NULL
        GROUP BY ticker
        ORDER BY n DESC
    """).fetchall()
    for r in rows:
        if r["ticker"] in seen_g:
            continue
        seen_g.add(r["ticker"])
        galaxy_universe.append({
            "ticker": r["ticker"],
            "exchange": (r["exchange"] or "okx").lower(),
            "asset_group": r["asset_group"] or "crypto",
            "n_24h": r["n"] or 0,
        })
    # Augment from instrument_profiles — canonical 1922 ticker scan universe
    try:
        rows = conn.execute("""
            SELECT ticker, exchange, asset_group, asset_type
            FROM instrument_profiles
            WHERE ticker IS NOT NULL
        """).fetchall()
        for r in rows:
            if r["ticker"] in seen_g:
                continue
            seen_g.add(r["ticker"])
            galaxy_universe.append({
                "ticker": r["ticker"],
                "exchange": (r["exchange"] or "unknown").lower(),
                "asset_group": r["asset_group"] or r["asset_type"] or "unknown",
                "n_24h": 0,
            })
    except sqlite3.OperationalError:
        pass
    # Augment from ticker_stats / ticker_baseline (catches anything still missing)
    for table in ("ticker_stats", "ticker_baseline"):
        try:
            rows = conn.execute(f"SELECT DISTINCT ticker FROM {table} WHERE ticker IS NOT NULL").fetchall()
            for r in rows:
                if r[0] in seen_g:
                    continue
                seen_g.add(r[0])
                galaxy_universe.append({
                    "ticker": r[0], "exchange": "unknown",
                    "asset_group": "unknown", "n_24h": 0,
                })
        except sqlite3.OperationalError:
            pass
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker, exchange, asset_group
            FROM strategy_cell_matrix
            WHERE ticker IS NOT NULL
        """).fetchall()
        for r in rows:
            if r["ticker"] in seen_g:
                continue
            seen_g.add(r["ticker"])
            galaxy_universe.append({
                "ticker": r["ticker"],
                "exchange": (r["exchange"] or "unknown").lower(),
                "asset_group": r["asset_group"] or "crypto",
                "n_24h": 0,
            })
    except sqlite3.OperationalError:
        pass

    # Recent closes — last 30 closed trades (newest first)
    recent_closes = []
    rows = conn.execute("""
        SELECT ticker, direction, pnl_usd, pnl_pct, exit_type,
               exit_ts, hold_seconds, exchange
        FROM trades
        WHERE status = 'closed' AND exit_ts IS NOT NULL
        ORDER BY exit_ts DESC
        LIMIT 30
    """).fetchall()
    for r in rows:
        recent_closes.append({
            "ticker": r["ticker"],
            "direction": (r["direction"] or "long").lower(),
            "pnl_usd": r["pnl_usd"] or 0,
            "pnl_pct": r["pnl_pct"] or 0,
            "exit_type": r["exit_type"] or "?",
            "exit_ts": r["exit_ts"] or 0,
            "hold_seconds": r["hold_seconds"] or 0,
            "exchange": (r["exchange"] or "okx").lower(),
        })

    # Per-ticker technical metrics — used for MKT intensity (volatility/wr → glow/size)
    ticker_tech = {}
    try:
        rows = conn.execute("""
            SELECT b.ticker,
                   COALESCE(b.atr_pct_median, 0) atr,
                   COALESCE(b.signal_score_median, 0) score_med,
                   COALESCE(s.win_rate, 0) wr,
                   COALESCE(s.trade_count, 0) tc,
                   COALESCE(s.avg_pnl, 0) avg_pnl,
                   COALESCE(s.blocked, 0) blocked
            FROM ticker_baseline b
            LEFT JOIN ticker_stats s ON s.ticker = b.ticker
        """).fetchall()
        for r in rows:
            ticker_tech[r["ticker"]] = {
                "atr": r["atr"], "score_med": r["score_med"],
                "wr": r["wr"], "tc": r["tc"],
                "avg_pnl": r["avg_pnl"], "blocked": r["blocked"],
            }
    except sqlite3.OperationalError:
        pass

    # WATCH list — tickers currently being scored (Codex fix: include exchange)
    watch_tickers = []
    try:
        rows = conn.execute("""
            SELECT s.ticker, COUNT(*) n_scored, MAX(ABS(s.score)) max_score,
                   COALESCE(ip.exchange, t.exchange) exchange
            FROM signals s
            LEFT JOIN instrument_profiles ip ON ip.ticker = s.ticker
            LEFT JOIN (SELECT ticker, exchange FROM trades GROUP BY ticker) t ON t.ticker = s.ticker
            WHERE s.ts >= strftime('%s','now') - 300
            GROUP BY s.ticker
            ORDER BY max_score DESC, n_scored DESC
            LIMIT 200
        """).fetchall()
        for r in rows:
            watch_tickers.append({
                "ticker": r["ticker"],
                "n_scored": r["n_scored"] or 0,
                "max_score": r["max_score"] or 0,
                "exchange": (r["exchange"] or "unknown").lower(),
            })
    except sqlite3.OperationalError:
        pass

    # Multi-axis context — what regimes / sessions / groups are currently active
    active_regimes_1h = set()
    rows = conn.execute("""
        SELECT DISTINCT regime FROM trades
        WHERE regime IS NOT NULL
          AND (entry_ts >= strftime('%s','now') - 3600
               OR exit_ts >= strftime('%s','now') - 3600)
    """).fetchall()
    for r in rows:
        active_regimes_1h.add((r[0] or "").lower())

    active_groups_open = {p["asset_group"] for p in open_positions}

    # Sydney AEST hour → session mapping (Codex fix: AEST=UTC+10, AEDT=UTC+11)
    # Use system local time (machine in Sydney) to get correct hour incl. DST
    import time as _t
    aest_hour = _t.localtime().tm_hour
    if 9 <= aest_hour < 13:    active_session = "session_asia_open"
    elif 13 <= aest_hour < 17: active_session = "session_asia_late"
    elif 17 <= aest_hour < 20: active_session = "session_eu_open"
    elif 20 <= aest_hour < 23: active_session = "session_eu_late"
    elif aest_hour >= 23 or aest_hour < 4: active_session = "session_us_core"
    else:                       active_session = "session_us_late"

    return {
        "open_positions": open_positions,
        "top_strategies": top_strategies,
        "top_tickers": top_tickers,
        "lit_tickers": lit_tickers,
        "firing_tickers": firing_tickers,
        "recent_closes": recent_closes,
        "active_regimes_1h": active_regimes_1h,
        "active_groups_open": active_groups_open,
        "active_session": active_session,
        "galaxy_universe": galaxy_universe,
        "watch_tickers": watch_tickers,
        "ticker_tech": ticker_tech,
    }


def fetch_spot_pipeline_state() -> list[dict]:
    """Return SPOT umbrella nodes — OKX SPOT (crypto) + Alpaca (stock/etf/crypto).

    Jin 2026-05-01: product-type 통합. SPOT = long-only / 실물 / no liquidation.
    Alpaca trades from main bot DB included as SPOT cluster (read-only).

    Cluster 'spot_data' tier 12, lime green (CFD = main bot regular cluster).
    """
    nodes: list[dict] = []
    # 1. OKX SPOT crypto from invasion_spot.sqlite
    if Path(_SPOT_DB).exists():
        try:
            conn = sqlite3.connect(f"file:{_SPOT_DB}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            for r in conn.execute(
                "SELECT id, ticker, entry_px, size_usd, strategy_id "
                "FROM trades WHERE status='open' LIMIT 50"):
                nodes.append({
                    "id": f"pos_spot_okx_{r['id']}",
                    "label": r["ticker"],
                    "ticker": r["ticker"],
                    "tier": 12, "cluster": "spot_data",
                    "kind": "pos_spot",
                    "size_usd": r["size_usd"] or 0,
                    "asset_group": "crypto",
                    "exchange": "okx",
                })
            for r in conn.execute(
                "SELECT strategy_id, COUNT(*) n FROM trades "
                "WHERE entry_ts >= strftime('%s','now')-86400 "
                "GROUP BY strategy_id LIMIT 10"):
                nodes.append({
                    "id": f"strat_spot_okx_{r['strategy_id'].replace(',', '_')}",
                    "label": r["strategy_id"],
                    "tier": 12, "cluster": "spot_data",
                    "kind": "strat_spot",
                    "n": r["n"],
                    "asset_group": "crypto",
                    "exchange": "okx",
                })
            conn.close()
        except sqlite3.Error:
            pass
    # 2. Alpaca SPOT (stock/etf/crypto) from main DB
    if Path(DB).exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            for r in conn.execute(
                "SELECT id, ticker, asset_group, size_usd FROM trades "
                "WHERE status='open' AND exchange='alpaca' LIMIT 50"):
                nodes.append({
                    "id": f"pos_spot_alpaca_{r['id']}",
                    "label": r["ticker"],
                    "ticker": r["ticker"],
                    "tier": 12, "cluster": "spot_data",
                    "kind": "pos_spot",
                    "size_usd": r["size_usd"] or 0,
                    "asset_group": r["asset_group"] or "stock",
                    "exchange": "alpaca",
                })
            conn.close()
        except sqlite3.Error:
            pass
    return nodes


def build_galaxy() -> dict:
    conn = open_ro()
    st = fetch_pipeline_state(conn)

    nodes = []
    idx = 0
    label_to_idx: dict[tuple, int] = {}  # (cluster, label) → node idx (for chain resolution)

    def add_node(n):
        nonlocal idx
        n["i"] = idx
        # Jin 2026-04-28: per-node phase desync. breathe sin wave (4.8s 주기,
        # 18% amplitude) 가 phase=0 동기 시 같은 cluster 의 모든 노드 동시
        # 깜빡 (예: STRAT 60개 일제 sync) → "주기적 글로잉" Jin 보고. Hash 로
        # deterministic per-id phase 부여 (재시작 시 stable + 노드 별 desync).
        if "phase" not in n:
            try:
                _h = abs(hash(n.get("id") or n.get("label") or str(idx)))
                n["phase"] = (_h % 6283) / 1000.0   # 0~6.28 (2π) range
            except Exception:
                n["phase"] = 0.0
        nodes.append(n)
        label_to_idx[(n["cluster"], n["label"])] = idx
        idx += 1

    # ---- Tier 0 POS — Live positions (innermost) ----
    # Jin 2026-04-27: dormant slot 제거 (의미 없는 placeholder).
    # POS = open positions only, exchange-grouped sub-spheres (3 colonies inside).
    for i, p in enumerate(st["open_positions"]):
        # POS size formula (Jin): base + |pnl_pct| * scale, max cap
        # 기본 0.25 / 1% movement = +0.25 / cap 1.30 (radius ~6.6 max)
        pct_abs = abs(p["pnl_pct"] or 0)
        intensity = min(1.30, 0.25 + pct_abs * 0.25)
        pnl_abs = abs(p["pnl_usd"] or 0)
        # state: loser (pnl_usd < 0) 강조, winner = lit, near-zero = dormant
        if p["pnl_usd"] is not None and p["pnl_usd"] < -5:
            state = "firing"   # loser 강조 (red glow via direction color)
        elif p["pnl_usd"] is not None and p["pnl_usd"] > 5:
            state = "lit"      # winner
        else:
            state = "dormant"  # near zero
        # Jin 2026-04-27 dynamic size: position notional 비례 (size_usd $5k = 1.5x cap)
        size_usd_val = abs(p.get("size_usd") or 0)
        size_mul = round(min(1.5, 0.7 + size_usd_val / 5000.0), 4)
        # Jin v23: trade_id 기반 unique id — multi-position per ticker collision 방지.
        _pos_id = f"pos_{p.get('trade_id') or (p.get('exchange') or 'x') + '_' + p['ticker']}"
        add_node({
            "id": _pos_id, "label": p["ticker"],
            "ticker": p["ticker"], "direction": p["direction"],
            "exchange": p["exchange"],
            "trade_id": p.get("trade_id"),
            "strategy_id": p.get("strategy_id"),
            "asset_group": (p.get("asset_group") or "unknown").lower(),  # Jin v9 group placement
            "pnl_usd": p["pnl_usd"], "pnl_pct": p["pnl_pct"], "size_usd": p["size_usd"],
            "intensity": intensity,
            "size_mul": size_mul,
            "cluster": "pos", "tier": 0, "state": state,
        })

    # ---- Tier 1 EXIT — exit patterns intensity by recent fire frequency ----
    exit_share = TIER_SHARE[1]
    # Count exit_type frequency in last 1h (intensity) + 30m (size_mul, Jin 2026-04-27)
    exit_counts = {}
    exit_counts_30m = {}
    try:
        rows = conn.execute("""
            SELECT exit_type, COUNT(*) c FROM trades
            WHERE status='closed' AND exit_ts >= strftime('%s','now') - 3600
            GROUP BY exit_type
        """).fetchall()
        for r in rows:
            if r["exit_type"]:
                exit_counts[r["exit_type"]] = r["c"]
        rows30 = conn.execute("""
            SELECT exit_type, COUNT(*) c FROM trades
            WHERE status='closed' AND exit_ts >= strftime('%s','now') - 1800
            GROUP BY exit_type
        """).fetchall()
        for r in rows30:
            if r["exit_type"]:
                exit_counts_30m[r["exit_type"]] = r["c"]
    except sqlite3.OperationalError:
        pass
    max_exit_count = max(exit_counts.values()) if exit_counts else 1

    for i in range(exit_share):
        label = EXIT_COMPONENTS[i % len(EXIT_COMPONENTS)] if i < len(EXIT_COMPONENTS) else f"exit_{i:02d}"
        # Pattern label like 'exit_TP' / 'exit_TIME' / 'exit_STOP' → match exit_type
        cnt30 = 0
        if label.startswith("exit_"):
            pat = label.replace("exit_", "")
            cnt = exit_counts.get(pat, 0)
            cnt30 = exit_counts_30m.get(pat, 0)
            if cnt > 0:
                state = "firing" if cnt >= max_exit_count * 0.5 else "lit"
                intensity = 0.30 + min(0.65, cnt / max(max_exit_count, 1) * 0.65)
            else:
                state = "lit"
                intensity = 0.30           # Jin v15 0.20→0.30 dormant 도 visible
        else:
            # Handlers (exit_cycle, close_handler etc.) — ambient lit
            state = "lit"
            intensity = 0.40
        # Jin 2026-04-28 v15: minimum 1.0 → 모든 exit type visible (사용자 "왜 두개만
        # 있는거 같이 보임"). cap 1.5 → 1.7 (강한 winner exit_type 더 큼).
        size_mul = round(min(1.7, 1.0 + cnt30 / 60.0), 4)
        add_node({
            "id": f"exit_{label}", "label": label,
            "ticker": None, "intensity": intensity,
            "size_mul": size_mul,
            "count_30m": cnt30,
            "cluster": "exit", "tier": 1, "state": state,
        })

    # ---- Tier 2 EXEC — gates + routers (Jin 2026-04-27 log-full-mapping) ----
    # Gate state: log [GATE] activity 기반 (REJECT count + LIQUIDITY_CLAMP count).
    # Router state: open_positions 의 exchange 분포 기반 (active exchange = firing).
    # Jin: "gate activity 도 없음" → log probe 로 firing/lit/dormant 정확하게 분배.
    exec_share = TIER_SHARE[2]
    gate_log_events = _probe_invasion_log_events()
    gate_total = (gate_log_events.get("[GATE]", 0)
                  + gate_log_events.get("REJECT", 0)
                  + gate_log_events.get("LIQUIDITY_CLAMP", 0))
    active_exchanges = {p["exchange"] for p in st["open_positions"]}
    # Jin 2026-04-28 v14 — gate 별 reject reason 분리 ("우리 게이트 1개야?
    # 1게이트 몰빵당해있는데"). 이전: 모든 gate 가 같은 gate_total → 같은 size.
    # 새: log probe 로 reason 별 count → 각 gate node 분리.
    _gate_reason_counts = {
        "gate_h0_universe":     0,  # liquidity / no_ws_feed
        "gate_h1_signal":       0,  # signal-related rejects
        "gate_h2_regime":       0,  # regime block
        "gate_h3_correlation":  0,  # correlation cap
        "gate_h4_concentration":0,  # concentration cap
        "gate_h5_throttle":     0,  # cooldown / throttle
        "gate_h6_kill_switch":  0,  # kill / disabled
    }
    try:
        import re as _re_g
        with open("data/invasion.log", "rb") as _gf:
            _gf.seek(0, 2); _gsize = _gf.tell()
            _gf.seek(max(0, _gsize - 500_000))
            _gtail = _gf.read().decode("utf-8", errors="replace")
        for _gline in _gtail.split("\n"):
            if "GATE" not in _gline or "REJECT" not in _gline:
                continue
            _gl = _gline.lower()
            if "liquidity" in _gl or "no_ws_feed" in _gl:
                _gate_reason_counts["gate_h0_universe"] += 1
            elif "regime" in _gl:
                _gate_reason_counts["gate_h2_regime"] += 1
            elif "correlat" in _gl:
                _gate_reason_counts["gate_h3_correlation"] += 1
            elif "concentr" in _gl:
                _gate_reason_counts["gate_h4_concentration"] += 1
            elif "cooldown" in _gl or "throttle" in _gl or "repeat" in _gl:
                _gate_reason_counts["gate_h5_throttle"] += 1
            elif "kill" in _gl or "disable" in _gl:
                _gate_reason_counts["gate_h6_kill_switch"] += 1
            else:
                _gate_reason_counts["gate_h1_signal"] += 1
    except Exception:
        pass
    _gate_max = max(_gate_reason_counts.values()) or 1
    for i in range(exec_share):
        label = EXEC_COMPONENTS[i % len(EXEC_COMPONENTS)] if i < len(EXEC_COMPONENTS) else f"exec_{i:02d}"
        if label.startswith("gate_"):
            # Gate 별 reject count 기반 firing/lit/dormant. count >= max*0.5 firing.
            _g_cnt = _gate_reason_counts.get(label, 0)
            if _g_cnt >= max(5, _gate_max * 0.5):
                state = "firing"
                intensity = round(min(0.85, 0.45 + _g_cnt * 0.002), 4)
            elif _g_cnt > 0:
                state = "lit"
                intensity = 0.40
            else:
                state = "dormant"
                intensity = 0.15
            # Size: 각 gate count 비례 (이전 모든 gate 같은 size 였음).
            size_mul = round(min(1.4, 0.7 + _g_cnt / 100.0), 4)
        elif label.startswith("router_"):
            ex = label.replace("router_", "").replace("capital", "cap")
            if ex in active_exchanges:
                state = "firing"
                intensity = 0.65
                size_mul = 1.4
            else:
                state = "dormant"
                intensity = 0.12
                size_mul = 0.7
        else:
            state = "lit" if st["open_positions"] else "dormant"
            intensity = 0.30 if state == "lit" else 0.10
            size_mul = 1.0 if state == "lit" else 0.7
        # Jin 2026-04-28 v16: EXEC = 함수 (gate / router) 위성으로 이동.
        # gate_* → kind='exec_gate' (EXIT↔REG gap 에 ring 형성)
        # router_* → kind='exec_router' (POS↔EXIT gap)
        if label.startswith("gate_"):
            _ek = "exec_gate"
        elif label.startswith("router_"):
            _ek = "exec_router"
        else:
            _ek = "exec_tool"
        _exec_transition = ORBIT_TRANSITION.get(_ek)
        _node_id = f"exec_{label}"
        _exec_node = {
            "id": _node_id, "label": label,
            "ticker": None, "intensity": intensity,
            "size_mul": size_mul,
            "orbit_kind": _ek,
            "shape": "square",
            "cluster": "orbit", "tier": 11, "state": state,
            "spin_axis": _spin_axis_from_id(_node_id),
            "spin_speed": _spin_speed_for_state(state, _node_id),
            "orbit_axis": _perturb_axis(_orbit_vector_for(_ek)[0], _node_id),
            "orbit_speed": _jitter_speed(_orbit_vector_for(_ek)[1], _node_id),
            "initial_orbit_angle": _initial_orbit_angle(_node_id),
        }
        if _exec_transition is not None:
            _exec_node["inter_a"] = _exec_transition[0]
            _exec_node["inter_b"] = _exec_transition[1]
            _exec_node["inter_radius"] = _exec_transition[2]
        add_node(_exec_node)

    # ---- Tier 3 REG — multi-matrix context (intensity = activity strength) ----
    reg_share = TIER_SHARE[3]
    active_regimes = st["active_regimes_1h"]
    active_groups = st["active_groups_open"]
    active_session = st["active_session"]
    # Per-axis activity counts for intensity strength
    regime_counts = {}  # regime → 1h trade count
    group_counts = {}   # asset_group → open count
    for p in st["open_positions"]:
        regime_counts[p["regime"]] = regime_counts.get(p["regime"], 0) + 1
        group_counts[p["asset_group"]] = group_counts.get(p["asset_group"], 0) + 1
    # Jin 2026-04-27 dynamic size: current regime 1.5x, non-current 0.6x
    for i in range(reg_share):
        label = REG_COMPONENTS[i % len(REG_COMPONENTS)] if i < len(REG_COMPONENTS) else f"reg_{i:02d}"
        is_active = False
        if label.startswith("regime_"):
            rname = label.replace("regime_", "")
            cnt = regime_counts.get(rname, 0)
            if rname in active_regimes or cnt > 0:
                state = "firing"
                intensity = 0.45 + min(0.50, cnt / 30.0)  # more positions = brighter regime
                is_active = True
            else:
                state = "dormant"; intensity = 0.06
        elif label.startswith("session_"):
            if label == active_session:
                state = "firing"; intensity = 0.85
                is_active = True
            else:
                state = "dormant"; intensity = 0.08
        elif label.startswith("group_"):
            gname = label.replace("group_", "")
            cnt = group_counts.get(gname, 0)
            if gname in active_groups:
                state = "firing"
                intensity = 0.40 + min(0.55, cnt / 20.0)
                is_active = True
            else:
                state = "dormant"; intensity = 0.06
        elif label.startswith("crisis_"):
            state = "dormant"; intensity = 0.06
        elif label.startswith("liq_tier_"):
            state = "lit"; intensity = 0.35
        elif label.startswith("hourly_learner_"):
            state = "lit"; intensity = 0.45
        else:
            state = "lit"; intensity = 0.40
        size_mul = 1.5 if is_active else 0.6
        add_node({
            "id": f"reg_{label}", "label": label,
            "ticker": None, "intensity": intensity,
            "size_mul": size_mul,
            "cluster": "reg", "tier": 3, "state": state,
        })

    # ---- Tier 4 GROUP — REMOVED (Jin 2026-04-27 mixup cleanup) ----
    # group cluster 제거 (Phase 2.5 잔재). Tier 4 는 빈 채로 둠 (frame functions 인덱스 호환).
    # asset_group 정보는 POS node 메타 + AXIS 위성 으로 충분히 표현됨.
    pass

    # ---- Tier 5 STRAT — strategies (own tier, firing if has open position) ----
    strat_share = TIER_SHARE[5]
    open_strat_ids = {p["strategy_id"] for p in st["open_positions"]}
    for i in range(strat_share):
        if i < len(st["top_strategies"]):
            s = st["top_strategies"][i]
            label = s["id"]
            active = s["status"] == "active"
            has_open = s["id"] in open_strat_ids
            n_trades = s["n_trades_24h"] or 0
            wr = s["win_rate_24h"] or 0
            pnl_24h = s["pnl_24h"] or 0
            # Multi-factor intensity: WR (0..1) + activity (0..1) + profit (0..1)
            wr_factor = max(0, wr - 0.4) * 1.5  # WR > 40% starts adding
            act_factor = min(1.0, n_trades / 30.0)
            profit_factor = max(0, min(1.0, pnl_24h / 50.0))  # +$50 = full profit boost
            base_intensity = 0.05 + 0.35 * wr_factor + 0.30 * act_factor + 0.20 * profit_factor
            if has_open:
                state = "firing"
                intensity = min(1.0, 0.55 + base_intensity)
            elif active and n_trades > 0:
                state = "lit"
                intensity = min(0.85, 0.20 + base_intensity)
            else:
                state = "dormant"
                intensity = 0.08
        else:
            label = f"strat_{i:03d}"
            state = "dormant"
            intensity = 0.04
            n_trades = 0
        # Jin 2026-04-28 v2 size redesign — 1차 fix (cap 2.0) cap saturate 14/20
        # firing 여전 → cap 2.0 → 1.5 축소 + divisor 100 → 150 (slower saturate).
        # MAX_NODE_RADIUS_PER_TIER[5] 도 5.5 → 4.0 으로 같이 좁힘.
        # n_trades 75 = 1.0 / 150 = 1.5 (cap) / 300 = 1.5 — winner strategy
        # 안에서도 lit/firing state factor 로 차별 (active 기준).
        _base_mul = min(1.5, 0.5 + n_trades / 150.0)
        _state_f = {"firing": 1.0, "lit": 0.7}.get(state, 0.4)  # dormant 0.4
        size_mul = round(_base_mul * _state_f, 4)
        # Jin 2026-04-28 group placement: strategy_id prefix → asset_group
        # ("crypto_specialist_*" → crypto). renderer 가 group azimuth band 사용.
        _sid_lower = (label or "").lower()
        _strat_group = "unknown"
        for _g in ("crypto", "forex", "indices", "commodity", "stock", "etf"):
            if _sid_lower.startswith(_g + "_") or f"_{_g}_" in _sid_lower:
                _strat_group = _g
                break
        add_node({
            "id": f"strat_{label}", "label": label,
            "ticker": None, "intensity": intensity,
            "size_mul": size_mul,
            "trades_24h": n_trades,
            "asset_group": _strat_group,                   # Jin 2026-04-28 group placement
            "cluster": "strat", "tier": 5, "state": state,
        })

    # ---- Tier 6 BRAIN — AI decision RESULTS data (Jin 2026-04-27 mixup cleanup) ----
    # 함수 (AI judges) 는 ORBIT 위성으로 이동. BRAIN sphere 표면 = 데이터 (results).
    # 10 nodes from ai_calls (last 30m) + ai_decisions (last 30m). 동적 — 하드코드 X.
    brain_share = TIER_SHARE[6]
    brain_counts = {k: 0 for k in BRAIN_DATA_NODES}
    try:
        rows = conn.execute("""
            SELECT stage, result FROM ai_calls
            WHERE ts >= strftime('%s','now') - 1800
        """).fetchall()
        for r in rows:
            stage = (r["stage"] or "").lower()
            result = (r["result"] or "")
            if stage == "entry_judge":
                if "approve=true" in result.lower():
                    brain_counts["entry_judge_pass"] += 1
                elif "approve=false" in result.lower():
                    brain_counts["entry_judge_reject"] += 1
            elif stage == "exit_advise":
                if "trigger=critical" in result.lower():
                    brain_counts["exit_advise_critical"] += 1
                elif "trigger=danger" in result.lower():
                    brain_counts["exit_advise_warn"] += 1
            elif stage == "signal_augment":
                brain_counts["signal_augment_count"] += 1
            elif stage == "portfolio_intel":
                brain_counts["portfolio_intel_count"] += 1
            elif "drift" in stage or "cusum" in stage:
                brain_counts["drift_alert_count"] += 1
    except sqlite3.OperationalError:
        pass
    try:
        rows = conn.execute("""
            SELECT action, executed, blocked_reason FROM ai_decisions
            WHERE ts >= strftime('%s','now') - 1800
        """).fetchall()
        for r in rows:
            action = (r["action"] or "").upper()
            executed = r["executed"] or 0
            blocked = r["blocked_reason"]
            if action == "HOLD":
                brain_counts["ai_hold_count"] += 1
            if executed == 1:
                brain_counts["ai_traded_count"] += 1
            if blocked:
                brain_counts["ai_blocked_count"] += 1
    except sqlite3.OperationalError:
        pass
    # Max for intensity normalization
    max_brain = max(brain_counts.values()) if brain_counts and max(brain_counts.values()) > 0 else 1
    for i in range(brain_share):
        if i < len(BRAIN_COMPONENTS):
            label = BRAIN_COMPONENTS[i]
            cnt = brain_counts.get(label, 0)
            if cnt >= max_brain * 0.5 and cnt > 0:
                state = "firing"
                intensity = 0.50 + min(0.40, cnt / max(max_brain, 1) * 0.40)
            elif cnt > 0:
                state = "lit"
                intensity = 0.30 + min(0.20, cnt / max(max_brain, 1) * 0.20)
            else:
                state = "dormant"
                intensity = 0.12
        else:
            label = f"brain_{i:03d}"
            cnt = 0
            state = "dormant"
            intensity = 0.08
        _base_mul = min(1.5, 0.5 + cnt / 80.0)
        _state_f = {"firing": 1.0, "lit": 0.7}.get(state, 0.4)
        size_mul = round(_base_mul * _state_f, 4)
        # Jin 2026-04-28 v16: BRAIN data = AI 함수 결과, ticker-agnostic → 위성.
        # entry_judge → REG↔STRAT (entry decision gap)
        # exit_advise → POS↔EXIT (exit decision gap)
        # 그 외 (signal_augment / portfolio_intel / drift / ai_hold/traded/blocked)
        #   → STRAT↔WATCH (generic data)
        if label.startswith("entry_judge"):
            _bk = "brain_entry"
        elif label.startswith("exit_advise"):
            _bk = "brain_exit"
        else:
            _bk = "brain_data"
        _brain_transition = ORBIT_TRANSITION.get(_bk)
        _brain_node_id = f"brain_{label}"
        _brain_node = {
            "id": _brain_node_id, "label": label,
            "ticker": None, "intensity": intensity,
            "count_30m": cnt,
            "size_mul": size_mul,
            "orbit_kind": _bk,
            "shape": "square",
            "cluster": "orbit", "tier": 11, "state": state,
            "spin_axis": _spin_axis_from_id(_brain_node_id),
            "spin_speed": _spin_speed_for_state(state, _brain_node_id),
            "orbit_axis": _perturb_axis(_orbit_vector_for(_bk)[0], _brain_node_id),
            "orbit_speed": _jitter_speed(_orbit_vector_for(_bk)[1], _brain_node_id),
            "initial_orbit_angle": _initial_orbit_angle(_brain_node_id),
        }
        if _brain_transition is not None:
            _brain_node["inter_a"] = _brain_transition[0]
            _brain_node["inter_b"] = _brain_transition[1]
            _brain_node["inter_radius"] = _brain_transition[2]
        add_node(_brain_node)

    # ---- Tier 7 WATCH — signal watchlist (currently being scored)
    # Jin 2026-04-28: ticker → asset_group lookup map (galaxy_universe). WATCH
    # 노드도 group 정보 부여 → renderer 가 group placement 적용.
    _ticker_to_group = {
        u["ticker"]: (u.get("asset_group") or "unknown").lower()
        for u in st.get("galaxy_universe", [])
        if u.get("ticker")
    }
    watch_share = TIER_SHARE[7]
    watch_pool = st.get("watch_tickers", [])
    for i in range(watch_share):
        if i < len(watch_pool):
            w = watch_pool[i]
            label = w["ticker"]
            ticker = w["ticker"]
            score_abs = w["max_score"] or 0
            n_scored = w["n_scored"] or 0
            exchange = w.get("exchange", "unknown")
            # Jin "score 강할수록 명백히 큰 size" — cap 1.20 → 2.50 확장
            # score 0=0.20 / 10=0.40 / 30=0.80 / 50=1.20 / 100=2.20 / 150+=2.50 cap
            intensity = min(2.50, 0.20 + score_abs * 0.020)
            if score_abs >= 30:
                state = "firing"     # very strong signal → bright glow
            elif score_abs >= 10:
                state = "lit"        # mid signal
            else:
                state = "dormant"    # weak / noise
        else:
            label = f"watch_{i:03d}"
            ticker = None
            exchange = None
            state = "dormant"
            intensity = 0.05
            score_abs = 0
        # Jin "강도 따라 안쪽/바깥쪽" — score 강 = inner (ring radius offset)
        # Jin 2026-04-28: divisor 완화 (0.025 → 0.012) → 80 score 도달 전 saturate 안 됨,
        # 진짜 strong (200+) 만 cap 도달. state factor 추가 for dormant 축소.
        _base_mul = min(2.5, 0.5 + score_abs * 0.012)
        _state_f = {"firing": 1.0, "lit": 0.80}.get(state, 0.50)  # dormant 0.50
        size_mul = round(_base_mul * _state_f, 4)
        # radial offset: score 강할수록 안쪽 (-0.05 inner) / 약 바깥 (+0.05 outer)
        radial_offset = round(max(-0.10, min(0.10, 0.10 - score_abs * 0.005)), 4)
        _watch_group = _ticker_to_group.get(ticker, "unknown") if ticker else "unknown"
        add_node({
            "id": f"watch_{label}", "label": label,
            "ticker": ticker, "exchange": exchange,
            "asset_group": _watch_group,                   # Jin 2026-04-28 group placement
            "n_scored": w.get("n_scored", 0) if i < len(watch_pool) else 0,
            "max_score": w.get("max_score", 0) if i < len(watch_pool) else 0,
            "intensity": intensity,
            "size_mul": size_mul,
            "radial_offset": radial_offset,   # Jin: 강도 따라 안쪽/바깥
            "cluster": "watch", "tier": 7, "state": state,
        })

    # ---- Tier 8 MKT — Full tradable universe (dense outer star shell) ----
    # Shuffle universe so exchanges mix evenly across sphere
    import random as _rnd
    universe_pool = list(st["galaxy_universe"])
    _rnd.Random(42).shuffle(universe_pool)
    # Cap mkt_share to actual universe size — no synthetic empty slots (avoids voids)
    mkt_share = min(TIER_SHARE[8], len(universe_pool))
    # Jin 2026-04-27 dynamic size: ticker 별 30m signal count
    # firing = 5+ signals → 1.4 cap, lit = 1-2 signals → ~1.0, dormant = 0.7
    # signal_count_30m proxy: firing_tickers (5+) / lit_tickers (1-4) / 그 외 0
    for i in range(mkt_share):
        if i < len(universe_pool):
            u = universe_pool[i]
            ticker = u["ticker"]
            label = ticker
            tech = st.get("ticker_tech", {}).get(ticker, {})
            atr = tech.get("atr", 0) or 0           # daily ATR%
            wr = tech.get("wr", 0) or 0             # historical win rate 0..1
            tc = tech.get("tc", 0) or 0             # trade count history
            blocked = tech.get("blocked", 0)
            signal_count_30m = 0
            if ticker in st["firing_tickers"]:
                state = "firing"
                intensity = 0.85   # firing MKT — strong exchange-color glow
                signal_count_30m = 8  # proxy ≥5+ signals (firing threshold)
            elif ticker in st["lit_tickers"]:
                # Jin 2026-04-27 log-full-mapping: lit ticker = recent gate REJECT or
                # DB signal touch (not entered). visible activity glow.
                state = "lit"
                intensity = 0.45
                signal_count_30m = 3  # proxy 1-4 signals
            else:
                state = "dormant"
                # Tighter dormant intensity range — uniform cloud feel (Jin: 골고루)
                # base ~0.10, slight tech variation 0.10-0.22
                atr_factor = min(0.06, atr / 18.0)
                wr_factor = max(0, wr - 0.5) * 0.10
                act_factor = min(0.04, tc / 200.0)
                base = 0.10 + atr_factor + wr_factor + act_factor
                if blocked:
                    base *= 0.6
                intensity = min(0.24, base)
            exchange = u.get("exchange", "unknown")
            asset_group = (u.get("asset_group") or "unknown").lower()
        else:
            label = f"mkt_{i:03d}"
            ticker = None
            state = "dormant"
            intensity = 0.04
            exchange = None
            asset_group = "unknown"
            signal_count_30m = 0
        # MKT size_mul: dormant 작은 dot (0.7), lit 1.0, firing 1.4 cap
        if state == "dormant":
            size_mul = 0.7
        else:
            size_mul = round(min(1.4, 0.7 + signal_count_30m / 20.0), 4)
        add_node({
            "id": f"mkt_{label}", "label": label,
            "ticker": ticker, "exchange": exchange,
            "asset_group": asset_group,                    # Jin 2026-04-28 group placement
            "intensity": intensity,
            "size_mul": size_mul,
            "signal_count_30m": signal_count_30m,
            "cluster": "mkt", "tier": 8, "state": state,
        })

    # ---- External Satellites (Jin v4: OBS / ACTION) ----
    # OBS: system health probes (LIVENESS_SHADOW + heartbeat + recent OKX recover)
    # ACTION: harness alert queue (recent .claude/harness_alerts/*.md)
    # Phase 2.5 (Jin 2026-04-27): tier shift — 8→9 (OBS), 9→10 (ACTION) (GROUP main sphere 신설)
    # cluster: 'obs' / 'action'
    obs_checks, action_queue = _build_external_satellites(conn)

    # Jin 2026-04-27 조정: 과하게 큰 사이즈 축소 (OBS warn 1.6→1.3, ACTION CRIT 2.0→1.4)
    # Jin 2026-04-28: 11 OBS 모두 1.0 uniform 문제 — active state 따라 차별.
    # warn(failed probe)=1.4 firing / ok=1.0 lit / stale=0.6 dormant.
    obs_axis, obs_speed = _orbit_vector_for("obs")
    for i, ck in enumerate(obs_checks[:12]):
        if not ck["ok"]:
            intensity = 0.90; size_mul = 1.4   # firing — needs attention
        else:
            intensity = 0.50; size_mul = 1.0   # lit — healthy active
        node_id = f"obs_{i}_{ck['id']}"
        add_node({
            "id": node_id, "label": ck["label"],
            "ticker": None, "exchange": None,
            "intensity": intensity, "size_mul": size_mul,
            "value": ck.get("value"), "unit": ck.get("unit"),
            "ok": ck["ok"], "warn": not ck["ok"],
            "shape": "square",
            "cluster": "obs", "tier": 9, "state": "lit" if ck["ok"] else "firing",
            # Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
            # per-node axis perturb + speed jitter + initial phase 분산
            "orbit_axis": _perturb_axis(obs_axis, node_id),
            "orbit_speed": _jitter_speed(obs_speed, node_id),
            "initial_orbit_angle": _initial_orbit_angle(node_id),
        })

    action_axis, action_speed = _orbit_vector_for("action")
    for i, act in enumerate(action_queue[:10]):
        sev = act.get("sev", "INFO")
        state = "firing" if sev in ("CRIT", "HIGH") else "lit"
        intensity = 0.90 if sev == "CRIT" else 0.70 if sev == "HIGH" else 0.50
        # Jin 2026-04-28: 10 ACTION 모두 1.0 (sev=INFO/MED 만 있음) → 4-tier 차별.
        # CRIT 1.5 / HIGH 1.25 / MED 1.0 / INFO 0.75 (older alerts 작게).
        if sev == "CRIT":   size_mul = 1.5
        elif sev == "HIGH": size_mul = 1.25
        elif sev == "MED":  size_mul = 1.0
        else:               size_mul = 0.75
        node_id = f"action_{i}_{act['id']}"
        add_node({
            "id": node_id, "label": act["label"],
            "ticker": None, "exchange": None,
            "intensity": intensity, "size_mul": size_mul, "sev": sev,
            "since_min": act.get("since_min", 0),
            "shape": "square",
            "cluster": "action", "tier": 10, "state": state,
            # Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
            # per-node axis perturb + speed jitter + initial phase 분산
            "orbit_axis": _perturb_axis(action_axis, node_id),
            "orbit_speed": _jitter_speed(action_speed, node_id),
            "initial_orbit_angle": _initial_orbit_angle(node_id),
        })

    # ---- Tier 11 ORBIT — 함수 위성 (Jin: 함수 = 위성, signal 쏘는 자)
    # Sensors / Providers / Hourly Learners / Brain Tools / Exit Engines / Exec Tools / Regime Infra
    # 본 sphere shell 의 함수성 components 를 외부 ring 위성으로 분리
    # Phase 2.5 (Jin 2026-04-27): tier shift 10→11 (GROUP main sphere 신설로)
    # 각 위성은 ORBIT_TRANSITION 매핑으로 inter_a / inter_b / inter_radius 보유
    # tier=11 필드는 호환성 유지, 실제 placement 는 inter_radius 사용 (sphere-render.js 분기)
    #
    # Jin 2026-04-27 (orbit-event-capture): state 를 하드코드(provider/sensor=firing)에서
    # 실제 function firing event 기반으로 교체. signals.providers + ai_calls + invasion.log
    # tail 30m parse → count >= threshold = firing / > 0 = lit / 0 = dormant.
    # Helper: _query_provider_calls / _query_ai_calls_by_model / _query_ai_calls_by_stage
    #         / _probe_invasion_log_events / _resolve_orbit_state.
    provider_calls = _query_provider_calls(conn)        # {provider_name: count}
    ai_calls_model = _query_ai_calls_by_model(conn)     # {model: count}
    ai_calls_stage = _query_ai_calls_by_stage(conn)     # {stage: count}
    log_events = _probe_invasion_log_events()           # {pattern: count} (1 read)
    for i, label in enumerate(ORBIT_COMPONENTS):
        kind = _orbit_kind(label)
        state, intensity, call_count = _resolve_orbit_state(
            kind, label, provider_calls, ai_calls_model, ai_calls_stage, log_events,
        )
        # Jin 2026-04-28 v14: "거래 트레이딩에 필요한 애들만" — registered legacy
        # function 들 중 영구 dormant + count=0 (봇이 호출 안 하는 dead provider)
        # skip. visualizer 가 실제 활동 노드만 표시.
        if state == "dormant" and call_count == 0:
            continue
        transition = ORBIT_TRANSITION.get(kind)  # (inter_a, inter_b, inter_radius) or None
        node_id = f"orbit_{i}_{label}"
        # Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 vector + speed
        cat_axis, cat_speed = _orbit_vector_for(kind)
        # Jin 2026-04-28 size redesign: state × call_count. 80 dormant uniform 문제 해소.
        # divisor 100 → 200 (slower saturate, prov 1000+ calls 만 cap 도달).
        # state factor: firing 1.0 / lit 0.8 / dormant 0.55.
        # Jin 2026-04-28 v15: AI judge ("쟤들 너무 작은데") boost — base min
        # 0.9, cap 1.7. brain_tool / exit_engine / regime 도 약간 boost (0.7).
        if kind == "ai_judge":
            _base_mul = min(1.7, 0.9 + call_count / 200.0)
        elif kind in ("brain_tool", "exit_engine", "regime_infra"):
            _base_mul = min(1.5, 0.7 + call_count / 200.0)
        else:
            _base_mul = min(1.4, 0.6 + call_count / 200.0)
        _state_f = {"firing": 1.0, "lit": 0.80}.get(state, 0.55)
        orbit_size_mul = round(_base_mul * _state_f, 4)
        node = {
            "id": node_id, "label": label,
            "ticker": None, "exchange": None,
            "intensity": intensity, "size_mul": orbit_size_mul,
            "call_count": call_count,
            "orbit_kind": kind,                  # function category
            "shape": "square",                   # Phase 2: square (own-axis rotation visual)
            "cluster": "orbit", "tier": 11, "state": state,
            # Phase 2 own-axis rotation (Jin 2026-04-27)
            "spin_axis": _spin_axis_from_id(node_id),
            "spin_speed": _spin_speed_for_state(state, node_id),
            # Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
            # per-node axis perturb + speed jitter + initial phase 분산
            "orbit_axis": _perturb_axis(cat_axis, node_id),
            "orbit_speed": _jitter_speed(cat_speed, node_id),
            "initial_orbit_angle": _initial_orbit_angle(node_id),
        }
        if transition is not None:
            node["inter_a"] = transition[0]
            node["inter_b"] = transition[1]
            node["inter_radius"] = transition[2]
        add_node(node)

    # ---- Tier 12 AXIS — 8-dim cell_matrix 차원 위성 (session × liq × crisis)
    # Phase 2.5 (Jin 2026-04-27): tier shift 11→12 + group axis 제거 (main sphere 격상)
    # Active session highlighted (firing), 나머지 lit/dormant
    import time as _t_axis
    aest_hour = _t_axis.localtime().tm_hour
    if   9 <= aest_hour < 13: active_session_label = "session_asia_open"
    elif 13 <= aest_hour < 17: active_session_label = "session_asia_late"
    elif 17 <= aest_hour < 20: active_session_label = "session_eu_open"
    elif 20 <= aest_hour < 23: active_session_label = "session_eu_late"
    elif aest_hour >= 23 or aest_hour < 4: active_session_label = "session_us_core"
    else: active_session_label = "session_us_late"
    for i, label in enumerate(AXIS_COMPONENTS):
        kind = _axis_kind(label)
        if kind == "session":
            if label == active_session_label:
                state = "firing"; intensity = 0.85
            else:
                state = "dormant"; intensity = 0.10
        elif kind == "liq":
            state = "lit"; intensity = 0.35
        elif kind == "crisis":
            state = "dormant"; intensity = 0.10
        else:
            state = "lit"; intensity = 0.35
        node_id = f"axis_{i}_{label}"
        # Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 vector + speed
        cat_axis, cat_speed = _orbit_vector_for(kind)
        # Jin 2026-04-28: 12 AXIS 모두 1.1 hardcoded → active session 도 동일 사이즈.
        # state 차별: firing (active session) 1.5 / lit (liq buckets etc) 1.0 / dormant 0.55.
        _axis_size_mul = {"firing": 1.5, "lit": 1.0}.get(state, 0.55)
        add_node({
            "id": node_id, "label": label,
            "ticker": None, "exchange": None,
            "intensity": intensity, "size_mul": _axis_size_mul,
            "axis_kind": kind,                   # NEW: dimension category
            "shape": "square",                   # Phase 2: square (own-axis rotation visual)
            "cluster": "axis", "tier": 12, "state": state,
            # Phase 2 own-axis rotation (Jin 2026-04-27)
            "spin_axis": _spin_axis_from_id(node_id),
            "spin_speed": _spin_speed_for_state(state, node_id),
            # Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
            # per-node axis perturb + speed jitter + initial phase 분산
            "orbit_axis": _perturb_axis(cat_axis, node_id),
            "orbit_speed": _jitter_speed(cat_speed, node_id),
            "initial_orbit_angle": _initial_orbit_angle(node_id),
        })

    # ---- Tier 13 EXIT_TALLY — outer satellite, exit_type counts (Jin 2026-04-27 mixup) ----
    # 8 nodes (one per exit_type), dynamic from trades (last 30m). 외부 ring.
    EXIT_TALLY_TYPES = ["TIME", "TRAIL", "TP", "STOP", "BEP",
                        "SIGNAL", "orphan_cleanup", "broker_removed"]
    exit_tally_counts = {k: 0 for k in EXIT_TALLY_TYPES}
    try:
        rows = conn.execute("""
            SELECT exit_type, COUNT(*) c FROM trades
            WHERE status='closed' AND exit_ts >= strftime('%s','now') - 1800
              AND exit_type IS NOT NULL
            GROUP BY exit_type
        """).fetchall()
        for r in rows:
            et = r["exit_type"]
            if et in exit_tally_counts:
                exit_tally_counts[et] = r["c"] or 0
    except sqlite3.OperationalError:
        pass
    max_tally = max(exit_tally_counts.values()) if exit_tally_counts and max(exit_tally_counts.values()) > 0 else 1
    for i, et in enumerate(EXIT_TALLY_TYPES):
        cnt = exit_tally_counts.get(et, 0)
        if cnt >= max_tally * 0.5 and cnt > 0:
            state = "firing"
            intensity = 0.55 + min(0.40, cnt / max(max_tally, 1) * 0.40)
            size_mul = 1.4
        elif cnt > 0:
            state = "lit"
            intensity = 0.30 + min(0.20, cnt / max(max_tally, 1) * 0.20)
            size_mul = 1.1
        else:
            state = "dormant"
            intensity = 0.10
            size_mul = 0.9
        node_id = f"exit_tally_{i}_{et}"
        et_axis, et_speed = _orbit_vector_for("exit_tally")
        add_node({
            "id": node_id, "label": f"tally_{et}",
            "ticker": None, "exchange": None,
            "intensity": intensity, "size_mul": size_mul,
            "exit_type": et,
            "count_30m": cnt,
            "shape": "square",
            "cluster": "exit_tally", "tier": 13, "state": state,
            # Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
            # per-node axis perturb + speed jitter + initial phase 분산
            "orbit_axis": _perturb_axis(et_axis, node_id),
            "orbit_speed": _jitter_speed(et_speed, node_id),
            "initial_orbit_angle": _initial_orbit_angle(node_id),
        })

    # ---- Stats ----
    tick_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE entry_ts >= strftime('%s','now') - 86400"
    ).fetchone()[0] or 0
    rate_row = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE entry_ts >= strftime('%s','now') - 60"
    ).fetchone()
    firing_rate = round((rate_row[0] or 0) / 60.0, 1)

    regime = "RISK_OFF"
    try:
        r = conn.execute(
            "SELECT regime FROM regime_history ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if r and r[0]:
            regime = r[0].upper()
    except sqlite3.OperationalError:
        pass

    firing_count = sum(1 for n in nodes if n["state"] == "firing")
    lit_count = sum(1 for n in nodes if n["state"] == "lit")

    # Build full pipeline trade chains:
    # MKT → WATCH → BRAIN → STRAT → REG → EXEC → POS (full cascade)
    def find_pos_idx(ticker):
        return label_to_idx.get(("pos", ticker), -1)

    def find_reg_idx(regime):
        return label_to_idx.get(("reg", f"regime_{regime}"), -1)

    # Jin v16: EXEC gate 가 cluster='orbit' 으로 이동 — 이전 'exec' lookup 0 매칭.
    exec_gates = [k for k in label_to_idx
                  if (k[0] == "exec" or k[0] == "orbit") and k[1].startswith("gate_")]

    def hash_str(s):
        h = 0
        for ch in s or "":
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return h

    def find_exec_idx(seed):
        if not exec_gates:
            return -1
        return label_to_idx[exec_gates[hash_str(seed) % len(exec_gates)]]

    def find_strat_idx(strategy_id):
        idx = label_to_idx.get(("strat", strategy_id), -1)
        if idx >= 0:
            return idx
        strat_keys = [k for k in label_to_idx if k[0] == "strat"]
        if not strat_keys:
            return -1
        return label_to_idx[strat_keys[hash_str(strategy_id) % len(strat_keys)]]

    def find_mkt_idx(ticker):
        return label_to_idx.get(("mkt", ticker), -1)

    def find_watch_idx(ticker):
        return label_to_idx.get(("watch", ticker), -1)

    # Brain anchor — A안 cleanup: BRAIN sphere = AI decision results.
    # Jin v16: BRAIN 노드 cluster='orbit' 이동 — orbit_kind brain_entry/brain_exit/brain_data.
    brain_anchors = [k for k in label_to_idx
                     if k[0] == "brain"
                     or (k[0] == "orbit" and k[1].startswith(("entry_judge", "exit_advise",
                                                              "signal_augment", "portfolio_intel",
                                                              "drift_alert", "ai_hold",
                                                              "ai_traded", "ai_blocked")))]

    def find_brain_idx(seed):
        if not brain_anchors:
            return -1
        return label_to_idx[brain_anchors[hash_str(seed) % len(brain_anchors)]]

    # Per-strategy WR for link confidence
    strat_wr_map = {s["id"]: s.get("win_rate_24h", 0) for s in st["top_strategies"]}

    chains = []
    for p in st["open_positions"]:
        pos_idx = find_pos_idx(p["ticker"])
        if pos_idx < 0:
            continue
        # Jin v19: chain 은 표면 데이터만 (mkt→watch→strat→reg→pos). 위성
        # (brain/exec orbit) 은 자기 effect 별도 (provider beam / satellite
        # signal). chain link 에서 위성 제외.
        mkt_idx = find_mkt_idx(p["ticker"])
        watch_idx = find_watch_idx(p["ticker"])
        strat_idx = find_strat_idx(p["strategy_id"])
        reg_idx = find_reg_idx(p["regime"])
        chain_nodes = [n for n in [mkt_idx, watch_idx, strat_idx, reg_idx, pos_idx] if n >= 0]
        if len(chain_nodes) < 2:
            continue
        # Link strength: combines pnl confidence + strategy WR + position size
        wr = strat_wr_map.get(p["strategy_id"], 0)
        pnl_conf = min(1.0, abs(p["pnl_usd"] or 0) / 30.0)
        wr_conf = max(0, wr - 0.4) * 1.7
        strength = min(1.0, 0.20 + 0.50 * wr_conf + 0.30 * pnl_conf)
        chains.append({
            "trade_id": p["trade_id"],
            "ticker": p["ticker"],
            "pnl_usd": p["pnl_usd"],
            "pnl_pct": p["pnl_pct"],
            "direction": p["direction"],
            "win_rate": round(wr, 3),
            "strength": round(strength, 3),
            "chain": chain_nodes,
        })

    # ---- Lifecycle paths (Jin 2026-04-27) ----
    # Trade lifecycle radial path: mkt -> provider -> watch -> strat -> brain -> exec -> pos -> exit
    # Click 시 BFS mesh 대신 lifecycle path 만 highlight (Jin "라이프사이클 연결")
    # Source 1: open positions (terminal = pos)
    # Source 2: recent_closes (terminal = exit_tally)
    provider_orbit_keys = [k for k in label_to_idx if k[0] == "orbit"
                           and (k[1].startswith("data_") or k[1].startswith("sig_"))]

    def find_provider_id(ticker, asset_group):
        if not provider_orbit_keys:
            return None
        seed = f"{asset_group or 'unknown'}::{ticker or 'x'}"
        key = provider_orbit_keys[hash_str(seed) % len(provider_orbit_keys)]
        i = label_to_idx[key]
        return nodes[i]["id"]

    def find_exit_tally_id(exit_type):
        if not exit_type:
            return None
        i = label_to_idx.get(("exit_tally", f"tally_{exit_type}"), -1)
        return nodes[i]["id"] if i >= 0 else None

    def idx_id(i):
        return nodes[i]["id"] if 0 <= i < len(nodes) else None

    lifecycle_paths = []

    # 1) Open positions — full radial path ending at POS
    # Jin v19: 위성 (brain/exec/provider orbit) 제외 — chain 은 표면 데이터만.
    for p in st["open_positions"]:
        pos_idx = find_pos_idx(p["ticker"])
        if pos_idx < 0:
            continue
        mkt_idx = find_mkt_idx(p["ticker"])
        watch_idx = find_watch_idx(p["ticker"])
        strat_idx = find_strat_idx(p["strategy_id"])
        ordered_ids = [
            idx_id(mkt_idx),
            idx_id(watch_idx),
            idx_id(strat_idx),
            idx_id(pos_idx),
        ]
        node_ids = [nid for nid in ordered_ids if nid]
        if len(node_ids) < 2:
            continue
        lifecycle_paths.append({
            "trigger_ticker": p["ticker"],
            "trigger_strategy": p["strategy_id"],
            "exchange": p["exchange"],
            "asset_group": p["asset_group"],
            "kind": "open",
            "node_ids": node_ids,
        })

    # 2) Recent closes (last 10) — terminal at exit_tally
    # Jin v19: 위성 (brain/exec/provider orbit) 제외 — 표면 데이터 + EXIT only.
    for c in st["recent_closes"][:10]:
        ticker = c.get("ticker")
        if not ticker:
            continue
        exchange = c.get("exchange", "okx")
        exit_type = c.get("exit_type")
        mkt_idx = find_mkt_idx(ticker)
        watch_idx = find_watch_idx(ticker)
        strat_idx = find_strat_idx(ticker)
        pos_idx = find_pos_idx(ticker)
        exit_id = find_exit_tally_id(exit_type)
        ordered_ids = [
            idx_id(mkt_idx),
            idx_id(watch_idx),
            idx_id(strat_idx),
            idx_id(pos_idx),       # may be None (closed only)
            exit_id,
        ]
        node_ids = [nid for nid in ordered_ids if nid]
        if len(node_ids) < 2:
            continue
        lifecycle_paths.append({
            "trigger_ticker": ticker,
            "trigger_strategy": None,
            "exchange": exchange,
            "asset_group": None,
            "kind": "closed",
            "exit_type": exit_type,
            "node_ids": node_ids,
        })

    conn.close()

    # Exchange-grouped PnL aggregates (for sub-sphere centers)
    exchange_pnl = {}
    for p in st["open_positions"]:
        ex = p["exchange"] or "okx"
        if ex not in exchange_pnl:
            exchange_pnl[ex] = {"exchange": ex, "count": 0, "pnl_usd": 0.0, "size_usd": 0.0}
        exchange_pnl[ex]["count"] += 1
        exchange_pnl[ex]["pnl_usd"] += p["pnl_usd"] or 0
        exchange_pnl[ex]["size_usd"] += p["size_usd"] or 0
    for v in exchange_pnl.values():
        v["pnl_usd"] = round(v["pnl_usd"], 2)
        v["size_usd"] = round(v["size_usd"], 2)

    # Jin 2026-04-30 Phase 4 T21: extend with SPOT bot nodes (tier 12, cluster=spot_data)
    nodes.extend(fetch_spot_pipeline_state())

    return {
        "nodes": nodes,
        "clusters": CLUSTERS,
        "live_trades": st["open_positions"],
        "recent_closes": st["recent_closes"],
        "galaxy_universe": st["galaxy_universe"],
        "trade_chains": chains,
        "lifecycle_paths": lifecycle_paths,
        "exchange_pnl": list(exchange_pnl.values()),
        "stats": {
            "regime": regime,
            "tick": tick_count,
            "node_count": len(nodes),
            "firing_rate": firing_rate,
            "firing": firing_count,
            "lit": lit_count,
            "cluster_count": len(CLUSTERS),
            "tier_count": 8,    # 8 main spheres (group cluster 제거 mixup cleanup)
            "open_count": len(st["open_positions"]),
            "ts": int(time.time()),
        },
    }


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    g = build_galaxy()
    OUT.write_text(json.dumps(g), encoding="utf-8")
    s = g["stats"]
    print(f"✓ Neural Cloud (pipeline tiers): {s['node_count']} nodes / {s['firing']} firing / {s['lit']} lit")
    print(f"  REGIME {s['regime']} · TICK {s['tick']:,} · OPEN {s['open_count']} · FIRING/s {s['firing_rate']}")


if __name__ == "__main__":
    main()
