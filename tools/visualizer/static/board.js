/* board.js — Polaris right-half analytics board + left-column bot log (DEMO/PAPER display-only).
 * Native HTML/CSS. 1s polling: /api/snapshot → #board, /api/botlog → #botlog-body.
 * All fetches cache-busted (?t=Date.now()) + no-store. No ANSI mirror. No sizing/order logic.
 * Injects its own <style>; renders into #board (right 70vw). Bot-log pane styled in index.html.
 */
(function () {
  'use strict';

  // ── Style injection ──────────────────────────────────────────────
  const CSS = `
  #board {
    height: 100vh; min-height: 0; overflow: hidden;
    display: grid;
    /* header / kpis / streams / rotation / equity (auto) + mid + bottom (bounded flex). minmax(0,..)
       keeps the sum inside 100vh; long lists scroll inside each panel's .p-body, never the page. */
    grid-template-rows: auto auto auto auto auto minmax(0, 1fr) minmax(0, 1.35fr);
    gap: 8px;
    padding: 10px 16px;
    box-sizing: border-box;
    background: linear-gradient(180deg, rgba(8,11,17,0.92), rgba(5,7,11,0.96));
    border-left: 1px solid var(--ghost);
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--p-wht);
  }
  #board .b-pos { color: var(--p-grn); }
  #board .b-neg { color: var(--p-red); }
  #board .b-flat { color: var(--p-dim); }
  #board .num { font-variant-numeric: tabular-nums; }

  /* Header */
  #board .b-head {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding-bottom: 4px; border-bottom: 1px solid var(--ghost);
  }
  #board .b-head .title { font-weight: 700; letter-spacing: 0.18em; color: var(--p-wht); font-size: 15px; }
  #board .b-head .star { color: var(--polaris-blue); }
  #board .b-head .badge {
    font-size: 10px; letter-spacing: 0.18em; font-weight: 700;
    padding: 2px 8px; border: 1px solid var(--polaris-blue); color: var(--polaris-blue);
  }
  #board .b-head .meta { color: var(--p-gry); font-size: 11px; }
  #board .b-head .meta b { color: var(--p-wht); font-weight: 700; }
  #board .b-head .clock { margin-left: auto; color: var(--p-cyn); font-weight: 700; font-size: 14px; }
  #board .b-head .regime-tag { color: var(--p-mag); font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase; }

  /* KPI cards — 70% width fits all 8 in one row */
  #board .kpis {
    display: grid; grid-template-columns: repeat(8, 1fr); gap: 7px;
  }
  #board .kpi {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.55);
    padding: 6px 9px;
    min-width: 0; overflow: hidden;
  }
  #board .kpi .k { color: var(--p-dim); font-size: 9px; letter-spacing: 0.10em; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board .kpi .v { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 2px; line-height: 1.1;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board .kpi .sub { color: var(--p-gry); font-size: 9px; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* Stream summary strip (T15 stage-2) — server-fed d.streams, 3 venue lanes.
     Display-only rollup; numbers are authoritative from snapshot.streams[]. */
  #board .streams-strip {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px;
  }
  #board .lane {
    border: 1px solid rgba(95,135,175,0.22);
    border-left: 4px solid var(--ghost);
    background: rgba(15,19,26,0.55);
    padding: 5px 9px; min-width: 0; overflow: hidden;
  }
  #board .lane.lane-a { border-left-color: var(--stream-a); }
  #board .lane.lane-b { border-left-color: var(--stream-b); }
  #board .lane.lane-c { border-left-color: var(--stream-c); }
  #board .lane .ln-top {
    display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  }
  #board .lane .ln-label {
    font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board .lane.lane-a .ln-label { color: var(--stream-a); }
  #board .lane.lane-b .ln-label { color: var(--stream-b); }
  #board .lane.lane-c .ln-label { color: var(--stream-c); }
  #board .lane .ln-eq { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums;
    color: var(--p-wht); white-space: nowrap; }
  #board .lane .ln-stats {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 8px; margin-top: 3px;
    font-size: 10px;
  }
  #board .lane .ln-stats .s { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lane .ln-stats .s .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .lane .ln-stats .s .lv { font-variant-numeric: tabular-nums; }
  /* Cost row (display-only "근거 있는 수익 추적") — fee / slip / AI$ / net-after-cost. */
  #board .lane .ln-cost {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px 8px; margin-top: 3px;
    font-size: 9px; border-top: 1px dotted rgba(255,255,255,0.08); padding-top: 3px;
  }
  #board .lane .ln-cost .s { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lane .ln-cost .s .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.06em; text-transform: uppercase; }
  #board .lane .ln-cost .s .lv { font-variant-numeric: tabular-nums; }
  /* Recent-closed mini-row per lane (OPEN vs CLOSED split) — compact closed list. */
  #board .lane .ln-closed {
    margin-top: 3px; font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    border-top: 1px dotted rgba(255,255,255,0.08); padding-top: 3px;
  }
  #board .lane .ln-closed .rc-lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.06em; text-transform: uppercase; }
  #board .lane .ln-closed .rc-item { margin-right: 7px; }
  #board .lane .ln-closed .rc-sym { color: var(--p-gry); }
  #board .lane .ln-closed .rc-pn { font-variant-numeric: tabular-nums; }

  /* Rotation + session-forced-exit telemetry strip (follow-up #12) — display-only.
     Surfaces the capital-rotation count + last fire (victim / E$_new vs E$_held /
     margin / cost) and the session-forced-exit counter. Graceful zero when none. */
  #board .rot-strip {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border: 1px solid rgba(95,135,175,0.22);
    border-left: 4px solid var(--p-mag);
    background: rgba(15,19,26,0.55);
    padding: 4px 10px; font-size: 10px; min-width: 0; overflow: hidden;
  }
  #board .rot-strip .rt-title {
    font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--p-mag);
  }
  #board .rot-strip .rt-kv { white-space: nowrap; }
  #board .rot-strip .rt-kv .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .rot-strip .rt-kv .lv { font-variant-numeric: tabular-nums; color: var(--p-wht); font-weight: 700; }
  #board .rot-strip .rt-last { color: var(--p-gry); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  #board .rot-strip .rt-last b { color: var(--p-cyn); }
  #board .rot-strip .rt-none { color: var(--p-dim); }

  /* Equity chart */
  #board .eq-wrap {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.40);
    padding: 5px 10px 3px;
  }
  #board .eq-wrap .eq-head {
    display: flex; align-items: baseline; gap: 16px;
    font-size: 10px; letter-spacing: 0.12em; color: var(--p-dim); text-transform: uppercase;
  }
  #board .eq-wrap .eq-head .h-title { color: var(--polaris-blue); font-weight: 700; }
  #board .eq-wrap .eq-head .v { color: var(--p-wht); font-weight: 700; }
  #board .eq-wrap svg { display: block; width: 100%; height: 72px; }
  /* Go-live confidence strip (Component A) — compact real-fee-net edge rollup
     under the equity curve. Display-only. */
  #board .conf-strip {
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px;
    margin-top: 3px; padding-top: 3px; border-top: 1px dotted rgba(95,135,175,0.14);
    font-size: 9.5px; letter-spacing: 0.04em; color: var(--p-dim);
  }
  #board .conf-strip .ck { color: var(--p-dim); text-transform: uppercase; letter-spacing: 0.10em; }
  #board .conf-strip .cv { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 0 5px; border-left: 2px solid var(--ghost);
  }
  #board .conf-strip .cell.lcb-pos { border-left-color: var(--p-grn); }
  #board .conf-strip .cell.lcb-neg { border-left-color: var(--p-red); }
  #board .conf-strip .cell .nm { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell .rg { color: var(--p-dim); }

  /* Tables row (positions + recent trades side by side) — wide at 70% */
  #board .mid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    min-height: 0;
  }
  #board .panel {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.40);
    display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  }
  #board .panel .p-head {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 4px 10px; border-bottom: 1px solid var(--ghost); flex: 0 0 auto;
    color: var(--polaris-blue); font-size: 10px; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase;
  }
  #board .panel .p-head .cnt { color: var(--p-cyn); letter-spacing: 0; }
  #board .panel .p-body { overflow: auto; flex: 1 1 0; min-height: 0; }
  #board .panel .p-body::-webkit-scrollbar { width: 5px; height: 5px; }
  #board .panel .p-body::-webkit-scrollbar-thumb { background: var(--ghost); }

  #board table { width: 100%; border-collapse: collapse; font-size: 11px; table-layout: fixed; }
  #board thead th {
    position: sticky; top: 0; background: rgba(10,13,18,0.96);
    color: var(--p-dim); font-weight: 700; text-align: right; letter-spacing: 0.04em;
    padding: 4px 7px; font-size: 10px; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board thead th.l { text-align: left; }
  #board tbody td {
    padding: 3px 7px; text-align: right; font-variant-numeric: tabular-nums;
    border-bottom: 1px dotted rgba(95,135,175,0.08); color: var(--p-gry);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board tbody td.l { text-align: left; }
  #board tbody td.tk { color: var(--p-wht); font-weight: 700; }
  #board tbody td.ex { color: var(--p-cyn); font-size: 10px; font-weight: 700; }
  /* per-table column widths (table-layout:fixed) — numeric cols sized, text cols flex+ellipsis */
  #board .tbl-pos col.c-ven  { width: 8%; }
  #board .tbl-pos col.c-sym  { width: 22%; }
  #board .tbl-pos col.c-str  { width: 14%; }
  #board .tbl-pos col.c-side { width: 9%; }
  #board .tbl-pos col.c-size { width: 12%; }
  #board .tbl-pos col.c-upnl { width: 13%; }
  #board .tbl-pos col.c-dlt  { width: 9%; }
  #board .tbl-pos col.c-held { width: 8%; }
  #board .tbl-pos col.c-mult { width: 9%; }
  #board .tbl-trd col.c-time { width: 13%; }
  #board .tbl-trd col.c-ven  { width: 8%; }
  #board .tbl-trd col.c-sym  { width: 20%; }
  #board .tbl-trd col.c-str  { width: 14%; }
  #board .tbl-trd col.c-side { width: 9%; }
  #board .tbl-trd col.c-pnl  { width: 14%; }
  #board .tbl-trd col.c-r    { width: 10%; }
  #board .tbl-trd col.c-rsn  { width: 12%; }
  #board tbody td.dir.long, #board tbody td.dir.buy { color: var(--p-grn); font-weight: 700; }
  #board tbody td.dir.short, #board tbody td.dir.sell { color: var(--p-red); font-weight: 700; }
  #board tbody tr:hover td { background: rgba(95,135,175,0.06); }
  #board .empty { color: var(--p-dim); padding: 8px; text-align: center; font-size: 10px; }

  /* Bottom analytics grid — 3-col, roomier at 70% */
  #board .bottom-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
    gap: 8px; min-height: 0;
  }
  #board .mini { font-size: 11px; }
  #board .mini .row {
    display: grid; align-items: center; gap: 8px; padding: 2px 10px;
    border-bottom: 1px dotted rgba(95,135,175,0.08);
  }
  /* min-width:0 so 1fr name column can shrink → ellipsis fires instead of overflowing the panel */
  #board .mini .row > * { min-width: 0; }
  #board .mini .row .name { color: var(--p-wht); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .num { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .sub { color: var(--p-dim); font-size: 9px; }

  /* gate funnel bars */
  #board .gate-row { grid-template-columns: 24px 1fr 42px; }
  #board .gate-bar { height: 8px; background: rgba(95,135,175,0.12); position: relative; }
  #board .gate-bar > i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--p-cyn); }
  #board .gate-row .gid { color: var(--p-dim); font-size: 10px; }
  #board .gate-row .pct { text-align: right; font-variant-numeric: tabular-nums; }

  /* strategy / cell / edge rows — slightly wider numeric cols for the larger font */
  #board .strat-row { grid-template-columns: 1fr 40px 40px 64px; }
  #board .cell-row  { grid-template-columns: 1fr 46px 44px; }
  #board .edge-row  { grid-template-columns: 1fr 70px 50px; }
  #board .learn-row { grid-template-columns: 1fr 56px 46px; }
  #board .alert-row { grid-template-columns: 50px 1fr; }
  #board .verdict-edge { color: var(--p-grn); }
  #board .verdict-anti { color: var(--p-red); }
  #board .verdict-neutral { color: var(--p-ylw); }
  #board .edge-row .vrd { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lvl-ERROR, #board .lvl-CRITICAL { color: var(--p-red); font-weight: 700; }
  #board .lvl-WARN, #board .lvl-WARNING { color: var(--p-ylw); }
  #board .lvl-INFO { color: var(--p-cyn); }

  /* Stream lanes (T15 stage-1) — client-side group-by venue→stream. Display-only.
     Tokens match sphere-render EXCHANGE_COLOR hex (venue is already a 1st-class
     encoding on the globe). A=okx B=capital C=alpaca. */
  #board { --stream-a: #5fdfff; --stream-b: #a87cff; --stream-c: #ffc84f; }
  #board td.lane-head {
    padding: 2px 7px 2px 6px; border-left: 4px solid var(--ghost);
    border-bottom: 1px solid var(--ghost);
    font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: var(--p-dim); background: rgba(15,19,26,0.55);
  }
  #board td.lane-head .ln-cnt { color: var(--p-gry); letter-spacing: 0; font-weight: 400; margin-left: 5px; }
  #board td.lane-head.lane-a { border-left-color: var(--stream-a); color: var(--stream-a); }
  #board td.lane-head.lane-b { border-left-color: var(--stream-b); color: var(--stream-b); }
  #board td.lane-head.lane-c { border-left-color: var(--stream-c); color: var(--stream-c); }
  #board tbody tr.row-a td:first-child { border-left: 3px solid var(--stream-a); }
  #board tbody tr.row-b td:first-child { border-left: 3px solid var(--stream-b); }
  #board tbody tr.row-c td:first-child { border-left: 3px solid var(--stream-c); }
  #board tbody tr.row-x td:first-child { border-left: 3px solid var(--ghost); }
  `;

  function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── helpers ───────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  function fmtUsd(v, dp = 0) {
    if (v == null || isNaN(v)) return '—';
    const sign = v < 0 ? '-' : '';
    const a = Math.abs(v);
    return sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function fmtPct(v, dp = 2) { return (v == null || isNaN(v)) ? '—' : v.toFixed(dp) + '%'; }
  function pn(v) { return v > 0 ? 'b-pos' : v < 0 ? 'b-neg' : 'b-flat'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
  function clockStr() {
    const d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  }
  function hms(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + 'h' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm' + String(s).padStart(2, '0') + 's';
    return s + 's';
  }
  function hhmmss(epoch) {
    const d = new Date(epoch * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  }

  // ── Stream lanes (T15 stage-1) ────────────────────────────────────
  // Client-side venue→stream group-by. A=okx B=capital C=alpaca. Display-only —
  // no snapshot/server change (stage-2 adds server-side StreamSummary).
  const VENUE_TO_STREAM = { okx: 'A', capital: 'B', alpaca: 'C' };
  const STREAM_ORDER = ['A', 'B', 'C', 'X'];          // X = unknown venue (lane tail)
  const STREAM_LABEL = { A: 'Stream A · OKX', B: 'Stream B · Capital', C: 'Stream C · Alpaca', X: 'Other' };
  function venueStream(venue) {
    return VENUE_TO_STREAM[String(venue || '').toLowerCase()] || 'X';
  }
  // Group rows by stream into lanes, preserving STREAM_ORDER and within-stream order.
  function laneGroups(rows) {
    const by = {};
    rows.forEach(r => { const s = venueStream(r.venue); (by[s] = by[s] || []).push(r); });
    return STREAM_ORDER.filter(s => by[s] && by[s].length).map(s => ({ stream: s, rows: by[s] }));
  }

  // ── one-time skeleton ─────────────────────────────────────────────
  function skeleton() {
    return `
    <div class="b-head">
      <span class="title"><span class="star">★</span> POLARIS</span>
      <span class="badge">DEMO·PAPER</span>
      <span class="regime-tag" id="b-regime">—</span>
      <span class="meta">focus <b id="b-focus">—</b> · cells <b id="b-cells">—</b> · refresh <b id="b-refresh">—</b></span>
      <span class="clock" id="b-clock">--:--:--</span>
    </div>

    <div class="kpis" id="b-kpis"></div>

    <div class="streams-strip" id="b-streams"></div>

    <div class="rot-strip" id="b-rotation"></div>

    <div class="eq-wrap">
      <div class="eq-head">
        <span class="h-title" title="Headline = REAL-FEE-NET (real OKX 0.10% taker). Dimmed line = demo-actual (0.7% demo drain). Go-live trigger (Jin 2026-05-31) = the real-fee-net curve trending UP.">Equity · REAL-FEE-NET (go-live)</span>
        <span>real Δ <span class="v" id="eq-real-delta">—</span></span>
        <span class="b-flat">demo Δ <span class="v" id="eq-demo-delta">—</span></span>
        <span title="Real-vs-demo fee wedge over the session (demo OKX is a 7x penalty vs real). Recovered if you go live on real OKX.">fee wedge <span class="v" id="eq-fee-wedge">—</span></span>
      </div>
      <svg id="eq-svg" viewBox="0 0 600 84" preserveAspectRatio="none"></svg>
      <div class="conf-strip" id="b-confidence"></div>
    </div>

    <div class="mid">
      <div class="panel">
        <div class="p-head"><span>Open Positions</span><span class="cnt" id="pos-cnt">0</span></div>
        <div class="p-body" id="pos-body"></div>
      </div>
      <div class="panel">
        <div class="p-head"><span>Recent Trades</span><span class="cnt" id="trd-cnt">0</span></div>
        <div class="p-body" id="trd-body"></div>
      </div>
    </div>

    <div class="bottom-grid">
      <div class="panel"><div class="p-head"><span>Per-Strategy</span></div><div class="p-body mini" id="strat-body"></div></div>
      <div class="panel" title="8-gate AI pipeline. pass% = signal survival rate at each stage.&#10;G1 Universe · G2 Strategy signal · G3 Validator (main cut) · G4 Pre-Entry · G5 Sizer · G6 Position Monitor · G7 Adaptive Exit · G8 Reflector"><div class="p-head"><span>Gate Funnel</span></div><div class="p-body mini" id="gate-body"></div></div>
      <div class="panel"><div class="p-head"><span>Cell Matrix</span></div><div class="p-body mini" id="cell-body"></div></div>
      <div class="panel"><div class="p-head"><span>Edge Validation</span></div><div class="p-body mini" id="edge-body"></div></div>
      <div class="panel"><div class="p-head"><span>Learners</span></div><div class="p-body mini" id="learn-body"></div></div>
      <div class="panel"><div class="p-head"><span>Alerts · AI Cost</span></div><div class="p-body mini" id="alert-body"></div></div>
    </div>`;
  }

  // ── renderers ─────────────────────────────────────────────────────
  function renderHeader(d) {
    const topRegime = (d.regime_bars || []).slice().sort((a, b) => b.count - a.count)[0];
    $('b-regime').textContent = topRegime ? topRegime.regime.replace(/_/g, ' ') : '—';
    $('b-focus').textContent = d.universe_focus_n ?? '—';
    $('b-cells').textContent = d.active_cells_n ?? '—';
    $('b-refresh').textContent = d.universe_last_refresh || '—';
  }

  function renderKpis(d) {
    // closed-weighted win rate
    let cw = 0, cn = 0;
    (d.strategy_stats || []).forEach(s => { cw += (s.wr_pct || 0) * (s.closed_n || 0); cn += (s.closed_n || 0); });
    const wr = cn ? cw / cn : null;
    const aiCost = (d.gpt_stats || []).reduce((a, g) => a + (g.cost_24h_proj_usd || 0), 0);
    const expPct = d.equity_now ? (d.exposed_usd / d.equity_now) * 100 : 0;

    const cards = [
      { k: 'Net PnL (session)', v: fmtUsd(d.daily_pnl_usd, 2), cls: pn(d.daily_pnl_usd), sub: (d.daily_trades || 0) + ' trades' },
      { k: 'Equity', v: fmtUsd(d.equity_now, 0), cls: '', sub: 'start ' + fmtUsd(d.starting_capital, 0) },
      { k: 'Drawdown', v: '-' + fmtPct(d.drawdown_pct), cls: (d.drawdown_pct > 0 ? 'b-neg' : 'b-flat'), sub: 'peak ' + fmtUsd(d.peak_equity, 0) },
      { k: 'Sharpe (session)', v: (d.sharpe_24h == null ? '—' : d.sharpe_24h.toFixed(2)), cls: pn(d.sharpe_24h), sub: '' },
      { k: 'Win Rate', v: (wr == null ? '—' : fmtPct(wr, 1)), cls: '', sub: cn + ' closed' },
      { k: 'Exposure', v: fmtUsd(d.exposed_usd, 0), cls: '', sub: fmtPct(expPct, 1) + ' of eq · ' + (d.open_positions_n || 0) + ' pos' },
      { k: 'uPnL', v: fmtUsd(d.upnl_total, 2), cls: pn(d.upnl_total), sub: '' },
      { k: 'AI Cost 24h', v: fmtUsd(aiCost, 2), cls: '', sub: 'projected' },
    ];
    $('b-kpis').innerHTML = cards.map(c =>
      `<div class="kpi"><div class="k">${esc(c.k)}</div><div class="v num ${c.cls}">${c.v}</div><div class="sub">${esc(c.sub)}</div></div>`
    ).join('');
  }

  // Per-stream summary strip (T15 stage-2). Server-fed d.streams is authoritative
  // for these rollup numbers (prefer over client laneGroups, which stays for the
  // per-row position/trade grouping). Graceful when d.streams missing/empty.
  function renderStreams(d) {
    const el = $('b-streams');
    if (!el) return;
    const rows = d.streams || [];
    if (!rows.length) { el.innerHTML = ''; return; }   // older snapshot → render nothing
    el.innerHTML = rows.map(s => {
      const lc = venueStream(s.venue).toLowerCase();    // reuse stage-1 venue→stream SSOT
      const expPct = s.equity_usd ? (s.exposed_usd / s.equity_usd) * 100 : 0;
      // Cost row (display-only). Graceful when an older snapshot omits the
      // fields: net_after_cost_usd === undefined → skip the row entirely.
      const hasCost = s.net_after_cost_usd != null;
      const costRow = hasCost ? `
        <div class="ln-cost">
          <span class="s"><span class="lk">Fee</span> <span class="lv b-flat">${fmtUsd(s.fee_usd, 2)}</span></span>
          <span class="s"><span class="lk">Slip</span> <span class="lv b-flat">${fmtUsd(s.slippage_usd, 2)}</span></span>
          <span class="s" title="AI cost attributed to this lane only (position-linked gate calls); pre-position G1-G5 LLM spend is unattributable and excluded"><span class="lk">AI$*</span> <span class="lv b-flat">${fmtUsd(s.ai_cost_usd, 4)}</span></span>
          <span class="s"><span class="lk">Net-Cost</span> <span class="lv ${pn(s.net_after_cost_usd)}">${fmtUsd(s.net_after_cost_usd, 2)}</span></span>
        </div>` : '';
      return `<div class="lane lane-${lc}" title="${esc(s.label)} (${esc(s.product_class)}) · start ${fmtUsd(s.starting_capital, 0)} · DD ${fmtPct(s.drawdown_pct)}">
        <div class="ln-top">
          <span class="ln-label">${esc(s.label)}</span>
          <span class="ln-eq">${fmtUsd(s.equity_usd, 0)}</span>
        </div>
        <div class="ln-stats">
          <span class="s"><span class="lk">Net PnL</span> <span class="lv ${pn(s.net_pnl_usd)}">${fmtUsd(s.net_pnl_usd, 2)}</span></span>
          <span class="s"><span class="lk">uPnL</span> <span class="lv ${pn(s.upnl_usd)}">${fmtUsd(s.upnl_usd, 2)}</span></span>
          <span class="s"><span class="lk">Exposure</span> <span class="lv b-flat">${fmtUsd(s.exposed_usd, 0)}</span></span>
          <span class="s" title="currently-open positions"><span class="lk">Open</span> <span class="lv ${s.open_positions_n ? 'b-pos' : 'b-flat'}">${s.open_positions_n || 0}</span></span>
          <span class="s" title="closed trades this session"><span class="lk">Closed</span> <span class="lv b-flat">${(s.closed_n != null ? s.closed_n : (s.daily_trades || 0))}</span></span>
          <span class="s"><span class="lk">Exp%</span> <span class="lv b-flat">${fmtPct(expPct, 1)}</span></span>
        </div>${costRow}${recentClosedRow(s)}
      </div>`;
    }).join('');
  }

  // Recent-closed mini-row per lane (OPEN vs CLOSED split): compact list of this
  // lane's most-recent closed trades. Graceful: omitted when older snapshot has
  // no recent_closed or the lane has no closed trades.
  function recentClosedRow(s) {
    const rc = s.recent_closed || [];
    if (!rc.length) return '';
    const items = rc.slice(0, 4).map(t => {
      const cls = pn(t.pnl_usd);
      return `<span class="rc-item" title="${esc(t.symbol)} ${esc(t.strategy_id)} ${esc(t.exit_reason)} ${fmtUsd(t.pnl_usd, 2)}">`
        + `<span class="rc-sym">${esc(t.symbol)}</span> <span class="rc-pn ${cls}">${fmtUsd(t.pnl_usd, 1)}</span></span>`;
    }).join('');
    return `<div class="ln-closed" title="recently-closed (distinct from currently-open)"><span class="rc-lk">closed:</span> ${items}</div>`;
  }

  // Rotation + session-forced-exit telemetry strip (follow-up #12). Server-fed
  // top-level fields: rotation_count / session_forced_exit_count / last_rotation
  // (victim symbol, E$_new vs E$_held, margin, cost). Graceful zero when none —
  // older snapshots omit these fields entirely.
  function renderRotation(d) {
    const el = $('b-rotation');
    if (!el) return;
    const rc = d.rotation_count || 0;
    const sfe = d.session_forced_exit_count || 0;
    const last = d.last_rotation || null;
    let lastHtml;
    if (last) {
      const eNew = last.e_new || 0, eHeld = last.e_held || 0;
      lastHtml = `<span class="rt-last" title="last rotation: ${esc(last.venue)} closed ${esc(last.victim_symbol)} (${esc(last.victim_strategy)}) → E$new ${eNew.toFixed(2)} vs E$held ${eHeld.toFixed(2)}, margin ${(last.margin||0).toFixed(2)}, cost ${(last.cost||0).toFixed(2)}">`
        + `last <b>${esc(last.victim_symbol)}</b> `
        + `<span class="${pn(eNew - eHeld)}">E$new ${eNew.toFixed(1)}</span> vs E$held ${eHeld.toFixed(1)} `
        + `· mgn ${(last.margin||0).toFixed(1)} · cost ${(last.cost||0).toFixed(2)}</span>`;
    } else {
      lastHtml = `<span class="rt-last rt-none">no rotations yet</span>`;
    }
    el.innerHTML =
      `<span class="rt-title" title="capital-rotation = finite-capital opportunity-cost redeploy (display-only telemetry)">Rotation</span>`
      + `<span class="rt-kv" title="capital rotations fired this session"><span class="lk">Rotations</span> <span class="lv">${rc}</span></span>`
      + `<span class="rt-kv" title="session-forced exits (calendar integrity, time-only)"><span class="lk">Session Exits</span> <span class="lv">${sfe}</span></span>`
      + lastHtml;
  }

  // Build an SVG line path for ``curve`` on a SHARED [min,max] value scale so
  // the real-fee-net vs demo-actual divergence is visible on one axis.
  function eqPath(curve, x, y, withArea) {
    const n = curve.length;
    let line = '', area = `M ${x(0)} 84 `;
    curve.forEach((v, i) => {
      const px = x(i).toFixed(1), py = y(v).toFixed(1);
      line += (i === 0 ? 'M' : 'L') + ' ' + px + ' ' + py + ' ';
      area += 'L ' + px + ' ' + py + ' ';
    });
    area += `L ${x(n - 1)} 84 Z`;
    return withArea ? { line, area } : { line, area: '' };
  }

  function renderEquity(d) {
    const svg = $('eq-svg');
    // Headline = real-fee-net (the go-live curve, Jin 2026-05-31). Demo-actual
    // is overlaid dimmed as the as-charged reference. Both are full SESSION
    // curves (server-delivered); plotted on a shared value scale.
    const real = d.equity_curve_real_fee_net || [];
    const demo = d.equity_curve || [];
    if (real.length < 2 && demo.length < 2) { svg.innerHTML = ''; return; }
    const W = 600, H = 84, pad = 2;
    const all = real.concat(demo);
    const min = Math.min(...all), max = Math.max(...all);
    const span = (max - min) || 1;
    const xN = Math.max(real.length, demo.length);
    const x = i => pad + (i / (xN - 1)) * (W - 2 * pad);
    const y = v => pad + (1 - (v - min) / span) * (H - 2 * pad);

    const rUp = real.length >= 2 ? real[real.length - 1] >= real[0] : true;
    const rStroke = rUp ? 'var(--p-grn)' : 'var(--p-red)';
    const rFill = rUp ? 'rgba(135,215,135,0.14)' : 'rgba(215,135,135,0.14)';
    let html = '';
    if (demo.length >= 2) {
      const dp = eqPath(demo, x, y, false);
      // Demo-actual: dimmed dashed reference line (no area).
      html += `<path d="${dp.line}" fill="none" stroke="rgba(158,158,158,0.55)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
    }
    if (real.length >= 2) {
      const rp = eqPath(real, x, y, true);
      html += `<path d="${rp.area}" fill="${rFill}" stroke="none"/>`;
      html += `<path d="${rp.line}" fill="none" stroke="${rStroke}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
    }
    svg.innerHTML = html;

    // Headline deltas (real = headline; demo = dimmed reference).
    if (real.length >= 2) {
      const dv = real[real.length - 1] - real[0];
      const el = $('eq-real-delta');
      el.textContent = fmtUsd(dv, 0);
      el.className = 'v ' + pn(dv);
    }
    if (demo.length >= 2) {
      const dv = demo[demo.length - 1] - demo[0];
      $('eq-demo-delta').textContent = fmtUsd(dv, 0);
    }
    // Fee wedge = real_fee_net_now − demo_now = demo_fee_total − real_fee_total.
    const wedge = (d.demo_fee_total || 0) - (d.real_fee_total || 0);
    const we = $('eq-fee-wedge');
    we.textContent = '+' + fmtUsd(wedge, 0);
    we.className = 'v b-pos';
  }

  // Go-live confidence strip (Component A). Compact: overall edge metrics +
  // per-(strategy×regime) real-fee-net R with LCB sign. Graceful when an older
  // snapshot omits d.confidence.
  function renderConfidence(d) {
    const el = $('b-confidence');
    if (!el) return;
    const c = d.confidence;
    if (!c || !c.n_closed) { el.innerHTML = ''; return; }
    const pf = (c.profit_factor >= 9.99) ? '∞' : (c.profit_factor || 0).toFixed(2);
    const turn = (c.turnover_ratio || 0).toFixed(2) + '×';
    const overall =
      `<span><span class="ck">WR</span> <span class="cv">${fmtPct(c.win_rate_pct, 1)}</span></span>` +
      `<span><span class="ck">PF</span> <span class="cv">${pf}</span></span>` +
      `<span title="Σ notional / starting equity"><span class="ck">Turn</span> <span class="cv">${turn}</span></span>` +
      `<span title="Real fee drag (R) — the cost wedge under the REAL OKX schedule"><span class="ck">FeeR(real)</span> <span class="cv b-neg">-${(c.fee_drag_real_r || 0).toFixed(1)}</span></span>` +
      `<span title="Demo fee drag (R) — the punitive 0.7% demo drain (7x real)"><span class="ck">FeeR(demo)</span> <span class="cv b-flat">-${(c.fee_drag_demo_r || 0).toFixed(1)}</span></span>`;
    const cells = (c.cells || []).slice(0, 6).map(cell => {
      const lcb = cell.lcb_real_fee_net_r || 0;
      const cls = lcb > 0 ? 'lcb-pos' : lcb < 0 ? 'lcb-neg' : '';
      const er = (cell.expected_real_fee_net_r || 0).toFixed(2);
      return `<span class="cell ${cls}" title="${esc(cell.strategy_id)} · ${esc(cell.regime)} · n=${cell.n} · E[R]=${er} · LCB=${lcb.toFixed(2)}">
        <span class="nm">${esc(cell.strategy_id)}</span><span class="rg">${esc(cell.regime)}</span>
        <span class="cv ${pn(lcb)}">${cell.lcb_sign}${Math.abs(lcb).toFixed(2)}R</span>
      </span>`;
    }).join('');
    el.innerHTML = overall + cells;
  }

  function renderPositions(d) {
    const rows = d.positions || [];
    $('pos-cnt').textContent = rows.length;
    if (!rows.length) { $('pos-body').innerHTML = '<div class="empty">no open positions</div>'; return; }
    const COLS = 9;
    const body = laneGroups(rows).map(g => {
      const s = g.stream, lc = s.toLowerCase();
      const head = `<tr><td colspan="${COLS}" class="lane-head lane-${lc}">${esc(STREAM_LABEL[s])}<span class="ln-cnt">· ${g.rows.length}</span></td></tr>`;
      const trs = g.rows.map(p => {
        const rc = (p.row_count > 1) ? ` <span class="b-flat">×${p.row_count}</span>` : '';
        return `<tr class="row-${lc}">
          <td class="l ex" title="${esc(p.venue)}">${esc(p.venue)}</td>
          <td class="l tk" title="${esc(p.symbol)}${p.row_count>1?' ×'+p.row_count:''}">${esc(p.symbol)}${rc}</td>
          <td class="l b-flat" title="${esc(p.strategy_id)}">${esc(p.strategy_id)}</td>
          <td class="dir ${esc(p.side)}">${esc(p.side)}</td>
          <td class="num">${fmtUsd(p.size_usd, 0)}</td>
          <td class="num ${pn(p.upnl_usd)}">${fmtUsd(p.upnl_usd, 2)}</td>
          <td class="num ${pn(p.delta_pct)}">${(p.delta_pct >= 0 ? '+' : '') + (p.delta_pct || 0).toFixed(2)}%</td>
          <td class="num b-flat">${hms(p.held_sec)}</td>
          <td class="num b-flat">${(p.cell_mult || 1).toFixed(2)}×</td>
        </tr>`;
      }).join('');
      return head + trs;
    }).join('');
    $('pos-body').innerHTML =
      `<table class="tbl-pos"><colgroup>
        <col class="c-ven"><col class="c-sym"><col class="c-str"><col class="c-side">
        <col class="c-size"><col class="c-upnl"><col class="c-dlt"><col class="c-held"><col class="c-mult">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th class="l">SYMBOL</th><th class="l">STRAT</th><th>SIDE</th>
        <th>SIZE$</th><th>uPnL$</th><th>Δ%</th><th>HELD</th><th>MULT</th>
      </tr></thead><tbody>${body}</tbody></table>`;
  }

  function renderTrades(d) {
    const rows = (d.recent_trades || []).slice(0, 10);
    $('trd-cnt').textContent = rows.length;
    if (!rows.length) { $('trd-body').innerHTML = '<div class="empty">no recent trades</div>'; return; }
    const COLS = 8;
    const body = laneGroups(rows).map(g => {
      const s = g.stream, lc = s.toLowerCase();
      const head = `<tr><td colspan="${COLS}" class="lane-head lane-${lc}">${esc(STREAM_LABEL[s])}<span class="ln-cnt">· ${g.rows.length}</span></td></tr>`;
      const trs = g.rows.map(t => `<tr class="row-${lc}">
          <td class="l b-flat">${hhmmss(t.ts_close)}</td>
          <td class="l ex" title="${esc(t.venue)}">${esc(t.venue)}</td>
          <td class="l tk" title="${esc(t.symbol)}">${esc(t.symbol)}</td>
          <td class="l b-flat" title="${esc(t.strategy_id)}">${esc(t.strategy_id)}</td>
          <td class="dir ${esc(t.side_close)}">${esc(t.side_close)}</td>
          <td class="num ${pn(t.pnl_usd)}">${fmtUsd(t.pnl_usd, 2)}</td>
          <td class="num ${pn(t.r_units)}">${(t.r_units >= 0 ? '+' : '') + (t.r_units || 0).toFixed(2)}R</td>
          <td class="l b-flat" title="${esc(t.exit_reason)}">${esc(t.exit_reason)}</td>
        </tr>`).join('');
      return head + trs;
    }).join('');
    $('trd-body').innerHTML =
      `<table class="tbl-trd"><colgroup>
        <col class="c-time"><col class="c-ven"><col class="c-sym"><col class="c-str">
        <col class="c-side"><col class="c-pnl"><col class="c-r"><col class="c-rsn">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">VEN</th><th class="l">SYMBOL</th><th class="l">STRAT</th>
        <th>SIDE</th><th>PnL$</th><th>R</th><th class="l">REASON</th>
      </tr></thead><tbody>${body}</tbody></table>`;
  }

  function renderStrategies(d) {
    const rows = d.strategy_stats || [];
    if (!rows.length) { $('strat-body').innerHTML = '<div class="empty">—</div>'; return; }
    $('strat-body').innerHTML = rows.map(s => `
      <div class="row strat-row" title="${esc(s.strategy_id)} · ${s.open_n} open / ${s.closed_n} closed · WR ${(s.wr_pct||0).toFixed(1)}% · PF ${(s.pf||0).toFixed(2)}">
        <span class="name">${esc(s.strategy_id)} <span class="sub">${s.open_n}o/${s.closed_n}c</span></span>
        <span class="num b-flat">${(s.wr_pct || 0).toFixed(0)}%</span>
        <span class="num b-flat">pf${(s.pf || 0).toFixed(1)}</span>
        <span class="num ${pn(s.pnl_usd)}">${fmtUsd(s.pnl_usd, 2)}</span>
      </div>`).join('');
  }

  // 8-gate AI pipeline descriptions (hover tooltip). pass% = survival rate of
  // signals through that stage.
  const GATE_DESC = {
    1: 'G1 Universe — tradable-universe scan',
    2: 'G2 Strategy signal — raw signal generation',
    3: 'G3 Validator — main cut (signal validation)',
    4: 'G4 Pre-Entry — pre-entry watcher',
    5: 'G5 Sizer — entry sizing',
    6: 'G6 Position Monitor — open-position monitoring',
    7: 'G7 Adaptive Exit — exit decision',
    8: 'G8 Reflector — post-trade reflection',
  };

  function renderGates(d) {
    const rows = d.gate_funnel || [];
    if (!rows.length) { $('gate-body').innerHTML = '<div class="empty">—</div>'; return; }
    $('gate-body').innerHTML = rows.map(g => {
      const desc = GATE_DESC[g.gate_id] || (esc(g.label));
      const tip = `${desc}\npass ${g.pass_n}/${g.total} (${(g.pass_rate || 0).toFixed(0)}%) — signal survival rate at this stage`;
      return `
      <div class="row gate-row" title="${esc(tip)}">
        <span class="gid">G${g.gate_id}</span>
        <span class="gate-bar"><i style="width:${Math.max(0, Math.min(100, g.pass_rate || 0)).toFixed(0)}%"></i></span>
        <span class="pct b-flat">${(g.pass_rate || 0).toFixed(0)}%</span>
      </div>`;
    }).join('');
  }

  function renderCells(d) {
    const top = (d.cell_top || []).map(c => ({ ...c, side: 'top' }));
    const bot = (d.cell_bottom || []).map(c => ({ ...c, side: 'bot' }));
    const rows = top.concat(bot);
    if (!rows.length) {
      // Both top & bottom empty → cells still warming up (need n≥20 samples).
      $('cell-body').innerHTML =
        '<div class="empty">cold start · building<br><span class="b-flat">(need n≥20 samples)</span></div>';
      return;
    }
    $('cell-body').innerHTML = rows.map(c => {
      const cls = c.score > 0 ? 'b-pos' : c.score < 0 ? 'b-neg' : 'b-flat';
      return `<div class="row cell-row" title="${esc(c.exchange)}/${esc(c.strategy)}/${esc(c.regime)} n=${(c.n_eff||0).toFixed(0)}">
        <span class="name">${esc(c.ticker)} <span class="sub">${esc(c.strategy)}</span></span>
        <span class="num ${cls}">${(c.score || 0).toFixed(3)}</span>
        <span class="num b-flat">${(c.mult || 1).toFixed(1)}×</span>
      </div>`;
    }).join('');
  }

  function renderEdge(d) {
    const rows = d.edge_validation || [];
    if (!rows.length) { $('edge-body').innerHTML = '<div class="empty">—</div>'; return; }
    $('edge-body').innerHTML = rows.map(e => {
      const v = (e.verdict || '').toLowerCase();
      const cls = v.includes('anti') ? 'verdict-anti' : v.includes('edge') ? 'verdict-edge' : 'verdict-neutral';
      return `<div class="row edge-row" title="p+ ${(e.p_pos||0).toFixed(3)} n=${e.n_samples} ${esc(e.regime)}">
        <span class="name">${esc(e.ticker)} <span class="sub">${esc(e.strategy)}</span></span>
        <span class="num ${e.cost_adj_exp >= 0 ? 'b-pos' : 'b-neg'}">${(e.cost_adj_exp || 0).toFixed(0)}</span>
        <span class="vrd ${cls}" style="text-align:right;font-size:9px">${esc(e.verdict)}</span>
      </div>`;
    }).join('');
  }

  function renderLearners(d) {
    const rows = d.learners || [];
    if (!rows.length) { $('learn-body').innerHTML = '<div class="empty">—</div>'; return; }
    $('learn-body').innerHTML = rows.map(l => {
      const dl = l.delta_1h || 0;
      const arrow = dl > 0 ? '▲' : dl < 0 ? '▼' : '·';
      return `<div class="row learn-row" title="${esc(l.learner_id)} n=${(l.n_eff||0).toFixed(0)}">
        <span class="name">${esc(l.key)}</span>
        <span class="num b-flat">${(l.value || 0).toFixed(2)}</span>
        <span class="num ${pn(dl)}">${arrow}${Math.abs(dl).toFixed(2)}</span>
      </div>`;
    }).join('');
  }

  function renderAlerts(d) {
    const gpt = d.gpt_stats || [];
    const alerts = (d.alerts || []).slice(0, 4);
    let html = '';
    if (gpt.length) {
      // ok% surfaces silent GPT degradation (e.g. gpt-5.5 100% fail) that the
      // call-rate / cost columns alone would hide. <90% = warn, <50% = neg.
      html += gpt.map(g => {
        const ok = (g.ok_pct == null) ? 100 : g.ok_pct;
        // <50% ok = red, <90% = amber, else green — surfaces silent GPT
        // degradation the call-rate/cost columns alone would hide.
        const okStyle = ok < 50 ? 'color:var(--p-red);font-weight:700'
          : ok < 90 ? 'color:var(--p-ylw);font-weight:700' : 'color:var(--p-grn)';
        const errTip = (g.err_n || 0) > 0 ? ` (${g.err_n} err)` : '';
        return `<div class="row strat-row" style="grid-template-columns:1fr 44px 44px 50px"
          title="${esc(g.model)} ok ${ok.toFixed(0)}%${errTip} · ${(g.calls_per_h || 0).toFixed(0)}/h">
          <span class="name b-flat">${esc(g.model)}</span>
          <span class="num b-flat">${(g.calls_per_h || 0).toFixed(0)}/h</span>
          <span class="num" style="${okStyle}">${ok.toFixed(0)}%</span>
          <span class="num b-flat">${fmtUsd(g.cost_24h_proj_usd, 2)}</span>
        </div>`;
      }).join('');
    }
    if (alerts.length) {
      html += alerts.map(a => `<div class="row alert-row" title="[${esc(a.level)}] ${esc(a.module)} ${hhmmss(a.ts)} — ${esc(a.msg || '')}">
        <span class="lvl-${esc(a.level)}">${esc(a.level)}</span>
        <span class="name b-flat" style="font-weight:400">${esc(a.msg || '')}</span>
      </div>`).join('');
    }
    $('alert-body').innerHTML = html || '<div class="empty">no alerts</div>';
  }

  // ── poll loop ─────────────────────────────────────────────────────
  function render(d) {
    renderHeader(d);
    renderKpis(d);
    renderStreams(d);
    renderRotation(d);
    renderEquity(d);
    renderConfidence(d);
    renderPositions(d);
    renderTrades(d);
    renderStrategies(d);
    renderGates(d);
    renderCells(d);
    renderEdge(d);
    renderLearners(d);
    renderAlerts(d);
  }

  async function poll() {
    try {
      // cache-bust (?t=) + no-store → always-fresh, no stale cached frame
      const r = await fetch('/api/snapshot?t=' + Date.now(), { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (e) { /* keep last frame */ }
  }

  // ── Bot log (bottom-left pane) ────────────────────────────────────
  // ts (gray) + level/keyword coloring. Auto-scroll to bottom when new lines arrive.
  function classifyLog(line) {
    const l = line.toLowerCase();
    if (l.includes('[error]') || l.includes('exception') || l.includes('traceback')) return 'bl-err';
    if (l.includes('[warning]') || l.includes('rejected')) return 'bl-warn';
    if (l.includes('order resp ok=true') || /\bopen\b/.test(l) || /\[tick \d+\]/.test(l)) return 'bl-hi';
    return '';
  }
  function fmtLogLine(line) {
    // split leading ISO timestamp (gray) from the rest
    const m = line.match(/^(\S+T\S+Z?)\s+(.*)$/);
    const cls = classifyLog(line);
    if (m) {
      return `<div class="bl-line ${cls}"><span class="ts">${esc(m[1])}</span> ${esc(m[2])}</div>`;
    }
    return `<div class="bl-line ${cls}">${esc(line)}</div>`;
  }
  let _lastLogSig = '';
  async function pollLog() {
    const body = $('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const lines = d.lines || [];
      const sig = lines.length + '|' + (lines[lines.length - 1] || '');
      if (sig === _lastLogSig) return;            // no change → skip re-render
      _lastLogSig = sig;
      // auto-scroll only if user is already near the bottom (don't yank during manual scroll-up)
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
      body.innerHTML = lines.map(fmtLogLine).join('');
      if (atBottom) body.scrollTop = body.scrollHeight;
    } catch (e) { /* keep last frame */ }
  }

  // ── Sphere freeze watchdog ────────────────────────────────────────
  // sphere-render frame() bumps window.__sphereHB every frame. If visible (!hidden) but the
  // heartbeat hasn't moved for ~5s → nudge a resize (re-fit kick). If still frozen ~10s → reload.
  function startSphereWatchdog() {
    let lastHB = -1, stalledMs = 0, nudged = false;
    setInterval(function () {
      if (document.hidden) { stalledMs = 0; nudged = false; lastHB = window.__sphereHB || 0; return; }
      const hb = window.__sphereHB || 0;
      if (hb !== lastHB) {                  // healthy — loop advancing
        lastHB = hb; stalledMs = 0; nudged = false;
        return;
      }
      stalledMs += 1000;
      if (stalledMs >= 10000) {             // hard freeze → last resort full refresh
        location.reload();
      } else if (stalledMs >= 5000 && !nudged) {   // soft stall → re-fit kick
        nudged = true;
        window.dispatchEvent(new Event('resize'));
      }
    }, 1000);
  }

  function init() {
    injectStyle();
    const board = $('board');
    if (board) {
      board.innerHTML = skeleton();
      setInterval(() => { const c = $('b-clock'); if (c) c.textContent = clockStr(); }, 1000);
      poll();
      setInterval(poll, 1000);
    }
    // Bot log pane (left column bottom 1/3) — independent of #board presence
    if ($('botlog-body')) {
      pollLog();
      setInterval(pollLog, 1000);
    }
    startSphereWatchdog();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
