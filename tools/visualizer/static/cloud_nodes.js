/* Polaris FLOW — "Flat Neural Map" structural layer (Jin 2026-07-10,
 * feat/flat-neural-map). Owns the Z2..Z5 zone NODES (strategy / gate /
 * execution-hub+position-orbit / exit·learn) + the static blueprint wiring
 * between them + the ambient low-alpha circulation riding that wiring.
 * cloud.js owns the canvas/rAF loop, the Z1 universe reservoir, and the
 * inbound ticker-dot journey (Z1 -> its strategy node -> the G2..G5 gate
 * chain -> the Z4 hub) — it calls into this module's tick()/draw() once a
 * frame (same split-file precedent as cloud_fx.js) and reads this module's
 * point*() getters to know where to glide its own dots to. This module owns
 * the OUTBOUND leg (Z4 hub -> G7 -> G8 -> a "lesson" particle back to the
 * strategy node that produced the trade) because that leg starts from data
 * this module already tracks (the open-position orbit), not from a Z1 dot.
 *
 * Static blueprint wiring vs. an "이상한 선 발사" radial effect (Jin
 * explicitly rejected the latter, feat/cloud-river precedent): the lines
 * drawn here are a FIXED, always-visible design-blueprint mesh (like a PCB
 * trace) — they never spawn/fade per event. Only PARTICLES (ambient dim ones
 * riding the mesh at all times, or bright ones tied to a real dot/fx) move.
 *
 * Data: /static/graph.json cluster:"pos" (setRoster) + /api/flow_stats
 * `classes`/`strategy_activity`/`stages` (setStats) + /stream/events exit
 * events + AI-judge verdicts (both forwarded in by cloud.js/flow.js).
 * Display-only — nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const fx = window.PolarisCloudFx;
  if (!fx) return;

  const CLASS_RGB = { EARN: [0x87, 0xff, 0xaf], BENCH: [0xff, 0x87, 0x87], PROVE: [0x87, 0xd7, 0xff] };
  const VERDICT_RGB = {
    dim: [0x8a, 0x90, 0x9c], green: [0x87, 0xff, 0xaf],
    amber: [0xff, 0xd0, 0x70], magenta: [0xff, 0x7a, 0xea],
  };
  const WIRE_RGB = [0x6a, 0x7a, 0x96];
  const AMBIENT_RGB = [0x8c, 0x96, 0xaa];
  const G7_HOLD_MS = 900, G8_HOLD_MS = 700;
  const GATE_IDS = [2, 3, 4, 5];
  const GATE_LABEL = { 2: 'SIGNAL', 3: 'VALIDATE', 4: 'WATCH', 5: 'SIZE' };
  const GATE_AI = { 3: true, 4: true };
  const GOLDEN_ANGLE = 2.399963; // ~137.508deg — phyllotaxis (sunflower) spacing, no overlap without physics

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function venueOf(ex) {
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return 'okx';
  }
  const skey = (v, s) => v + '|' + s;

  // ── Zone geometry (recomputed by cloud.js's fit()) ──────────────────────
  let zones = null;
  let paneTop = 50, paneBottom = 500, cy = 275;
  let gateX = {}, hubXY = { x: 0, y: 0 }, z5X = { 7: 0, 8: 0 }, orbitMaxR = 40;

  function layout(W, H, z) {
    zones = z;
    paneTop = 50;
    paneBottom = Math.max(paneTop + 80, H - 18);
    cy = (paneTop + paneBottom) / 2;
    const n = GATE_IDS.length, zw3 = zones.z3.x1 - zones.z3.x0;
    GATE_IDS.forEach((gid, i) => { gateX[gid] = zones.z3.x0 + (n > 1 ? (i * zw3) / (n - 1) : zw3 / 2); });
    hubXY = { x: (zones.z4.x0 + zones.z4.x1) / 2, y: cy };
    const zw5 = zones.z5.x1 - zones.z5.x0;
    z5X = { 7: zones.z5.x0 + zw5 * 0.3, 8: zones.z5.x0 + zw5 * 0.75 };
    orbitMaxR = Math.max(28, Math.min(zones.z4.x1 - zones.z4.x0, paneBottom - paneTop) / 2 - 16);
    layoutStrategyNodes();
    return { paneTop, paneBottom, cy };
  }

  // ── Z2 strategy nodes (pts-classes routing state, flow_stats.classes) ───
  let stratNodes = [];
  let stratByKey = {};
  let wireEdges = []; // static blueprint [x0,y0,x1,y1,alpha]

  function layoutStrategyNodes() {
    if (!zones) return;
    const zw = zones.z2.x1 - zones.z2.x0, zh = paneBottom - paneTop;
    const n = stratNodes.length || 1;
    const cols = Math.max(2, Math.min(4, Math.round(zw / 92)));
    const rows = Math.max(1, Math.ceil(n / cols));
    const cw = zw / cols, ch = zh / rows;
    stratNodes.forEach((node, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      node.x = zones.z2.x0 + cw * (col + 0.5);
      node.y = paneTop + ch * (row + 0.5);
    });
    rebuildWireEdges();
  }

  function rebuildWireEdges() {
    if (!zones) return;
    wireEdges = [];
    const z1a = { x: zones.z1.x1, y: cy };
    for (const n of stratNodes) wireEdges.push([z1a.x, z1a.y, n.x, n.y, 0.09]);
    const g2 = { x: gateX[2], y: cy };
    for (const n of stratNodes) wireEdges.push([n.x, n.y, g2.x, g2.y, 0.08]);
    for (let i = 0; i < GATE_IDS.length - 1; i++) {
      wireEdges.push([gateX[GATE_IDS[i]], cy, gateX[GATE_IDS[i + 1]], cy, 0.24]);
    }
    wireEdges.push([gateX[5], cy, hubXY.x, hubXY.y, 0.24]);
    wireEdges.push([hubXY.x, hubXY.y, z5X[7], cy, 0.24]);
    wireEdges.push([z5X[7], cy, z5X[8], cy, 0.24]);
  }

  function setStats(classes, activity, stages) {
    const actByKey = {};
    for (const a of activity || []) actByKey[skey(a.venue, a.strategy_id)] = a;
    const nextByKey = {};
    for (const c of classes || []) {
      const key = skey(c.venue, c.strategy_id);
      let node = stratByKey[key];
      if (!node) {
        node = {
          key, venue: c.venue, strategy_id: c.strategy_id, x: 0, y: 0,
          satellites: [0, 1].map(() => ({
            phase: Math.random() * 6.2832, speed: 0.6 + Math.random() * 0.5,
            r: 10 + Math.random() * 5,
          })),
        };
      }
      node.cls = c.strategy_class;
      node.window_w = c.window_w;
      node.filled = c.filled;
      const a = actByKey[key];
      node.activity = a ? (a.signals_n || 0) + (a.fills_n || 0) : 0;
      nextByKey[key] = node;
    }
    stratByKey = nextByKey;
    stratNodes = Object.values(nextByKey).sort((a, b) => a.key < b.key ? -1 : 1);
    layoutStrategyNodes();
    gateStats = {};
    for (const s of stages || []) gateStats[s.gate_id] = s;
  }
  let gateStats = {};

  // ── Z4 hub + open-position orbit (phyllotaxis slots — dense, no stacking) ─
  const posSlots = new Map(); // "venue:ticker" -> {slot,x,y,venue,ticker,pnlUsd,nextTick}
  function setRoster(list) {
    const keys = (list || []).map((p) => p.venue + ':' + p.ticker).sort();
    const keySet = new Set(keys);
    for (const k of Array.from(posSlots.keys())) if (!keySet.has(k)) posSlots.delete(k);
    keys.forEach((k, i) => {
      let s = posSlots.get(k);
      if (!s) {
        s = { key: k, x: hubXY.x, y: hubXY.y, nextTick: performance.now() + 1200 + Math.random() * 2600 };
        posSlots.set(k, s);
      }
      s.slot = i;
    });
    for (const p of list || []) {
      const s = posSlots.get(p.venue + ':' + p.ticker);
      if (s) { s.venue = p.venue; s.ticker = p.ticker; s.pnlUsd = p.pnlUsd; }
    }
  }
  function orbitPoint(slot) {
    const ang = slot * GOLDEN_ANGLE;
    const r = Math.min(orbitMaxR, 14 + 6 * Math.sqrt(slot + 1));
    return { x: hubXY.x + Math.cos(ang) * r, y: hubXY.y + Math.sin(ang) * r * 0.75 };
  }

  // ── G7/G8 exit sequence — the OUTBOUND leg this module owns ─────────────
  const exiting = [];
  function onExit(e) {
    const venue = venueOf(e.exchange);
    const key = venue + ':' + e.ticker;
    const s = posSlots.get(key);
    const start = s ? { x: s.x, y: s.y } : hubXY;
    posSlots.delete(key);
    const pnl = parseFloat(e.pnl_usd) || 0;
    fx.spawnHitFlash(start.x, start.y);
    fx.spawnDamageNumber(start.x, start.y, pnl);
    fx.pushKillFeed(e);
    const fxKey = 'exit:' + key + ':' + performance.now();
    if (pnl >= 0) fx.spawnSparkleBurst(fxKey, start.x, start.y);
    else fx.spawnShatter(fxKey, start.x, start.y);
    exiting.push({
      venue, sid: e.strategy_id, leg: 1, phase: 'glide', t: 0, dur: 0.5,
      x0: start.x, y0: start.y, x1: z5X[7], y1: cy, x: start.x, y: start.y, holdT: 0,
    });
  }
  function tickExiting(dt) {
    for (let k = exiting.length - 1; k >= 0; k--) {
      const d = exiting[k];
      if (d.phase === 'glide') {
        d.t = Math.min(1, d.t + dt / d.dur);
        d.x = lerp(d.x0, d.x1, easeOutCubic(d.t));
        d.y = lerp(d.y0, d.y1, easeOutCubic(d.t));
        if (d.t >= 1) { d.phase = 'hold'; d.holdT = 0; d.holdMs = d.leg === 1 ? G7_HOLD_MS : G8_HOLD_MS; }
      } else {
        d.holdT += dt * 1000;
        if (d.holdT < d.holdMs) continue;
        if (d.leg === 1) {
          d.leg = 2; d.phase = 'glide'; d.t = 0; d.dur = 0.5;
          d.x0 = d.x; d.y0 = d.y; d.x1 = z5X[8]; d.y1 = cy;
        } else {
          const target = pointForStrategy(d.venue, d.sid) || { x: zones ? zones.z1.x1 : d.x, y: cy };
          fx.spawnFeedbackArc(d.x, d.y, target.x, target.y);
          exiting.splice(k, 1);
        }
      }
    }
  }

  // ── AI verdict halo (replaces the removed #verdict-ticker DOM panel) ────
  function onVerdict(v) {
    const color = VERDICT_RGB[v.color] || VERDICT_RGB.dim;
    let target = null;
    if (gateX[v.gate_id] != null) target = { x: gateX[v.gate_id], y: cy };
    else if (v.gate_id === 7) target = { x: z5X[7], y: cy };
    if (target) fx.spawnAiHalo(target.x, target.y, color);
  }

  // ── Point getters (consumed by cloud.js's own Z1->hub dot journey) ──────
  function pointForStrategy(venue, sid) {
    const n = stratByKey[skey(venue, sid)];
    return n ? { x: n.x, y: n.y } : null;
  }
  function pointForGate(gid) { return gateX[gid] != null ? { x: gateX[gid], y: cy } : null; }
  function pointForZ4Hub() { return { x: hubXY.x, y: hubXY.y }; }

  // ── Ambient circulation — dim, anonymous, honest-cadence-only particles
  // riding the static wiring (G6 monitor cadence / bar ticks / roster
  // refresh — never a stand-in for a real signal/fill, those are the bright
  // dots cloud.js drives). Capped concurrency, cheap per-frame cost. ────────
  const AMBIENT_MAX = 40;
  const ambient = [];
  let ambientAccum = 0;
  function tickAmbient(dt) {
    ambientAccum += dt;
    if (ambientAccum > 0.35 && ambient.length < AMBIENT_MAX && wireEdges.length) {
      ambientAccum = 0;
      const e = wireEdges[(Math.random() * wireEdges.length) | 0];
      ambient.push({ x0: e[0], y0: e[1], x1: e[2], y1: e[3], t: 0, dur: 0.9 + Math.random() * 0.6 });
    }
    for (let k = ambient.length - 1; k >= 0; k--) {
      ambient[k].t += dt / ambient[k].dur;
      if (ambient[k].t >= 1) ambient.splice(k, 1);
    }
  }

  // ── Draw ──────────────────────────────────────────────────────────────
  function drawWiring(ctx) {
    for (const [x0, y0, x1, y1, a] of wireEdges) {
      ctx.strokeStyle = rgba(WIRE_RGB, a);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    }
    // learning-loop guide: G8 -> Z2 centroid, a standing dashed arc (Jin's
    // "이상한 선 발사" ban is about radial burst FX, not a fixed blueprint
    // curve — this is always-on, never spawns/fades).
    if (!zones || !stratNodes.length) return;
    const cx2 = (zones.z2.x0 + zones.z2.x1) / 2, cyMid = (paneTop + paneBottom) / 2;
    ctx.save();
    ctx.setLineDash([2, 4]);
    ctx.strokeStyle = rgba(WIRE_RGB, 0.10);
    ctx.beginPath();
    ctx.moveTo(z5X[8], cy);
    ctx.quadraticCurveTo((z5X[8] + cx2) / 2, paneTop - 30, cx2, cyMid);
    ctx.stroke();
    ctx.restore();
  }
  function drawAmbient(ctx) {
    for (const p of ambient) {
      const x = lerp(p.x0, p.x1, p.t), y = lerp(p.y0, p.y1, p.t);
      ctx.fillStyle = rgba(AMBIENT_RGB, (1 - p.t) * 0.28);
      ctx.beginPath(); ctx.arc(x, y, 1.1, 0, 6.2832); ctx.fill();
    }
  }
  function drawStratNodes(ctx, now) {
    ctx.font = '600 7.5px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    for (const n of stratNodes) {
      const color = CLASS_RGB[n.cls] || CLASS_RGB.PROVE;
      const r = Math.max(4.5, Math.min(10, 5 + Math.sqrt(n.activity || 0) * 1.2));
      if (n.cls === 'PROVE') {
        for (const sat of n.satellites) {
          const a = now / 1000 * sat.speed + sat.phase;
          ctx.fillStyle = rgba(color, 0.35);
          ctx.beginPath();
          ctx.arc(n.x + Math.cos(a) * sat.r, n.y + Math.sin(a) * sat.r, 1.1, 0, 6.2832);
          ctx.fill();
        }
      }
      ctx.fillStyle = 'rgba(12,15,22,0.85)';
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832); ctx.fill();
      ctx.strokeStyle = rgba(color, 0.85); ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832); ctx.stroke();
      ctx.fillStyle = 'rgba(220,226,240,0.8)';
      ctx.textBaseline = 'bottom';
      ctx.fillText(String(n.strategy_id).slice(0, 11), n.x, n.y - r - 2);
      ctx.fillStyle = 'rgba(140,148,164,0.75)';
      ctx.textBaseline = 'top';
      ctx.fillText((n.filled || 0) + '/' + (n.window_w || 0), n.x, n.y + r + 2);
    }
  }
  function drawGateNodes(ctx) {
    ctx.font = '700 8px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    for (const gid of GATE_IDS) {
      const x = gateX[gid], g = gateStats[gid];
      const n = (g && (gid === 5 ? g.sized_n : g.total)) || 0;
      ctx.fillStyle = 'rgba(12,15,22,0.9)';
      ctx.beginPath(); ctx.arc(x, cy, 7, 0, 6.2832); ctx.fill();
      ctx.strokeStyle = GATE_AI[gid] ? 'rgba(255,122,234,0.85)' : 'rgba(95,175,255,0.85)';
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(x, cy, 7, 0, 6.2832); ctx.stroke();
      ctx.fillStyle = 'rgba(220,226,240,0.85)';
      ctx.textBaseline = 'bottom';
      ctx.fillText('G' + gid + ' ' + GATE_LABEL[gid] + (GATE_AI[gid] ? ' · AI' : ''), x, cy - 11);
      ctx.fillStyle = 'rgba(160,168,184,0.8)';
      ctx.textBaseline = 'top';
      ctx.fillText(String(n), x, cy + 11);
    }
  }
  function drawHubAndPositions(ctx, now) {
    ctx.fillStyle = 'rgba(220,226,240,0.85)';
    ctx.beginPath(); ctx.arc(hubXY.x, hubXY.y, 6, 0, 6.2832); ctx.fill();
    ctx.font = '700 8px JetBrains Mono, monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText('MONITOR', hubXY.x, hubXY.y - 10);
    for (const s of posSlots.values()) {
      const p = orbitPoint(s.slot);
      s.x = p.x; s.y = p.y;
      const win = (s.pnlUsd || 0) >= 0;
      ctx.fillStyle = win ? 'rgba(135,255,175,0.85)' : 'rgba(255,135,135,0.85)';
      ctx.beginPath(); ctx.arc(p.x, p.y, 2.6, 0, 6.2832); ctx.fill();
      if (now >= s.nextTick) {
        fx.spawnTickPulse(p.x, p.y);
        s.nextTick = now + 2000 + Math.random() * 3000;
      }
    }
  }
  function drawZ5AndExiting(ctx) {
    ctx.font = '700 8px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    [[7, 'G7 EXIT · AI'], [8, 'G8 REFLECT']].forEach(([gid, label]) => {
      const x = z5X[gid], g = gateStats[gid];
      const n = (g && (gid === 7 ? g.exits_n : g.total)) || 0;
      ctx.fillStyle = 'rgba(12,15,22,0.9)';
      ctx.beginPath(); ctx.arc(x, cy, 7, 0, 6.2832); ctx.fill();
      ctx.strokeStyle = gid === 7 ? 'rgba(255,122,234,0.85)' : 'rgba(95,175,255,0.85)';
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(x, cy, 7, 0, 6.2832); ctx.stroke();
      ctx.fillStyle = 'rgba(220,226,240,0.85)'; ctx.textBaseline = 'bottom';
      ctx.fillText(label, x, cy - 11);
      ctx.fillStyle = 'rgba(160,168,184,0.8)'; ctx.textBaseline = 'top';
      ctx.fillText(String(n), x, cy + 11);
    });
    for (const d of exiting) {
      ctx.fillStyle = 'rgba(242,246,255,0.9)';
      ctx.beginPath(); ctx.arc(d.x, d.y, 4.2, 0, 6.2832); ctx.fill();
    }
  }

  function tick(dt) { tickExiting(dt); tickAmbient(dt); }
  function draw(ctx, now) {
    drawWiring(ctx);
    drawAmbient(ctx);
    drawStratNodes(ctx, now);
    drawGateNodes(ctx);
    drawHubAndPositions(ctx, now);
    drawZ5AndExiting(ctx);
  }

  // Playwright/manual-QA hook only (Jin 2026-07-10 verification checklist item
  // 4 — position-dot count + no-overlap assert). Read-only, no behavioural
  // effect; the actual no-overlap guarantee is the phyllotaxis math in
  // orbitPoint() above (golden-angle spacing), not this getter.
  function debugCounts() { return { positions: posSlots.size, strategies: stratNodes.length }; }

  window.PolarisCloudNodes = {
    layout, setStats, setRoster, onExit, onVerdict,
    pointForStrategy, pointForGate, pointForZ4Hub,
    tick, draw, debugCounts,
  };
})();
