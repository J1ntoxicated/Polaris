"""Live events extractor — log tail + sqlite poll → JSON stream.

Pulls recent events from invasion.log (last N lines) + recent closes from sqlite.
Browser polls /events every 1-2s for animation feed.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
# Jin 2026-05-02 ADR-007: live event feed reads SPOT bot's log + DB so the
# visualizer surfaces SPOT activity (entries, exits, signals).  The galaxy
# snapshot (snapshot.py) still reads the main bot DB for the structural
# diagram — separate concern.
LOG = ROOT / "logs" / "spot_bot.log"
DB = ROOT / "data" / "invasion_spot.sqlite"

# Patterns to extract from log
# Jin 2026-04-27 (log-full-mapping): invasion.log 전체 function → dashboard 매핑.
# 기존 9 + 신규 11 (regime_flip / cell_learn / composer / evolver_mutation / gate_pass /
#   gate_block / broker_tick / broker_liveness / ws_reconnect / harness_alert / heartbeat).
# 매 패턴 group(1) = ticker (없으면 cluster/component label or 'system').
PATTERNS = {
    "scan": re.compile(r"\[SIGNAL_PROF\] composer.py:score:\d+ (\w[\w/]+) total="),
    "signal_pass": re.compile(r"\[\s*SIGNAL\] engine.py:evaluate:\d+ PASS (\w[\w/]+) (long|short) score=([+-][\d.]+)"),
    "signal_reject": re.compile(r"\[\s*SIGNAL\] engine.py:evaluate:\d+ REJECT (\w[\w/]+) (long|short) score=([+-][\d.]+)"),
    "entry": re.compile(r"\[PIPELINE\] .*ENTRY (\w[\w/]+) (long|short) \$(\d+)"),
    "exit_trigger": re.compile(r"trade\.exit_triggered ticker=(\w[\w/]+) reason=(\S+)"),
    # trade.closed full event (pnl_usd / pnl_pct / exit_type / direction 추출 → frontend supernova)
    "exit": re.compile(r"trade\.closed ticker=(\w[\w/]+) direction=(\w+) exchange=(\w+).*?pnl_pct=([+-]?[\d.]+) pnl_usd=([+-]?[\d.]+).*?exit_type=([^\s]+)"),
    "size_cap": re.compile(r"SIZE_CAP(?:_FSM)? (\w[\w/]+): size=(\d+)"),
    "cell_skip": re.compile(r"SKIP_DEMOTED.*ticker=(\w[\w/]+)"),
    "ai_critical": re.compile(r"CRITICAL (\w[\w/]+).*PnL critical ([+-][\d.]+)%"),
    # AI decision call — orchestrator records each AI invocation
    "ai_decision": re.compile(r"\[\s*AI\] orchestrator\.py:record_call:\d+ (\w+) (\S+) \$([\d.]+) (\d+)ms"),
    # === Jin 2026-04-27 log-full-mapping (신규 11) ===
    # GATE REJECT — entry gate 실패 (group(1) = ticker, group(2) = reject reason)
    "gate_reject": re.compile(r"\[\s*GATE\] entry\.py:_reject:\d+ REJECT (\S+): (\w+)"),
    # GATE LIQUIDITY clamp — sizing reduced (group(1) = ticker, group(2) = clamped size)
    "gate_clamp": re.compile(r"\[\s*GATE\] entry\.py:check:\d+ LIQUIDITY_CLAMP (\S+) size->\$(\d+)"),
    # REGIME flip / detector update (group(1) = detector name e.g. CryptoDetector, group(2) = state)
    "regime_flip": re.compile(r"\[\s*REGIME\] regime\.py:update:\d+ (\w+Detector): (\w+) conf="),
    # CELL_LEARN — cell ema update (group(1) = cell tuple sig, group(2) = ema_new)
    "cell_learn": re.compile(r"\[CELL_LEARN\].*cell=\((.*?)\) ema_new=([+-]?[\d.]+)"),
    # EVOLVE — strategy mutation/init (group(1) = trigger reason or 'init')
    "evolver": re.compile(r"\[\s*EVOLVE\] (?:evolution|evolver)\.py:\w+:\d+ (.+)"),
    # BROKER_SYNC tick / db insert (group(1) = exchange, group(2) = ticker)
    "broker_tick": re.compile(r"\[BROKER_SYNC\].*DB_INSERT_ADOPTED (\w+) (\S+) strat="),
    # BROKER_SYNC liveness 1h heartbeat (group(1) = optional context)
    "broker_liveness": re.compile(r"\[BROKER_SYNC\].*(?:liveness_1h|tick_done|liveness)"),
    # CAP_WS / OKX_WS reconnect / resubscribe (system-wide WS recovery)
    "ws_reconnect": re.compile(r"\[(?:CAP_WS|\s*OKX|\s*ALP)\].*(?:reconnect|resubscrib|recovered)"),
    # HARNESS_ALERT emit (group(1) = alert category, group(2) = severity)
    "harness_alert": re.compile(r"\[HARNESS_ALERT\].*?(\w+)/(LOW|MED|HIGH|CRIT)"),
    # HEART beat tick (group(1) = pos count) — system pulse
    "heartbeat": re.compile(r"\[\s*HEART\] heartbeat\.py:tick:\d+ (\d+)pos"),
    # LIVENESS_SHADOW PASS/FAIL (group(1) = ticker, group(2) = PASS|FAIL)
    "liveness_shadow": re.compile(r"\[LIVENESS_SHADOW\].*?(\S+) (PASS|FAIL)"),
    # === Jin 2026-05-02 ADR-007 (SPOT bot log format) ===
    # SPOT bot signal fire (PASS) — per-ticker strategy decided to enter
    "spot_signal": re.compile(
        r"SIGNAL (\w[\w/]+) tier=(\S+) strat=(\S+) score=([+-]?[\d.]+)"),
    # SPOT bot scan (no fire) — per-ticker evaluation finished, no entry
    "spot_scan": re.compile(
        r"SCAN (\w[\w/]+) tier=(\S+) n_strat=\d+ fired=0"),
    # SPOT bot ENTRY (filled trade) — supersedes [PIPELINE] entry
    "spot_entry": re.compile(
        r"ENTRY (\w[\w/]+) strat=(\S+) tier=(\S+) score=([+-]?[\d.]+)"),
    # SPOT bot EXIT — per-trade close with decision + pnl_pct
    "spot_exit": re.compile(
        r"EXIT (\w[\w/]+) strat=(\S+) decision=(\S+) "
        r"pnl=\$([+-]?[\d.]+) \(([+-]?[\d.]+)%\)"),
    # SPOT bot blacklist (51155 etc.)
    "spot_blacklist": re.compile(
        r"blacklisting (\S+) for session — sCode=(\d+)"),
    # === ADR-008 Polaris autonomy (block→weight + crisis + learner + evolver) ===
    # weight_resolver decision: WEIGHT BTC-USDT/bb_break_momentum size_mul=1.40 ★ AMP x1.40 (crisis_off)
    "weight_decision": re.compile(
        r"WEIGHT (\S+)/(\S+) size_mul=([+-]?[\d.]+)\s*(.*)$"),
    # crisis regime flip (replaces stub regime): regime neutral → crisis_off amp=x1.40
    "polaris_regime_flip": re.compile(
        r"regime (\w+) → (\w+) amp=x([\d.]+)"),
    # 6h learner cycle done: [learner_6h] cycle done — N cells tuned
    "polaris_learner_cycle": re.compile(
        r"\[learner_6h\] cycle done — (\d+) cells tuned"),
    # evolver round: [evolver] round done — spawned=N retired=M top=...
    "polaris_evolver_round": re.compile(
        r"\[evolver\] round done — spawned=(\d+) retired=(\d+) top=(.*)$"),
    # evolver sibling spawned: [evolver] spawned <id> parent=<p> kind=<k> attrs=...
    "polaris_evolver_spawn": re.compile(
        r"\[evolver\] spawned (\S+) parent=(\S+) kind=(\S+) attrs=(.*)$"),
}


def open_ro() -> sqlite3.Connection:
    # Plain connect (not file:?mode=ro) — WAL-mode DBs require -shm/-wal
    # companion files which read-only URI cannot create, causing
    # "unable to open database file" against the live SPOT DB.
    conn = sqlite3.connect(str(DB), timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


# Track last-seen log position
_last_log_size = 0
# Two buffers: SCAN spam (~30/sec, low signal) is capped low so it cannot
# evict meaningful events.  Important events keep their own buffer.
_scan_buffer = deque(maxlen=80)
_event_buffer = deque(maxlen=300)  # SIGNAL / ENTRY / EXIT / errors / regime / etc.
_last_close_ts = 0


def tail_log(max_bytes: int = 2_500_000) -> list[dict]:
    """Read last N bytes of log, extract events.

    Jin 2026-05-02: SPOT bot ~30 SCAN/sec spam — 600KB still missed last
    ENTRY (byte 753K, log 1.4M, 600KB tail starts at 810K).  2.5MB ≈
    1-1.5h covers SIGNAL/ENTRY (분당 1-2회) reliably.  PATTERNS still
    deduped via _event_buffer (maxlen=300).
    """
    global _last_log_size
    if not LOG.exists():
        return []

    sz = LOG.stat().st_size
    new_events = []

    # Open + seek to last position (or backwards from end)
    start = max(_last_log_size, sz - max_bytes) if _last_log_size > 0 else max(0, sz - max_bytes)

    try:
        with LOG.open("rb") as f:
            f.seek(start)
            chunk = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    _last_log_size = sz

    now = time.time()
    for line in chunk.split("\n"):
        if not line.strip():
            continue
        # Extract timestamp
        ts_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not ts_match:
            continue
        try:
            event_time = time.mktime(time.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            event_time = now

        # Skip very old (>5min ago) — Jin 2026-05-02: 30s 너무 짧아서
        # SIGNAL/ENTRY (분당 1-2회) 누락 빈번 → 5min window
        if now - event_time > 300:
            continue

        # Match patterns
        for ev_type, pat in PATTERNS.items():
            m = pat.search(line)
            if m:
                ticker = m.group(1) if (m.lastindex or 0) >= 1 else "unknown"
                ev = {"type": ev_type, "ticker": ticker, "ts": event_time}
                if ev_type == "signal_pass" or ev_type == "signal_reject":
                    ev["direction"] = m.group(2)
                    ev["score"] = float(m.group(3))
                elif ev_type == "entry":
                    ev["direction"] = m.group(2)
                    ev["size"] = int(m.group(3))
                elif ev_type == "exit_trigger":
                    ev["reason"] = m.group(2)
                elif ev_type == "exit":
                    ev["direction"] = m.group(2)
                    ev["exchange"] = m.group(3)
                    ev["pnl_pct"] = float(m.group(4))
                    ev["pnl_usd"] = float(m.group(5))
                    ev["exit_type"] = m.group(6)
                elif ev_type == "size_cap":
                    ev["size"] = int(m.group(2))
                elif ev_type == "ai_critical":
                    ev["pnl"] = float(m.group(2))
                elif ev_type == "ai_decision":
                    ev["stage"] = m.group(1)
                    ev["model"] = m.group(2)
                    ev["cost"] = float(m.group(3))
                    ev["latency"] = int(m.group(4))
                # === Jin 2026-04-27 log-full-mapping field 추출 ===
                elif ev_type == "gate_reject":
                    ev["reason"] = m.group(2)
                elif ev_type == "gate_clamp":
                    ev["clamped_size"] = int(m.group(2))
                elif ev_type == "regime_flip":
                    ev["detector"] = m.group(1)   # CryptoDetector / MacroDetector
                    ev["state"] = m.group(2)      # neutral / risk_on / etc
                    ev["ticker"] = "system"       # not ticker-specific
                elif ev_type == "cell_learn":
                    # cell tuple → ticker 추출 (7th element in tuple, 'TICKER')
                    cell_str = m.group(1)
                    ev["ema_new"] = float(m.group(2))
                    # Try parse ticker from cell tuple parts
                    parts = [p.strip().strip("'\"") for p in cell_str.split(",")]
                    ev["ticker"] = parts[6] if len(parts) >= 7 else "system"
                    ev["cell"] = cell_str[:80]
                elif ev_type == "evolver":
                    ev["trigger"] = m.group(1)[:80]
                    ev["ticker"] = "system"
                elif ev_type == "broker_tick":
                    ev["exchange"] = m.group(1)
                    ev["ticker"] = m.group(2)
                elif ev_type == "broker_liveness":
                    ev["ticker"] = "system"
                elif ev_type == "ws_reconnect":
                    ev["ticker"] = "system"
                elif ev_type == "harness_alert":
                    ev["category"] = m.group(1)
                    ev["severity"] = m.group(2)
                    ev["ticker"] = "system"
                elif ev_type == "heartbeat":
                    ev["pos_count"] = int(m.group(1))
                    ev["ticker"] = "system"
                elif ev_type == "liveness_shadow":
                    ev["status"] = m.group(2)   # PASS / FAIL
                # === Jin 2026-05-02 SPOT bot fields ===
                elif ev_type == "spot_signal":
                    ev["tier"] = m.group(2)
                    ev["strat"] = m.group(3)
                    ev["score"] = float(m.group(4))
                    # alias to canonical signal_pass shape so downstream
                    # frontend handlers don't need new branches
                    ev["type"] = "signal_pass"
                    ev["direction"] = "long"
                elif ev_type == "spot_scan":
                    ev["tier"] = m.group(2)
                    ev["type"] = "scan"
                elif ev_type == "spot_entry":
                    ev["strat"] = m.group(2)
                    ev["tier"] = m.group(3)
                    ev["score"] = float(m.group(4))
                    ev["direction"] = "long"
                    ev["type"] = "entry"
                elif ev_type == "spot_exit":
                    ev["strat"] = m.group(2)
                    ev["exit_type"] = m.group(3)
                    ev["pnl_usd"] = float(m.group(4))
                    ev["pnl_pct"] = float(m.group(5))
                    ev["direction"] = "long"
                    ev["exchange"] = "okx_spot"
                    ev["type"] = "exit"
                elif ev_type == "spot_blacklist":
                    ev["ticker"] = m.group(1)
                    ev["scode"] = m.group(2)
                # === ADR-008 Polaris autonomy ===
                elif ev_type == "weight_decision":
                    ev["ticker"] = m.group(1)
                    ev["strat"] = m.group(2)
                    ev["size_mul"] = float(m.group(3))
                    ev["notes"] = (m.group(4) or "")[:120]
                    ev["type"] = "weight_decision"
                    ev["direction"] = "long"
                elif ev_type == "polaris_regime_flip":
                    ev["from"] = m.group(1)
                    ev["to"] = m.group(2)
                    ev["amp"] = float(m.group(3))
                    ev["ticker"] = "system"
                    ev["type"] = "regime_flip"
                elif ev_type == "polaris_learner_cycle":
                    ev["touched"] = int(m.group(1))
                    ev["ticker"] = "system"
                    ev["type"] = "learner_cycle"
                elif ev_type == "polaris_evolver_round":
                    ev["spawned"] = int(m.group(1))
                    ev["retired"] = int(m.group(2))
                    ev["top"] = m.group(3)[:120]
                    ev["ticker"] = "system"
                    ev["type"] = "evolver_round"
                elif ev_type == "polaris_evolver_spawn":
                    ev["sibling_id"] = m.group(1)
                    ev["parent_id"] = m.group(2)
                    ev["kind"] = m.group(3)
                    ev["attrs"] = m.group(4)[:120]
                    ev["ticker"] = "system"
                    ev["type"] = "evolver_spawn"
                new_events.append(ev)
                # Maintain priority buffers — SCAN 분리, 나머지 important
                if ev.get("type") == "scan":
                    _scan_buffer.append(ev)
                else:
                    _event_buffer.append(ev)
                break

    return new_events


def recent_closes() -> list[dict]:
    """Last 30 closed trades from SPOT sqlite.

    Jin 2026-05-02 ADR-007: SPOT trades schema uses ``net_pnl_usd``, has no
    ``exchange``/``direction``/``hold_seconds`` columns — derive them.
    """
    global _last_close_ts
    conn = open_ro()
    rows = conn.execute("""
        SELECT id, ticker, exit_type, strategy_id,
               ROUND(net_pnl_usd, 2) pnl, exit_ts, entry_ts,
               ROUND((exit_ts - entry_ts) / 60.0, 1) hold_min
        FROM trades WHERE status='closed' AND exit_ts >= ?
        ORDER BY exit_ts DESC LIMIT 30
    """, (_last_close_ts,)).fetchall()
    conn.close()

    closes = []
    for r in rows:
        closes.append({
            "id": r["id"],
            "ticker": r["ticker"],
            "exchange": "okx_spot",
            "direction": "long",
            "exit_type": r["exit_type"],
            "strategy_id": r["strategy_id"],
            "pnl": r["pnl"],
            "ts": r["exit_ts"],
            "hold_min": r["hold_min"],
        })
    if rows:
        _last_close_ts = max(r["exit_ts"] for r in rows)
    return closes


def stats() -> dict:
    conn = open_ro()
    s = {
        "trades_1h": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed' AND exit_ts >= strftime('%s','now','-1 hour')"
        ).fetchone()[0],
        "trades_5m": conn.execute(
            "SELECT COUNT(*) FROM trades WHERE exit_ts >= strftime('%s','now','-5 minutes')"
        ).fetchone()[0],
        "open": conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0],
        "pnl_1h": conn.execute(
            "SELECT ROUND(COALESCE(SUM(net_pnl_usd),0),2) FROM trades WHERE status='closed' AND exit_ts >= strftime('%s','now','-1 hour')"
        ).fetchone()[0] or 0,
    }
    conn.close()
    return s


def feed() -> dict:
    """Combined event feed.

    Frontend renders signal flow + supernova; SIGNAL / ENTRY / EXIT 등
    중요 이벤트가 SCAN spam 에 묻히지 않도록 분리 후 union.
    """
    new = tail_log()
    important = [e for e in new if e.get("type") != "scan"]
    scans = [e for e in new if e.get("type") == "scan"]
    # Pull also from rolling buffers so a slow render still surfaces recent events
    important_recent = list(_event_buffer)[-150:]
    scan_recent = list(_scan_buffer)[-50:]
    # Dedup by (type, ticker, ts)
    seen = set()
    merged = []
    for ev in (important + important_recent + scans + scan_recent):
        k = (ev.get("type"), ev.get("ticker"), int(ev.get("ts") or 0))
        if k in seen:
            continue
        seen.add(k)
        merged.append(ev)
    merged.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return {
        "events": merged[:200],
        "closes": recent_closes(),
        "stats": stats(),
        "ts": int(time.time()),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(feed(), indent=2))
