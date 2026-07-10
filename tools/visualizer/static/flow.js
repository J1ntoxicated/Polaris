/* Polaris FLOW — "Living Pipeline River" (Jin: gate+AI activity as one
 * left→right hierarchy flow, "글로브처럼 한눈에"). Canvas 2D, same render stack
 * as the globe (no WebGL, no 3D projection — a flat left→right river).
 *
 * Self-contained: does NOT import globe-core.js / globe-flows.js. Patterns are
 * reused (SSE subscribe shape, TTL-cached stat poll, botlog tail) but this file
 * owns its own canvas + draw loop so /flow never depends on the globe booting.
 *
 * Jin 2026-07-10 (feat/visual-wall-director, then feat/cloud-river, then
 * feat/flat-neural-map): /flow is now the bottom ~45% pane of a combined
 * page — the layout constants below are pane-relative, not full-screen. The
 * top ~55% used to be a 3D globe; it's now a flat single-field system
 * blueprint (cloud.js/cloud_nodes.js/cloud_fx.js) with its OWN independent
 * zone geometry — the two panes no longer share an x-axis contract (that
 * made sense for the old 3-band Cloud River, not the 5-zone blueprint). This
 * file also renders the pts-classes river annotations (PROVE-class dotted
 * entry particles, EARN/BENCH transition badges, per-strategy score_F window
 * gauges, "NEW CANDIDATE" universe-admission flash) sourced from the same
 * /api/flow_stats payload's `classes` / `survivor_admissions_recent` fields.
 *
 * Data:
 *   /api/flow_stats  (5s TTL, matches the server cache — Jin 2026-07-10
 *                     feat/flat-neural-map realtime-sync) → per-gate 1h volume +
 *                     venue breakdown, fills volume, drop-lane reasons, AI-judge
 *                     shadow mismatches + recent verdicts, conversion summary,
 *                     pts-classes routing state, recent universe admissions.
 *   /stream/events    (SSE, live) → fills (entry/exit, carries real `exchange`)
 *                     drive the honest per-venue-lane particles; the gate-decision
 *                     channel (no reliable per-venue field on that lightweight
 *                     event) drives a neutral "spine" activity particle instead
 *                     of guessing a venue lane.
 *   /api/botlog       (poll ~3s) → condensed mini ticker (index.html pattern).
 *   /api/snapshot     (poll ~5s) → equity/today-net for the bottom ticker.
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade — the
 * drop lane is measurement honesty (same precedent as the globe's kill-sediment
 * motes), not a new block/filter architecture.
 */
