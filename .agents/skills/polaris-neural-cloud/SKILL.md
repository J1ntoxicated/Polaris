---
name: polaris-neural-cloud
description: Live 3D dashboard for the Polaris/북극성 trading system — concentric shells of pipeline stages with orbiting AI satellites and 4 exchange cores at center. Use when the user wants a system-state visualization, live dashboard, or "neural cloud" view. Self-contained drop-in.
---

# Polaris Neural Cloud — v4 (concentric)

A single-screen 3D dashboard rendering the entire Polaris system as a celestial body:

- **5 concentric shells** (outside → inside): MARKET / REGIME, PROVIDERS, SIGNALS / SCAN, STRATEGY × CELL, GATE / RISK
- **4 inner cores** at center: OKX, CAP, ALP, BIN (each is a small sub-sphere holding open positions)
- **Orbiting AI satellites** on three altitudes (high / mid / low) plus direct-tool diamonds (REGIME DETECTOR, EVOLVER)
- **External satellites**: EXIT (close orbit), OBS (wide watcher), ACTION (output ring)

Inbound flows pulse outside-in, AI satellites fire vertical beams down to shell nodes, outbound arcs return from inner cores back through EXIT.

---

## When to use this skill

Drop this in whenever the user wants:
- A live overview dashboard for the trading system
- A "neural cloud" / "galaxy" / "북극성 전체 그림" visualization
- A handoff visual for showing system state to a person
- A debugging surface where you can see which AI / shell is active right now

---

## File layout

```
polaris-neural-cloud/
  SKILL.md            ← this file
  README.md           ← human-facing docs
  src/                ← editable source (preferred entry: src/index.html)
    index.html
    sphere-render.js  ← topology + render loop + PolarisCloud API
    data-adapter.js   ← binds JSON snapshots → nodes/satellites + tails events
    interaction.js    ← hover/click/chain
    colors_and_type.css
    data/             ← mock snapshots — replace with real data
      regime.json
      providers.json
      pipeline.json
      strategies.json
      exit.json
      obs.json
      actions.json
      events.jsonl
  bundle/
    polaris-neural-cloud.html  ← self-contained single-file (~145KB) for handoff
```

Two ways to deploy:
- **Drop `bundle/polaris-neural-cloud.html`** somewhere and open it. Self-contained, no deps. For handoff / read-only.
- **Drop the whole `src/` folder** if you need to edit topology, swap data live, or hot-reload during dev. Requires a static server (`python3 -m http.server`) for the `fetch('data/*.json')` calls to work.

---

## Step 1 — Bind real AI module names

The mock has placeholder satellite labels (`AI · HIGH α`, `AI · MID β`, ...). Replace them by scanning the codebase for actual AI modules and calling `bindAIModules` once on load.

### Tier classification rule

Decide each AI module's tier:

| tier | when | examples |
|---|---|---|
| `high` | wide context, slow cadence, big decisions (capital allocation, master verdicts) | master_judge, allocator |
| `mid`  | tactical AI — signal evaluation, critic, conviction scoring | critic, conviction, scout |
| `low`  | tight transactional AI — fast cadence, narrow scope (exit timing, gate) | exit_timer, gate_ai, micro_tuner |
| `tool` | NOT AI — deterministic system module shown alongside AI for context | regime_detector, evolver |

### Slot capacity (current build)

- `high`: 2 slots
- `mid`: 3 slots
- `low`: 3 slots
- `tools`: 2 slots

Pass more than this and the extras are silently dropped. If the user has more modules than slots, ask before truncating, or add slots to `SATELLITES` in `sphere-render.js`.

### Code to add

