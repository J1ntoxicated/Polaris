/* board_tabs.js — Polaris E2 Trading-IA full-width tab renderers (display-only).
 *
 * The 8 tabs below the always-visible 3-stream exchange summary (Jin 2026-05-31
 * IA rebuild). Each tab is full-width + richly columned. Loaded AFTER board.js;
 * consumes the shared helpers on ``window.PolarisBoard`` and exposes
 * ``window.PolarisBoardTabs`` = { paneMarkup, renderTab, renderTabCounts }.
 *
 * Pure DOM render off the /api/snapshot frame — no fetch, no sizing/order/exit
 * logic, no snapshot mutation. Tabs: POSITIONS / TRADES / REGIME / STRATEGY /
 * EXIT / AI / EDGE / RISK.
 */
(function () {
  'use strict';

  const B = window.PolarisBoard;
  if (!B) { return; }   // board.js must load first
  const { $, fmtUsd, fmtPct, fmtSignedPct, fmtPx, fmtR, pn, esc, hms, hhmmss,
    venueStream, laneGroups, STREAM_LABEL, STREAM_TAGLINE,
    getActiveExchange, venueFilter } = B;

  // E3 (Jin 2026-05-31): when an exchange is selected (not 'all'), each tab
  // scopes its venue-carrying rows to that venue (client-side filter; no data
  // change). A small inline note is appended to a scoped panel head; tabs whose
  // data carries no venue render with an 'all venues' note rather than a faked
  // per-venue split. ``scopeLabel`` = uppercase venue name or '' on ALL.
  function scopeLabel() {
    const ex = getActiveExchange();
    return ex === 'all' ? '' : ex.toUpperCase();
  }

  // ── tab CSS (panel shell + tables + lane heads + inner layouts) ───────────
  // These styles back the tab CONTENT only; the always-visible header / KPIs /
  // streams strip / tab strip live in board.js. The --stream-* color tokens are
  // defined on #board in board.js (shared with the streams strip).
  const CSS = `
  /* Panel shell (reused by every tab). */
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

  /* Tables (reused by every tab). */
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
  #board tbody td.dir.long, #board tbody td.dir.buy { color: var(--p-grn); font-weight: 700; }
  #board tbody td.dir.short, #board tbody td.dir.sell { color: var(--p-red); font-weight: 700; }
  #board tbody tr:hover td { background: rgba(95,135,175,0.06); }
  #board .empty { color: var(--p-dim); padding: 8px; text-align: center; font-size: 10px; }
  #board .stack-badge { cursor: help; border-bottom: 1px dotted var(--p-dim); }

  /* CURRENT-price flash (POSITIONS tab) — tint on value change each 1s poll. */
  #board td.px-flash-up   { animation: pxup 0.9s ease-out; }
  #board td.px-flash-down { animation: pxdn 0.9s ease-out; }
  @keyframes pxup { 0% { background: rgba(135,215,135,0.45); } 100% { background: transparent; } }
  @keyframes pxdn { 0% { background: rgba(215,135,135,0.45); } 100% { background: transparent; } }

  /* Per-row lane heads + lane left-borders (grouped POSITIONS / TRADES tables). */
  #board td.lane-head {
    padding: 2px 7px 2px 6px; border-left: 4px solid var(--ghost);
    border-bottom: 1px solid var(--ghost);
    font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: var(--p-dim); background: rgba(15,19,26,0.55);
  }
  #board td.lane-head .ln-cnt { color: var(--p-gry); letter-spacing: 0; font-weight: 400; margin-left: 5px; }
  #board td.lane-head .ln-tag { color: var(--p-dim); letter-spacing: 0.06em; font-weight: 400; font-size: 8px; margin-left: 7px; text-transform: none; }
  #board td.lane-head.lane-a { border-left-color: var(--stream-a); color: var(--stream-a); }
  #board td.lane-head.lane-b { border-left-color: var(--stream-b); color: var(--stream-b); }
  #board td.lane-head.lane-c { border-left-color: var(--stream-c); color: var(--stream-c); }
  #board tbody tr.row-a td:first-child { border-left: 3px solid var(--stream-a); }
  #board tbody tr.row-b td:first-child { border-left: 3px solid var(--stream-b); }
  #board tbody tr.row-c td:first-child { border-left: 3px solid var(--stream-c); }
  #board tbody tr.row-x td:first-child { border-left: 3px solid var(--ghost); }

  /* Two-up / three-up panel grids used by several tabs. */
  #board .tab-grid-1 { display: grid; grid-template-rows: minmax(0,1fr); gap: 8px; min-height: 0; }
  #board .tab-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; min-height: 0; }
  #board .tab-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; min-height: 0; }
  #board .tab-rows-2 { display: grid; grid-template-rows: auto minmax(0,1fr); gap: 8px; min-height: 0; }

  /* mini key/value rows (REGIME / STRATEGY / EXIT / AI / EDGE / RISK panels). */
  #board .mini { font-size: 11px; }
  #board .mini .row {
    display: grid; align-items: center; gap: 8px; padding: 2px 10px;
    border-bottom: 1px dotted rgba(95,135,175,0.08);
  }
  #board .mini .row > * { min-width: 0; }
  #board .mini .row .name { color: var(--p-wht); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .num { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .sub { color: var(--p-dim); font-size: 9px; }

  /* horizontal bar (regime confidence / agreement / gate funnel). */
  #board .hbar { height: 8px; background: rgba(95,135,175,0.12); position: relative; }
  #board .hbar > i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--p-cyn); }
  #board .hbar.bar-pos > i { background: var(--p-grn); }
  #board .hbar.bar-neg > i { background: var(--p-red); }
  #board .hbar.bar-warn > i { background: var(--p-ylw); }

  /* chips (FSM states / exit reasons / evidence layers). */
  #board .chips { display: flex; flex-wrap: wrap; gap: 5px; padding: 6px 10px; }
  #board .chip {
    border: 1px solid rgba(95,135,175,0.30); background: rgba(15,19,26,0.6);
    padding: 2px 8px; font-size: 10px; white-space: nowrap;
    display: inline-flex; align-items: center; gap: 5px;
  }
  #board .chip .cn { color: var(--p-cyn); font-weight: 700; font-variant-numeric: tabular-nums; }
  #board .chip .cl { color: var(--p-gry); letter-spacing: 0.06em; }
  #board .chip.ev-l1 { border-left: 3px solid #b48cff; }
  #board .chip.ev-l2 { border-left: 3px solid #ffaa55; }
  #board .chip.ev-l3 { border-left: 3px solid #87ffd7; }

  /* equity chart (EDGE tab) — reused from the legacy board. */
  #board .eq-wrap {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.40);
    padding: 5px 10px 3px;
  }
  #board .eq-wrap .eq-head {
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
    font-size: 10px; letter-spacing: 0.12em; color: var(--p-dim); text-transform: uppercase;
  }
  #board .eq-wrap .eq-head .h-title { color: var(--polaris-blue); font-weight: 700; }
  #board .eq-wrap .eq-head .v { color: var(--p-wht); font-weight: 700; }
  #board .eq-wrap svg { display: block; width: 100%; height: 90px; }
  #board .conf-strip {
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px;
    margin-top: 3px; padding-top: 3px; border-top: 1px dotted rgba(95,135,175,0.14);
    font-size: 9.5px; letter-spacing: 0.04em; color: var(--p-dim);
  }
  #board .conf-strip .ck { color: var(--p-dim); text-transform: uppercase; letter-spacing: 0.10em; }
  #board .conf-strip .cv { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell { display: inline-flex; align-items: center; gap: 4px; padding: 0 5px; border-left: 2px solid var(--ghost); }
  #board .conf-strip .cell.lcb-pos { border-left-color: var(--p-grn); }
  #board .conf-strip .cell.lcb-neg { border-left-color: var(--p-red); }
  #board .conf-strip .cell .nm { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell .rg { color: var(--p-dim); }

  /* rotation telemetry strip (RISK tab). */
  #board .rot-strip {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border: 1px solid rgba(95,135,175,0.22); border-left: 4px solid var(--p-mag);
    background: rgba(15,19,26,0.55); padding: 4px 10px; font-size: 10px;
    min-width: 0; overflow: hidden;
  }
  #board .rot-strip .rt-title { font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--p-mag); }
  #board .rot-strip .rt-kv { white-space: nowrap; }
  #board .rot-strip .rt-kv .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .rot-strip .rt-kv .lv { font-variant-numeric: tabular-nums; color: var(--p-wht); font-weight: 700; }
  #board .rot-strip .rt-last { color: var(--p-gry); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  #board .rot-strip .rt-last b { color: var(--p-cyn); }
  #board .rot-strip .rt-none { color: var(--p-dim); }

  #board .lvl-ERROR, #board .lvl-CRITICAL { color: var(--p-red); font-weight: 700; }
  #board .lvl-WARN, #board .lvl-WARNING { color: var(--p-ylw); }
  #board .lvl-INFO { color: var(--p-cyn); }
  #board .verdict-edge { color: var(--p-grn); }
  #board .verdict-anti { color: var(--p-red); }
  #board .verdict-neutral { color: var(--p-ylw); }
  `;

  function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-tabs-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // Single full-width panel helper.
  function panel(title, bodyId, bodyCls) {
    return `<div class="panel"><div class="p-head"><span>${esc(title)}</span>`
      + `<span class="cnt" id="${bodyId}-cnt"></span></div>`
      + `<div class="p-body ${bodyCls || ''}" id="${bodyId}"></div></div>`;
  }

  // ── pane markup (called once by board.js skeleton) ────────────────────────
  function paneMarkup() {
    return `
    <div class="tab-pane active" id="pane-positions">
      <div class="tab-grid-1">${panel('Open Positions · 3 Streams', 'pos-body')}</div>
    </div>
    <div class="tab-pane" id="pane-trades">
      <div class="tab-grid-1">${panel('Recent Trades', 'trd-body')}</div>
    </div>
    <div class="tab-pane" id="pane-regime">
      <div class="tab-grid-1">${panel('Regime · venue × asset-group (L1 macro / L2 asset / L3 price-action)', 'regime-body')}</div>
    </div>
    <div class="tab-pane" id="pane-strategy">
      <div class="tab-grid-1">${panel('Per-Strategy · signals / win-rate / real-fee-net R · per-regime cell EV', 'strat-body')}</div>
    </div>
    <div class="tab-pane" id="pane-exit">
      <div class="tab-rows-2">
        <div class="chips" id="exit-fsm"></div>
        <div class="tab-grid-2">
          ${panel('Exit Reasons · histogram', 'exit-reasons', 'mini')}
          ${panel('Exit Gates · G6 Monitor / G7 Adaptive', 'exit-gates', 'mini')}
        </div>
      </div>
    </div>
    <div class="tab-pane" id="pane-ai">
      <div class="tab-grid-2">
        ${panel('Per-Gate GPT · calls/h · tokens · real cost · ok%', 'ai-gpt', 'mini')}
        ${panel('Conductor Shadow · technical vs GPT agreement (by gate × regime)', 'ai-shadow', 'mini')}
      </div>
    </div>
    <div class="tab-pane" id="pane-edge">
      <div class="tab-rows-2">
        <div class="eq-wrap">
          <div class="eq-head">
            <span class="h-title" title="Headline = REAL-FEE-NET (real OKX 0.10% taker). Dimmed line = demo-actual (0.7% demo drain). Go-live trigger = the real-fee-net curve trending UP.">Equity · REAL-FEE-NET (go-live)</span>
            <span>real Δ <span class="v" id="eq-real-delta">—</span></span>
            <span class="b-flat">demo Δ <span class="v" id="eq-demo-delta">—</span></span>
            <span title="Real-vs-demo fee wedge over the session (demo OKX is a 7x penalty vs real).">fee wedge <span class="v" id="eq-fee-wedge">—</span></span>
          </div>
          <svg id="eq-svg" viewBox="0 0 600 90" preserveAspectRatio="none"></svg>
          <div class="conf-strip" id="b-confidence"></div>
          <div class="conf-strip" id="b-benchmark" title="Offline deterministic replay benchmark (real OKX fee, baseline clock). 3-tier gate: relative / risk-adjusted / statistical. Edge significance on held-out bars — NOT a calendar gate."></div>
        </div>
        <div class="tab-grid-1">${panel('Edge Validation · per (strategy × ticker × regime) posterior', 'edge-body', 'mini')}</div>
      </div>
    </div>
    <div class="tab-pane" id="pane-risk">
      <div class="tab-rows-2">
        <div class="rot-strip" id="b-rotation"></div>
        <div class="tab-grid-3">
          ${panel('Cell Matrix · top / bottom', 'cell-body', 'mini')}
          ${panel('Alerts · Halts', 'alert-body', 'mini')}
          ${panel('Admission Shadow · would-suppress (edge-first, net real fee)', 'risk-admit', 'mini')}
        </div>
      </div>
    </div>`;
  }

  // ── tab counts (header badges per tab) ────────────────────────────────────
  function setCnt(id, val) { const el = $(id); if (el) el.textContent = (val === '' || val == null) ? '' : ('· ' + val); }
  function renderTabCounts(d) {
    // Counts reflect the active-exchange scope (venue-carrying tabs).
    setCnt('tabcnt-positions', venueFilter(d.positions).length);
    setCnt('tabcnt-trades', venueFilter(d.recent_trades).length);
    setCnt('tabcnt-regime', venueFilter(d.regime_states).length);
    setCnt('tabcnt-strategy', (d.strategy_stats || []).length);
    const surf = d.exit_surface || {};
    const fsm = surf.fsm_states || {};
    setCnt('tabcnt-exit', Object.values(fsm).reduce((a, b) => a + b, 0));
    const ai = d.ai_shadow || {};
    setCnt('tabcnt-ai', (ai.shadow_agreement || []).length);
    setCnt('tabcnt-edge', (d.edge_validation || []).length);
    setCnt('tabcnt-risk', (d.alerts || []).length);
  }

  // ── TAB 1 · POSITIONS (expanded columns, flashing CURRENT price) ──────────
  const POS_COLS = 14;
  const _lastPx = {};   // (venue|symbol|strat|side) → last CURRENT price for flash
  function renderPositions(d) {
    const rows = venueFilter(d.positions);   // E3 venue scope
    const body = $('pos-body'); if (!body) return;
    setCnt('pos-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no open positions${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    // MIXED list (Jin 2026-06-01): no per-exchange grouping/lane-heads — all
    // positions in ONE table, sorted by size so venues interleave. The per-row
    // left-border colour (row-{a/b/c}) stays only as a subtle venue cue; the VEN
    // column already names the venue. (The streams-strip on top keeps per-venue
    // summary.)
    const groups = rows.slice().sort(
      (a, b) => (b.size_usd || 0) - (a.size_usd || 0)
    ).map(p => {
      const lc = venueStream(p.venue).toLowerCase();
      const key = [p.venue, p.symbol, p.strategy_id, p.side].join('|');
      const prev = _lastPx[key];
      let flash = '';
      if (prev != null && p.last_price != null && p.last_price !== prev) {
        flash = p.last_price > prev ? ' px-flash-up' : ' px-flash-down';
      }
      _lastPx[key] = p.last_price;
      const rc = (p.row_count > 1)
        ? ` <span class="b-flat stack-badge" title="${p.row_count} stacked positions on this (symbol, strategy, side); SIZE$ is the aggregate">×${p.row_count}</span>`
        : '';
      const dpc = fmtSignedPct(p.delta_pct, 2);
      const upnlPct = fmtSignedPct(p.upnl_pct, 2);
      const stop = (p.stop_price > 0) ? fmtPx(p.stop_price) : '—';
      const mfeMae = `${fmtR(p.mfe_r, 1)}/${fmtR(p.mae_r, 1)}`;
      return `<tr class="row-${lc}">
        <td class="l ex" title="${esc(p.venue)}">${esc(p.venue)}</td>
        <td class="l tk" title="${esc(p.symbol)}${p.row_count > 1 ? ' ×' + p.row_count : ''}">${esc(p.symbol)}${rc}</td>
        <td class="dir ${esc(p.side)}" title="${esc(p.side)}${lc === 'b' ? ' (CFD — long/short)' : ''}">${esc(p.side)}</td>
        <td class="num b-flat" title="entry ${fmtPx(p.entry_price)}">${fmtPx(p.entry_price)}</td>
        <td class="num${flash}" title="current (last close) ${fmtPx(p.last_price)}">${fmtPx(p.last_price)}</td>
        <td class="num ${pn(p.delta_pct)}" title="price move since entry">${dpc}</td>
        <td class="num">${fmtUsd(p.size_usd, 0)}</td>
        <td class="num ${pn(p.upnl_usd)}">${fmtUsd(p.upnl_usd, 2)}</td>
        <td class="num ${pn(p.upnl_pct)}">${upnlPct}</td>
        <td class="num b-flat" title="time held">${hms(p.held_sec)}</td>
        <td class="l b-flat" title="${esc(p.strategy_id)}">${esc(p.strategy_id)}</td>
        <td class="l b-flat" title="regime ${esc(p.regime)}">${esc(p.regime || '—')}</td>
        <td class="num b-flat" title="protective stop ${stop}">${stop}</td>
        <td class="num b-flat" title="MFE/MAE in R · exit-FSM ${esc(p.exit_state)}">${mfeMae}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:6%"><col style="width:13%"><col style="width:6%"><col style="width:9%">
        <col style="width:9%"><col style="width:6%"><col style="width:7%"><col style="width:8%">
        <col style="width:6%"><col style="width:6%"><col style="width:9%"><col style="width:7%">
        <col style="width:8%"><col style="width:8%">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th class="l">SYMBOL</th><th>SIDE</th><th>ENTRY</th>
        <th>CURRENT</th><th>Δ%</th><th>SIZE$</th><th>uPnL$</th>
        <th>uPnL%</th><th>HELD</th><th class="l">STRAT</th><th class="l">REGIME</th>
        <th>STOP</th><th title="max-favorable / max-adverse excursion in R">MFE/MAE</th>
      </tr></thead><tbody>${groups}</tbody></table>`;
  }

  // ── TAB 2 · TRADES (expanded, more rows) ──────────────────────────────────
  const TRD_COLS = 12;
  function renderTrades(d) {
    const rows = venueFilter(d.recent_trades).slice(0, 40);   // E3 venue scope
    const body = $('trd-body'); if (!body) return;
    setCnt('trd-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no recent trades${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    // MIXED (Jin 2026-06-01): no per-exchange grouping — recent trades in ONE
    // time-ordered list (newest first); per-row colour is a subtle venue cue only.
    const groups = rows.map(t => {
      const lc = venueStream(t.venue).toLowerCase();
      return `<tr class="row-${lc}">
          <td class="l b-flat">${hhmmss(t.ts_close)}</td>
          <td class="l ex" title="${esc(t.venue)}">${esc(t.venue)}</td>
          <td class="l tk" title="${esc(t.symbol)}">${esc(t.symbol)}</td>
          <td class="dir ${esc(t.side_close)}">${esc(t.side_close)}</td>
          <td class="l b-flat" title="${esc(t.strategy_id)}">${esc(t.strategy_id)}</td>
          <td class="l b-flat" title="regime ${esc(t.regime)}">${esc(t.regime || '—')}</td>
          <td class="num b-flat" title="entry ${fmtPx(t.entry_price)}">${fmtPx(t.entry_price)}</td>
          <td class="num b-flat" title="exit ${fmtPx(t.exit_price)}">${fmtPx(t.exit_price)}</td>
          <td class="num ${pn(t.pnl_usd)}">${fmtUsd(t.pnl_usd, 2)}</td>
          <td class="num ${pn(t.pnl_pct)}">${fmtSignedPct(t.pnl_pct, 2)}</td>
          <td class="num b-flat">${hms(t.held_sec)}</td>
          <td class="l b-flat" title="exit ${esc(t.exit_reason)} · fee real ${fmtUsd(t.real_fee_usd, 4)} | demo ${fmtUsd(t.fee_usd, 4)}">${esc(t.exit_reason)} <span class="b-flat">${fmtUsd(t.real_fee_usd, 2)}|${fmtUsd(t.fee_usd, 2)}</span></td>
        </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:8%"><col style="width:6%"><col style="width:12%"><col style="width:6%">
        <col style="width:11%"><col style="width:9%"><col style="width:9%"><col style="width:9%">
        <col style="width:8%"><col style="width:6%"><col style="width:6%"><col style="width:14%">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">VEN</th><th class="l">SYMBOL</th><th>SIDE</th>
        <th class="l">STRAT</th><th class="l">REGIME</th><th>ENTRY</th><th>EXIT</th>
        <th>PnL$</th><th>PnL%</th><th>HELD</th><th class="l" title="exit reason + fee(real|demo)">REASON · FEE(R|D)</th>
      </tr></thead><tbody>${groups}</tbody></table>`;
  }

  // ── TAB 3 · REGIME ────────────────────────────────────────────────────────
  const REGIME_COLS = 8;
  function renderRegime(d) {
    const rows = venueFilter(d.regime_states);   // E3 venue scope
    const body = $('regime-body'); if (!body) return;
    setCnt('regime-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no regime state yet${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    const trs = rows.map(r => {
      const lc = venueStream(r.venue).toLowerCase();
      const confPct = Math.max(0, Math.min(100, (r.confidence || 0) * 100));
      const barCls = confPct >= 66 ? 'bar-pos' : confPct >= 33 ? 'bar-warn' : 'bar-neg';
      const ev = [];
      if (r.evidence_l1) ev.push(`<span class="chip ev-l1"><span class="cl">L1</span> ${esc(r.evidence_l1)}</span>`);
      if (r.evidence_l2) ev.push(`<span class="chip ev-l2"><span class="cl">L2</span> ${esc(r.evidence_l2)}</span>`);
      if (r.evidence_l3) ev.push(`<span class="chip ev-l3"><span class="cl">L3</span> ${esc(r.evidence_l3)}</span>`);
      const evHtml = ev.length ? ev.join(' ') : '<span class="b-flat">—</span>';
      const cons = (r.consecutive_candidate && r.consecutive_count)
        ? `${esc(r.consecutive_candidate)} ×${r.consecutive_count}` : '—';
      return `<tr class="row-${lc}">
        <td class="l ex">${esc(r.venue)}</td>
        <td class="l tk" title="${esc(r.group_id)}">${esc(r.group_id)}</td>
        <td class="l" style="color:var(--p-mag);font-weight:700">${esc((r.regime || '').replace(/_/g, ' '))}</td>
        <td class="num">${confPct.toFixed(0)}%</td>
        <td><span class="hbar ${barCls}"><i style="width:${confPct.toFixed(0)}%"></i></span></td>
        <td class="l">${evHtml}</td>
        <td class="l b-flat" title="2-consecutive hysteresis candidate">${cons}</td>
        <td class="num b-flat">${r.updated_ts ? hhmmss(r.updated_ts) : '—'}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:7%"><col style="width:11%"><col style="width:13%"><col style="width:6%">
        <col style="width:14%"><col style="width:31%"><col style="width:12%"><col style="width:6%">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th class="l">GROUP</th><th class="l">REGIME</th><th>CONF</th>
        <th class="l">—</th><th class="l">EVIDENCE (L1·L2·L3)</th><th class="l">2-CONSEC</th><th>UPD</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── TAB 4 · STRATEGY ──────────────────────────────────────────────────────
  function renderStrategy(d) {
    // StrategyStat carries no venue — genuinely global. When scoped, show an
    // 'all venues' note rather than fabricating a per-venue split.
    const rows = d.strategy_stats || [];
    const body = $('strat-body'); if (!body) return;
    setCnt('strat-body-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">no strategy stats</div>'; return; }
    const sc = scopeLabel();
    const note = sc ? `<div class="empty" style="text-align:left;color:var(--p-dim)">scope ${esc(sc)} — strategy stats are cross-venue (all venues)</div>` : '';
    // per-(strategy×regime) confidence cells for the EV column.
    const cells = (d.confidence && d.confidence.cells) || [];
    const cellsByStrat = {};
    cells.forEach(c => { (cellsByStrat[c.strategy_id] = cellsByStrat[c.strategy_id] || []).push(c); });
    const trs = rows.map(s => {
      const cs = cellsByStrat[s.strategy_id] || [];
      const evHtml = cs.length ? cs.slice(0, 4).map(c => {
        const lcb = c.lcb_real_fee_net_r || 0;
        const cls = lcb > 0 ? 'b-pos' : lcb < 0 ? 'b-neg' : 'b-flat';
        return `<span class="chip" title="${esc(c.regime)} n=${c.n} E[R]=${(c.expected_real_fee_net_r||0).toFixed(2)} LCB=${lcb.toFixed(2)}">`
          + `<span class="cl">${esc(c.regime)}</span> <span class="${cls}">${c.lcb_sign}${Math.abs(lcb).toFixed(2)}R</span></span>`;
      }).join(' ') : '<span class="b-flat">—</span>';
      const pf = (s.pf >= 9.99) ? '∞' : (s.pf || 0).toFixed(2);
      return `<tr>
        <td class="l tk" title="${esc(s.strategy_id)}">${esc(s.strategy_id)}</td>
        <td class="num b-flat">${s.open_n || 0}</td>
        <td class="num b-flat">${s.closed_n || 0}</td>
        <td class="num">${(s.wr_pct || 0).toFixed(1)}%</td>
        <td class="num b-flat">${pf}</td>
        <td class="num ${pn(s.avg_r)}">${fmtR(s.avg_r, 2)}</td>
        <td class="num ${pn(s.pnl_usd)}">${fmtUsd(s.pnl_usd, 2)}</td>
        <td class="num b-flat">${fmtUsd(s.notional_usd, 0)}</td>
        <td class="l" title="per-regime real-fee-net LCB cells">${evHtml}</td>
      </tr>`;
    }).join('');
    body.innerHTML = note +
      `<table><colgroup>
        <col style="width:14%"><col style="width:6%"><col style="width:7%"><col style="width:7%">
        <col style="width:6%"><col style="width:8%"><col style="width:10%"><col style="width:10%">
        <col style="width:32%">
       </colgroup><thead><tr>
        <th class="l">STRATEGY</th><th>OPEN</th><th>CLOSED</th><th>WR</th>
        <th>PF</th><th>AVG-R</th><th>PnL$</th><th>NOTIONAL</th>
        <th class="l" title="per-regime cell expectancy — real-fee-net LCB (+EV / -EV)">REGIME CELL EV</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }


  // ── dispatch registry ─────────────────────────────────────────────────────
  // board_tabs.js owns the table tabs; board_tabs_ext.js registers the analytics
  // tabs (EXIT / AI / EDGE / RISK) via ``register`` so each module stays within
  // the LOC guideline. Unregistered tabs are a graceful no-op.
  const RENDERERS = {
    positions: renderPositions,
    trades: renderTrades,
    regime: renderRegime,
    strategy: renderStrategy,
  };
  function register(which, fn) { RENDERERS[which] = fn; }
  function renderTab(which, d) {
    const fn = RENDERERS[which];
    if (fn) fn(d);
  }

  injectStyle();
  window.PolarisBoardTabs = {
    paneMarkup: paneMarkup,
    renderTab: renderTab,
    renderTabCounts: renderTabCounts,
    register: register,
    setCnt: setCnt,
  };
})();
