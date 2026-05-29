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
    /* header / kpis / equity (auto) + mid + bottom (bounded flex). minmax(0,..) keeps the
       sum inside 100vh; long lists scroll inside each panel's .p-body, never the page. */
    grid-template-rows: auto auto auto minmax(0, 1fr) minmax(0, 1.35fr);
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

    <div class="eq-wrap">
      <div class="eq-head">
        <span class="h-title">Equity Curve (1H)</span>
        <span>min <span class="v" id="eq-min">—</span></span>
        <span>max <span class="v" id="eq-max">—</span></span>
        <span>Δ <span class="v" id="eq-delta">—</span></span>
      </div>
      <svg id="eq-svg" viewBox="0 0 600 84" preserveAspectRatio="none"></svg>
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
      <div class="panel"><div class="p-head"><span>Gate Funnel</span></div><div class="p-body mini" id="gate-body"></div></div>
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
      { k: 'Net PnL (today)', v: fmtUsd(d.daily_pnl_usd, 2), cls: pn(d.daily_pnl_usd), sub: (d.daily_trades || 0) + ' trades' },
      { k: 'Equity', v: fmtUsd(d.equity_now, 0), cls: '', sub: 'start ' + fmtUsd(d.starting_capital, 0) },
      { k: 'Drawdown', v: '-' + fmtPct(d.drawdown_pct), cls: (d.drawdown_pct > 0 ? 'b-neg' : 'b-flat'), sub: 'peak ' + fmtUsd(d.peak_equity, 0) },
      { k: 'Sharpe 24h', v: (d.sharpe_24h == null ? '—' : d.sharpe_24h.toFixed(2)), cls: pn(d.sharpe_24h), sub: '' },
      { k: 'Win Rate', v: (wr == null ? '—' : fmtPct(wr, 1)), cls: '', sub: cn + ' closed' },
      { k: 'Exposure', v: fmtUsd(d.exposed_usd, 0), cls: '', sub: fmtPct(expPct, 1) + ' of eq · ' + (d.open_positions_n || 0) + ' pos' },
      { k: 'uPnL', v: fmtUsd(d.upnl_total, 2), cls: pn(d.upnl_total), sub: '' },
      { k: 'AI Cost 24h', v: fmtUsd(aiCost, 2), cls: '', sub: 'projected' },
    ];
    $('b-kpis').innerHTML = cards.map(c =>
      `<div class="kpi"><div class="k">${esc(c.k)}</div><div class="v num ${c.cls}">${c.v}</div><div class="sub">${esc(c.sub)}</div></div>`
    ).join('');
  }

  function renderEquity(d) {
    const svg = $('eq-svg');
    let curve = d.equity_curve || [];
    const ts = d.equity_curve_ts || [];
    // Frontend-only 1h slice: keep last 3600s by timestamp. Does NOT touch server/daily PnL/DD/Sharpe.
    if (ts.length === curve.length && curve.length > 1) {
      const ref = (d.ts_now || ts[ts.length - 1]);
      const cutoff = ref - 3600;
      let start = 0;
      for (let i = curve.length - 1; i >= 0; i--) {
        if (ts[i] < cutoff) { start = i + 1; break; }
      }
      if (start > 0 && start < curve.length) curve = curve.slice(start);
      // if <1h of data, start stays 0 → show all available
    }
    if (curve.length < 2) { svg.innerHTML = ''; return; }
    const W = 600, H = 84, pad = 2;
    const min = Math.min(...curve), max = Math.max(...curve);
    const span = (max - min) || 1;
    const n = curve.length;
    const x = i => pad + (i / (n - 1)) * (W - 2 * pad);
    const y = v => pad + (1 - (v - min) / span) * (H - 2 * pad);
    let line = '', area = `M ${x(0)} ${H} `;
    curve.forEach((v, i) => {
      const px = x(i).toFixed(1), py = y(v).toFixed(1);
      line += (i === 0 ? 'M' : 'L') + ' ' + px + ' ' + py + ' ';
      area += 'L ' + px + ' ' + py + ' ';
    });
    area += `L ${x(n - 1)} ${H} Z`;
    const last = curve[n - 1], first = curve[0];
    const up = last >= first;
    const stroke = up ? 'var(--p-grn)' : 'var(--p-red)';
    const fill = up ? 'rgba(135,215,135,0.12)' : 'rgba(215,135,135,0.12)';
    svg.innerHTML =
      `<path d="${area}" fill="${fill}" stroke="none"/>` +
      `<path d="${line}" fill="none" stroke="${stroke}" stroke-width="1.4" vector-effect="non-scaling-stroke"/>`;
    $('eq-min').textContent = fmtUsd(min, 0);
    $('eq-max').textContent = fmtUsd(max, 0);
    const dv = last - first, dp = first ? (dv / first) * 100 : 0;
    const de = $('eq-delta');
    de.textContent = fmtUsd(dv, 0) + ' (' + (dp >= 0 ? '+' : '') + dp.toFixed(2) + '%)';
    de.className = 'v ' + pn(dv);
  }

  function renderPositions(d) {
    const rows = d.positions || [];
    $('pos-cnt').textContent = rows.length;
    if (!rows.length) { $('pos-body').innerHTML = '<div class="empty">no open positions</div>'; return; }
    const body = rows.map(p => {
      const rc = (p.row_count > 1) ? ` <span class="b-flat">×${p.row_count}</span>` : '';
      return `<tr>
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
    const body = rows.map(t => `<tr>
        <td class="l b-flat">${hhmmss(t.ts_close)}</td>
        <td class="l ex" title="${esc(t.venue)}">${esc(t.venue)}</td>
        <td class="l tk" title="${esc(t.symbol)}">${esc(t.symbol)}</td>
        <td class="l b-flat" title="${esc(t.strategy_id)}">${esc(t.strategy_id)}</td>
        <td class="dir ${esc(t.side_close)}">${esc(t.side_close)}</td>
        <td class="num ${pn(t.pnl_usd)}">${fmtUsd(t.pnl_usd, 2)}</td>
        <td class="num ${pn(t.r_units)}">${(t.r_units >= 0 ? '+' : '') + (t.r_units || 0).toFixed(2)}R</td>
        <td class="l b-flat" title="${esc(t.exit_reason)}">${esc(t.exit_reason)}</td>
      </tr>`).join('');
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

  function renderGates(d) {
    const rows = d.gate_funnel || [];
    if (!rows.length) { $('gate-body').innerHTML = '<div class="empty">—</div>'; return; }
    $('gate-body').innerHTML = rows.map(g => `
      <div class="row gate-row" title="${esc(g.label)} pass ${g.pass_n}/${g.total}">
        <span class="gid">G${g.gate_id}</span>
        <span class="gate-bar"><i style="width:${Math.max(0, Math.min(100, g.pass_rate || 0)).toFixed(0)}%"></i></span>
        <span class="pct b-flat">${(g.pass_rate || 0).toFixed(0)}%</span>
      </div>`).join('');
  }

  function renderCells(d) {
    const top = (d.cell_top || []).map(c => ({ ...c, side: 'top' }));
    const bot = (d.cell_bottom || []).map(c => ({ ...c, side: 'bot' }));
    const rows = top.concat(bot);
    if (!rows.length) { $('cell-body').innerHTML = '<div class="empty">—</div>'; return; }
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
    renderEquity(d);
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
