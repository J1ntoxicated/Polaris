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
  let chainStreams = [];      // [{key, nodes:[node...], color, speed, parts:[t...], pnl}]
  const ribbons = [];         // transient entry/exit ribbons (one-shot flares)
  const satLasers = [];       // transient satellite → conductor laser beams (one-shot)
  let _satTick = 0;           // wall-clock accumulator for satellite laser stagger

  function lerp(a, b, t) { return a + (b - a) * t; }

  // ── Build chain streams from backend graph payload ──────────────────────────
  // Reload-glitch fix (Jin, repeated): the 2s loadGraph used to discard every
  // stream and rebuild parts from scratch ([0,1/n,2/n…]) → particles snapped back
  // to the chain start on every refresh. We now key each stream by a STABLE id
  // (ticker + first/last node id) and carry the previous parts[] (particle phase)
  // over when the same stream reappears, so the flow stays continuous across
  // reloads. New streams get fresh phases; vanished ones simply drop.
  function streamKey(ticker, seq) {
    return (ticker || '~') + '|' + seq[0].id + '|' + seq[seq.length - 1].id;
  }
  function rebuildStreams(d) {
    const nodeByIndex = window.PolarisGlobe_nodeByIndex;
    const chains = d.trade_chains || [];
    // index existing streams by stable key so we can carry particle phase over.
    const prev = new Map();
    for (const s of chainStreams) if (s.key) prev.set(s.key, s);
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
      const key = streamKey(c.ticker, seq);
      const old = prev.get(key);
      let parts;
      if (old && old.parts) {
        // carry over particle phase; resize to the new particle count if needed.
        parts = old.parts.slice(0, nParts);
        for (let i = parts.length; i < nParts; i++) parts.push(i / nParts);
      } else {
        parts = [];
        for (let i = 0; i < nParts; i++) parts.push(i / nParts);
      }
      out.push({ key, nodes: seq, color: col, speed: 0.25 + strength * 0.4, parts, pnl, ticker: c.ticker });
    }
    chainStreams = out;
  }


  // ── Frame draw — called by globe-core before nodes are drawn ────────────────
  // faint grey neural pulse — Jin: "뭔가 지나가는 정도"만 (barely visible). Pale
  // grey core + very soft glow; trade-chain particles (§3) use this.
  // Jin: 입자를 아주 작게 → 점이 아니라 "라인이 글로잉하며 신호가 지나가는" 느낌.
  // 라인과 동일 회색 톤, 작은 코어 + 부드러운 글로우.
  function drawNeuralPulse(ctx, rgba, x, y, a) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, 1.5);
    g.addColorStop(0, `rgba(184,190,202,${0.16 * a})`);
    g.addColorStop(1, 'rgba(184,190,202,0)');
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(x, y, 1.5, 0, 6.2832); ctx.fill();
    ctx.fillStyle = `rgba(198,204,214,${Math.min(1, 0.30 * a)})`;
    ctx.beginPath(); ctx.arc(x, y, 0.5, 0, 6.2832); ctx.fill();
  }

  function drawFlows(ctx, project, now, dt, helpers) {
    const rgba = helpers.rgba;
    const conductor = window.PolarisGlobe_conductor;
    const gs = window.PolarisGlobe_galaxyState;
    const allNodes = window.PolarisGlobe_nodes;

    // Jin: conductor + its synapse lines removed (거래만 라인 신경망). cp = the (now
    // invisible) cloud centre at origin — satellites still revolve around it and fire
    // their event lasers toward it.
    const cp = project(conductor.x, conductor.y, conductor.z);

    // 1b) satellite EVENT lasers — Jin E6: 위성은 평소 선 없음. state==='firing' 위성이
    //     주기적으로(node phase로 stagger) conductor 로 빠른 레이저 빔을 한 발 쏜다
    //     ("해당 사항 발생 → 레이저"). spawn here, advance/expire below.
    if (allNodes && allNodes.length) {
      _satTick += dt;
      for (let i = 0; i < allNodes.length; i++) {
        const n = allNodes[i];
        if (!n.sat || !n._screen || n.state !== 'firing') continue;
        // fire interval ~2.6-3.8s, staggered per node so beams don't bunch up.
        const period = 2.6 + (n.phase || 0) * 1.2;
        if (n._nextLaser == null) n._nextLaser = _satTick + (n.phase || 0) * period;
        if (_satTick >= n._nextLaser) {
          n._nextLaser = _satTick + period;
          satLasers.push({ node: n, color: n.color || [0xb8, 0xbc, 0xc6], t: 0, dur: 0.42 });
        }
      }
    }
    // advance + draw lasers (satellite → conductor sweep, fade out)
    for (let i = satLasers.length - 1; i >= 0; i--) {
      const lz = satLasers[i];
      lz.t += dt / lz.dur;
      if (lz.t >= 1 || !lz.node._screen) { satLasers.splice(i, 1); continue; }
      const sp = lz.node._screen;
      const t = lz.t;
      // head sweeps from satellite toward the conductor; short trailing tail.
      const hx = lerp(sp.sx, cp.sx, t), hy = lerp(sp.sy, cp.sy, t);
      const tt = Math.max(0, t - 0.32);
      const tx = lerp(sp.sx, cp.sx, tt), ty = lerp(sp.sy, cp.sy, tt);
      const a = (1 - t) * 0.7;
      ctx.strokeStyle = rgba([0x90, 0x96, 0xa2], a);   // Jin: 모든 라인 회색 통일(레이저도)
      ctx.lineWidth = 1.0;
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(hx, hy); ctx.stroke();
      // small glow at the head
      const gr = ctx.createRadialGradient(hx, hy, 0, hx, hy, 4);
      gr.addColorStop(0, rgba([0x90, 0x96, 0xa2], a));
      gr.addColorStop(1, rgba([0x90, 0x96, 0xa2], 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(hx, hy, 4, 0, 6.2832); ctx.fill();
    }

    // Jin: capital-lifecycle inter-galaxy arcs removed (거래만 라인; 구조선 거슬림).
    // The crypto→CFD→Alpaca capital flow gets its own dedicated viz later (#15).

    // 3) trade-chain particle flows — small WHITE neural pulses on a crisp,
    //    faintly pnl-tinted pathway (Jin: 작은 하얀 신경 입자 + 또렷한 연결).
    for (const s of chainStreams) {
      const pts = s.nodes.map((n) => project(n.x, n.y, n.z));
      // pathway line: gray, barely visible — just enough to read "something passing"
      // (Jin E6.1: 신경망 연결선도 회색 거의 안보이게; 색은 위성 노드로 구분).
      ctx.strokeStyle = rgba([0x90, 0x96, 0xa2], 0.11);   // Jin: 통일 회색, 가는 글로잉 라인
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.moveTo(pts[0].sx, pts[0].sy);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].sx, pts[i].sy);
      ctx.stroke();
      // particles riding the chain (segmented along the polyline) — white pulses
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
        drawNeuralPulse(ctx, rgba, x, y, 0.95);
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
      // Jin E6.1: do NOT rebuild lifecycle arcs every reload — they sit between
      // fixed galaxy centres, and rebuilding reset their Math.random() phase + parts
      // each 2s → the arc "line" jumped (the reload glitch Jin kept seeing). Build
      // once lazily (drawFlows §2 `if (!lifecycleArcs.length)`); never on refresh.
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
