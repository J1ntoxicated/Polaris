/* Polaris FLOW — console v2 readouts layer (Jin 2026-07-11 "제대로 하자",
 * build spec wall_console_blueprint.md §4). The "showcase" panels: corner
 * equity/AI crowns, the BAY arc gauges, the gate-ladder + KELLY CELL LEDGER +
 * BR venue/exit strip along the bottom, the g8 register column, and the
 * regime confidence grid. Every number drawn here traces back to
 * ``console`` (graph.json, set via wall_spine.js's setConsole — a zero-cost
 * field copy off collect_snapshot, see polaris_graph.py's _console_block)
 * or ``verdicts_recent``/``summary`` (flow_stats, setVerdicts/
 * setFlowSummary). Decoration (panel frames/corner brackets/section
 * headers) carries no data shape, so it never reads as a fabricated number.
 *
 * Implementation note (rework r3 fix, review CRITICAL/MED item 2): the build
 * spec calls for an offscreen-canvas bake of the static panel chrome at 1s
 * cadence with per-frame blit + only the small dynamic bits redrawn. The
 * five panels with NO now-dependent animation — KELLY CELL LEDGER, the BAY
 * gauges, STREAMS+SESSION, EXIT FSM, REGIME MATRIX (drawArcGauge takes no
 * `now`; these are pure data grids/arcs) — are now baked to an offscreen
 * canvas by ensureBake() below, re-baked only when wall_spine_hud.js's
 * pollRoster/pollStats hand draw() a fresh console/summary object (an exact
 * "did a poll land" signal — both pollers hand a NEW object every ~1s
 * cycle, never mutate in place) or the wall resizes; draw() then blits that
 * bitmap with one drawImage/frame. The TL/TR crowns and the gate ladder stay
 * on the live per-frame path unchanged: label-breathing alpha, the verdict
 * tape's slide/fade entrance, and the kill-pulse decay all genuinely animate
 * on `now`, so baking those would freeze the motion the design contract
 * calls for; the register column also stays live (positions come from
 * field.js's per-frame screen layout). No panel draw function changed
 * shape to add this — they're called unchanged, just against an offscreen
 * ctx for the five now-baked ones.
 *
 * Loaded after wall_spine_deco.js / wall_console_lanes.js, before
 * wall_spine.js (which calls draw(ctx, now) once per frame — see the
 * frame() seam there) and wall_spine_hud.js (which feeds setConsole/
 * setVerdicts/setFlowSummary off the existing graph.json + flow_stats
 * polls — no new endpoint).
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const field = window.PolarisSpineField;
  const deco = window.PolarisSpineDeco;
  if (!field || !deco) return;
  const Z = field.WALL_ZONES;

  // Same 8-hue cool->warm progression as wall_spine.js's GATE_COLORS (Jin
  // 2026-07-10 "각 게이트 색도 좀 다르게") — duplicated here rather than
  // plumbed cross-module for 8 stable literal hex strings shared visual
  // language, not runtime state.
  const GATE_COLORS = ['#5fa8ff', '#5fdfff', '#6fffc4', '#9dff6f', '#ffe066', '#ffb454', '#ff7a9e', '#c48aff'];
  const STEEL = '#8a94b0';
  const WARM = '#dfe8ff';
  const PNL_POS = '#7dffa8', PNL_NEG = '#ff7d8a';

  function hashStr(s) {
    let h = 2166136261;
    const t = String(s == null ? '' : s);
    for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function fmtUsd(v) {
    if (v == null || isNaN(v)) return '—';
    return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  function fmtPct(v, digits) {
    if (v == null || isNaN(v)) return '—';
    return v.toFixed(digits == null ? 1 : digits) + '%';
  }
  function fmtNum(v, digits) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(digits == null ? 2 : digits);
  }
  // VIRTUAL ACCOUNT mode branch — mirrors wall_spine_hud.js's renderEquity
  // (the bot-log strip ticker) so the console crown/gauges/sparkline never
  // show a different EQUITY number than the strip on the same screen. Falls
  // back to the legacy real-venue pair when virtual mode is off.
  function equityOf(core) {
    if (!core) return { equity: null, today: null };
    const virt = !!core.virtual_account_enabled;
    return {
      equity: virt ? core.virtual_equity_usd : core.equity_now,
      today: virt ? core.virtual_daily_pnl_usd : core.equity_now - core.starting_capital,
    };
  }
  // client-side 1s-poll equity ring (600pt cap) — feeds both the BR SESSION
  // sparkline below and sessionPeakUsd() (rework r3 fix, see there for why).
  const equityRing = [];
  const EQUITY_RING_MAX = 600;
  function pushEquitySample(v) {
    if (v == null || isNaN(v)) return;
    const last = equityRing[equityRing.length - 1];
    if (last === v) return; // dedupe identical consecutive samples (no per-frame push, only per-poll data change)
    equityRing.push(v);
    if (equityRing.length > EQUITY_RING_MAX) equityRing.shift();
  }
  // rework r3 fix (review CRITICAL/MED item 1): PEAK (TL crown) and the
  // EQUITY/PEAK BAY gauge both used to divide by core.peak_equity — a LEGACY
  // real-venue scalar — even when EQUITY itself had branched to
  // core.virtual_equity_usd (equityOf() above). On a real DB this drew
  // virtual EQUITY ($296,411) directly above a smaller legacy PEAK
  // ($230,131): a self-evidently impossible "equity exceeds its own peak"
  // reading, and clamped the BAY ring permanently FULL (ratio > 1 -> min(1,
  // ...)), unable to ever show virtual drawdown. No persisted all-time
  // virtual-peak field exists on the snapshot to branch to instead, so this
  // derives a SESSION peak (since page load) from the same client equity
  // ring the SESSION sparkline already builds, folding the current reading
  // in so the result can never sit below the EQUITY value it's compared
  // against. Labelled "(SESS)" in the crown — a since-page-load high, not an
  // all-time one; still real, non-fabricated data (every point in the ring
  // is a genuine polled equity reading).
  function sessionPeakUsd(eqNow) {
    if (eqNow == null || isNaN(eqNow)) return null;
    return equityRing.length ? Math.max(eqNow, ...equityRing) : eqNow;
  }

  /* ===== panel frame (shared chrome — border + corner brackets + header,
   * source-over only) ===== */
  function panelFrame(ctx, x0, y0, x1, y1, title) {
    ctx.fillStyle = 'rgba(5,7,14,0.82)';
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    ctx.strokeStyle = 'rgba(150,190,225,0.12)'; ctx.lineWidth = 1;
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0 - 1, y1 - y0 - 1);
    const tick = 5;
    [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]].forEach(([cx, cy, sx, sy]) => {
      ctx.beginPath();
      ctx.moveTo(cx, cy + sy * tick); ctx.lineTo(cx, cy); ctx.lineTo(cx + sx * tick, cy);
      ctx.strokeStyle = 'rgba(150,190,225,0.28)'; ctx.lineWidth = 0.8; ctx.stroke();
    });
    if (title) {
      ctx.font = '700 7.5px JetBrains Mono, monospace';
      ctx.fillStyle = 'rgba(160,200,235,0.4)';
      ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
      ctx.fillText(title, x0 + 6, y0 + 11);
    }
  }
  function labelValueRow(ctx, x0, x1, y, lbl, val, color, breathe) {
    ctx.textAlign = 'left'; ctx.font = '600 6.5px JetBrains Mono, monospace';
    ctx.fillStyle = 'rgba(138,148,176,0.6)';
    ctx.fillText(lbl, x0, y);
    ctx.textAlign = 'right'; ctx.font = '700 8.5px ui-monospace, Menlo, monospace';
    const a = breathe == null ? 0.92 : 0.7 + 0.25 * breathe;
    ctx.fillStyle = color ? field.rgba(color, a) : field.rgba(WARM, a);
    ctx.fillText(val, x1, y);
    ctx.beginPath(); ctx.moveTo(x0, y + 3); ctx.lineTo(x1, y + 3);
    ctx.strokeStyle = 'rgba(159,192,255,0.14)'; ctx.lineWidth = 0.6; ctx.stroke();
  }

  /* ===== TL crown — EQUITY/PEAK/FEE-NET/EXPOSURE/ΣuPnL/SESSION ===== */
  function drawPanelTL(ctx, c, now, W, H) {
    const z = Z.crownTL;
    const x0 = W * z.x0, x1 = W * z.x1, y0 = H * z.y0, y1 = H * z.y1;
    panelFrame(ctx, x0, y0, x1, y1, 'EQUITY');
    if (!c || !c.core) return;
    const core = c.core;
    const virtualMode = !!core.virtual_account_enabled;
    const eqNow = equityOf(core).equity;
    const streams = c.streams || [];
    const exposure = streams.reduce((a, s) => a + (s.exposed || 0), 0);
    const upnl = streams.reduce((a, s) => a + (s.upnl || 0), 0);
    const sess = c.sessions || {};
    const sessLbl = ['okx', 'capital', 'alpaca']
      .map((v) => (sess[v] || '-').replace('_', ' ').slice(0, 9)).join(' · ');
    // PEAK/FEE-NET are core.peak_equity/core.real_fee_net — LEGACY real-venue
    // scalars. In VIRTUAL mode EQUITY above has already branched to
    // virtual_equity_usd, so pairing it with a legacy PEAK produced an
    // impossible "equity exceeds its own peak" reading (rework r3 fix — see
    // sessionPeakUsd() by equityOf()). No virtual all-time peak/fee-total
    // exists on the snapshot, so PEAK branches to the session-derived peak
    // and FEE-NET is relabelled to disclose it's still the legacy figure
    // rather than silently mixing accounting bases.
    const peakVal = virtualMode ? sessionPeakUsd(eqNow) : core.peak_equity;
    const peakLbl = virtualMode ? 'PEAK (SESS)' : 'PEAK';
    const feeLbl = virtualMode ? 'FEE-NET (LGCY)' : 'FEE-NET';
    const rows = [
      ['EQUITY', fmtUsd(eqNow), null, 4],
      [peakLbl, fmtUsd(peakVal), null, 1800],
      [feeLbl, fmtUsd(core.real_fee_net), null, 1800],
      ['EXPOSURE', fmtUsd(exposure), null, 1800],
      ['Σ uPnL', fmtUsd(upnl), upnl >= 0 ? PNL_POS : PNL_NEG, 900],
      ['SESSION', sessLbl, null, 3600],
    ];
    const innerY0 = y0 + 16, innerY1 = y1 - 4;
    const rowH = (innerY1 - innerY0) / rows.length;
    rows.forEach(([lbl, val, col, period], i) => {
      const ry = innerY0 + (i + 0.72) * rowH;
      const breathe = field.breath(now, period, hashStr(lbl));
      labelValueRow(ctx, x0 + 6, x1 - 6, ry, lbl, val, col, breathe);
    });
  }

  /* ===== TR crown — 8-gate mini sparkbars + AI CALLS/OK%/ERR + VERDICT TAPE
   * (Jin 2026-07-11: entrance-animates only on a genuinely NEW verdict —
   * "연속 스크롤 금지" — never a continuous marquee). ===== */
  const verdictSeen = new Map(); // ts -> firstSeenPerfNow (entrance anim clock)
  function drawPanelTR(ctx, c, verdicts, now, W, H) {
    const z = Z.crownTR;
    const x0 = W * z.x0, x1 = W * z.x1, y0 = H * z.y0, y1 = H * z.y1;
    panelFrame(ctx, x0, y0, x1, y1, 'AI / GATES');
    const flow = (c && c.gate_flow_1h) || {};
    const vals = GATE_COLORS.map((_, i) => Number(flow[i + 1]) || 0);
    const maxN = Math.max(1, ...vals);
    const barX0 = x0 + 6, barX1 = x1 - 6, barTop = y0 + 14, barBot = y0 + 40;
    const barW = (barX1 - barX0) / 8;
    vals.forEach((n, i) => {
      const bx = barX0 + i * barW;
      ctx.fillStyle = 'rgba(138,148,176,0.14)';
      ctx.fillRect(bx, barTop, barW - 2, barBot - barTop);
      const h = (barBot - barTop) * Math.min(1, n / maxN);
      ctx.fillStyle = field.rgba(GATE_COLORS[i], 0.75);
      ctx.fillRect(bx, barBot - h, barW - 2, h);
    });
    const gpt = (c && c.gpt) || {};
    // ERR is a call-error COUNT, not a P/L figure — green/red is money-only
    // (color contract §1). Amber (matches the verdict tape's own 'amber'
    // convention below) flags it without bleeding into the money palette.
    labelValueRow(ctx, x0 + 6, x1 - 6, barBot + 12,
      'AI CALLS/h · OK% · ERR',
      (gpt.calls_per_h || 0) + ' · ' + fmtPct(gpt.ok_pct) + ' · ' + (gpt.err_n || 0),
      gpt.err_n ? '#ffb454' : null, field.breath(now, 900, 11));
    // VERDICT TAPE — up to 3 real recent AI-judge verdicts (flow_stats caps
    // at 3; never padded with fabricated rows).
    const tapeY = barBot + 24;
    (verdicts || []).slice(0, 3).forEach((v, i) => {
      const key = String(v.ts) + ':' + v.gate_id;
      if (!verdictSeen.has(key)) verdictSeen.set(key, now);
      const age = now - verdictSeen.get(key);
      const k = age >= 300 ? 1 : 1 - Math.pow(1 - age / 300, 3); // easeOut
      const chipY = tapeY + i * 11;
      const startOffset = (1 - k) * 20; // slides in from +20px on first sight, settles at 0
      const text = 'g' + v.gate_id + ' ' + String(v.verdict || '').slice(0, 14).toLowerCase();
      // AI verdict pass/amber/dim is a judge STATE, not P/L — green/red is
      // money-only (color contract §1). Warm-white (the deck's own "active"
      // hue) reads a pass verdict without borrowing the profit-green hex.
      const col = v.color === 'green' ? WARM : (v.color === 'amber' ? '#ffb454' : STEEL);
      ctx.globalAlpha = k;
      ctx.textAlign = 'right'; ctx.font = '600 7px JetBrains Mono, monospace';
      ctx.fillStyle = field.rgba(col, 0.85);
      ctx.fillText(text, x1 - 6 + startOffset, chipY);
      ctx.globalAlpha = 1;
    });
    for (const key of Array.from(verdictSeen.keys())) {
      if (!(verdicts || []).some((v) => String(v.ts) + ':' + v.gate_id === key)) verdictSeen.delete(key);
    }
  }

  /* ===== BAY gauges — EQUITY/PEAK ratio, DAY PnL, FIRING, CONVERSION ===== */
  function drawGauges(ctx, c, summary, now, W, H) {
    const z = Z.bayRect;
    const cy = H * 0.375, r = 26;
    const cxs = [W * 0.05, W * 0.115, W * 0.18, W * 0.245];
    if (!c || !c.core) return;
    const core = c.core;
    const eq = equityOf(core).equity;
    // Same virtual-aware peak basis as the TL crown's PEAK row (rework r3
    // fix) — a legacy core.peak_equity denominator here in VIRTUAL mode
    // clamped this ring permanently FULL (eq/legacy-peak > 1 -> min(1, ...)),
    // never able to show virtual drawdown.
    const peakBasis = core.virtual_account_enabled ? sessionPeakUsd(eq) : core.peak_equity;
    const pk = peakBasis > 0 ? Math.max(0, Math.min(1, eq / peakBasis)) : 0;
    deco.drawArcGauge(ctx, cxs[0], cy, r, pk, WARM, 'EQUITY', fmtUsd(eq));
    const dayPnl = c.day_pnl || 0;
    // Scale: full ring = +-10% of starting capital (a fixed, documented
    // reference — NOT a fabricated data value, the $ figure drawn is real).
    const scale = 0.1 * Math.max(1, core.starting_capital || 0);
    const dpPct = Math.max(0, Math.min(1, 0.5 + dayPnl / (2 * scale)));
    deco.drawArcGauge(ctx, cxs[1], cy, r, dpPct, dayPnl >= 0 ? PNL_POS : PNL_NEG, 'DAY PNL', fmtUsd(dayPnl));
    const firing = Math.max(0, Math.min(1, c.firing_rate || 0));
    deco.drawArcGauge(ctx, cxs[2], cy, r, firing, WARM, 'FIRING', fmtPct(firing * 100));
    const sig2sized = ((summary && summary.signal_to_sized_pct) || 0) / 100;
    const sized2fill = ((summary && summary.sized_to_fill_pct) || 0) / 100;
    deco.drawArcGauge(ctx, cxs[3], cy, r, sig2sized, WARM, 'CONVERSION', null);
    deco.drawArcGauge(ctx, cxs[3], cy, r - 7, sized2fill, WARM, null, fmtPct(sized2fill * 100, 0));
  }

  /* ===== gate ladder — 8-column volume bars + kill ticks + GATE PULSE ===== */
  function drawGateLadder(ctx, c, gatePulseAt, now, W, H) {
    const gz = Z.ladderBand;
    const x0 = W * gz.gateOps.x0, x1 = W * gz.gateOps.x1, y0 = H * gz.y0, y1 = H * gz.y1;
    panelFrame(ctx, x0, y0, x1, y1, 'GATE OPS · 1h');
    const flow = (c && c.gate_flow_1h) || {};
    const vals = GATE_COLORS.map((_, i) => Number(flow[i + 1]) || 0);
    const maxLog = Math.max(1, ...vals.map((n) => Math.log1p(n)));
    const bx0 = x0 + 8, bx1 = x1 - 8, base = y1 - 16, top = y0 + 12;
    const colW = (bx1 - bx0) / 8;
    vals.forEach((n, i) => {
      const cx = bx0 + (i + 0.5) * colW;
      const h = (base - top) * (Math.log1p(n) / maxLog);
      ctx.fillStyle = field.rgba(GATE_COLORS[i], 0.5);
      ctx.fillRect(cx - colW * 0.28, base - h, colW * 0.56, h);
      const pulseAge = now - (gatePulseAt ? gatePulseAt(i + 1) : 0);
      if (pulseAge >= 0 && pulseAge < 400) {
        const a = 1 - pulseAge / 400;
        ctx.fillStyle = field.rgba(WARM, 0.6 * a);
        ctx.fillRect(cx - colW * 0.36, base - h - 3, colW * 0.72, 3);
      }
      ctx.font = '600 6px JetBrains Mono, monospace'; ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(138,148,176,0.55)';
      ctx.fillText('g' + (i + 1), cx, base + 9);
    });
  }

  /* ===== KELLY CELL LEDGER — top/bottom 4 cells by mult (showcase #1) ===== */
  function drawCellLedger(ctx, c, W, H) {
    const gz = Z.ladderBand;
    const x0 = W * gz.cellLedger.x0, x1 = W * gz.cellLedger.x1, y0 = H * gz.y0, y1 = H * gz.y1;
    panelFrame(ctx, x0, y0, x1, y1, 'KELLY CELL LEDGER');
    const cols = [
      ['EXCH', 0.14], ['STRAT', 0.30], ['TKR', 0.20], ['REGIME', 0.16], ['N_EFF', 0.08], ['SCORE', 0.06], ['MULT', 0.06],
    ];
    const innerX0 = x0 + 6, innerW = x1 - x0 - 10;
    let cx = innerX0;
    const colX = cols.map(([lbl, frac]) => { const at = cx; cx += innerW * frac; return at; });
    // Column-header row sits BELOW the panel title (Jin 2026-07-11 self-
    // critique round 1: the two were stamping the same y — "KELLY CELL
    // LEDGER" and "EXCH" collided text-on-text).
    ctx.font = '600 6px JetBrains Mono, monospace'; ctx.fillStyle = 'rgba(138,148,176,0.55)'; ctx.textAlign = 'left';
    cols.forEach(([lbl], i) => ctx.fillText(lbl, colX[i], y0 + 22));
    const top = (c && c.cell_top) || [], bot = (c && c.cell_bottom) || [];
    const rowH = (y1 - y0 - 30) / 8;
    const drawRow = (row, i) => {
      const ry = y0 + 26 + (i + 0.75) * rowH;
      ctx.font = '600 6.5px ui-monospace, Menlo, monospace'; ctx.fillStyle = field.rgba(STEEL, 0.85); ctx.textAlign = 'left';
      ctx.fillText(String(row.exchange || '').slice(0, 3).toUpperCase(), colX[0], ry);
      ctx.fillText(String(row.strategy || '').slice(0, 12), colX[1], ry);
      ctx.fillText(String(row.ticker || '').slice(0, 8), colX[2], ry);
      ctx.fillText(String(row.regime || '').slice(0, 8), colX[3], ry);
      ctx.textAlign = 'right';
      ctx.fillText(fmtNum(row.n_eff, 1), colX[4] + innerW * cols[4][1] - 2, ry);
      ctx.fillText(fmtNum(row.score, 2), colX[5] + innerW * cols[5][1] - 2, ry);
      // MULT is the only warm-white-emphasised column — never P/L color (not money).
      ctx.font = '700 6.5px ui-monospace, Menlo, monospace'; ctx.fillStyle = field.rgba(WARM, 0.92);
      ctx.fillText(fmtNum(row.mult, 2) + '×', colX[6] + innerW * cols[6][1] - 2, ry);
    };
    top.slice(0, 4).forEach(drawRow);
    if (top.length && bot.length) {
      const dy = y0 + 26 + 4 * rowH;
      ctx.beginPath(); ctx.moveTo(innerX0, dy); ctx.lineTo(x1 - 6, dy);
      ctx.strokeStyle = 'rgba(159,192,255,0.14)'; ctx.lineWidth = 0.6; ctx.stroke();
    }
    bot.slice(0, 4).forEach((row, i) => drawRow(row, i + 4));
  }

  /* ===== BR — per-venue strip + SESSION sparkline (equityRing/
   * pushEquitySample now live up by equityOf()/sessionPeakUsd() — shared
   * with the TL crown + BAY gauges, see rework r3 fix there) ===== */
  const VENUE_LABEL = { okx: 'OKX', cap: 'CAPITAL', alp: 'ALPACA' };
  function drawPanelBR(ctx, c, W, H) {
    const br = Z.ladderBand.br;
    const x0 = W * br.x0, x1 = W * br.x1, y0 = H * Z.ladderBand.y0, y1m = H * 0.90, y1 = H * Z.ladderBand.y1;
    panelFrame(ctx, x0, y0, x1, y1m, 'STREAMS');
    const streams = (c && c.streams) || [];
    const rowH = (y1m - y0 - 14) / 3;
    ['okx', 'cap', 'alp'].forEach((v, i) => {
      const s = streams.find((x) => x.venue === v) || {};
      const ry = y0 + 14 + (i + 0.7) * rowH;
      const vc = field.venueColorOf(v) || STEEL;
      ctx.beginPath(); ctx.arc(x0 + 8, ry - 3, 2, 0, Math.PI * 2); ctx.fillStyle = field.rgba(vc, 0.9); ctx.fill();
      ctx.font = '600 6.5px JetBrains Mono, monospace'; ctx.textAlign = 'left'; ctx.fillStyle = field.rgba(vc, 0.85);
      const staleTag = s.marks_label ? ' · ' + s.marks_label + ' ' + Math.round((s.age_sec || 0) / 60) + 'm' : '';
      ctx.fillText((VENUE_LABEL[v] || v.toUpperCase()) + staleTag, x0 + 14, ry);
      ctx.textAlign = 'right'; ctx.font = '700 7.5px ui-monospace, Menlo, monospace';
      ctx.fillStyle = field.rgba(STEEL, 0.85);
      ctx.fillText('exp ' + fmtUsd(s.exposed || 0), x0 + (x1 - x0) * 0.66, ry);
      const upnl = s.upnl || 0;
      ctx.fillStyle = field.rgba(upnl >= 0 ? PNL_POS : PNL_NEG, 0.92);
      ctx.fillText(fmtUsd(upnl), x1 - 6, ry);
    });
    // SESSION sparkline — global equity client ring buffer (virtual-mode
    // branched, same as the crown/gauges — see equityOf()).
    pushEquitySample(c && c.core && equityOf(c.core).equity);
    if (equityRing.length > 1) {
      const sx0 = x0 + 6, sx1 = x1 - 6, sy0 = y1m - 8, sy1 = y1m - 3;
      const lo = Math.min(...equityRing), hi = Math.max(...equityRing);
      const span = Math.max(1, hi - lo);
      ctx.beginPath();
      equityRing.forEach((v, i) => {
        const px = sx0 + (sx1 - sx0) * (i / (equityRing.length - 1));
        const py = sy1 - (sy1 - sy0) * ((v - lo) / span);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.strokeStyle = field.rgba(STEEL, 0.55); ctx.lineWidth = 0.8; ctx.stroke();
      const delta = equityRing[equityRing.length - 1] - equityRing[0];
      ctx.font = '600 6px JetBrains Mono, monospace'; ctx.textAlign = 'left';
      ctx.fillStyle = 'rgba(138,148,176,0.55)'; ctx.fillText('SESSION', sx0, sy0 - 2);
      ctx.textAlign = 'right';
      ctx.fillStyle = field.rgba(delta >= 0 ? PNL_POS : PNL_NEG, 0.85);
      ctx.fillText((delta >= 0 ? '+' : '') + fmtUsd(delta), sx1, sy0 - 2);
    }
  }

  /* ===== EXIT FSM strip — fsm_states + exit_reason tally + g6/g7 decisions
   * (the exit_tally node cluster's count binding, moved off the constellation
   * per field.js's M2 skip). ===== */
  function drawExitFsm(ctx, c, W, H) {
    const br = Z.ladderBand.br;
    const x0 = W * br.x0, x1 = W * br.x1, y0 = H * 0.905, y1 = H * Z.ladderBand.y1;
    panelFrame(ctx, x0, y0, x1, y1, 'EXIT FSM');
    const ex = (c && c.exit) || {};
    const tally = ex.tally || {};
    // Rows start BELOW the panel title (Jin 2026-07-11 self-critique round
    // 1: same class of bug as the CELL LEDGER fix — the first bar was
    // stamping right over "EXIT FSM"). Panel is only ~0.05H tall, so this
    // caps at 4 reasons at a tight 7px pitch rather than 5 at 9px.
    const reasons = Object.keys(tally).sort((a, b) => tally[b] - tally[a]).slice(0, 4);
    const maxN = Math.max(1, ...reasons.map((r) => tally[r]));
    let ty = y0 + 20;
    ctx.font = '600 5.5px JetBrains Mono, monospace'; ctx.textAlign = 'left';
    reasons.forEach((r) => {
      const w = ((x1 - x0) * 0.42) * (tally[r] / maxN);
      ctx.fillStyle = 'rgba(138,148,176,0.18)'; ctx.fillRect(x0 + 6, ty - 4, (x1 - x0) * 0.42, 5);
      ctx.fillStyle = field.rgba(STEEL, 0.7); ctx.fillRect(x0 + 6, ty - 4, w, 5);
      ctx.fillStyle = 'rgba(223,232,255,0.8)';
      ctx.fillText(r.slice(0, 12).toLowerCase() + ' ' + tally[r], x0 + 10 + (x1 - x0) * 0.42, ty);
      ty += 7;
    });
    const g6n = Object.values(ex.g6_decisions || {}).reduce((a, b) => a + b, 0);
    const g7n = Object.values(ex.g7_decisions || {}).reduce((a, b) => a + b, 0);
    ctx.textAlign = 'right'; ctx.fillStyle = field.rgba(STEEL, 0.7);
    ctx.fillText('g6:' + g6n + '  g7:' + g7n, x1 - 6, y1 - 5);
  }

  /* ===== register — g8 satellite cloud as a right-edge instrument column
   * (learners/AI judges/session·liq·crisis axes/gate tallies/health). No
   * orbit, no rotation — coordinates come from field.js's M2 register
   * layout; this only draws the name/value/delta text next to each row +
   * a 1px leader to g8. ===== */
  // Jin 2026-07-11 self-critique round 4: registerRect is only ~0.026W wide
  // (~50px) — a right-aligned "label value delta" string at 6px font ran
  // 60-90px, spilling left THROUGH the row's own marker dot and well past
  // the column into the gate spine. Text is now LEFT-aligned starting just
  // right of the dot (field.js moved the dot to the column's left edge for
  // the same reason) with a much shorter abbreviation + no delta — trades
  // the delta figure for actually fitting in the lane it's drawn in.
  function drawRegister(ctx, W, H) {
    const z = Z.registerRect;
    const x0 = W * z.x0;
    const nodes = [].concat(
      field.nodesOf('action'), field.nodesOf('obs'), field.nodesOf('orbit'), field.nodesOf('axis'),
    );
    const gs = field.gateScreen()[7];
    ctx.font = '500 5px JetBrains Mono, monospace'; ctx.textAlign = 'left';
    nodes.forEach((n) => {
      const s = field.screenOf(n.id);
      if (!s) return;
      // ai_judge orbit nodes carry no numeric field (role satellites, not
      // measurements) — show the role name alone rather than a bare unlabeled
      // dot (Jin 2026-07-11 self-critique round 4).
      const val = n.value != null ? fmtNum(n.value, 2) : (n.calls_per_h != null ? String(n.calls_per_h) : (n.count != null ? String(n.count) : null));
      const lbl = String(n.label || '').split(':')[0].replace(/^(orbit_|axis_)/, '').slice(0, val == null ? 12 : 7);
      const active = n.state === 'firing' || n.state === 'lit';
      ctx.fillStyle = field.rgba(active ? WARM : STEEL, active ? 0.8 : 0.45);
      ctx.fillText(val == null ? lbl : lbl + ' ' + val, x0 + 6, s.y + 2);
      if (gs) {
        ctx.beginPath(); ctx.moveTo(x0 - 4, s.y); ctx.lineTo(gs.x, gs.y);
        ctx.strokeStyle = 'rgba(150,190,225,0.05)'; ctx.lineWidth = 0.5; ctx.stroke();
      }
    });
  }

  /* ===== regime confidence matrix — venue x asset-class grid, sited in the
   * empty gap between the DROP LANE panel and GATE OPS (Jin 2026-07-11 self-
   * critique rounds 1-2: console.regimes is pre-aggregated server-side to
   * (venue, asset-class) — see polaris_graph.py's _console_block — a
   * handful of rows, NOT the raw ~1200 per-symbol regime_state rows a real
   * DB carries. Round 1 drew one column per raw group_id — off-screen.
   * Round 2 placed the aggregated grid "beside" the existing reg dots at
   * their own y — collided with the g3 gate reticle/label, which shares
   * that y-band. This is round 3: the reg dots + gate spine both live in
   * y 0.60-0.82H; the only genuinely empty stretch at this x is BELOW them,
   * between the DROP LANE panel (x<=0.19W) and GATE OPS (x>=0.36W)). 3px-
   * ish cells, brightness=confidence, no rotation/orbit — a fixed read-only
   * grid. ===== */
  function drawRegimeMatrix(ctx, c, W, H) {
    const regs = (c && c.regimes) || [];
    if (!regs.length) return;
    const venues = Array.from(new Set(regs.map((r) => r.venue))).sort();
    const classes = Array.from(new Set(regs.map((r) => r.group_id))).sort();
    const cell = 9, gap = 2;
    const x0 = W * 0.21, y0 = H * 0.83;
    ctx.font = '600 6px JetBrains Mono, monospace'; ctx.fillStyle = 'rgba(160,200,235,0.35)'; ctx.textAlign = 'left';
    ctx.fillText('REGIME MATRIX', x0, y0 - 4);
    classes.forEach((cls, ci) => {
      ctx.font = '500 5px JetBrains Mono, monospace'; ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(138,148,176,0.55)';
      ctx.fillText(cls.slice(0, 4), x0 + 14 + ci * (cell + gap), y0 + 6);
      venues.forEach((v, vi) => {
        const row = regs.find((r) => r.venue === v && r.group_id === cls);
        const conf = row ? row.confidence : 0;
        const vc = field.venueColorOf(v) || STEEL;
        const cy = y0 + 10 + vi * (cell + gap);
        if (ci === 0) {
          ctx.font = '600 5px JetBrains Mono, monospace'; ctx.textAlign = 'right';
          ctx.fillStyle = field.rgba(vc, 0.7);
          ctx.fillText(v.toUpperCase(), x0 + 10, cy + cell - 2);
        }
        if (!row) return;
        ctx.fillStyle = field.rgba(vc, 0.15 + 0.55 * Math.max(0, Math.min(1, conf)));
        ctx.fillRect(x0 + 14 + ci * (cell + gap), cy, cell, cell);
      });
    });
  }

  /* ===== offscreen bake — see the file-header implementation note. Keyed on
   * object identity of both `c` and `summary` (not just `c`) since drawGauges
   * reads both and the two pollers, while both ~1s-cadenced, aren't
   * guaranteed in lockstep. ===== */
  let bakeCanvas = null, bakeCtx = null, bakedW = 0, bakedH = 0;
  let bakedC, bakedSummary; // undefined initial — distinct from any real poll value, incl. null
  function ensureBake(c, summary, W, H) {
    if (!bakeCanvas || bakedW !== W || bakedH !== H) {
      // Match wall_spine.js's fitCanvas() DPR handling — the live ctx this
      // gets drawImage'd into is already scaled by this same dpr (its
      // ctx.setTransform(dpr,0,0,dpr,0,0)), so an un-scaled W×H bake canvas
      // would blit blurry on a Retina/HiDPI display next to crisp live text.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      bakeCanvas = document.createElement('canvas');
      bakeCanvas.width = Math.max(1, Math.round(W * dpr));
      bakeCanvas.height = Math.max(1, Math.round(H * dpr));
      bakeCtx = bakeCanvas.getContext('2d');
      bakeCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      bakedW = W; bakedH = H; bakedC = undefined; bakedSummary = undefined;
    }
    if (c === bakedC && summary === bakedSummary) return;
    bakedC = c; bakedSummary = summary;
    bakeCtx.clearRect(0, 0, W, H);
    // drawPanelBR first — it pushes this poll's equity sample into the
    // shared ring sessionPeakUsd() reads (consumed by drawGauges right below
    // AND by the still-live TL crown later this same frame).
    drawPanelBR(bakeCtx, c, W, H);
    drawGauges(bakeCtx, c, summary, 0, W, H);
    drawCellLedger(bakeCtx, c, W, H);
    drawExitFsm(bakeCtx, c, W, H);
    drawRegimeMatrix(bakeCtx, c, W, H);
  }

  /* ===== public draw seam — 2-line append in wall_spine.js's frame() ===== */
  function draw(ctx, now) {
    const spine = window.PolarisSpine;
    if (!spine) return;
    const sz = field.sizeOf && field.sizeOf();
    if (!sz || !sz.W || !sz.H) return;
    const { W, H } = sz;
    const c = spine.consoleOf ? spine.consoleOf() : null;
    const verdicts = spine.verdictsOf ? spine.verdictsOf() : [];
    const summary = spine.flowSummaryOf ? spine.flowSummaryOf() : {};
    const gatePulseAt = spine.gatePulseAt;
    ctx.save();
    ensureBake(c, summary, W, H);
    // 5-arg form — draws the (possibly DPR-scaled, so higher source-pixel-
    // count) bake canvas into a logical W×H rect of the already dpr-scaled
    // live ctx, not its raw source pixel size (see ensureBake's dpr note).
    ctx.drawImage(bakeCanvas, 0, 0, W, H);
    drawPanelTL(ctx, c, now, W, H);
    drawPanelTR(ctx, c, verdicts, now, W, H);
    drawGateLadder(ctx, c, gatePulseAt, now, W, H);
    drawRegister(ctx, W, H);
    ctx.restore();
  }

  window.PolarisConsoleReadouts = { draw };
})();
