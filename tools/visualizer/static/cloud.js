/* Polaris FLOW — "Cloud River" horizontal ticker field (Jin 2026-07-10,
 * feat/cloud-river). Replaces the old 3D globe (canvas#sphere + globe-core/
 * satellites/flows/funnel.js) on /flow — this is a flat, canvas-2D particle
 * field that shares the SAME x-axis as flow.js's river below it (stage
 * columns G1..G8, published as ``window.PolarisWallLayout`` — see flow.js's
 * publishWallLayout) so the two panes read as one continuous pipeline.
 *
 * Coordinate system:
 *   X = pipeline stage (G1 Universe .. G8 Reflector), same pixel as the
 *       river's own gate columns below.
 *   Y = venue band, 3 rows (OKX cyan top / Capital purple mid / Alpaca amber
 *       bottom — same colors as the river's lanes).
 *
 * Data (all EXISTING endpoints — no server change):
 *   /static/graph.json  (~1s warm cache) → the dynamic universe roster.
 *     cluster:"mkt"  = the full tradable universe (dormant background dots).
 *     cluster:"pos"  = open positions (G6 MONITOR hover dots).
 *   /stream/events (shared bus, events_bus.js) → gate_events (stage warp) +
 *     fills entry/exit (hit flash / exit fx / kill feed).
 *   /api/flow_stats (30s TTL, matches flow.js's poll) → survivor_admissions_
 *     recent (loot-beam "new candidate" pop).
 *
 * Effects (radar ping / loot beam / hit flash / sparkle/shatter burst / kill
 * feed / floating damage numbers) live in cloud_fx.js — all dot-anchored,
 * this file owns the dot simulation + the ONE rAF loop that also drives fx.
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const canvas = document.getElementById('cloud-canvas');
  const fx = window.PolarisCloudFx;
  if (!canvas || !fx) return;
  const ctx = canvas.getContext('2d');

  const VENUES = ['okx', 'capital', 'alpaca'];
  const VCOLOR_RGB = { okx: [0x5f, 0xdf, 0xff], capital: [0xa8, 0x7c, 0xff], alpaca: [0xff, 0xc8, 0x4f] };
  const VLABEL = { okx: 'OKX', capital: 'CAP', alpaca: 'ALP' };

  const CLOUD_DOT_CAP = 600;           // render cap (spec) — dormant field is sampled above this
  const DECAY_MS = 180000;             // 3min idle -> ease back to the G1 reservoir
  const G7_HOLD_MS = 900, G8_HOLD_MS = 700; // exit sequence hold at each stage before advancing

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function venueOf(ex) {
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return 'okx';
  }
  // Same ticker-cleaning convention as fills/mkt-nodes across this codebase
  // ("venue:SYM-QUOTE" -> "SYM") so gate-event symbols join the roster map.
  function cleanSymbol(sym) { return String(sym || '').split(':').pop().split('-')[0]; }

  // ── Canvas fit (devicePixelRatio-aware, mirrors flow.js/globe-core) ──────
  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  let laneY = {};
  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const top = 46, bottom = Math.max(top + 40, H - 20);
    VENUES.forEach((v, i) => { laneY[v] = top + (bottom - top) * (i / (VENUES.length - 1)); });
  }
  window.addEventListener('resize', fit);
  fit();

  // stageX comes from flow.js's shared layout contract (published before
  // this script runs, and republished on every resize via 'wall-layout').
  function stageXAt(idx) {
    const layout = window.PolarisWallLayout;
    if (layout && layout.stageX && layout.stageX[idx] != null) return layout.stageX[idx];
    const margin = 50, usable = Math.max(100, W - 2 * margin); // pre-layout fallback
    return margin + (idx * usable) / 7;
  }

  // ── Roster state ──────────────────────────────────────────────────────
  const dormant = new Map();      // key -> dot   (cluster:"mkt", the universe reservoir)
  const positions = new Map();    // key -> dot   (cluster:"pos", open positions)
  const tickerToKey = new Map();  // bare ticker -> dormant key (G1..G5 gate-event lookup)
  let dormantRenderList = [];     // stride-sampled subset actually drawn (dot cap)
  const exiting = [];             // transient dots mid G7->G8->respawn sequence

  function strideSample(arr, cap) {
    if (arr.length <= cap || cap <= 0) return cap <= 0 ? [] : arr;
    const stride = arr.length / cap;
    const out = [];
    for (let i = 0; i < cap; i++) out.push(arr[Math.floor(i * stride)]);
    return out;
  }
  function rebuildDormantRenderList() {
    const all = Array.from(dormant.values());
    const activeSet = all.filter((d) => d.stage !== 0 || d.warpT < 1);
    const idle = all.filter((d) => d.stage === 0 && d.warpT >= 1);
    const budget = Math.max(0, CLOUD_DOT_CAP - positions.size - activeSet.length);
    dormantRenderList = strideSample(idle, budget).concat(activeSet);
  }

  function ingestRoster(nodes) {
    const seenD = new Set(), seenP = new Set();
    for (const n of nodes || []) {
      if (!n || !n.ticker || !n.exchange) continue;
      const venue = venueOf(n.exchange);
      if (n.cluster === 'mkt') {
        const key = 'd:' + n.exchange + ':' + n.ticker;
        seenD.add(key);
        tickerToKey.set(n.ticker, key);
        if (!dormant.has(key)) {
          dormant.set(key, {
            key, ticker: n.ticker, venue, stage: 0, x: null, y: null,
            driftSeed: Math.random() * 1000, twinkleSeed: Math.random() * 1000,
            jx: Math.random() - 0.5, jy: Math.random() - 0.5,
            lastActivityTs: 0, warpFromX: 0, warpToX: 0, warpT: 1, warpDur: 0.3,
          });
        }
      } else if (n.cluster === 'pos') {
        const key = 'p:' + n.exchange + ':' + n.ticker;
        seenP.add(key);
        const prev = positions.get(key);
        positions.set(key, {
          key, ticker: n.ticker, venue,
          pnlUsd: n.pnl_usd || 0, sizeUsd: n.size_usd || 0,
          bobSeed: prev ? prev.bobSeed : Math.random() * 1000,
        });
      }
    }
    for (const key of Array.from(dormant.keys())) if (!seenD.has(key)) dormant.delete(key);
    for (const key of Array.from(positions.keys())) if (!seenP.has(key)) positions.delete(key);
    // days-long wall use: universe rotation would otherwise slow-grow this map
    for (const [t, key] of Array.from(tickerToKey.entries())) if (!seenD.has(key)) tickerToKey.delete(t);
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
    let any = false;
    for (const dot of dormant.values()) {
      if (dot.stage !== 0 && dot.warpT >= 1 && now - dot.lastActivityTs > DECAY_MS) {
        triggerWarp(dot, 0);
        any = true;
      }
    }
    if (any) rebuildDormantRenderList();
  }

  // ── Warp: 400-700ms ease forward-transition to a new stage column ───────
  function triggerWarp(dot, targetStageIdx) {
    dot.warpFromX = dot.x != null ? dot.x : stageXAt(dot.stage);
    dot.warpToX = stageXAt(targetStageIdx);
    dot.stage = targetStageIdx;
    dot.warpT = 0;
    dot.warpDur = 0.4 + Math.random() * 0.3;
    dot.lastActivityTs = performance.now();
  }

  // ── survivor_admissions_recent (own poll, matches flow.js's 30s TTL) ─────
  let lastAdmissionTs = 0, admissionsSeeded = false;
  async function pollAdmissions() {
    try {
      const r = await fetch('/api/flow_stats?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const list = d.survivor_admissions_recent || [];
      if (admissionsSeeded) {
        for (const a of list) {
          if (a.ts > lastAdmissionTs) {
            const venue = venueOf(a.venue);
            fx.spawnLootBeam('adm:' + a.symbol, stageXAt(0), laneY[venue]);
          }
        }
      }
      admissionsSeeded = true;
      if (list.length) lastAdmissionTs = Math.max(lastAdmissionTs, ...list.map((a) => a.ts));
    } catch (e) { /* display-only */ }
  }

  // ── SSE: gate_events (stage warp) + fills entry/exit (fx sequences) ─────
  function onGateEvent(g) {
    const idx = (g.gate_id || 0) - 1;
    if (idx < 0 || idx > 4) return; // only the pre-fill pipeline (G1..G5) lives in the dormant pool
    const key = tickerToKey.get(cleanSymbol(g.symbol));
    const dot = key && dormant.get(key);
    if (!dot) return;
    dot.lastActivityTs = performance.now();
    if (idx > dot.stage) {
      triggerWarp(dot, idx);
      rebuildDormantRenderList();
      if (g.gate_id === 2) fx.spawnRadarPing(dot.warpToX, laneY[dot.venue], VCOLOR_RGB[dot.venue]);
    }
  }
  function onEntry(e) {
    const venue = venueOf(e.exchange);
    fx.spawnHitFlash(stageXAt(4), laneY[venue]); // G5 SIZE -> FILLS
  }
  function findPositionKeyByTicker(ticker) {
    for (const [k, v] of positions) if (v.ticker === ticker) return k;
    return null;
  }
  function onExit(e) {
    const pnl = parseFloat(e.pnl_usd) || 0;
    const venue = venueOf(e.exchange);
    const posKey = findPositionKeyByTicker(e.ticker);
    const prev = posKey ? positions.get(posKey) : null;
    if (posKey) positions.delete(posKey); // graduated out of the G6 monitor lane
    const dot = { key: 'exit:' + e.ticker + ':' + performance.now(), ticker: e.ticker, venue };
    dot.x = stageXAt(6); dot.y = laneY[venue]; // G7 EXIT
    exiting.push(dot);
    fx.spawnHitFlash(dot.x, dot.y);
    fx.spawnDamageNumber(dot.x, dot.y, pnl);
    fx.pushKillFeed(e);
    // Loss = local red shatter (dot-anchored) + kill feed line — this IS the
    // killcam replacement per spec, not a full-screen vignette (abolished).
    if (pnl >= 0) fx.spawnSparkleBurst(dot.key, dot.x, dot.y);
    else fx.spawnShatter(dot.key, dot.x, dot.y);
    setTimeout(() => {
      dot.x = stageXAt(7); // G8 REFLECT
      setTimeout(() => {
        fx.spawnCometTail(dot.x, dot.y, stageXAt(0), laneY[dot.venue]);
        const idx = exiting.indexOf(dot);
        if (idx >= 0) exiting.splice(idx, 1);
        // re-seed the reservoir dot back at G1 (comet-tail respawn, the
        // "learning loop" — no-op if the ticker fell out of the roster).
        const dkey = tickerToKey.get(e.ticker);
        const ddot = dkey && dormant.get(dkey);
        if (ddot) { ddot.stage = 0; ddot.warpT = 1; ddot.lastActivityTs = 0; }
      }, G8_HOLD_MS);
    }, G7_HOLD_MS);
    void prev; // position's prior pnl/size not needed for the exit fx itself
  }
  function handleStream(payload) {
    for (const e of payload.events || []) {
      if (e.type === 'entry') onEntry(e);
      else if (e.type === 'exit') onExit(e);
    }
    for (const g of payload.gate_events || []) onGateEvent(g);
  }

  // ── Draw: dormant reservoir + open positions + exiting + fx overlay ────
  function drawVenueLabels() {
    ctx.font = '700 8px JetBrains Mono, monospace';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    for (const v of VENUES) {
      ctx.fillStyle = rgba(VCOLOR_RGB[v], 0.55);
      ctx.fillText(VLABEL[v], 6, laneY[v]);
    }
  }
  // Glide afterimage — a few fading trail dots behind the glide's own path
  // (NOT a radial line/speed line; Jin explicitly rejected "이상한 선 발사").
  function drawGlideTrail(x, y, dot) {
    const dir = dot.warpToX >= dot.warpFromX ? -1 : 1;
    const n = 3;
    for (let i = 1; i <= n; i++) {
      const a = (0.5 * (1 - dot.warpT)) * (1 - i / (n + 1));
      ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], a);
      ctx.beginPath(); ctx.arc(x + dir * i * 3, y, 2 - i * 0.4, 0, 6.2832); ctx.fill();
    }
  }
  function drawDormant(now) {
    for (const dot of dormantRenderList) {
      dot.warpT = Math.min(1, dot.warpT + lastDt / dot.warpDur);
      let x, y;
      if (dot.warpT < 1) {
        x = lerp(dot.warpFromX, dot.warpToX, easeOutCubic(dot.warpT));
        y = laneY[dot.venue];
        drawGlideTrail(x, y, dot);
      } else if (dot.stage === 0) {
        const drift = Math.sin(now / 4000 + dot.driftSeed) * 14 + Math.cos(now / 6500 + dot.driftSeed) * 6;
        x = stageXAt(0) + dot.jx * 34 + drift;
        y = laneY[dot.venue] + dot.jy * 22 + Math.sin(now / 3000 + dot.driftSeed) * 4;
      } else {
        x = stageXAt(dot.stage); y = laneY[dot.venue];
      }
      dot.x = x; dot.y = y;
      const twinkle = 0.35 + 0.35 * Math.sin(now / 1400 + dot.twinkleSeed);
      const idle = dot.stage === 0 && dot.warpT >= 1;
      const alpha = idle ? 0.12 + twinkle * 0.18 : 0.85;
      const r = idle ? 1.4 : 3;
      ctx.fillStyle = rgba(VCOLOR_RGB[dot.venue], alpha);
      ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
    }
  }
  function sizeBucket(usd) {
    const a = Math.abs(usd || 0);
    if (a >= 5000) return 7;
    if (a >= 1500) return 5.5;
    if (a >= 500) return 4.2;
    return 3;
  }
  function drawPositions(now) {
    const x = stageXAt(5); // G6 MONITOR
    for (const dot of positions.values()) {
      const y = laneY[dot.venue] + Math.sin(now / 1000 + dot.bobSeed) * 5;
      const win = dot.pnlUsd >= 0;
      const color = win ? [0x87, 0xff, 0xaf] : [0xff, 0x87, 0x87];
      const alpha = 0.55 + Math.min(0.4, Math.abs(dot.pnlUsd) / 300);
      dot.x = x; dot.y = y;
      ctx.fillStyle = rgba(color, alpha);
      ctx.beginPath(); ctx.arc(x, y, sizeBucket(dot.sizeUsd), 0, 6.2832); ctx.fill();
    }
  }
  function drawExiting() {
    for (const dot of exiting) {
      ctx.fillStyle = 'rgba(242,246,255,0.9)';
      ctx.beginPath(); ctx.arc(dot.x, dot.y, 4.5, 0, 6.2832); ctx.fill();
    }
  }

  // Camera is fully fixed (Jin 2026-07-10 explicit: no zoom/Ken Burns/pan/
  // shake/director-camera navigation) — no transform is applied here.
  let lastT = performance.now();
  let lastDt = 0;
  function frame(now) {
    lastDt = Math.min(0.1, (now - lastT) / 1000);
    lastT = now;
    ctx.clearRect(0, 0, W, H);
    drawVenueLabels();
    drawDormant(now);
    drawPositions(now);
    drawExiting();
    fx.tick(lastDt);
    fx.draw(ctx, W, H);
    requestAnimationFrame(frame);
  }

  window.addEventListener('wall-layout', () => rebuildDormantRenderList());

  // ── Boot ─────────────────────────────────────────────────────────────
  pollRoster();
  setInterval(pollRoster, 3000);
  setInterval(sweepDecay, 5000);
  pollAdmissions();
  setInterval(pollAdmissions, 30000);
  if (window.PolarisEvents) window.PolarisEvents.on(handleStream);
  requestAnimationFrame((t) => { lastT = t; requestAnimationFrame(frame); });
})();