(function () {
  const canvas = document.getElementById('flow-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // ── Pipeline stages (left→right) — 8 gates + the FILLS hop between G5→G6 ──
  const STAGES = [
    { id: 1, label: 'G1 UNIVERSE', gate: true },
    { id: 2, label: 'G2 SIGNAL', gate: true },
    { id: 3, label: 'G3 VALIDATE', gate: true, ai: true },
    { id: 4, label: 'G4 WATCH', gate: true, ai: true },
    { id: 5, label: 'G5 SIZE', gate: true },
    { id: 'fills', label: 'FILLS', gate: false },
    { id: 6, label: 'G6 MONITOR', gate: true },
    { id: 7, label: 'G7 EXIT', gate: true, ai: true },
    { id: 8, label: 'G8 REFLECT', gate: true },
  ];
  const VENUES = ['okx', 'capital', 'alpaca'];
  const VCOLOR = { okx: '#5fdfff', capital: '#a87cff', alpaca: '#ffc84f' };
  const VCOLOR_RGB = { okx: [0x5f, 0xdf, 0xff], capital: [0xa8, 0x7c, 0xff], alpaca: [0xff, 0xc8, 0x4f] };
  const DROP_RGB = [0x8a, 0x90, 0x9c];
  const VERDICT_RGB = {
    dim: [0x8a, 0x90, 0x9c], green: [0x87, 0xff, 0xaf],
    amber: [0xff, 0xd0, 0x70], magenta: [0xff, 0x7a, 0xea],
  };

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  // volume → line/node weight (sqrt so a busy gate doesn't blow out the canvas).
  function weight(n) { return clamp(1 + Math.sqrt(Math.max(0, n)) * 0.9, 1, 14); }

  // ── Canvas fit (devicePixelRatio-aware, mirrors globe-core.fit) ──────────
  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    layout();
  }
  window.addEventListener('resize', fit);

  // ── Layout: stage x-coords + venue lane y-coords + drop-lane y ───────────
  // Jin 2026-07-10: this canvas now fills a ~45vh pane (not the full
  // viewport, see flow.html's .river-pane) — LANE_TOP/bottomReserve are
  // retuned to that pane's compact header/footer strips, not full-page ones.
  const MARGIN_X = 50;
  const LANE_TOP = 80;      // clears col-headers + the venue-legend row
  const BOTTOM_RESERVE = 92; // drop panel + class-gauge strip + margin
  let colX = [];
  let laneY = {};
  let dropY = 0;
  const headersEl = document.getElementById('col-headers');

  function layout() {
    const n = STAGES.length;
    const usable = Math.max(100, W - 2 * MARGIN_X);
    colX = STAGES.map((_, i) => MARGIN_X + (n > 1 ? (i * usable) / (n - 1) : 0));
    const laneBottom = Math.max(LANE_TOP + 60, H - BOTTOM_RESERVE);
    const laneSpan = laneBottom - LANE_TOP;
    VENUES.forEach((v, i) => { laneY[v] = LANE_TOP + (laneSpan * 0.62) * (i / (VENUES.length - 1 || 1)); });
    dropY = LANE_TOP + laneSpan;
    renderHeaders();
    if (window.PolarisFlowClasses) window.PolarisFlowClasses.setColX(colX);
  }

  // Jin 2026-07-10 (stage-count semantics fix): G1-G5 are per-journey (one
  // gate_events row per signal) so raw `total` reads as a real funnel count,
  // but G6/G7 are per-tick (a position gets re-checked ~every 2min) — raw
  // `total` there overcounts wildly (e.g. 9-16 open positions x ~430
  // checks/h reads as "586"). headlineOf() picks the honest big number per
  // gate (flow_data.py's additive sized_n/kill_n/live_n/checks_n/exits_n/
  // holds_n fields, `total` elsewhere); subOf() is the small secondary line.
  function headlineOf(s) {
    const g = s.gate ? statsByGate[s.id] : null;
    if (g && s.id === 5 && typeof g.sized_n === 'number') return g.sized_n;
    if (g && s.id === 6 && typeof g.live_n === 'number') return g.live_n;
    if (g && s.id === 7 && typeof g.exits_n === 'number') return g.exits_n;
    return stageTotal(s);
  }
  // window label derives from the payload (10m rolling per Jin 2026-07-10
  // "실시간 숫자" — never a hardcoded "/h").
  function windowLabel() {
    const sec = (stats && stats.window_sec) || 600;
    return sec % 3600 === 0 ? (sec / 3600) + 'h' : Math.round(sec / 60) + 'm';
  }
  function subOf(s) {
    const g = s.gate ? statsByGate[s.id] : null;
    if (!g) return '';
    if (s.id === 5 && typeof g.kill_n === 'number') return 'KILL ' + g.kill_n;
    if (s.id === 6 && typeof g.checks_n === 'number') return g.checks_n + ' checks/' + windowLabel();
    if (s.id === 7 && typeof g.holds_n === 'number') return 'HOLD ' + g.holds_n;
    return '';
  }
  function renderHeaders() {
    if (!headersEl) return;
    headersEl.innerHTML = STAGES.map((s, i) => {
      const cls = s.gate ? 'col-h gate' : 'col-h fills';
      const n = headlineOf(s);
      const sub = subOf(s);
      return `<div class="${cls}" style="left:${colX[i]}px"><span class="n">${n}</span>${sub ? `<span class="sub">${sub}</span>` : ''}${s.label}${s.ai ? ' · AI' : ''}</div>`;
    }).join('');
  }

  // ── Live stat state (from /api/flow_stats, 5s TTL — matches server cache) ──
  let stats = null;
  let statsByGate = {};
  let classByKey = {};   // "venue|strategy_id" -> {strategy_class, window_w, filled}
  const classKey = (venue, sid) => venue + '|' + sid;
  function stageTotal(s) {
    if (!stats) return 0;
    if (s.id === 'fills') return (stats.fills && stats.fills.total) || 0;
    const g = statsByGate[s.id];
    return g ? g.total : 0;
  }
  function stageVenue(s, v) {
    if (!stats) return 0;
    if (s.id === 'fills') return (stats.fills && stats.fills.venues && stats.fills.venues[v]) || 0;
    const g = statsByGate[s.id];
    return g ? (g.venues[v] || 0) : 0;
  }

  async function pollStats() {
    try {
      const r = await fetch('/api/flow_stats?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const prevVerdictTs = (stats && stats.verdicts_recent && stats.verdicts_recent[0]) ? stats.verdicts_recent[0].ts : 0;
      stats = d;
      statsByGate = {};
      for (const g of d.stages || []) statsByGate[g.gate_id] = g;
      classByKey = {};
      for (const c of d.classes || []) classByKey[classKey(c.venue, c.strategy_id)] = c;
      renderHeaders();
      renderSummary(d);
      renderDrops(d);
      // pulse-ring any verdict newer than the last poll (5s cadence AI pulse)
      // — the river's own gate-column ring (spawnVerdictPulse) AND, if the
      // cloud pane above is loaded, that same verdict's Z3/Z5 gate-node halo
      // (cloud_nodes.js's onVerdict — replaces the removed #verdict-ticker
      // text panel, Jin 2026-07-10 feat/flat-neural-map).
      for (const v of d.verdicts_recent || []) {
        if (v.ts > prevVerdictTs) {
          spawnVerdictPulse(v);
          if (window.PolarisCloudNodes) window.PolarisCloudNodes.onVerdict(v);
        }
      }
      // pts-classes river annotations (gauge strip / EARN·BENCH badge pop /
      // NEW CANDIDATE flash) — split into flow_classes.js to keep this file
      // under the file-size cap; owns its own classByKey copy for diffing.
      if (window.PolarisFlowClasses) window.PolarisFlowClasses.onStatsPoll(d);
    } catch (e) { /* display-only — keep last frame */ }
  }

  function fmtPct(v) { return (v == null ? '–' : v.toFixed(1) + '%'); }
  function renderSummary(d) {
    const s = d.summary || {};
    const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    set('s-sig2sized', fmtPct(s.signal_to_sized_pct));
    set('s-sized2fill', fmtPct(s.sized_to_fill_pct));
    set('s-ai', String(s.ai_calls_n != null ? s.ai_calls_n : '–'));
    set('s-drops', String(s.drops_total != null ? s.drops_total : '–'));
    set('s-shadow', String(d.shadow_mismatch_n != null ? d.shadow_mismatch_n : '–'));
  }
  function renderDrops(d) {
    const totEl = document.getElementById('drop-total');
    const rowsEl = document.getElementById('drop-rows');
    const winEl = document.getElementById('drop-window');
    if (winEl) winEl.textContent = 'DROP LANE (' + windowLabel() + ')';
    if (!rowsEl) return;
    const drops = (d.drops || []).slice(0, 3);
    if (totEl) totEl.textContent = String((d.summary && d.summary.drops_total) || 0);
    if (!drops.length) { rowsEl.innerHTML = '<div class="empty">no drops in window</div>'; return; }
    rowsEl.innerHTML = drops.map((r) =>
      `<div class="row"><span>${esc(r.label)} · ${esc(r.reason || '—')}</span><span class="n">${r.n}</span></div>`
    ).join('');
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── Transient effects: verdict pulse rings + drop motes + particles ──────
  const pulses = [];      // {gid, t, dur, color}
  const dropMotes = [];   // {gi, t, dur}
  const particles = [];   // {x0,y0,x1,y1,t,dur,color,r}
  let _lastDropSpawn = {};

  function spawnVerdictPulse(v) {
    pulses.push({ gid: v.gate_id, t: 0, dur: 1.3, color: VERDICT_RGB[v.color] || VERDICT_RGB.dim });
  }
  function gateColIndex(gid) { return STAGES.findIndex((s) => s.id === gid); }
  function gateCenterY() {
    const ys = VENUES.map((v) => laneY[v]);
    return (Math.min(...ys) + Math.max(...ys)) / 2;
  }

  function spawnFillParticle(exchange, fromId, toId, color, opts) {
    const gx = exchangeToVenue(exchange);
    const i0 = gateColIndex(fromId), i1 = gateColIndex(toId);
    if (i0 < 0 || i1 < 0) return;
    const y = laneY[gx] != null ? laneY[gx] : gateCenterY();
    const dotted = !!(opts && opts.dotted);
    particles.push({
      x0: colX[i0], y0: y, x1: colX[i1], y1: y, t: 0, dur: 0.9, color,
      r: dotted ? 2.0 : 3.2, dotted,
    });
  }
  // PROVE-class tracks (still capital-limited, unproven) render their entry
  // particle smaller/dotted so the river visually distinguishes them from an
  // EARN-class fill — measurement only, never a block/throttle (flow_not_block).
  function isProveClass(exchange, strategyId) {
    const gx = exchangeToVenue(exchange);
    const c = classByKey[gx + '|' + strategyId];
    return !!(c && c.strategy_class === 'PROVE');
  }
  function spawnSpineParticle(gid) {
    const i1 = gateColIndex(gid);
    if (i1 < 0) return;
    const i0 = Math.max(0, i1 - 1);
    const y = gateCenterY();
    particles.push({ x0: colX[i0], y0: y, x1: colX[i1], y1: y, t: 0, dur: 0.7, color: [0xc4, 0xca, 0xd6], r: 1.8, spine: true });
  }
  function exchangeToVenue(ex) {
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return 'okx';
  }

  // ── Draw loop ──────────────────────────────────────────────────────────
  let lastT = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - lastT) / 1000);
    lastT = now;
    ctx.clearRect(0, 0, W, H);
    drawEdges();
    drawDropLane(dt);
    drawNodes();
    drawPulses(dt);
    drawParticles(dt);
    requestAnimationFrame(frame);
  }

  function drawEdges() {
    for (let i = 0; i < STAGES.length - 1; i++) {
      for (const v of VENUES) {
        const n = stageVenue(STAGES[i], v);
        const n2 = stageVenue(STAGES[i + 1], v);
        const vol = Math.max(n, n2);
        if (vol <= 0) continue;
        ctx.strokeStyle = rgba(VCOLOR_RGB[v], 0.30);
        ctx.lineWidth = weight(vol);
        ctx.beginPath();
        ctx.moveTo(colX[i], laneY[v]);
        ctx.lineTo(colX[i + 1], laneY[v]);
        ctx.stroke();
      }
    }
  }

  function drawNodes() {
    for (let i = 0; i < STAGES.length; i++) {
      const s = STAGES[i];
      for (const v of VENUES) {
        const n = stageVenue(s, v);
        const r = 2.4 + Math.min(6, Math.sqrt(n) * 1.1);
        ctx.fillStyle = rgba(VCOLOR_RGB[v], n > 0 ? 0.85 : 0.18);
        ctx.beginPath(); ctx.arc(colX[i], laneY[v], r, 0, 6.2832); ctx.fill();
        if (n > 0) {
          ctx.fillStyle = rgba(VCOLOR_RGB[v], 0.75);
          ctx.font = '600 8px JetBrains Mono, monospace';
          ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
          ctx.fillText(String(n), colX[i] + r + 3, laneY[v]);
        }
      }
    }
  }

  function drawDropLane(dt) {
    if (!stats) return;
    const drops = stats.drops || [];
    const byGate = {};
    for (const d of drops) byGate[d.gate_id] = (byGate[d.gate_id] || 0) + d.n;
    const gids = Object.keys(byGate).map(Number);
    if (!gids.length) return;
    // faint collector guide across the span of contributing gates.
    const xs = gids.map((g) => colX[gateColIndex(g)]).filter((x) => x != null);
    if (xs.length) {
      ctx.strokeStyle = rgba(DROP_RGB, 0.14);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.min(...xs), dropY); ctx.lineTo(Math.max(...xs), dropY); ctx.stroke();
    }
    for (const g of gids) {
      const i = gateColIndex(g);
      if (i < 0) continue;
      const n = byGate[g];
      ctx.strokeStyle = rgba(DROP_RGB, 0.32);
      ctx.lineWidth = weight(n) * 0.55;
      ctx.beginPath(); ctx.moveTo(colX[i], gateCenterY() + 30); ctx.lineTo(colX[i], dropY); ctx.stroke();
      ctx.fillStyle = rgba(DROP_RGB, 0.9);
      ctx.beginPath(); ctx.arc(colX[i], dropY, 2.6, 0, 6.2832); ctx.fill();
      ctx.font = '600 8.5px JetBrains Mono, monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(String(n), colX[i], dropY + 5);
      // periodic downward mote so the "leak" reads as motion, not a static line.
      const last = _lastDropSpawn[g] || 0;
      const now = performance.now();
      if (now - last > Math.max(600, 3000 / Math.min(6, n))) {
        _lastDropSpawn[g] = now;
        dropMotes.push({ gi: i, t: 0, dur: 0.8 });
      }
    }
    for (let k = dropMotes.length - 1; k >= 0; k--) {
      const m = dropMotes[k];
      m.t += dt / m.dur;
      if (m.t >= 1) { dropMotes.splice(k, 1); continue; }
      const y = lerp(gateCenterY() + 30, dropY, m.t);
      const a = (1 - m.t) * 0.8;
      ctx.fillStyle = rgba(DROP_RGB, a);
      ctx.beginPath(); ctx.arc(colX[m.gi], y, 1.8, 0, 6.2832); ctx.fill();
    }
  }

  function drawPulses(dt) {
    for (let k = pulses.length - 1; k >= 0; k--) {
      const p = pulses[k];
      p.t += dt / p.dur;
      if (p.t >= 1) { pulses.splice(k, 1); continue; }
      const i = gateColIndex(p.gid);
      if (i < 0) continue;
      const cy = gateCenterY();
      const r = 10 + p.t * 26;
      const a = (1 - p.t) * 0.55;
      ctx.strokeStyle = rgba(p.color, a);
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(colX[i], cy, r, 0, 6.2832); ctx.stroke();
    }
  }

  function drawParticles(dt) {
    for (let k = particles.length - 1; k >= 0; k--) {
      const p = particles[k];
      p.t += dt / p.dur;
      if (p.t >= 1) { particles.splice(k, 1); continue; }
      const x = lerp(p.x0, p.x1, p.t), y = lerp(p.y0, p.y1, p.t);
      const a = p.spine ? 0.5 * (1 - p.t * 0.3) : 0.95;
      // PROVE-class entries trail a faint dashed line behind the (smaller)
      // head dot — visually distinct from an EARN-class fill without a new
      // hue (measurement, not a block/throttle).
      if (p.dotted) {
        ctx.save();
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = rgba(p.color, a * 0.5);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(p.x0, p.y0); ctx.lineTo(x, y); ctx.stroke();
        ctx.restore();
      }
      const gr = ctx.createRadialGradient(x, y, 0, x, y, p.r * 2.2);
      gr.addColorStop(0, rgba(p.color, a));
      gr.addColorStop(1, rgba(p.color, 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(x, y, p.r * 2.2, 0, 6.2832); ctx.fill();
      ctx.fillStyle = rgba(p.color, Math.min(1, a + 0.2));
      ctx.beginPath(); ctx.arc(x, y, p.r * 0.6, 0, 6.2832); ctx.fill();
    }
  }

  // ── SSE live stream — fills (real venue) + gate decisions (spine only) ───
  function handleStreamPayload(payload) {
    for (const e of payload.events || []) {
      if (e.type === 'entry') {
        const dotted = isProveClass(e.exchange, e.strategy_id);
        spawnFillParticle(e.exchange, 5, 'fills', [0xf2, 0xf6, 0xff], { dotted });
      } else if (e.type === 'exit') {
        const pnl = parseFloat(e.pnl_usd) || 0;
        const col = pnl >= 0 ? [0x87, 0xff, 0xaf] : [0xff, 0x87, 0x87];
        spawnFillParticle(e.exchange, 7, 8, col);
      }
    }
    for (const g of payload.gate_events || []) {
      spawnSpineParticle(g.gate_id);
    }
  }
  function connectStream() {
    // Shared bus (events_bus.js, loaded before this file — feat/cloud-river
    // Jin 2026-07-10): one /stream/events socket for the whole page instead
    // of a private EventSource per module. Falls back to an own connection
    // if the bus script didn't load, so this file still works standalone.
    if (window.PolarisEvents) { window.PolarisEvents.on(handleStreamPayload); return; }
    let es;
    try { es = new EventSource('/stream/events'); } catch (e) { return; }
    es.onmessage = (ev) => {
      let payload;
      try { payload = JSON.parse(ev.data); } catch (e) { return; }
      handleStreamPayload(payload);
    };
    es.onerror = () => { /* EventSource auto-reconnects */ };
  }

  // ── Bot log mini ticker (condensed index.html/board.js pattern) ─────────
  function classifyLog(line) {
    const l = line.toLowerCase();
    if (/\b(error|critical|fatal)\b/.test(l) || l.includes('exception') || l.includes('traceback')) return 'bl-err';
    if (/\b(warn|warning)\b/.test(l) || l.includes('rejected') || l.includes('retry')) return 'bl-warn';
    if (l.includes('order resp ok=true') || /\bopen(ed)?\b/.test(l) || /\bclos(e|ed)\b/.test(l) || /\bfill(ed)?\b/.test(l)) return 'bl-hi';
    return '';
  }
  function fmtLogLine(line) {
    const m = line.match(/^(\S+T\S+Z?)\s+(.*)$/);
    const cls = classifyLog(line);
    if (m) return `<div class="bl-line ${cls}"><span class="ts">${esc(m[1])}</span> ${esc(m[2])}</div>`;
    return `<div class="bl-line ${cls}">${esc(line)}</div>`;
  }
  // Jin 2026-07-10: the wall's bottom ticker is a slim 3-line strip (the
  // globe pane above it now owns most of the vertical budget) — was 8.
  const LOG_TAIL_N = 3;
  async function pollLog() {
    const body = document.getElementById('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const lines = ((await r.json()).lines || []).slice(-LOG_TAIL_N);
      if (!lines.length) return;
      body.innerHTML = lines.map(fmtLogLine).join('');
      body.scrollTop = body.scrollHeight;
    } catch (e) { /* keep last frame */ }
  }

  // ── Bottom ticker: equity / today-net (VIRTUAL ledger, the dashboard's
  // primary mode per Jin 2026-07-07) — same /api/snapshot fields + aggregate
  // pattern board.js's virtualKpiHtml/streams table already use. ───────────
  function fmtUsd(v) {
    if (v == null || isNaN(v)) return '–';
    const sign = v < 0 ? '-' : '';
    return sign + '$' + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  async function pollEquity() {
    try {
      const r = await fetch('/api/snapshot?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const equity = (d.streams || []).reduce(
        (a, s) => a + (s.virtual_equity_usd != null ? s.virtual_equity_usd : (s.virtual_seed_usd || 0)), 0
      );
      const today = d.virtual_daily_pnl_usd;
      const eqEl = document.getElementById('bl-equity');
      const todayEl = document.getElementById('bl-today');
      if (eqEl) eqEl.textContent = fmtUsd(equity);
      if (todayEl) {
        todayEl.textContent = fmtUsd(today);
        todayEl.classList.toggle('pos', today > 0);
        todayEl.classList.toggle('neg', today < 0);
      }
    } catch (e) { /* keep last frame */ }
  }

  // ── Boot ──────────────────────────────────────────────────────────────
  fit();
  pollStats();
  setInterval(pollStats, 5000);    // matches the server's flow_stats TTL
  pollLog();
  setInterval(pollLog, 3000);
  pollEquity();
  setInterval(pollEquity, 5000);
  connectStream();
  requestAnimationFrame((t) => { lastT = t; requestAnimationFrame(frame); });
})();
