/* Polaris Neural Cloud — E4 galaxy globe (live flows + data + SSE)
 *
 * Layered on globe-core.js. Owns everything that makes the globe feel alive
 * rather than a static blob (Jin E4 complaint):
 *
 *   - trade_chains → particle flows drawn between projected
 *     nodes (source-strategy → regime → watch/mkt → position), riding live.
 *   - conductor ↔ galaxy synapse threads (the AI hub "managing" the 3 galaxies).
 *   - capital-lifecycle arcs: crypto factory → CFD amplifier → Alpaca vault
 *     (the north-star inter-galaxy flow).
 *   - node pulse on activity, entry/exit FLASH from the live SSE fill stream.
 *   - frequent graph refresh (2s) + 1s lifecycle re-pull so activity shows fast.
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  if (!document.getElementById('sphere')) return;

  const venueKey = window.PolarisGlobe_venueKey;
  const GALAXIES = window.PolarisGlobe_galaxies;
  const setGraph = window.PolarisGlobe_setGraph;

  // Live particle streams along trade chains. Each is a polyline through node
  // positions; particles travel 0→1 along it, repeating while the trade is live.
  let chainStreams = [];      // [{nodes:[node...], color, speed, parts:[t...], pnl}]
  let lifecycleArcs = [];     // capital-lifecycle inter-galaxy arcs (north-star)
  const ribbons = [];         // transient entry/exit ribbons (one-shot flares)

  function lerp(a, b, t) { return a + (b - a) * t; }

  // ── Build chain streams from backend graph payload ──────────────────────────
  function rebuildStreams(d) {
    const nodeByIndex = window.PolarisGlobe_nodeByIndex;
    const chains = d.trade_chains || [];
    const out = [];
    for (const c of chains) {
      const seq = [];
      for (const idx of c.chain || []) {
        const n = nodeByIndex[idx];
        if (n) seq.push(n);
      }
      if (seq.length < 2) continue;
      const pnl = c.pnl_usd || 0;
      const col = pnl > 0.0001 ? [0x87, 0xff, 0xaf] : (pnl < -0.0001 ? [0xff, 0x87, 0x87] : [0xff, 0xff, 0xff]);
      const strength = c.strength != null ? c.strength : 0.6;
      const nParts = 2 + Math.round(strength * 3);
      const parts = [];
      for (let i = 0; i < nParts; i++) parts.push(i / nParts);
      out.push({ nodes: seq, color: col, speed: 0.25 + strength * 0.4, parts, pnl, ticker: c.ticker });
    }
    chainStreams = out;
  }

  // ── Capital-lifecycle arcs: crypto → CFD → Alpaca (north-star flow) ─────────
  // One persistent arc per leg between galaxy centres; intensity tracks how much
  // live activity each source galaxy has (open positions in chains).
  function rebuildLifecycleArcs() {
    const gs = window.PolarisGlobe_galaxyState;
    const legs = [['okx', 'capital'], ['capital', 'alpaca']];
    lifecycleArcs = legs.map(([a, b]) => ({ a: gs[a], b: gs[b], phase: Math.random(), parts: [0, 0.5] }));
  }

  // ── Frame draw — called by globe-core before nodes are drawn ────────────────
  function drawFlows(ctx, project, now, dt, helpers) {
    const rgba = helpers.rgba;
    const conductor = window.PolarisGlobe_conductor;
    const gs = window.PolarisGlobe_galaxyState;

    // 1) conductor ↔ galaxy synapse threads (faint, always present)
    const cp = project(conductor.x, conductor.y, conductor.z);
    for (const k of ['okx', 'capital', 'alpaca']) {
      const g = gs[k]; if (!g || !g._screen) continue;
      const beat = 0.12 + g.pulse * 0.5;
      ctx.strokeStyle = rgba(g.theme, beat);
      ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(cp.sx, cp.sy); ctx.lineTo(g._screen.sx, g._screen.sy); ctx.stroke();
    }

    // 2) capital-lifecycle arcs (crypto → CFD → Alpaca), travelling particles
    if (!lifecycleArcs.length) rebuildLifecycleArcs();
    for (const arc of lifecycleArcs) {
      const a = project(arc.a.cx, arc.a.cy, arc.a.cz);
      const b = project(arc.b.cx, arc.b.cy, arc.b.cz);
      const midx = (a.sx + b.sx) / 2, midy = (a.sy + b.sy) / 2 - Math.hypot(b.sx - a.sx, b.sy - a.sy) * 0.28;
      // dotted guide arc (the north-star pathway)
      ctx.strokeStyle = rgba([0xff, 0xf5, 0xd2], 0.10);
      ctx.lineWidth = 0.8;
      ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.quadraticCurveTo(midx, midy, b.sx, b.sy); ctx.stroke();
      // travelling capital particles
      for (let i = 0; i < arc.parts.length; i++) {
        arc.parts[i] += dt * 0.12;
        if (arc.parts[i] > 1) arc.parts[i] -= 1;
        const t = arc.parts[i];
        const x = (1 - t) * (1 - t) * a.sx + 2 * (1 - t) * t * midx + t * t * b.sx;
        const y = (1 - t) * (1 - t) * a.sy + 2 * (1 - t) * t * midy + t * t * b.sy;
        ctx.fillStyle = rgba([0xff, 0xf5, 0xd2], 0.7);
        ctx.beginPath(); ctx.arc(x, y, 1.8, 0, 6.2832); ctx.fill();
      }
    }

    // 3) trade-chain particle flows (the live pathways Jin missed)
    for (const s of chainStreams) {
      const pts = s.nodes.map((n) => project(n.x, n.y, n.z));
      // faint pathway line through the chain
      ctx.strokeStyle = rgba(s.color, 0.16);
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(pts[0].sx, pts[0].sy);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].sx, pts[i].sy);
      ctx.stroke();
      // particles riding the chain (segmented along the polyline)
      const segCount = pts.length - 1;
      for (let pi = 0; pi < s.parts.length; pi++) {
        s.parts[pi] += dt * s.speed;
        if (s.parts[pi] > 1) {
          s.parts[pi] -= 1;
          // each completed lap pulses the terminal position node (heartbeat)
          const term = s.nodes[s.nodes.length - 1];
          if (term) term.pulse = Math.min(1, term.pulse + 0.5);
        }
        const t = s.parts[pi];
        const seg = Math.min(segCount - 1, Math.floor(t * segCount));
        const local = t * segCount - seg;
        const p0 = pts[seg], p1 = pts[seg + 1];
        const x = lerp(p0.sx, p1.sx, local), y = lerp(p0.sy, p1.sy, local);
        const g2 = ctx.createRadialGradient(x, y, 0, x, y, 5);
        g2.addColorStop(0, rgba(s.color, 0.9));
        g2.addColorStop(1, rgba(s.color, 0));
        ctx.fillStyle = g2;
        ctx.beginPath(); ctx.arc(x, y, 5, 0, 6.2832); ctx.fill();
        ctx.fillStyle = rgba(s.color, 0.95);
        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, 6.2832); ctx.fill();
      }
    }

    // 4) one-shot entry/exit ribbons (conductor → galaxy flare on a new fill)
    for (let i = ribbons.length - 1; i >= 0; i--) {
      const rb = ribbons[i];
      rb.t += dt / rb.dur;
      if (rb.t >= 1) { ribbons.splice(i, 1); continue; }
      const g = gs[rb.gx];
      if (!g || !g._screen) continue;
      const t = rb.t;
      const x = lerp(cp.sx, g._screen.sx, t), y = lerp(cp.sy, g._screen.sy, t);
      const a = (1 - t) * 0.9;
      const gr = ctx.createRadialGradient(x, y, 0, x, y, 9);
      gr.addColorStop(0, rgba(rb.color, a));
      gr.addColorStop(1, rgba(rb.color, 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(x, y, 9, 0, 6.2832); ctx.fill();
    }
  }
  window.PolarisGlobe_drawFlows = drawFlows;

  // ── Live activity hooks ──────────────────────────────────────────────────────
  function pulseGalaxy(gx, strength) {
    const gs = window.PolarisGlobe_galaxyState;
    if (gs[gx]) { gs[gx].pulse = Math.min(1, gs[gx].pulse + strength); gs[gx].hot = Math.min(1, gs[gx].hot + strength * 0.6); }
    window.PolarisGlobe_conductor.pulse = Math.min(1, window.PolarisGlobe_conductor.pulse + strength * 0.5);
  }
  function flashTicker(ticker, gx, color) {
    const nodes = window.PolarisGlobe_nodes;
    for (const n of nodes) {
      if (n.ticker === ticker) { n.flash = 1; n.pulse = 1; }
    }
    if (gx) ribbons.push({ gx, color: color || [0x87, 0xff, 0xaf], t: 0, dur: 1.2 });
  }

  // ── Data refresh — frequent for live feel (Jin: "리프레시가 드뭄") ───────────
  let _inflight = false;
  async function loadGraph() {
    if (_inflight) return;
    _inflight = true;
    try {
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch('/static/graph.json?t=' + Date.now(), { signal: ctrl.signal });
      clearTimeout(tid);
      if (!r.ok) throw new Error('graph ' + r.status);
      const d = await r.json();
      setGraph(d);
      rebuildStreams(d);
      rebuildLifecycleArcs();
      updateTicker(d);
    } catch (e) {
      // display-only — never break the render loop
    } finally {
      _inflight = false;
    }
  }

  function updateTicker(d) {
    const el = document.getElementById('ticker');
    if (!el) return;
    const st = d.stats || {};
    const gs = window.PolarisGlobe_galaxyState;
    const parts = ['okx', 'capital', 'alpaca'].map((k) => {
      const g = gs[k]; const lab = (GALAXIES[k].label.split('·')[0] || k).trim();
      return `${lab} ${g.count}`;
    });
    el.textContent = `OPEN ${st.open_count || 0} · NODES ${st.node_count || 0} · ${parts.join(' / ')}`;
  }

  // ── SSE live fill stream → entry/exit flashes ───────────────────────────────
  function connectStream() {
    let es;
    try { es = new EventSource('/stream/events'); }
    catch (e) { return; }
    es.onmessage = (ev) => {
      let payload;
      try { payload = JSON.parse(ev.data); } catch (e) { return; }
      const events = payload.events || [];
      for (const e of events) {
        const gx = venueKey(e.exchange);
        if (e.type === 'entry') {
          flashTicker(e.ticker, gx, [0x87, 0xff, 0xaf]);
          if (gx) pulseGalaxy(gx, 0.8);
          setTickerLine(`OPEN ${e.ticker} ${(e.direction || '').toUpperCase()} · ${(e.exchange || '').toUpperCase()}`);
          scheduleReload(1500);
        } else if (e.type === 'exit') {
          const pnl = parseFloat(e.pnl_usd) || 0;
          flashTicker(e.ticker, gx, pnl >= 0 ? [0x87, 0xff, 0xaf] : [0xff, 0x87, 0x87]);
          if (gx) pulseGalaxy(gx, 0.9);
          setTickerLine(`CLOSE ${e.ticker} ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} · ${(e.exchange || '').toUpperCase()}`);
          scheduleReload(1500);
        }
      }
    };
    es.onerror = () => { /* EventSource auto-reconnects */ };
  }

  let _pending = null;
  function scheduleReload(ms) {
    if (_pending) clearTimeout(_pending);
    _pending = setTimeout(() => { _pending = null; loadGraph(); }, ms);
  }
  function setTickerLine(txt) {
    const el = document.getElementById('ticker');
    if (el) el.textContent = txt;
  }

  // ── Boot ─────────────────────────────────────────────────────────────────────
  loadGraph();
  setInterval(loadGraph, 2000);          // Jin E4: 2s graph refresh (was 5s — felt static)
  connectStream();
})();
