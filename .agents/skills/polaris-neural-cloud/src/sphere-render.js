/* Polaris Neural Cloud v4 — concentric shells + inner exchange cores + orbiting AI satellites
 *
 * Topology (outside → inside):
 *   MARKET / REGIME    shell 0  r=1.00
 *   PROVIDERS          shell 1  r=0.85
 *   SIGNALS / SCANS    shell 2  r=0.70
 *   STRATEGY × CELL    shell 3  r=0.55
 *   GATE / RISK        shell 4  r=0.40
 *   INNER CORES        4 small exchange sub-spheres clustered at origin
 *
 *   AI HIGH      r≈1.30  slow, wide
 *   AI MID       r≈1.00  med
 *   DIRECT TOOLS r≈0.78  med (REGIME DETECTOR, EVOLVER) — diamond markers
 *   AI LOW       r≈0.50  fast, tight
 *
 *   EXIT       orbits inner cores
 *   OBS        wide watcher orbit
 *   ACTION     outermost output arc
 */

(function () {
  const canvas = document.getElementById('sphere');
  if (!canvas) { console.warn('[v4] no #sphere canvas'); return; }
  const ctx = canvas.getContext('2d');

  // ── Resize ────────────────────────────────────────────────────────────
  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0, CX = 0, CY = 0, BASE_R = 0;
  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    CX = W / 2; CY = H / 2;
    BASE_R = Math.min(W, H) * 0.32;
  }
  window.addEventListener('resize', fit); fit();

  // ── Defs ──────────────────────────────────────────────────────────────
  const SHELLS = [
    { id: 'market',    label: 'MARKET / REGIME', radius: 1.00, density: 36, color: [0xd7, 0xaf, 0xff], effect: 'flare'    },
    { id: 'providers', label: 'PROVIDERS',       radius: 0.85, density: 50, color: [0x87, 0xd7, 0xff], effect: 'particle' },
    { id: 'signals',   label: 'SIGNALS / SCANS', radius: 0.70, density: 70, color: [0x87, 0xaf, 0xd7], effect: 'beam'     },
    { id: 'strategy',  label: 'STRATEGY × CELL', radius: 0.55, density: 80, color: [0x87, 0xd7, 0x87], effect: 'plasma'   },
    { id: 'gate',      label: 'GATE / RISK',     radius: 0.40, density: 36, color: [0xff, 0xaf, 0x87], effect: 'burst'    },
  ];

  const INNER_CORES = [
    { id: 'okx', label: 'OKX', cx:  0.10, cy:  0.06, cz:  0.00, radius: 0.11, density: 18, color: [0x5f, 0xd7, 0xff] },
    { id: 'cap', label: 'CAP', cx: -0.10, cy:  0.06, cz:  0.04, radius: 0.08, density: 10, color: [0xff, 0xd7, 0x87] },
    { id: 'alp', label: 'ALP', cx:  0.00, cy: -0.08, cz: -0.04, radius: 0.08, density: 10, color: [0xd7, 0xd7, 0x87] },
    { id: 'bin', label: 'BIN', cx:  0.00, cy: -0.04, cz:  0.10, radius: 0.08, density:  8, color: [0xff, 0xaf, 0x5f] },
  ];

  // Satellites (each one is a moving point with orbit params)
  // tier:  high|mid|tool|low|exit|obs|action
  const SATELLITES = [
    // High orbit — system-wide AI judges (placeholders, swap via bindAIModules)
    { id: 'ai_high_a', name: 'AI · HIGH α', tier: 'high', orbitR: 1.30, speed: 0.10, axis: { x: 0.1, y: 1.0, z: 0.05 }, phase: 0.00, color: [0xff, 0xd7, 0xff], kind: 'ai' },
    { id: 'ai_high_b', name: 'AI · HIGH β', tier: 'high', orbitR: 1.32, speed: 0.08, axis: { x: 0.5, y: 0.8, z: 0.2  }, phase: 2.10, color: [0xd7, 0xd7, 0xff], kind: 'ai' },

    // Mid orbit — tactical AI
    { id: 'ai_mid_a',  name: 'AI · MID α',  tier: 'mid',  orbitR: 1.00, speed: 0.18, axis: { x: 0.7, y: 0.3, z: 0.6  }, phase: 0.40, color: [0x87, 0xff, 0xff], kind: 'ai' },
    { id: 'ai_mid_b',  name: 'AI · MID β',  tier: 'mid',  orbitR: 1.02, speed: 0.16, axis: { x: 0.2, y: 0.6, z: 0.8  }, phase: 1.80, color: [0x87, 0xd7, 0xff], kind: 'ai' },
    { id: 'ai_mid_c',  name: 'AI · MID γ',  tier: 'mid',  orbitR: 0.98, speed: 0.20, axis: { x: 0.9, y: 0.1, z: 0.5  }, phase: 4.10, color: [0x87, 0xff, 0xd7], kind: 'ai' },

    // Direct tools — diamonds, system-direct
    { id: 'tool_regime',  name: 'REGIME DETECTOR', tier: 'tool', orbitR: 0.78, speed: 0.12, axis: { x: 0.0, y: 1.0, z: 0.0 }, phase: 0.00, color: [0xd7, 0xaf, 0xff], kind: 'tool' },
    { id: 'tool_evolver', name: 'EVOLVER',         tier: 'tool', orbitR: 0.80, speed: 0.10, axis: { x: 1.0, y: 0.0, z: 0.0 }, phase: Math.PI, color: [0x87, 0xd7, 0x87], kind: 'tool' },

    // Low orbit — fast transactional AI
    { id: 'ai_low_a', name: 'AI · LOW α', tier: 'low', orbitR: 0.50, speed: 0.42, axis: { x: 0.4, y: 0.7, z: 0.5 }, phase: 0.00, color: [0xff, 0xff, 0x87], kind: 'ai' },
    { id: 'ai_low_b', name: 'AI · LOW β', tier: 'low', orbitR: 0.52, speed: 0.38, axis: { x: 0.6, y: 0.2, z: 0.8 }, phase: 1.50, color: [0xff, 0xd7, 0x87], kind: 'ai' },
    { id: 'ai_low_c', name: 'AI · LOW γ', tier: 'low', orbitR: 0.48, speed: 0.46, axis: { x: 0.1, y: 0.9, z: 0.3 }, phase: 3.20, color: [0xff, 0xaf, 0x87], kind: 'ai' },

    // EXIT — orbits inner core
    { id: 'sat_exit', name: 'EXIT QUALITY', tier: 'exit', orbitR: 0.28, speed: 0.55, axis: { x: 0.3, y: 0.5, z: 0.8 }, phase: 0.00, color: [0xff, 0x87, 0x5f], kind: 'sat' },
    // OBS — wide watcher
    { id: 'sat_obs',  name: 'OBS / SAFETY', tier: 'obs',  orbitR: 1.45, speed: 0.04, axis: { x: 1.0, y: 0.2, z: 0.0 }, phase: 0.50, color: [0xd7, 0xd7, 0x87], kind: 'sat' },
    // ACTION — outermost
    { id: 'sat_action', name: 'ACTION ITEMS', tier: 'action', orbitR: 1.55, speed: 0.06, axis: { x: 0.0, y: 0.0, z: 1.0 }, phase: 1.00, color: [0xd7, 0x87, 0x87], kind: 'sat' },
  ];

  // ── Build shell nodes ─────────────────────────────────────────────────
  const nodes = [];   // { i, layer:'shell'|'core', shellIdx, coreIdx, x,y,z, importance, phase, lastFired, entity }
  const SHELL_NODE_RANGES = {};
  let nid = 0;

  // Fibonacci sphere distribution for even coverage
  function fibSphere(n) {
    const pts = [];
    const phi = Math.PI * (Math.sqrt(5) - 1);
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / (n - 1)) * 2;
      const radius = Math.sqrt(1 - y * y);
      const theta = phi * i;
      pts.push({ x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius });
    }
    return pts;
  }

  SHELLS.forEach((sh, si) => {
    const start = nid;
    const pts = fibSphere(sh.density);
    pts.forEach(p => {
      const jitter = 0.97 + Math.random() * 0.06;
      nodes.push({
        i: nid,
        layer: 'shell',
        shellIdx: si,
        coreIdx: -1,
        x: p.x * sh.radius * jitter,
        y: p.y * sh.radius * jitter,
        z: p.z * sh.radius * jitter,
        importance: 0.4 + Math.random() * 0.5,
        phase: Math.random() * Math.PI * 2,
        lastFired: -Infinity,
        entity: null,
      });
      nid++;
    });
    SHELL_NODE_RANGES[sh.id] = { start, end: nid };
  });

  // ── Build inner core nodes ────────────────────────────────────────────
  const CORE_NODE_RANGES = {};
  INNER_CORES.forEach((co, ci) => {
    const start = nid;
    const pts = fibSphere(co.density);
    pts.forEach(p => {
      nodes.push({
        i: nid,
        layer: 'core',
        shellIdx: -1,
        coreIdx: ci,
        x: co.cx + p.x * co.radius,
        y: co.cy + p.y * co.radius,
        z: co.cz + p.z * co.radius,
        importance: 0.6 + Math.random() * 0.4,
        phase: Math.random() * Math.PI * 2,
        lastFired: -Infinity,
        entity: null,
      });
      nid++;
    });
    CORE_NODE_RANGES[co.id] = { start, end: nid };
  });

  const N_TOTAL = nodes.length;

  // ── Build edges ───────────────────────────────────────────────────────
  // intra-shell K-nearest, inter-shell radial pairs, gate→core
  const edges = [];
  const edgeMap = new Map();

  function addEdge(a, b, kind) {
    if (a === b) return;
    const key = a < b ? a + ',' + b : b + ',' + a;
    if (edgeMap.has(key)) return;
    edgeMap.set(key, edges.length);
    edges.push({ a, b, kind, strength: 0, lastFired: -Infinity });
  }

  // intra-shell K=3
  SHELLS.forEach(sh => {
    const { start, end } = SHELL_NODE_RANGES[sh.id];
    for (let i = start; i < end; i++) {
      const a = nodes[i];
      const dists = [];
      for (let j = start; j < end; j++) {
        if (i === j) continue;
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        dists.push({ j, d: dx * dx + dy * dy + dz * dz });
      }
      dists.sort((p, q) => p.d - q.d);
      for (let k = 0; k < Math.min(3, dists.length); k++) addEdge(i, dists[k].j, 'intra-shell');
    }
  });

  // intra-core K=3
  INNER_CORES.forEach(co => {
    const { start, end } = CORE_NODE_RANGES[co.id];
    for (let i = start; i < end; i++) {
      const a = nodes[i];
      const dists = [];
      for (let j = start; j < end; j++) {
        if (i === j) continue;
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        dists.push({ j, d: dx * dx + dy * dy + dz * dz });
      }
      dists.sort((p, q) => p.d - q.d);
      for (let k = 0; k < Math.min(3, dists.length); k++) addEdge(i, dists[k].j, 'intra-core');
    }
  });

  // inter-shell radial: shell-i → shell-(i+1) closest pairs
  for (let si = 0; si < SHELLS.length - 1; si++) {
    const A = SHELL_NODE_RANGES[SHELLS[si].id];
    const B = SHELL_NODE_RANGES[SHELLS[si + 1].id];
    const pairs = [];
    for (let i = A.start; i < A.end; i++) {
      const a = nodes[i];
      let bestJ = -1, bestD = Infinity;
      for (let j = B.start; j < B.end; j++) {
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        const d = dx * dx + dy * dy + dz * dz;
        if (d < bestD) { bestD = d; bestJ = j; }
      }
      pairs.push({ i, j: bestJ, d: bestD });
    }
    pairs.sort((p, q) => p.d - q.d);
    const take = Math.min(pairs.length, Math.round(A.end - A.start) * 0.6 | 0);
    for (let k = 0; k < take; k++) addEdge(pairs[k].i, pairs[k].j, 'radial');
  }

  // gate → core: each gate node → nearest inner core node
  {
    const G = SHELL_NODE_RANGES['gate'];
    const allCoreStart = CORE_NODE_RANGES[INNER_CORES[0].id].start;
    const allCoreEnd = CORE_NODE_RANGES[INNER_CORES[INNER_CORES.length - 1].id].end;
    for (let i = G.start; i < G.end; i++) {
      const a = nodes[i];
      let bestJ = -1, bestD = Infinity;
      for (let j = allCoreStart; j < allCoreEnd; j++) {
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        const d = dx * dx + dy * dy + dz * dz;
        if (d < bestD) { bestD = d; bestJ = j; }
      }
      if (bestJ >= 0) addEdge(i, bestJ, 'gate-core');
    }
  }

  // ── Camera + orbit ────────────────────────────────────────────────────
  let yaw = 0, pitch = 0.18, zoom = 1.0, autoRotate = true;
  let dragging = false, dragStart = null;
  canvas.addEventListener('mousedown', (e) => { dragging = true; autoRotate = false; dragStart = { x: e.clientX, y: e.clientY, yaw, pitch }; });
  window.addEventListener('mouseup', () => { dragging = false; });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    yaw = dragStart.yaw + (e.clientX - dragStart.x) * 0.005;
    pitch = Math.max(-1.4, Math.min(1.4, dragStart.pitch - (e.clientY - dragStart.y) * 0.005));
  });
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoom = Math.max(0.5, Math.min(2.5, zoom * (e.deltaY > 0 ? 0.92 : 1.08))); }, { passive: false });
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Space') { e.preventDefault(); autoRotate = !autoRotate; }
    if (e.code === 'KeyR') { yaw = 0; pitch = 0.18; zoom = 1.0; autoRotate = true; }
  });

  // Hover detection
  let hoverNode = -1, hoverSat = -1;
  canvas.addEventListener('mousemove', (e) => {
    if (dragging) return;
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    let bn = -1, bd = 18 * 18;
    for (let i = 0; i < N_TOTAL; i++) {
      const p = project(nodes[i]);
      if (p.depth < -0.3) continue;
      const dx = p.sx - sx, dy = p.sy - sy, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; bn = i; }
    }
    let bs = -1, bsd = 18 * 18;
    SATELLITES.forEach((s, si) => {
      const p = projectSat(s);
      if (p.depth < -0.3) return;
      const dx = p.sx - sx, dy = p.sy - sy, d = dx * dx + dy * dy;
      if (d < bsd) { bsd = d; bs = si; }
    });
    if (bn !== hoverNode || bs !== hoverSat) {
      hoverNode = bn; hoverSat = bs;
      canvas.style.cursor = (bn >= 0 || bs >= 0) ? 'crosshair' : 'default';
      canvas.dispatchEvent(new CustomEvent('hover', { detail: {
        node: bn >= 0 ? nodes[bn] : null,
        nodeIdx: bn,
        satellite: bs >= 0 ? SATELLITES[bs] : null,
        screen: { x: e.clientX, y: e.clientY },
      }}));
    }
  });
  canvas.addEventListener('click', () => {
    if (hoverNode >= 0) canvas.dispatchEvent(new CustomEvent('node-click', { detail: { idx: hoverNode, node: nodes[hoverNode] } }));
    else if (hoverSat >= 0) canvas.dispatchEvent(new CustomEvent('sat-click', { detail: { idx: hoverSat, sat: SATELLITES[hoverSat] } }));
  });

  // ── Projection ────────────────────────────────────────────────────────
  function transform(x, y, z) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    let nx = x * cy - z * sy;
    let nz = x * sy + z * cy;
    let ny = y;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const ny2 = ny * cp - nz * sp;
    const nz2 = ny * sp + nz * cp;
    const persp = 1 / (1 - nz2 * 0.32);
    return {
      sx: CX + nx * BASE_R * zoom * persp,
      sy: CY + ny2 * BASE_R * zoom * persp,
      depth: nz2,
      persp,
    };
  }
  function project(p) { return transform(p.x, p.y, p.z); }
  function projectSat(s) {
    // Compute satellite position at current time
    const t = performance.now() * 0.001;
    const a = s.phase + t * s.speed;
    // axis vector normalized; build orthonormal basis (axis, u, v)
    const ax = s.axis.x, ay = s.axis.y, az = s.axis.z;
    const len = Math.hypot(ax, ay, az) || 1;
    const Ax = ax / len, Ay = ay / len, Az = az / len;
    // pick a vector not parallel to axis
    let ux = 1, uy = 0, uz = 0;
    if (Math.abs(Ax) > 0.9) { ux = 0; uy = 1; uz = 0; }
    // u_perp = u - (u·A)A; then normalize
    const uDotA = ux * Ax + uy * Ay + uz * Az;
    let u1x = ux - uDotA * Ax, u1y = uy - uDotA * Ay, u1z = uz - uDotA * Az;
    const u1L = Math.hypot(u1x, u1y, u1z) || 1;
    u1x /= u1L; u1y /= u1L; u1z /= u1L;
    // v = A × u
    const v1x = Ay * u1z - Az * u1y;
    const v1y = Az * u1x - Ax * u1z;
    const v1z = Ax * u1y - Ay * u1x;
    const ca = Math.cos(a), sa = Math.sin(a);
    const x = (u1x * ca + v1x * sa) * s.orbitR;
    const y = (u1y * ca + v1y * sa) * s.orbitR;
    const z = (u1z * ca + v1z * sa) * s.orbitR;
    s._pos = { x, y, z };
    return transform(x, y, z);
  }

  // ── Firing list ───────────────────────────────────────────────────────
  // f: { kind: 'edge'|'beam'|'outbound', edgeIdx?, satIdx?, targetNodeIdx?, t, life, speed, decay, color, intensity, seed }
  const firing = [];
  const supernovas = [];
  const chainNodes = new Set();
  const chainEdges = new Set();
  let chainActive = false;
  let killSwitch = false;
  let currentRegime = 'NEUTRAL';
  let currentNSI = 0.78;

  const REGIME_TINT = {
    RISK_ON:    [135, 215, 135],
    RISK_OFF:   [215, 135, 135],
    NEUTRAL:    [215, 215, 135],
    TRANSITION: [135, 215, 255],
    CRISIS:     [255, 95,  95],
  };

  function spawnEdgeFire(edgeIdx, opts = {}) {
    if (edgeIdx < 0 || edgeIdx >= edges.length) return;
    const e = edges[edgeIdx];
    const a = nodes[e.a], b = nodes[e.b];
    let color = [200, 220, 255];
    // Pick color from outer shell (the data flows inward)
    if (a.layer === 'shell') color = SHELLS[a.shellIdx].color;
    else if (b.layer === 'shell') color = SHELLS[b.shellIdx].color;
    if (a.layer === 'core') color = INNER_CORES[a.coreIdx].color;
    firing.push({
      kind: 'edge', edgeIdx,
      t: 0, life: 1,
      speed: opts.speed || (0.7 + Math.random() * 0.8),
      decay: opts.decay || (0.6 + Math.random() * 0.4),
      color: opts.color || color,
      intensity: opts.intensity || 1.0,
      seed: Math.random() * 1000,
    });
    e.strength = Math.min(1, e.strength + 0.04);
    e.lastFired = performance.now();
    nodes[e.a].lastFired = performance.now();
    nodes[e.b].lastFired = performance.now();
  }

  function spawnSatBeam(satIdx, targetNodeIdx, opts = {}) {
    if (satIdx < 0 || satIdx >= SATELLITES.length) return;
    if (targetNodeIdx < 0 || targetNodeIdx >= N_TOTAL) return;
    firing.push({
      kind: 'beam', satIdx, targetNodeIdx,
      t: 0, life: 1,
      speed: opts.speed || 1.6,
      decay: opts.decay || 0.8,
      color: opts.color || SATELLITES[satIdx].color,
      intensity: opts.intensity || 1.4,
      seed: Math.random() * 1000,
    });
    nodes[targetNodeIdx].lastFired = performance.now();
  }

  function spawnOutbound(fromNodeIdx, satIdx, opts = {}) {
    if (fromNodeIdx < 0 || fromNodeIdx >= N_TOTAL) return;
    firing.push({
      kind: 'outbound', edgeIdx: -1, fromNodeIdx, satIdx,
      t: 0, life: 1, speed: opts.speed || 1.0, decay: 0.6,
      color: opts.color || SATELLITES[satIdx]?.color || [255, 175, 135],
      intensity: 1.0, seed: Math.random() * 1000,
    });
  }

  // ── Drawing helpers ───────────────────────────────────────────────────
  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }

  function drawBackdrop() {
    const r = Math.min(W, H) * 0.65;
    const tint = REGIME_TINT[currentRegime] || REGIME_TINT.NEUTRAL;
    const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, r);
    g.addColorStop(0,   `rgba(${tint[0]},${tint[1]},${tint[2]},0.22)`);
    g.addColorStop(0.5, `rgba(${tint[0]},${tint[1]},${tint[2]},0.06)`);
    g.addColorStop(1,   `rgba(${tint[0]},${tint[1]},${tint[2]},0)`);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }

  // Faint shell wireframes (3 great-circle outlines per shell)
  function drawShellWireframes() {
    SHELLS.forEach((sh, si) => {
      const r = sh.radius;
      ctx.save();
      ctx.strokeStyle = rgba(sh.color, 0.06);
      ctx.lineWidth = 0.5;
      // 3 great circles: equator, meridian, prime
      const segs = 64;
      for (let k = 0; k < 3; k++) {
        ctx.beginPath();
        for (let i = 0; i <= segs; i++) {
          const t = (i / segs) * Math.PI * 2;
          let p;
          if (k === 0) p = transform(Math.cos(t) * r, 0, Math.sin(t) * r);
          if (k === 1) p = transform(0, Math.cos(t) * r, Math.sin(t) * r);
          if (k === 2) p = transform(Math.cos(t) * r, Math.sin(t) * r, 0);
          if (i === 0) ctx.moveTo(p.sx, p.sy); else ctx.lineTo(p.sx, p.sy);
        }
        ctx.stroke();
      }
      ctx.restore();
    });
  }

  function drawDormantEdges(projected) {
    for (let ei = 0; ei < edges.length; ei++) {
      const e = edges[ei];
      const a = projected[e.a], b = projected[e.b];
      const front = ((a.depth + b.depth) / 2 + 1) / 2;
      const inChain = chainEdges.has(ei);
      let alpha = 0.010 + front * 0.025 + e.strength * 0.10;
      let lw = 0.3 + e.strength * 0.6;
      let col = [120, 160, 200];
      if (e.kind === 'radial') { col = [180, 200, 230]; alpha *= 1.4; }
      if (e.kind === 'gate-core') { col = [255, 200, 150]; alpha *= 1.6; }
      if (chainActive) {
        if (inChain) { alpha = 0.85; lw = 2.2; col = [255, 235, 200]; }
        else alpha *= 0.18;
      }
      ctx.strokeStyle = rgba(col, alpha);
      ctx.lineWidth = lw;
      if (inChain && chainActive) { ctx.shadowColor = rgba([255, 235, 160], 1); ctx.shadowBlur = 14; }
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      if (inChain && chainActive) ctx.shadowBlur = 0;
    }
  }

  function drawNodes(projected, time) {
    const order = projected.map((p, i) => i).sort((x, y) => projected[x].depth - projected[y].depth);
    for (const i of order) {
      const p = projected[i];
      const n = nodes[i];
      const inChain = chainNodes.has(i);
      const dim = chainActive && !inChain ? 0.18 : 1.0;
      const baseColor = n.layer === 'shell' ? SHELLS[n.shellIdx].color : INNER_CORES[n.coreIdx].color;
      const depthFade = 0.30 + 0.70 * ((p.depth + 1) / 2);
      const breathe = 1 + Math.sin(time * 0.0013 + n.phase) * 0.20;
      const recentFire = Math.max(0, 1 - (time - n.lastFired) / 800);
      const intensity = ((n.importance * 0.55 + recentFire * 1.2) * breathe * depthFade) * dim;
      const r = (1.0 + n.importance * 1.3 + recentFire * 1.8 + (inChain ? 1.6 : 0)) * p.persp;

      if ((n.importance > 0.6 && depthFade > 0.5) || recentFire > 0.1 || inChain) {
        const haloR = r * (4 + recentFire * 3 + (inChain ? 4 : 0));
        const hg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, haloR);
        const haloColor = inChain ? [255, 235, 200] : baseColor;
        hg.addColorStop(0, rgba(haloColor, (inChain ? 0.75 : 0.40) * intensity));
        hg.addColorStop(1, rgba(haloColor, 0));
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, haloR, 0, Math.PI * 2); ctx.fill();
      }
      ctx.fillStyle = rgba(inChain ? [255, 250, 230] : baseColor, Math.min(1, intensity * 1.6));
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2); ctx.fill();

      if (inChain && n.entity) {
        ctx.save();
        ctx.font = '11px ui-monospace, monospace';
        ctx.fillStyle = 'rgba(255,245,210,0.95)';
        ctx.shadowColor = 'rgba(0,0,0,0.9)'; ctx.shadowBlur = 6;
        ctx.fillText(n.entity.label || ('node #' + i), p.sx + r + 8, p.sy + 4);
        ctx.restore();
      }
    }
  }

  // Inner core labels
  function drawCoreLabels() {
    INNER_CORES.forEach(co => {
      const p = transform(co.cx, co.cy, co.cz);
      if (p.depth < -0.5) return;
      ctx.save();
      ctx.font = 'bold 10px ui-monospace, monospace';
      ctx.fillStyle = rgba(co.color, 0.8);
      ctx.shadowColor = 'rgba(0,0,0,0.9)'; ctx.shadowBlur = 4;
      ctx.fillText(co.label, p.sx + 6, p.sy - 8);
      ctx.restore();
    });
  }

  // Satellites + their trails
  function drawSatellites(time) {
    SATELLITES.forEach((s, si) => {
      const p = projectSat(s);
      const depthFade = 0.4 + 0.6 * ((p.depth + 1) / 2);

      // Trail — past few positions
      ctx.save();
      const trailSegs = 24;
      ctx.strokeStyle = rgba(s.color, 0.18 * depthFade);
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      for (let k = 0; k <= trailSegs; k++) {
        const tBack = (s.phase + (time * 0.001 - k * 0.04) * s.speed);
        // Recompute orbit position at a-tBack
        const ax = s.axis.x, ay = s.axis.y, az = s.axis.z;
        const len = Math.hypot(ax, ay, az) || 1;
        const Ax = ax / len, Ay = ay / len, Az = az / len;
        let ux = 1, uy = 0, uz = 0;
        if (Math.abs(Ax) > 0.9) { ux = 0; uy = 1; uz = 0; }
        const uDotA = ux * Ax + uy * Ay + uz * Az;
        let u1x = ux - uDotA * Ax, u1y = uy - uDotA * Ay, u1z = uz - uDotA * Az;
        const u1L = Math.hypot(u1x, u1y, u1z) || 1;
        u1x /= u1L; u1y /= u1L; u1z /= u1L;
        const v1x = Ay * u1z - Az * u1y;
        const v1y = Az * u1x - Ax * u1z;
        const v1z = Ax * u1y - Ay * u1x;
        const ca = Math.cos(tBack), sa = Math.sin(tBack);
        const x = (u1x * ca + v1x * sa) * s.orbitR;
        const y = (u1y * ca + v1y * sa) * s.orbitR;
        const z = (u1z * ca + v1z * sa) * s.orbitR;
        const tp = transform(x, y, z);
        if (k === 0) ctx.moveTo(tp.sx, tp.sy);
        else ctx.lineTo(tp.sx, tp.sy);
      }
      ctx.stroke();
      ctx.restore();

      // Satellite marker
      const sz = (s.kind === 'tool' ? 4 : (s.tier === 'high' ? 4 : s.tier === 'mid' ? 3.5 : s.tier === 'low' ? 3 : 3.5)) * p.persp;
      ctx.save();
      ctx.shadowColor = rgba(s.color, 1);
      ctx.shadowBlur = 14 * depthFade;
      // Halo
      const haloR = sz * 4;
      const hg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, haloR);
      hg.addColorStop(0, rgba(s.color, 0.55 * depthFade));
      hg.addColorStop(1, rgba(s.color, 0));
      ctx.fillStyle = hg;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, haloR, 0, Math.PI * 2); ctx.fill();

      // Marker shape
      ctx.fillStyle = rgba(s.color, depthFade);
      if (s.kind === 'tool') {
        // Diamond
        ctx.beginPath();
        ctx.moveTo(p.sx, p.sy - sz);
        ctx.lineTo(p.sx + sz, p.sy);
        ctx.lineTo(p.sx, p.sy + sz);
        ctx.lineTo(p.sx - sz, p.sy);
        ctx.closePath();
        ctx.fill();
      } else if (s.kind === 'sat') {
        // Square
        ctx.fillRect(p.sx - sz, p.sy - sz, sz * 2, sz * 2);
      } else {
        // Circle (AI)
        ctx.beginPath(); ctx.arc(p.sx, p.sy, sz, 0, Math.PI * 2); ctx.fill();
      }
      // Inner white core
      ctx.fillStyle = `rgba(255,255,255,${0.85 * depthFade})`;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, sz * 0.4, 0, Math.PI * 2); ctx.fill();
      ctx.restore();

      // Label for tool satellites + on-hover for AI
      if (s.kind === 'tool') {
        ctx.save();
        ctx.font = '9px ui-monospace, monospace';
        ctx.fillStyle = rgba(s.color, 0.7 * depthFade);
        ctx.shadowColor = 'rgba(0,0,0,0.9)'; ctx.shadowBlur = 4;
        ctx.fillText(s.name, p.sx + sz + 6, p.sy + 3);
        ctx.restore();
      }
    });
  }

  // ── Effect renderers ──────────────────────────────────────────────────
  function fxLine(a, b, f, opts = {}) {
    ctx.save();
    ctx.shadowColor = rgba(f.color, 1);
    ctx.shadowBlur = (opts.blur || 14) * f.life * f.intensity;
    ctx.strokeStyle = rgba(f.color, (opts.alpha || 0.9) * f.life);
    ctx.lineWidth = (opts.width || 2.0) * f.life * f.intensity;
    ctx.beginPath();
    ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy);
    ctx.stroke();
    ctx.restore();
  }
  function fxParticleStream(a, b, f) {
    const dx = b.sx - a.sx, dy = b.sy - a.sy;
    const N_P = 5;
    ctx.save();
    for (let i = 0; i < N_P; i++) {
      const off = ((f.t * 0.9 + i / N_P + f.seed * 0.001) % 1);
      const px = a.sx + dx * off, py = a.sy + dy * off;
      ctx.fillStyle = rgba(f.color, 0.7 * f.life);
      ctx.shadowColor = rgba(f.color, 1); ctx.shadowBlur = 6;
      ctx.beginPath(); ctx.arc(px, py, 1.4, 0, Math.PI * 2); ctx.fill();
    }
    ctx.shadowBlur = 0;
    ctx.strokeStyle = rgba(f.color, 0.10 * f.life);
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
    ctx.restore();
  }

  function drawFiring(projected) {
    for (const f of firing) {
      const inChain = chainActive && f.kind === 'edge' && chainEdges.has(f.edgeIdx);
      const dim = chainActive ? (inChain ? 1.0 : 0.20) : 0.7;
      const prevA = ctx.globalAlpha;
      ctx.globalAlpha = prevA * dim;

      if (f.kind === 'edge') {
        const e = edges[f.edgeIdx];
        const a = projected[e.a], b = projected[e.b];
        if (e.kind === 'radial') {
          // beam-like inward stream
          const t = f.t;
          const startT = Math.max(0, t * 1.4 - 0.4);
          const endT = Math.min(1, t * 1.4);
          const sx = a.sx + (b.sx - a.sx) * startT, sy = a.sy + (b.sy - a.sy) * startT;
          const ex = a.sx + (b.sx - a.sx) * endT, ey = a.sy + (b.sy - a.sy) * endT;
          fxLine({ sx, sy }, { sx: ex, sy: ey }, f, { blur: 14, width: 2.2, alpha: 0.85 });
        } else if (e.kind === 'gate-core') {
          fxLine(a, b, f, { blur: 18, width: 3.0, alpha: 0.9 });
        } else if (e.kind === 'intra-core') {
          // Plasma-like
          fxLine(a, b, f, { blur: 20, width: 2.8, alpha: 0.7 });
        } else {
          fxParticleStream(a, b, f);
        }
      } else if (f.kind === 'beam') {
        const sat = SATELLITES[f.satIdx];
        const a = projectSat(sat); // current sat pos
        const b = projected[f.targetNodeIdx];
        // Bright beam from satellite to node
        ctx.save();
        ctx.shadowColor = rgba(f.color, 1);
        ctx.shadowBlur = 22 * f.life;
        ctx.strokeStyle = rgba(f.color, 0.9 * f.life);
        ctx.lineWidth = 1.8 * f.life;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
        // Bright tip
        const tipT = Math.min(1, f.t);
        const tx = a.sx + (b.sx - a.sx) * tipT, ty = a.sy + (b.sy - a.sy) * tipT;
        ctx.fillStyle = '#fff';
        ctx.beginPath(); ctx.arc(tx, ty, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      } else if (f.kind === 'outbound') {
        const a = projected[f.fromNodeIdx];
        const sat = SATELLITES[f.satIdx];
        const b = projectSat(sat);
        // Curved warm arc (quadratic)
        const mx = (a.sx + b.sx) / 2, my = (a.sy + b.sy) / 2;
        const dx = b.sx - a.sx, dy = b.sy - a.sy;
        const len = Math.hypot(dx, dy);
        const nx = -dy / len, ny = dx / len;
        const cx = mx + nx * len * 0.25, cy = my + ny * len * 0.25;
        ctx.save();
        ctx.shadowColor = rgba(f.color, 1);
        ctx.shadowBlur = 16 * f.life;
        ctx.strokeStyle = rgba(f.color, 0.85 * f.life);
        ctx.lineWidth = 1.6 * f.life;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy); ctx.quadraticCurveTo(cx, cy, b.sx, b.sy);
        ctx.stroke();
        // Traveling head
        const t = f.t, it = 1 - t;
        const px = it * it * a.sx + 2 * it * t * cx + t * t * b.sx;
        const py = it * it * a.sy + 2 * it * t * cy + t * t * b.sy;
        ctx.fillStyle = '#fff5d2';
        ctx.beginPath(); ctx.arc(px, py, 2.4, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }

      // Arrival flash on edges
      if (f.kind === 'edge' && f.t >= 1 && f.life > 0.5) {
        const e = edges[f.edgeIdx];
        const b = projected[e.b];
        const arrival = Math.max(0, 1 - (f.t - 1) * 5);
        if (arrival > 0) {
          ctx.save();
          ctx.shadowColor = rgba(f.color, 1);
          ctx.shadowBlur = 28 * arrival;
          ctx.fillStyle = rgba(f.color, arrival);
          ctx.beginPath(); ctx.arc(b.sx, b.sy, 4.5 * arrival, 0, Math.PI * 2); ctx.fill();
          ctx.restore();
        }
      }
      ctx.globalAlpha = prevA;
    }

    // Supernovas
    for (let i = supernovas.length - 1; i >= 0; i--) {
      const s = supernovas[i];
      const node = projected[s.nodeIdx];
      if (s.t > 1) { supernovas.splice(i, 1); continue; }
      const expand = s.t * 60;
      ctx.save();
      ctx.shadowColor = rgba(s.color, 1);
      ctx.shadowBlur = 40 * (1 - s.t);
      ctx.strokeStyle = rgba(s.color, 1 - s.t);
      ctx.lineWidth = 2.5 * (1 - s.t) + 0.5;
      ctx.beginPath(); ctx.arc(node.sx, node.sy, expand, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = `rgba(255,255,255,${1 - s.t})`;
      ctx.beginPath(); ctx.arc(node.sx, node.sy, 6 * (1 - s.t) + 1, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      s.t += 0.025;
    }
  }

  // ── Helpers for AI/PolarisCloud API ──────────────────────────────────
  function pickRandomEdgeKind(kind) {
    const list = [];
    for (let i = 0; i < edges.length; i++) if (edges[i].kind === kind) list.push(i);
    if (!list.length) return -1;
    return list[(Math.random() * list.length) | 0];
  }
  function pickRandomNodeInShell(shellId) {
    const r = SHELL_NODE_RANGES[shellId];
    if (!r) return -1;
    return r.start + ((Math.random() * (r.end - r.start)) | 0);
  }
  function pickRandomNodeInCore(coreId) {
    const r = CORE_NODE_RANGES[coreId];
    if (!r) return -1;
    return r.start + ((Math.random() * (r.end - r.start)) | 0);
  }
  function nodesByShell(shellId) {
    const r = SHELL_NODE_RANGES[shellId];
    if (!r) return [];
    const out = [];
    for (let i = r.start; i < r.end; i++) out.push(i);
    return out;
  }

  // ── Frame loop ────────────────────────────────────────────────────────
  let last = performance.now();
  let tickN = 12847;

  function frame(now) {
    const dt = Math.min(50, now - last) / 1000;
    last = now;
    if (autoRotate) yaw += dt * 0.06;

    // Project all nodes
    const projected = nodes.map(project);

    // Advance firing
    for (let i = firing.length - 1; i >= 0; i--) {
      const f = firing[i];
      if (f.t < 1) f.t += f.speed * dt;
      else { f.life -= f.decay * dt; if (f.life <= 0) firing.splice(i, 1); }
    }

    // Decay edge strengths
    for (const e of edges) e.strength *= Math.pow(0.5, dt / 30);

    // Background firings — gentle inbound radial flow
    if (Math.random() < dt * 8) spawnEdgeFire(pickRandomEdgeKind('radial'));
    if (Math.random() < dt * 6) spawnEdgeFire(pickRandomEdgeKind('intra-shell'));
    if (Math.random() < dt * 3) spawnEdgeFire(pickRandomEdgeKind('intra-core'));
    if (Math.random() < dt * 1) spawnEdgeFire(pickRandomEdgeKind('gate-core'));
    // Background AI satellite beams
    if (Math.random() < dt * 2 && !killSwitch) {
      const si = (Math.random() * SATELLITES.length) | 0;
      const sat = SATELLITES[si];
      // Pick a target node — high satellites go for shells, low go for inner
      let target;
      if (sat.tier === 'high') target = pickRandomNodeInShell(['signals', 'strategy'][((Math.random() * 2) | 0)]);
      else if (sat.tier === 'mid') target = pickRandomNodeInShell(['providers', 'signals'][((Math.random() * 2) | 0)]);
      else if (sat.tier === 'low') target = pickRandomNodeInShell(['gate', 'strategy'][((Math.random() * 2) | 0)]);
      else if (sat.tier === 'tool') target = pickRandomNodeInShell(sat.id === 'tool_regime' ? 'market' : 'strategy');
      else target = pickRandomNodeInShell('strategy');
      if (target >= 0) spawnSatBeam(si, target);
    }

    // Render
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawBackdrop();
    drawShellWireframes();
    drawDormantEdges(projected);
    drawNodes(projected, now);
    drawCoreLabels();
    drawSatellites(now);
    drawFiring(projected);

    // HUD
    const fc = document.getElementById('firing-count');
    if (fc) fc.textContent = firing.length;
    const ec = document.getElementById('edge-count');
    if (ec) ec.textContent = firing.filter(f => f.t < 1).length;

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ── Tick counter ──────────────────────────────────────────────────────
  setInterval(() => {
    tickN++;
    const tk = document.getElementById('tick'); if (tk) tk.textContent = tickN.toLocaleString();
  }, 1000);

  // ── PolarisCloud API ──────────────────────────────────────────────────
  const ENTITY_NODE = {}; // 'cluster:id' → nodeIdx (legacy v3 cluster names mapped to v4)
  const ENTITY_SAT = {};  // 'sat:<id>' → satIdx

  // Map v3 cluster names to v4 layers
  const V3_TO_V4 = {
    market:    'market',
    providers: 'providers',
    pipeline:  'gate',     // open positions / scans → gate shell
    strategy:  'strategy',
    exit:      null,       // satellite (sat_exit)
    obs:       null,       // satellite (sat_obs)
    action:    null,       // satellite (sat_action)
  };

  window.PolarisCloud = {
    SHELLS, INNER_CORES, SATELLITES,
    SHELL_NODE_RANGES, CORE_NODE_RANGES,

    setRegime(r) {
      const u = (r || '').toUpperCase();
      if (REGIME_TINT[u]) {
        currentRegime = u;
        const el = document.getElementById('regime-val');
        if (el) {
          el.textContent = u;
          el.style.color = u === 'RISK_OFF' || u === 'CRISIS' ? '#d78787'
                         : u === 'RISK_ON' ? '#87d787' : '#d7d787';
        }
      }
    },
    setNSI(v) {
      currentNSI = Math.max(0, Math.min(1, v));
      const el = document.getElementById('nsi-val');
      if (el) el.textContent = (currentNSI * 100).toFixed(0);
    },
    setKillSwitch(on) {
      killSwitch = !!on;
      if (on) {
        // Blink ACTION satellite
        for (let i = 0; i < 6; i++) {
          setTimeout(() => spawnSatBeam(SATELLITES.findIndex(s => s.id === 'sat_action'), pickRandomNodeInShell('gate')), i * 200);
        }
      }
    },

    pulseShell(shellId, n = 4) {
      for (let i = 0; i < n; i++) {
        const r = SHELL_NODE_RANGES[shellId]; if (!r) return;
        // pick an intra-shell edge involving these
        const list = [];
        for (let ei = 0; ei < edges.length; ei++) {
          const e = edges[ei];
          if (nodes[e.a].layer === 'shell' && nodes[e.a].shellIdx === SHELLS.findIndex(s => s.id === shellId) &&
              nodes[e.b].layer === 'shell' && nodes[e.b].shellIdx === SHELLS.findIndex(s => s.id === shellId)) list.push(ei);
        }
        if (list.length) spawnEdgeFire(list[(Math.random() * list.length) | 0], { intensity: 1.2 });
      }
    },

    pulseCluster(clusterId, n = 4) {
      // legacy v3 alias
      const v4 = V3_TO_V4[clusterId];
      if (v4) return this.pulseShell(v4, n);
      // satellite
      const satMap = { exit: 'sat_exit', obs: 'sat_obs', action: 'sat_action' };
      const satId = satMap[clusterId];
      if (satId) {
        const si = SATELLITES.findIndex(s => s.id === satId);
        for (let i = 0; i < n; i++) {
          const target = pickRandomNodeInShell(clusterId === 'exit' ? 'gate' : clusterId === 'obs' ? 'providers' : 'gate');
          if (target >= 0) spawnSatBeam(si, target);
        }
      }
    },

    pulseEdge(fromCluster, toCluster) {
      // Find a radial edge between mapped shells; otherwise just fire a sat beam
      const fromV4 = V3_TO_V4[fromCluster] || fromCluster;
      const toV4 = V3_TO_V4[toCluster] || toCluster;
      const fromIdx = SHELLS.findIndex(s => s.id === fromV4);
      const toIdx = SHELLS.findIndex(s => s.id === toV4);
      if (fromIdx >= 0 && toIdx >= 0) {
        // Pick a radial edge with one endpoint in each
        const list = [];
        for (let ei = 0; ei < edges.length; ei++) {
          const e = edges[ei];
          if (e.kind !== 'radial') continue;
          const aSh = nodes[e.a].shellIdx, bSh = nodes[e.b].shellIdx;
          if ((aSh === fromIdx && bSh === toIdx) || (aSh === toIdx && bSh === fromIdx)) list.push(ei);
        }
        if (list.length) spawnEdgeFire(list[(Math.random() * list.length) | 0], { intensity: 1.4 });
      }
    },

    fireTrade({ ticker, dir = 'L', exchange = 'okx', pnl = 0 } = {}) {
      // Cascade: market → providers → signals → strategy → gate → core
      const seq = ['market', 'providers', 'signals', 'strategy', 'gate'];
      let delay = 0;
      for (let i = 0; i < seq.length - 1; i++) {
        setTimeout(() => this.pulseEdge(seq[i], seq[i + 1]), delay);
        delay += 200;
      }
      // Then gate → core (the exchange's core)
      setTimeout(() => {
        // Pick a gate node and an exchange core node — find a gate-core edge
        const coreId = exchange.toLowerCase();
        const cR = CORE_NODE_RANGES[coreId];
        if (!cR) return;
        const list = [];
        for (let ei = 0; ei < edges.length; ei++) {
          const e = edges[ei];
          if (e.kind !== 'gate-core') continue;
          if (nodes[e.a].coreIdx === INNER_CORES.findIndex(c => c.id === coreId) ||
              nodes[e.b].coreIdx === INNER_CORES.findIndex(c => c.id === coreId)) list.push(ei);
        }
        if (list.length) spawnEdgeFire(list[(Math.random() * list.length) | 0], { intensity: 1.6 });
      }, delay);
      delay += 300;

      // Supernova at a core node
      setTimeout(() => {
        const coreId = exchange.toLowerCase();
        const ni = pickRandomNodeInCore(coreId);
        if (ni >= 0) supernovas.push({ nodeIdx: ni, color: pnl >= 0 ? [135, 215, 135] : [215, 135, 135], t: 0 });
        // Outbound to exit
        if (ni >= 0) {
          const exitSi = SATELLITES.findIndex(s => s.id === 'sat_exit');
          if (exitSi >= 0) spawnOutbound(ni, exitSi);
        }
      }, delay);
    },

    // Bind real entities to nodes/satellites
    attachEntities(entities) {
      // Group by target layer
      const byLayer = {};
      entities.forEach(e => {
        const layer = V3_TO_V4[e.cluster];
        if (!layer) return; // skip exit/obs/action — they're satellites
        if (!byLayer[layer]) byLayer[layer] = [];
        byLayer[layer].push(e);
      });
      Object.entries(byLayer).forEach(([shellId, ents]) => {
        const r = SHELL_NODE_RANGES[shellId]; if (!r) return;
        const idxs = [];
        for (let i = r.start; i < r.end; i++) idxs.push(i);
        ents.forEach((ent, k) => {
          const ni = idxs[k % idxs.length];
          nodes[ni].entity = ent;
          nodes[ni].importance = Math.max(nodes[ni].importance, ent.importance || 0.7);
          ENTITY_NODE[`${ent.cluster}:${ent.id}`] = ni;
        });
      });
    },

    attachRelations(relations) {
      relations.forEach(({ a, b, weight = 0.5 }) => {
        const ia = ENTITY_NODE[`${a.cluster}:${a.id}`];
        const ib = ENTITY_NODE[`${b.cluster}:${b.id}`];
        if (ia === undefined || ib === undefined) return;
        const key = ia < ib ? ia + ',' + ib : ib + ',' + ia;
        let idx = edgeMap.get(key);
        if (idx === undefined) { addEdge(ia, ib, 'rel'); idx = edgeMap.get(key); }
        if (idx !== undefined) edges[idx].strength = Math.max(edges[idx].strength, weight);
      });
    },

    // Bind real AI modules to satellites (called by CLI later)
    bindAIModules({ high = [], mid = [], low = [], tools = [] } = {}) {
      const apply = (tier, list) => {
        const sats = SATELLITES.filter(s => s.tier === tier);
        list.forEach((m, k) => {
          if (k >= sats.length) return;
          sats[k].name = m.name || sats[k].name;
          sats[k].id = m.id || sats[k].id;
          if (m.weight !== undefined) sats[k].weight = m.weight;
          if (m.color) sats[k].color = m.color;
        });
      };
      apply('high', high);
      apply('mid', mid);
      apply('low', low);
      apply('tool', tools);
    },

    highlightChain(nodeIdx) {
      chainNodes.clear(); chainEdges.clear();
      if (nodeIdx < 0 || nodeIdx >= N_TOTAL) { chainActive = false; return; }
      chainActive = true;
      chainNodes.add(nodeIdx);

      // Build adjacency
      const adj = new Map();
      edges.forEach((e, ei) => {
        if (!adj.has(e.a)) adj.set(e.a, []);
        if (!adj.has(e.b)) adj.set(e.b, []);
        adj.get(e.a).push({ ei, other: e.b });
        adj.get(e.b).push({ ei, other: e.a });
      });

      // Walk outward (toward outer shells) and inward (toward core)
      const seedNode = nodes[nodeIdx];
      const seedShell = seedNode.layer === 'shell' ? seedNode.shellIdx : SHELLS.length;
      // Outer walk: shell-1, shell-2, ... shell-(SHELLS.length-1) reverse
      const outerSeq = [];
      for (let s = seedShell - 1; s >= 0; s--) outerSeq.push({ kind: 'shell', shellIdx: s });
      const innerSeq = [];
      for (let s = seedShell + 1; s < SHELLS.length; s++) innerSeq.push({ kind: 'shell', shellIdx: s });
      innerSeq.push({ kind: 'core' });

      function walk(seq) {
        let frontier = [nodeIdx];
        for (const target of seq) {
          let bestEdge = -1, bestNode = -1, bestStr = -1;
          for (const f of frontier) {
            const links = adj.get(f) || [];
            for (const { ei, other } of links) {
              const o = nodes[other];
              const match = (target.kind === 'shell' && o.layer === 'shell' && o.shellIdx === target.shellIdx)
                         || (target.kind === 'core' && o.layer === 'core');
              if (!match) continue;
              const s = (edges[ei].kind === 'radial' || edges[ei].kind === 'gate-core' ? 1.0 : 0) + edges[ei].strength;
              if (s > bestStr) { bestStr = s; bestEdge = ei; bestNode = other; }
            }
          }
          if (bestNode < 0) break;
          chainNodes.add(bestNode); chainEdges.add(bestEdge);
          frontier = [bestNode];
        }
      }
      walk(outerSeq);
      walk(innerSeq);

      // Animate firing along chain edges
      [...chainEdges].forEach((ei, k) => {
        setTimeout(() => spawnEdgeFire(ei, { intensity: 1.6 }), k * 100);
      });
    },
    clearChain() { chainNodes.clear(); chainEdges.clear(); chainActive = false; },
    isChainActive() { return chainActive; },

    _getNode(i) { return nodes[i]; },
    _entityNodeIdx: ENTITY_NODE,

    // WS bridge
    connectWS(url = 'ws://localhost:7777') {
      try {
        const ws = new WebSocket(url);
        ws.onmessage = (ev) => {
          try {
            const m = JSON.parse(ev.data);
            if (m.type === 'regime') this.setRegime(m.value);
            else if (m.type === 'nsi') this.setNSI(m.value);
            else if (m.type === 'pulse') this.pulseEdge(m.from, m.to);
            else if (m.type === 'cluster') this.pulseCluster(m.cluster, m.n);
            else if (m.type === 'trade') this.fireTrade(m);
            else if (m.type === 'kill') this.setKillSwitch(m.value);
            else if (m.type === 'bind_ai') this.bindAIModules(m.modules);
          } catch (e) { console.warn('bad msg', e); }
        };
        ws.onopen = () => console.log('[PolarisCloud v4] WS connected', url);
        return ws;
      } catch (e) { console.warn('WS connect failed', e); return null; }
    },

    stats() {
      return {
        nodes: N_TOTAL,
        shells: SHELLS.length,
        cores: INNER_CORES.length,
        satellites: SATELLITES.length,
        edges: edges.length,
        firing: firing.length,
      };
    },
  };
})();
