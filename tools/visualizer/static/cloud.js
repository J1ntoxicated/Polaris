/* Polaris FLOW — "Flat Neural Map" (Jin 2026-07-10, feat/flat-neural-map).
 * Supersedes the earlier "Cloud River" 3-band layout: venue is now color-only
 * (a small legend chip, no lanes) and the pane reads as a single wide system
 * blueprint — Z1 UNIVERSE (this file's reservoir) .. Z2 STRATEGIES .. Z3
 * GATES .. Z4 EXECUTION .. Z5 EXIT/LEARN (all four owned by cloud_nodes.js).
 * This file owns the canvas/rAF loop (calls cloud_nodes.tick()/draw() and
 * cloud_fx.tick()/draw() once a frame — 3 modules, ONE loop), the zone
 * geometry, the Z1 ticker reservoir simulation (jittered-grid placement so
 * ~900 dots never stack — see assignGrid), and the INBOUND ticker-dot
 * journey (Z1 -> its strategy node -> the G2..G5 gate chain -> the Z4 hub).
 * cloud_nodes.js owns the OUTBOUND leg (hub -> G7 -> G8 -> lesson particle)
 * because it already tracks the open-position orbit that leg starts from.
 *
 * The pane below (flow.js's river) is UNCHANGED — this pane no longer shares
 * its x-axis with it (the old "same stage-column pixel" contract made sense
 * for a 3-band river; a 5-zone system blueprint is a different visual
 * language, so this file now owns its own independent zone geometry).
 *
 * Data (all EXISTING endpoints — no server change beyond flow_data.py's
 * additive `strategy_activity` field):
 *   /static/graph.json  (3s poll) → cluster:"mkt" (Z1 reservoir) + cluster:
 *     "pos" (forwarded to cloud_nodes.setRoster for the Z4 orbit).
 *   /api/flow_stats (this file's OWN 5s poll — pollAdmissions, matches the
 *     server's 5s TTL — NOT forwarded from flow.js) → `classes` +
 *     `strategy_activity` (Z2 nodes) + `stages` (Z3/Z5 counts).
 *   /stream/events (shared bus) → gate_events (Z1->Z2/Z3 glide) + fills
 *     entry (Z1->Z4 glide) + exit (forwarded straight to cloud_nodes.onExit).
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const canvas = document.getElementById('cloud-canvas');
  const fx = window.PolarisCloudFx;
  const nodes = window.PolarisCloudNodes;
  if (!canvas || !fx || !nodes) return;
  const ctx = canvas.getContext('2d');

  const VCOLOR_RGB = { okx: [0x5f, 0xdf, 0xff], capital: [0xa8, 0x7c, 0xff], alpaca: [0xff, 0xc8, 0x4f] };
  const VENUES = ['okx', 'capital', 'alpaca'];

  const CLOUD_DOT_CAP = 900;           // spec allowance 600->900 (verified 60fps in preview, see notes)
  const DECAY_MS = 180000;             // 3min idle -> ease back to the Z1 reservoir grid slot

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function venueOf(ex) {
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return 'okx';
  }
  function cleanSymbol(sym) { return String(sym || '').split(':').pop().split('-')[0]; }

  // ── Canvas fit + zone geometry (independent of the river pane below) ────
  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  let zones = null;       // {z1:{x0,x1}, z2:{...}, ...}
  let paneTop = 50, paneBottom = 500, cy = 275;
  const ZONE_FRAC = { z1: [0.00, 0.22], z2: [0.24, 0.44], z3: [0.46, 0.64], z4: [0.66, 0.81], z5: [0.83, 0.97] };
  const ZONE_LABEL = { z1: 'UNIVERSE', z2: 'STRATEGIES', z3: 'GATES', z4: 'EXECUTION', z5: 'EXIT · LEARN' };

  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    zones = {};
    for (const k of Object.keys(ZONE_FRAC)) {
      zones[k] = { x0: ZONE_FRAC[k][0] * W, x1: ZONE_FRAC[k][1] * W };
    }
    const geo = nodes.layout(W, H, zones);
    paneTop = geo.paneTop; paneBottom = geo.paneBottom; cy = geo.cy;
    rebuildDormantRenderList();
  }
  window.addEventListener('resize', fit);

  // ── Z1 reservoir state ───────────────────────────────────────────────
  const dormant = new Map();      // key -> dot (cluster:"mkt")
  const tickerToKey = new Map();  // bare ticker -> dormant key
  let idleList = [];              // grid-assigned idle subset actually drawn
  let dormantRenderList = [];     // idleList + any mid-journey dots
  let meshEdges = [];             // [dotA, dotB] faint Z1-internal neighbor links

  function strideSample(arr, cap) {
    if (arr.length <= cap || cap <= 0) return cap <= 0 ? [] : arr;
    const stride = arr.length / cap;
    const out = [];
    for (let i = 0; i < cap; i++) out.push(arr[Math.floor(i * stride)]);
    return out;
  }
  function rebuildDormantRenderList() {
    if (!zones) return;
    const all = Array.from(dormant.values());
    const activeSet = all.filter((d) => d.restX != null || d.t < 1);
    const idleAll = all.filter((d) => d.restX == null && d.t >= 1);
    // firing dots stay in idleList (they need assignGrid slots) but are never
    // stride-sampled out — a hot ticker must stay rendered while glowing.
    const firingIdle = idleAll.filter((d) => d.firing);
    const dustIdle = idleAll.filter((d) => !d.firing);
    const budget = Math.max(0, CLOUD_DOT_CAP - activeSet.length - firingIdle.length);
    idleList = firingIdle.concat(strideSample(dustIdle, budget));
    dormantRenderList = idleList.concat(activeSet);
    assignGrid();
  }
  // Jittered grid ("blue-noise-ish") placement — Jin 2026-07-10: dots must
  // read as individually countable, never a stacked blob. Each idle dot gets
  // a stable grid cell (index-derived, so it doesn't jump between polls) +
  // a per-dot jitter CLAMPED to a fraction of the cell so neighbors never
  // overlap even after drift — no physics engine needed.
  function assignGrid() {
    const z1 = zones.z1, w = z1.x1 - z1.x0, h = paneBottom - paneTop;
    const n = idleList.length;
    if (!n) { meshEdges = []; return; }
    const cols = Math.max(1, Math.round(Math.sqrt((n * w) / h)));
    const rows = Math.max(1, Math.ceil(n / cols));
    const cw = w / cols, ch = h / rows;
    idleList.forEach((dot, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      dot.gx = z1.x0 + cw * (col + 0.5) + dot.jx * cw * 0.34;
      dot.gy = paneTop + ch * (row + 0.5) + dot.jy * ch * 0.34;
      dot.cellW = cw; dot.cellH = ch;
    });
    meshEdges = [];
    const MAX_MESH = 260;
    for (let i = 0; i < n && meshEdges.length < MAX_MESH; i++) {
      if ((i + 1) % cols !== 0 && i + 1 < n && i % 2 === 0) meshEdges.push([idleList[i], idleList[i + 1]]);
      if (i + cols < n && i % (cols * 2) === 0) meshEdges.push([idleList[i], idleList[i + cols]]);
    }
  }

  function ingestRoster(rosterNodes) {
    const seenD = new Set();
    const posList = [];
    for (const n of rosterNodes || []) {
      if (!n || !n.ticker || !n.exchange) continue;
      const venue = venueOf(n.exchange);
      if (n.cluster === 'mkt') {
        const key = 'd:' + n.exchange + ':' + n.ticker;
        seenD.add(key);
        tickerToKey.set(n.ticker, key);
        if (!dormant.has(key)) {
          dormant.set(key, {
            key, ticker: n.ticker, venue, x: null, y: null, gx: 0, gy: 0,
            cellW: 20, cellH: 20, restX: null, restY: null, t: 1, dur: 0.3,
            tx0: 0, ty0: 0, tx1: 0, ty1: 0, returning: false,
            driftSeed: Math.random() * 1000, twinkleSeed: Math.random() * 1000,
            jx: Math.random() - 0.5, jy: Math.random() - 0.5,
            lastActivityTs: 0, focus: false, firing: false, glow: 0,
          });
        }
        // live L0 state (Jin 2026-07-10 "지켜보는 애들은 글로잉 해야"):
        // firing = signal activity now (graph.json state/signal_count_30m);
        // focus = L0 watchlist member. Refreshed every roster poll — glow
        // tiers in drawDormant read these, no fabrication (roster is SSOT).
        const dd = dormant.get(key);
        dd.focus = n.active === true;
        dd.firing = n.state === 'firing' || (n.signal_count_30m || 0) > 0;
        dd.glow = Math.max(0, Math.min(1, n.intensity != null ? +n.intensity : 0));
      } else if (n.cluster === 'pos') {
        // sid = strategy_id — the per-lot discriminator (venue:ticker alone
        // collapses two open positions in the SAME instrument held by two
        // DIFFERENT strategies into one orbit slot; review finding 2026-07-10
        // — see cloud_nodes.js setRoster).
        posList.push({ ticker: n.ticker, venue, pnlUsd: n.pnl_usd || 0, sid: n.strategy_id || '' });
      }
    }
    for (const key of Array.from(dormant.keys())) if (!seenD.has(key)) dormant.delete(key);
    // days-long wall use: universe rotation would otherwise slow-grow this map
    for (const [t, key] of Array.from(tickerToKey.entries())) if (!seenD.has(key)) tickerToKey.delete(t);
    nodes.setRoster(posList);
    rebuildDormantRenderList();
  }

  async function pollRoster() {
    try {
      const r = await fetch('/static/graph.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      ingestRoster(d.nodes || []);
    } catch (e) { /* display-only — keep last frame */ }
  }

  function sweepDecay() {
    const now = performance.now();
    for (const dot of dormant.values()) {
      if (dot.restX != null && dot.t >= 1 && now - dot.lastActivityTs > DECAY_MS) {
        triggerWarp(dot, { x: dot.gx, y: dot.gy }, true);
      }
    }
  }

  // ── Warp: ease tween to a new (x,y) target — generalizes the old 1D
  // "stage" warp to the 2D zone map. `returning` marks a decay/hold-timeout
  // ease-back to the Z1 grid slot, so the draw loop knows to clear restX/Y
  // (go fully idle) once the tween lands, instead of parking there. ────────
  function triggerWarp(dot, point, returning) {
    const curX = dot.x != null ? dot.x : dot.gx, curY = dot.y != null ? dot.y : dot.gy;
    dot.tx0 = curX; dot.ty0 = curY;
    dot.tx1 = point.x; dot.ty1 = point.y;
    dot.t = 0; dot.dur = 0.4 + Math.random() * 0.3;
    dot.restX = point.x; dot.restY = point.y;
    dot.returning = !!returning;
    dot.lastActivityTs = performance.now();
  }

  // ── survivor_admissions_recent + classes/stats (own 5s poll, matches the
  // server's 5s TTL — Jin 2026-07-10 end-to-end <=5s realtime-sync mandate;
  // NOT forwarded from flow.js, which polls the same endpoint separately) ──
  let lastAdmissionTs = 0, admissionsSeeded = false;
  async function pollAdmissions() {
    try {
      const r = await fetch('/api/flow_stats?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      nodes.setStats(d.classes || [], d.strategy_activity || [], d.stages || []);
      const list = d.survivor_admissions_recent || [];
      if (admissionsSeeded) {
        for (const a of list) {
          if (a.ts > lastAdmissionTs && zones) {
            const vi = VENUES.indexOf(venueOf(a.venue));
            const ax = zones.z1.x0 + (zones.z1.x1 - zones.z1.x0) * 0.5;
            fx.spawnLootBeam('adm:' + a.symbol, ax, cy + (vi - 1) * 16);
          }
        }
      }
      admissionsSeeded = true;
      if (list.length) lastAdmissionTs = Math.max(lastAdmissionTs, ...list.map((a) => a.ts));
    } catch (e) { /* display-only */ }
  }

  // ── SSE: gate_events (Z1->strategy->gate-chain glide) + fills entry
  // (Z1->hub glide, then ease back — the actual position orbit dot is a
  // SEPARATE roster-driven object owned by cloud_nodes) + exit (forwarded
  // straight through — cloud_nodes owns the whole outbound leg). ──────────
  function onGateEvent(g) {
    if (!zones || g.gate_id < 2 || g.gate_id > 5) return;
    const key = tickerToKey.get(cleanSymbol(g.symbol));
    const dot = key && dormant.get(key);
    if (!dot) return;
    let target = null;
    if (g.gate_id === 2) target = (g.strategy && nodes.pointForStrategy(dot.venue, g.strategy)) || nodes.pointForGate(2);
    else target = nodes.pointForGate(g.gate_id);
    if (!target) return;
    triggerWarp(dot, target, false);
    if (g.gate_id === 2) fx.spawnRadarPing(target.x, target.y, VCOLOR_RGB[dot.venue]);
  }
  function onEntry(e) {
    const key = tickerToKey.get(e.ticker);
    const dot = key && dormant.get(key);
    if (!dot) return;
    const target = nodes.pointForZ4Hub();
    triggerWarp(dot, target, false);
    fx.spawnHitFlash(target.x, target.y);
    setTimeout(() => triggerWarp(dot, { x: dot.gx, y: dot.gy }, true), dot.dur * 1000 + 500);
  }
  function handleStream(payload) {
    for (const e of payload.events || []) {
      if (e.type === 'entry') onEntry(e);
      else if (e.type === 'exit') nodes.onExit(e);
    }
    for (const g of payload.gate_events || []) onGateEvent(g);
  }

  // ── Draw: zone labels + Z1 reservoir (mesh + dots) ──────────────────────
  function drawZoneLabels() {
    ctx.font = '600 7px JetBrains Mono, monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillStyle = 'rgba(140,148,164,0.5)';
    for (const k of Object.keys(zones)) {
      ctx.fillText(ZONE_LABEL[k], (zones[k].x0 + zones[k].x1) / 2, paneTop - 20);
    }
  }
  function drawMesh() {
    ctx.strokeStyle = 'rgba(120,150,190,0.08)';
    ctx.lineWidth = 1;
    for (const [a, b] of meshEdges) {
      if (a.x == null || b.x == null) continue;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
  }
  function drawGlideTrail(x, y, dot) {
    const dx = dot.tx1 - dot.tx0, dy = dot.ty1 - dot.ty0;
    const dist = Math.hypot(dx, dy) || 1;
    const ux = -dx / dist, uy = -dy / dist;
    for (let i = 1; i <= 3; i++) {
      const a = (0.5 * (1 - dot.t)) * (1 - i / 4);
      ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], a);
      ctx.beginPath(); ctx.arc(x + ux * i * 3, y + uy * i * 3, 2 - i * 0.4, 0, 6.2832); ctx.fill();
    }
  }
  function drawDormant(now) {
    for (const dot of dormantRenderList) {
      let x, y;
      if (dot.t < 1) {
        dot.t = Math.min(1, dot.t + lastDt / dot.dur);
        x = lerp(dot.tx0, dot.tx1, easeOutCubic(dot.t));
        y = lerp(dot.ty0, dot.ty1, easeOutCubic(dot.t));
        drawGlideTrail(x, y, dot);
        if (dot.t >= 1 && dot.returning) { dot.restX = null; dot.restY = null; dot.returning = false; }
      } else if (dot.restX != null) {
        x = dot.restX + Math.sin(now / 900 + dot.driftSeed) * 2;
        y = dot.restY + Math.cos(now / 900 + dot.driftSeed) * 2;
      } else {
        const ampX = Math.min(18, dot.cellW * 0.4), ampY = Math.min(12, dot.cellH * 0.4);
        x = dot.gx + Math.sin(now / 4000 + dot.driftSeed) * ampX;
        y = dot.gy + Math.cos(now / 5200 + dot.driftSeed) * ampY;
      }
      dot.x = x; dot.y = y;
      const twinkle = 0.35 + 0.35 * Math.sin(now / 1400 + dot.twinkleSeed);
      const idle = dot.restX == null && dot.t >= 1;
      // Glow tiers (Jin 2026-07-10 "시그널/워치/지켜보는 애들은 글로잉"):
      // firing (live signal, roster state/signal_count_30m) > mid-pipeline >
      // focus (L0 watchlist) > dust. Halo = 2 concentric low-alpha arcs at
      // the dot only (element-local, no shadowBlur — cheap at firing counts).
      const hot = dot.firing || !idle;
      if (hot) {
        const pulse = 0.75 + 0.25 * Math.sin(now / 600 + dot.twinkleSeed);
        const boost = 0.55 + 0.45 * (dot.glow || 0);
        const core = idle ? 2.2 : 3;
        ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], 0.13 * pulse * boost);
        ctx.beginPath(); ctx.arc(x, y, core * 3.4, 0, 6.2832); ctx.fill();
        ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], 0.3 * pulse * boost);
        ctx.beginPath(); ctx.arc(x, y, core * 1.9, 0, 6.2832); ctx.fill();
        ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], Math.min(1, 0.7 + 0.3 * boost));
        ctx.beginPath(); ctx.arc(x, y, core, 0, 6.2832); ctx.fill();
        continue;
      }
      // baseline brightness scales with the roster's per-ticker intensity
      // (0..1, L0 watch level) — `active` alone is 602/638 so a binary lift
      // would wash out contrast; continuous intensity keeps the field readable
      // with the watched names visibly brighter (globe-era precedent).
      const focusLift = dot.focus ? 0.06 + 0.14 * (dot.glow || 0) : 0;
      const alpha = 0.14 + twinkle * 0.22 + focusLift;
      const r = dot.focus && dot.glow > 0.5 ? 1.9 : 1.5;
      ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], alpha);
      ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
    }
  }

  // Camera is fully fixed (Jin 2026-07-10 explicit: no zoom/pan/shake) — no
  // transform is applied here.
  let lastT = performance.now();
  let lastDt = 0;
  function frame(now) {
    lastDt = Math.min(0.1, (now - lastT) / 1000);
    lastT = now;
    ctx.clearRect(0, 0, W, H);
    nodes.tick(lastDt);
    drawZoneLabels();
    nodes.draw(ctx, now);
    drawMesh();
    drawDormant(now);
    fx.tick(lastDt);
    fx.draw(ctx, W, H);
    requestAnimationFrame(frame);
  }

  // Playwright/manual-QA hook only (Jin 2026-07-10 verification checklist
  // item 6 — confirm a real SSE gate_event actually warps a Z1 reservoir dot
  // off its idle grid slot). Read-only, no behavioural effect.
  window.PolarisCloud = {
    debugDot(ticker) {
      const key = tickerToKey.get(ticker);
      const dot = key && dormant.get(key);
      return dot ? { restX: dot.restX, restY: dot.restY, t: dot.t } : null;
    },
  };

  // ── Boot ─────────────────────────────────────────────────────────────
  fit();
  pollRoster();
  setInterval(pollRoster, 3000);
  setInterval(sweepDecay, 5000);
  pollAdmissions();
  setInterval(pollAdmissions, 5000); // classes/stats freshness (Jin: end-to-end <=5s)
  if (window.PolarisEvents) window.PolarisEvents.on(handleStream);
  requestAnimationFrame((t) => { lastT = t; requestAnimationFrame(frame); });
})();
