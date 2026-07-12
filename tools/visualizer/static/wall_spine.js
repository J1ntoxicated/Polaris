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
 * wall_spine_field.js, loaded first; the Jarvis decoration layer (strategy
 * score_F reticle, watch bracket chip, engineering graticule — feat/jarvis-
 * language, Jin 2026-07-10) is wall_spine_deco.js, loaded second (optional —
 * this file degrades gracefully if it's missing).
 *
 * Camera is fully static (no zoom/pan/shake). No full-screen or radial-burst
 * effects — every effect is anchored to a node/edge. Additive ('lighter')
 * blending only for glow passes. Single rAF loop, English UI copy only.
 * Display-only: nothing here issues, sizes, gates or throttles a trade.
 *
 * console v2 (Jin 2026-07-11 "제대로 하자"): the corner/BAY/ladder readout
 * panels (wall_console_readouts.js, loaded third, optional) get their data
 * through the setConsole/setVerdicts/setFlowSummary/markGatePulse/
 * gatePulseAt surface below — this file owns that small state, readouts.js
 * pulls it back out lazily each frame. Scope note on `breath()` (field.js's
 * new phase-dispersion helper): it is used ONLY by the new readout elements
 * that have no `screen[].phaseOff` of their own. The EXISTING glow call
 * sites in this file/field.js/deco.js (glowAt, strategy/system firing glow,
 * the strategy score_F reticle) already disperse phase via each node's own
 * `screen[].phaseOff` — rewriting dozens of already-working, already-
 * dispersed call sites to route through `breath()` instead would be a pure
 * refactor with the same visual result and real regression risk, so it was
 * left alone (surgical changes).
 */
(function () {
  const canvas = document.getElementById('spine-canvas');
  const field = window.PolarisSpineField;
  if (!canvas || !field) return;
  const deco = window.PolarisSpineDeco; // optional — Jarvis decoration layer
  const readouts = window.PolarisConsoleReadouts; // optional — console v2 panels
  const ctx = canvas.getContext('2d');

  // ===== console v2 state (Jin 2026-07-11 "제대로 하자") =====
  // Owned here (not readouts.js) so wall_spine_hud.js has one place to push
  // graph.json's `console` block + flow_stats' `verdicts_recent`/`summary`
  // into, same pattern as setGateCounts/setStrategyGauges below. readouts.js
  // pulls it back out lazily (via window.PolarisSpine, safe — draw() is only
  // ever called from frame(), long after every script tag has finished
  // executing and this object below exists).
  let consoleData = null;
  let verdictsRecent = [];
  let flowSummary = {};
  const gatePulse = {}; // gate_id (1-8) -> performance.now() of last SSE pulse
  function setConsole(obj) { consoleData = obj || null; }
  function setVerdicts(list) { verdictsRecent = list || []; }
  function setFlowSummary(s) { flowSummary = s || {}; }
  function markGatePulse(gateId) { gatePulse[gateId] = performance.now(); }
  function gatePulseAt(gateId) { return gatePulse[gateId] || 0; }

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
    // dpr cap 2.0->1.5 (2026-07-12 랙 포렌식: 페인트-바운드 — 레티나 풀해상도
    // 래스터가 프레임 비용의 주범군. 1.5는 텍스트 가독 유지 + 픽셀 -44%)
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = rect.width; H = rect.height;
    canvas.width = staticLayer.width = Math.max(1, Math.round(W * dpr));
    canvas.height = staticLayer.height = Math.max(1, Math.round(H * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    staticCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    field.setSize(W, H);
  }

  /* ===== gate decision motes + kill sediment (real events only) =====
   * Jin 2026-07-10 "게이트가 너무 비어있다": each gate keeps its last ~14
   * REAL decisions orbiting it (color = verdict family — gate hue for
   * pass-through, amber for modify/adjust, steel for hold; NO green/red,
   * those are money-only per the color contract). G5 KILLs fall away as dim
   * sediment below the sizer (measurement honesty — globe kill-mote
   * precedent, not a block). */
  const gateMotes = [[], [], [], [], [], [], [], []];
  // Evaluation flash (Jin 2026-07-11 effect-builder round, item 2): one-shot
  // micro notch on a gate's hairline ring the instant its verdict mote
  // lands — same mote object (ang/color), no second random draw. Piggybacks
  // pushGateMote below; drawn in wall_spine.js's drawGates (own ring geometry).
  const gateNotch = [null, null, null, null, null, null, null, null];
  // G1 universe structure (Jin 2026-07-10 "유니버스도 표시"): live tier
  // census from the real roster (S/A/B/T focus tiers), redrawn per poll.
  let g1Tiers = [];
  function setUniverseTiers(nodes) {
    const c = {};
    (nodes || []).forEach((n) => {
      if (n.cluster !== 'mkt' || !n.tier_label) return;
      c[n.tier_label] = (c[n.tier_label] || 0) + 1;
    });
    g1Tiers = ['S', 'A', 'B', 'T'].filter((k) => c[k]).map((k) => ({ k, n: c[k] }));
  }
  // G2 recent-signal tags ("뭐가 잡혔나") — real gate_id=2 events only.
  const g2Tags = [];
  function pushSignalTag(g, exchange) {
    if (!g || g.gate_id !== 2 || !g.symbol) return;
    const sym = String(g.symbol).split(':').pop().split('-')[0].split('_')[0];
    g2Tags.unshift({ sym, venue: exchange || '', born: performance.now() });
    if (g2Tags.length > 5) g2Tags.pop();
  }
  const sediment = [];
  function verdictFamilyColor(decision, gateIdx) {
    const d = String(decision || '').toUpperCase();
    if (/KILL|REJECT|EXIT_NOW|STOP/.test(d)) return null; // -> sediment
    if (/MODIFY|ADJUST|WIDEN|SWAP|REFINE/.test(d)) return GATE_TICK;
    if (/HOLD/.test(d)) return '#7a8496';
    return GATE_COLORS[gateIdx] || GATE_HALO;
  }
  function pushGateMote(gateIdx, decision) {
    if (gateIdx < 0 || gateIdx > 7) return;
    const col = verdictFamilyColor(decision, gateIdx);
    if (col == null) {
      const gs = field.gateScreen()[gateIdx];
      if (gs && sediment.length < 60) sediment.push({ x: gs.x + (Math.random() - 0.5) * 26, y: gs.y + 18, vy: 6 + Math.random() * 8, born: performance.now() });
      return;
    }
    const ring = gateMotes[gateIdx];
    const mote = { ang: Math.random() * Math.PI * 2, drift: (Math.random() < 0.5 ? -1 : 1) * (0.05 + Math.random() * 0.1), color: col, born: performance.now() };
    ring.push(mote);
    if (ring.length > 14) ring.shift();
    gateNotch[gateIdx] = mote;
  }
  const NOTCH_MS = 260;
  const MOTE_TTL = 150000;
  function drawGateMotes(now, dt) {
    ctx.globalCompositeOperation = 'lighter';
    const gs = field.gateScreen();
    gateMotes.forEach((ring, i) => {
      const g = gs[i];
      if (!g) return;
      for (let k = ring.length - 1; k >= 0; k--) {
        const m = ring[k];
        const age = now - m.born;
        if (age > MOTE_TTL) { ring.splice(k, 1); continue; }
        m.ang += m.drift * dt;
        const fade = 1 - age / MOTE_TTL;
        const r = 52 + (k % 3) * 7;
        field.drawDot(ctx, g.x + Math.cos(m.ang) * r, g.y + Math.sin(m.ang) * r * 0.72, 1.7, m.color, 0.55 * fade, 3);
      }
    });
    ctx.globalCompositeOperation = 'source-over';
    // G1 tier satellites — the universe's real output structure
    const g1 = gs[0];
    if (g1 && g1Tiers.length) {
      ctx.font = '600 8px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      g1Tiers.forEach((t, j) => {
        const ang = Math.PI * 0.62 + j * 0.42;
        const x = g1.x + Math.cos(ang) * 78, y = g1.y + Math.sin(ang) * 60;
        field.drawDot(ctx, x, y, 2.4, GATE_COLORS[0], 0.5, 0);
        ctx.fillStyle = field.rgba('#8a94b0', 0.8);
        ctx.fillText(t.k + ' ' + t.n, x, y + 12);
      });
    }
    // G2 recent-signal tags — what the signal net just caught (fade 60s)
    const g2 = gs[1];
    if (g2) {
      ctx.font = '600 8.5px JetBrains Mono, monospace';
      ctx.textAlign = 'left';
      for (let k = g2Tags.length - 1; k >= 0; k--) {
        const tg = g2Tags[k];
        const age = now - tg.born;
        if (age > 60000) { g2Tags.splice(k, 1); continue; }
        const vc = (field.venueColorOf && field.venueColorOf(tg.venue)) || '#8fb0c8';
        ctx.fillStyle = field.rgba(vc, 0.85 * (1 - age / 60000));
        ctx.fillText(tg.sym, g2.x + 52, g2.y + 34 + k * 12);
      }
    }
    // kill sediment: drifts down, fades — measurement honesty, not a block
    for (let k = sediment.length - 1; k >= 0; k--) {
      const s = sediment[k];
      const age = now - s.born;
      if (age > 60000) { sediment.splice(k, 1); continue; }
      s.y += s.vy * dt;
      field.drawDot(ctx, s.x, s.y, 1.4, '#7a7f8c', 0.4 * (1 - age / 60000), 0);
    }
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
      ctx.strokeStyle = field.rgba(gc, 0.6); ctx.lineWidth = 0.8; ctx.stroke();

      const ringR = 40;
      // Jarvis refinement (Jin 2026-07-10, feat/jarvis-language "게이트
      // 레티클을 이중 헤어라인 링+코너 틱 4점으로 정제"): a static inner
      // hairline ring + 4 non-rotating corner brackets frame the reticle.
      // The throughput-rotating ring/tick-band below is UNTOUCHED — its
      // spin rate is real signal (processing rate), not decoration.
      ctx.globalCompositeOperation = 'lighter';
      ctx.beginPath(); ctx.arc(gs.x, gs.y, core + 9, 0, Math.PI * 2);
      ctx.strokeStyle = field.rgba(gc, 0.3 + fireT * 0.2); ctx.lineWidth = 0.7; ctx.stroke();
      // Evaluation flash: the same mote pushGateMote just recorded, stamped
      // once on the hairline ring at its own angle, fading over NOTCH_MS.
      const notch = gateNotch[i];
      if (notch) {
        const nAge = now - notch.born;
        if (nAge < NOTCH_MS) {
          const nf = 1 - nAge / NOTCH_MS;
          const nx = gs.x + Math.cos(notch.ang) * (core + 9), ny = gs.y + Math.sin(notch.ang) * (core + 9);
          field.drawDot(ctx, nx, ny, 2 + nf * 1.6, notch.color, 0.6 * nf, 8 * nf);
        } else {
          gateNotch[i] = null;
        }
      }
      const brR = ringR + 12, brTick = 6;
      for (let c = 0; c < 4; c++) {
        const ca = Math.PI / 4 + c * (Math.PI / 2);
        const ux = Math.cos(ca), uy = Math.sin(ca), tx = -uy, ty = ux;
        const cx = gs.x + ux * brR, cy = gs.y + uy * brR;
        ctx.beginPath();
        ctx.moveTo(cx - ux * brTick, cy - uy * brTick);
        ctx.lineTo(cx, cy);
        ctx.lineTo(cx + tx * brTick, cy + ty * brTick);
        ctx.strokeStyle = field.rgba(GATE_TICK, 0.4 + fireT * 0.25); ctx.lineWidth = 0.75; ctx.stroke();
      }
      ctx.globalCompositeOperation = 'source-over';

      // jarvis arc-ring reticle (rotating tick band)
      // Meaningful rotation (Jin 2026-07-10 "도는 건 상관없는데 의미가
      // 있어야"): the reticle's spin RATE = this gate's real throughput
      // (same log-scaled load as the sweep arc) + a kick while firing.
      // Idle gate ~still, busy gate visibly working. Angle integrates via
      // per-gate state so rate changes never snap the ring.
      const load = Math.min(1, Math.log1p(g.count) / maxGateLog);
      gs.spin = (gs.spin == null ? i * 0.61 : gs.spin)
        + (now - (gs.spinT || now)) * 0.001 * (0.03 + 0.9 * load + 1.6 * fireT);
      gs.spinT = now;
      const rot = gs.spin, span = Math.PI * 1.45;
      ctx.save();
      ctx.translate(gs.x, gs.y);
      ctx.rotate(rot);
      ctx.beginPath(); ctx.arc(0, 0, ringR, -span / 2, span / 2);
      ctx.strokeStyle = field.rgba(GATE_TICK, 0.5 + fireT * 0.3); ctx.lineWidth = 0.8; ctx.stroke();
      for (let k = 0; k <= 14; k++) {
        const a = -span / 2 + (span * k / 14);
        const inR = ringR - 4, outR = ringR + (k % 2 === 0 ? 6 : 3);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * inR, Math.sin(a) * inR);
        ctx.lineTo(Math.cos(a) * outR, Math.sin(a) * outR);
        ctx.strokeStyle = field.rgba(GATE_TICK, 0.4); ctx.lineWidth = 0.7; ctx.stroke();
      }
      ctx.restore();

      // graft 3 (radial) — log-scaled activity-load sweep: a FIXED (non-
      // rotating) arc from -90deg whose length encodes real relative
      // throughput (log1p(count)/max), not a ratio (flow_not_block makes a
      // pass/kill ratio ~100% everywhere — length is the only honest signal).
      const sweep = Math.max(0.06, Math.min(1, Math.log1p(g.count) / maxGateLog)) * (Math.PI * 1.5);
      ctx.globalCompositeOperation = 'lighter';
      ctx.beginPath(); ctx.arc(gs.x, gs.y, ringR + 7, -Math.PI / 2, -Math.PI / 2 + sweep);
      ctx.strokeStyle = field.rgba(GATE_SWEEP, 0.55 + fireT * 0.3); ctx.lineWidth = 0.9; ctx.stroke();
      ctx.globalCompositeOperation = 'source-over';

      ctx.textAlign = 'center';
      ctx.font = "700 10px ui-monospace, Menlo, monospace";
      ctx.fillStyle = field.rgba(GATE_CORE, 0.9);
      // g8 label forced BELOW (circuit topology round-3: above-side put it
      // on the same band as g1's below-side 'universe' label — corner smash)
      const below = g.id === 'g8' ? true : (i % 2 === 0);
      const ly = below ? gs.y + ringR + 20 : gs.y - ringR - 12;
      ctx.fillText(`g${g.n} · ${g.label}`, gs.x, ly);
      // Jarvis numeric HUD block (Jin 2026-07-10, feat/jarvis-language
      // "소형 수치 블록·위 헤어라인"): a thin rule above the tabular count
      // separates it into its own small readout block.
      ctx.font = "400 9px ui-monospace, Menlo, monospace";
      const countTxt = String(g.count);
      const cy = ly + (below ? 12 : -12);
      const cw = ctx.measureText(countTxt).width;
      ctx.beginPath();
      ctx.moveTo(gs.x - cw / 2 - 2, cy - 7);
      ctx.lineTo(gs.x + cw / 2 + 2, cy - 7);
      ctx.strokeStyle = field.rgba(GATE_TICK, 0.35); ctx.lineWidth = 0.65; ctx.stroke();
      ctx.fillStyle = field.rgba(GATE_TICK, 0.85);
      ctx.fillText(countTxt, gs.x, cy);
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
    drawGateMotes(now, dt);
    drawComets(now, dt);
    if (deco) deco.drawDecor(ctx, now);
    if (readouts) readouts.draw(ctx, now);
    if (deco) deco.drawZoneScan(ctx, now);

    requestAnimationFrame(frame);
  }

  /* ===== public API ===== */
  let booted = false;
  // Jarvis static-layer wrapper (Jin 2026-07-10, feat/jarvis-language): the
  // faint engineering graticule (wall_spine_deco.js) bakes on TOP of the
  // field's static render, same cadence — both boot() and every 1s refresh()
  // re-render the whole static layer from scratch, so the graticule has to
  // be re-stamped alongside it or it would vanish on the first refresh.
  function bakeStatic() {
    field.renderStaticLayer(staticCtx);
    if (deco) deco.renderGraticule(staticCtx, W, H, field.gateScreen());
  }
  function boot(data) {
    fitCanvas();
    field.buildLayout(data);
    if (field.setProbeLinks) field.setProbeLinks(data.probe_links);
    field.buildEdges(data);
    bakeStatic();
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
    setUniverseTiers(nodes);
    bakeStatic();
  }
  function fireGateEvent(g) {
    if (!g || !g.gate_id) return;
    pushGateMote(g.gate_id - 1, g.decision);
    const gid = 'g' + g.gate_id;
    const srcNode = g.symbol && field.findNode((n) => n.ticker && g.symbol.indexOf(n.ticker) >= 0 && (n.cluster === 'mkt' || n.cluster === 'watch'));
    if (srcNode) {
      // Scan pulse (Jin 2026-07-12): 300ms "being inspected" bracket flash
      // on the ticker's own dot for EVERY real gate_event naming it (1-8).
      if (field.markScan) field.markScan(srcNode.id, field.venueColorOf && field.venueColorOf(srcNode.exchange));
      // Jin 2026-07-10: the ticker ITSELF progresses rightward — a mkt dot
      // with a real g1..g5 event parks beside that gate (venue-colored glow)
      // and re-parks further right on each later gate; idle -> glides home.
      if (srcNode.cluster === 'mkt' && g.gate_id >= 1 && g.gate_id <= 5) {
        field.migrateTicker(srcNode.id, g.gate_id - 1);
      }
      // real-time wiring: this strategy really fired on this ticker NOW
      if (g.gate_id === 2) pushSignalTag(g, srcNode.exchange);
      if (g.strategy && field.nodeById('strat_' + g.strategy)) {
        field.touchWire('strat_' + g.strategy, srcNode.id, field.venueColorOf(srcNode.exchange));
      }
      const es = field.pathEdges([srcNode.id, gid]);
      const vc = field.venueColorOf && field.venueColorOf(srcNode.exchange);
      if (es.length) { spawnComet(es, vc || field.clusterColor()[srcNode.cluster] || GATE_HALO, 1.8); return; }
    }
    field.markFire(gid, 700);
  }
  function fireEntry(e) {
    // strat->g2 is the real cached edge; g2..g5 ride the gate backbone
    // (both built forward in buildEdges) — a fill/open is the sizer's (G5)
    // output, so the comet rides the whole signal->sized chain to get there.
    const strat = field.nodeById('strat_' + e.strategy_id);
    const path = strat ? field.pathEdges([strat.id, 'g2', 'g3', 'g4', 'g5']) : [];
    const evc = field.venueColorOf && field.venueColorOf(e.exchange);
    if (path.length) spawnComet(path, evc || field.clusterColor().strat || '#8fd7ff', 2.0);
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
    // Journey ends at g7 now (console v2 M2): exit_tally nodes no longer get
    // a screen position (their count reads through the EXIT FSM strip
    // instead — wall_console_readouts.js's drawExitFsm), so appending a
    // tally hop here would make pathEdges() fail the WHOLE path (every hop
    // must resolve) and silently drop the win/loss comet trail entirely.
    const ids = [pos ? pos.id : null, 'g6', 'g7'].filter(Boolean);
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
    setStrategyGauges: (classes) => { if (deco) deco.setStrategyGauges(classes); },
    // console v2 (Jin 2026-07-11) — setters wall_spine_hud.js's existing
    // graph.json/flow_stats polls push into, getters wall_console_readouts.js
    // pulls lazily inside its per-frame draw().
    setConsole, setVerdicts, setFlowSummary, markGatePulse, gatePulseAt,
    consoleOf: () => consoleData,
    verdictsOf: () => verdictsRecent,
    flowSummaryOf: () => flowSummary,
  };
})();
