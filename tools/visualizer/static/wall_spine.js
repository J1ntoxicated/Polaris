/* Polaris FLOW — "Synaptic Spine" (Jin 2026-07-10, feat/jarvis-wall).
 * Supersedes the "Flat Neural Map" (cloud.js/cloud_nodes.js/cloud_fx.js) and
 * the "Living Pipeline River" (flow.js) with a single unified read: the G1..
 * G8 gate pipeline is a braided spine that dissolves into the universe/
 * strategy constellation around it (philosophy doc: vault/50_research/
 * wall_design_philosophy_synaptic_current.md) — no grid, no lanes, no
 * separate river canvas. Ported from the winning prototype
 * (concept_spine.html, judged against concept_organic/concept_radial) with
 * four grafts from the runner-ups (see wall_spine_field.js for grafts 1a/1b/
 * 1c/4 — layout, whisper mesh, parallax bob, lineage hue): (2) organic's
 * 9-point cream comet trail below, (3) radial's log-scaled activity-load arc
 * on each gate's jarvis ring below.
 *
 * This file owns the canvas/rAF loop, the 8 gate cores (arc-ring draw +
 * count), the comet pool (real events only — no simulator), and the small
 * public API (window.PolarisSpine) that wall_spine_hud.js drives with real
 * /static/graph.json + /api/flow_stats + /stream/events data. The background
 * constellation (layout/edges/whisper-mesh/static pre-render/parallax bob) is
 * wall_spine_field.js, loaded first.
 *
 * Camera is fully static (no zoom/pan/shake). No full-screen or radial-burst
 * effects — every effect is anchored to a node/edge. Additive ('lighter')
 * blending only for glow passes. Single rAF loop, English UI copy only.
 * Display-only: nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const canvas = document.getElementById('spine-canvas');
  const field = window.PolarisSpineField;
  if (!canvas || !field) return;
  const ctx = canvas.getContext('2d');

  const GATE_CORE = '#eafcff', GATE_HALO = '#5fd7ff', GATE_TICK = '#ffb454';
  // Jin 2026-07-10 "각 게이트 색도 좀 다르게": per-gate identity hues G1→G8
  // (cool→warm progression so pipeline depth also reads by color).
  const GATE_COLORS = ['#5fa8ff', '#5fdfff', '#6fffc4', '#9dff6f', '#ffe066', '#ffb454', '#ff7a9e', '#c48aff'];
  const GATE_SWEEP = '#87ffe0'; // graft 3 (radial) — activity-load sweep, distinct from the jarvis reticle tick color

  const GATES = [
    { n: 1, id: 'g1', label: 'universe', count: 0 },
    { n: 2, id: 'g2', label: 'signal', count: 0 },
    { n: 3, id: 'g3', label: 'validator', count: 0 },
    { n: 4, id: 'g4', label: 'preentry', count: 0 },
    { n: 5, id: 'g5', label: 'sizer', count: 0 },
    { n: 6, id: 'g6', label: 'monitor', count: 0 },
    { n: 7, id: 'g7', label: 'exit', count: 0 },
    { n: 8, id: 'g8', label: 'reflector', count: 0 },
  ];
  let maxGateLog = 1; // log1p(count) ceiling for the activity-sweep (graft 3)
  function setGateCounts(stages) {
    const byGate = {};
    (stages || []).forEach((s) => { byGate[s.gate_id] = s; });
    GATES.forEach((g) => {
      const s = byGate[g.n];
      g.count = (s && (g.n === 5 ? s.sized_n : g.n === 6 ? s.live_n : g.n === 7 ? s.exits_n : s.total)) || 0;
    });
    maxGateLog = Math.max(1, ...GATES.map((g) => Math.log1p(g.count)));
  }

  const staticLayer = document.createElement('canvas');
  const staticCtx = staticLayer.getContext('2d');
  let W = 1344, H = 962;
  function fitCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = rect.width; H = rect.height;
    canvas.width = staticLayer.width = Math.max(1, Math.round(W * dpr));
    canvas.height = staticLayer.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    staticCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    field.setSize(W, H);
  }

  /* ===== comets — bright particles = real events only ===== */
  const comets = [];
  function spawnComet(path, color, width) {
    if (!path || !path.length) return;
    comets.push({ path, t: 0, speed: 0.55 + Math.random() * 0.35, color, width: width || 2.2 });
  }
  // graft 2 (organic) — 9-point cream trail (reads as a real event streak
  // far better than a short same-hue tail); head keeps the event's own hue
  // so G6/G7/etc identity is still legible at a glance.
  function drawComets(now, dt) {
    ctx.globalCompositeOperation = 'lighter';
    for (let i = comets.length - 1; i >= 0; i--) {
      const c = comets[i];
      c.t += c.speed * dt;
      const total = c.path.length;
      if (c.t >= total) { comets.splice(i, 1); continue; }
      const idx = Math.min(total - 1, Math.floor(c.t));
      const seg = c.path[idx];
      const localT = c.t - idx;
      const sampleT = seg.reversed ? 1 - localT : localT;
      const pt = field.bezierPoint(seg.e, sampleT);
      for (let k = 9; k >= 1; k--) {
        const ltt = Math.max(0, localT - k * 0.035);
        const tt = seg.reversed ? 1 - ltt : ltt;
        const tp = field.bezierPoint(seg.e, tt);
        const a = 1 - k / 9;
        field.drawDot(ctx, tp.x, tp.y, c.width * 0.55 * a + 0.5, '#fff7e1', 0.7 * a * a, 0);
      }
      field.drawDot(ctx, pt.x, pt.y, c.width * 1.6, c.color, 0.95, 14);
      field.drawDot(ctx, pt.x, pt.y, c.width * 0.7, '#ffffff', 0.9, 6);
      field.markFire(seg.a, 500);
      field.markFire(seg.b, 900);
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  function drawGates(now, t) {
    const gateScreen = field.gateScreen();
    GATES.forEach((g, i) => {
      const gs = gateScreen[i];
      if (!gs) return;
      const fireT = Math.max(0, Math.min(1, (gs.fireUntil - now) / 900));
      // graft 1a — core/halo brightness tier lowered so the relay-hubs read
      // as woven INTO the field at rest, and only pop when actually firing.
      const core = 10 + fireT * 5.5, halo = 21 + fireT * 9;
      const gc = GATE_COLORS[i] || GATE_HALO;
      ctx.globalCompositeOperation = 'lighter';
      field.drawDot(ctx, gs.x, gs.y, halo, gc, 0.10 + fireT * 0.24, 12 + fireT * 15);
      field.drawDot(ctx, gs.x, gs.y, core, GATE_CORE, 0.68 + fireT * 0.28, 8 + fireT * 13);
      ctx.globalCompositeOperation = 'source-over';
      ctx.beginPath(); ctx.arc(gs.x, gs.y, core, 0, Math.PI * 2);
      ctx.strokeStyle = field.rgba(gc, 0.6); ctx.lineWidth = 1; ctx.stroke();

      // jarvis arc-ring reticle (rotating tick band)
      const rot = t * 0.05 + i * 0.61, ringR = 40, span = Math.PI * 1.45;
      ctx.save();
      ctx.translate(gs.x, gs.y);
      ctx.rotate(rot);
      ctx.beginPath(); ctx.arc(0, 0, ringR, -span / 2, span / 2);
      ctx.strokeStyle = field.rgba(GATE_TICK, 0.5 + fireT * 0.3); ctx.lineWidth = 1.1; ctx.stroke();
      for (let k = 0; k <= 14; k++) {
        const a = -span / 2 + (span * k / 14);
        const inR = ringR - 4, outR = ringR + (k % 2 === 0 ? 6 : 3);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * inR, Math.sin(a) * inR);
        ctx.lineTo(Math.cos(a) * outR, Math.sin(a) * outR);
        ctx.strokeStyle = field.rgba(GATE_TICK, 0.4); ctx.lineWidth = 0.8; ctx.stroke();
      }
      ctx.restore();

      // graft 3 (radial) — log-scaled activity-load sweep: a FIXED (non-
      // rotating) arc from -90deg whose length encodes real relative
      // throughput (log1p(count)/max), not a ratio (flow_not_block makes a
      // pass/kill ratio ~100% everywhere — length is the only honest signal).
      const sweep = Math.max(0.06, Math.min(1, Math.log1p(g.count) / maxGateLog)) * (Math.PI * 1.5);
      ctx.globalCompositeOperation = 'lighter';
      ctx.beginPath(); ctx.arc(gs.x, gs.y, ringR + 7, -Math.PI / 2, -Math.PI / 2 + sweep);
      ctx.strokeStyle = field.rgba(GATE_SWEEP, 0.42 + fireT * 0.3); ctx.lineWidth = 2; ctx.stroke();
      ctx.globalCompositeOperation = 'source-over';

      ctx.textAlign = 'center';
      ctx.font = "700 10px ui-monospace, Menlo, monospace";
      ctx.fillStyle = field.rgba(GATE_CORE, 0.9);
      const below = (i % 2 === 0);
      const ly = below ? gs.y + ringR + 20 : gs.y - ringR - 12;
      ctx.fillText(`g${g.n} · ${g.label}`, gs.x, ly);
      ctx.font = "400 9px ui-monospace, Menlo, monospace";
      ctx.fillStyle = field.rgba(GATE_TICK, 0.85);
      ctx.fillText(String(g.count), gs.x, ly + (below ? 12 : -12));
    });
  }

  let lastT = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - lastT) / 1000);
    lastT = now;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(staticLayer, 0, 0);
    const dpr = canvas.width / W;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    field.drawField(ctx, now, dt);
    drawGates(now, now / 1000);
    drawComets(now, dt);

    requestAnimationFrame(frame);
  }

  /* ===== public API ===== */
  let booted = false;
  function boot(data) {
    fitCanvas();
    field.buildLayout(data);
    field.buildEdges(data);
    field.renderStaticLayer(staticCtx);
    if (!booted) {
      booted = true;
      requestAnimationFrame((t) => { lastT = t; requestAnimationFrame(frame); });
    }
  }
  // Lightweight per-poll refresh (no relayout/re-edges/K-NN rescan) — updates
  // per-node intensity/state and re-blits the static layer from the SAME
  // cached positions/edges. Cheap enough for the 1s poll cadence.
  function refresh(nodes) {
    if (!booted) return;
    field.refreshNodeState(nodes);
    field.renderStaticLayer(staticCtx);
  }
  function fireGateEvent(g) {
    if (!g || !g.gate_id) return;
    const gid = 'g' + g.gate_id;
    const srcNode = g.symbol && field.findNode((n) => n.ticker && g.symbol.indexOf(n.ticker) >= 0 && (n.cluster === 'mkt' || n.cluster === 'watch'));
    if (srcNode) {
      // Jin 2026-07-10: the ticker ITSELF progresses rightward — a mkt dot
      // with a real g1..g5 event parks beside that gate (venue-colored glow)
      // and re-parks further right on each later gate; idle -> glides home.
      if (srcNode.cluster === 'mkt' && g.gate_id >= 1 && g.gate_id <= 5) {
        field.migrateTicker(srcNode.id, g.gate_id - 1);
      }
      const es = field.pathEdges([srcNode.id, gid]);
      if (es.length) { spawnComet(es, field.clusterColor()[srcNode.cluster] || GATE_HALO, 1.8); return; }
    }
    field.markFire(gid, 700);
  }
  function fireEntry(e) {
    // strat->g2 is the real cached edge; g2..g5 ride the gate backbone
    // (both built forward in buildEdges) — a fill/open is the sizer's (G5)
    // output, so the comet rides the whole signal->sized chain to get there.
    const strat = field.nodeById('strat_' + e.strategy_id);
    const path = strat ? field.pathEdges([strat.id, 'g2', 'g3', 'g4', 'g5']) : [];
    if (path.length) spawnComet(path, field.clusterColor().strat || '#8fd7ff', 2.0);
    else field.markFire('g5', 700);
    // The filled ticker's journey continues as a pos-cluster node — send its
    // parked mkt dot home instead of leaving it camped at the sizer.
    const mkt = e.ticker && field.findNode((n) => n.cluster === 'mkt' && n.ticker === e.ticker && n.exchange === e.exchange);
    if (mkt) field.migrateHome(mkt.id);
  }
  function fireExit(e) {
    // node.exchange and the SSE payload's e.exchange are both the same
    // 3-letter lowercase venue code (server.py's `_short_venue`/`[:3].lower()`)
    // — match both, not ticker alone, so a ticker open on two venues at once
    // doesn't visually point at the wrong position.
    const pos = field.findNode((n) => n.cluster === 'pos' && n.ticker === e.ticker && n.exchange === e.exchange);
    const tally = field.findNode((n) => n.cluster === 'exit_tally');
    const ids = [pos ? pos.id : null, 'g6', 'g7', tally ? tally.id : null].filter(Boolean);
    const path = field.pathEdges(ids);
    const win = (e.pnl_usd || 0) >= 0;
    if (path.length) spawnComet(path, win ? (field.clusterColor().pos || '#87ffaf') : (field.clusterColor().exit || '#ff5f7a'), 2.4);
    else field.markFire('g7', 900);
  }
  function fireKill(k) { field.markFire('g' + k.gate_id, 600); }
  function fireVerdict(v) { field.markFire('g' + v.gate_id, 900); }
  function firstGateX() { const gs = field.gateScreen(); return gs.length ? gs[0].x : 0; }

  let resizeTimer = null;
  let lastData = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (lastData) boot(lastData); }, 150);
  });

  window.PolarisSpine = {
    boot: (data) => { lastData = data; boot(data); },
    refresh, setGateCounts,
    fireGateEvent, fireEntry, fireExit, fireKill, fireVerdict,
    firstGateX,
  };
})();
