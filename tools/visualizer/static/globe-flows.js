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
 *   - frequent graph refresh (1s, board-synced) + 1s lifecycle re-pull so activity shows fast.
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

  // ── PERSISTENT NEURAL LINKS — the PIPELINE wiring (Jin 2026-06-23 structure) ──
  // Jin: "프로브·시그널·활동에 맞게 구성 … 지금 중구난방." The old wiring was largely
  // GEOMETRIC (nearest-K satellite mesh) so it read as chaos. It is replaced by the
  // ACTUAL data-flow order — L0 probe → signal → G-gate lane → open position — so a
  // trade's path on the globe IS the pipeline, not random proximity:
  //   • PROBE → SIGNAL      : an active watch/mkt node (the universe being probed) ↔
  //                            its strategy satellite (by strategy_id, else nearest).
  //   • SIGNAL → GATE       : strat satellite ↔ the G-gate lane anchor (G1 entry).
  //   • GATE → POSITION     : the last gate anchor (G8) ↔ the core trade heart.
  //   • TICKER ↔ its TRADE  : active lit watch/mkt ↔ same-ticker pos (the holding).
  // Drawn EVERY frame (permanent circuit) with traveling neurotransmitter pulses.
  // Budgeted + capped (no N²) so it stays 60fps. Gate anchors come from the funnel
  // lane (globe-funnel.js); if absent the gate hops are simply skipped (graceful).
  let edges = [];             // [{a:node, b:node, phase, speed, baseA, col}]
  let _edgeSig = '';
  const MAX_EDGES = 240;      // hard cap → stays 60fps regardless of universe size

  // A gate anchor is a {x,y,z} home on the funnel lane (globe-funnel exposes it).
  // We wrap it in a lightweight pseudo-node carrying a live _screen so the edge
  // drawer (which reads a._screen) can use it without touching the node array.
  const _gateProxies = new Map();   // gate_id → {gate:true, gid, _screen}
  function gateProxy(gid) {
    let g = _gateProxies.get(gid);
    if (!g) { g = { gate: true, gid }; _gateProxies.set(gid, g); }
    return g;
  }
  // Refresh every gate proxy's projected screen pos each frame from its lane anchor.
  function projectGateProxies(project) {
    const anch = window.PolarisGlobe_gateAnchors;
    const conductor = window.PolarisGlobe_conductor || { x: 0, y: 0, z: 0 };
    if (!anch) return;
    for (const [gid, g] of _gateProxies) {
      const a = anch[gid];
      g._screen = a ? project(conductor.x + a.x, conductor.y + a.y, conductor.z + a.z) : null;
    }
  }

  function buildEdges() {
    const all = window.PolarisGlobe_nodes || [];
    const out = [];
    const stratSats = [];     // strat-family satellites (the signal layer)
    const gateSats = [];      // gate/layer satellites (the internal gate families)
    const posList = [];       // open-position nodes (the trade heart)
    const tickerNodes = new Map();   // ticker → outer node (probe/ticker in the cloud)
    for (const n of all) {
      if (n.sat) {
        if (n.fam === 'strat') stratSats.push(n);
        else gateSats.push(n);                 // all non-strat satellites = gate families
        continue;
      }
      if (n.role === 'pos') { if (n.ticker) posList.push(n); continue; }
      // outer probe/ticker node — keep the first per ticker as the link endpoint.
      if ((n.role === 'watch' || n.role === 'mkt') && n.ticker && !tickerNodes.has(n.ticker)) {
        tickerNodes.set(n.ticker, n);
      }
    }
    // ordered gate ids present on the lane (G1..G8) → entry = min, exit = max.
    const anch = window.PolarisGlobe_gateAnchors || {};
    const gateIds = Object.keys(anch).map(Number).filter((x) => !isNaN(x)).sort((x, y) => x - y);
    const gateEntry = gateIds.length ? gateProxy(gateIds[0]) : null;
    const gateExit = gateIds.length ? gateProxy(gateIds[gateIds.length - 1]) : null;
    const push = (a, b, col) => {
      if (!a || !b || a === b || out.length >= MAX_EDGES) return;
      out.push({ a, b, phase: Math.random(), speed: 0.16 + Math.random() * 0.12, baseA: 0.10, col });
    };
    const stratById = new Map();
    for (const s of stratSats) { if (s.ticker) stratById.set(s.ticker, s); }
    const nearestSat = (sats, ref) => {
      let target = null, md = Infinity;
      for (const s of sats) {
        const dd = (s.hx - ref.hx) ** 2 + (s.hy - ref.hy) ** 2 + (s.hz - ref.hz) ** 2;
        if (dd < md) { md = dd; target = s; }
      }
      return target;
    };
    // ── LINKS ONLY FOR OPEN POSITIONS (Jin '포지션 있는 애들만 선 그어줘, 연결된거
    //    내부 게이트들에') ──────────────────────────────────────────────────────
    // The dormant 699-ticker universe gets NO links (too busy). For each OPEN
    // position we draw ONLY its wiring:
    //   • position ↔ its TICKER probe (the outer-shell node it holds).
    //   • position → its internal GATE satellites (the gates it relates to / passed
    //     through): its strategy's signal satellite, the gate-lane entry/exit
    //     anchors, plus the nearest gate-family satellite as a representative of the
    //     internal gates. No universe-wide proximity/mesh edges.
    for (const p of posList) {
      // position ↔ its ticker probe (outer shell) — the holding link.
      const tk = tickerNodes.get(p.ticker);
      if (tk) push(tk, p, [0x88, 0x9a, 0xc8]);
      // position → its strategy signal satellite (its gate family entry).
      const sig = (p.strategyId && stratById.get(p.strategyId)) || (stratSats.length ? nearestSat(stratSats, p) : null);
      if (sig) push(p, sig, [0x9a, 0xc4, 0xb0]);
      // position → internal gate lane (signal→G1, …, G8→position).
      if (sig && gateEntry) push(sig, gateEntry, [0xff, 0xb0, 0x70]);
      if (gateExit) push(gateExit, p, [0xbf, 0xd4, 0xff]);
      // position → its nearest internal gate-family satellite (a related gate).
      const g = gateSats.length ? nearestSat(gateSats, p) : null;
      if (g) push(p, g, [0xff, 0xb0, 0x70]);
    }
    edges = out;
  }

  // Cheap signature so we only rebuild edges when the node population changes.
  function edgeSignature() {
    const all = window.PolarisGlobe_nodes || [];
    // Edges are POSITION-driven now → the signature keys on the set of open-position
    // tickers (+ satellite count for the gate endpoints). Dormant universe tickers no
    // longer affect the wiring, so they don't churn the edge rebuild.
    let ns = 0;
    const posTk = [];
    for (const n of all) {
      if (n.sat) ns++;
      else if (n.role === 'pos' && n.ticker) posTk.push(n.ticker);
    }
    posTk.sort();
    // include gate-lane presence so edges rebuild once the funnel anchors exist
    // (the pipeline hops appear) and when the gate set changes.
    const anch = window.PolarisGlobe_gateAnchors;
    const ng = anch ? Object.keys(anch).length : 0;
    return posTk.join(',') + '|' + ns + '|' + ng;
  }

  // Draw the persistent links + their traveling neurotransmitter pulses. `live` is
  // a 0..1 global excitement (fresh AI decision flash + venue heat) that brightens
  // the whole circuit + speeds the pulses on real activity.
  function drawNeuralLinks(ctx, project, rgba, dt, live) {
    const sig = edgeSignature();
    if (sig !== _edgeSig) { _edgeSig = sig; buildEdges(); }
    // gate proxies aren't in the node array, so project their lane anchors here so
    // edge endpoints that reference a gate proxy have a fresh _screen this frame.
    projectGateProxies(project);
    const speedBoost = 1 + live * 1.6;
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const a = e.a._screen, b = e.b._screen;
      if (!a || !b) continue;
      // persistent faint wire — always visible, brightens on activity.
      const wa = (e.baseA + live * 0.22);
      ctx.strokeStyle = rgba(e.col, wa);
      ctx.lineWidth = 0.6 + live * 0.5;
      ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
      // neurotransmitter pulse traveling along the link (continuous loop).
      // Jin 2026-06-23: 입자가 너무 바쁘고 산만 → 아주 작게 + 흐릿하게 (보일듯 말듯).
      // radius 2.4→0.9, peak alpha 0.85→0.22 so the dots barely register — the
      // line itself carries the "signal passing" feel, not a busy bead.
      e.phase += dt * e.speed * speedBoost;
      if (e.phase >= 1) e.phase -= 1;
      const t = e.phase;
      const px = lerp(a.sx, b.sx, t), py = lerp(a.sy, b.sy, t);
      const pa = 0.5 + live * 0.5;
      const gr = ctx.createRadialGradient(px, py, 0, px, py, 0.9);
      gr.addColorStop(0, rgba(e.col, Math.min(1, 0.22 * pa)));
      gr.addColorStop(1, rgba(e.col, 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(px, py, 0.9, 0, 6.2832); ctx.fill();
    }
  }
  window.PolarisGlobe_invalidateEdges = function () { _edgeSig = ''; };

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

    // 0) PERSISTENT NEURAL LINKS + neurotransmitter pulses (Jin '상시 연결 라인 +
    //    신경전달물질'). The full related-node wiring is drawn EVERY frame so the
    //    circuit is permanently visible; bright pulses travel along it continuously
    //    (faster/brighter on a fresh AI decision or venue heat). Drawn first so the
    //    trade particles + nodes read on top. Budgeted (nearest-K, capped) = 60fps.
    {
      const flash = window.PolarisGlobe_decisionFlash || 0;
      let heat = 0;
      if (gs) for (const k of ['okx', 'capital', 'alpaca']) {
        const g = gs[k]; if (g) heat = Math.max(heat, (g.pulse || 0) + (g.hot || 0) * 0.5);
      }
      const live = Math.min(1, flash + heat);
      drawNeuralLinks(ctx, project, rgba, dt, live);
    }

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
      // periodic firing-satellite beams stay neutral gray; a REAL exit streak
      // (fireExitStreak) carries its P&L color so the exit→centre pulse reads.
      const lc = lz.color || [0x90, 0x96, 0xa2];
      ctx.strokeStyle = rgba(lc, a);
      ctx.lineWidth = 1.0;
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(hx, hy); ctx.stroke();
      // small glow at the head
      const gr = ctx.createRadialGradient(hx, hy, 0, hx, hy, 4);
      gr.addColorStop(0, rgba(lc, a));
      gr.addColorStop(1, rgba(lc, 0));
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

  // EXIT family → CENTER pulse: on a REAL exit event (SSE exit / kills), the EXIT
  // satellites SHOOT a streak toward the centre (the trade heart) — Jin concentric
  // circuit spec #2. Picks every live exit-family satellite and fires one streak
  // each (color = P&L green/red), advanced + drawn by the satLasers loop in drawFlows.
  function fireExitStreak(pnl) {
    const nodes = window.PolarisGlobe_nodes;
    const col = (pnl >= 0) ? [0x87, 0xff, 0xaf] : [0xff, 0x87, 0x87];
    let fired = false;
    for (const n of nodes) {
      if (n.sat && n.fam === 'exit') {
        satLasers.push({ node: n, color: col, t: 0, dur: 0.5 });
        n.flash = 1; n.pulse = 1;
        fired = true;
      }
    }
    return fired;
  }

  // ── Probe layer (Jin 2026-06-23: 프로브를 클라우드에 표시) ─────────────────────
  // Probes are DATA-DRIVEN from the board's /api/snapshot poll (board.js / mobile.js
  // bridge → showProbes). The cloud reads /static/graph.json for its node graph, so
  // a probe is represented in the cloud's EXISTING visual language: its ticker node
  // PULSES (the probe "pinging" its target) and the owning venue galaxy gets a soft
  // pulse — no new shapes, no new colors (venue colour comes from the existing
  // galaxy theme). GRACEFUL: empty/absent probe array → nothing happens.
  //   Generic probe shape: {name|probe, ticker|symbol, reading|value|state,
  //   decision?, ts?, venue?}. We pulse only on NEW/CHANGED readings (keyed by
  //   name+ticker+reading) so the cloud stays smooth on the 1s board refresh
  //   instead of re-pulsing every poll.
  const _probeSeen = new Map();   // key → last wall-clock seen (for prune)
  function probeKey(p) {
    const nm = p.name || p.probe || p.id || '';
    const tk = p.ticker || p.symbol || '';
    const rd = (p.reading != null) ? p.reading
      : (p.value != null) ? p.value
      : (p.state != null) ? p.state : '';
    return nm + '|' + tk + '|' + rd;
  }
  function showProbes(probes) {
    if (!Array.isArray(probes) || !probes.length) return;
    const now = performance.now();
    for (const p of probes) {
      if (!p) continue;
      const key = probeKey(p);
      const prev = _probeSeen.get(key);
      _probeSeen.set(key, now);
      if (prev != null) continue;   // already pulsed this reading — stay smooth
      const tk = p.ticker || p.symbol || '';
      // venue: explicit field first (venueKey takes a 3-char prefix), else infer
      // okx from a crypto pair symbol (…-USDT/USDC/USD) — a bare symbol's first 3
      // chars are NOT a venue, so never feed the raw symbol to venueKey.
      let gx = p.venue ? venueKey(p.venue) : null;
      if (!gx && /-USD[TC]?$/.test(String(tk).toUpperCase())) gx = 'okx';
      if (tk) flashTicker(tk, null, null);   // pulse the probe's ticker node(s)
      if (gx) pulseGalaxy(gx, 0.45);         // soft venue-galaxy pulse
    }
    // prune keys not seen for a while so a re-fired reading can pulse again.
    for (const [k, t] of _probeSeen) { if (now - t > 30000) _probeSeen.delete(k); }
  }
  // Public hook — board.js (desktop) + mobile.js bridge their snapshot probe data
  // here each poll. Lives on the shared PolarisGlobe object (display-only).
  window.PolarisGlobe = window.PolarisGlobe || {};
  window.PolarisGlobe.showProbes = showProbes;

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
      // Gate-decision channel (separate payload key) → push into a capped ring
      // buffer the board's Live Gate Activity feed merges in between 1s polls,
      // for the smooth-realtime, no-lag feel. Display-only; never touches trading.
      const gevs = payload.gate_events || [];
      if (gevs.length) {
        const buf = (window.__gateStream || []);
        window.__gateStream = buf.concat(gevs).slice(-200);   // newest 200 kept
        try {
          if (window.PolarisBoardTabs && window.PolarisBoardTabs.renderGateFeedLive) {
            window.PolarisBoardTabs.renderGateFeedLive();
          }
        } catch (e) { /* feed optional */ }
      }
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
          fireExitStreak(pnl);                 // EXIT satellites → centre pulse
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
  setInterval(loadGraph, 1000);          // Jin: 1s graph refresh — sync to the 1s board (smooth realtime, no 2s slideshow)
  connectStream();
})();
