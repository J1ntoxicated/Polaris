/*
 * Chart tab — per-ticker candlestick + technicals (DEMO/PAPER, display-only).
 *
 * READ-ONLY window onto the stored `bars`: a venue-grouped symbol selector + a
 * resolution selector fetch `/api/ticker_chart`, then TradingView Lightweight
 * Charts (vendored, Apache-2.0) draws candles + volume with MA/BB/VWAP overlays
 * on the price pane and RSI / MACD / ADX sub-panes. Nothing here reads or writes
 * sizing/risk/orders — it only renders bars + on-demand indicator series.
 *
 * Wiring is deliberately self-contained to keep the shared-file edit surface
 * minimal: this module INJECTS its own `#pane-chart` into `#board` and REGISTERS
 * its renderer on `window.PolarisBoardTabs`. The only edits elsewhere are the
 * one TABS entry in board.js and the two <script> includes in index.html.
 */
(function () {
  'use strict';
  const B = window.PolarisBoard;
  const T = window.PolarisBoardTabs;
  if (!B || !T) return;            // board core absent → no-op (graceful)
  const esc = B.esc;

  // ── pane skeleton (self-injected once) ────────────────────────────────────
  const PANE_HTML = `
    <div class="panel chart-panel">
      <div class="p-head">
        <span>Ticker Chart</span>
        <span class="chart-controls">
          <select id="chart-venue" class="chart-sel" title="Exchange filter"></select>
          <input id="chart-search" class="chart-search" type="text" autocomplete="off"
                 spellcheck="false" placeholder="search symbol / name…" title="Filter symbols by symbol or name">
          <select id="chart-symbol" class="chart-sel" title="Symbol"></select>
          <select id="chart-res" class="chart-sel" title="Resolution"></select>
          <span class="chart-status" id="chart-status"></span>
        </span>
      </div>
      <div class="p-body chart-body">
        <div class="chart-price" id="chart-price"></div>
        <div class="chart-sub" id="chart-rsi"></div>
        <div class="chart-sub" id="chart-macd"></div>
        <div class="chart-sub" id="chart-adx"></div>
        <div class="chart-legend" id="chart-legend"></div>
      </div>
    </div>`;

  const CSS = `
    #pane-chart .chart-panel { grid-row: 1 / -1; display: flex; flex-direction: column; min-height: 0; }
    #pane-chart .chart-controls { margin-left: auto; display: flex; gap: 6px; align-items: center; }
    #pane-chart .chart-sel { background: #0e1014; color: #cfd3dc; border: 1px solid #2a2f3a;
      font: 11px ui-monospace, monospace; padding: 2px 4px; border-radius: 3px; }
    #pane-chart .chart-search { background: #0e1014; color: #cfd3dc; border: 1px solid #2a2f3a;
      font: 11px ui-monospace, monospace; padding: 2px 6px; border-radius: 3px; width: 150px; }
    #pane-chart .chart-search::placeholder { color: #5a6068; }
    #pane-chart .chart-search:focus { outline: none; border-color: #5a6373; }
    #pane-chart .chart-status { font: 10px ui-monospace, monospace; color: #6b7280; }
    #pane-chart .chart-body { display: flex; flex-direction: column; gap: 2px; min-height: 0; overflow: hidden; }
    #pane-chart .chart-price { flex: 3 1 0; min-height: 120px; }
    #pane-chart .chart-sub { flex: 1 1 0; min-height: 60px; }
    #pane-chart .chart-legend { flex: 0 0 auto; font: 10px ui-monospace, monospace;
      color: #8b909c; padding: 2px 4px; display: flex; gap: 12px; flex-wrap: wrap; }
    #pane-chart .chart-legend b { color: #cfd3dc; font-weight: 600; }`;

  function injectStyle() {
    if (document.getElementById('chart-tab-style')) return;
    const s = document.createElement('style');
    s.id = 'chart-tab-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  let _paneReady = false;
  function ensurePane() {
    if (_paneReady) return true;
    const board = document.getElementById('board');
    const tabs = document.getElementById('b-tabs');
    if (!board || !tabs) return false;          // skeleton not mounted yet
    if (document.getElementById('pane-chart')) { _paneReady = true; return true; }
    injectStyle();
    const wrap = document.createElement('div');
    wrap.className = 'tab-pane';
    wrap.id = 'pane-chart';
    wrap.innerHTML = PANE_HTML;
    board.appendChild(wrap);
    wireControls();
    _paneReady = true;
    return true;
  }

  // ── charts (Lightweight Charts) ───────────────────────────────────────────
  const COL = {
    up: '#26a69a', down: '#ef5350', vol: '#37415133',
    ema20: '#f0b429', ema50: '#9b5de5', bb: '#5a6373', vwap: '#00b4d8',
    rsi: '#f0b429', macd: '#26a69a', signal: '#ef5350', hist: '#5a637388',
    adx: '#00b4d8', grid: '#1b1f27', text: '#8b909c',
  };

  let _charts = [];      // {chart, series...} we can dispose on rebuild
  function disposeCharts() {
    _charts.forEach(c => { try { c.remove(); } catch (e) { /* already gone */ } });
    _charts = [];
  }

  function baseOpts(h) {
    return {
      height: h, autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: COL.text, fontSize: 10 },
      grid: { vertLines: { color: COL.grid }, horzLines: { color: COL.grid } },
      timeScale: { borderColor: COL.grid, timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: COL.grid },
      crosshair: { mode: 0 },
    };
  }

  // pair a series array (None-padded) with bar times → [{time, value}] sans nulls
  function line(bars, vals) {
    const out = [];
    for (let i = 0; i < bars.length; i++) {
      const v = vals ? vals[i] : null;
      if (v !== null && v !== undefined) out.push({ time: bars[i].t, value: v });
    }
    return out;
  }

  function render(payload) {
    const LWC = window.LightweightCharts;
    const priceEl = document.getElementById('chart-price');
    if (!LWC || !priceEl) return;
    disposeCharts();
    const bars = payload.bars || [];
    const ind = payload.indicators || {};
    setStatus(payload.available
      ? `${esc(payload.venue)} · ${esc(payload.symbol)} · ${esc(payload.native_interval)} · ${bars.length} bars`
      : 'no bars for this symbol / resolution');
    document.getElementById('chart-rsi').innerHTML = '';
    document.getElementById('chart-macd').innerHTML = '';
    document.getElementById('chart-adx').innerHTML = '';
    priceEl.innerHTML = '';
    if (!bars.length) { renderLegend(payload); return; }

    // ── price pane: candles + volume + MA/BB/VWAP overlays ──
    const pc = LWC.createChart(priceEl, baseOpts(0));
    _charts.push(pc);
    const candles = pc.addCandlestickSeries({
      upColor: COL.up, downColor: COL.down, borderVisible: false,
      wickUpColor: COL.up, wickDownColor: COL.down,
    });
    candles.setData(bars.map(b => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = pc.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: '',
      color: COL.vol,
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(bars.map(b => ({ time: b.t, value: b.v })));
    const overlay = (vals, color, width) => {
      const s = pc.addLineSeries({ color, lineWidth: width || 1, priceLineVisible: false,
        lastValueVisible: false });
      s.setData(line(bars, vals));
    };
    overlay(ind.bb_upper, COL.bb, 1);
    overlay(ind.bb_lower, COL.bb, 1);
    overlay(ind.bb_mid, COL.bb, 1);
    overlay(ind.ema20, COL.ema20, 1);
    overlay(ind.ema50, COL.ema50, 1);
    overlay(ind.vwap, COL.vwap, 1);

    // ── RSI sub-pane (0–100, 30/70 guides) ──
    const rc = LWC.createChart(document.getElementById('chart-rsi'), baseOpts(0));
    _charts.push(rc);
    const rsi = rc.addLineSeries({ color: COL.rsi, lineWidth: 1, priceLineVisible: false });
    rsi.setData(line(bars, ind.rsi));
    [30, 70].forEach(lv => rsi.createPriceLine({ price: lv, color: COL.grid, lineWidth: 1,
      lineStyle: 2, axisLabelVisible: false }));

    // ── MACD sub-pane (line + signal + histogram) ──
    const mc = LWC.createChart(document.getElementById('chart-macd'), baseOpts(0));
    _charts.push(mc);
    const hist = mc.addHistogramSeries({ priceLineVisible: false });
    hist.setData(line(bars, ind.macd_hist).map(p => ({ time: p.time, value: p.value,
      color: p.value >= 0 ? COL.up + '88' : COL.down + '88' })));
    mc.addLineSeries({ color: COL.macd, lineWidth: 1, priceLineVisible: false })
      .setData(line(bars, ind.macd));
    mc.addLineSeries({ color: COL.signal, lineWidth: 1, priceLineVisible: false })
      .setData(line(bars, ind.macd_signal));

    // ── ADX sub-pane (trend strength, 25 guide) ──
    const ac = LWC.createChart(document.getElementById('chart-adx'), baseOpts(0));
    _charts.push(ac);
    const adx = ac.addLineSeries({ color: COL.adx, lineWidth: 1, priceLineVisible: false });
    adx.setData(line(bars, ind.adx));
    adx.createPriceLine({ price: 25, color: COL.grid, lineWidth: 1, lineStyle: 2,
      axisLabelVisible: false });

    renderLegend(payload);
  }

  function renderLegend(payload) {
    const el = document.getElementById('chart-legend');
    if (!el) return;
    if (!payload.available || !(payload.bars || []).length) { el.innerHTML = ''; return; }
    el.innerHTML =
      '<span><b style="color:' + COL.ema20 + '">EMA20</b></span>'
      + '<span><b style="color:' + COL.ema50 + '">EMA50</b></span>'
      + '<span><b style="color:' + COL.bb + '">Bollinger 20/2</b></span>'
      + '<span><b style="color:' + COL.vwap + '">VWAP</b></span>'
      + '<span>Sub-panes: <b>RSI(14)</b> · <b>MACD(12,26,9)</b> · <b>ADX(14)</b></span>';
  }

  function setStatus(txt) {
    const el = document.getElementById('chart-status');
    if (el) el.textContent = txt;
  }

  // ── selectors + fetch ─────────────────────────────────────────────────────
  const RES_LABEL = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1h', '1d': '1d' };
  let _groups = [];
  // Client-side selector filters (display-only — never touch the fetch path).
  // _venueFilter: '' = all venues, else the venue name. _searchText: lowercased
  // substring matched against symbol OR name. Both just hide <option>s.
  let _venueFilter = '';
  let _searchText = '';

  // Per-symbol human name from the API list (universe.name), keyed venue␟symbol.
  function symApiName(venue, symbol) {
    for (const g of _groups) {
      if (g.venue !== venue) continue;
      const m = (g.symbols || []).find(s => s.symbol === symbol);
      if (m && m.name) return m.name;
    }
    return '';
  }

  function currentSymbolMeta() {
    const sel = document.getElementById('chart-symbol');
    if (!sel || !sel.value) return null;
    // unit-separator (U+241F) joined as `venue␟symbol`; split on the FIRST
    // separator only so a symbol that ever contained one is not truncated.
    const sep = sel.value.indexOf('␟');
    const venue = sep < 0 ? sel.value : sel.value.slice(0, sep);
    const symbol = sep < 0 ? '' : sel.value.slice(sep + 1);
    for (const g of _groups) {
      if (g.venue !== venue) continue;
      const m = (g.symbols || []).find(s => s.symbol === symbol);
      if (m) return { venue, symbol, resolutions: m.resolutions || [] };
    }
    return { venue, symbol, resolutions: [] };
  }

  function populateResolutions() {
    const meta = currentSymbolMeta();
    const sel = document.getElementById('chart-res');
    if (!sel) return;
    const prev = sel.value;
    const res = (meta && meta.resolutions.length) ? meta.resolutions : ['1m'];
    sel.innerHTML = res.map(r =>
      `<option value="${esc(r)}">${esc(RES_LABEL[r] || r)}</option>`).join('');
    if (res.includes(prev)) sel.value = prev;
  }

  // Dropdown label = "{symbol} : {name}" when a human name is known. Prefers the
  // backend universe.name (covers the full Alpaca ~1600 stocks), falling back to
  // the board's hardcoded SYM_NAME map (PolarisBoardTabs.symName), else the bare
  // "{symbol}" (graceful). The <option> VALUE is left unchanged (venue␟symbol) so
  // the fetch path is untouched (display-only).
  function symNameFor(venue, symbol) {
    return symApiName(venue, symbol) || ((T && T.symName) ? T.symName(symbol) : '');
  }
  function symLabel(venue, symbol) {
    const name = symNameFor(venue, symbol);
    return name ? `${symbol} : ${name}` : symbol;
  }

  // True iff (symbol, name) matches the active search text (symbol OR name
  // substring, case-insensitive). Empty search → everything matches.
  function symMatches(venue, symbol) {
    if (!_searchText) return true;
    const name = symNameFor(venue, symbol).toLowerCase();
    return symbol.toLowerCase().includes(_searchText) || name.includes(_searchText);
  }

  // Populate the EXCHANGE filter <select> once (All + each venue present).
  function populateVenues() {
    const sel = document.getElementById('chart-venue');
    if (!sel) return;
    const prev = sel.value;
    const venues = _groups.map(g => g.venue);
    const opts = ['<option value="">All venues</option>'].concat(
      venues.map(v => `<option value="${esc(v)}">${esc(v)}</option>`));
    sel.innerHTML = opts.join('');
    if (venues.includes(prev)) sel.value = prev;
  }

  function populateSymbols() {
    const sel = document.getElementById('chart-symbol');
    if (!sel) return;
    const prev = sel.value;   // preserve the current selection when still visible
    const opts = [];
    let anyVisible = false;
    _groups.forEach(g => {
      if (_venueFilter && g.venue !== _venueFilter) return;   // ② venue filter
      const inner = (g.symbols || [])
        .filter(s => symMatches(g.venue, s.symbol))           // ① search filter
        .map(s => {
          anyVisible = true;
          return `<option value="${esc(g.venue)}␟${esc(s.symbol)}">${esc(symLabel(g.venue, s.symbol))}</option>`;
        }).join('');
      if (inner) opts.push(`<optgroup label="${esc(g.venue)}">${inner}</optgroup>`);
    });
    sel.innerHTML = opts.join('');
    if (!anyVisible) return;   // nothing matches → empty select, leave res as-is
    // Keep the prior symbol selected if it survived the filter; else fall to the
    // first visible option (so the chart always shows something coherent).
    const stillThere = Array.prototype.some.call(sel.options, o => o.value === prev);
    if (stillThere) sel.value = prev;
  }

  function load() {
    const meta = currentSymbolMeta();
    const resSel = document.getElementById('chart-res');
    if (!meta || !resSel) return;
    const url = '/api/ticker_chart?venue=' + encodeURIComponent(meta.venue)
      + '&symbol=' + encodeURIComponent(meta.symbol)
      + '&resolution=' + encodeURIComponent(resSel.value || '1m')
      + '&t=' + Date.now();
    setStatus('loading…');
    fetch(url, { cache: 'no-store' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('http ' + r.status)))
      .then(render)
      .catch(() => setStatus('fetch error'));
  }

  // Re-filter the symbol list, then reload the chart only if the active symbol
  // changed because the previous one was filtered out (avoids a redundant fetch
  // on every keystroke when the selection survives).
  function applyFilters() {
    const sel = document.getElementById('chart-symbol');
    const prev = sel ? sel.value : '';
    populateSymbols();
    if (sel && sel.value !== prev) { populateResolutions(); load(); }
  }

  function wireControls() {
    const sym = document.getElementById('chart-symbol');
    const res = document.getElementById('chart-res');
    const search = document.getElementById('chart-search');
    const venue = document.getElementById('chart-venue');
    if (sym) sym.addEventListener('change', () => { populateResolutions(); load(); });
    if (res) res.addEventListener('change', load);
    if (search) search.addEventListener('input', () => {
      _searchText = search.value.trim().toLowerCase();
      applyFilters();
    });
    if (venue) venue.addEventListener('change', () => {
      _venueFilter = venue.value;
      applyFilters();
    });
  }

  let _listLoaded = false;
  function loadSymbolList() {
    if (_listLoaded) return;
    _listLoaded = true;
    fetch('/api/ticker_chart?list=symbols&t=' + Date.now(), { cache: 'no-store' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('http ' + r.status)))
      .then(d => {
        _groups = (d && d.groups) || [];
        populateVenues();
        populateSymbols();
        populateResolutions();
        load();
      })
      .catch(() => { _listLoaded = false; setStatus('symbol list error'); });
  }

  // Renderer is called by board.js on poll/switch. We ignore the snapshot `d`
  // (the chart has its own user-driven fetch) and just make sure the pane is
  // mounted + the symbol list is loaded once.
  function renderChartTab() {
    if (!ensurePane()) return;
    loadSymbolList();
  }

  T.register('chart', renderChartTab);

  // Inject the pane as soon as the skeleton exists so a tab-switch finds it even
  // before the first render call. Retry briefly until board.js has mounted.
  function tryMount(attempt) {
    if (ensurePane() || attempt > 40) return;
    setTimeout(() => tryMount(attempt + 1), 50);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => tryMount(0));
  } else {
    tryMount(0);
  }
})();
