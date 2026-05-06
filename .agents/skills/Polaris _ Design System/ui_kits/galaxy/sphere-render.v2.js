/* Polaris Neural Cloud — spherical 3D node cloud
 *
 * - 320 nodes distributed on/in a sphere via Fibonacci lattice + radial jitter
 * - Soft auto-rotation (yaw + slight pitch wobble) for that "movie" feel
 * - All nodes ALWAYS rendered (faint dots). Only EDGES light up: a sparse
 *   spanning network is precomputed (k-NN), but at any instant only a small
 *   subset is "firing" — that subset glows + sends a lightning pulse along
 *   the edge, then fades.
 * - Cluster colors are assigned by node-id stripe, but the cloud is one shape.
 */

(function () {
  const canvas = document.getElementById('sphere');
  const ctx = canvas.getContext('2d');

  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0, CX = 0, CY = 0;
  function fit() {
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    CX = W / 2;
    CY = H / 2;
  }
  window.addEventListener('resize', fit);
  fit();

  // ── Cluster palette (used to color individual nodes; cloud is one shape)
  const CLUSTERS = [
    { id: 'okx', color: [0x87, 0xd7, 0xff] },
    { id: 'cap', color: [0x87, 0xaf, 0xd7] },
    { id: 'alp', color: [0xd7, 0xd7, 0x87] },
    { id: 'ai',  color: [0xd7, 0xaf, 0xff] },
    { id: 'bin', color: [0x87, 0xd7, 0x87] },
    { id: 'core',color: [0xff, 0xaf, 0x87] },
  ];

  // ── Generate nodes on a sphere (Fibonacci lattice) + radial jitter ──────
  const N = 540;
  const R = 1; // unit sphere; scaled later
  const nodes = [];
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2; // [1..-1]
    const radius = Math.sqrt(1 - y * y);
    const theta = goldenAngle * i;
    // Radial jitter so it isn't a perfect shell — gives volumetric density
    const rj = 0.92 + Math.random() * 0.08;
    const xj = Math.cos(theta) * radius * rj;
    const zj = Math.sin(theta) * radius * rj;
    const yj = y * rj;

    // Cluster assignment — use longitude bands for visual variety
    const lon = Math.atan2(zj, xj); // -π..π
    const lat = Math.asin(yj);      // -π/2..π/2
    let clusterIdx;
    if (Math.abs(lat) > 0.9) clusterIdx = 5; // poles = CORE
    else if (lon < -Math.PI * 2 / 3) clusterIdx = 0;     // OKX
    else if (lon < -Math.PI / 3)     clusterIdx = 1;     // CAP
    else if (lon < 0)                clusterIdx = 3;     // AI
    else if (lon < Math.PI / 3)      clusterIdx = 2;     // ALP
    else if (lon < Math.PI * 2 / 3)  clusterIdx = 4;     // BIN
    else                             clusterIdx = 0;     // OKX wrap

    nodes.push({
      i, x: xj, y: yj, z: zj,
      cluster: clusterIdx,
      // base intensity — most are dim; ~30% are baseline-lit
      base: Math.random() < 0.45 ? 0.55 : 0.22,
      // breathing offset
      phase: Math.random() * Math.PI * 2,
    });
  }

  // ── Build edge candidates: each node connects to its K nearest neighbors
  const K = 5;
  const edgeMap = new Set();
  const edges = [];
  for (let i = 0; i < N; i++) {
    const a = nodes[i];
    const dists = [];
    for (let j = 0; j < N; j++) {
      if (i === j) continue;
      const b = nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
      dists.push({ j, d: dx * dx + dy * dy + dz * dz });
    }
    dists.sort((p, q) => p.d - q.d);
    for (let k = 0; k < K; k++) {
      const j = dists[k].j;
      const key = i < j ? i + ',' + j : j + ',' + i;
      if (edgeMap.has(key)) continue;
      edgeMap.add(key);
      edges.push({ a: i, b: j, d: Math.sqrt(dists[k].d) });
    }
  }

  // ── Active edge / pulse system ──────────────────────────────────────────
  // active: { edgeIdx, t (0..1 along edge), life (0..1, fades 1→0), speed }
  const firing = [];
  const FIRING_TARGET = 42; // average # of edges firing at any time

  function spawnFiring() {
    // Pick an edge weighted toward longer "synapses" for cinematic feel
    const idx = (Math.random() * edges.length) | 0;
    firing.push({
      edgeIdx: idx,
      t: 0,
      life: 1,
      speed: 0.9 + Math.random() * 1.4, // pulse traversal in sec⁻¹
      decay: 0.45 + Math.random() * 0.4, // life decay rate per second after pulse done
      // Forks: 0–2 mini secondary pulses on adjacent edges, deferred
      fork: Math.random() < 0.35,
    });
  }
  // seed
  for (let i = 0; i < FIRING_TARGET; i++) spawnFiring();

  // ── Camera / projection ─────────────────────────────────────────────────
  let yaw = 0, pitch = 0.18;
  const FOV = 800; // perspective
  function project(p) {
    // Rotate yaw around Y, then pitch around X
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    let x = p.x * cy - p.z * sy;
    let z = p.x * sy + p.z * cy;
    let y = p.y;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const y2 = y * cp - z * sp;
    const z2 = y * sp + z * cp;
    // World -> screen with perspective
    const sphereR = Math.min(W, H) * 0.36;
    const camZ = 3.2; // distance from camera to sphere center, in units
    const zz = z2 + camZ;
    const f = FOV / (zz * (FOV / sphereR));
    return {
      sx: CX + x * sphereR * (FOV / (zz * FOV / sphereR)),
      sy: CY + y2 * sphereR * (FOV / (zz * FOV / sphereR)),
      depth: z2, // -1..1ish (front = positive)
    };
  }
  // Simplify: precompute scale
  function project2(p) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    let x = p.x * cy - p.z * sy;
    let z = p.x * sy + p.z * cy;
    let y = p.y;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const y2 = y * cp - z * sp;
    const z2 = y * sp + z * cp;
    const sphereR = Math.min(W, H) * 0.36;
    const persp = 1 / (1 - z2 * 0.35); // mild perspective
    return {
      sx: CX + x * sphereR * persp,
      sy: CY + y2 * sphereR * persp,
      depth: z2,
      persp,
    };
  }

  // ── Drawing ─────────────────────────────────────────────────────────────
  function rgba(c, a) {
    return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  }

  function drawCloudSphereGlow() {
    // Soft volumetric haze behind the sphere
    const r = Math.min(W, H) * 0.5;
    const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, r);
    g.addColorStop(0,   'rgba(95,135,175,0.32)');
    g.addColorStop(0.45,'rgba(95,135,175,0.10)');
    g.addColorStop(0.85,'rgba(95,135,175,0.02)');
    g.addColorStop(1,   'rgba(95,135,175,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // Subtle rim ring to read as a sphere
    ctx.save();
    ctx.strokeStyle = 'rgba(95,135,175,0.10)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(CX, CY, Math.min(W, H) * 0.36, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function drawNodes(projected, time) {
    // Sort by depth so back nodes draw first
    const order = projected.map((p, i) => i).sort((a, b) => projected[a].depth - projected[b].depth);
    for (const i of order) {
      const p = projected[i];
      const n = nodes[i];
      const cl = CLUSTERS[n.cluster];
      // Depth fade: back-side nodes are dimmer
      const depthFade = 0.35 + 0.65 * ((p.depth + 1) / 2);
      const breathe = 1 + Math.sin(time * 0.0013 + n.phase) * 0.25;
      const intensity = n.base * breathe * depthFade;
      const r = (1.0 + n.base * 1.4) * p.persp;

      // Soft halo for lit-baseline nodes
      if (n.base > 0.3 && depthFade > 0.5) {
        const haloR = r * 4;
        const hg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, haloR);
        hg.addColorStop(0, rgba(cl.color, 0.35 * intensity));
        hg.addColorStop(1, rgba(cl.color, 0));
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, haloR, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.fillStyle = rgba(cl.color, Math.min(1, intensity * 1.6));
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawDormantEdges(projected) {
    // Render every edge as a faint thread so the topology is felt — opacity
    // scaled by combined depth so the front of the sphere reads brighter.
    for (const e of edges) {
      const a = projected[e.a], b = projected[e.b];
      const avgDepth = (a.depth + b.depth) / 2; // -1..1
      const front = (avgDepth + 1) / 2; // 0..1
      const alpha = 0.04 + front * 0.10;
      ctx.strokeStyle = `rgba(135,175,215,${alpha})`;
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
  }

  // Lightning jitter: subdivide a line and offset midpoints orthogonally
  function lightningPath(ax, ay, bx, by, segments, amp) {
    const pts = [{ x: ax, y: ay }];
    const dx = bx - ax, dy = by - ay;
    const len = Math.hypot(dx, dy);
    const nx = -dy / len, ny = dx / len;
    for (let i = 1; i < segments; i++) {
      const t = i / segments;
      const ox = ax + dx * t;
      const oy = ay + dy * t;
      const j = (Math.random() - 0.5) * amp * (1 - Math.abs(t - 0.5) * 1.4);
      pts.push({ x: ox + nx * j, y: oy + ny * j });
    }
    pts.push({ x: bx, y: by });
    return pts;
  }

  function drawFiring(projected, dt, time) {
    for (const f of firing) {
      const e = edges[f.edgeIdx];
      const a = projected[e.a], b = projected[e.b];
      const n1 = nodes[e.a], n2 = nodes[e.b];
      const cl = CLUSTERS[n1.cluster];
      const cl2 = CLUSTERS[n2.cluster];
      const blendC = [
        Math.round((cl.color[0] + cl2.color[0]) / 2),
        Math.round((cl.color[1] + cl2.color[1]) / 2),
        Math.round((cl.color[2] + cl2.color[2]) / 2),
      ];

      // Synapse trunk: bright glowing line
      ctx.save();
      ctx.shadowColor = rgba(blendC, 1);
      ctx.shadowBlur = 14 * f.life;
      ctx.strokeStyle = rgba(blendC, 0.85 * f.life);
      ctx.lineWidth = 1.4 * f.life;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      ctx.restore();

      // Lightning overlay during traversal phase
      if (f.t < 1) {
        const segs = 6;
        const amp = Math.hypot(b.sx - a.sx, b.sy - a.sy) * 0.06;
        // Trunk lightning (full edge, very thin)
        const path = lightningPath(a.sx, a.sy, b.sx, b.sy, segs, amp);
        ctx.save();
        ctx.strokeStyle = `rgba(255,255,255,${0.55 * f.life})`;
        ctx.lineWidth = 0.9;
        ctx.shadowColor = '#fff';
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.moveTo(path[0].x, path[0].y);
        for (let i = 1; i < path.length; i++) ctx.lineTo(path[i].x, path[i].y);
        ctx.stroke();
        ctx.restore();

        // Pulse head (bright traveling spark)
        const t = f.t;
        const px = a.sx + (b.sx - a.sx) * t;
        const py = a.sy + (b.sy - a.sy) * t;
        ctx.save();
        ctx.shadowColor = '#fff';
        ctx.shadowBlur = 18;
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(px, py, 2.6, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      // When pulse arrives, briefly super-glow the destination node
      if (f.t >= 1 && f.t < 1 + 0.001) f.t = 1; // clamp
      if (f.t >= 1) {
        const arrival = Math.max(0, 1 - (f.t - 1) * 4);
        if (arrival > 0) {
          ctx.save();
          ctx.shadowColor = rgba(cl2.color, 1);
          ctx.shadowBlur = 30 * arrival;
          ctx.fillStyle = rgba(cl2.color, arrival);
          ctx.beginPath();
          ctx.arc(b.sx, b.sy, 5 * arrival, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = `rgba(255,255,255,${arrival})`;
          ctx.beginPath();
          ctx.arc(b.sx, b.sy, 2.2 * arrival, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      }
    }
  }

  // ── Main loop ───────────────────────────────────────────────────────────
  let last = performance.now();
  let pulsesSpawned = 0;

  function frame(now) {
    const dt = Math.min(50, now - last) / 1000; // seconds
    last = now;

    // Auto-rotate
    yaw += dt * 0.10;
    pitch = 0.18 + Math.sin(now * 0.0003) * 0.06;

    // Project all nodes once
    const projected = nodes.map(project2);

    // Advance firing
    for (let i = firing.length - 1; i >= 0; i--) {
      const f = firing[i];
      if (f.t < 1) {
        f.t += f.speed * dt;
        if (f.t > 1) {
          // Fork chance: spawn from arrival node
          if (f.fork) {
            const arrivalNode = edges[f.edgeIdx].b;
            // find a neighbor edge of arrivalNode
            const neighbors = edges
              .map((e, idx) => ({ e, idx }))
              .filter(x => x.e.a === arrivalNode || x.e.b === arrivalNode);
            if (neighbors.length) {
              const pick = neighbors[(Math.random() * neighbors.length) | 0];
              firing.push({
                edgeIdx: pick.idx,
                t: 0,
                life: 0.8,
                speed: 1.2 + Math.random() * 1.0,
                decay: 0.7,
                fork: false,
              });
            }
          }
        }
      } else {
        f.life -= f.decay * dt;
        if (f.life <= 0) firing.splice(i, 1);
      }
    }

    // Maintain target firing count
    while (firing.length < FIRING_TARGET) {
      spawnFiring();
      pulsesSpawned++;
    }
    // Occasional burst
    if (Math.random() < dt * 0.6) {
      const burst = 3 + ((Math.random() * 4) | 0);
      for (let i = 0; i < burst; i++) { spawnFiring(); pulsesSpawned++; }
    }

    // Draw
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawCloudSphereGlow();
    drawDormantEdges(projected);
    drawNodes(projected, now);
    drawFiring(projected, dt, now);

    // Counters
    document.getElementById('pulse-count').textContent = firing.filter(f => f.t < 1).length;
    document.getElementById('edge-count').textContent = firing.length;

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ── Top counters ────────────────────────────────────────────────────────
  let tickN = 12847;
  setInterval(() => {
    tickN++;
    document.getElementById('tick').textContent = tickN.toLocaleString();
    document.getElementById('sps').textContent = (12 + Math.random() * 6).toFixed(1);
  }, 1000);

  // ── Bottom ticker ───────────────────────────────────────────────────────
  const tickerLines = [
    'TRADE  router.okx  OPEN BTC-USDT-SWAP L $8,200 @ 62341.0',
    'AI     judge       gemini conviction=0.74 → ENTER  claude(critic)=PASS',
    'INFO   exit_engine TRAIL ETH-USDT-SWAP +0.59% bep_zone=Y',
    'WARN   gate.H9     soft fail dup_cooldown ticker=DOGE-USDT-SWAP',
    'TRADE  router.cap  CLOSE EURUSD S +0.06% +$3.1 reason=trail',
    'EVO    evolver     mutation Bayesian parent=tour_ai child=tour_bayes_v3',
    'INFO   cell_lrnr   incremental update +12 cells in 1h',
    'WARN   data_qa     okx ws lag=420ms (warn>300)',
    'TRADE  router.okx  CLOSE DOGE-USDT-SWAP S -2.36% -$49.6 reason=hard_stop',
    'CRIT   gate.kill   KILL switch armed reason=DD_24h>-3.5%',
  ];
  let tIdx = 0;
  function fmtTime() { return new Date().toTimeString().slice(0, 8); }
  setInterval(() => {
    tIdx = (tIdx + 1) % tickerLines.length;
    document.getElementById('ticker').textContent = `${fmtTime()}  ${tickerLines[tIdx]}`;
  }, 2200);
})();
