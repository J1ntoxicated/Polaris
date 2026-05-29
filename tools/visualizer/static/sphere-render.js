/* Polaris Neural Cloud — Pipeline-tier nested spheres + live positions
 *
 * Tier 0 (innermost) = open positions
 * Tier 1            = execution components
 * Tier 2            = decision/brain layer (strategies + providers + composer/cell)
 * Tier 3 (outer)    = market ticker shell
 *
 * Trade synapses: POS (tier 0) ↔ DEC (tier 2) — strategy fuels position
 * Pulses repeat along live synapses while trade is alive.
 * Shockwave ring on entry/exit/regime events.
 * Mouse hover → tooltip · drag → rotate sphere.
 */
(function () {
  const canvas = document.getElementById('sphere');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0, CX = 0, CY = 0;
  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    CX = W / 2; CY = H / 2;
  }
  window.addEventListener('resize', fit);
  fit();

  // Cluster palette — 14-tier pipeline (8 main sphere + 5 satellite ring + tier 4 empty)
  // A안 mixup cleanup (Jin 2026-04-27): GROUP cluster 제거 (Phase 2.5 잔재) +
  //   BRAIN sphere = AI decision results data (10 dynamic) +
  //   AI judges (10 함수) → ORBIT 'ai_judge' 위성 +
  //   EXIT_TALLY 외부 satellite (8 exit_type counts, T13)
  // Tier 4 is EMPTY (group cluster removed; index preserved for frame functions)
  const CLUSTERS = {
    pos:    [0x87, 0xd7, 0xff],  // T0 cyan       live positions (data)
    exit:   [0xff, 0x87, 0xd7],  // T1 magenta    exit types (data)
    exec:   [0x87, 0xaf, 0xd7],  // T2 blue       gates + routers (data)
    reg:    [0xd7, 0xd7, 0x87],  // T3 yellow     regime states (data, 5 only)
    // T4 empty — group cluster removed (mixup cleanup)
    strat:  [0xff, 0x9f, 0x87],  // T5 coral      strategies (data)
    brain:  [0xd7, 0xaf, 0xff],  // T6 violet     AI decision results (10 dynamic data)
    watch:  [0x87, 0xff, 0xd7],  // T7 teal       signal watchlist (data)
    mkt:    [0xff, 0xaf, 0x87],  // T8 amber      market universe (data)
    obs:    [0xd7, 0xd7, 0x87],  // T9  yellow    system health (square)
    action: [0xd7, 0x87, 0x87],  // T10 red       alert queue (square)
    orbit:  [0x9f, 0xc7, 0xff],  // T11 light blue 함수 위성 (diamond) — incl. AI judges
    axis:   [0xff, 0xd7, 0xc7],  // T12 peach     dimension axis (diamond)
    exit_tally: [0xff, 0x87, 0xaf], // T13 pink   exit_type counts (outer ring, NEW mixup)
    dec:    [0xd7, 0xaf, 0xff],  // legacy alias
  };
  const LONG_COLOR  = [0x87, 0xd7, 0x87];   // grn for long
  const SHORT_COLOR = [0xd7, 0x87, 0x87];   // red for short
  const PROFIT_COLOR = [0x87, 0xff, 0xaf];  // bright green
  const LOSS_COLOR   = [0xff, 0x87, 0x87];  // bright red
  const NEUTRAL_COLOR = [0xff, 0xff, 0xff]; // white (Jin "0이면 하얗고")

  // Exchange-tinted colors — distinct hues per exchange (Jin: 색 구분 확실히)
  const EXCHANGE_COLOR = {
    okx:    [0x5f, 0xdf, 0xff],  // bright cyan      OKX crypto
    cap:    [0xa8, 0x7c, 0xff],  // saturated purple CAP forex/indices  ← different hue from cyan
    alp:    [0xff, 0xc8, 0x4f],  // gold/amber       ALP stock          ← warmer than tier yellow
    bin:    [0x5f, 0xe0, 0x8a],  // emerald green    BIN crypto data
    other:  [0xb0, 0xb0, 0xc0],  // grey
  };

  // Muted grey for non-active background tickers (Jin: dormant 회색)
  const INACTIVE_GREY = [0xa0, 0xa4, 0xb4];

  // Cluster (tier) colors — chain segment 별 origin node cluster color 표시
  // Jin 2026-04-28 mandate: "신경망 펑션 트리거 시 출발점 노드 색으로 — layer 영향 표시"
  const CLUSTER_COLORS = {
    pos:        [0x87, 0xd7, 0xff],  // light blue (T0 live positions)
    exit:       [0xff, 0x87, 0xd7],  // pink (T1 exit patterns)
    exec:       [0x87, 0xaf, 0xd7],  // mid blue (T2 execution)
    reg:        [0xd7, 0xd7, 0x87],  // yellow (T3 regime context)
    strat:      [0xff, 0x9f, 0x87],  // orange (T5 strategies)
    brain:      [0xd7, 0xaf, 0xff],  // purple (T6 ai decisions)
    watch:      [0x87, 0xff, 0xd7],  // mint (T7 signal watch)
    mkt:        [0xff, 0xaf, 0x87],  // peach (T8 market shell)
    spot_data:  [0x00, 0xff, 0x88],  // Jin 2026-04-30: SPOT bot lime green (T12)
    exit_tally: [0xff, 0x87, 0xaf],  // rose (T13 exit tally)
  };

  function colorFor(node) {
    // POS — color by PnL (winner green / loser red / neutral grey)
    // Jin 2026-04-28 mandate "프로핏 로스 색": direction → PnL 기반 변경.
    // Shape/size 는 original (filled disc, base radius) 그대로.
    if (node.cluster === 'pos') {
      return chainColor(node.pnl_usd || 0);
    }
    // MKT / WATCH: exchange tint for ALL tradable tickers (Jin "거래 가능은 다 색")
    // alpha 는 state 따라 별도 (firing 강하게, dormant dim) — color 는 exchange tint 항상
    if (node.cluster === 'mkt' || node.cluster === 'watch') {
      if (node.exchange) {
        const ek = exchangeKey(node.exchange);
        const c = EXCHANGE_COLOR[ek];
        if (c) return c;
      }
      return INACTIVE_GREY;  // exchange 없는 rare case 만 grey
    }
    // Jin 2026-04-28 v13: STRAT — group 별 색 다르게 ("전략은 그룹별 색 다르게").
    if (node.cluster === 'strat' && node.asset_group) {
      const gk = _groupKey(node.asset_group);
      const gc = GROUP_COLORS[gk];
      if (gc) return gc;
    }
    // Jin v21: ORBIT 위성 — kind 별 색 다르게 ("위성들 색 구분 좀, 다 똑같누").
    if (node.cluster === 'orbit' && node.orbit_kind && ORBIT_KIND_COLOR[node.orbit_kind]) {
      return ORBIT_KIND_COLOR[node.orbit_kind];
    }
    // Jin 2026-04-30 Phase 4 T22: SPOT bot — lime green by kind (pos_spot / strat_spot)
    if (node.cluster === 'spot_data') {
      return SPOT_KIND_COLOR[node.kind] || CLUSTER_COLORS.spot_data;
    }
    return CLUSTERS[node.cluster] || CLUSTERS.mkt;
  }

  const STATE_BASE = {
    firing:  0.85,
    lit:     0.55,
    dormant: 0.35,   // Jin "쟤들도 콜하니까 일정 수준 기본" — 0.22 → 0.35
  };

  // ── Live data ───────────────────────────────────────────────────────────
  let nodes = [];
  let edges = [];
  let firingTickers = new Set();
  let nodeByTicker = new Map();
  let decNodes = [];

  // Persistent trade chains — DEC → REG → EXEC → POS for each live trade
  const tradeChains = new Map();
  const PULSE_INTERVAL_MS = 1400;
  const CHAIN_PULSE_SPEED = 0.8;  // Jin 2026-04-30 랙 fix: 1.2→0.8 (pulse 33% 느리게)

  // ── Galaxy backdrop — full ticker universe scattered around sphere ──────
  // Each entry: { ticker, x, y, color, baseBrightness, glow, lastSignalAt, score, direction }
  // x,y are in unit-space relative to viewport center (multiplied by sphere radius scale at draw)
  const galaxy = [];                  // ordered list
  const galaxyByTicker = new Map();   // ticker → galaxy entry
  // Comets: animated particles from galaxy → sphere on signal/entry
  const comets = [];                  // { startX, startY, endX, endY, startedAt, dur, color, ticker }

  function tickerHashColor(ticker) {
    let h = 0;
    for (let i = 0; i < (ticker || '').length; i++) {
      h = (h * 31 + ticker.charCodeAt(i)) | 0;
    }
    h = Math.abs(h);
    // Pick from cluster palette deterministically
    const palette = [CLUSTERS.pos, CLUSTERS.exit, CLUSTERS.exec,
                     CLUSTERS.reg, CLUSTERS.dec, CLUSTERS.mkt,
                     LONG_COLOR, PROFIT_COLOR];
    return palette[h % palette.length];
  }

  function buildGalaxy(universe) {
    galaxy.length = 0;
    galaxyByTicker.clear();
    if (!universe) return;
    // Deterministic PRNG from ticker hash so positions are stable across refresh
    function tickerRand(ticker, salt) {
      let h = 0;
      const s = (ticker || 'x') + '|' + salt;
      for (let i = 0; i < s.length; i++) h = (h * 1103515245 + s.charCodeAt(i) + 12345) | 0;
      return ((h >>> 0) / 0xffffffff);
    }
    for (const u of universe) {
      // Donut scatter outside sphere — radius 1.30..2.55 of sphere shell
      const ang = tickerRand(u.ticker, 'a') * Math.PI * 2;
      const rUnit = 1.30 + tickerRand(u.ticker, 'r') * 1.25;
      // Slight vertical compression for milky-way-disk feel
      const yJit = (tickerRand(u.ticker, 'y') - 0.5) * 0.6;
      // Convert to cartesian (x,y) in unit-sphere-radius space
      const x = Math.cos(ang) * rUnit;
      const y = Math.sin(ang) * rUnit + yJit;
      const entry = {
        ticker: u.ticker,
        ux: x, uy: y,                  // unit coordinates
        color: tickerHashColor(u.ticker),
        baseBrightness: Math.min(0.10, 0.04 + (u.n_24h || 0) / 200),
        glow: 0,                       // 0..1, decays each frame
        lastSignalAt: 0,
        score: 0,
        direction: null,
      };
      galaxy.push(entry);
      galaxyByTicker.set(u.ticker, entry);
    }
  }

  function drawGalaxy() {
    if (!galaxy.length) return;
    const sphereR = Math.min(W, H) * 0.36;
    for (let i = 0; i < galaxy.length; i++) {
      const g = galaxy[i];
      const px = CX + g.ux * sphereR;
      const py = CY + g.uy * sphereR;
      // Skip if offscreen (cheap clip)
      if (px < -10 || px > W + 10 || py < -10 || py > H + 10) continue;
      const total = Math.min(1, g.baseBrightness + g.glow);
      const r = 0.5 + g.glow * 1.6;
      // Halo only when signal hit
      if (g.glow > 0.18) {
        const halo = r * (4 + g.glow * 6);
        const hg = ctx.createRadialGradient(px, py, 0, px, py, halo);
        hg.addColorStop(0, rgba(g.color, 0.45 * g.glow));
        hg.addColorStop(1, rgba(g.color, 0));
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(px, py, halo, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.fillStyle = rgba(g.color, total);
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function tickGalaxy(dt) {
    // Decay glow back toward 0 — half-life ~0.8s
    const decay = Math.exp(-dt * 0.85);
    for (let i = 0; i < galaxy.length; i++) {
      if (galaxy[i].glow > 0.001) galaxy[i].glow *= decay;
      else galaxy[i].glow = 0;
    }
  }

  // Comet from T6 MKT outer shell → T0 POS sub-sphere (inward attach on entry)
  // 3D coords — re-projected each frame so comet path follows sphere rotation
  function spawnComet(ticker, score, direction, exchange) {
    const sourceNode = nodeByTicker.get(ticker);
    if (!sourceNode || sourceNode.tier !== 7) {
      // Fallback: galaxy 2D backdrop position (legacy, rare)
      const g = galaxyByTicker.get(ticker);
      if (!g) return;
      const sphereR = Math.min(W, H) * 0.36;
      const startX = CX + g.ux * sphereR;
      const startY = CY + g.uy * sphereR;
      comets.push({
        mode: '2d', startX, startY,
        endX: CX, endY: CY,
        startedAt: performance.now(), dur: 1100,
        color: directionColor(direction, g.color),
        ticker, score: Math.abs(score || 0),
      });
      return;
    }
    // 3D inward path: T6 outer ticker pos → T0 POS sub-sphere center
    const exKey = exchangeKey(exchange || sourceNode.exchange);
    const subCenter = POS_SUB_CENTERS[exKey] || [0, 0, 0];
    comets.push({
      mode: '3d',
      startNode: sourceNode,
      endX3: subCenter[0], endY3: subCenter[1], endZ3: subCenter[2],
      startedAt: performance.now(),
      dur: 1300,
      color: directionColor(direction, sourceNode && colorFor(sourceNode)),
      ticker, score: Math.abs(score || 0),
    });
  }

  function directionColor(direction, fallback) {
    const d = (direction || '').toLowerCase();
    if (d === 'short') return LOSS_COLOR;
    if (d === 'long') return PROFIT_COLOR;
    return fallback || NEUTRAL_COLOR;
  }

  function drawComets(now) {
    for (let i = comets.length - 1; i >= 0; i--) {
      const c = comets[i];
      const t = (now - c.startedAt) / c.dur;
      // Resolve start/end screen coords — 3D mode re-projects each frame
      let sx, sy, ex, ey;
      if (c.mode === '3d') {
        const sNode = c.startNode;
        const sP = { sx: 0, sy: 0, depth: 0, persp: 1 };
        const eP = { sx: 0, sy: 0, depth: 0, persp: 1 };
        projectInto({ x: sNode.x, y: sNode.y, z: sNode.z }, sP);
        projectInto({ x: c.endX3, y: c.endY3, z: c.endZ3 }, eP);
        sx = sP.sx; sy = sP.sy; ex = eP.sx; ey = eP.sy;
      } else {
        sx = c.startX; sy = c.startY; ex = c.endX; ey = c.endY;
      }
      if (t >= 1) {
        spawnShock(ex, ey, c.color, 'expand');
        const n = nodeByTicker.get(c.ticker);
        if (n) flashFire(c.ticker, 1800);
        comets.splice(i, 1);
        continue;
      }
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const px = sx + (ex - sx) * eased;
      const py = sy + (ey - sy) * eased;
      const trailFrac = 0.25;
      const tx = sx + (ex - sx) * Math.max(0, eased - trailFrac);
      const ty = sy + (ey - sy) * Math.max(0, eased - trailFrac);
      // Skip render if both endpoints behind sphere (3D mode)
      if (c.mode === '3d') {
        const sNode = c.startNode;
        const sP = projectedCache[sNode.i];
        if (sP && sP.depth < -0.3 && eased < 0.3) continue;
      }
      ctx.save();
      const grad = ctx.createLinearGradient(tx, ty, px, py);
      grad.addColorStop(0, rgba(c.color, 0));
      grad.addColorStop(1, rgba(c.color, 0.85));
      ctx.strokeStyle = grad;
      ctx.lineWidth = 1.5;
      ctx.shadowColor = rgba(c.color, 0.9);
      ctx.shadowBlur = 12;
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(px, py);
      ctx.stroke();
      // Head spark
      ctx.shadowColor = '#fff';
      ctx.shadowBlur = 8;  // Jin 2026-04-30 랙 fix: 16→8
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.arc(px, py, 2.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  function chainColor(pnl_usd) {
    // Jin 2026-04-28: 명확한 winner/loser 표시 (threshold 0 — any positive=green, any negative=red).
    // 이전 ±$0.5 threshold = 작은 PnL trade 들 모두 NEUTRAL grey 보여 white-ish.
    const v = pnl_usd || 0;
    if (v > 0)   return PROFIT_COLOR;
    if (v < 0)   return LOSS_COLOR;
    return NEUTRAL_COLOR;  // exactly 0 (just opened, no PnL yet)
  }

  // ── Tiered geometry — 14 tiers (8 main + 5 external ring + tier 4 empty)
  // A안 cleanup (Jin 2026-04-27): GROUP 제거 (T4 empty), EXIT_TALLY 추가 (T13)
  // Main 8 (POS · EXIT · EXEC · REG · - · STRAT · BRAIN · WATCH · MKT)
  // Outer 5: T9 OBS · T10 ACTION · T11 ORBIT · T12 AXIS · T13 EXIT_TALLY
  // T4 radius 보존 (frame functions 인덱스 호환), 0 nodes 라 visible 0
  const TIER_RADIUS = [0.115, 0.215, 0.32, 0.42, 0.515, 0.61, 0.71, 0.81, 0.91, 1.10, 1.22, 1.35, 1.50, 1.62];
  // Jin 2026-04-28 v13: intra-tier K-NN ambient edges 모두 제거 — 사용자 보고
  // "링크 그냥 지금 문어발식". Logical chain (티커→시그널→전략→포지션) 만
  // 남기고 ambient positional edges 제거. trade_chains (active trade) +
  // drawRadialConnections (cross-tier ghost) + drawRelationships (persistent)
  // 가 logical chain 시각화 담당.
  const TIER_K =      [0,     0,     0,    0,    0,     0,    0,    0,    0,    0,    0,    0,    0,    0   ];
  // Jin 2026-04-28 v3: T5 STRAT 1.0 → 0.6 (MAX 2.5 절반 후 firing 23/23 cap
  // saturate 재발 → "다 같음" 사용자 보고). 0.6 으로 추가 좁히면 firing
  // sm 1.0 → (0.4+0.85²×5)×0.6 = 2.41 (no cap), sm 1.5 → 3.61 → cap 2.5
  // (winner 만 saturate). lit 0.7×base 1.91×0.6 = 1.20 (no cap, varied).
  // dormant 0.4×base 1.01×0.6 = 0.36 (no cap, tiny).
  // Jin 2026-04-28 v15: T2 EXEC 1.0 → 1.4 ("게이트들 다 너무 쩜" 보고). gate
  // 노드 dormant 도 visible.
  const TIER_SIZE  =  [3.0,   1.4,   1.4,  0.95, 1.2,   0.6,  0.85, 0.55, 0.55, 0.95, 1.00, 0.85, 0.85, 0.95];
  // T9 OBS / T10 ACTION / T11 ORBIT (function 위성) / T12 AXIS (dimension 위성) / T13 EXIT_TALLY (외부)

  // Jin 2026-04-27 dynamic-size cap: tier 별 max radius (px). size_mul × tierBoost × baseEff
  // 가 어떻게 폭주해도 cap 적용. 외부 위성 너무 크면 layers 가림 → 작은 cap.
  // Jin 2026-04-28 v2: T5/T6 5.5 → 4.0 좁힘 (cap saturate 14/20 firing 해소).
  // 기존 5.5 px 가 firing+sm 1.5+ 모든 winner strategy → 동일 cap = 균일 거대.
  // 4.0 으로 좁히면 sm 1.0 (n_trades 75) firing → 5.6 → cap 4.0,
  // sm 1.5 (n_trades 150+) firing → 8.4 → cap 4.0 (여전히 cap 이지만
  // visual span 감소). lit (state_f 0.7) sm 1.05 → 5.9 → cap 4.0,
  // state factor 가 cap 영향 안 받는 영역 (sm < 0.95) 에서 차별 살림.
  const MAX_NODE_RADIUS_PER_TIER = [
    10.0, // T0  POS         (heart, 가장 큰 cap)  Jin 2026-05-02 +1.25x
    6.5,  // T1  EXIT
    6.5,  // T2  EXEC
    6.0,  // T3  REG
    7.0,  // T4  (empty, group cluster 제거)
    3.2,  // T5  STRAT       (v3 절반 정책 유지, 1.25x boost)
    6.5,  // T6  BRAIN
    6.0,  // T7  WATCH
    4.5,  // T8  MKT         (dot cloud)
    4.5,  // T9  OBS
    4.5,  // T10 ACTION
    4.5,  // T11 ORBIT
    4.0,  // T12 AXIS
    4.5,  // T13 EXIT_TALLY
  ];

  // Reusable projection buffer — mutate in place to avoid GC churn
  let projBuf = null;
  function ensureProjBuf(n) {
    if (!projBuf || projBuf.length !== n) {
      projBuf = new Array(n);
      for (let i = 0; i < n; i++) projBuf[i] = { sx: 0, sy: 0, depth: 0, persp: 1 };
    }
  }
  function projectInto(p, out) {
    // Jin 2026-04-28 chain drift REMOVED — "정신없음" 보고 (v6 radial fix
    // 후에도 align 끌림 너무 강함). projectInto 가 base position 직접 사용.
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const x = p.x * cy - p.z * sy;
    const z = p.x * sy + p.z * cy;
    const y = p.y;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const y2 = y * cp - z * sp;
    const z2 = y * sp + z * cp;
    const sphereR = Math.min(W, H) * 0.36 * zoom;
    const persp = 1 / (1 - z2 * 0.22);  // 0.35 → 0.22 (밖으로 안 튀어나옴)
    out.sx = CX + x * sphereR * persp;
    out.sy = CY + y2 * sphereR * persp;
    out.depth = z2;
    out.persp = persp;
  }

  // POS tier 0 sub-cluster centers per exchange — 3 mini-spheres inside main sphere
  // Jin 2026-04-27: 더 prominent 분리 (3 colony 시각적 명확)
  const POS_SUB_CENTERS = {
    okx:    [-0.115, -0.05,  0.00],   // left colony (cyan)
    cap:    [ 0.115, -0.05,  0.00],   // right colony (purple)
    alp:    [ 0.00,   0.110, 0.00],   // top colony (gold)
    bin:    [ 0.00,  -0.115, 0.00],   // bottom (emerald)
    other:  [ 0.00,   0.00,  0.00],
  };
  const POS_SUB_RADIUS = 0.060;   // 더 tight cluster — colony 분리 명확

  function exchangeKey(ex) {
    const m = (ex || '').toLowerCase();
    if (m === 'okx') return 'okx';
    if (m === 'cap' || m === 'capital') return 'cap';
    if (m === 'alp' || m === 'alpaca') return 'alp';
    if (m === 'bin' || m === 'binance') return 'bin';
    return 'other';
  }

  // T8 MKT azimuth-based grouping: each exchange occupies a sector on the shell (Phase 2.5)
  // → activity on one exchange illuminates that whole sphere region distinctly
  const MKT_SECTORS = {
    okx:    [-Math.PI,          -Math.PI * 0.5],  // back-left  (180° wedge)
    cap:    [-Math.PI * 0.5,     0.0],            // front-left
    alp:    [ 0.0,               Math.PI * 0.5],  // front-right
    bin:    [ Math.PI * 0.5,     Math.PI],        // back-right
    other:  null,                                  // fill polar caps (y>0.85, y<-0.85)
  };

  // Jin 2026-04-28 — Asset group sectors (equal 60° 골고루).
  // MKT (T8) + WATCH (T7) + STRAT (T5) + POS (T0) 가 같은 group 영역에 colocate
  // → ticker→strategy lineage vertical column. Jin "편중 안되게 골고루" mandate
  // → equal width fixed (count 비례 X).
  //
  // Jin 2026-04-28 v12 — exchange interleave 순서. 이전 [crypto, forex,
  // indices, commodity, stock, etf] 는 OKX(crypto) + CAP(forex/indices/
  // commodity) 4 group 인접 → sphere 한쪽 몰림. alpaca(stock/etf) 가 반대편.
  // 새 order [crypto, stock, forex, etf, indices, commodity]:
  //   OKX → alpaca+cap → CAP → alpaca → CAP → CAP — exchange 섞임.
  const TWO_PI = Math.PI * 2;
  const GROUP_ORDER = ['crypto', 'stock', 'forex', 'etf', 'indices', 'commodity'];
  const GROUP_SECTORS = {
    crypto:    [-Math.PI,                 -Math.PI + TWO_PI * (1/6)],
    stock:     [-Math.PI + TWO_PI * (1/6), -Math.PI + TWO_PI * (2/6)],
    forex:     [-Math.PI + TWO_PI * (2/6), -Math.PI + TWO_PI * (3/6)],
    etf:       [-Math.PI + TWO_PI * (3/6), -Math.PI + TWO_PI * (4/6)],
    indices:   [-Math.PI + TWO_PI * (4/6), -Math.PI + TWO_PI * (5/6)],
    commodity: [-Math.PI + TWO_PI * (5/6),  Math.PI],
    unknown:   null,                                  // → polar caps fallback
  };
  // Subtle group cloud tints (drawGroupCloud — 0.04~0.08 alpha radial gradient).
  const GROUP_COLORS = {
    crypto:    [255, 170,  85],   // amber/gold
    forex:     [ 90, 130, 255],   // blue
    indices:   [180, 110, 255],   // purple
    commodity: [220, 130,  90],   // brown/red
    stock:     [ 90, 200, 130],   // green
    etf:       [ 90, 210, 230],   // cyan
  };
  // Jin 2026-04-28 — DB asset_group label alias. 'index' → 'indices',
  // 'micro' → 'crypto' (microcap crypto) — polar caps 안 가게 정상 group 매핑.
  const GROUP_ALIAS = {
    'index': 'indices',
    'micro': 'crypto',
    'shares': 'stock',
  };
  function _groupKey(g) {
    let k = (g || '').toLowerCase();
    if (GROUP_ALIAS[k]) k = GROUP_ALIAS[k];
    return GROUP_SECTORS[k] !== undefined ? k : 'unknown';
  }
  function _groupCenterTheta(g) {
    const sec = GROUP_SECTORS[_groupKey(g)];
    if (!sec) return 0;
    return (sec[0] + sec[1]) * 0.5;
  }

  // Jin 2026-04-28 — MKT (T8) primary placement = asset_group azimuth (was
  // exchange). 같은 그룹 ticker 들이 같은 sphere 영역 모임 → group cloud color
  // 도 같은 영역 → ticker→watch→strategy→position chain 시각 명확.
  // Exchange 정보는 colorFor() (PROFIT/exchange tint) 로 별도 표시 보존.
  function regroupMktByGroup(nodeList, geomArr) {
    // Jin 2026-04-28 v13 — soft group blend. 이전 strict 60° sector "사과
    // 깎다 말다" 보고 → sphere 전체 fibonacci + 30% group center bias.
    const allMkt = [];
    for (const n of nodeList) {
      if (n.tier === 8) allMkt.push(n);
    }
    const N = allMkt.length;
    if (!N) return;
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const R = TIER_RADIUS[8];
    const BLEND = 0.30;   // 30% group bias, 70% sphere-wide spread
    for (let i = 0; i < N; i++) {
      const n = allMkt[i];
      // 1) Default fibonacci (sphere 전체 균일).
      const yu = 1 - (i / Math.max(N - 1, 1)) * 2;
      const ringR = Math.sqrt(Math.max(0, 1 - yu * yu));
      const theta = goldenAngle * i;
      let x = Math.cos(theta) * ringR;
      let y = yu;
      let z = Math.sin(theta) * ringR;
      // 2) Group center bias (같은 group 노드들이 살짝 끌림).
      const grp = _groupKey(n.asset_group);
      const sec = GROUP_SECTORS[grp];
      if (sec) {
        const centerTheta = (sec[0] + sec[1]) * 0.5;
        const bx = Math.cos(centerTheta);
        const bz = Math.sin(centerTheta);
        x = x * (1 - BLEND) + bx * BLEND;
        y = y * (1 - BLEND);             // 적도 쪽으로 살짝
        z = z * (1 - BLEND) + bz * BLEND;
        const mag = Math.sqrt(x * x + y * y + z * z) || 1;
        x /= mag; y /= mag; z /= mag;     // sphere surface 정규화
      }
      const nid = n.id;
      const rj = 0.94 + _seedRand(nid, 'mkt_rj') * 0.06;
      geomArr[n.i] = {
        x: x * R * rj,
        y: y * R * rj,
        z: z * R * rj,
        phase: _seedRand(nid, 'mkt_phase') * Math.PI * 2,
      };
    }
  }

  // Legacy exchange-based MKT placement — preserved as fallback (toggle if
  // group placement causes layout regression). Currently unused.
  function regroupMktTier(nodeList, geomArr) {
    const groups = { okx: [], cap: [], alp: [], bin: [], other: [] };
    for (const n of nodeList) {
      if (n.tier !== 8) continue;
      groups[exchangeKey(n.exchange)].push(n);
    }
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const R = TIER_RADIUS[8];
    for (const [key, members] of Object.entries(groups)) {
      if (!members.length) continue;
      const N = members.length;
      if (key === 'other') {
        // Polar caps — split top/bottom
        for (let i = 0; i < N; i++) {
          const isTop = i % 2 === 0;
          const t = i / Math.max(N - 1, 1);
          const y = isTop ? 0.86 + t * 0.13 : -0.86 - t * 0.13;
          const yc = Math.max(-0.99, Math.min(0.99, y));
          const radius = Math.sqrt(Math.max(0, 1 - yc * yc));
          const theta = goldenAngle * i;
          const nid = members[i].id;
          geomArr[members[i].i] = {
            x: Math.cos(theta) * radius * R * (0.94 + _seedRand(nid, 'mkt_other_rj1') * 0.06),
            y: yc * R,
            z: Math.sin(theta) * radius * R * (0.94 + _seedRand(nid, 'mkt_other_rj2') * 0.06),
            phase: _seedRand(nid, 'mkt_other_phase') * Math.PI * 2,
          };
        }
      } else {
        const [aMin, aMax] = MKT_SECTORS[key];
        const sectorWidth = aMax - aMin;
        for (let i = 0; i < N; i++) {
          // y from -0.85..0.85 (leave polar caps for 'other')
          const y = -0.85 + (i / Math.max(N - 1, 1)) * 1.70;
          const radius = Math.sqrt(Math.max(0, 1 - y * y));
          // theta within sector — interleaved Fibonacci so points spread within sector
          const fracInSector = ((goldenAngle * i) % (Math.PI * 2)) / (Math.PI * 2);
          const theta = aMin + sectorWidth * fracInSector;
          const nid = members[i].id;
          const rj = 0.94 + _seedRand(nid, 'mkt_rj') * 0.06;
          geomArr[members[i].i] = {
            x: Math.cos(theta) * radius * R * rj,
            y: y * R,
            z: Math.sin(theta) * radius * R * rj,
            phase: _seedRand(nid, 'mkt_phase') * Math.PI * 2,
          };
        }
      }
    }
  }

  // WATCH tier (Phase 2.5: T7) — each watching ticker positioned inward from its MKT (T8) counterpart
  // → visual continuity: MKT outer ticker → WATCH same-azimuth inward
  function regroupWatchByMkt(nodeList, geomArr) {
    const mktByTicker = new Map();
    for (const n of nodeList) {
      if (n.tier === 8 && n.ticker) {
        if (!mktByTicker.has(n.ticker)) mktByTicker.set(n.ticker, n);
      }
    }
    const baseRatio = TIER_RADIUS[7] / TIER_RADIUS[8];
    for (const n of nodeList) {
      if (n.tier !== 7 || !n.ticker) continue;
      const mktN = mktByTicker.get(n.ticker);
      if (!mktN) continue;
      const m = geomArr[mktN.i];
      if (!m) continue;
      // Jin: WATCH score 강할수록 inner (radial_offset 음수)
      const offset = (typeof n.radial_offset === 'number') ? n.radial_offset : 0;
      const ratio = baseRatio + offset;
      geomArr[n.i] = {
        x: m.x * ratio,
        y: m.y * ratio,
        z: m.z * ratio,
        phase: _seedRand(n.id, 'watch_mkt_phase') * Math.PI * 2,
      };
    }
  }

  // Jin 2026-04-27: Active WATCH 를 POS 주위 ring 으로 envelope (감싸는 형태)
  // dormant WATCH 는 outer (regroupWatchByMkt 처리) 유지
  function regroupWatchAroundPos(nodeList, geomArr) {
    const posByTicker = new Map();
    for (const n of nodeList) {
      if (n.tier === 0 && n.ticker && n.state === 'firing') {
        if (!posByTicker.has(n.ticker)) posByTicker.set(n.ticker, n);
      }
    }
    if (posByTicker.size === 0) return;
    const RING_R = 0.185;            // 감싸는 ring radius (POS sub-radius 0.060 outer = ~0.175)
    const TANGENTIAL = 0.028;        // ring 안 scatter 폭
    let scatterIdx = 0;
    for (const n of nodeList) {
      if (n.tier !== 7 || !n.ticker) continue;     // Phase 2.5: WATCH was T6
      if (n.state === 'dormant') continue;     // active only
      const posN = posByTicker.get(n.ticker);
      if (!posN) continue;
      const p = geomArr[posN.i];
      if (!p) continue;
      const len = Math.sqrt(p.x*p.x + p.y*p.y + p.z*p.z) || 0.01;
      const ux = p.x / len, uy = p.y / len, uz = p.z / len;
      // tangential scatter for ring distinction
      const angle = (scatterIdx++ * 1.618) % (Math.PI * 2);
      // Build orthogonal basis to (ux,uy,uz) for tangent plane scatter
      const refX = Math.abs(ux) < 0.9 ? 1 : 0;
      const refY = Math.abs(ux) < 0.9 ? 0 : 1;
      const t1x = uy * 0 - uz * refY;
      const t1y = uz * refX - ux * 0;
      const t1z = ux * refY - uy * refX;
      const t1len = Math.sqrt(t1x*t1x + t1y*t1y + t1z*t1z) || 1;
      const t2x = uy * (t1z/t1len) - uz * (t1y/t1len);
      const t2y = uz * (t1x/t1len) - ux * (t1z/t1len);
      const t2z = ux * (t1y/t1len) - uy * (t1x/t1len);
      const dx = (t1x/t1len) * Math.cos(angle) + t2x * Math.sin(angle);
      const dy = (t1y/t1len) * Math.cos(angle) + t2y * Math.sin(angle);
      const dz = (t1z/t1len) * Math.cos(angle) + t2z * Math.sin(angle);
      geomArr[n.i] = {
        x: ux * RING_R + dx * TANGENTIAL,
        y: uy * RING_R + dy * TANGENTIAL,
        z: uz * RING_R + dz * TANGENTIAL,
        phase: _seedRand(n.id, 'watch_pos_phase') * Math.PI * 2,
      };
    }
  }

  // Jin 2026-04-28 v13 — STRAT (T5) soft group blend (MKT 와 동일 logic).
  function regroupStratByGroup(nodeList, geomArr) {
    const allStrat = [];
    for (const n of nodeList) {
      if (n.tier === 5) allStrat.push(n);
    }
    const N = allStrat.length;
    if (!N) return;
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const R = TIER_RADIUS[5];
    const BLEND = 0.30;
    for (let i = 0; i < N; i++) {
      const n = allStrat[i];
      const yu = 1 - (i / Math.max(N - 1, 1)) * 2;
      const ringR = Math.sqrt(Math.max(0, 1 - yu * yu));
      const theta = goldenAngle * i;
      let x = Math.cos(theta) * ringR;
      let y = yu;
      let z = Math.sin(theta) * ringR;
      const grp = _groupKey(n.asset_group);
      const sec = GROUP_SECTORS[grp];
      if (sec) {
        const centerTheta = (sec[0] + sec[1]) * 0.5;
        const bx = Math.cos(centerTheta);
        const bz = Math.sin(centerTheta);
        x = x * (1 - BLEND) + bx * BLEND;
        y = y * (1 - BLEND);
        z = z * (1 - BLEND) + bz * BLEND;
        const mag = Math.sqrt(x * x + y * y + z * z) || 1;
        x /= mag; y /= mag; z /= mag;
      }
      const nid = n.id;
      const rj = 0.94 + _seedRand(nid, 'strat_rj') * 0.06;
      geomArr[n.i] = {
        x: x * R * rj,
        y: y * R * rj,
        z: z * R * rj,
        phase: _seedRand(nid, 'strat_phase') * Math.PI * 2,
      };
    }
  }

  function regroupPosTier(nodeList, geomArr) {
    // Jin 2026-04-28 v12 revert — POS 는 exchange-별 sub-sphere colony 복원.
    // 사용자 mandate "포지션은 익스체인지별 그룹핑 스피어 하랑께 왜 저걸 없애놨냐".
    // MKT 표면 만 group cloud. POS (heart) 는 exchange colony 그대로.
    const groups = { okx: [], cap: [], alp: [], bin: [], other: [] };
    for (const n of nodeList) {
      if (n.tier !== 0) continue;
      const k = exchangeKey(n.exchange);
      groups[k].push(n);
    }
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    for (const key of Object.keys(groups)) {
      const members = groups[key];
      const c = POS_SUB_CENTERS[key];
      if (!c) continue;
      const N = members.length;
      const thetaOffset = (key.charCodeAt(0) || 0) * 0.83;
      for (let i = 0; i < N; i++) {
        const y = N === 1 ? 0 : 1 - (i / Math.max(N - 1, 1)) * 2;
        const radius = Math.sqrt(Math.max(0, 1 - y * y));
        const theta = goldenAngle * i + thetaOffset;
        const idx = members[i].i;
        const nid = members[i].id;
        geomArr[idx] = {
          x: c[0] + Math.cos(theta) * radius * POS_SUB_RADIUS,
          y: c[1] + y * POS_SUB_RADIUS,
          z: c[2] + Math.sin(theta) * radius * POS_SUB_RADIUS,
          phase: _seedRand(nid, 'pos_phase') * Math.PI * 2,
        };
      }
    }
  }

  // Deterministic per-node PRNG — reload-stable placement (Jin 2026-04-27)
  // node.id + salt → [0,1). Same id+salt = same value forever.
  // FNV-1a 32-bit hash; output bucketed to 10000 for slight quantization.
  // ANIMATION random (lightning fork, ambient spark) NOT replaced — frame variance is intentional.
  function _seedRand(nodeId, salt) {
    let h = 2166136261;
    const s = (nodeId || 'x') + '|' + (salt || '');
    for (let i = 0; i < s.length; i++) {
      h = (h * 16777619) ^ s.charCodeAt(i);
    }
    return ((h >>> 0) % 10000) / 10000;
  }

  function buildGeometryTiered(nodeList) {
    // 14-tier (8 main spheres + 5 satellite ring: T9 OBS / T10 ACTION / T11 ORBIT / T12 AXIS / T13 EXIT_TALLY)
    // A안 cleanup (Jin 2026-04-27): GROUP 제거 (T4 empty), EXIT_TALLY 추가 (T13)
    const tiers = [[], [], [], [], [], [], [], [], [], [], [], [], [], []];
    for (const n of nodeList) {
      const t = (typeof n.tier === 'number') ? Math.min(13, n.tier) : 8;
      tiers[t].push(n);
    }
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const out = new Array(nodeList.length);
    for (let t = 0; t < tiers.length; t++) {
      const grp = tiers[t];
      const N = grp.length;
      if (!N) continue;
      const R = TIER_RADIUS[t];
      // Tier 9 OBS / 10 ACTION / 13 EXIT_TALLY: equatorial-band ring (정지 satellite)
      // Each tier in own latitude band so they don't overlap
      if (t === 9 || t === 10 || t === 13) {
        let yBand = 0.18;
        if (t === 10) yBand = -0.18;
        else if (t === 13) yBand = 0.50;       // higher band, distinct from OBS/ACTION
        for (let i = 0; i < N; i++) {
          const theta = (i / N) * Math.PI * 2 + (t === 10 ? Math.PI / N : (t === 13 ? Math.PI / (2 * N) : 0));
          const nid = grp[i].id;
          const cy = yBand + (_seedRand(nid, 'ring_yjit') - 0.5) * 0.10;
          const ringR = Math.sqrt(Math.max(0, 1 - cy * cy)) * R;
          const bx = Math.cos(theta) * ringR;
          const by = cy * R;
          const bz = Math.sin(theta) * ringR;
          out[grp[i].i] = {
            x: bx, y: by, z: bz,
            phase: _seedRand(nid, 'ring_phase') * Math.PI * 2,
            // Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 base — Rodrigues rotation
            _basePos: { x: bx, y: by, z: bz },
          };
        }
        continue;
      }
      // Tier 11 ORBIT (함수 위성) / 12 AXIS (차원 위성): 회전 위성
      // Jin: "레이어 사이 펑션들은 정지위성일 필요 X"
      // → orbit kind 별 다른 latitude + 회전 (frame 마다 updateSatelliteRotation 으로 갱신)
      // Phase 1 (Jin 2026-04-27): T11 ORBIT 은 outer ring 대신 inter-tier mid radius 사용
      //   n.inter_radius 가 있으면 그 값으로 sphere 사이 gap 에 위치 (kind 별 매핑)
      //   AXIS (T12) 는 outer ring (TIER_RADIUS[12]=1.50) 그대로 유지
      if (t === 11 || t === 12) {
        // Per-kind latitude band — Jin 2026-04-27 (위성 layer 시각 분리):
        //   카테고리별 다른 latitude → 같은 inter-tier gap 안에서도 layer 들이
        //   "ring 하나" 로 보이지 않고 위/아래 분리된 면 위에서 회전.
        //   ai_judge: 적도 0.00 (BRAIN sphere 인근 가장 visible)
        const ORBIT_LAT = {
          exit_engine:   0.55,   // 북반구 high
          exec_tool:     0.30,   // 북반구 mid
          sensor:        0.10,   // 북반구 low
          regime_infra: -0.10,   // 남반구 low
          learner:      -0.30,   // 남반구 mid
          ai_judge:      0.00,   // 적도 (가장 visible)
          brain_tool:   -0.50,   // 남반구 deep
          provider:      0.45,   // 북반구 mid-high
          unknown:       0.0,
        };
        // Phase 2.5: group axis 제거 — session × liq × crisis 만
        const AXIS_LAT = {
          session:  0.52,
          liq:      0.0,  crisis: -0.62,
          unknown:  0.0,
        };
        for (let i = 0; i < N; i++) {
          const n = grp[i];
          let yBand = 0;
          if (t === 11) yBand = ORBIT_LAT[n.orbit_kind] || 0;
          else yBand = AXIS_LAT[n.axis_kind] || 0;
          // Phase 1: ORBIT inter-tier radius 우선, 없으면 outer ring fallback
          const nodeR = (t === 11 && typeof n.inter_radius === 'number') ? n.inter_radius : R;
          // Spread within same kind: tiny offset by index within kind
          const baseTheta = (i / N) * Math.PI * 2 + (t === 12 ? Math.PI / N : 0);
          const cy = yBand + (i % 3 - 1) * 0.04;        // ±4% scatter
          const ringR = Math.sqrt(Math.max(0, 1 - cy * cy)) * nodeR;
          const bx = Math.cos(baseTheta) * ringR;
          const by = cy * nodeR;
          const bz = Math.sin(baseTheta) * ringR;
          out[n.i] = {
            x: bx, y: by, z: bz,
            phase: _seedRand(n.id, 'sat_phase') * Math.PI * 2,
            // Save base for runtime rotation
            _baseTheta: baseTheta,
            _yBand: cy,
            _ringR: ringR,
            _yPos: cy * nodeR,
            // Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 base
            _basePos: { x: bx, y: by, z: bz },
          };
        }
        continue;
      }
      // Tier 0-8 (Phase 2.5): spherical Fibonacci lattice
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / Math.max(N - 1, 1)) * 2;
        const radius = Math.sqrt(Math.max(0, 1 - y * y));
        const theta = goldenAngle * i;
        const nid = grp[i].id;
        const rj = 0.92 + _seedRand(nid, 'fib_rj') * 0.08;
        out[grp[i].i] = {
          x: Math.cos(theta) * radius * rj * R,
          y: y * rj * R,
          z: Math.sin(theta) * radius * rj * R,
          phase: _seedRand(nid, 'fib_phase') * Math.PI * 2,
        };
      }
    }
    return out;
  }

  function buildEdgesPerTier(nodeList) {
    // 14 tiers (8 main + 5 outer + tier 4 empty) — A안 cleanup
    const tiers = [[], [], [], [], [], [], [], [], [], [], [], [], [], []];
    for (let i = 0; i < nodeList.length; i++) {
      const t = (typeof nodeList[i].tier === 'number') ? Math.min(13, nodeList[i].tier) : 8;
      tiers[t].push(i);
    }
    const seen = new Set();
    const edgesArr = [];
    for (let t = 0; t < tiers.length; t++) {
      const idxs = tiers[t];
      if (idxs.length < 2) continue;
      const K = Math.min(TIER_K[t], idxs.length - 1);
      for (let p = 0; p < idxs.length; p++) {
        const a = nodeList[idxs[p]];
        const dists = [];
        for (let q = 0; q < idxs.length; q++) {
          if (p === q) continue;
          const b = nodeList[idxs[q]];
          const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          dists.push({ j: idxs[q], d: dx*dx + dy*dy + dz*dz });
        }
        dists.sort((u, v) => u.d - v.d);
        for (let k = 0; k < K; k++) {
          const ia = idxs[p], ib = dists[k].j;
          const key = ia < ib ? ia+','+ib : ib+','+ia;
          if (seen.has(key)) continue;
          seen.add(key);
          edgesArr.push({ a: ia, b: ib, tier: t });
        }
      }
    }
    return edgesArr;
  }

  // ── Camera + interaction ────────────────────────────────────────────────
  let yaw = 0, pitch = 0.18;
  let zoom = 1.0;             // 1.0 = base, 0.5..2.0 range
  let dragging = false;
  let dragStartX = 0, dragStartY = 0;
  let dragYaw0 = 0, dragPitch0 = 0;
  let lastInteractionAt = 0;

  function project(p) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const x = p.x * cy - p.z * sy;
    const z = p.x * sy + p.z * cy;
    const y = p.y;
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const y2 = y * cp - z * sp;
    const z2 = y * sp + z * cp;
    const sphereR = Math.min(W, H) * 0.36 * zoom;
    const persp = 1 / (1 - z2 * 0.22);
    return {
      sx: CX + x * sphereR * persp,
      sy: CY + y2 * sphereR * persp,
      depth: z2,
      persp,
    };
  }

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }

  // ── Drawing layers ──────────────────────────────────────────────────────
  function drawCloudGlow() {
    const r = Math.min(W, H) * 0.5;
    const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, r);
    // Dim ambient — sphere only "breathes" subtly, real glow comes from events
    g.addColorStop(0,   'rgba(95,135,175,0.10)');
    g.addColorStop(0.45,'rgba(95,135,175,0.04)');
    g.addColorStop(0.85,'rgba(95,135,175,0.01)');
    g.addColorStop(1,   'rgba(95,135,175,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // Jin 2026-04-28 — subtle group cloud tint per asset_group region. 각
    // group sector center 에 작은 radial gradient (4~7% alpha) 추가 → MKT/
    // WATCH/STRAT 가 같은 영역에 colocate 된 group 의 시각적 연속성 강조.
    // sphere 표면 yaw rotation 에 따라 어느 group 영역이 앞면인지 변함 →
    // sector center theta + 현재 yaw 로 화면상 위치 계산.
    const sphereR = Math.min(W, H) * 0.36 * zoom;
    for (const [grp, color] of Object.entries(GROUP_COLORS)) {
      const sec = GROUP_SECTORS[grp];
      if (!sec) continue;
      const centerTheta = (sec[0] + sec[1]) * 0.5;
      // World → screen with current yaw (y=0 plane, equator).
      const wx = Math.cos(centerTheta);
      const wz = Math.sin(centerTheta);
      // Apply yaw rotation only (pitch leaves equator near horizontal).
      const cy = Math.cos(yaw), sy = Math.sin(yaw);
      const x = wx * cy - wz * sy;
      const z = wx * sy + wz * cy;
      // Skip if behind sphere (depth-cull, z>0.3 → 후면).
      if (z > 0.3) continue;
      const persp = 1 / (1 - z * 0.22);
      const sx = CX + x * sphereR * persp;
      const sy2 = CY;
      // Fade by depth (front = brighter).
      const depthFade = Math.max(0, 0.55 - z * 0.5);
      const radius = sphereR * 0.55;
      const cg = ctx.createRadialGradient(sx, sy2, 0, sx, sy2, radius);
      const [cr, cgr, cb] = color;
      cg.addColorStop(0,   `rgba(${cr},${cgr},${cb},${(0.06 * depthFade).toFixed(3)})`);
      cg.addColorStop(0.5, `rgba(${cr},${cgr},${cb},${(0.025 * depthFade).toFixed(3)})`);
      cg.addColorStop(1,   `rgba(${cr},${cgr},${cb},0)`);
      ctx.fillStyle = cg;
      ctx.fillRect(0, 0, W, H);
    }

    // Tier rim rings — visualize nested spheres (6 tiers)
    ctx.save();
    for (let t = 0; t < TIER_RADIUS.length; t++) {
      const R = TIER_RADIUS[t] * Math.min(W, H) * 0.36;
      ctx.strokeStyle = `rgba(95,135,175,${0.04 + (5 - t) * 0.015})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(CX, CY, R, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawDormantEdges(projected) {
    // Jin 2026-04-27: tier 별 / 종류별 edge 색 차별 (각 tier 의 cluster color)
    // Intra-shell edges (k-NN within tier) — color per tier, subtle alpha
    // Phase 3 chain provenance (Jin 2026-04-27): chainActive 일 때 chain edge bright pulsing,
    // 외 edge dim (alpha × 0.18). chainEdges Set lookup O(1).
    ctx.lineWidth = 0.5;
    const chainPulse = chainActive
      ? (0.55 + 0.45 * Math.sin(performance.now() * 0.005))
      : 0;
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const a = projected[e.a], b = projected[e.b];
      const avgDepth = (a.depth + b.depth) * 0.5;
      if (avgDepth < -0.55) continue;
      const front = (avgDepth + 1) * 0.5;
      const tierBoost = (3 - e.tier) * 0.018;
      let alpha = 0.03 + front * 0.09 + tierBoost;
      // Per-tier color from CLUSTERS palette (A안 cleanup: 14 tiers, T4 empty, T13 EXIT_TALLY)
      const tierClusters = ['pos','exit','exec','reg',null,'strat','brain','watch','mkt','obs','action','orbit','axis','exit_tally'];
      const cl = CLUSTERS[tierClusters[e.tier]] || [135, 175, 215];
      let lw = 0.5;
      if (chainActive) {
        if (chainEdges.has(i)) {
          alpha = Math.min(0.85, 0.35 + chainPulse * 0.45);  // bright + pulse
          lw = 1.2;
        } else {
          alpha = alpha * 0.18;                              // dim
        }
      }
      ctx.lineWidth = lw;
      ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${alpha})`;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
    ctx.lineWidth = 0.5;
  }

  // Radial connection — Jin 2026-04-27: tier 간 outside→inside flow (very subtle dim)
  // Connects firing/lit nodes to next-inner-tier closest node (visual cascade hint)
  function drawRadialConnections(projected) {
    if (!projected.length) return;
    ctx.lineWidth = 0.4;
    // For each firing/lit node in tiers 8-1, find nearest-azimuth node in tier-1 (inner)
    // Drawn as ghost line, much dimmer than chain (Jin: 링크 안 도드라지게)
    // Phase 2.5: 9 main tiers (0-8), exclude tier > 8 (OBS/ACTION/ORBIT/AXIS)
    const byTier = [[],[],[],[],[],[],[],[],[]];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.tier == null || n.tier > 8) continue;     // exclude outer ring (T9-T12)
      if (n.state === 'dormant') continue;            // active only
      byTier[n.tier].push(i);
    }
    // Connect each active node in tier T to the closest active node in nearest non-empty inner tier
    // (T4 empty after group cluster removal — skip empty inner tiers to keep cascade unbroken)
    for (let t = 8; t >= 1; t--) {
      const outerNodes = byTier[t];
      if (!outerNodes.length) continue;
      let innerNodes = null;
      for (let inner = t - 1; inner >= 0; inner--) {
        if (byTier[inner] && byTier[inner].length) { innerNodes = byTier[inner]; break; }
      }
      if (!innerNodes) continue;
      // Sample subset to keep performance — max 12 outer per tier
      const sample = outerNodes.slice(0, 12);
      for (const oi of sample) {
        const a = projected[oi];
        if (a.depth < -0.55) continue;
        let bestIdx = -1, bestD = Infinity;
        for (const ii of innerNodes) {
          const ip = projected[ii];
          const dx = a.sx - ip.sx, dy = a.sy - ip.sy;
          const d = dx * dx + dy * dy;
          if (d < bestD) { bestD = d; bestIdx = ii; }
        }
        if (bestIdx < 0) continue;
        const b = projected[bestIdx];
        if (b.depth < -0.55) continue;
        const front = ((a.depth + b.depth) * 0.5 + 1) * 0.5;
        const alpha = 0.025 + front * 0.05;     // very subtle (Jin: 안 도드라지게)
        // Color: outer tier color (A안 cleanup: T4 empty after group cluster removal)
        const tierClusters = ['pos','exit','exec','reg',null,'strat','brain','watch','mkt'];
        const cl = CLUSTERS[tierClusters[t]] || [135, 175, 215];
        ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${alpha})`;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }
    }
  }

  // Persistent relationships — Jin 2026-04-27: 시안 attachRelations 차용
  //   1. Active Regime ↔ AI HIGH/MID (regime drives AI judgment)
  //   2. AI HIGH/MID ↔ Top 6 active Strategies (judges score strategies)
  //   3. Top Strategies → Live Positions (already drawn via trade chains)
  // Subtle persistent edges (Jin: 안 도드라지게) — alpha 0.04~0.14
  function drawRelationships(projected) {
    // Index nodes by role
    const regimeFiring = [];
    const aiHighFiring = [];
    const aiMidFiring = [];
    const stratActive = [];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.cluster === 'reg' && n.state === 'firing' && n.label && n.label.startsWith('regime_')) {
        regimeFiring.push(i);
      } else if (n.cluster === 'brain' && n.state === 'firing') {
        if (n.ai_tier === 'high') aiHighFiring.push(i);
        else if (n.ai_tier === 'mid') aiMidFiring.push(i);
      } else if (n.cluster === 'strat' && n.state !== 'dormant') {
        stratActive.push(i);
      }
    }
    if (!regimeFiring.length && !aiHighFiring.length && !aiMidFiring.length) return;

    ctx.lineWidth = 0.5;

    // Jin 2026-04-27 lag fix: relationships glow 제거 (perf saver, gradient line 만 그림)
    ctx.shadowBlur = 0;
    const aiAll = [...aiHighFiring, ...aiMidFiring];
    // 1. Regime ↔ AI HIGH/MID
    for (const ri of regimeFiring) {
      const a = projected[ri];
      if (a.depth < -0.55) continue;
      for (const ai of aiAll) {
        const b = projected[ai];
        if (b.depth < -0.55) continue;
        const front = ((a.depth + b.depth) * 0.5 + 1) * 0.5;
        const isHigh = aiHighFiring.indexOf(ai) >= 0;
        const alpha = isHigh ? 0.08 + front * 0.10 : 0.05 + front * 0.06;
        const grad = ctx.createLinearGradient(a.sx, a.sy, b.sx, b.sy);
        const rcl = CLUSTERS.reg, bcl = CLUSTERS.brain;
        grad.addColorStop(0, `rgba(${rcl[0]},${rcl[1]},${rcl[2]},${alpha})`);
        grad.addColorStop(1, `rgba(${bcl[0]},${bcl[1]},${bcl[2]},${alpha})`);
        ctx.strokeStyle = grad;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }
    }
    // 2. AI HIGH/MID ↔ Top 6 active strategies
    const topStrats = stratActive.slice(0, 6);
    if (topStrats.length) {
      for (const ai of aiAll) {
        const a = projected[ai];
        if (a.depth < -0.55) continue;
        const isHigh = aiHighFiring.indexOf(ai) >= 0;
        const weight = isHigh ? 0.45 : 0.25;
        for (const si of topStrats) {
          const b = projected[si];
          if (b.depth < -0.55) continue;
          const front = ((a.depth + b.depth) * 0.5 + 1) * 0.5;
          const alpha = (0.05 + front * 0.08) * weight;
          const grad = ctx.createLinearGradient(a.sx, a.sy, b.sx, b.sy);
          const bcl = CLUSTERS.brain, scl = CLUSTERS.strat;
          grad.addColorStop(0, `rgba(${bcl[0]},${bcl[1]},${bcl[2]},${alpha})`);
          grad.addColorStop(1, `rgba(${scl[0]},${scl[1]},${scl[2]},${alpha})`);
          ctx.strokeStyle = grad;
          ctx.beginPath();
          ctx.moveTo(a.sx, a.sy);
          ctx.lineTo(b.sx, b.sy);
          ctx.stroke();
        }
      }
    }
  }

  let projectedCache = [];

  // Persistent trade chains: DEC → REG → EXEC → POS, colored by PnL
  function tickTradeChains(now, dt) {
    for (const tc of tradeChains.values()) {
      const segCount = Math.max(1, tc.chain.length - 1);
      // Advance pulse position (continuous t increments through full chain)
      tc.pulseT += dt * CHAIN_PULSE_SPEED;
      if (tc.pulseT >= segCount) tc.pulseT -= segCount;  // wrap → repeat
    }
  }

  // Jin 2026-04-28 — Chain proximity drift ("워터풀" effect). 활성 trade chain
  // 의 인접 노드 쌍이 서로 살짝 끌림 → ticker/regime/strategy/position 이
  // 자연스럽게 같은 영역으로 모임 → trade lineage 시각 명확. 너무 dramatic 안
  // 되도록 천천히 drift + decay (spring back to base when chain expires).
  // _chainOffsetX/Y/Z = 누적 drift, projectInto 가 base + offset 으로 render.
  function tickChainDrift(dt) {
    if (!nodes.length) return;
    // Decay all nodes — spring back to base (chain 종료 후 원위치 복귀).
    // half-life ~1.15s (decay = exp(-dt * 0.6) per frame).
    const decay = Math.exp(-dt * 0.6);
    for (const n of nodes) {
      if (n._chainOffsetX) {
        n._chainOffsetX *= decay;
        if (Math.abs(n._chainOffsetX) < 0.0005) n._chainOffsetX = 0;
      }
      if (n._chainOffsetY) {
        n._chainOffsetY *= decay;
        if (Math.abs(n._chainOffsetY) < 0.0005) n._chainOffsetY = 0;
      }
      if (n._chainOffsetZ) {
        n._chainOffsetZ *= decay;
        if (Math.abs(n._chainOffsetZ) < 0.0005) n._chainOffsetZ = 0;
      }
    }
    if (!tradeChains.size) return;
    const PULL = 0.04 * dt;            // gentle attract — 4% per second
    const MAX_OFFSET = 0.10;           // sphere unit radius ≈ 1 → 10% max drift
    for (const tc of tradeChains.values()) {
      const ch = tc.chain;
      if (!ch || ch.length < 2) continue;
      for (let i = 0; i < ch.length - 1; i++) {
        const a = nodes[ch[i]], b = nodes[ch[i + 1]];
        if (!a || !b) continue;
        const ax = a.x + (a._chainOffsetX || 0);
        const ay = a.y + (a._chainOffsetY || 0);
        const az = a.z + (a._chainOffsetZ || 0);
        const bx = b.x + (b._chainOffsetX || 0);
        const by = b.y + (b._chainOffsetY || 0);
        const bz = b.z + (b._chainOffsetZ || 0);
        const dx = bx - ax, dy = by - ay, dz = bz - az;
        // Jin 2026-04-28: radial 성분 제거 — drift 가 자기 tier (sphere shell)
        // 유지하면서 tangential (azimuth/latitude) 방향으로만 끌림. 이전 코드는
        // raw (dx,dy,dz) 적용 → MKT (outer) 가 POS (center) 쪽으로 끌려 "중앙
        // 으로 빨려감" 보고. (dx,dy,dz) 를 a/b 의 normal vector 에 수직 평면
        // (tangent) 으로 project → radial 보존, group region azimuth align 만.
        const arLen = Math.sqrt(ax*ax + ay*ay + az*az) || 1;
        const arx = ax / arLen, ary = ay / arLen, arz = az / arLen;
        const adot = dx*arx + dy*ary + dz*arz;
        const atx = dx - adot * arx, aty = dy - adot * ary, atz = dz - adot * arz;
        const brLen = Math.sqrt(bx*bx + by*by + bz*bz) || 1;
        const brx = bx / brLen, bry = by / brLen, brz = bz / brLen;
        const bdot = (-dx)*brx + (-dy)*bry + (-dz)*brz;
        const btx = (-dx) - bdot * brx, bty = (-dy) - bdot * bry, btz = (-dz) - bdot * brz;
        // Mutual tangential attraction — a tangent to b's azimuth direction.
        a._chainOffsetX = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (a._chainOffsetX || 0) + atx * PULL));
        a._chainOffsetY = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (a._chainOffsetY || 0) + aty * PULL));
        a._chainOffsetZ = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (a._chainOffsetZ || 0) + atz * PULL));
        b._chainOffsetX = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (b._chainOffsetX || 0) + btx * PULL));
        b._chainOffsetY = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (b._chainOffsetY || 0) + bty * PULL));
        b._chainOffsetZ = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET,
          (b._chainOffsetZ || 0) + btz * PULL));
      }
    }
  }
  function drawTradeChains(projected) {
    // Jin 2026-04-27: 지지직 lightning-bolt 효과 — jagged path + flicker + random arc burst
    // MKT → WATCH → BRAIN → STRAT → REG → EXEC → POS 7-stage cascade
    // chainHighlightIdx (clicked node) — same-ticker chain 만 prominent, 나머지 dim
    const now = performance.now();
    let highlightTicker = null;
    if (chainHighlightIdx >= 0 && nodes[chainHighlightIdx]) {
      highlightTicker = nodes[chainHighlightIdx].ticker;
    }
    // Jin 2026-04-30 PERF: PERF mode 시 chain pulse rendering 전부 skip
    // (continuous animation + per-chain flicker = frame budget 25%).
    if (_PERF_MODE) return;
    for (const tc of tradeChains.values()) {
      const ch = tc.chain;
      if (ch.length < 2) continue;
      let strength = tc.strength != null ? tc.strength : 0.5;
      // Jin v4 click highlight: same-ticker chain bright, others dim
      if (highlightTicker) {
        if (tc.ticker === highlightTicker) strength = Math.min(1.0, strength + 0.5);
        else strength = strength * 0.25;
      }
      // Jin 2026-04-27: 살아 숨쉬는 breathing — slower 주기, deeper dim
      // 0.003 rad/ms ≈ ~3.5초 cycle (was 0.86Hz → 0.29Hz)
      const flickerPhase = (now * 0.003 + (tc.trade_id * 0.7 || 0)) % (Math.PI * 2);
      const breathBase = 0.5 + 0.5 * Math.sin(flickerPhase);   // 0..1 smooth breathing
      const flicker = Math.max(0.10,
        0.15 + 0.90 * breathBase + 0.06 * (Math.random() - 0.5));  // 0.10~1.10 (cap below)
      // Random discharge burst — chain 가끔 (1%, Jin 2026-04-30 톤다운) 전체 flash
      const isDischarging = Math.random() < 0.01;
      const burstMul = isDischarging ? 1.8 : 1.0;
      // Jagged lightning trunk — Jin 2026-04-27: 성능 최적화 (shadow blur 줄임, segment 줄임)
      // Sustained stroke = grey dim (tc.color), trigger effects (discharge burst) 만 origin cluster color (Jin "트리거 이팩트만")
      for (let s = 0; s < ch.length - 1; s++) {
        const a = projected[ch[s]], b = projected[ch[s + 1]];
        if (!a || !b) continue;
        const avgDepth = (a.depth + b.depth) * 0.5;
        if (avgDepth < -0.55) continue;
        const front = (avgDepth + 1) * 0.5;
        const baseAlpha = (0.06 + front * 0.18) * (0.55 + strength * 0.75);
        const alpha = Math.min(0.55, baseAlpha * flicker * burstMul);
        // Lightning-bolt zigzag (segment 줄임 4-7 → 3-4 for perf)
        const dx = b.sx - a.sx, dy = b.sy - a.sy;
        const len = Math.sqrt(dx*dx + dy*dy) || 1;
        const px = -dy / len, py = dx / len;
        const segments = 3 + (strength > 0.7 ? 1 : 0);     // 3-4 only
        const jitterAmp = 1.2 + strength * 2.0 + (isDischarging ? 2.0 : 0);
        // Trigger effect color — origin node cluster (Jin "트리거 시 출발점 노드 색"). Sustained = grey.
        const originNode = nodes[ch[s]];
        const triggerColor = (originNode && CLUSTER_COLORS[originNode.cluster]) || tc.color;
        if (isDischarging) {
          // Discharge burst (3% random) = trigger effect = origin cluster color
          ctx.shadowColor = rgba(triggerColor, Math.min(1.0, alpha * 2.4));
          ctx.shadowBlur = 4 + strength * 3;  // Jin 2026-04-30 랙 fix: 8+6→4+3
          ctx.strokeStyle = rgba(triggerColor, alpha);
        } else {
          // Sustained = grey dim
          ctx.shadowBlur = 0;
          ctx.strokeStyle = rgba(tc.color, alpha);
        }
        ctx.lineWidth = 0.55 + front * 0.40 + strength * 0.65;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        for (let j = 1; j < segments; j++) {
          const t = j / segments;
          // Random perpendicular offset — peaks at midpoint, taper at ends
          const taper = 1 - Math.abs(2 * t - 1);
          const offset = (Math.random() - 0.5) * jitterAmp * taper;
          ctx.lineTo(
            a.sx + dx * t + px * offset,
            a.sy + dy * t + py * offset
          );
        }
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }
      ctx.shadowBlur = 0;
      // Single head spark — only present at chain head (intermittent crackle)
      const segCount = ch.length - 1;
      let pt = tc.pulseT;
      if (pt >= segCount) pt -= segCount;
      const segIdx = Math.min(segCount - 1, Math.floor(pt));
      const localT = pt - segIdx;
      const sa = projected[ch[segIdx]], sb = projected[ch[segIdx + 1]];
      if (sa && sb) {
        const avgDepth = (sa.depth + sb.depth) * 0.5;
        if (avgDepth >= -0.55) {
          const front = (avgDepth + 1) * 0.5;
          const sx = sa.sx + (sb.sx - sa.sx) * localT;
          const sy = sa.sy + (sb.sy - sa.sy) * localT;
          // Spark visibility follows breathing — bright phase = crackle, dim phase = quiet
          // Jin 2026-04-27: 살아 숨쉬는 spark, breath 따라 visibility 변화
          const sparkVisible = Math.random() < (0.20 + 0.55 * breathBase);  // 20%~75%
          const sparkAlpha = (sparkVisible ? 1.0 : 0.0) *
                             (0.30 + front * 0.30) * burstMul * (0.4 + 0.6 * breathBase);
          if (sparkAlpha > 0) {
            const baseR = (1.5 + strength * 1.8) * burstMul;
            // Sparkle = trigger effect = origin node cluster color (Jin "트리거 이팩트")
            const sparkOriginNode = nodes[ch[segIdx]];
            const sparkColor = (sparkOriginNode && CLUSTER_COLORS[sparkOriginNode.cluster]) || tc.color;
            ctx.shadowColor = rgba(sparkColor, 0.95);
            ctx.shadowBlur = 9;  // Jin 2026-04-30 랙 fix: 18→9
            ctx.fillStyle = rgba(sparkColor, sparkAlpha);
            ctx.beginPath();
            ctx.arc(sx, sy, baseR * (0.85 + front * 0.35), 0, Math.PI * 2);
            ctx.fill();
            // White-hot crackle core (less opaque)
            ctx.shadowBlur = 0;
            ctx.fillStyle = `rgba(255,255,255,${0.55 * sparkAlpha})`;
            ctx.beginPath();
            ctx.arc(sx, sy, baseR * 0.40, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }
      // Random discharge fork — burst 일 때 chain 끝 (POS) 에 작은 spark fork
      if (isDischarging && ch.length >= 2) {
        const tail = projected[ch[ch.length - 1]];
        if (tail && tail.depth >= -0.55) {
          const forkCount = 2 + Math.floor(Math.random() * 3);
          for (let f = 0; f < forkCount; f++) {
            const angle = Math.random() * Math.PI * 2;
            const forkLen = 6 + Math.random() * 10;
            const fx = tail.sx + Math.cos(angle) * forkLen;
            const fy = tail.sy + Math.sin(angle) * forkLen;
            ctx.shadowColor = rgba(tc.color, 0.8);
            ctx.shadowBlur = 8;
            ctx.strokeStyle = rgba(tc.color, 0.7);
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(tail.sx, tail.sy);
            ctx.lineTo(fx, fy);
            ctx.stroke();
          }
        }
      }
      ctx.shadowBlur = 0;
    }
  }

  // ── Shockwaves ──────────────────────────────────────────────────────────
  const shockwaves = [];
  function spawnShock(x, y, color, mode) {
    shockwaves.push({
      x, y, color,
      mode: mode || 'expand',
      born: performance.now(),
      maxAge: 1100,
    });
  }
  function drawShockwaves(now) {
    for (let i = shockwaves.length - 1; i >= 0; i--) {
      const s = shockwaves[i];
      const t = (now - s.born) / s.maxAge;
      if (t >= 1) { shockwaves.splice(i, 1); continue; }
      const baseR = Math.min(W, H) * 0.06;   // was 0.10 — tighter pop
      const radius = s.mode === 'contract' ? baseR * (1 - t) : baseR * t;
      const alpha = (1 - t) * 0.65;
      ctx.save();
      ctx.strokeStyle = rgba(s.color, alpha);
      ctx.lineWidth = 1.5 + (1 - t) * 1.5;
      ctx.shadowColor = rgba(s.color, alpha);
      ctx.shadowBlur = 6;  // Jin 2026-04-30 랙 fix: 12→6
      ctx.beginPath();
      ctx.arc(s.x, s.y, radius, 0, Math.PI * 2);
      ctx.stroke();
      // inner ghost ring
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = rgba(s.color, alpha * 0.4);
      ctx.beginPath();
      ctx.arc(s.x, s.y, radius * 0.7, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  // ── Firing pulse system (random ambient sparks) ─────────────────────────
  const firing = [];
  let FIRING_TARGET = 24;
  function spawnFiring(preferEdgeIdx = null) {
    if (!edges.length) return;
    let idx;
    if (preferEdgeIdx != null) idx = preferEdgeIdx;
    else if (firingTickers.size > 0 && Math.random() < 0.65) {
      const cand = [];
      for (let e = 0; e < edges.length; e++) {
        const ed = edges[e];
        if (nodes[ed.a].state === 'firing' || nodes[ed.b].state === 'firing') cand.push(e);
      }
      idx = cand.length ? cand[(Math.random() * cand.length) | 0]
                        : (Math.random() * edges.length) | 0;
    } else idx = (Math.random() * edges.length) | 0;
    firing.push({
      edgeIdx: idx, t: 0, life: 1,
      speed: 0.9 + Math.random() * 1.4,
      decay: 0.45 + Math.random() * 0.4,
      fork: Math.random() < 0.30,
    });
  }
  function lightningPath(ax, ay, bx, by, segs, amp) {
    const pts = [{x: ax, y: ay}];
    const dx = bx - ax, dy = by - ay;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    for (let i = 1; i < segs; i++) {
      const t = i / segs;
      const ox = ax + dx * t, oy = ay + dy * t;
      const j = (Math.random() - 0.5) * amp * (1 - Math.abs(t - 0.5) * 1.4);
      pts.push({ x: ox + nx * j, y: oy + ny * j });
    }
    pts.push({ x: bx, y: by });
    return pts;
  }
  function drawFiring(projected) {
    for (const f of firing) {
      const e = edges[f.edgeIdx]; if (!e) continue;
      const a = projected[e.a], b = projected[e.b];
      const n1 = nodes[e.a], n2 = nodes[e.b];
      if (!n1 || !n2) continue;
      const avgDepth = (a.depth + b.depth) * 0.5;
      if (avgDepth < -0.55) continue;  // cull back-half
      const cl1 = colorFor(n1), cl2 = colorFor(n2);
      const blendC = [
        (cl1[0] + cl2[0]) >> 1,
        (cl1[1] + cl2[1]) >> 1,
        (cl1[2] + cl2[2]) >> 1,
      ];
      ctx.save();
      ctx.shadowColor = rgba(blendC, 1);
      ctx.shadowBlur = 10 * f.life;
      ctx.strokeStyle = rgba(blendC, 0.85 * f.life);
      ctx.lineWidth = 1.3 * f.life;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      ctx.restore();
      if (f.t < 1) {
        const px = a.sx + (b.sx - a.sx) * f.t;
        const py = a.sy + (b.sy - a.sy) * f.t;
        ctx.save();
        ctx.shadowColor = '#fff';
        ctx.shadowBlur = 6;  // Jin 2026-04-30 랙 fix: 12→6
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(px, py, 2.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }
  }

  // Pre-allocated z-sort buffer
  let _orderBuf = null;
  function drawNodes(projected, time) {
    const N = nodes.length;
    if (!_orderBuf || _orderBuf.length !== N) {
      _orderBuf = new Array(N);
      for (let i = 0; i < N; i++) _orderBuf[i] = i;
    }
    _orderBuf.sort((a, b) => projected[a].depth - projected[b].depth);
    for (let oi = 0; oi < N; oi++) {
      const i = _orderBuf[oi];
      const p = projected[i], n = nodes[i];
      if (!n) continue;
      const cl = colorFor(n);
      const dataInt = (n.intensity != null) ? n.intensity : (STATE_BASE[n.state] || 0.22);
      const bump = n._intensityBump || 0;
      const base = Math.min(1, dataInt + bump);
      const depthFade = 0.35 + 0.65 * ((p.depth + 1) / 2);

      // Special path: T8 MKT dormant = full universe cloud — guaranteed visible (Phase 2.5)
      if (n.tier === 8 && n.state === 'dormant' && bump < 0.05) {
        const a = 0.20 + depthFade * 0.30;
        ctx.fillStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${a})`;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, 0.6 + depthFade * 0.45, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }

      const breathe = 1 + Math.sin(time * 0.0013 + (n.phase || 0)) * 0.18;
      // Phase 3 chain provenance dim (Jin 2026-04-27): non-chain alpha × 0.15
      let chainDim = 1.0;
      if (chainActive) {
        chainDim = chainNodes.has(i) ? 1.15 : 0.15;
      }
      const intensity = base * breathe * depthFade * chainDim;
      const tierSizeBoost = TIER_SIZE[n.tier] || 1.0;
      // Per-node size_mul (Jin v4: AI tier high/mid/low/tool 차별 + OBS/ACTION sev 차별)
      const sizeMul = n.size_mul || 1.0;
      // Outer satellite (T9/T10/T13) minimum size boost — Jin "쟤들도 콜하니까 일정 수준 기본"
      // base² × 5 + 0.4 의 minimum 을 dormant case 에 적용 (max firing 그대로)
      let baseEff = base;
      const isOuterSat = (n.tier === 9 || n.tier === 10 || n.tier === 13 || n.tier === 12);
      if (isOuterSat && baseEff < 0.7) baseEff = 0.7;   // 3x minimum (max=0.85 그대로)
      let r = (0.4 + baseEff * baseEff * 5.0) * p.persp * tierSizeBoost * sizeMul;
      // Jin 2026-04-27 dynamic-size cap: tier 별 max radius enforce (size_mul 폭주 방지)
      const _maxR = MAX_NODE_RADIUS_PER_TIER[n.tier];
      if (typeof _maxR === 'number' && r > _maxR) r = _maxR;

      // Halo ONLY when firing OR strong intensity bump (event-driven, not ambient)
      // Jin 2026-04-30 PERF: PERF mode 시 halo 전부 skip (createRadialGradient
      // 알록 GC pressure 가 frame budget 30% 차지).
      if (!_PERF_MODE && (n.state === 'firing' || bump > 0.15) && depthFade > 0.45) {
        // MKT firing gets extra halo so exchange color shines clearly
        // OBS/ACTION/EXIT_TALLY (T9/T10/T13) external satellites also get prominent halo
        let haloMul = 4;
        if (n.cluster === 'mkt') haloMul = 6;
        else if (n.tier === 9 || n.tier === 10 || n.tier === 13) haloMul = 2;
        else if (n.ai_tier === 'high') haloMul = 6;             // AI HIGH prominent
        else if (n.cluster === 'pos' || n.tier === 0) haloMul = 5;  // POS slight ↑
        const haloR = r * (haloMul + bump * 8);
        let haloAlpha = 0.22;                                   // global ↓ 0.32→0.22 (Jin 2026-04-30 추가 톤다운)
        if (n.cluster === 'mkt') haloAlpha = 0.45;              // ↓ 0.55→0.45
        else if (n.tier === 9 || n.tier === 10 || n.tier === 13) haloAlpha = 0.55;  // ↓ 0.65→0.55
        else if (n.ai_tier === 'high') haloAlpha = 0.45;        // ↓ 0.55→0.45
        else if (n.cluster === 'pos' || n.tier === 0) haloAlpha = 0.50;  // POS ↑ 0.40→0.50
        const hg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, haloR);
        hg.addColorStop(0, rgba(cl, haloAlpha * intensity));
        hg.addColorStop(1, rgba(cl, 0));
        ctx.fillStyle = hg;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, haloR, 0, Math.PI * 2);
        ctx.fill();
      }
      // Body shape — square for satellites (OBS/ACTION/ORBIT/AXIS/EXIT_TALLY) / circle otherwise
      // A안 cleanup: T13 EXIT_TALLY square (외부 ring)
      ctx.fillStyle = rgba(cl, Math.min(1, intensity * 1.5));
      if (n.shape === 'square' || n.tier === 9 || n.tier === 10 || n.tier === 13) {
        const sR = r * 1.05;             // slight upsize for square
        if (typeof n.spin_speed === 'number' && (n.tier === 11 || n.tier === 12)) {
          // own-axis rotation — translate→rotate→draw centered→restore
          ctx.save();
          ctx.translate(p.sx, p.sy);
          ctx.rotate(n._spinAngle || 0);
          ctx.fillRect(-sR, -sR, sR * 2, sR * 2);
          ctx.restore();
        } else {
          ctx.fillRect(p.sx - sR, p.sy - sR, sR * 2, sR * 2);
        }
      } else {
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
        ctx.fill();
      }
      if (n.state === 'firing' && depthFade > 0.4) {
        if (n.tier === 0) {
          // White hot-core for POS (heart)
          ctx.fillStyle = `rgba(255,255,255,${0.7 * depthFade})`;
          ctx.beginPath();
          ctx.arc(p.sx, p.sy, r * 0.5, 0, Math.PI * 2);
          ctx.fill();
        } else if (n.tier === 9 || n.tier === 10 || n.tier === 13) {
          // Bright square core for satellite (OBS/ACTION/EXIT_TALLY)
          const cR = r * 0.45;
          ctx.fillStyle = `rgba(255,255,255,${0.55 * depthFade})`;
          ctx.fillRect(p.sx - cR, p.sy - cR, cR * 2, cR * 2);
        } else {
          // Subtle sparkle on tip
          ctx.fillStyle = `rgba(255,255,255,${0.30 * depthFade})`;
          ctx.beginPath();
          ctx.arc(p.sx, p.sy, r * 0.25, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
  }

  // Phase 3 (Jin 2026-04-27): 카테고리별 독립 궤도 — 메인 sphere 자전과 별개,
  // 각 위성 카테고리가 (orbit_axis, orbit_speed) 따라 _basePos 주위 Rodrigues 회전.
  // 14 categories (regime_infra/sensor/provider/learner/brain_tool/exit_engine/
  // exec_tool/ai_judge/session/liq/crisis/obs/action/exit_tally) 각각 다른 회전축.
  // 자전축 동방향(regime_infra/obs) / 반대(sensor/action) / 대각선(provider/learner/brain_tool/etc).
  //
  // Legacy fields (_baseTheta/_ringR/_yPos) 는 호환성으로 남기되 사용 안 함;
  // _basePos.{x,y,z} = deterministic 초기 위치, _orbitAngle = 누적 회전.
  let _lastSpinNow = 0;
  function updateSatelliteRotation(now) {
    const dt = _lastSpinNow > 0 ? Math.min(0.1, (now - _lastSpinNow) / 1000) : 0;
    _lastSpinNow = now;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      // Apply only to satellite tiers (T9 OBS / T10 ACTION / T11 ORBIT / T12 AXIS / T13 EXIT_TALLY)
      const isSat = (n.tier === 9 || n.tier === 10 || n.tier === 11
                  || n.tier === 12 || n.tier === 13);
      if (!isSat) continue;

      // Phase 3 — 카테고리별 궤도 (orbit_axis + orbit_speed from snapshot.py SAT_ORBIT)
      // Lazy init _basePos from current position (graph.json _basePos=null fallback)
      if (!n._basePos && typeof n.x === 'number') {
        n._basePos = { x: n.x, y: n.y, z: n.z };
      }
      if (n._basePos && Array.isArray(n.orbit_axis) && typeof n.orbit_speed === 'number') {
        // Phase 3.1 (Jin 2026-04-27 satellite-orbit-visible):
        // 같은 카테고리 위성도 다른 phase 에서 시작 → 같이 모이지 않음
        if (n._orbitAngle === undefined) {
          n._orbitAngle = (typeof n.initial_orbit_angle === 'number')
            ? n.initial_orbit_angle : 0;
        }
        if (dt > 0) {
          n._orbitAngle = n._orbitAngle + n.orbit_speed * dt;
          // wrap [-2π, 2π) to keep magnitude bounded
          if (n._orbitAngle > Math.PI * 2) n._orbitAngle -= Math.PI * 2;
          else if (n._orbitAngle < -Math.PI * 2) n._orbitAngle += Math.PI * 2;
        }
        const ang = n._orbitAngle || 0;
        // Rodrigues rotation: rotate _basePos around unit axis by ang
        // v_rot = v*cos(θ) + (k × v)*sin(θ) + k*(k·v)*(1-cos(θ))
        const kx = n.orbit_axis[0], ky = n.orbit_axis[1], kz = n.orbit_axis[2];
        const vx = n._basePos.x, vy = n._basePos.y, vz = n._basePos.z;
        const c = Math.cos(ang), s = Math.sin(ang);
        const dot = kx * vx + ky * vy + kz * vz;
        const oc = 1 - c;
        // (k × v)
        const cxx = ky * vz - kz * vy;
        const cxy = kz * vx - kx * vz;
        const cxz = kx * vy - ky * vx;
        n.x = vx * c + cxx * s + kx * dot * oc;
        n.y = vy * c + cxy * s + ky * dot * oc;
        n.z = vz * c + cxz * s + kz * dot * oc;
      }

      // Phase 2 — own-axis rotation (independent of sphere yaw / orbit theta)
      // spin_speed (rad/s, 0.2-1.5 from snapshot.py state-tier) accumulated each frame
      // (T11/T12 only — square-shape satellites; T9/T10/T13 not currently spun)
      if ((n.tier === 11 || n.tier === 12)
          && typeof n.spin_speed === 'number' && dt > 0) {
        n._spinAngle = (n._spinAngle || 0) + n.spin_speed * dt;
        // wrap [0, 2π) to keep magnitude bounded
        if (n._spinAngle > Math.PI * 2) n._spinAngle -= Math.PI * 2;
      }
    }
  }

  // Decay transient intensity bumps each frame (real-time twinkle)
  function tickIntensityBumps(dt) {
    const decay = Math.exp(-dt * 1.4);  // ~0.5s half-life
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n._intensityBump) {
        n._intensityBump *= decay;
        if (n._intensityBump < 0.01) n._intensityBump = 0;
      }
    }
  }

  // ── Main loop ───────────────────────────────────────────────────────────
  let last = performance.now();
  let animationStarted = false;
  let lastFrameAt = performance.now();
  let rafId = 0;

  function scheduleFrame() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(frame);
  }

  // Jin 2026-04-27 lag fix: 30fps throttle (60fps → 30fps, 50% GPU load)
  // Browser 가 한 번씩 lag 시 frame skip 통한 sustained smoothness
  const TARGET_FPS = 30;
  const MIN_FRAME_INTERVAL_MS = 1000 / TARGET_FPS;

  // Jin 2026-04-30 Performance Mode — base node count fix (mkt 2200→200) 후
  // 효과 켜도 안정. default OFF (시각 풍부). 'p' 키로 ON 가능 (랙 발생 시).
  let _PERF_MODE = false;
  window.PolarisCloud = window.PolarisCloud || {};
  window.PolarisCloud.togglePerfMode = () => {
    _PERF_MODE = !_PERF_MODE;
    console.log("Performance Mode:", _PERF_MODE ? "ON (visual ↓, smooth ↑)"
                                                  : "OFF (visual ↑, lag risk)");
    return _PERF_MODE;
  };
  window.PolarisCloud.isPerfModeOn = () => _PERF_MODE;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'p' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      window.PolarisCloud.togglePerfMode();
    }
  });

  function frame(now) {
    rafId = 0;
    // Jin 2026-04-30 랙 fix: 탭 hidden 시 frame skip (background 시 GPU/CPU 0)
    if (document.hidden) {
      setTimeout(scheduleFrame, 500);  // 500ms 후 재시도 (alive 만 유지)
      return;
    }
    if (now - lastFrameAt < MIN_FRAME_INTERVAL_MS) {
      // Skip — schedule next without drawing
      scheduleFrame();
      return;
    }
    lastFrameAt = now;
    // Jin 2026-04-30 PERF: shadowBlur 글로벌 override (모든 site 0).
    // PERF off 시 정상 복원.
    if (_PERF_MODE) {
      const _proto = Object.getPrototypeOf(ctx);
      if (!ctx._perfPatched) {
        Object.defineProperty(ctx, 'shadowBlur', {
          get() { return 0; },
          set(_v) { /* swallow */ },
          configurable: true,
        });
        ctx._perfPatched = true;
      }
    } else if (ctx._perfPatched) {
      delete ctx.shadowBlur;  // restore prototype default
      ctx._perfPatched = false;
    }
    try {
      _frameBody(now);
    } catch (err) {
      console.error('frame error', err);
    }
    scheduleFrame();
  }

  function _frameBody(now) {
    const dt = Math.min(50, now - last) / 1000;
    last = now;

    // Auto-rotate only when no recent user interaction (and toggle enabled)
    const idleMs = now - lastInteractionAt;
    if (autoRotateEnabled && !dragging && idleMs > 2500) {
      yaw += dt * 0.03;
      pitch = 0.18 + Math.sin(now * 0.0003) * 0.06;
    }
    // Click zoom — gradual zoom toward focus node (Jin v4)
    if (focusNodeIdx >= 0 && idleMs < 4000) {
      const targetZoom = 1.6;
      zoom += (targetZoom - zoom) * dt * 1.2;
    }
    // Jin v4: 위성 회전 — T10 ORBIT (function) + T11 AXIS (dimension)
    // T8 OBS / T9 ACTION 은 정지 (status indicator)
    updateSatelliteRotation(now);

    if (!nodes.length) return;

    ensureProjBuf(nodes.length);
    // Jin 2026-04-28: tickChainDrift 호출 제거 ("드리프팅 빼자 정신없다" mandate).
    // group placement (regroupMktByGroup / regroupStratByGroup) 만으로 같은
    // group ticker→strategy→position 영역 colocate 효과 충분.
    for (let i = 0; i < nodes.length; i++) projectInto(nodes[i], projBuf[i]);
    const projected = projBuf;
    projectedCache = projected;
    tickTradeChains(now, dt);

    for (let i = firing.length - 1; i >= 0; i--) {
      const f = firing[i];
      if (f.t < 1) f.t += f.speed * dt;
      else {
        f.life -= f.decay * dt;
        if (f.life <= 0) firing.splice(i, 1);
      }
    }
    // No random ambient pulses — all motion comes from real events
    // (trade chains pulse persistently, comets fire on signals/entries/exits only)

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    tickIntensityBumps(dt);
    // Galaxy outside sphere — disabled per Jin (정신사나움), all tickers now inside T6 MKT shell
    drawCloudGlow();
    drawDormantEdges(projected);
    drawRadialConnections(projected);          // Jin v4: 셸 사이 outside→inside ghost lines
    drawRelationships(projected);              // Jin v4: Regime ↔ AI ↔ Strategy persistent
    drawTradeChains(projected);                 // 지지직 lightning chain (POS 향한 active)
    drawChainTransientSparks(projected, now);  // 거래별 chain segment cascade
    drawSatelliteSignals(projected, now);      // 위성 → 노드 transient lightning beam
    drawOutboundArcs(projected, now);          // POS → ACTION quadratic arc (close cinematic)
    drawEdgeSparks(projected, now);            // 가끔 random edge ambient spark (cinematic)
    drawSupernovas(projected);                  // 큰 PnL trade close ring expansion
    drawMetricRipples(projected, now);         // 메트릭별 single node ripple (좁은 ring)
    maybeSpawnEdgeSpark(now);                   // ambient cinematic spawn
    drawNodes(projected, now);
    drawFiring(projected);
    drawComets(now);
    drawShockwaves(now);
    drawAiRipples(now);
    // Exchange labels removed — hover tooltip shows the same info on demand

    const elPulse = document.getElementById('pulse-count');
    const elEdge = document.getElementById('edge-count');
    if (elPulse) elPulse.textContent = firing.filter(f => f.t < 1).length + tradeChains.size;
    if (elEdge) elEdge.textContent = tradeChains.size;
  }

  // ── Mouse interaction ───────────────────────────────────────────────────
  const tooltip = document.getElementById('tooltip');
  let hoverNodeIdx = -1;

  function findHoverNode(mx, my) {
    // Codex#f: skip back-half + use larger hit radius for meaningful (firing/lit) nodes
    if (!projectedCache.length) return -1;
    let best = -1, bestDist = Infinity;
    for (let i = 0; i < projectedCache.length; i++) {
      const p = projectedCache[i];
      if (p.depth < -0.2) continue;   // skip back-of-sphere
      const n = nodes[i];
      if (!n) continue;
      // tier-8 (MKT) dormant uses smaller radius (avoid over-trigger on dim cloud) — Phase 2.5
      const isMeaningful = n.state === 'firing' || n.state === 'lit';
      const radius = (n.tier === 8 && !isMeaningful) ? 6 : (isMeaningful ? 14 : 10);
      const dx = p.sx - mx, dy = p.sy - my;
      const d = dx*dx + dy*dy;
      if (d < radius * radius && d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    return best;
  }

  function showTooltip(node, mx, my) {
    if (!tooltip) return;
    // Full text labels (Jin 2026-04-27 Phase 2.5: 13 tiers — 9 sphere + 4 satellite)
    const tierName = [
      'Live Position', 'Exit Type', 'Gate / Router', 'Regime State',
      'Asset Group', 'Strategy', 'AI Judge', 'Signal Watchlist', 'Market Universe',
      'System Health (OBS)', 'Action Queue',
      'Function Satellite', 'Dimension Axis'
    ][node.tier] || '?';
    const stateChip = node.state.toUpperCase();
    const lines = [
      `<div class="tt-h"><span class="tt-tier t${node.tier}">${tierName}</span> ${node.label}</div>`,
      `<div class="tt-l">state <b>${stateChip}</b>${node.intensity != null ? ' · int <b>'+(node.intensity*100|0)+'%</b>' : ''}</div>`,
    ];
    // POS — show live pnl from matching trade (lookup via ticker)
    if (node.cluster === 'pos' && node.ticker) {
      const tc = [...tradeChains.values()].find(c => c.ticker === node.ticker);
      lines.push(`<div class="tt-l">ticker <b>${node.ticker}</b> · ${(node.direction || '').toUpperCase()}</div>`);
      if (node.exchange) lines.push(`<div class="tt-l">exchange <b>${(node.exchange || '').toUpperCase()}</b></div>`);
      if (tc) {
        const pn = tc.pnl_usd || 0;
        const pp = tc.pnl_pct || 0;
        const pnlCls = pn >= 0 ? 'pos' : 'neg';
        lines.push(`<div class="tt-l">pnl <b class="${pnlCls}">${pn>=0?'+':''}$${pn.toFixed(2)} (${pp>=0?'+':''}${pp.toFixed(2)}%)</b></div>`);
        if (tc.strength != null) lines.push(`<div class="tt-l">link <b>${(tc.strength*100|0)}%</b> · wr <b>${(tc.win_rate*100|0)}%</b></div>`);
      }
    }
    // WATCH — score / scored count / exchange
    else if (node.cluster === 'watch') {
      if (node.ticker) lines.push(`<div class="tt-l">ticker <b>${node.ticker}</b></div>`);
      if (node.exchange) lines.push(`<div class="tt-l">exchange <b>${(node.exchange || '').toUpperCase()}</b></div>`);
      if (node.max_score != null) lines.push(`<div class="tt-l">max score <b>${node.max_score.toFixed(2)}</b> · n <b>${node.n_scored||0}</b></div>`);
    }
    // MKT — exchange + tech metrics
    else if (node.cluster === 'mkt') {
      if (node.ticker) lines.push(`<div class="tt-l">ticker <b>${node.ticker}</b></div>`);
      if (node.exchange) lines.push(`<div class="tt-l">exchange <b>${(node.exchange || '').toUpperCase()}</b></div>`);
    }
    // STRAT — strategy_id label (already in label)
    else if (node.cluster === 'strat') {
      lines.push(`<div class="tt-l">strategy</div>`);
    }
    // Default
    else {
      lines.push(`<div class="tt-l">cluster <b>${node.cluster}</b></div>`);
      if (node.ticker) lines.push(`<div class="tt-l">ticker <b>${node.ticker}</b></div>`);
    }
    tooltip.innerHTML = lines.join('');
    tooltip.style.display = 'block';
    const pad = 14;
    let x = mx + pad, y = my + pad;
    if (x + 240 > W) x = mx - 240 - pad;
    if (y + 90 > H) y = my - 90 - pad;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }
  function hideTooltip() {
    if (tooltip) tooltip.style.display = 'none';
    hoverNodeIdx = -1;
  }

  // ─── Detail panel (Jin v4: click → detail + provenance chain) ─────
  const detailPanel = document.getElementById('detail-panel');
  const dpCluster = document.getElementById('dp-cluster');
  const dpTitle = document.getElementById('dp-title');
  const dpGrid = document.getElementById('dp-grid');
  const dpChain = document.getElementById('dp-chain');
  const dpClose = document.getElementById('dp-close');
  if (dpClose) dpClose.addEventListener('click', () => closeDetailPanel());

  // Phase 2.5: 13 tiers (9 main + 4 outer). GROUP at index 4.
  const TIER_FULL_NAME = [
    'Live Position', 'Exit Pattern', 'Execution', 'Regime Context',
    'Asset Group', 'Strategy', 'Brain', 'Signal Watchlist', 'Market Universe',
    'System Health', 'Action Queue', 'Function Satellite', 'Dimension Axis'
  ];
  const TIER_HEX = ['#87d7ff','#ff87d7','#87afd7','#d7d787','#ffd787','#ff9f87','#d7afff','#87ffd7','#ffaf87','#d7d787','#d78787','#9fc7ff','#ffd7c7'];

  function openDetailPanel(idx) {
    if (!detailPanel || idx < 0 || idx >= nodes.length) return;
    const n = nodes[idx];
    if (!n) return;
    const tierName = TIER_FULL_NAME[n.tier] || n.cluster.toUpperCase();
    const color = TIER_HEX[n.tier] || '#fff';
    dpCluster.textContent = tierName;
    dpCluster.style.color = color;
    dpCluster.style.borderColor = color;
    dpTitle.textContent = n.label || '—';

    // Render fields per cluster (real data link)
    const rows = [];
    const push = (k, v, cls = '') =>
      rows.push(`<span class="k">${k}</span><span class="v ${cls}">${v}</span>`);

    push('state', (n.state || '?').toUpperCase());
    if (n.intensity != null) push('intensity', (n.intensity * 100 | 0) + '%');
    if (n.size_mul != null) push('size mult', n.size_mul.toFixed(2) + '×');

    if (n.cluster === 'pos' && n.ticker) {
      const tc = [...tradeChains.values()].find(c => c.ticker === n.ticker);
      push('ticker', n.ticker);
      push('direction', (n.direction || '').toUpperCase());
      if (n.exchange) push('exchange', n.exchange.toUpperCase());
      if (tc) {
        const pn = tc.pnl_usd || 0, pp = tc.pnl_pct || 0;
        push('PnL $', (pn >= 0 ? '+' : '') + pn.toFixed(2), pn >= 0 ? 'pos' : 'neg');
        push('PnL %', (pp >= 0 ? '+' : '') + pp.toFixed(2) + '%', pp >= 0 ? 'pos' : 'neg');
      }
    } else if (n.cluster === 'brain') {
      push('AI tier', (n.ai_tier || 'unknown').toUpperCase());
    } else if (n.cluster === 'obs') {
      if (n.value != null) push('value', n.value + (n.unit || ''));
      push('ok', n.ok ? 'YES' : 'NO', n.ok ? 'pos' : 'neg');
    } else if (n.cluster === 'action') {
      push('severity', n.sev || 'INFO', (n.sev === 'CRIT' || n.sev === 'HIGH') ? 'neg' : '');
      if (n.since_min != null) push('age', n.since_min + 'm');
    } else if (n.cluster === 'mkt' || n.cluster === 'watch') {
      if (n.ticker) push('ticker', n.ticker);
      if (n.exchange) push('exchange', n.exchange.toUpperCase());
      if (n.max_score != null) push('score', n.max_score.toFixed(2));
      if (n.n_scored != null) push('scored', n.n_scored);
    }

    dpGrid.innerHTML = rows.join('');

    // Provenance chain — outside → inside (Market → Watch → Brain → Strategy → Regime → Execution → Position)
    // Find seed-ticker chain if available, else show 8-tier ordering with cluster colors
    dpChain.innerHTML = renderProvenanceChain(idx, n);

    detailPanel.classList.add('visible');
  }

  function renderProvenanceChain(seedIdx, seedNode) {
    // If this node is part of a known trade chain (has ticker), trace MKT→…→POS along chain
    let chain = null;
    if (seedNode.ticker) {
      const tc = [...tradeChains.values()].find(c => c.ticker === seedNode.ticker);
      if (tc && tc.chain && tc.chain.length) chain = tc.chain;
    }
    // A안 cleanup (Jin 2026-04-27): GROUP 제거 → 7-stage chain (was 8)
    const ordered = ['mkt','watch','brain','strat','reg','exec','pos'];
    const orderedFull = [
      'Market Universe', 'Signal Watchlist', 'AI Decisions', 'Strategy',
      'Regime Context', 'Execution', 'Live Position'
    ];
    const orderedHex = ['#ffaf87','#87ffd7','#d7afff','#ff9f87','#d7d787','#87afd7','#87d7ff'];
    let lines = [];
    if (chain) {
      // Resolve each chain index to label
      lines = chain.map((nidx, i) => {
        const cn = nodes[nidx];
        if (!cn) return '';
        const isSeed = nidx === seedIdx;
        const cI = ordered.indexOf(cn.cluster);
        const c = cI >= 0 ? orderedHex[cI] : (TIER_HEX[cn.tier] || '#fff');
        const arrow = i < chain.length - 1 ? '<div class="arrow">↓</div>' : '';
        return `<div class="chain-step ${isSeed ? 'seed' : ''}">
          <span class="swatch" style="background:${c};color:${c}"></span>
          <span class="lbl">${cn.label || cn.id}</span>
        </div>${arrow}`;
      });
    } else {
      // Fall back: show generic 7-stage pipeline order
      lines = ordered.map((cl, i) => {
        const c = orderedHex[i];
        const isSeed = seedNode.cluster === cl;
        const arrow = i < ordered.length - 1 ? '<div class="arrow">↓</div>' : '';
        return `<div class="chain-step ${isSeed ? 'seed' : ''}">
          <span class="swatch" style="background:${c};color:${c}"></span>
          <span class="lbl">${orderedFull[i]}</span>
        </div>${arrow}`;
      });
    }
    return lines.join('');
  }

  function closeDetailPanel() {
    if (detailPanel) detailPanel.classList.remove('visible');
    chainHighlightIdx = -1;
    // Phase 3 chain provenance (Jin 2026-04-27): also clear BFS chain highlight
    chainActive = false;
    chainNodes.clear();
    chainEdges.clear();
  }

  // ── Chain provenance highlight (Jin 2026-04-27 — 4ef775a0 BFS extract) ──
  // 노드 클릭 시 양방향 BFS frontier 확장 (deps + dependents) → 관련 chain bright,
  // 다른 노드/edge dim. ESC / 빈 클릭 → clearChain.
  // 기존 chainHighlightIdx (POS ticker chain spark) 와 공존 — 호환 보존.
  let chainActive = false;
  const chainNodes = new Set();
  const chainEdges = new Set();
  const CHAIN_BFS_MAX_DEPTH = 5;

  function highlightChainBfs(nodeIdx) {
    chainNodes.clear();
    chainEdges.clear();
    if (nodeIdx < 0 || nodeIdx >= nodes.length) {
      chainActive = false;
      return;
    }
    chainActive = true;
    chainNodes.add(nodeIdx);
    let frontier = [nodeIdx];
    const visited = new Set([nodeIdx]);
    let depth = 0;
    while (frontier.length && depth < CHAIN_BFS_MAX_DEPTH) {
      const next = [];
      for (const idx of frontier) {
        for (let e = 0; e < edges.length; e++) {
          const ed = edges[e];
          const a = ed.a, b = ed.b;
          if (a === idx && !visited.has(b)) {
            visited.add(b); chainNodes.add(b); chainEdges.add(e); next.push(b);
          } else if (b === idx && !visited.has(a)) {
            visited.add(a); chainNodes.add(a); chainEdges.add(e); next.push(a);
          }
        }
      }
      frontier = next;
      depth++;
    }
  }

  // ── Lifecycle chain highlight (Jin 2026-04-27) ──
  // BFS mesh 대체: 클릭한 노드 의 entity (ticker / strategy) 와 연관된
  // trade lifecycle radial path 만 highlight.
  // 흐름: mkt → provider → watch → strat → brain → exec → pos → exit_tally
  // Source: snapshot.py 의 graph.json["lifecycle_paths"] (recent open + closed)
  function highlightLifecycle(nodeIdx) {
    chainNodes.clear();
    chainEdges.clear();
    if (nodeIdx < 0 || nodeIdx >= nodes.length) {
      chainActive = false;
      return;
    }
    const clicked = nodes[nodeIdx];
    const ticker = clicked.ticker || null;
    // Strategy detection: STRAT cluster 노드 label 이 strategy_id
    const strategy = (clicked.cluster === 'strat') ? (clicked.label || null) : null;

    const lifecyclePaths = window._lifecyclePaths || [];
    const matched = lifecyclePaths.filter(p =>
      (ticker && p.trigger_ticker === ticker) ||
      (strategy && p.trigger_strategy === strategy)
    );

    // Helper: id → idx (one-time map per call)
    const idMap = new Map();
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i] && nodes[i].id) idMap.set(nodes[i].id, i);
    }

    if (matched.length === 0) {
      // Fallback: same-ticker radial cluster (no specific lifecycle data)
      chainActive = true;
      chainNodes.add(nodeIdx);
      if (ticker) {
        for (let i = 0; i < nodes.length; i++) {
          if (nodes[i].ticker === ticker) chainNodes.add(i);
        }
      }
      return;
    }

    chainActive = true;
    for (const path of matched) {
      const ids = path.node_ids || [];
      const idxList = [];
      for (const nid of ids) {
        const i = idMap.get(nid);
        if (i !== undefined) {
          chainNodes.add(i);
          idxList.push(i);
        }
      }
      // Sequential edges along resolved path
      for (let k = 0; k < idxList.length - 1; k++) {
        const fromIdx = idxList[k];
        const toIdx = idxList[k + 1];
        for (let e = 0; e < edges.length; e++) {
          const ed = edges[e];
          if ((ed.a === fromIdx && ed.b === toIdx) ||
              (ed.a === toIdx && ed.b === fromIdx)) {
            chainEdges.add(e);
            break;
          }
        }
      }
    }
  }

  function clearChain() {
    chainActive = false;
    chainNodes.clear();
    chainEdges.clear();
    chainHighlightIdx = -1;
  }

  // Public API for highlight/clear chain + cluster pulse
  window.PolarisCloud = window.PolarisCloud || {};
  window.PolarisCloud.highlightChain = (idx) => {
    chainHighlightIdx = idx;
    highlightLifecycle(idx);
  };
  window.PolarisCloud.clearChain = () => { clearChain(); };
  window.PolarisCloud.openDetail = openDetailPanel;
  window.PolarisCloud.closeDetail = closeDetailPanel;
  window.PolarisCloud.pulseCluster = (cluster, strength = 1.0) => {
    for (const n of nodes) {
      if (n.cluster === cluster) n._intensityBump = Math.min(1.0, (n._intensityBump || 0) + strength * 0.5);
    }
  };

  // Jin 2026-04-27 v4: 전체 cluster pulse — system-wide event 만 (regime change 등)
  // Single linear event (trade/signal) 는 cluster 전체 X, 해당 chain link 만 spark
  function pulseTiersStaggered(tierList, delayMs = 80) {
    // A안 cleanup: 14-tier index map (T4 null = group removed). T13 = exit_tally.
    const tierClusters = ['pos','exit','exec','reg',null,'strat','brain','watch','mkt','obs','action','orbit','axis','exit_tally'];
    tierList.forEach((t, i) => {
      const cl = tierClusters[t];
      if (!cl) return;
      setTimeout(() => {
        window.PolarisCloud.pulseCluster(cl, 0.8);
      }, i * delayMs);
    });
  }

  // Jin 2026-04-27 v4: 단일 trade event → 해당 chain 의 segment 별 staggered spark traveling
  // Cluster 전체 pulse 와 구분 (single linear event)
  // reverse=false: outside-in (entry, MKT→POS) / true: inside-out (exit, POS→ACTION)
  function chainSparkCascade(ticker, reverse = false, color = null) {
    const tc = [...tradeChains.values()].find(c => c.ticker === ticker);
    if (!tc || !tc.chain || tc.chain.length < 2) return;
    const ch = reverse ? [...tc.chain].reverse() : tc.chain;
    const segCount = ch.length - 1;
    const fallbackCl = color || tc.color || PROFIT_COLOR;
    // Jin 2026-04-28: per-segment origin cluster color (layer 영향 표시).
    // Caller 의 color (PROFIT/LOSS) 무시 — origin cluster 가 우선.
    for (let s = 0; s < segCount; s++) {
      const aIdx = ch[s], bIdx = ch[s + 1];
      const originNode = nodes[aIdx];
      const segCl = (originNode && CLUSTER_COLORS[originNode.cluster]) || fallbackCl;
      setTimeout(() => {
        chainTransientSparks.push({
          fromIdx: aIdx, toIdx: bIdx, color: segCl,
          born: performance.now(), ttl: 600,
        });
      }, s * 90);
    }
    // Bump intensity on the seed POS only (single, not whole cluster)
    bumpTickerIntensity(ticker, 0.5);
  }
  window.PolarisCloud.chainSparkCascade = chainSparkCascade;

  // Jin 2026-04-27 v4: 3-Tier Activity 구분 (티어 / 메트릭 / 거래)
  //
  //   1. **Tier activity** (system-wide): regime change, market-wide event
  //      → pulseCluster() entire cluster nodes _intensityBump (전체 반짝)
  //
  //   2. **Metric activity** (single node, narrow scope): specific cell, single AI module fire
  //      → metricRipple(nodeIdx) single node bump + small ring around it (좁은 영향)
  //
  //   3. **Trade activity** (single linear chain): ticker-specific entry/exit/signal
  //      → chainSparkCascade(ticker) chain link 단위 staggered spark (해당 link 만)
  //
  // 이렇게 분리하면 visual noise 감소 + event scope 명확히 visible

  // Metric ripple — 단일 노드 활동 (좁은 ring + 노드 bump)
  const metricRipples = [];     // { nodeIdx, color, born, ttl }
  function metricRipple(nodeIdx, color = null) {
    if (nodeIdx < 0 || nodeIdx >= nodes.length) return;
    const n = nodes[nodeIdx];
    const cl = color || CLUSTERS[n.cluster] || [200, 200, 200];
    n._intensityBump = Math.min(1.0, (n._intensityBump || 0) + 0.45);
    metricRipples.push({
      nodeIdx, color: cl,
      born: performance.now(), ttl: 900,
    });
  }
  function metricRippleByLabel(cluster, label, color = null) {
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === cluster && nodes[i].label === label) {
        metricRipple(i, color); return i;
      }
    }
    return -1;
  }
  // Jin 2026-04-28 — small surface effect on cluster + ticker match (signal
  // pass/reject visualization). 표면 활동성 표시. magnitude 작음.
  function smallRippleByClusterTicker(cluster, ticker, color, magnitude = 0.20) {
    if (!ticker) return -1;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === cluster && nodes[i].ticker === ticker) {
        const n = nodes[i];
        const cl = color || CLUSTERS[n.cluster] || [200, 200, 200];
        n._intensityBump = Math.min(1.0, (n._intensityBump || 0) + magnitude);
        metricRipples.push({
          nodeIdx: i, color: cl,
          born: performance.now(), ttl: 600,    // shorter ttl than full ripple
        });
        return i;
      }
    }
    return -1;
  }
  window.PolarisCloud.metricRipple = metricRipple;
  window.PolarisCloud.metricRippleByLabel = metricRippleByLabel;
  window.PolarisCloud.smallRippleByClusterTicker = smallRippleByClusterTicker;

  function drawMetricRipples(projected, now) {
    for (let i = metricRipples.length - 1; i >= 0; i--) {
      const r = metricRipples[i];
      const t = (now - r.born) / r.ttl;
      if (t >= 1) { metricRipples.splice(i, 1); continue; }
      const p = projected[r.nodeIdx];
      if (!p) continue;
      if (p.depth < -0.55) continue;
      const radius = 4 + t * 12;       // 좁은 ring (max ~16px, 메트릭 단위 narrow scope)
      const life = 1 - t;
      const cl = r.color;
      ctx.save();
      ctx.shadowColor = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.85 * life})`;
      ctx.shadowBlur = 10 * life;
      ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.7 * life})`;
      ctx.lineWidth = 1.0 * life + 0.3;
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  // Per-segment transient sparks for single linear events
  const chainTransientSparks = [];
  function drawChainTransientSparks(projected, now) {
    for (let i = chainTransientSparks.length - 1; i >= 0; i--) {
      const sp = chainTransientSparks[i];
      const t = (now - sp.born) / sp.ttl;
      if (t >= 1) { chainTransientSparks.splice(i, 1); continue; }
      const a = projected[sp.fromIdx], b = projected[sp.toIdx];
      if (!a || !b) continue;
      if (a.depth < -0.55 && b.depth < -0.55) continue;
      // Lifecycle: head spark traveling along segment
      const px = a.sx + (b.sx - a.sx) * t;
      const py = a.sy + (b.sy - a.sy) * t;
      const life = (t < 0.15) ? (t / 0.15) : (t > 0.85 ? (1 - t) / 0.15 : 1.0);
      const cl = sp.color;
      // Bright glow line along segment (transient flash on link)
      ctx.shadowColor = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.7 * life})`;
      ctx.shadowBlur = 10;
      ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.45 * life})`;
      ctx.lineWidth = 1.2 * life + 0.4;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      // Traveling head
      ctx.fillStyle = `rgba(255,255,255,${0.85 * life})`;
      ctx.beginPath();
      ctx.arc(px, py, 1.8, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  // Jin 2026-04-27 v4: 위성 (BRAIN/OBS/ACTION) → 해당 노드로 signal beam transient
  // 위성 firing 시 관련 cluster 노드로 짧은 lightning beam (1.5초 fade)
  const satelliteSignals = [];   // { from, to, color, born, ttl, kind }
  function spawnSatelliteSignal(satCluster, ticker, aiTier = null) {
    // Find nearest firing node in satCluster (or AI HIGH if aiTier='high')
    let fromIdx = -1;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.cluster !== satCluster) continue;
      if (aiTier && n.ai_tier !== aiTier) continue;
      if (n.state === 'dormant') continue;
      fromIdx = i;
      break;
    }
    if (fromIdx < 0) return;
    // Find target POS node by ticker
    let toIdx = -1;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === 'pos' && nodes[i].ticker === ticker) {
        toIdx = i;
        break;
      }
    }
    if (toIdx < 0) return;
    const cl = CLUSTERS[satCluster] || [255, 255, 255];
    satelliteSignals.push({
      from: fromIdx, to: toIdx, color: cl,
      born: performance.now(), ttl: 1500,
    });
  }
  window.PolarisCloud.spawnSatelliteSignal = spawnSatelliteSignal;

  // Jin 2026-04-28 v17 — provider 위성에서 ticker 로 lightning beam.
  // signal_pass 시 "시그널 프로바이더들이 시그널 잡힐떄마다 이펙트 거기서
  // 나가야지" 사용자 mandate. firing provider 위성 중 1-2 random 선택 →
  // ticker (POS / WATCH / MKT 매칭 우선순위) 로 staggered beam.
  // Jin v20: 위성 kind 별 own color — beam 이 너무 녹/빨 단조 → 위성 자기 색.
  const ORBIT_KIND_COLOR = {
    provider:     [120, 200, 255],   // sky blue (data feed)
    sensor:       [180, 140, 255],   // purple (regime sensing)
    regime_infra: [220, 200, 130],   // gold (regime infra)
    learner:      [130, 230, 200],   // teal (hourly learner)
    ai_judge:     [255, 150, 220],   // pink (AI judge)
    brain_tool:   [200, 170, 255],   // violet (AI tool)
    brain_data:   [200, 170, 255],   // violet
    brain_entry:  [200, 220, 130],   // lime (entry decision)
    brain_exit:   [255, 130, 130],   // coral (exit decision)
    exec_gate:    [135, 175, 215],   // blue (gate)
    exec_router:  [100, 180, 230],   // navy blue (router)
    exec_tool:    [135, 175, 215],   // blue
    exit_engine:  [255, 135, 215],   // magenta (exit logic)
  };

  // Jin 2026-04-30 Phase 4 T22: SPOT bot kind-specific colors (lime green family)
  const SPOT_KIND_COLOR = {
    pos_spot:   [0x00, 0xff, 0x88],
    strat_spot: [0x44, 0xff, 0xaa],
  };

  function spawnProviderToTickerBeam(ticker, color) {
    if (!ticker) return;
    // Find target — prefer POS (open position), then WATCH (active scoring),
    // then MKT (universe ticker).
    let toIdx = -1;
    for (const cluster of ['pos', 'watch', 'mkt']) {
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].cluster === cluster && nodes[i].ticker === ticker) {
          toIdx = i;
          break;
        }
      }
      if (toIdx >= 0) break;
    }
    if (toIdx < 0) return;
    // Collect firing provider satellites.
    const providers = [];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.cluster === 'orbit'
          && (n.orbit_kind === 'provider' || n.orbit_kind === 'sensor')
          && n.state === 'firing') {
        providers.push(i);
      }
    }
    if (!providers.length) return;
    // Pick up to 2 random firing providers — staggered emission.
    const pickCount = Math.min(2, providers.length);
    const used = new Set();
    for (let k = 0; k < pickCount; k++) {
      let pickI;
      let tries = 0;
      do {
        pickI = providers[Math.floor(Math.random() * providers.length)];
        tries++;
      } while (used.has(pickI) && tries < 6);
      used.add(pickI);
      // Jin v20: 위성 자기 kind color 사용 (단조 녹/빨 → 위성별 색)
      const provNode = nodes[pickI];
      const beamColor = ORBIT_KIND_COLOR[provNode.orbit_kind] || color || [180, 220, 100];
      satelliteSignals.push({
        from: pickI, to: toIdx, color: beamColor,
        born: performance.now() + k * 80, ttl: 1200,
      });
    }
  }
  window.PolarisCloud.spawnProviderToTickerBeam = spawnProviderToTickerBeam;

  // Jin v4: Outbound arc — POS → ACTION 위성 quadratic arc (close 시 outward flow)
  const outboundArcs = [];     // { fromIdx, toIdx, color, born, ttl }
  function spawnOutboundArc(ticker, satCluster = 'action') {
    let fromIdx = -1, toIdx = -1;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === 'pos' && nodes[i].ticker === ticker) {
        fromIdx = i; break;
      }
    }
    if (fromIdx < 0) return;
    // Pick first firing node in target cluster (ACTION ring 첫 firing)
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === satCluster && nodes[i].state !== 'dormant') {
        toIdx = i; break;
      }
    }
    if (toIdx < 0) {
      // Fallback: any node in cluster
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].cluster === satCluster) { toIdx = i; break; }
      }
    }
    if (toIdx < 0) return;
    const cl = CLUSTERS[satCluster] || [255, 175, 135];
    outboundArcs.push({
      fromIdx, toIdx, color: cl,
      born: performance.now(), ttl: 1800,
    });
  }
  window.PolarisCloud.spawnOutboundArc = spawnOutboundArc;

  function drawOutboundArcs(projected, now) {
    for (let i = outboundArcs.length - 1; i >= 0; i--) {
      const arc = outboundArcs[i];
      const t = (now - arc.born) / arc.ttl;
      if (t >= 1) { outboundArcs.splice(i, 1); continue; }
      const a = projected[arc.fromIdx], b = projected[arc.toIdx];
      if (!a || !b) continue;
      // Quadratic arc bowed outward (perpendicular bow)
      const mx = (a.sx + b.sx) * 0.5, my = (a.sy + b.sy) * 0.5;
      const dx = b.sx - a.sx, dy = b.sy - a.sy;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const cx = mx + nx * len * 0.25, cy = my + ny * len * 0.25;
      // Lifecycle: warm fade
      const life = Math.max(0, 1 - t);
      const cl = arc.color;
      ctx.save();
      ctx.shadowColor = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.9 * life})`;
      ctx.shadowBlur = 14 * life;
      ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.65 * life})`;
      ctx.lineWidth = 1.4 * life + 0.3;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.quadraticCurveTo(cx, cy, b.sx, b.sy);
      ctx.stroke();
      // Traveling head
      const it = 1 - t;
      const px = it * it * a.sx + 2 * it * t * cx + t * t * b.sx;
      const py = it * it * a.sy + 2 * it * t * cy + t * t * b.sy;
      ctx.shadowBlur = 0;
      ctx.fillStyle = `rgba(255,245,210,${0.85 * life})`;
      ctx.beginPath();
      ctx.arc(px, py, 2.4 * (0.5 + life * 0.6), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  // Jin v4: Supernova — 큰 trade close 시 explosive ring (큰 PnL 임팩트)
  const supernovas = [];        // { nodeIdx, color, t, magnitude }
  function spawnSupernova(ticker, magnitude = 1.0, color = null) {
    let idx = -1;
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].cluster === 'pos' && nodes[i].ticker === ticker) { idx = i; break; }
    }
    if (idx < 0) return;
    supernovas.push({
      nodeIdx: idx,
      color: color || (magnitude > 0 ? PROFIT_COLOR : LOSS_COLOR),
      t: 0, magnitude: Math.min(2.5, Math.max(0.5, magnitude)),
    });
  }
  window.PolarisCloud.spawnSupernova = spawnSupernova;

  function drawSupernovas(projected) {
    for (let i = supernovas.length - 1; i >= 0; i--) {
      const s = supernovas[i];
      if (s.t > 1) { supernovas.splice(i, 1); continue; }
      const node = projected[s.nodeIdx];
      if (!node) { s.t += 0.025; continue; }
      const expand = s.t * 50 * s.magnitude;
      const life = 1 - s.t;
      ctx.save();
      ctx.shadowColor = rgba(s.color, life);
      ctx.shadowBlur = 32 * life;
      ctx.strokeStyle = rgba(s.color, life);
      ctx.lineWidth = 2.0 * life + 0.4;
      ctx.beginPath();
      ctx.arc(node.sx, node.sy, expand, 0, Math.PI * 2);
      ctx.stroke();
      // Inner ghost ring
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = rgba(s.color, life * 0.4);
      ctx.beginPath();
      ctx.arc(node.sx, node.sy, expand * 0.7, 0, Math.PI * 2);
      ctx.stroke();
      // Bright core dot
      ctx.fillStyle = `rgba(255,255,255,${life})`;
      ctx.beginPath();
      ctx.arc(node.sx, node.sy, 5 * life + 1, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      s.t += 0.022;
    }
  }

  // Jin v4: Edge traveling sparks — 가끔 random intra-shell/radial edge 에 spark traveling (ambient cinematic)
  const edgeSparks = [];        // { edgeIdx, t, color, born, ttl }
  let lastEdgeSparkAt = 0;
  function maybeSpawnEdgeSpark(now) {
    // ~매 6초 평균 1개 (ambient cinematic, 너무 많으면 시끄러움)
    if (now - lastEdgeSparkAt < 5000 + Math.random() * 4000) return;
    if (!edges.length) return;
    lastEdgeSparkAt = now;
    // Pick a random edge from active tier (1-6, excluding T0 POS / T7 MKT noise)
    const candidates = [];
    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      if (e.tier >= 1 && e.tier <= 6) candidates.push(i);
    }
    if (!candidates.length) return;
    const ei = candidates[Math.floor(Math.random() * candidates.length)];
    const e = edges[ei];
    // A안 cleanup: T4 null (group removed)
    const tierClusters = ['pos','exit','exec','reg',null,'strat','brain','watch','mkt'];
    const cl = CLUSTERS[tierClusters[e.tier]] || [200, 200, 200];
    edgeSparks.push({
      edgeIdx: ei, color: cl,
      born: now, ttl: 900 + Math.random() * 400,
    });
  }
  function drawEdgeSparks(projected, now) {
    for (let i = edgeSparks.length - 1; i >= 0; i--) {
      const sp = edgeSparks[i];
      const t = (now - sp.born) / sp.ttl;
      if (t >= 1) { edgeSparks.splice(i, 1); continue; }
      const e = edges[sp.edgeIdx];
      if (!e) { edgeSparks.splice(i, 1); continue; }
      const a = projected[e.a], b = projected[e.b];
      if (!a || !b) continue;
      if (a.depth < -0.55 && b.depth < -0.55) continue;
      const px = a.sx + (b.sx - a.sx) * t;
      const py = a.sy + (b.sy - a.sy) * t;
      const life = (t < 0.2) ? (t / 0.2) : (t > 0.8 ? (1 - t) / 0.2 : 1.0);
      const cl = sp.color;
      ctx.shadowColor = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.75 * life})`;
      ctx.shadowBlur = 6;
      ctx.fillStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${0.7 * life})`;
      ctx.beginPath();
      ctx.arc(px, py, 1.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  function drawSatelliteSignals(projected, now) {
    if (!satelliteSignals.length) return;
    for (let i = satelliteSignals.length - 1; i >= 0; i--) {
      const sig = satelliteSignals[i];
      const t = (now - sig.born) / sig.ttl;
      if (t >= 1) { satelliteSignals.splice(i, 1); continue; }
      const a = projected[sig.from], b = projected[sig.to];
      if (!a || !b) continue;
      if (a.depth < -0.55 && b.depth < -0.55) continue;
      // Lifecycle: 0-0.3 grow, 0.3-0.7 sustain bright, 0.7-1.0 fade
      let progress = 1.0;
      if (t < 0.3) progress = t / 0.3;
      else if (t > 0.7) progress = (1 - t) / 0.3;
      const alpha = 0.55 * progress;
      // Lightning-like jagged path (시안 + 우리 지지직 effect)
      const dx = b.sx - a.sx, dy = b.sy - a.sy;
      const len = Math.sqrt(dx*dx + dy*dy) || 1;
      const px = -dy / len, py = dx / len;
      const segments = 5;
      const jitter = 2.5;
      const cl = sig.color;
      ctx.shadowColor = `rgba(${cl[0]},${cl[1]},${cl[2]},${alpha})`;
      ctx.shadowBlur = 8;
      ctx.strokeStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${alpha})`;
      ctx.lineWidth = 0.8 + progress * 0.8;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      for (let j = 1; j < segments; j++) {
        const tt = j / segments;
        const taper = 1 - Math.abs(2 * tt - 1);
        const offset = (Math.random() - 0.5) * jitter * taper;
        ctx.lineTo(a.sx + dx * tt + px * offset, a.sy + dy * tt + py * offset);
      }
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      // Bright spark at target
      if (progress > 0.7) {
        ctx.fillStyle = `rgba(255,255,255,${0.6 * progress})`;
        ctx.beginPath();
        ctx.arc(b.sx, b.sy, 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.shadowBlur = 0;
  }

  // Legend click → pulse cluster (Jin v4 차용)
  document.addEventListener('click', (e) => {
    const row = e.target.closest('.pipeline-strip .lg');
    if (!row) return;
    // Identify cluster by dot color or label text
    const txt = (row.textContent || '').toLowerCase();
    let cluster = null;
    if (txt.includes('live position')) cluster = 'pos';
    else if (txt.includes('exit pattern')) cluster = 'exit';
    else if (txt.includes('execution')) cluster = 'exec';
    else if (txt.includes('regime')) cluster = 'reg';
    else if (txt.includes('strateg')) cluster = 'strat';
    else if (txt.includes('brain')) cluster = 'brain';
    else if (txt.includes('watch')) cluster = 'watch';
    else if (txt.includes('market')) cluster = 'mkt';
    else if (txt.includes('health') || txt.includes('obs')) cluster = 'obs';
    else if (txt.includes('action')) cluster = 'action';
    if (cluster) window.PolarisCloud.pulseCluster(cluster, 1.0);
  });

  // Keyboard: R = reset view, Space = toggle auto-rotate, Esc = close detail panel
  // Jin 2026-04-27: v4 시안 차용 (Space + Esc 추가)
  let autoRotateEnabled = true;
  window.addEventListener('keydown', (ev) => {
    if (ev.key === 'r' || ev.key === 'R') {
      yaw = 0; pitch = 0.18; zoom = 1.0;
      focusNodeIdx = -1; clickedNodeIdx = -1;
      lastInteractionAt = performance.now();
    } else if (ev.key === ' ' || ev.code === 'Space') {
      autoRotateEnabled = !autoRotateEnabled;
      lastInteractionAt = performance.now();
      ev.preventDefault();
    } else if (ev.key === 'Escape') {
      closeDetailPanel();
      clickedNodeIdx = -1;
      focusNodeIdx = -1;
      // Phase 3 chain provenance (Jin 2026-04-27): ESC clears chain highlight
      clearChain();
    }
  });

  // Click → focus + highlight chain + open detail panel (Jin v4)
  let clickedNodeIdx = -1;
  let focusNodeIdx = -1;
  let chainHighlightIdx = -1;
  canvas.addEventListener('click', (ev) => {
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const idx = findHoverNode(mx, my);
    if (idx < 0) {
      clickedNodeIdx = -1;
      focusNodeIdx = -1;
      // Phase 3 chain provenance (Jin 2026-04-27): empty click clears chain highlight
      clearChain();
      closeDetailPanel();
      return;
    }
    clickedNodeIdx = idx;
    focusNodeIdx = idx;
    chainHighlightIdx = idx;
    // Lifecycle chain (Jin 2026-04-27): radial path mkt→provider→watch→strat→brain→exec→pos→exit
    // (BFS mesh 대체 — Jin "노드끼리 연결 아니라 라이프사이클 연결")
    highlightLifecycle(idx);
    openDetailPanel(idx);
    // Click-zoom: gradual zoom in toward node (handled in main loop)
    lastInteractionAt = performance.now();
  });

  canvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    // Trackpad pinch = ctrlKey true; scroll = false
    const delta = ev.deltaY > 0 ? 0.92 : 1.08;
    zoom = Math.max(0.4, Math.min(2.5, zoom * delta));
    lastInteractionAt = performance.now();
  }, { passive: false });

  canvas.addEventListener('mousedown', (ev) => {
    dragging = true;
    dragStartX = ev.clientX; dragStartY = ev.clientY;
    dragYaw0 = yaw; dragPitch0 = pitch;
    canvas.style.cursor = 'grabbing';
    lastInteractionAt = performance.now();
  });
  window.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      canvas.style.cursor = 'grab';
      lastInteractionAt = performance.now();
    }
  });
  let _lastHoverAt = 0;
  window.addEventListener('mousemove', (ev) => {
    if (dragging) {
      const dx = ev.clientX - dragStartX;
      const dy = ev.clientY - dragStartY;
      yaw = dragYaw0 + dx * 0.005;
      pitch = Math.max(-1.2, Math.min(1.2, dragPitch0 + dy * 0.005));
      lastInteractionAt = performance.now();
      hideTooltip();
      return;
    }
    // Throttle hover hit-test to ~30fps
    const now = performance.now();
    if (now - _lastHoverAt < 33) return;
    _lastHoverAt = now;
    const rect = canvas.getBoundingClientRect();
    if (ev.clientX < rect.left || ev.clientX > rect.right ||
        ev.clientY < rect.top || ev.clientY > rect.bottom) {
      hideTooltip();
      return;
    }
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const idx = findHoverNode(mx, my);
    if (idx !== hoverNodeIdx) hoverNodeIdx = idx;
    if (idx >= 0) showTooltip(nodes[idx], mx, my);
    else hideTooltip();
  });
  canvas.style.cursor = 'grab';

  // ── Live position list (left rail) ──────────────────────────────────────
  function fmtAge(entryTs) {
    if (!entryTs) return '--';
    const sec = Math.max(0, Math.floor(Date.now() / 1000 - entryTs));
    if (sec < 60) return sec + 's';
    const min = Math.floor(sec / 60);
    if (min < 60) return min + 'm';
    const hr = Math.floor(min / 60);
    return hr + 'h' + (min % 60) + 'm';
  }
  function fmtSize(usd) {
    if (!usd) return '--';
    if (usd >= 10000) return '$' + (usd / 1000).toFixed(1) + 'k';
    if (usd >= 1000) return '$' + (usd / 1000).toFixed(2) + 'k';
    return '$' + usd.toFixed(0);
  }
  function fmtPnl(usd) {
    if (usd == null) return '--';
    const sign = usd >= 0 ? '+' : '';
    return sign + '$' + usd.toFixed(2);
  }
  function fmtAgeSec(sec) {
    if (sec == null || sec < 0) return '--';
    if (sec < 60) return Math.floor(sec) + 's';
    const min = Math.floor(sec / 60);
    if (min < 60) return min + 'm';
    return Math.floor(min / 60) + 'h' + (min % 60) + 'm';
  }
  function exchangeShort(ex) {
    const m = { okx: 'OKX', capital: 'CAP', cap: 'CAP', alpaca: 'ALP', alp: 'ALP', binance: 'BIN', bin: 'BIN' };
    return m[(ex || '').toLowerCase()] || (ex || '?').toUpperCase().slice(0, 3);
  }
  function fmtPrice(p) {
    if (!p) return '--';
    if (p >= 1000) return p.toFixed(0);
    if (p >= 100)  return p.toFixed(1);
    if (p >= 1)    return p.toFixed(3);
    if (p >= 0.01) return p.toFixed(4);
    return p.toFixed(6);
  }
  function fmtPct(v) {
    if (v == null) return '--';
    const sign = v >= 0 ? '+' : '';
    return sign + v.toFixed(2) + '%';
  }
  function renderPositionList(liveTrades) {
    const el = document.getElementById('pos-list');
    if (!el) return;
    el.innerHTML = '';
    // Show ALL open positions (scrollable)
    for (const t of liveTrades) {
      const row = document.createElement('div');
      row.className = 'plrow';
      const dirClass = t.direction === 'short' ? 'short' : 'long';
      const dirChar = t.direction === 'short' ? 'S' : 'L';
      const pct = t.pnl_pct || 0;
      const pctClass = pct >= 0 ? 'pos' : 'neg';
      row.innerHTML = `
        <span class="ex">${exchangeShort(t.exchange)}</span>
        <span class="tk">${t.ticker}</span>
        <span class="dir ${dirClass}">${dirChar}</span>
        <span class="px">${fmtPrice(t.current_price)}</span>
        <span class="pn ${pctClass}">${fmtPct(pct)}</span>`;
      el.appendChild(row);
    }
    const counter = document.getElementById('pos-count');
    if (counter) counter.textContent = liveTrades.length;
  }
  // Cache for in-canvas labels
  let _currentExchangePnls = [];

  function drawExchangeLabels() {
    if (!_currentExchangePnls.length) return;
    ctx.save();
    ctx.font = 'bold 11px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    const sphereR = Math.min(W, H) * 0.36;
    for (const e of _currentExchangePnls) {
      const key = exchangeKey(e.exchange);
      const c = POS_SUB_CENTERS[key];
      if (!c) continue;
      // Project the sub-cluster center through current camera
      const tmp = { sx: 0, sy: 0, depth: 0, persp: 1 };
      projectInto({ x: c[0], y: c[1], z: c[2] }, tmp);
      if (tmp.depth < -0.45) continue;  // hidden behind sphere
      const pnl = e.pnl_usd || 0;
      const cl = pnl > 0.5 ? PROFIT_COLOR : pnl < -0.5 ? LOSS_COLOR : NEUTRAL_COLOR;
      const front = (tmp.depth + 1) * 0.5;
      const alpha = 0.55 + front * 0.40;
      const lbl = `${exchangeShort(e.exchange)} · ${e.count} · ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}`;
      // Subtle stroke for legibility on busy backdrop
      ctx.lineWidth = 3;
      ctx.strokeStyle = `rgba(5,7,11,${0.85 * alpha})`;
      ctx.strokeText(lbl, tmp.sx, tmp.sy + 22);
      ctx.fillStyle = `rgba(${cl[0]},${cl[1]},${cl[2]},${alpha})`;
      ctx.fillText(lbl, tmp.sx, tmp.sy + 22);
    }
    ctx.restore();
  }

  function renderExchangePnl(exPnls) {
    _currentExchangePnls = exPnls || [];
    const el = document.getElementById('expnl-list');
    if (!el) return;
    el.innerHTML = '';
    const sorted = exPnls.slice().sort((a, b) => b.size_usd - a.size_usd);
    for (const e of sorted) {
      const row = document.createElement('div');
      row.className = 'expnl-row';
      const pnl = e.pnl_usd || 0;
      const cls = pnl >= 0 ? 'pos' : 'neg';
      row.innerHTML = `
        <span class="ex">${exchangeShort(e.exchange)}</span>
        <span class="cnt">${e.count} pos</span>
        <span class="pn ${cls}">${fmtPnl(pnl)}</span>`;
      el.appendChild(row);
    }
  }
  function renderRecentCloses(closes) {
    const el = document.getElementById('cls-list');
    if (!el) return;
    el.innerHTML = '';
    // Show ALL recent closes (scrollable)
    for (const c of closes) {
      const row = document.createElement('div');
      row.className = 'plrow cls';
      const dirClass = c.direction === 'short' ? 'short' : 'long';
      const dirChar = c.direction === 'short' ? 'S' : 'L';
      const pnl = c.pnl_usd || 0;
      const pnlClass = pnl >= 0 ? 'pos' : 'neg';
      row.innerHTML = `
        <span class="ex">${exchangeShort(c.exchange)}</span>
        <span class="tk">${c.ticker}</span>
        <span class="dir ${dirClass}">${dirChar}</span>
        <span class="pn ${pnlClass}">${fmtPnl(pnl)}</span>
        <span class="exit">${(c.exit_type || '').slice(0,5)}</span>`;
      el.appendChild(row);
    }
    const counter = document.getElementById('cls-count');
    if (counter) counter.textContent = closes.length;
  }

  // ── Data load ───────────────────────────────────────────────────────────
  async function loadGraph() {
    try {
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch('/static/graph.json?t=' + Date.now(), { signal: ctrl.signal });
      clearTimeout(tid);
      if (!r.ok) throw new Error('graph fetch ' + r.status);
      const d = await r.json();
      // Jin 2026-04-28: defer heavy rebuild work to next idle frame so the
      // 60s-interval fetch doesn't block the active frame (rendering lag
      // "데이터 로드 시 lag" 보고). requestIdleCallback 우선, 없으면 setTimeout 0.
      await new Promise(resolve => {
        if (typeof requestIdleCallback === 'function') {
          requestIdleCallback(() => resolve(), { timeout: 100 });
        } else {
          setTimeout(resolve, 0);
        }
      });
      const N = d.nodes.length;

      // Lifecycle paths (Jin 2026-04-27) — store for click → highlightLifecycle lookup
      window._lifecyclePaths = d.lifecycle_paths || [];

      // Jin 2026-04-27: ID-based geometry reuse — refresh 시 같은 id 노드 위치 보존 (no jump)
      // 이전 nodes 의 geom 을 id → geom map 으로 저장
      const oldGeomById = new Map();
      for (const n of nodes) {
        if (n && n.id) {
          oldGeomById.set(n.id, {
            x: n.x, y: n.y, z: n.z, phase: n.phase,
            _baseTheta: n._baseTheta, _ringR: n._ringR, _yPos: n._yPos,
            _basePos: n._basePos,                    // Phase 3 (Jin 2026-04-27)
            _orbitAngle: n._orbitAngle,              // accumulated angle preserved across refresh
          });
        }
      }
      const sameTopo = nodes.length === N && nodes.every((n, i) => n.tier === d.nodes[i].tier);
      let needsBuild = false;
      const nodeRefs = d.nodes.map((dn) => {
        const out = { ...dn };
        const old = oldGeomById.get(dn.id);
        if (old && old.x !== undefined) {
          out.x = old.x; out.y = old.y; out.z = old.z; out.phase = old.phase;
          if (old._baseTheta !== undefined) {
            out._baseTheta = old._baseTheta;
            out._ringR = old._ringR;
            out._yPos = old._yPos;
          }
          // Phase 3 (Jin 2026-04-27): preserve category-orbit base + accumulated angle
          if (old._basePos) out._basePos = old._basePos;
          if (typeof old._orbitAngle === 'number') out._orbitAngle = old._orbitAngle;
        } else {
          needsBuild = true;     // 새 id 발견 → build 필요
        }
        return out;
      });
      // sameTopo 또는 needsBuild 둘 다 false 면 모두 reuse — rebuild 스킵 (no jump)
      if (needsBuild || edges.length === 0) {
        // Jin 2026-04-30 랙 fix: heavy rebuild 를 6 chunks 로 분할 (each yields back).
        // 이전: 5 regroup × N nodes 가 한 frame 안에 직렬 → ~150ms stutter.
        // 전환: 각 stage 사이 yield → 30fps 기준 한 frame 5ms 이하 유지.
        const _yield = () => new Promise(r => {
          if (typeof requestIdleCallback === 'function') {
            requestIdleCallback(() => r(), { timeout: 50 });
          } else {
            setTimeout(r, 0);
          }
        });
        const geom = buildGeometryTiered(nodeRefs);
        await _yield();
        regroupPosTier(nodeRefs, geom);
        await _yield();
        regroupMktByGroup(nodeRefs, geom);
        await _yield();
        regroupStratByGroup(nodeRefs, geom);
        await _yield();
        regroupWatchByMkt(nodeRefs, geom);
        await _yield();
        regroupWatchAroundPos(nodeRefs, geom);
        await _yield();
        // Jin 2026-04-27: 새 노드만 geom apply (기존 노드는 oldGeomById 보존됨)
        for (let i = 0; i < N; i++) {
          const g = geom[i];
          if (!g) continue;
          // 이미 oldGeomById 에서 위치 받은 노드는 skip (jump 방지)
          if (nodeRefs[i].x !== undefined && oldGeomById.has(nodeRefs[i].id)) continue;
          nodeRefs[i].x = g.x;
          nodeRefs[i].y = g.y;
          nodeRefs[i].z = g.z;
          nodeRefs[i].phase = g.phase;
          // 위성 meta 도 새 build 결과 반영
          if (g._baseTheta !== undefined) {
            nodeRefs[i]._baseTheta = g._baseTheta;
            nodeRefs[i]._ringR = g._ringR;
            nodeRefs[i]._yPos = g._yPos;
          }
          // Phase 3 (Jin 2026-04-27): _basePos for category-orbit Rodrigues rotation
          if (g._basePos) {
            nodeRefs[i]._basePos = g._basePos;
          }
        }
      }

      // Preserve flash overrides by ticker
      const oldByTicker = new Map();
      for (const n of nodes) {
        if (n.ticker && n._flashUntil) {
          oldByTicker.set(n.ticker, { until: n._flashUntil, orig: n._origState });
        }
      }
      for (const n of nodeRefs) {
        if (n.ticker && oldByTicker.has(n.ticker)) {
          const f = oldByTicker.get(n.ticker);
          n._origState = f.orig || n.state;
          n._flashUntil = f.until;
          n.state = 'firing';
        }
      }

      nodes = nodeRefs;
      if (!sameTopo || edges.length === 0) edges = buildEdgesPerTier(nodes);

      nodeByTicker.clear();
      for (const n of nodes) {
        if (n.ticker && !nodeByTicker.has(n.ticker)) nodeByTicker.set(n.ticker, n);
      }
      decNodes = nodes.filter(n => n.cluster === 'strat');  // Codex#2: 'dec' deprecated → 'strat'
      firingTickers = new Set(nodes.filter(n => n.state === 'firing' && n.ticker).map(n => n.ticker));

      // Rebuild trade chains (DEC → REG → EXEC → POS, colored by PnL)
      const incomingLive = d.live_trades || [];
      const incomingChains = d.trade_chains || [];
      const incomingIds = new Set(incomingChains.map(c => c.trade_id));
      for (const tid of Array.from(tradeChains.keys())) {
        if (!incomingIds.has(tid)) tradeChains.delete(tid);
      }
      for (const c of incomingChains) {
        if (tradeChains.has(c.trade_id)) continue;
        tradeChains.set(c.trade_id, {
          chain: c.chain,
          ticker: c.ticker,
          pnl_usd: c.pnl_usd,
          pnl_pct: c.pnl_pct,
          strength: c.strength != null ? c.strength : 0.5,   // Codex#1: pass through
          win_rate: c.win_rate || 0,
          color: [0x80, 0x80, 0x90],  // Jin "신경망 연결은 grey 디밍" — chain dim grey
          pulseT: Math.random() * Math.max(1, c.chain.length - 1),
        });
      }
      // Update existing chains' colors + strength as data refreshes
      for (const c of incomingChains) {
        const tc = tradeChains.get(c.trade_id);
        if (tc) {
          tc.pnl_usd = c.pnl_usd;
          tc.pnl_pct = c.pnl_pct;
          tc.strength = c.strength != null ? c.strength : tc.strength;
          tc.color = [0x80, 0x80, 0x90];  // chain dim grey (Jin mandate)
        }
      }

      // Build galaxy backdrop from full ticker universe (only on first load or topo change)
      if (!sameTopo || galaxy.length === 0) {
        buildGalaxy(d.galaxy_universe || []);
      }

      renderPositionList(incomingLive);
      renderRecentCloses(d.recent_closes || []);
      renderExchangePnl(d.exchange_pnl || []);

      // Counters
      const s = d.stats;
      const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
      setText('tick', (s.tick || 0).toLocaleString());
      setText('lit-n', s.node_count);
      setText('sps', (s.firing_rate || 0).toFixed(1));
      setText('regime-val', s.regime);
      setText('cluster-count', s.cluster_count);
      setText('node-count', s.node_count);
      setText('node-count2', s.node_count);
      setText('tier-count', s.tier_count);

      if (!animationStarted) {
        animationStarted = true;
        scheduleFrame();
      }
    } catch (e) { console.error('loadGraph error', e); }
  }

  function hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function pickDecIdx(seed) {
    if (!decNodes.length) return -1;
    return decNodes[hashStr(seed || 'x') % decNodes.length].i;
  }

  // ── SSE live events ─────────────────────────────────────────────────────
  function bumpTickerIntensity(ticker, amount) {
    if (!ticker) return;
    const n = nodeByTicker.get(ticker);
    if (!n) return;
    n._intensityBump = Math.min(1.0, (n._intensityBump || 0) + amount);
  }
  function bumpGalaxyTicker(ticker, amount, score, direction) {
    const g = galaxyByTicker.get(ticker);
    if (!g) return;
    g.glow = Math.min(1, g.glow + amount * 0.85 + 0.15);
    g.lastSignalAt = performance.now();
    g.score = score || 0;
    g.direction = direction || null;
  }

  function flashFire(ticker, ttl) {
    if (!ticker) return;
    const n = nodeByTicker.get(ticker);
    if (!n) return;
    if (n.state !== 'firing') n._origState = n.state;
    n.state = 'firing';
    n._flashUntil = performance.now() + (ttl || 6000);
    firingTickers.add(ticker);
  }
  function addLiveTradeChain(tradeId, ticker, strategyId, regimeName) {
    if (!ticker) return;
    if (tradeChains.has(tradeId)) return;
    // Jin 2026-04-30 랙 fix: 동시 live chain cap 30 — 30+ 는 oldest evict
    const MAX_LIVE_CHAINS = 30;
    if (tradeChains.size >= MAX_LIVE_CHAINS) {
      const oldestId = tradeChains.keys().next().value;
      if (oldestId) tradeChains.delete(oldestId);
    }
    const posNode = nodeByTicker.get(ticker);
    if (!posNode) return;
    // Codex#2: full 7-layer chain MKT → WATCH → BRAIN → STRAT → REG → EXEC → POS
    let mktIdx = -1, watchIdx = -1, brainIdx = -1, regIdx = -1, execIdx = -1;
    for (const n of nodes) {
      if (n.ticker === ticker) {
        if (n.cluster === 'mkt' && mktIdx < 0) mktIdx = n.i;
        else if (n.cluster === 'watch' && watchIdx < 0) watchIdx = n.i;
      }
      // A안 cleanup: BRAIN sphere = AI decision results (composer/signal_engine 는 ORBIT 으로 이동).
      // Anchor on first BRAIN node (label-agnostic) to keep chain MKT→…→POS unbroken.
      if (n.cluster === 'brain' && brainIdx < 0) brainIdx = n.i;
      if (n.cluster === 'reg' && regIdx < 0
          && n.label === `regime_${(regimeName || 'neutral').toLowerCase()}`) regIdx = n.i;
      if (n.cluster === 'exec' && execIdx < 0
          && n.label && n.label.startsWith('gate_')) execIdx = n.i;
    }
    const stratIdx = pickDecIdx(strategyId || ticker);
    const chain = [mktIdx, watchIdx, brainIdx, stratIdx, regIdx, execIdx, posNode.i].filter(i => i >= 0);
    tradeChains.set(tradeId, {
      chain, ticker, pnl_usd: 0, pnl_pct: 0, strength: 0.5,
      color: chainColor(0),
      pulseT: 0,
    });
  }
  function removeLiveTradeChainByTicker(ticker) {
    for (const [tid, tc] of tradeChains.entries()) {
      if (tc.ticker === ticker) tradeChains.delete(tid);
    }
  }
  // Jin 2026-04-30 랙 fix: setTicker 250ms 디바운스 (DOM 갱신 폭주 방지)
  // signal_pass 가 분당 100+ 발생 시 DOM textContent write 가 layout thrash 유발.
  let _tickerLastTs = 0;
  let _tickerPending = null;
  function setTicker(s) {
    const _now = performance.now();
    if (_now - _tickerLastTs < 250) {
      _tickerPending = s;
      return;
    }
    _tickerLastTs = _now;
    const el = document.getElementById('ticker');
    if (el) el.textContent = `${new Date().toTimeString().slice(0,8)}  ${s}`;
  }
  // Pending ticker flush — 250ms 마다 마지막 메시지 표시
  setInterval(() => {
    if (_tickerPending) {
      const el = document.getElementById('ticker');
      if (el) el.textContent = `${new Date().toTimeString().slice(0,8)}  ${_tickerPending}`;
      _tickerPending = null;
      _tickerLastTs = performance.now();
    }
  }, 250);
  // Exit effect — outward radial burst (counterpart of entry inward comet)
  function spawnExitBurst(ticker, exitType, direction) {
    const n = nodeByTicker.get(ticker);
    if (!n) return;
    const sx = projectedCache[n.i]?.sx;
    const sy = projectedCache[n.i]?.sy;
    if (sx == null) return;
    // Color by direction (long profit/loss feel)
    const d = (direction || n.direction || '').toLowerCase();
    let col = NEUTRAL_COLOR;
    // Exit type semantic: TP=profit, STOP/KILL=loss, others neutral
    if (exitType === 'TP' || exitType === 'BEP') col = PROFIT_COLOR;
    else if (exitType === 'STOP' || exitType === 'KILL' || exitType === 'TIME') col = LOSS_COLOR;
    else if (d === 'short') col = LOSS_COLOR;
    else if (d === 'long') col = PROFIT_COLOR;
    // Pure in-place ring pop — no flying particles (Jin: 의미없는 별동별 제거)
    spawnShock(sx, sy, col, 'expand');
  }

  // ── AI brain ripple — concentric waves from BRAIN tier node when AI decides ─
  const aiRipples = [];   // { cx, cy, color, born, dur, label }

  function spawnAiRipple(stage) {
    // Find a BRAIN tier node — pick by stage label hash for variety
    const brainNodes = nodes.filter(n => n.cluster === 'brain');
    if (!brainNodes.length) return;
    const idx = hashStr(stage || 'ai') % brainNodes.length;
    const n = brainNodes[idx];
    const p = projectedCache[n.i];
    if (!p) return;
    aiRipples.push({
      cx: p.sx, cy: p.sy,
      color: CLUSTERS.brain,
      born: performance.now(),
      dur: 1700,
      label: stage,
    });
  }
  function drawAiRipples(now) {
    for (let i = aiRipples.length - 1; i >= 0; i--) {
      const r = aiRipples[i];
      const t = (now - r.born) / r.dur;
      if (t >= 1) { aiRipples.splice(i, 1); continue; }
      // Three concentric rings, staggered
      ctx.save();
      for (let ring = 0; ring < 3; ring++) {
        const rt = t - ring * 0.18;
        if (rt <= 0 || rt >= 1) continue;
        const radius = rt * 60;
        const alpha = (1 - rt) * 0.55;
        ctx.strokeStyle = rgba(r.color, alpha);
        ctx.lineWidth = 0.8 + (1 - rt) * 0.6;
        ctx.shadowColor = rgba(r.color, alpha);
        ctx.shadowBlur = 4;
        ctx.beginPath();
        ctx.arc(r.cx, r.cy, radius, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  // Track recent exit fires per ticker — dedup BUS spam (INSIGHT-007)
  const _recentExitAt = new Map();
  function shouldFireExit(ticker) {
    const now = performance.now();
    const last = _recentExitAt.get(ticker) || 0;
    if (now - last < 5000) return false;  // 5s dedup window
    _recentExitAt.set(ticker, now);
    return true;
  }

  function shockFromTicker(ticker, mode, direction) {
    const n = nodeByTicker.get(ticker);
    if (!n) return;
    const p = projectedCache[n.i];
    if (!p) return;
    // Direction-driven color: long=profit-green, short=loss-red, else cluster color
    const d = (direction || n.direction || '').toLowerCase();
    let col;
    if (d === 'short') col = LOSS_COLOR;
    else if (d === 'long') col = PROFIT_COLOR;
    else col = colorFor(n);
    spawnShock(p.sx, p.sy, col, mode || 'expand');
  }

  // === Galaxy sound system (Jin 2026-04-27) ===
  // Web Audio API synthesis — subtle, on-demand, default mute (browser autoplay policy).
  // 외부 sound file 없음, native 합성 (sine/triangle/sawtooth/square + biquad filter + noise buffer).
  let _audioCtx = null;
  let _audioEnabled = false;

  function _ensureAudio() {
    if (!_audioCtx) {
      try {
        _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (err) { return null; }
    }
    return _audioCtx;
  }

  function _playTone(freq, durMs, options) {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const opts = options || {};
    const osc = ctx2.createOscillator();
    const gain = ctx2.createGain();
    osc.frequency.value = freq;
    osc.type = opts.type || 'sine';
    gain.gain.value = 0;
    osc.connect(gain);
    gain.connect(ctx2.destination);
    const now = ctx2.currentTime;
    const dur = durMs / 1000;
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(opts.vol || 0.05, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);
    if (opts.glide) {
      osc.frequency.linearRampToValueAtTime(opts.glide, now + dur);
    }
    osc.start(now);
    osc.stop(now + dur + 0.05);
  }

  function _playRegimeWhoosh() {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const buf = ctx2.createBuffer(1, Math.floor(ctx2.sampleRate * 0.5), ctx2.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * 0.05;
    const src = ctx2.createBufferSource();
    const filter = ctx2.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 800;
    const gain = ctx2.createGain();
    gain.gain.setValueAtTime(0.06, ctx2.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx2.currentTime + 0.5);
    src.buffer = buf;
    src.connect(filter); filter.connect(gain); gain.connect(ctx2.destination);
    src.start();
  }

  // === Cosmic synth helpers (Jin 2026-04-27 우주 테마 강화) ===
  // Web Audio synth only (외부 file 0). reverb = synthesized impulse response.
  // _playTone / _playRegimeWhoosh 보존 (다른 곳 호출 가능).
  function _createCosmicReverb(ctx2, durSec, decay) {
    const d = (typeof durSec === 'number') ? durSec : 2.0;
    const dec = (typeof decay === 'number') ? decay : 1.5;
    const sampleRate = ctx2.sampleRate;
    const length = Math.max(1, Math.floor(sampleRate * d));
    const buffer = ctx2.createBuffer(2, length, sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const data = buffer.getChannelData(ch);
      for (let i = 0; i < length; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, dec);
      }
    }
    const conv = ctx2.createConvolver();
    conv.buffer = buffer;
    return conv;
  }

  function _playCosmicPad(freqs, durMs, opts) {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const o = opts || {};
    const dur = durMs / 1000;
    const now = ctx2.currentTime;
    const reverb = _createCosmicReverb(ctx2, o.reverbSec || 1.5, 1.5);
    const masterGain = ctx2.createGain();
    const filter = ctx2.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(o.filterStart || 800, now);
    filter.frequency.exponentialRampToValueAtTime(o.filterEnd || 2400, now + Math.max(0.01, dur * 0.5));
    masterGain.gain.setValueAtTime(0, now);
    masterGain.gain.linearRampToValueAtTime(o.vol || 0.04, now + 0.05);
    masterGain.gain.exponentialRampToValueAtTime(0.001, now + dur);
    for (const freq of freqs) {
      const osc = ctx2.createOscillator();
      const subOsc = ctx2.createOscillator(); // 1 octave below for depth
      osc.type = o.type || 'sine';
      subOsc.type = 'sine';
      osc.frequency.value = freq;
      subOsc.frequency.value = freq / 2;
      osc.connect(filter);
      subOsc.connect(filter);
      osc.start(now);
      subOsc.start(now);
      osc.stop(now + dur + 0.1);
      subOsc.stop(now + dur + 0.1);
    }
    filter.connect(reverb);
    reverb.connect(masterGain);
    masterGain.connect(ctx2.destination);
  }

  function _playCosmicShimmer(baseFreq, durMs, opts) {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const o = opts || {};
    const dur = durMs / 1000;
    const now = ctx2.currentTime;
    const reverb = _createCosmicReverb(ctx2, 1.0, 2.0);
    const gain = ctx2.createGain();
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(o.vol || 0.03, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);
    // 3 detuned oscillators for shimmer (5th below, root, octave above)
    [-7, 0, 12].forEach((semitone, i) => {
      const osc = ctx2.createOscillator();
      osc.type = 'triangle';
      osc.frequency.value = baseFreq * Math.pow(2, semitone / 12);
      osc.detune.value = (i - 1) * 5;
      osc.connect(gain);
      osc.start(now);
      osc.stop(now + dur);
    });
    gain.connect(reverb);
    reverb.connect(ctx2.destination);
  }

  function _playCosmicBurst(durMs, opts) {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const o = opts || {};
    const dur = durMs / 1000;
    const now = ctx2.currentTime;
    const reverb = _createCosmicReverb(ctx2, 2.5, 1.8);
    const gain = ctx2.createGain();
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(o.vol || 0.06, now + 0.05);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);
    const baseFreq = o.baseFreq || 110;
    // Chord: root + 5th + octave + 12th + 2 octaves
    [1, 1.5, 2, 3, 4].forEach((mult) => {
      const osc = ctx2.createOscillator();
      osc.type = mult > 2 ? 'sine' : 'triangle';
      osc.frequency.value = baseFreq * mult;
      osc.connect(gain);
      osc.start(now);
      osc.stop(now + dur);
    });
    // Filter sweep (low → high → low)
    const filter = ctx2.createBiquadFilter();
    filter.type = 'bandpass';
    filter.Q.value = 2;
    filter.frequency.setValueAtTime(200, now);
    filter.frequency.exponentialRampToValueAtTime(3000, now + Math.max(0.01, dur * 0.3));
    filter.frequency.exponentialRampToValueAtTime(400, now + dur);
    gain.connect(filter);
    filter.connect(reverb);
    reverb.connect(ctx2.destination);
  }

  function _playCosmicWhoosh() {
    if (!_audioEnabled) return;
    const ctx2 = _ensureAudio();
    if (!ctx2) return;
    const dur = 1.2;
    const now = ctx2.currentTime;
    // Atmospheric noise burst with bandpass sweep
    const buf = ctx2.createBuffer(2, Math.floor(ctx2.sampleRate * dur), ctx2.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const data = buf.getChannelData(ch);
      for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * 0.5;
    }
    const src = ctx2.createBufferSource();
    const filter = ctx2.createBiquadFilter();
    filter.type = 'bandpass';
    filter.Q.value = 5;
    filter.frequency.setValueAtTime(200, now);
    filter.frequency.exponentialRampToValueAtTime(4000, now + dur * 0.5);
    filter.frequency.exponentialRampToValueAtTime(300, now + dur);
    const gain = ctx2.createGain();
    gain.gain.setValueAtTime(0.05, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + dur);
    src.buffer = buf;
    src.connect(filter); filter.connect(gain); gain.connect(ctx2.destination);
    src.start();
    // Drone overlay (low pad)
    _playCosmicPad([110, 165], 1000, {
      vol: 0.03, type: 'sine', filterStart: 400, filterEnd: 1200
    });
  }

  function playSoundForEvent(eventType, ev) {
    if (!_audioEnabled) return;
    try {
      switch (eventType) {
        case 'signal_pass':
          // Cosmic chime — single bell with shimmer + reverb
          _playCosmicShimmer(440, 200, { vol: 0.025 });
          break;
        case 'signal_reject':
          // Low muted thud (lowpass-darkened sub-pad)
          _playCosmicPad([110], 100, {
            vol: 0.015, type: 'sine', filterStart: 300, filterEnd: 200
          });
          break;
        case 'entry':
          // Ascending cosmic chord — C E G + delayed shimmer overlay
          _playCosmicPad([261.63, 329.63, 392.00], 400, {
            vol: 0.04, type: 'triangle',
            filterStart: 600, filterEnd: 2200, reverbSec: 1.5
          });
          setTimeout(() => _playCosmicShimmer(880, 300, { vol: 0.02 }), 100);
          break;
        case 'exit':
        case 'exit_trigger': {
          const pnl = parseFloat((ev && ev.pnl_usd) || 0) || 0;
          const isProfit = pnl >= 0;
          if (isProfit) {
            // Profit: ascending bell chord A C# E + shimmer
            _playCosmicPad([440, 554.37, 659.25], 350, {
              vol: 0.045, type: 'triangle',
              filterStart: 800, filterEnd: 2800
            });
            setTimeout(() => _playCosmicShimmer(1320, 250, { vol: 0.025 }), 80);
          } else {
            // Loss: descending minor pad
            _playCosmicPad([220, 174.61, 130.81], 400, {
              vol: 0.035, type: 'triangle',
              filterStart: 600, filterEnd: 200
            });
          }
          const pp = Math.abs(parseFloat((ev && ev.pnl_pct) || 0) || 0);
          if (pp > 1.0) {
            // Supernova burst — big cosmic event (5-osc chord + filter sweep + 2.5s reverb)
            setTimeout(() => _playCosmicBurst(800, {
              vol: 0.08, baseFreq: isProfit ? 220 : 110
            }), 150);
          }
          break;
        }
        case 'regime_change':
        case 'regime_flip':
          // Atmospheric whoosh — bandpass noise sweep + drone overlay
          _playCosmicWhoosh();
          break;
        case 'harness_alert':
          // Tense triton chord (warning, sawtooth)
          _playCosmicPad([440, 622.25], 200, {
            vol: 0.05, type: 'sawtooth',
            filterStart: 800, filterEnd: 1600, reverbSec: 0.8
          });
          break;
        case 'ai_critical':
          // High alert — bright minor chord (square + bright filter)
          _playCosmicPad([659.25, 783.99, 880], 250, {
            vol: 0.06, type: 'square',
            filterStart: 1200, filterEnd: 3000, reverbSec: 1.0
          });
          break;
      }
    } catch (err) { /* swallow audio errors silently — never break SSE */ }
  }

  // User toggle (button click in index.html)
  window.PolarisCloud = window.PolarisCloud || {};
  window.PolarisCloud.toggleSound = (on) => {
    _audioEnabled = (on !== undefined) ? !!on : !_audioEnabled;
    if (_audioEnabled) _ensureAudio();
    return _audioEnabled;
  };
  window.PolarisCloud.isSoundOn = () => _audioEnabled;

  // Jin 2026-04-30 PLAYBACK QUEUE — visualizer = 보고싶은 거, 실시간 X
  //   Bot 이벤트는 큐에 push (drop 0).
  //   화면은 일정 페이스로 재생 — 동영상 buffering 처럼 smooth.
  //   Burst 시 큐가 누적 → 자연스럽게 lag 으로 표현 (지나간 거 다 보임).
  const _eventQueue = [];
  const _MAX_QUEUE_SIZE = 5000;          // drop 0 (5000 = 100초 burst 도 안전)
  const _EVENTS_PER_FRAME = 5;           // 매 100ms 최대 5 이벤트 = 50/s drain
  const _LAG_ALERT_THRESHOLD = 200;      // 200+ 큐 = "lag" 표시
  const _VISUAL_EVENT_TYPES = new Set([
    'entry', 'exit', 'exit_trigger', 'harness_alert',
    'broker_liveness', 'ws_reconnect', 'ai_critical',
    'signal_pass', 'signal_reject',
    'size_cap',
  ]);  // heartbeat / 기타 silent = queue 안 들어감

  function _enqueueEvent(e) {
    if (!_VISUAL_EVENT_TYPES.has(e.type)) return;
    if (_eventQueue.length >= _MAX_QUEUE_SIZE) _eventQueue.shift();
    _eventQueue.push(e);
  }

  // Adaptive drain — backlog 작으면 정상 페이스, 크면 catch-up 가속
  function _processEventBudget() {
    const depth = _eventQueue.length;
    let budget = _EVENTS_PER_FRAME;
    if (depth > 500) budget = 15;        // 5+초 backlog → 150/s catch-up
    else if (depth > 100) budget = 10;   // 1+초 backlog → 100/s
    let n = 0;
    while (n < budget && _eventQueue.length > 0) {
      const e = _eventQueue.shift();
      try { _renderEvent(e); } catch (err) { console.error(err); }
      n++;
    }
    // Optional: HUD lag indicator
    if (depth > _LAG_ALERT_THRESHOLD) {
      const el = document.getElementById('queue-lag');
      if (el) el.textContent = `~${Math.round(depth/50)}s lag (q=${depth})`;
    } else {
      const el = document.getElementById('queue-lag');
      if (el && el.textContent) el.textContent = '';
    }
  }

  function _renderEvent(e) {
    if (e.type === 'entry') {
            // 거래 activity: chain link 단위 outside-in cascade (NOT 전체 cluster)
            flashFire(e.ticker, 3000);
            const tradeId = e.trade_id || `synth_${e.ticker}_${e.ts || Date.now()}`;
            addLiveTradeChain(tradeId, e.ticker, e.strategy_id, e.regime);
            shockFromTicker(e.ticker, 'expand', e.direction);
            // Single linear: chain spark cascade (해당 ticker 만)
            setTimeout(() => chainSparkCascade(e.ticker, false, PROFIT_COLOR), 50);
            setTicker(`TRADE  router.${(e.exchange || 'okx').slice(0,3)}  OPEN ${e.ticker} ${(e.direction || '').toUpperCase()}`);
            // Jin v24: entry 후 즉시 graph reload — 새 POS 노드 visible 해야 사용자
            // "새 포지션 열리거나 나가거나 갱신 안되잖아" mandate. 1.5s 지연 후
            // (server cache 5s TTL 안 fresh, snapshot.py가 trades open status 반영).
            debouncedLoadGraph(5000);  // Jin 2026-04-30 랙 fix: 2s→5s
          } else if (e.type === 'exit' || e.type === 'exit_trigger') {
            // 거래 activity: chain reverse cascade + outbound arc (POS → ACTION 호)
            if (!shouldFireExit(e.ticker)) return;
            const pnl = parseFloat(e.pnl_usd) || 0;
            const isProfit = pnl >= 0;
            spawnExitBurst(e.ticker, e.exit_type || e.reason, e.direction);
            // Single linear: reverse chain cascade
            chainSparkCascade(e.ticker, true, isProfit ? PROFIT_COLOR : LOSS_COLOR);
            // Outbound arc: POS → ACTION 호 (cinematic outward flow)
            setTimeout(() => spawnOutboundArc(e.ticker, 'action'), 120);
            // Supernova for ALL close events (Jin "닫히는 포지션 폭파")
            // magnitude scales with |pnl_pct|: 0.3% = 0.3 / 1% = 0.7 / 3%+ = 2.5 cap
            const pp = Math.abs(parseFloat(e.pnl_pct) || 0);
            const mag = Math.min(2.5, 0.3 + pp * 0.7);
            spawnSupernova(e.ticker, mag,
              isProfit ? PROFIT_COLOR : LOSS_COLOR);
            removeLiveTradeChainByTicker(e.ticker);
            // Jin v24: exit 후 즉시 reload — POS 노드 사라짐 visible.
            debouncedLoadGraph(5000);  // Jin 2026-04-30 랙 fix: 2s→5s
            setTicker(`TRADE  CLOSE ${e.ticker} ${e.exit_type || e.reason || ''} ${pnl.toFixed(2)}`);
          } else if (e.type === 'signal_pass') {
            // Jin 2026-04-30 playback queue: per-ticker 500ms 디바운스
            // (queue 가 burst 보호하므로 debounce 완화 — 같은 ticker 0.5s 내 spam 만 차단)
            const _now = Date.now();
            if (!window._lastSigEffectTs) window._lastSigEffectTs = {};
            const _last = window._lastSigEffectTs[e.ticker] || 0;
            if (_now - _last < 500) {
              setTicker(`SIGNAL PASS ${e.ticker} ${(e.direction || '').toUpperCase()} score=${(e.score||0).toFixed(1)}`);
              return;
            }
            window._lastSigEffectTs[e.ticker] = _now;
            // Jin v18 lifecycle cascade: MKT 글로잉 → WATCH 디밍 → (entry 시 POS).
            const score = Math.abs(e.score || 0);
            const norm = Math.min(1, score / 12);
            const sigColor = (e.direction === 'short') ? LOSS_COLOR : PROFIT_COLOR;
            smallRippleByClusterTicker('mkt', e.ticker, sigColor, 0.45 + norm * 0.3);
            if (norm > 0.5) spawnProviderToTickerBeam(e.ticker, sigColor);
            setTimeout(() => {
              smallRippleByClusterTicker('watch', e.ticker, sigColor, 0.35 + norm * 0.25);
              metricRippleByLabel('watch', e.ticker);
            }, 200);
            setTimeout(() => chainSparkCascade(e.ticker, false, sigColor), 350);
            bumpTickerIntensity(e.ticker, norm * 0.4);
            setTicker(`SIGNAL PASS ${e.ticker} ${(e.direction || '').toUpperCase()} score=${(e.score||0).toFixed(1)}`);
          } else if (e.type === 'signal_reject') {
            // Jin 2026-04-30 playback queue: per-ticker 800ms (REJECT 가 PASS 보다 빈번)
            const _now = Date.now();
            if (!window._lastRejEffectTs) window._lastRejEffectTs = {};
            const _last = window._lastRejEffectTs[e.ticker] || 0;
            if (_now - _last < 800) {
              setTicker(`SIGNAL REJECT ${e.ticker} ${(e.direction || '').toUpperCase()} score=${(e.score||0).toFixed(1)}`);
              return;
            }
            window._lastRejEffectTs[e.ticker] = _now;
            const score = Math.abs(e.score || 0);
            const norm = Math.min(1, score / 12);
            bumpTickerIntensity(e.ticker, norm * 0.25);
            metricRippleByLabel('watch', e.ticker);
            smallRippleByClusterTicker('mkt', e.ticker, LOSS_COLOR, norm * 0.20);
            if (norm > 0.5) spawnProviderToTickerBeam(e.ticker, LOSS_COLOR);
            setTicker(`SIGNAL REJECT ${e.ticker} ${(e.direction || '').toUpperCase()} score=${(e.score||0).toFixed(1)}`);
          } else if (e.type === 'size_cap') {
            // 메트릭 activity: EXIT 안 size_cap 노드 ripple + ACTION sat beam (cluster pulse 제거)
            bumpTickerIntensity(e.ticker, 0.4);
            metricRippleByLabel('exit', 'size_cap');
            spawnSatelliteSignal('action', e.ticker);
            setTicker(`WIRE   T13.size_cap  ${e.ticker}`);
          } else if (e.type === 'ai_critical') {
            // 메트릭 + 거래 activity: BRAIN HIGH 단일 노드 ripple + 해당 ticker chain beam
            // (NO cluster-wide pulse — single AI 결정만)
            const aiNodeIdx = (() => {
              for (let i = 0; i < nodes.length; i++) {
                if (nodes[i].cluster === 'brain' && nodes[i].ai_tier === 'high') return i;
              }
              return -1;
            })();
            if (aiNodeIdx >= 0) metricRipple(aiNodeIdx);
            spawnSatelliteSignal('brain', e.ticker, 'high');
            bumpTickerIntensity(e.ticker, 0.6);
            setTicker(`AI CTRL  CRITICAL  ${e.ticker} ${e.mode || ''}`);
          } else if (e.type === 'ai_decision') {
            // 메트릭 activity: AI stage 별 BRAIN 안 단일 노드 ripple
            spawnAiRipple(e.stage);
            const stageMap = { 'fast': 'ai_advisor', 'deep': 'ai_controller', 'mod': 'ai_modulator' };
            const target = stageMap[e.stage] || 'ai_controller';
            metricRippleByLabel('orbit', target);  // v16: BRAIN → 위성 (cluster 'orbit')
            setTicker(`AI  ${e.stage}  ${e.model || ''}  ${(e.cost || 0).toFixed(4)}$  ${e.latency || 0}ms`);
          } else if (e.type === 'regime_change') {
            // 티어 activity: REG cluster 전체 pulse (system-wide event 만 cluster pulse)
            window.PolarisCloud.pulseCluster && window.PolarisCloud.pulseCluster('reg', 1.0);
            setTicker(`REGIME  → ${(e.to || '?').toUpperCase()}`);
          } else if (e.type === 'regime_flip') {
            // Jin 2026-04-27 log-full-mapping: detector update (CryptoDetector / MacroDetector)
            // → REG cluster 일부 (해당 state) ripple + regime_history 위성 spark
            metricRippleByLabel('reg', `regime_${e.state || 'neutral'}`);
            spawnSatelliteSignal('orbit', 'regime_history');
            setTicker(`REGIME  ${e.detector || '?'}  ${(e.state || '?').toUpperCase()}`);
          } else if (e.type === 'cell_learn') {
            // Jin: cell ema update → cell_matrix brain_tool 위성 firing + ticker bump (있으면)
            spawnSatelliteSignal('orbit', e.ticker || 'cell_matrix');
            metricRippleByLabel('orbit', 'cell_matrix');
            if (e.ticker && e.ticker !== 'system') {
              bumpTickerIntensity(e.ticker, 0.15);
            }
            setTicker(`LEARN  cell  ema=${(e.ema_new || 0).toFixed(4)}`);
          } else if (e.type === 'evolver') {
            // Jin 2026-04-28 v2: 이전 코드는 STRAT cluster 전체 (60 nodes) pulse
            // → "함수 신호 → 전체 확확" 의 큰 원인. 3-tier 원칙 위반 (cluster
            // pulse = system-wide event 한정, evolver 는 단일 strategy mutation).
            // 새 동작: evolver 위성 spark + 영향 받은 strategy_id 단일 ripple.
            spawnSatelliteSignal('orbit', 'evolver');
            metricRippleByLabel('orbit', 'evolver');
            // Specific strategy_id 가 event 에 있으면 해당 STRAT 노드만 ripple,
            // 없으면 evolver 위성만 표시 (cluster-wide bump 제거).
            if (e.strategy_id) {
              for (let i = 0; i < nodes.length; i++) {
                const _n = nodes[i];
                if (_n.cluster === 'strat' && _n.label === e.strategy_id) {
                  metricRipple(i);
                  break;
                }
              }
            }
            setTicker(`EVOLVE ${(e.trigger || '').slice(0, 40)}`);
          } else if (e.type === 'gate_reject') {
            // Jin: GATE REJECT → EXEC gate 노드 ripple + ticker red bump (no chain — blocked)
            // gate label 추측: reason 기반 (liquidity_depth → gate_h0_universe / no_ws_feed → gate_h0)
            const reasonGate = (e.reason || '').toLowerCase();
            let gateLabel = 'gate_h1_signal';
            if (reasonGate.includes('liquidity')) gateLabel = 'gate_h0_universe';
            else if (reasonGate.includes('regime')) gateLabel = 'gate_h2_regime';
            else if (reasonGate.includes('correlat')) gateLabel = 'gate_h3_correlation';
            else if (reasonGate.includes('concentr')) gateLabel = 'gate_h4_concentration';
            else if (reasonGate.includes('throttle') || reasonGate.includes('repeat')) gateLabel = 'gate_h5_throttle';
            else if (reasonGate.includes('kill')) gateLabel = 'gate_h6_kill_switch';
            metricRippleByLabel('orbit', gateLabel);  // v16: EXEC → 위성 (cluster 'orbit')
            if (e.ticker) bumpTickerIntensity(e.ticker, 0.15);
            setTicker(`GATE   REJECT ${e.ticker} ${e.reason || ''}`);
          } else if (e.type === 'gate_clamp') {
            // Jin: LIQUIDITY_CLAMP → exec gate ripple + ticker bump (sized down)
            metricRippleByLabel('orbit', 'gate_h0_universe');  // v16: EXEC → 위성
            if (e.ticker) bumpTickerIntensity(e.ticker, 0.20);
            setTicker(`GATE   CLAMP ${e.ticker} $${e.clamped_size || 0}`);
          } else if (e.type === 'broker_tick') {
            // Jin: broker_sync DB_INSERT_ADOPTED → OBS broker_sync health pulse + ticker lit
            metricRippleByLabel('obs', 'BROKER SYNC');
            if (e.ticker) bumpTickerIntensity(e.ticker, 0.10);
            setTicker(`BROKER ${e.exchange || ''} ${e.ticker || ''}`);
          } else if (e.type === 'broker_liveness') {
            // Jin: broker_sync liveness_1h → OBS broker_sync strong glow
            metricRippleByLabel('obs', 'BROKER SYNC');
            metricRippleByLabel('obs', 'CAP HEARTBEAT');
            setTicker(`BROKER liveness_1h`);
          } else if (e.type === 'ws_reconnect') {
            // Jin: WS reconnect → OBS ws_recover pulse
            metricRippleByLabel('obs', 'WS RECOVER');
            setTicker(`WS     reconnect`);
          } else if (e.type === 'harness_alert') {
            // Jin: HARNESS_ALERT emit → ACTION cluster spawn + harness_alerter exit_engine 위성
            const sev = e.severity || 'INFO';
            spawnSatelliteSignal('action', e.category || 'alert');
            spawnSatelliteSignal('orbit', 'harness_alerter');
            metricRippleByLabel('orbit', 'harness_alerter');
            setTicker(`ALERT  ${sev}  ${e.category || ''}`);
          } else if (e.type === 'heartbeat') {
            // Jin 2026-04-30 랙 fix: 전체 노드 loop 제거 (1000+ nodes 매 30s frame
            // stutter 원인). OBS HEART TICK ripple 만 유지.
            metricRippleByLabel('obs', 'HEART TICK');
          } else if (e.type === 'liveness_shadow') {
            // Jin: LIVENESS_SHADOW PASS/FAIL → OBS liveness ripple + ticker bump
            metricRippleByLabel('obs', 'LIVENESS');
            if (e.ticker && e.ticker !== 'system') {
              bumpTickerIntensity(e.ticker, e.status === 'PASS' ? 0.10 : 0.25);
            }
          }
  }
  // Jin 2026-04-30 아키텍처 전환: SSE handler 는 enqueue 만, 실제 처리는 frame budget 내
  try {
    const es = new EventSource('/stream/events');
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        (d.events || []).forEach(e => {
          // Audio cue (저비용): 항상 즉시 재생
          playSoundForEvent(e.type, e);
          // 시각 이벤트는 queue 에 push (frame budget 내 process)
          _enqueueEvent(e);
        });
      } catch (err) { console.error(err); }
    };
    es.onerror = () => {};
  } catch (err) { console.warn('SSE not available', err); }
  // Frame budget processor — 매 100ms (10Hz) 에 _EVENTS_PER_FRAME 처리
  // RAF 와 별개로 setInterval 사용 (RAF 는 hidden 시 멈춤, queue 는 hidden 동안에도 drain)
  setInterval(_processEventBudget, 100);

  // Expire flashes
  setInterval(() => {
    const now = performance.now();
    for (const n of nodes) {
      if (n._flashUntil && now > n._flashUntil) {
        n.state = n._origState || 'dormant';
        n._flashUntil = 0;
        if (n.ticker) firingTickers.delete(n.ticker);
      }
    }
  }, 500);

  loadGraph();
  setInterval(loadGraph, 5000);   // Jin 2026-05-29 실시간화: 노드(포지션/universe) ~5s 갱신 (SSE 펄스는 별도 실시간)

  // Jin v25: debounced loadGraph — entry/exit burst (분당 5+ trade) 시 매번
  // setTimeout 호출 폭주 → frame stutter. 다음 reload 예정 있으면 reset 해서
  // 마지막 한 번만 fire. server cache 5s 라 fresh 보장.
  let _pendingLoad = null;
  function debouncedLoadGraph(delayMs = 2000) {
    if (_pendingLoad) clearTimeout(_pendingLoad);
    _pendingLoad = setTimeout(() => {
      _pendingLoad = null;
      loadGraph();
    }, delayMs);
  }

  // ── Function satellite ambient lightning (Jin "펑션들 알아서 라이트닝") ──────
  // 매 8s 마다 firing 위성 중 random pick → satelliteSignal 발사 (5s → 8s, frame budget)
  // requestIdleCallback 으로 frame loop 와 분리 (block X)
  function _ambientSatelliteLightning() {
    if (!nodes.length) return;
    const firingSats = [];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.state === 'firing' && (n.cluster === 'orbit' || n.cluster === 'obs'
          || n.cluster === 'action' || n.cluster === 'exit_tally'
          || n.cluster === 'watch' || n.cluster === 'brain')) {
        firingSats.push(i);
      }
    }
    if (!firingSats.length) return;
    // Pick 1 random firing satellite (was 2 — frame budget 줄임)
    const satIdx = firingSats[(Math.random() * firingSats.length) | 0];
    const sat = nodes[satIdx];
    let targetTicker = sat.ticker;
    if (!targetTicker && firingTickers.size > 0) {
      const arr = Array.from(firingTickers);
      targetTicker = arr[(Math.random() * arr.length) | 0];
    }
    if (targetTicker) spawnSatelliteSignal(sat.cluster, targetTicker);
  }
  setInterval(() => {
    if (document.hidden) return;  // Jin 2026-04-30 랙 fix: hidden 시 skip
    if (typeof requestIdleCallback === 'function') {
      requestIdleCallback(_ambientSatelliteLightning, { timeout: 50 });
    } else {
      _ambientSatelliteLightning();
    }
  }, 12000);  // Jin 2026-04-30 랙 fix: 8s → 12s (ambient 빈도 33% ↓)

  // ── Watchdog: ensure rAF didn't die silently ─────────────────────────────
  // Some browsers freeze rAF after long backgrounding / GPU loss / idle throttle.
  // Every 2s, check if frame ticked recently — if not, force restart.
  setInterval(() => {
    const now = performance.now();
    if (animationStarted && now - lastFrameAt > 2000) {
      console.warn('[watchdog] rAF stalled', now - lastFrameAt, 'ms — restarting');
      last = now;  // avoid huge dt jump
      scheduleFrame();
    }
  }, 2000);

  // Visibility change — re-kick on tab return
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      last = performance.now();
      lastFrameAt = last;
      scheduleFrame();
      // Force a fresh data fetch on return
      loadGraph();
    }
  });

  // SSE reconnect-on-failure (browsers retry by default but sometimes give up)
  // — handled by re-creating EventSource on permanent error.
  // (The original try/catch block above creates es; this is best-effort fallback.)
  window.addEventListener('online', () => {
    last = performance.now();
    scheduleFrame();
    loadGraph();
  });
})();