Append to `src/index.html` (or to bundle's last script block):

```html
<script>
window.addEventListener('load', () => {
  setTimeout(() => {
    window.PolarisCloud.bindAIModules({
      high: [
        { id: 'master_judge',     name: 'MASTER JUDGE',  weight: 0.45 },
        { id: 'capital_allocator', name: 'ALLOCATOR',    weight: 0.30 },
      ],
      mid: [
        { id: 'critic',     name: 'CRITIC',     weight: 0.30 },
        { id: 'conviction', name: 'CONVICTION', weight: 0.25 },
        { id: 'scout',      name: 'SCOUT',      weight: 0.20 },
      ],
      low: [
        { id: 'exit_timer',  name: 'EXIT TIMER', weight: 0.12 },
        { id: 'gate_ai',     name: 'GATE AI',    weight: 0.10 },
        { id: 'micro_tuner', name: 'MICRO',      weight: 0.08 },
      ],
      tools: [
        { id: 'regime_detector', name: 'REGIME DETECTOR' },
        { id: 'evolver',         name: 'EVOLVER' },
      ],
    });
  }, 500);
});
</script>
```

`name` should be ≤14 chars, ALL CAPS preferred (the rendering uses tracked monospace).

`color` is optional — array of 3 ints `[r,g,b]`. Default colors per tier are good.

---

## Step 2 — Wire real data (optional)

The default build runs forever on mock JSONs and a synthetic event tail. To go live, two paths:

### Path A — JSON file replacement (simplest)

Have the trading system write/replace these on a heartbeat:

| file | shape |
|---|---|
| `data/regime.json` | `{ current, score, fg_index, vix, btc_dom, since }` |
| `data/providers.json` | `{ ai_judges: [...], data: [...] }` (each with `id, kind, weight, calls_24h, lag_ms, ok_rate, ...`) |
| `data/pipeline.json` | `{ open_positions: [{ ticker, dir, size_usd, pnl_pct, age_min, exchange }, ...], scan_candidates: [{ ticker, score, exchange }, ...] }` |
| `data/strategies.json` | `{ cells: [{ id, elo, status, kind, trades_24h, win, pnl_pct }, ...] }` |
| `data/exit.json` | `{ metrics: [{ id, label, value }, ...] }` |
| `data/obs.json` | `{ checks: [{ id, label, value, unit, ok }, ...] }` |
| `data/actions.json` | `{ queue: [{ id, sev, label, since_min }, ...] }` |
| `data/events.jsonl` | append-only, one JSON event per line (see schema below) |

Refer to `src/data/*.json` for full mock examples.

Reload (or call `PolarisData.reload()` from console) to apply.

### Path B — WebSocket push

```js
PolarisCloud.connectWS('ws://localhost:7777');
```

Server pushes JSON messages:

```json
{"type": "regime", "value": "RISK_OFF"}
{"type": "nsi", "value": 0.87}
{"type": "pulse", "from": "providers", "to": "signals"}
{"type": "cluster", "cluster": "exit", "n": 3}
{"type": "trade", "ticker": "BTC-USDT-SWAP", "dir": "L", "exchange": "okx", "pnl": 1.2}
{"type": "kill", "value": true}
{"type": "bind_ai", "modules": { "high": [...], "mid": [...], "low": [...], "tools": [...] }}
```

### events.jsonl event kinds

```jsonc
{"ts": "19:14:08", "kind": "regime", "from": "NEUTRAL", "to": "RISK_OFF"}
{"ts": "...", "kind": "signal", "strategy": "trend_v3", "ticker": "BTC-USDT", "conviction": 0.82}
{"ts": "...", "kind": "ai_judge", "model": "master_judge", "verdict": "PASS", "strategy": "trend_v3"}
{"ts": "...", "kind": "ai_critic", "model": "critic", "verdict": "WARN", "strategy": "trend_v3"}
{"ts": "...", "kind": "gate_pass", "strategy": "trend_v3", "ticker": "BTC-USDT"}
{"ts": "...", "kind": "gate_fail", "strategy": "trend_v3", "ticker": "BTC-USDT", "reason": "h9_cap"}
{"ts": "...", "kind": "trade_open",  "ticker": "BTC-USDT-SWAP", "dir": "L", "size_usd": 8200, "strategy": "trend_v3", "exchange": "okx"}
{"ts": "...", "kind": "trade_close", "ticker": "BTC-USDT-SWAP", "dir": "L", "pnl_pct": 1.2, "reason": "trail", "exchange": "okx"}
{"ts": "...", "kind": "exit_trail", "ticker": "BTC-USDT", "pnl_pct": 0.8, "strategy": "trend_v3"}
{"ts": "...", "kind": "exit_bep",   "ticker": "BTC-USDT", "pnl_pct": 0.0}
{"ts": "...", "kind": "evo_mutation", "method": "tournament", "parent": "trend_v3", "child": "trend_v4_a"}
{"ts": "...", "kind": "scan", "exchange": "okx", "candidates": 12}
{"ts": "...", "kind": "obs_warn", "check": "lag_okx_ws", "value": "1240ms"}
{"ts": "...", "kind": "obs_ok",   "check": "lag_okx_ws", "value": "180ms"}
{"ts": "...", "kind": "action_emit", "sev": "WARN", "label": "okx ws degraded"}
```

---

## Step 3 — Programmatic API (console / external)

```js
PolarisCloud.setRegime('CRISIS')             // RISK_ON | RISK_OFF | NEUTRAL | TRANSITION | CRISIS
PolarisCloud.setNSI(0.42)                    // 0..1, normalized system integrity
PolarisCloud.setKillSwitch(true)             // emergency: ACTION satellite blasts gate
PolarisCloud.pulseShell('strategy', 6)       // animate a shell
PolarisCloud.pulseEdge('providers','signals')// animate radial flow between two shells
PolarisCloud.pulseCluster('exit', 4)         // animate a satellite (also accepts shell ids)
PolarisCloud.fireTrade({                     // full cascade market→core→supernova→exit
  ticker: 'ETH-USDT-SWAP',
  dir: 'L',
  exchange: 'okx',  // 'okx' | 'cap' | 'alp' | 'bin'
  pnl: 2.1
})
PolarisCloud.highlightChain(nodeIdx)         // light up provenance from any node
PolarisCloud.clearChain()
PolarisCloud.stats()                         // { nodes, shells, cores, satellites, edges, firing }
```

---

## Step 4 — Topology cheatsheet

```
  outermost                                                    innermost
  ─────────────────────────────────────────────────────────────────────
  MARKET ─→ PROVIDERS ─→ SIGNALS ─→ STRATEGY ─→ GATE ─→ INNER CORES
  shell 0    shell 1     shell 2    shell 3     shell 4   (4 sub-spheres)
  r=1.00     r=0.85      r=0.70     r=0.55      r=0.40    OKX/CAP/ALP/BIN

  Orbits (cross-cutting):
    AI HIGH      r≈1.30   slow, wide       2 slots
    AI MID       r≈1.00   medium           3 slots
    DIRECT TOOLS r≈0.78   medium, diamond  2 slots
    AI LOW       r≈0.50   fast, tight      3 slots

  External output ring:
    OBS    r≈1.45  square   wide watcher
    ACTION r≈1.55  square   outermost
    EXIT   r≈0.28  square   loops inner cores
```

Outbound flow (after a trade closes): inner core → EXIT satellite → ACTION → off-screen.

---

## Step 5 — Controls

- Mouse drag → rotate
- Wheel → zoom
- Space → toggle auto-rotate
- R → reset camera
- Click any shell node → side panel + provenance chain (lights up the radial path)
- Click any satellite → satellite detail + carried entities
- Click any legend row → pulse that shell/satellite
- Esc → close detail panel

---

## Common edits

- **More AI slots**: add entries to the `SATELLITES` array in `src/sphere-render.js` with the desired `tier`. Keep `axis` vectors unique so trails don't overlap.
- **More inner cores** (e.g. add HYP, MEX): add to `INNER_CORES` array.
- **Different shell colors**: edit `SHELLS[i].color` ([R,G,B] 0-255).
- **Different shell radii**: edit `SHELLS[i].radius`. Keep them strictly decreasing and >0.40 for the innermost (otherwise gate-core edges look weird).
- **Add a new kind of edge effect**: there are slots in `drawFiring()` for `radial`, `gate-core`, `intra-shell`, `intra-core`. Add a new `kind` and a renderer.

---

## Re-bundling after edits

The bundle in `bundle/` is generated from `src/`. After editing `src/`, regenerate with whatever your bundler is (or just ship the `src/` folder plus a static server). The bundle is purely for "drop one file and open" handoffs.
