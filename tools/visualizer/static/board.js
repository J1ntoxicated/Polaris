/* board.js — Polaris right-half analytics board core + left-column bot log
 * (DEMO/PAPER display-only). Native HTML/CSS. 1s polling: /api/snapshot →
 * #board, /api/botlog → #botlog-body. All fetches cache-busted (?t=Date.now())
 * + no-store. No ANSI mirror. No sizing/order logic.
 *
 * E2 Trading-IA rebuild (Jin 2026-05-31): the right pane is HEADER (KPIs) +
 * 3-STREAM EXCHANGE SUMMARY (always visible) + a FULL-WIDTH TAB STRIP below
 * (POSITIONS / TRADES / REGIME / STRATEGY / EXIT / AI / EDGE / RISK). NO
 * side-by-side positions/trades. The per-tab renderers + their CSS live in
 * board_tabs.js (loaded after this file); shared helpers + the active-tab
 * gate are exposed on ``window.PolarisBoard`` so the split modules cooperate.
 */
(function () {
  'use strict';

  // ── Style injection (core shell + header + exchange summary + tab strip) ──
  const CSS = `
  #board {
    height: 100vh; min-height: 0; overflow: hidden;
    display: grid;
    /* 6 rows: head + KPIs + exchange selector + summary + tab strip + the active
       tab pane (the only 1fr — hidden display:none panes take no grid space).
       MUST be 6 tracks or the tab strip eats the 1fr and the pane spills to an
       implicit bottom row. Each pane owns its layout; lists scroll in .p-body. */
    grid-template-rows: auto auto auto auto auto minmax(0, 1fr);
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
  /* (k) Polaris star — bigger; LIVE = soft twinkle/glow shimmer, STALE = dim/calm.
     Glow built only from existing --polaris-blue token alpha (no new colours). */
  #board .b-head .star { color: var(--polaris-blue); font-size: 21px; line-height: 1;
    display: inline-block; transition: opacity .6s ease, text-shadow .6s ease; }
  #board .b-head .star.live { animation: starTwinkle 2.4s ease-in-out infinite; }
  #board .b-head .star.stale { opacity: 0.4; text-shadow: none; }
  @keyframes starTwinkle {
    0%,100% { opacity: 1;    text-shadow: 0 0 5px rgba(95,135,175,0.55), 0 0 11px rgba(95,135,175,0.30); transform: scale(1); }
    50%     { opacity: 0.78; text-shadow: 0 0 13px rgba(95,135,175,1), 0 0 26px rgba(95,135,175,0.55); transform: scale(1.10); }
  }
  #board .b-head .badge {
    font-size: 10px; letter-spacing: 0.18em; font-weight: 700;
    padding: 2px 8px; border: 1px solid var(--polaris-blue); color: var(--polaris-blue);
  }
  #board .b-head .meta { color: var(--p-gry); font-size: 11px; }
  #board .b-head .meta b { color: var(--p-wht); font-weight: 700; }
  #board .b-head .clock { margin-left: auto; color: var(--p-cyn); font-weight: 700; font-size: 14px; }
  #board .b-head .regime-lbl { color: var(--p-gry); font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; margin-right: 5px; }
  #board .b-head .regime-tag { color: var(--p-mag); font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; font-size: 11px; }
  #board .b-footer { color: var(--p-dim); font-size: 9px; letter-spacing: 0.04em; text-align: right; padding: 3px 8px; opacity: 0.6; }

  /* KPI cards — header strip, all in one row */
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
  /* KPI headline labels (clean English money headline — 2026-06-22). */
  #board #b-kpis .kk { color: var(--p-dim); font-size: 10px; letter-spacing: 0.06em;
    text-transform: uppercase; }
  /* (j) HEALTH one-liner — at-a-glance status above the money headline.
     state-colour comes from existing pnl tokens; no new colours. */
  #board #b-kpis .health {
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 1px 2px 3px; font-size: 11px; border-bottom: 1px dotted rgba(95,135,175,0.14);
  }
  #board #b-kpis .health .hl { font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase; }
  #board #b-kpis .health .dot { font-size: 9px; margin-right: 4px; }
  #board #b-kpis .health .anom { color: var(--p-ylw); }
  #board #b-kpis .health .anom.clear { color: var(--p-grn); }
  /* (f) main-area dual equity sparkline — axis-less Tufte; sits in the KPI block. */
  #board #b-kpis .eq-spark { display: flex; align-items: center; gap: 10px; }
  #board #b-kpis .eq-spark svg { display: block; width: 200px; height: 30px; flex: 0 0 auto; }
  #board #b-kpis .eq-spark .lg { font-size: 9px; color: var(--p-dim); letter-spacing: 0.04em;
    text-transform: uppercase; white-space: nowrap; }
  #board #b-kpis .eq-spark .lg .v { font-weight: 700; font-variant-numeric: tabular-nums; }
  /* (j-iii) anomaly strip — only things to watch, else 'all clear'. */
  #board #b-kpis .anoms { display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
    font-size: 10px; color: var(--p-ylw); }
  #board #b-kpis .anoms .ax { white-space: nowrap; }
  #board #b-kpis .anoms .ax::before { content: '⚠ '; }
  #board #b-kpis .anoms.clear { color: var(--p-grn); }
  #board #b-kpis .anoms.clear .ax::before { content: '✓ '; }

  /* 3-stream exchange summary strip — ALWAYS visible (Jin: stays on top).
     Bloomberg de-card (2026-06-22): one dense tabular ROW per venue, no card
     chrome. server-fed d.streams. E3 row click→scope + highlight: board_exchange.js. */
  #board .streams-strip {
    border: 1px solid rgba(95,135,175,0.22); background: rgba(15,19,26,0.40);
    overflow: hidden;
  }
  #board .streams-tbl { width: 100%; border-collapse: collapse; font-size: 11px; table-layout: fixed; }
  #board .streams-tbl thead th {
    color: var(--p-dim); font-weight: 700; text-align: right; letter-spacing: 0.06em;
    padding: 3px 8px; font-size: 9px; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    border-bottom: 1px solid var(--ghost); background: rgba(10,13,18,0.96);
  }
  #board .streams-tbl thead th.l { text-align: left; }
  #board .streams-tbl tbody td {
    padding: 3px 8px; text-align: right; font-variant-numeric: tabular-nums;
    border-bottom: 1px dotted rgba(95,135,175,0.08); color: var(--p-gry);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board .streams-tbl tbody td.l { text-align: left; }
  #board .streams-tbl tbody tr.lane td:first-child { border-left: 3px solid var(--ghost); }
  #board .streams-tbl tbody tr.lane.lane-a td:first-child { border-left-color: var(--stream-a); }
  #board .streams-tbl tbody tr.lane.lane-b td:first-child { border-left-color: var(--stream-b); }
  #board .streams-tbl tbody tr.lane.lane-c td:first-child { border-left-color: var(--stream-c); }
  #board .streams-tbl td.ln-label { font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .streams-tbl tr.lane-a td.ln-label { color: var(--stream-a); }
  #board .streams-tbl tr.lane-b td.ln-label { color: var(--stream-b); }
  #board .streams-tbl tr.lane-c td.ln-label { color: var(--stream-c); }
  #board .streams-tbl td.ln-tag { font-size: 9px; letter-spacing: 0.04em; }
  #board .streams-tbl td.ln-eq { color: var(--p-wht); font-weight: 700; }
  #board .streams-tbl td.ln-rc .rc-item { margin-right: 6px; }
  #board .streams-tbl td.ln-rc .rc-sym { color: var(--p-gry); }
  #board .streams-tbl td.ln-rc .rc-pn { font-variant-numeric: tabular-nums; }

  /* ── Tab strip (E2) — full bottom width, 8 tabs, each pane full-width. ──── */
  #board .b-tabs {
    display: flex; gap: 4px; align-items: stretch; flex-wrap: wrap;
    border-bottom: 1px solid var(--ghost); padding-bottom: 2px;
  }
  #board .b-tab {
    appearance: none; cursor: pointer;
    background: rgba(15,19,26,0.55);
    border: 1px solid rgba(95,135,175,0.22); border-bottom: none;
    color: var(--p-dim);
    font-family: var(--font-mono); font-size: 11px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase;
    padding: 5px 13px;
  }
  #board .b-tab:hover { color: var(--p-wht); }
  #board .b-tab.active {
    color: var(--polaris-blue);
    border-color: var(--polaris-blue);
    background: rgba(95,135,175,0.10);
  }
  #board .b-tab .tab-cnt { color: var(--p-cyn); font-weight: 700; letter-spacing: 0; margin-left: 5px; font-size: 10px; }
  /* Tab panes: only the active one renders; it fills the whole bottom width.
     The active pane is the whole-page scroll container (Jin 2026-06-24): if its
     content is taller than the 1fr grid row it scrolls vertically instead of
     clipping at the bottom. Inner panels with their own .p-body scroll still
     work; this only adds an outer scroll when the stacked content overflows. */
  #board .tab-pane { display: none; min-height: 0; }
  #board .tab-pane.active { display: grid; min-height: 0; grid-template-rows: minmax(0, 1fr);
    overflow-y: auto; overflow-x: hidden; }
  #board .tab-pane.active::-webkit-scrollbar { width: 6px; }
  #board .tab-pane.active::-webkit-scrollbar-thumb { background: var(--ghost); }

  /* Stream lane color tokens — used by the always-visible streams strip AND
     (in board_tabs.js) by the per-row grouped-table lane heads. */
  #board { --stream-a: #5fdfff; --stream-b: #a87cff; --stream-c: #ffc84f; }
  /* Panel shell + tables + price-flash + lane-head CSS (used only by tab
     content) live in board_tabs.js to keep this core-shell module compact. */
  `;

  function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── helpers (shared with board_tabs.js via window.PolarisBoard) ───────────
  const $ = (id) => document.getElementById(id);
  function fmtUsd(v, dp = 0) {
    if (v == null || isNaN(v)) return '—';
    const sign = v < 0 ? '-' : '';
    const a = Math.abs(v);
    return sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function fmtPct(v, dp = 2) { return (v == null || isNaN(v)) ? '—' : v.toFixed(dp) + '%'; }
  function fmtSignedPct(v, dp = 2) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(dp) + '%';
  }
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
  // Compact price formatter — adapts dp to magnitude so a $0.0042 alt and a
  // $68,000 BTC both read cleanly.
  function fmtPx(v) {
    if (v == null || isNaN(v) || v <= 0) return '—';
    const a = Math.abs(v);
    const dp = a >= 1000 ? 1 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
    return v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  // Quote-currency symbol for the price columns (issue 4: J225 71,847 is JPY,
  // not raw). USD / USDT / USDC and unknown codes get no symbol (the price reads
  // as-is); only the non-USD quote currencies the venues actually quote in get a
  // glyph. This labels the price cell ONLY — size_usd / pnl stay USD ($).
  const CCY_SYMBOL = {
    JPY: '¥', EUR: '€', GBP: '£', AUD: 'A$', NZD: 'NZ$', CHF: 'CHF ',
    CAD: 'C$', NOK: 'kr ', SEK: 'kr ', DKK: 'kr ', ZAR: 'R ', SGD: 'S$',
    HKD: 'HK$', CNH: '¥', PLN: 'zł ',
  };
  function ccySymbol(ccy) {
    return CCY_SYMBOL[String(ccy || '').toUpperCase()] || '';
  }
  // Price with its quote-currency symbol prepended (falls back to the bare
  // fmtPx when the quote is USD-equivalent / unknown).
  function fmtPxCcy(v, ccy) {
    const s = fmtPx(v);
    if (s === '—') return s;
    return ccySymbol(ccy) + s;
  }
  function fmtR(v, dp = 2) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(dp) + 'R';
  }

  // Symbol sparkline (Jin 2026-06-25) — tiny inline SVG trend of a row's recent
  // closes (d.spark[], oldest→newest). Trend colour: last >= first = green (up),
  // else red. Returns '' for a missing / too-short series so the cell just shows
  // the symbol. Display-only chrome; the series comes from the read-only bars
  // cache embedded on the snapshot row. ~62×16 px, scales the closes to the box.
  const SPARK_W = 62, SPARK_H = 16, SPARK_PAD = 2;
  function sparkline(arr) {
    if (!Array.isArray(arr) || arr.length < 2) return '';
    const n = arr.length;
    let lo = Infinity, hi = -Infinity;
    for (const v of arr) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (!isFinite(lo) || !isFinite(hi)) return '';
    const span = (hi - lo) || 1;   // flat series → a centred horizontal line
    const innerW = SPARK_W - SPARK_PAD * 2, innerH = SPARK_H - SPARK_PAD * 2;
    const pts = arr.map((v, i) => {
      const x = SPARK_PAD + (n === 1 ? 0 : (i / (n - 1)) * innerW);
      const y = SPARK_PAD + innerH - ((v - lo) / span) * innerH;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    const up = arr[n - 1] >= arr[0];
    const col = up ? 'var(--p-grn)' : 'var(--p-red)';
    return `<svg class="spark" width="${SPARK_W}" height="${SPARK_H}" `
      + `viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none" `
      + `aria-hidden="true"><polyline points="${pts}" fill="none" `
      + `stroke="${col}" stroke-width="1" stroke-linejoin="round" `
      + `stroke-linecap="round"/></svg>`;
  }

  // uPnL trajectory (Jin 2026-06-27) — "실시간 피 어케 되는지": how a position's
  // unrealised PnL has MOVED since entry, not just its current value. Derives a
  // uPnL_t series from the row's own price history (spark[], oldest→newest) +
  // entry_price + qty + side: uPnL_t = (price_t − entry_price) × qty × (+1 long
  // / −1 short). The series is a SHAPE (quote-ccy units, auto-scaled), so the
  // sign vs the zero baseline + the peak/trough markers are honest regardless of
  // FX conversion (qty×Δprice shares the USD-uPnL sign). DISPLAY-ONLY — derived
  // purely from already-present row fields; never feeds sizing/gating/exit.
  function upnlTrajectory(spark, entryPrice, qty, side) {
    if (!Array.isArray(spark) || spark.length < 2) return null;
    const e = +entryPrice, q = +qty;
    if (!isFinite(e) || !isFinite(q) || e === 0 || q === 0) return null;
    const dir = String(side || '').toLowerCase() === 'short' ? -1 : 1;
    const series = spark.map(px => (+px - e) * q * dir);
    let mfeIdx = 0, maeIdx = 0;   // peak (max uPnL) / trough (min uPnL) of the path
    for (let i = 1; i < series.length; i++) {
      if (series[i] > series[mfeIdx]) mfeIdx = i;
      if (series[i] < series[maeIdx]) maeIdx = i;
    }
    return { series, mfeIdx, maeIdx, last: series[series.length - 1] };
  }
  // Compact inline uPnL-trajectory sparkline for a positions row. Green when the
  // CURRENT uPnL (last point) is in profit, red when underwater. A dim zero
  // baseline marks breakeven so you SEE where the path crossed it; a green dot
  // marks the peak (MFE) and a red dot the trough (MAE) of the path itself. ''
  // when the series can't be derived (no/short history) → the cell stays blank.
  const TRAJ_W = 64, TRAJ_H = 16, TRAJ_PAD = 2;
  // Render a derived uPnL series into the compact trajectory SVG (peak/trough
  // dots + breakeven baseline). Shared by the snapshot render + the live extend
  // so both paint identically. '' for a series shorter than 2 points.
  function _upnlSvgFromSeries(series) {
    const s = series, n = s.length;
    if (n < 2) return '';
    let lo = Infinity, hi = -Infinity, mfeIdx = 0, maeIdx = 0;
    for (let i = 0; i < n; i++) {
      const v = s[i];
      if (v < lo) lo = v; if (v > hi) hi = v;
      if (v > s[mfeIdx]) mfeIdx = i; if (v < s[maeIdx]) maeIdx = i;
    }
    // Always include 0 in the range so the breakeven baseline is on-canvas.
    lo = Math.min(lo, 0); hi = Math.max(hi, 0);
    const span = (hi - lo) || 1;
    const iw = TRAJ_W - TRAJ_PAD * 2, ih = TRAJ_H - TRAJ_PAD * 2;
    const xOf = i => TRAJ_PAD + (n === 1 ? 0 : (i / (n - 1)) * iw);
    const yOf = v => TRAJ_PAD + ih - ((v - lo) / span) * ih;
    const pts = s.map((v, i) => xOf(i).toFixed(1) + ',' + yOf(i).toFixed(1)).join(' ');
    const col = s[n - 1] >= 0 ? 'var(--p-grn)' : 'var(--p-red)';
    const zeroY = yOf(0).toFixed(1);
    const base = `<line x1="${TRAJ_PAD}" y1="${zeroY}" x2="${TRAJ_W - TRAJ_PAD}" `
      + `y2="${zeroY}" stroke="var(--p-dim)" stroke-width="0.5" `
      + `stroke-dasharray="2 2"/>`;
    const peak = `<circle cx="${xOf(mfeIdx).toFixed(1)}" cy="${yOf(s[mfeIdx]).toFixed(1)}" `
      + `r="1.4" fill="var(--p-grn)"/>`;
    const trough = `<circle cx="${xOf(maeIdx).toFixed(1)}" cy="${yOf(s[maeIdx]).toFixed(1)}" `
      + `r="1.4" fill="var(--p-red)"/>`;
    return `<svg class="traj" width="${TRAJ_W}" height="${TRAJ_H}" `
      + `viewBox="0 0 ${TRAJ_W} ${TRAJ_H}" preserveAspectRatio="none" `
      + `aria-hidden="true">${base}<polyline points="${pts}" fill="none" `
      + `stroke="${col}" stroke-width="1" stroke-linejoin="round" `
      + `stroke-linecap="round"/>${peak}${trough}</svg>`;
  }
  function upnlSparkline(spark, entryPrice, qty, side) {
    const t = upnlTrajectory(spark, entryPrice, qty, side);
    if (!t) return '';
    return _upnlSvgFromSeries(t.series);
  }

  // Symbol cell (Jin 2026-06-26) — lays a row's SYMBOL + human NAME + sparkline
  // on a fixed 3-column grid (sym block │ name │ spark, spark flush-right) so
  // they line up cleanly across rows instead of inline-flowing ragged. Pure
  // markup/CSS — the spark series + name are the same display-only data as
  // before. badgeHtml = optional ×N stack badge rendered inline after the
  // symbol. Returns the inner content for a `td.tk` cell.
  function symCell(symbol, name, sparkHtml, badgeHtml) {
    const sym = `<span class="sym">${esc(symbol)}${badgeHtml || ''}</span>`;
    const nm = `<span class="nm">${name ? esc(name) : ''}</span>`;
    const spk = `<span class="spk">${sparkHtml || ''}</span>`;
    return `<span class="symcell">${sym}${nm}${spk}</span>`;
  }
  // Live tick-direction recolour (Jin 2026-06-26, req ③) — point an already-
  // rendered sparkline's stroke at the latest LIVE tick direction (up=green /
  // down=red) the instant a mark streams in, between the 1s snapshot repaints.
  // dir = 'up' | 'down' | '' (no-op). Touches the stroke colour ONLY — never the
  // geometry (the snapshot owns the series); the next poll re-derives colour from
  // the closes. `scope` = the row <tr> (or any element) that holds the svg.spark.
  function setSparkDir(scope, dir) {
    if (!scope || !dir) return;
    const pl = scope.querySelector('svg.spark polyline');
    if (pl) pl.setAttribute('stroke', dir === 'up' ? 'var(--p-grn)' : 'var(--p-red)');
  }

  // (j/k) Snapshot freshness — LIVE when the latest snapshot tick is recent.
  // d.ts_now = epoch seconds the snapshot was generated; compared to wall clock.
  // STALE_SEC = a few poll intervals of slack (poll = 1s, snapshot bg-refresh
  // is coarser). Returns { live, ageSec }.
  const STALE_SEC = 30;
  function freshness(d) {
    const ts = d && d.ts_now;
    if (!ts) return { live: false, ageSec: null };
    const age = Math.floor(Date.now() / 1000) - ts;
    return { live: age >= 0 && age <= STALE_SEC, ageSec: age };
  }

  // ── Bloomberg per-cell diff updater (Jin 2026-06-24) ──────────────────────
  // Keeps a live-value <table> as keyed rows (by row key). On each poll the
  // caller builds a fresh row-spec list; this diffs it against the live DOM and
  // touches ONLY changed cells (textContent) — unchanged cells keep their DOM,
  // so the whole table no longer repaints every second. A changed cell briefly
  // flashes (green up / red down by the cell's flash hint). Rows added/removed
  // (new position / exit) are the only structural change.
  //
  // spec = {
  //   tableHtml: () => full <table>…<tbody></tbody></table> shell (built ONCE,
  //              on first paint or when the column structure version changes),
  //   structKey: string — bump to force a one-time full rebuild (e.g. scope
  //              change / colgroup change),
  //   rows: [ { key, cells: [ { html|text, cls, title, flash } ] } ],
  // }
  // cell.flash: 'up' | 'down' | '' — when set AND the cell text changed, the
  // cell gets a fade flash. Pass 'up'/'down' from a numeric compare so only true
  // movers flash; omit for cells that should update silently.
  const FLASH_UP = 'px-flash-up', FLASH_DOWN = 'px-flash-down';
  function syncTable(container, spec) {
    if (!container) return;
    const prevStruct = container.__structKey;
    let tbody = container.querySelector('tbody');
    // (Re)build the table shell only on first paint or a structure-version bump.
    if (!tbody || prevStruct !== spec.structKey) {
      container.innerHTML = spec.tableHtml();
      container.__structKey = spec.structKey;
      tbody = container.querySelector('tbody');
    }
    if (!tbody) return;
    const rowSpecs = spec.rows;
    const have = new Map();           // key → <tr> currently in the DOM
    for (const tr of tbody.children) { if (tr.dataset.k != null) have.set(tr.dataset.k, tr); }
    const seen = new Set();
    // Fade-in only AFTER the first paint — so a genuinely-new row (a fresh
    // position / trade) eases in individually, but the initial load doesn't
    // flood every row with the animation (which would read as a batch sweep).
    const painted = container.__painted === spec.structKey;
    let cursor = tbody.firstChild;    // walk to keep DOM order == spec order
    for (const rs of rowSpecs) {
      seen.add(rs.key);
      let tr = have.get(rs.key);
      if (!tr) {
        tr = document.createElement('tr');
        tr.dataset.k = rs.key;
        if (rs.trCls) tr.className = rs.trCls;
        rs.cells.forEach(c => {
          const td = document.createElement('td');
          applyCell(td, c, false);
          tr.appendChild(td);
        });
        if (painted) {   // new row arriving on a live frame → ease it in
          tr.classList.add('row-in');
          tr.addEventListener('animationend', () => tr.classList.remove('row-in'), { once: true });
        }
      } else {
        if (rs.trCls != null && tr.className !== rs.trCls) tr.className = rs.trCls;
        const tds = tr.children;
        for (let i = 0; i < rs.cells.length; i++) {
          let td = tds[i];
          if (!td) { td = document.createElement('td'); tr.appendChild(td); applyCell(td, rs.cells[i], false); continue; }
          applyCell(td, rs.cells[i], true);
        }
        // drop any stale trailing cells (column count shrank within same struct)
        while (tr.children.length > rs.cells.length) tr.removeChild(tr.lastChild);
      }
      // place tr at the cursor position (preserve spec order); advance cursor.
      if (cursor === tr) { cursor = tr.nextSibling; }
      else { tbody.insertBefore(tr, cursor); }
    }
    // remove rows no longer present (closed positions / venues that dropped off).
    have.forEach((tr, k) => { if (!seen.has(k)) tbody.removeChild(tr); });
    container.__painted = spec.structKey;   // subsequent frames may fade new rows
  }
  // Apply one cell spec to a <td>. When diff=true, only touches what changed and
  // flashes on a value change; when diff=false (fresh cell) it just sets state.
  function applyCell(td, c, diff) {
    const html = c.html != null ? c.html : null;
    const text = c.text != null ? String(c.text) : null;
    const changed = html != null
      ? td.__html !== html
      : td.textContent !== (text == null ? '' : text);
    if (changed) {
      if (html != null) { td.innerHTML = html; td.__html = html; }
      else { td.textContent = text == null ? '' : text; td.__html = null; }
    }
    const cls = c.cls || '';
    if (td.__baseCls !== cls) { td.__baseCls = cls; td.className = cls; }
    if (c.title != null && td.getAttribute('title') !== c.title) td.setAttribute('title', c.title);
    if (diff && changed && c.flash) {
      td.classList.remove(FLASH_UP, FLASH_DOWN);
      // force reflow so re-adding the same class restarts the animation
      void td.offsetWidth;
      td.classList.add(c.flash === 'up' ? FLASH_UP : FLASH_DOWN);
    }
  }
  // Axis-less Tufte sparkline path for a numeric series into a w×h box.
  function sparkPath(series, w, h, pad) {
    const n = series.length;
    if (n < 2) return '';
    const mn = Math.min(...series), mx = Math.max(...series);
    const span = (mx - mn) || 1;
    const x = i => pad + (i / (n - 1)) * (w - 2 * pad);
    const y = v => pad + (1 - (v - mn) / span) * (h - 2 * pad);
    let dd = '';
    series.forEach((v, i) => { dd += (i === 0 ? 'M' : 'L') + ' ' + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
    return dd;
  }
  // Shared min/max across two series so both sparklines share one y-scale.
  function sparkPathShared(series, mn, mx, w, h, pad) {
    const n = series.length;
    if (n < 2) return '';
    const span = (mx - mn) || 1;
    const x = i => pad + (i / (n - 1)) * (w - 2 * pad);
    const y = v => pad + (1 - (v - mn) / span) * (h - 2 * pad);
    let dd = '';
    series.forEach((v, i) => { dd += (i === 0 ? 'M' : 'L') + ' ' + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
    return dd;
  }

  // Client-side venue→stream group-by. A=okx B=capital C=alpaca. Display-only.
  const VENUE_TO_STREAM = { okx: 'A', capital: 'B', alpaca: 'C' };
  const STREAM_ORDER = ['A', 'B', 'C', 'X'];
  const STREAM_LABEL = { A: 'OKX', B: 'CAPITAL', C: 'ALPACA', X: 'OTHER' };
  const STREAM_TAGLINE = {
    A: 'SPOT CRYPTO · long · 24/7',
    B: 'CFD · LONG/SHORT · sessions',
    C: 'US EQUITY · long · RTH/PDT',
    X: 'unmapped venue',
  };
  function venueStream(venue) {
    return VENUE_TO_STREAM[String(venue || '').toLowerCase()] || 'X';
  }
  function laneGroups(rows) {
    const by = {};
    rows.forEach(r => { const s = venueStream(r.venue); (by[s] = by[s] || []).push(r); });
    return STREAM_ORDER.filter(s => by[s] && by[s].length).map(s => ({ stream: s, rows: by[s] }));
  }

  // ── one-time skeleton ─────────────────────────────────────────────
  // HEADER (KPIs) + 3-STREAM EXCHANGE SUMMARY (always visible) + full-width
  // TAB STRIP + the 8 tab panes (only the active one renders). board_tabs.js
  // owns each pane's inner markup + render fn.
  // 6 composite tabs (2026-06-22 redesign). Each tab groups several existing
  // snapshot panels + new sub-renderers; the 6-row #board grid contract is
  // UNCHANGED — only the tab-strip content + per-tab pane grouping changed.
  const TABS = [
    { id: 'activity', label: 'Activity' },
    { id: 'gates', label: 'Gates' },
    { id: 'performance', label: 'Performance' },
    { id: 'logic', label: 'Logic' },
    { id: 'context', label: 'Context' },
    { id: 'build', label: 'Build' },
    { id: 'path', label: 'Roadmap' },
    { id: 'learned', label: 'Lessons' },
    { id: 'ai', label: 'AI' },
    { id: 'chart', label: 'Chart' },
    { id: 'weekend', label: 'Weekend' },
  ];

  function skeleton() {
    const tabBtns = TABS.map((t, i) =>
      `<button type="button" class="b-tab${i === 0 ? ' active' : ''}" data-tab="${t.id}">`
      + `${esc(t.label)}<span class="tab-cnt" id="tabcnt-${t.id}"></span></button>`
    ).join('');
    const panes = window.PolarisBoardTabs
      ? window.PolarisBoardTabs.paneMarkup(TABS)
      : '';
    return `
    <div class="b-head">
      <span class="title"><span class="star">★</span> POLARIS</span>
      <span class="badge">DEMO·PAPER</span>
      <span class="regime-lbl">MARKETS</span><span class="regime-tag" id="b-regime">—</span>
      <span class="meta">Watching <b id="b-focus">—</b> symbols · <b id="b-cells">—</b> active cells · refreshed <b id="b-refresh">—</b></span>
      <span class="clock" id="b-clock">--:--:--</span>
    </div>

    <div class="kpis" id="b-kpis"></div>

    <!-- EXCHANGE SELECTOR (E3) — scopes tabs below + globe focus (board_exchange.js). -->
    ${window.PolarisBoardExchange ? window.PolarisBoardExchange.selectorMarkup() : ''}

    <!-- 3-STREAM EXCHANGE SUMMARY — always visible (stays on top). -->
    <div class="streams-strip" id="b-streams"></div>

    <!-- FULL-WIDTH TAB STRIP — POSITIONS default. -->
    <div class="b-tabs" id="b-tabs">${tabBtns}</div>

    ${panes}

    <div class="b-footer" id="b-footer"></div>`;
  }

  // ── shared renderers (header + KPIs + exchange summary) ───────────────────
  // Per-instrument regime DISTRIBUTION (Jin 2026-06-23): regime is per-market
  // (60 markets, each own regime in regime_states), NOT one global regime. The
  // header shows the spread across markets ("39 choppy · 10 volatile · …") so it
  // never reads as a single global label. regime_bars already counts per-market.
  function regimeDist(d) {
    const bars = (d.regime_bars || []).filter(b => (b.count || 0) > 0)
      .slice().sort((a, b) => b.count - a.count);
    if (!bars.length) return null;
    const total = bars.reduce((s, b) => s + (b.count || 0), 0);
    return { total, parts: bars.map(b => b.count + ' ' + regimePlain(b.regime)) };
  }
  function renderHeader(d) {
    const dist = regimeDist(d);
    const tag = $('b-regime');
    if (dist) {
      // Jin 2026-06-23: regime notation was too much on screen. Compact to
      // total + dominant only; full per-regime breakdown stays in the tooltip
      // (+ the REGIME tab + Open Positions REGIME column).
      tag.textContent = dist.total + ' mkt · ' + dist.parts[0];
      tag.title = 'Regime is PER-INSTRUMENT — ' + dist.total
        + ' markets, each classified on its own. Counts: ' + dist.parts.join(', ')
        + '. (Per-market detail in the REGIME tab + Open Positions REGIME column.)';
    } else {
      tag.textContent = '—';
      tag.title = '';
    }
    $('b-focus').textContent = d.universe_focus_n ?? '—';
    $('b-cells').textContent = d.active_cells_n ?? '—';
    $('b-refresh').textContent = d.universe_last_refresh || '—';
    // (k) star LIVE/STALE — twinkle when the snapshot tick is fresh, dim when stale.
    const star = document.querySelector('#board .b-head .star');
    if (star) {
      const live = freshness(d).live;
      star.classList.toggle('live', live);
      star.classList.toggle('stale', !live);
    }
  }

  // P0-1 — stale-server detection aid: small footer showing the running
  // server's git SHA + boot time (server.py stamps ``meta`` on every
  // /api/snapshot response). Bloomberg density: one dim line, no card.
  function renderFooter(d) {
    const el = $('b-footer'); if (!el) return;
    const m = d.meta;
    if (!m) { el.textContent = ''; return; }
    const boot = m.boot_ts ? new Date(m.boot_ts * 1000).toLocaleString() : '—';
    el.textContent = 'server ' + (m.git_sha || 'unknown') + ' · up since ' + boot;
  }

  // (j) Anomaly scan — only things worth watching. Returns a short string list;
  // empty list ⇒ 'all clear'. Pure display heuristics off live snapshot fields:
  //  · stuck exits   = positions whose exit FSM left 'open' but still held long
  //  · stale price   = snapshot tick itself is stale (ts_now age)
  //  · drift losses  = positions reconciled away without a clean exit this session
  const STUCK_EXIT_SEC = 1800;   // 30m past the exit FSM leaving 'open'
  function scanAnomalies(d) {
    const out = [];
    const pos = d.positions || [];
    const stuck = pos.filter(p => {
      const st = String(p.exit_state || '').toLowerCase();
      return st && st !== 'open' && (p.held_sec || 0) > STUCK_EXIT_SEC;
    }).length;
    if (stuck) out.push(stuck + ' stuck exit' + (stuck > 1 ? 's' : ''));
    const fr = freshness(d);
    if (!fr.live) out.push('stale price feed' + (fr.ageSec != null ? ' (' + hms(fr.ageSec) + ')' : ''));
    const driftN = d.reconciled_loss_n || 0;
    // Hardening #1: PRIMARY = actual realized Σ pnl_usd over reconciled positions
    // (real lost $); the mae_r×risk est is the labeled secondary fallback.
    const driftActual = d.reconciled_realized_usd || 0;
    const driftEst = d.reconciled_loss_usd || 0;
    const driftUsd = driftActual !== 0 ? driftActual : driftEst;
    if (driftN > 0) out.push(driftN + ' tracking failure' + (driftN > 1 ? 's' : '') + ' (-$' + Math.abs(driftUsd).toFixed(0) + (driftActual !== 0 ? '' : ' est') + ', not trades)');
    return out;
  }

  // Plain-English regime label (Jin 2026-06-22: no CHOP-style abbreviations).
  // Canonical regimes are bull_trend / bear_trend / chop / crisis (+ range /
  // trend); anything unmapped falls back to underscores→spaces.
  const REGIME_PLAIN = {
    bull_trend: 'trending up', bear_trend: 'trending down', chop: 'choppy',
    crisis: 'volatile', range: 'range-bound', trend: 'trending',
    calm: 'calm', quiet: 'quiet', neutral: 'neutral',
  };
  function regimePlain(r) {
    if (!r) return '—';
    const k = String(r).toLowerCase();
    return REGIME_PLAIN[k] || k.replace(/_/g, ' ');
  }

  // Reshaped (2026-06-22, Jin feedback): the always-visible KPI block is now a
  // CLEAN ENGLISH MONEY HEADLINE — no jargon clusters. Row 1 = status (bot /
  // market / updated). Row 2 = labelled money metrics. The old BLEED (worst
  // strategies) + HIDDEN (costs) blocks moved to the Performance tab.
  // SINCE RESET (new logic) — prominent forward-edge line. Measured only over
  // trades OPENED after the latest main-logic reset (the edge under the CURRENT
  // logic). Hidden when no reset has been stamped (all-time metrics stand alone).
  function sinceResetHtml(d) {
    const s = d.since_reset;
    if (!s) return '';
    const pf = (s.pf >= 9.99) ? '∞' : (s.pf || 0).toFixed(2);
    const winning = (s.pf != null && s.pf >= 1) || (s.net_usd >= 0);
    const netCls = winning ? 'b-pos' : 'b-neg';
    const when = s.reset_ts ? new Date(s.reset_ts * 1000).toLocaleString() : '';
    const lbl = s.label ? esc(s.label) : 'reset';
    const sha = s.git_sha ? ' <span class="kk">@' + esc(s.git_sha) + '</span>' : '';
    const m = (label, valHtml) =>
      `<span style="display:inline-flex;align-items:baseline;gap:5px">
        <span class="kk">${label}</span> <span style="font-weight:700">${valHtml}</span></span>`;
    return `<div title="Forward edge since the latest main-logic reset — counts only trades OPENED under the new logic (opened_ts ≥ reset @ ${esc(when)})"
        style="display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;padding:5px 8px;margin-top:4px;border:1px solid rgba(95,175,255,.35);border-radius:4px;background:rgba(95,175,255,.07);font-size:13px">
        <span style="font-weight:800;letter-spacing:.08em;color:var(--p-acc,#5fafff)">SINCE RESET</span>
        <span class="kk" title="${esc(when)}">${lbl}${sha}</span>
        ${m('Net', `<span class="${netCls}">${fmtUsd(s.net_usd, 0)}</span>`)}
        ${m('Trades', `<span class="b-flat">${s.n || 0}</span>`)}
        ${m('Win', `<span class="b-flat">${(s.win_pct || 0).toFixed(0)}%</span>`)}
        ${m('PF', `<span class="${winning ? 'b-pos' : 'b-neg'}">${pf}</span>`)}
        ${m('Avg-R', `<span class="${pn(s.avg_r)}">${fmtR(s.avg_r, 2)}</span>`)}
      </div>`;
  }

  function renderKpis(d) {
    const el = $('b-kpis'); if (!el) return;
    el.style.display = 'block';
    const c = d.confidence || {};
    const pf = c.profit_factor, wr = c.win_rate_pct;
    // Regime is per-instrument (60 markets). Show the dominant share + a full
    // per-market breakdown on hover, never a single global label.
    const rdist = regimeDist(d);
    const top = rdist ? (d.regime_bars || []).slice().sort((a, b) => b.count - a.count)[0] : null;
    const market = rdist
      ? top.count + '/' + rdist.total + ' ' + regimePlain(top.regime)
      : '—';
    const marketTitle = rdist
      ? 'Per-instrument: ' + rdist.parts.join(', ') + ' (' + rdist.total + ' markets)'
      : '';
    // today's % vs starting capital (snapshot carries no daily_pnl_pct).
    const start = d.starting_capital || 0;
    const dayPct = start ? (d.daily_pnl_usd / start) * 100 : null;
    const profitable = (pf != null && pf >= 1);
    const pfTag = (pf == null) ? ''
      : profitable
        ? '<span class="b-pos" style="font-size:9px;letter-spacing:.1em">profitable</span>'
        : '<span class="b-neg" style="font-size:9px;letter-spacing:.1em">losing</span>';
    // (j-i) HEALTH one-liner — Bot LIVE/STALE by tick freshness · winning/losing
    //       · anomaly count. (j-ii) state colour: winning green / losing red.
    const fr = freshness(d);
    const botCls = fr.live ? 'b-pos' : 'b-neg';
    const botTxt = fr.live ? 'LIVE' : 'STALE';
    const botSub = fr.live ? '' : (fr.ageSec != null ? ' <span class="b-flat" style="font-weight:400">' + hms(fr.ageSec) + ' ago</span>' : '');
    const winning = (pf != null) ? profitable : (d.daily_pnl_usd >= 0);
    const winCls = winning ? 'b-pos' : 'b-neg';
    const winTxt = winning ? 'WINNING' : 'LOSING';
    const anoms = scanAnomalies(d);
    const anomTxt = anoms.length
      ? '<span class="anom">' + anoms.length + ' to watch</span>'
      : '<span class="anom clear">all clear</span>';
    const health =
      `<div class="health">
        <span><span class="dot ${botCls}">●</span><span class="hl ${botCls}">Bot ${botTxt}</span>${botSub}</span>
        <span class="hl ${winCls}">${winTxt}</span>
        ${anomTxt}
        ${dualSparkHtml(d)}
      </div>`;
    const status = health +
      `<div style="display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;padding:4px 2px;font-size:12px">
        <span><span class="kk">Bot</span> <span class="${botCls}" style="font-weight:700">${botTxt}</span></span>
        <span title="${esc(marketTitle)}"><span class="kk">Markets</span> <span style="color:var(--p-wht);font-weight:700">${esc(market)}</span></span>
        <span style="margin-left:auto"><span class="kk">Updated</span> <span class="b-flat" id="b-kpi-clock">${clockStr()}</span></span>
      </div>`;
    const metric = (label, valHtml, tag) =>
      `<span style="display:inline-flex;align-items:baseline;gap:6px">
        <span class="kk">${label}</span> <span style="font-weight:700">${valHtml}</span>${tag ? ' ' + tag : ''}</span>`;
    const metrics =
      `<div style="display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;padding:4px 2px;margin-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:13px">
        ${metric('Equity', `<span title="OKX demo charges 70bps (7x real); real-fee-net = equity at live 10bps fees"><span class="b-flat">${fmtUsd(d.equity_now, 0)}</span> <span class="kk">demo</span> · <span class="${(start && d.equity_now_real_fee_net >= start) ? 'b-pos' : 'b-neg'}" style="font-weight:700">${fmtUsd(d.equity_now_real_fee_net, 0)}</span> <span class="kk">real-fee-net</span></span>`)}
        ${metric('Today', `<span class="${pn(d.daily_pnl_usd)}">${fmtUsd(d.daily_pnl_usd, 0)}${dayPct == null ? '' : ' (' + fmtSignedPct(dayPct, 2) + ')'}</span>`)}
        ${metric('Win rate', `<span class="b-flat">${wr == null ? '—' : wr.toFixed(0) + '%'}</span>`)}
        ${metric('Profit factor', `<span class="${profitable ? 'b-pos' : 'b-neg'}">${pf == null ? '—' : pf.toFixed(2)}</span>`, pfTag)}
        ${metric('Max drawdown', `<span class="b-neg">-${fmtPct(d.drawdown_pct, 1)}</span>`)}
      </div>`;
    // SINCE RESET (new logic) — the FORWARD edge measured only over trades OPENED
    // after the latest main-logic reset. This is the headline Jin reads (the edge
    // under the CURRENT logic), shown above the all-time metrics. Absent (null) =
    // no reset stamped → the line is hidden and the all-time metrics stand alone.
    el.innerHTML = status + sinceResetHtml(d) + metrics;
  }

  // (f) main-area dual-equity sparkline — equity_curve (demo, dashed dim) vs
  // equity_curve_real_fee_net (headline, green/red by trend). Axis-less, small,
  // shares one y-scale so the GAP (honest fee cost) reads at a glance. Renders
  // inline in the HEALTH row; graceful empty when the curves aren't ready.
  function dualSparkHtml(d) {
    const real = d.equity_curve_real_fee_net || [];
    const demo = d.equity_curve || [];
    if (real.length < 2 && demo.length < 2) return '';
    const W = 200, H = 30, pad = 2;
    const all = real.concat(demo);
    const mn = Math.min(...all), mx = Math.max(...all);
    const rUp = real.length >= 2 ? real[real.length - 1] >= real[0] : true;
    const rStroke = rUp ? 'var(--p-grn)' : 'var(--p-red)';
    let paths = '';
    if (demo.length >= 2) {
      paths += `<path d="${sparkPathShared(demo, mn, mx, W, H, pad)}" fill="none" stroke="rgba(158,158,158,0.55)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
    }
    if (real.length >= 2) {
      paths += `<path d="${sparkPathShared(real, mn, mx, W, H, pad)}" fill="none" stroke="${rStroke}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
    }
    const dv = real.length >= 2 ? real[real.length - 1] - real[0] : null;
    const lg = dv == null ? ''
      : `<span class="lg">trend <span class="v ${pn(dv)}">${fmtUsd(dv, 0)}</span> after real fees</span>`;
    return `<span class="eq-spark" style="margin-left:auto" title="Equity trend — solid line = profit after REAL OKX fees (green up / red down); dim dashed = demo-actual. Gap = honest fee cost.">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${paths}</svg>${lg}</span>`;
  }

  // Per-stream summary strip — ALWAYS visible. Server-fed d.streams.
  // Bloomberg de-card (2026-06-22): each venue = ONE dense tabular row (no card
  // chrome / big padding). venue │ equity │ net PnL │ uPnL │ exposure │ open │
  // closed │ fee │ slip │ net-cost · recently-closed inline. Row click → scope
  // (data-ex preserved; board_exchange.js delegates the click + highlights).
  // Per-cell diff state for the streams strip live values (equity / uPnL).
  // venue → { eq, upnl } last numeric value, for flash-direction.
  const _lastStream = {};
  const STREAMS_SHELL =
    `<table class="streams-tbl"><colgroup>
        <col style="width:9%"><col style="width:14%"><col style="width:8%"><col style="width:7%">
        <col style="width:7%"><col style="width:7%"><col style="width:4%"><col style="width:4%">
        <col style="width:5%"><col style="width:5%"><col style="width:5%"><col style="width:6%">
        <col style="width:13%">
       </colgroup><thead><tr>
        <th class="l">VENUE</th><th class="l">TRACK</th><th>EQUITY</th><th>NET P&L</th>
        <th>uPnL</th><th>EXPOSURE</th><th>EXP%</th><th>OPEN</th>
        <th>CLOSED</th><th>FEE</th><th>SLIP</th><th title="net after fees + slippage">NET-COST</th>
        <th class="l">RECENTLY CLOSED</th>
      </tr></thead><tbody></tbody></table>`;
  function flashDir(prev, cur) {
    if (prev == null || cur == null || cur === prev) return '';
    return cur > prev ? 'up' : 'down';
  }
  function renderStreams(d) {
    const el = $('b-streams');
    if (!el) return;
    const rows = d.streams || [];
    if (!rows.length) { el.innerHTML = ''; el.__structKey = null; return; }
    const specRows = rows.map(s => {
      const st = venueStream(s.venue);
      const lc = st.toLowerCase();
      const tagline = STREAM_TAGLINE[st] || '';
      const expPct = s.equity_usd ? (s.exposed_usd / s.equity_usd) * 100 : 0;
      const closed = (s.closed_n != null ? s.closed_n : (s.daily_trades || 0));
      const exKey = String(s.venue || '').toLowerCase();   // E3 click→scope key
      const key = exKey || String(s.label || st);
      const prev = _lastStream[key] || {};
      const eqFlash = flashDir(prev.eq, s.equity_usd);
      const upFlash = flashDir(prev.upnl, s.upnl_usd);
      _lastStream[key] = { eq: s.equity_usd, upnl: s.upnl_usd };
      return {
        key: key,
        trCls: 'lane lane-' + lc,
        trAttrs: { 'data-ex': exKey,
          title: `${s.label} — ${tagline} (${s.product_class}) · click to scope · start ${fmtUsd(s.starting_capital, 0)} · DD ${fmtPct(s.drawdown_pct)}` },
        cells: [
          { text: s.label, cls: 'l ln-label' },
          { text: tagline, cls: 'l ln-tag b-flat' },
          { text: fmtUsd(s.equity_usd, 0), cls: 'num ln-eq', flash: eqFlash },
          { text: fmtUsd(s.net_pnl_usd, 0), cls: 'num ' + pn(s.net_pnl_usd) },
          { text: fmtUsd(s.upnl_usd, 0), cls: 'num ' + pn(s.upnl_usd), flash: upFlash },
          { text: fmtUsd(s.exposed_usd, 0), cls: 'num b-flat' },
          { text: fmtPct(expPct, 0), cls: 'num b-flat' },
          { text: s.open_positions_n || 0, cls: 'num ' + (s.open_positions_n ? 'b-pos' : 'b-flat') },
          { text: closed, cls: 'num b-flat' },
          { text: fmtUsd(s.fee_usd, 0), cls: 'num b-flat', title: 'fees this session' },
          { text: fmtUsd(s.slippage_usd, 0), cls: 'num b-flat', title: 'slippage this session' },
          { text: s.net_after_cost_usd == null ? '—' : fmtUsd(s.net_after_cost_usd, 0), cls: 'num ' + pn(s.net_after_cost_usd), title: 'net after fees + slippage' },
          { html: recentClosedInline(s), cls: 'l ln-rc b-flat' },
        ],
      };
    });
    syncTable(el, { tableHtml: () => STREAMS_SHELL, structKey: 'streams-v1', rows: specRows });
    // Restore per-row attrs (data-ex / title) that syncTable doesn't manage.
    const tb = el.querySelector('tbody');
    if (tb) {
      specRows.forEach(rs => {
        const tr = tb.querySelector('tr[data-k="' + (window.CSS && CSS.escape ? CSS.escape(rs.key) : rs.key) + '"]');
        if (tr && rs.trAttrs) {
          for (const a in rs.trAttrs) {
            if (tr.getAttribute(a) !== rs.trAttrs[a]) tr.setAttribute(a, rs.trAttrs[a]);
          }
        }
      });
    }
    // Re-apply the active-exchange highlight (syncTable may have rebuilt rows).
    if (window.PolarisBoardExchange) window.PolarisBoardExchange.syncExchangeUi();
  }

  // Recently-closed, inline (was a separate row in the old card). Compact.
  function recentClosedInline(s) {
    const rc = s.recent_closed || [];
    if (!rc.length) return '—';
    return rc.slice(0, 4).map(t =>
      `<span class="rc-item" title="${esc(t.symbol)} ${esc(t.strategy_id)} ${esc(t.exit_reason)} ${fmtUsd(t.pnl_usd, 2)}">`
      + `<span class="rc-sym">${esc(t.symbol)}</span> <span class="rc-pn ${pn(t.pnl_usd)}">${fmtUsd(t.pnl_usd, 0)}</span></span>`
    ).join(' ');
  }

  // ── tab switcher ──────────────────────────────────────────────────────────
  // Pure DOM visibility — no data/snapshot/trading change. Tracks the active
  // tab id so the poll loop only re-renders the visible pane (cheap).
  let _activeTab = TABS[0].id;
  function initTabs() {
    const tabs = $('b-tabs');
    if (!tabs) return;
    tabs.addEventListener('click', (e) => {
      const btn = e.target.closest('.b-tab');
      if (!btn) return;
      const which = btn.getAttribute('data-tab');
      if (!which) return;
      _activeTab = which;
      tabs.querySelectorAll('.b-tab').forEach(b =>
        b.classList.toggle('active', b === btn));
      TABS.forEach(t => {
        const pane = $('pane-' + t.id);
        if (pane) pane.classList.toggle('active', t.id === which);
      });
      // Re-render the just-activated pane immediately from the last frame.
      if (_lastFrame && window.PolarisBoardTabs) {
        window.PolarisBoardTabs.renderTab(which, _lastFrame);
      }
    });
  }

  // ── poll loop ─────────────────────────────────────────────────────────────
  let _lastFrame = null;
  // E3: re-render streams highlight + active tab now (called by board_exchange.js).
  function rerenderActive() {
    if (!_lastFrame) return;
    renderStreams(_lastFrame);
    if (window.PolarisBoardTabs) window.PolarisBoardTabs.renderTab(_activeTab, _lastFrame);
  }
  function render(d) {
    _lastFrame = d;
    // Each section is isolated: a throw in one (e.g. a null field after a
    // backend change) must NOT blank the rest of the board. Errors surface in
    // the console instead of being swallowed by poll()'s catch.
    try { renderHeader(d); } catch (e) { console.error('[render] renderHeader', e); }
    try { renderKpis(d); } catch (e) { console.error('[render] renderKpis', e); }
    try { renderStreams(d); } catch (e) { console.error('[render] renderStreams', e); }
    try { renderFooter(d); } catch (e) { console.error('[render] renderFooter', e); }
    if (window.PolarisBoardTabs) {
      try { window.PolarisBoardTabs.renderTabCounts(d, TABS); }
      catch (e) { console.error('[render] renderTabCounts', e); }
      // Render only the visible tab (the rest re-render on switch from cache).
      try { window.PolarisBoardTabs.renderTab(_activeTab, d); }
      catch (e) { console.error('[render] renderTab', e); }
    }
    // Bridge optional probe data to the Neural Cloud (display-only). Probes pulse
    // their ticker node + venue galaxy on the globe; graceful no-op when absent.
    if (window.PolarisGlobe && window.PolarisGlobe.showProbes) {
      try { window.PolarisGlobe.showProbes(d.probe_events || d.probes || []); } catch (e) {}
    }
  }

  async function poll() {
    try {
      const r = await fetch('/api/snapshot?t=' + Date.now(), { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (e) { /* keep last frame */ }
  }

  // ── Live price SSE push (Jin 2026-06-24) ──────────────────────────────────
  // Open-position price cells flash per-cell in real time as marks STREAM in —
  // NOT a poll. An EventSource on /stream/prices receives a push the instant a
  // mark moves; each message carries ONLY the cells that changed, so just those
  // cells get their textContent updated + a brief up/down flash and that cell
  // alone reacts (no whole-table repaint, no synchronized full-grid refresh).
  // diffs ONLY the CURRENT / Δ% / uPnL$ / uPnL% cells of the keyed Open-Positions
  // rows: a cell whose value moved gets its textContent updated + a brief up/down
  // flash; unchanged cells are untouched (no repaint). Pure display — no snapshot
  // mutation, no sizing/gate/trade logic. Graceful no-op when pos-body isn't
  // mounted (non-Activity tab) or the endpoint is unavailable.
  // Column indices match renderPositions' cell order (board_tabs.js): 4=CURRENT
  // (last_price), 5=Δ% (delta_pct); uPnL TRAJ moved to 7 (next to SIZE$), so
  // 8=uPnL$ (upnl_usd), 10=uPnL% (upnl_pct). (Previously uPnL% wrote to 8 =
  // uPnLnet$ — fixed here while reindexing.)
  const _priceLast = {};   // key → { px, upnl, delta } last numeric value for flash dir
  // uPnL trajectory live-extend (Jin 2026-06-27). renderPositions stashes each
  // open row's deriving fields (spark seed + entry/qty/side) here per row key;
  // the price stream appends a live uPnL point from the streamed last_price so
  // the mini path GROWS smoothly between 1s snapshots. _trajLive caps the live
  // tail so it never runs away; the next snapshot reseeds geometry from spark.
  const _posTrajMeta = {};   // key → { spark, entry, qty, side }
  const _trajLive = {};      // key → array of live derived uPnL points (capped)
  const TRAJ_LIVE_CAP = 30;
  // renderPositions calls this each snapshot when it reseeds a row's meta: the
  // fresh spark[] already incorporates the latest close, so the live tail starts
  // over from there (no unbounded cross-snapshot accumulation).
  function resetTrajLive(key) { _trajLive[key] = []; }
  function _redrawTraj(tr, key, lastPrice) {
    const m = _posTrajMeta[key];
    if (!m || lastPrice == null) return;
    const cell = tr.children[7];   // uPnL TRAJ column (moved next to SIZE$)
    if (!cell) return;
    const dir = String(m.side || '').toLowerCase() === 'short' ? -1 : 1;
    const pt = (+lastPrice - +m.entry) * +m.qty * dir;
    if (!isFinite(pt)) return;
    const buf = _trajLive[key] || (_trajLive[key] = []);
    buf.push(pt);
    if (buf.length > TRAJ_LIVE_CAP) buf.shift();
    // Seed series = the snapshot spark-derived path; the live tail extends it.
    const seed = upnlTrajectory(m.spark, m.entry, m.qty, m.side);
    const seedSeries = seed ? seed.series : [];
    const merged = seedSeries.concat(buf);
    if (merged.length < 2) return;
    cell.innerHTML = _upnlSvgFromSeries(merged);
    // Invalidate syncTable's cached html for this cell so the NEXT snapshot poll
    // always re-asserts the authoritative spark-derived geometry (the live tail
    // is display-only; the snapshot owns the truth).
    cell.__html = null;
  }
  function _flashCell(td, dir) {
    if (!td || !dir) return;
    td.classList.remove(FLASH_UP, FLASH_DOWN);
    void td.offsetWidth;   // restart the animation if re-flashing the same cell
    td.classList.add(dir === 'up' ? FLASH_UP : FLASH_DOWN);
  }
  function _setPriceCell(td, text, cls, dir) {
    if (!td) return;
    const changed = td.textContent !== text;
    if (changed) td.textContent = text;
    // set the base class BEFORE flashing so the flash animation class is not
    // wiped by a className reassignment (sign may have flipped: pn class change).
    if (cls != null && td.className.replace(/ ?px-flash-(up|down)/, '') !== cls) {
      td.className = cls; td.__baseCls = cls;
    }
    if (changed) _flashCell(td, dir);
  }
  // Apply ONE streamed price cell (or a fallback keyed map). Touches only the
  // four price cells of the matching keyed row; unchanged text is left alone so
  // a single cell reacts in isolation. `p` carries the key inline (SSE form).
  function _applyPriceCell(tb, p) {
    const key = p.key;
    if (!key) return;
    const tr = tb.querySelector('tr[data-k="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
    if (!tr) return;
    const prev = _priceLast[key] || {};
    const pxDir = flashDir(prev.px, p.last_price);
    const upDir = flashDir(prev.upnl, p.upnl_usd);
    const dDir = flashDir(prev.delta, p.delta_pct);
    _priceLast[key] = { px: p.last_price, upnl: p.upnl_usd, delta: p.delta_pct };
    const tds = tr.children;
    _setPriceCell(tds[4], fmtPx(p.last_price), null, pxDir);
    _setPriceCell(tds[5], fmtSignedPct(p.delta_pct, 2), 'num ' + pn(p.delta_pct), dDir);
    _setPriceCell(tds[8], fmtUsd(p.upnl_usd, 2), 'num ' + pn(p.upnl_usd), upDir);
    _setPriceCell(tds[10], fmtSignedPct(p.upnl_pct, 2), 'num ' + pn(p.upnl_pct), '');
    // Req ③: recolour THIS row's sparkline to the live tick direction (up=green /
    // down=red) the instant the mark moves — between the 1s snapshot repaints.
    if (pxDir) setSparkDir(tr, pxDir);
    // Extend the uPnL-trajectory mini-graph with the live point so the path
    // grows smoothly between 1s snapshots (display-only; snapshot owns geometry).
    _redrawTraj(tr, key, p.last_price);
    // Jin 2026-06-24 SYNC: a streamed cell that moved (price flashed) → pulse the
    // SAME symbol's node on the globe on the SAME event, so cell flash + cloud
    // pulse fire together. key = venue|symbol|strategy|side. Display-only.
    if ((pxDir || upDir) && window.PolarisGlobe && window.PolarisGlobe.pulseSymbol) {
      const parts = String(key).split('|');
      try { window.PolarisGlobe.pulseSymbol(parts[1], parts[0]); } catch (e) { /* display-only */ }
    }
  }
  // Drain a streamed batch of changed cells (each carries its own key).
  function applyPriceStream(changed) {
    const body = $('pos-body'); if (!body || !changed || !changed.length) return;
    const tb = body.querySelector('tbody'); if (!tb) return;
    for (let i = 0; i < changed.length; i++) _applyPriceCell(tb, changed[i]);
  }
  // Subscribe to the live-mark push stream. Each message delivers ONLY the cells
  // that moved on the server since the last frame — true per-cell streaming, not
  // a poll. EventSource auto-reconnects; a torn connection just resumes (the
  // server re-sends the full set as its first frame). Display-only.
  let _priceES = null;
  function connectPriceStream() {
    if (_priceES || typeof EventSource === 'undefined') return;
    try {
      _priceES = new EventSource('/stream/prices');
      _priceES.onmessage = (ev) => {
        try {
          const j = JSON.parse(ev.data);
          if (j && j.prices) applyPriceStream(j.prices);
        } catch (e) { /* ignore a malformed frame */ }
      };
      _priceES.onerror = () => { /* EventSource auto-reconnects */ };
    } catch (e) { /* SSE unavailable — open-position cells fall back to the 1s snapshot poll */ }
  }

  // ── Bot log (bottom-left pane) ────────────────────────────────────────────
  function classifyLog(line) {
    const l = line.toLowerCase();
    if (/\b(error|critical|fatal)\b/.test(l) || l.includes('exception') || l.includes('traceback'))
      return 'bl-err';
    if (/\b(warn|warning)\b/.test(l) || l.includes('rejected') || l.includes('retry'))
      return 'bl-warn';
    if (l.includes('order resp ok=true') || /\bopen(ed)?\b/.test(l)
        || /\bclos(e|ed)\b/.test(l) || /\bfill(ed)?\b/.test(l) || /\[tick \d+\]/.test(l))
      return 'bl-hi';
    return '';
  }
  function fmtLogLine(line) {
    const m = line.match(/^(\S+T\S+Z?)\s+(.*)$/);
    const cls = classifyLog(line);
    if (m) {
      return `<div class="bl-line ${cls}"><span class="ts">${esc(m[1])}</span> ${esc(m[2])}</div>`;
    }
    return `<div class="bl-line ${cls}">${esc(line)}</div>`;
  }
  // Rolling tail (Jin: 한 줄씩 차례로 롤링; 페이지 전체 repaint 금지). The /api/botlog
  // tail overlaps heavily each poll, so we APPEND only the genuinely-new trailing
  // lines (matched by the last few shown lines as a stable block) instead of
  // rewriting innerHTML — no flicker, scroll stays pinned, old lines roll off the top.
  let _logShown = [];
  const _LOG_CAP = 600;
  function _newTail(prev, lines) {
    if (!prev.length) return lines;                 // first paint
    const k = Math.min(3, prev.length);
    const sig = prev.slice(prev.length - k);
    for (let i = lines.length - 1; i >= k - 1; i--) {
      let ok = true;
      for (let j = 0; j < k; j++) { if (lines[i - (k - 1) + j] !== sig[j]) { ok = false; break; } }
      if (ok) return lines.slice(i + 1);            // lines after the matched block
    }
    return null;                                    // discontinuity (rotation/gap) → full render
  }
  async function pollLog() {
    const body = $('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const lines = (await r.json()).lines || [];
      if (!lines.length) return;
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
      const nt = _newTail(_logShown, lines);
      let appended = false;
      if (nt === null) {                            // discontinuity: one full render
        body.innerHTML = lines.map(fmtLogLine).join('');
        _logShown = lines.slice();
        appended = true;
      } else if (nt.length) {                        // normal: append new lines only
        body.insertAdjacentHTML('beforeend', nt.map(fmtLogLine).join(''));
        _logShown = _logShown.concat(nt);
        appended = true;
      }
      if (appended) {
        while (_logShown.length > _LOG_CAP) {        // roll old lines off the top
          _logShown.shift();
          if (body.firstChild) body.removeChild(body.firstChild);
        }
        if (atBottom) body.scrollTop = body.scrollHeight;
      }
    } catch (e) { /* keep last frame */ }
  }

  // ── Sphere freeze watchdog ────────────────────────────────────────────────
  function startSphereWatchdog() {
    let lastHB = -1, stalledMs = 0, nudged = false;
    setInterval(function () {
      if (document.hidden) { stalledMs = 0; nudged = false; lastHB = window.__sphereHB || 0; return; }
      const hb = window.__sphereHB || 0;
      if (hb !== lastHB) {
        lastHB = hb; stalledMs = 0; nudged = false;
        return;
      }
      stalledMs += 1000;
      if (stalledMs >= 10000) {
        location.reload();
      } else if (stalledMs >= 5000 && !nudged) {
        nudged = true;
        window.dispatchEvent(new Event('resize'));
      }
    }, 1000);
  }

  // Expose shared helpers for board_tabs.js (loaded after this file).
  if (typeof window !== 'undefined') window.PolarisBoard = {
    $: $, fmtUsd: fmtUsd, fmtPct: fmtPct, fmtSignedPct: fmtSignedPct,
    fmtPx: fmtPx, fmtPxCcy: fmtPxCcy, ccySymbol: ccySymbol,
    fmtR: fmtR, sparkline: sparkline, upnlSparkline: upnlSparkline,
    posTrajMeta: _posTrajMeta,   // key → {spark, entry, qty, side} for live extend
    resetTrajLive: resetTrajLive,   // renderPositions reseeds the live tail per poll
    symCell: symCell, setSparkDir: setSparkDir,
    pn: pn, esc: esc, hms: hms, hhmmss: hhmmss,
    venueStream: venueStream, laneGroups: laneGroups,
    STREAM_LABEL: STREAM_LABEL, STREAM_TAGLINE: STREAM_TAGLINE,
    freshness: freshness,
    syncTable: syncTable,   // Bloomberg per-cell diff updater (live-value tables)
    // E3: re-render hook for exchange-select. The scope helpers
    // (getActiveExchange/venueMatches/venueFilter) are added by board_exchange.js.
    rerenderActive: rerenderActive,
  };

  function init() {
    injectStyle();
    const board = $('board');
    if (board) {
      board.innerHTML = skeleton();
      initTabs();
      if (window.PolarisBoardExchange) window.PolarisBoardExchange.initExchangeSelector();
      setInterval(() => { const c = $('b-clock'); if (c) c.textContent = clockStr(); }, 1000);
      poll();
      setInterval(poll, 1000);
      // Per-cell price flash via PUSH — subscribe to /stream/prices (SSE). The
      // server pushes only the open-position cells that moved, so each cell
      // flashes the instant its mark changes (no poll, no full-grid sync).
      // Display-only. The 1s snapshot poll still owns structure (adds/removes).
      connectPriceStream();
    }
    if ($('botlog-body')) {
      pollLog();
      setInterval(pollLog, 1000);
    }
    startSphereWatchdog();
  }

  // Node export (unit tests) — expose the pure uPnL-trajectory math without the
  // browser bootstrap. Guarded so the browser path is untouched.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { upnlTrajectory: upnlTrajectory, _upnlSvgFromSeries: _upnlSvgFromSeries };
  }
  // Browser bootstrap — only when a real DOM is present (skipped under node).
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }
})();
