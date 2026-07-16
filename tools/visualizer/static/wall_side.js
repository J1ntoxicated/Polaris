/* Polaris FLOW — left work pane (Jin 2026-07-10 "모바일 빼고 반 갈라서").
 * Top half: live OPEN POSITIONS (1s /api/snapshot poll — same ledger the
 * board's Activity tab reads). Bottom half: ACTIVITY feed — REAL trade/gate
 * events scrolling up jarvis-style (SSE via the shared PolarisEvents bus,
 * seeded from snapshot.recent_trades). The wall-bottom BOT LOG remains the
 * raw system log; this pane is the human-readable activity stream.
 * Color contract: green/red = money only; venues = OKX cyan / CAP violet /
 * ALP amber; system rows = steel. Display-only. English UI.
 */
(function () {
  var posRows = document.getElementById('side-pos-rows');
  var posN = document.getElementById('side-pos-n');
  var actRows = document.getElementById('side-act-rows');
  var chartHdr = document.getElementById('side-chart-hdr');
  var chartBody = document.getElementById('side-chart-body');
  if (!posRows || !actRows) return; // ?nomobile=1 — pane absent

  var VCOLOR = { okx: '#5fdfff', cap: '#a87cff', alp: '#ffc84f' };
  function vkey(v) { return String(v || '').slice(0, 3).toLowerCase(); }
  function vcolor(v) { return VCOLOR[vkey(v)] || '#8a94b0'; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }
  function pnlCls(n) { return n > 0 ? 'pos-c' : n < 0 ? 'neg-c' : 'flat-c'; }
  function usd(n) {
    if (n == null || isNaN(n)) return '—';
    var s = n < 0 ? '-$' : '+$';
    return s + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: n === 0 ? 0 : 2, minimumFractionDigits: 0 });
  }
  function kusd(n) {
    if (n == null || isNaN(n)) return '—';
    var a = Math.abs(n);
    return '$' + (a >= 1000 ? (a / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k' : a.toFixed(0));
  }
  function hhmmss(ts) {
    var d = new Date(ts * 1000);
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' + ('0' + d.getSeconds()).slice(-2);
  }

  /* ===== OPEN POSITIONS — keyed rows, per-cell live ticks =====
   * Structure rebuilds only when the position SET changes (1s poll); price/
   * PnL cells update in place — from the poll AND from /stream/prices (the
   * bot's live marks diffed at 4Hz), so prices tick in true real time with
   * an up/down flash. Key = venue|symbol|strategy|side (server's SSE key). */
  var rowIndex = new Map(); // key -> {cur, pnl, pct}
  var grpIndex = new Map(); // groupKey -> {pnl, exp, caret} — 접힘 헤더 라이브 갱신
  var lastSnap = null;      // 토글 시 즉시 재렌더용
  var expanded = new Set(JSON.parse(localStorage.getItem('polaris_flow_grp_open') || '[]'));
  function saveExpanded() {
    localStorage.setItem('polaris_flow_grp_open', JSON.stringify(Array.from(expanded)));
  }
  var sumEl = document.getElementById('side-pos-sum');
  function renderSummary(rows) {
    if (!sumEl) return;
    var upnl = 0, size = 0, vc = { okx: 0, cap: 0, alp: 0 };
    rows.forEach(function (p) {
      upnl += p.upnl_usd || 0;
      size += p.size_usd || 0;
      var k = vkey(p.venue);
      if (vc[k] != null) vc[k]++;
    });
    var html = '<span>Σ uPnL <b class="' + pnlCls(upnl) + '">' + usd(upnl) + '</b></span>'
      + '<span>exp <b>' + kusd(size) + '</b></span>'
      + '<span class="vc" style="color:' + VCOLOR.okx + '">OKX ' + vc.okx + '</span>'
      + '<span class="vc" style="color:' + VCOLOR.cap + '">CAP ' + vc.cap + '</span>'
      + '<span class="vc" style="color:' + VCOLOR.alp + '">ALP ' + vc.alp + '</span>';
    if (html !== renderSummary._last) { sumEl.innerHTML = html; renderSummary._last = html; }
  }
  function fmtPx(n) {
    if (n == null || isNaN(n)) return '—';
    var a = Math.abs(n);
    return n.toFixed(a >= 1000 ? 0 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6);
  }
  function rowKey(p) {
    return String(p.venue || '') + '|' + String(p.symbol || '') + '|' + String(p.strategy_id || '') + '|' + String(p.side || '');
  }
  // Inline per-row spark (Jin 2026-07-15 dashboard-todo P4) — the position's
  // own spark[30pt] (already on the snapshot row), coloured by uPnL sign
  // (money-only green/red contract), never by trend direction.
  function rowSparkSvg(p) {
    var arr = Array.isArray(p.spark) ? p.spark : [];
    if (arr.length < 2) return '';
    var w = 40, h = 12, pad = 1, n = arr.length;
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < n; i++) { if (arr[i] < lo) lo = arr[i]; if (arr[i] > hi) hi = arr[i]; }
    if (!isFinite(lo) || !isFinite(hi)) return '';
    var span = (hi - lo) || 1, iw = w - pad * 2, ih = h - pad * 2;
    var pts = arr.map(function (v, idx) {
      var x = pad + (n === 1 ? 0 : (idx / (n - 1)) * iw);
      var y = pad + ih - ((v - lo) / span) * ih;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
    var col = u > 0 ? '#7dffa8' : u < 0 ? '#ff7d8a' : '#8a94b0';
    return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">'
      + '<polyline points="' + pts + '" fill="none" stroke="' + col + '" stroke-width="1" stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }
  // CHART inspect-panel selection (Jin 2026-07-15 dashboard-todo P4): default =
  // auto-follow the most-recently-opened position (min held_sec); clicking a
  // row pins it (CHART_STATE.sel) until a genuinely NEW top position appears
  // (topKey changes), which unpins and returns to auto-follow.
  var CHART_STATE = { sel: null, topKey: null };
  function chartPick(all) {
    if (!all.length) { CHART_STATE.topKey = null; return null; }
    var top = all[0];
    for (var i = 1; i < all.length; i++) {
      var hs = all[i].held_sec == null ? Infinity : all[i].held_sec;
      var th = top.held_sec == null ? Infinity : top.held_sec;
      if (hs < th) top = all[i];
    }
    var tk = rowKey(top);
    if (tk !== CHART_STATE.topKey) { CHART_STATE.topKey = tk; CHART_STATE.sel = null; }
    if (CHART_STATE.sel) {
      for (var j = 0; j < all.length; j++) { if (rowKey(all[j]) === CHART_STATE.sel) return all[j]; }
      CHART_STATE.sel = null; // pinned position closed — fall back to auto-follow
    }
    return top;
  }
  function regimeLabel(p) {
    var a = p.entry_regime, b = p.regime;
    if (a && b && a !== b) return esc(a) + ' → ' + esc(b);
    return esc(b || a || '');
  }
  // Lightweight inline inspect chart — spark price line + entry(dotted steel)/
  // stop(dashed red) levels + MFE/MAE band (R-multiples converted to price via
  // R = |entry - stop|, the same risk unit the position was sized against) +
  // current-price marker. Deep multi-indicator charting lives on the board;
  // this is a glance-only reimplementation, not a rebuild of it.
  function buildChartSvg(p) {
    var arr = Array.isArray(p.spark) && p.spark.length >= 2 ? p.spark.slice() : null;
    var entry = p.entry_price || null;
    var lastPx = p.last_price || (arr ? arr[arr.length - 1] : entry);
    if (!arr) arr = [entry, lastPx].filter(function (v) { return v != null; });
    if (arr.length < 2) return '<div class="side-chart-empty">no price history yet</div>';
    var stop = p.stop_price ? p.stop_price : null;
    var dir = String(p.side || '').toLowerCase() === 'short' ? -1 : 1;
    var R = (entry != null && stop != null) ? Math.abs(entry - stop) : null;
    var mfe = (R != null && p.mfe_atr_r != null) ? entry + dir * p.mfe_atr_r * R : null;
    var mae = (R != null && p.mae_atr_r != null) ? entry + dir * p.mae_atr_r * R : null;

    var vals = arr.slice();
    if (entry != null) vals.push(entry);
    if (stop != null) vals.push(stop);
    if (mfe != null) vals.push(mfe);
    if (mae != null) vals.push(mae);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var padSpan = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1;
    lo -= padSpan; hi += padSpan;
    var span = (hi - lo) || 1;

    var W = 400, H = 130, PADX = 6, PADY = 8, n = arr.length;
    var iw = W - PADX * 2, ih = H - PADY * 2;
    function xOf(i) { return PADX + (n === 1 ? 0 : (i / (n - 1)) * iw); }
    function yOf(v) { return PADY + ih - ((v - lo) / span) * ih; }
    function hline(y, col, dash, w) {
      var yy = y.toFixed(1);
      return '<line x1="' + PADX + '" y1="' + yy + '" x2="' + (W - PADX) + '" y2="' + yy + '" stroke="' + col + '" stroke-width="' + w + '" stroke-dasharray="' + dash + '" opacity="0.85"/>';
    }

    var pts = arr.map(function (v, i) { return xOf(i).toFixed(1) + ',' + yOf(v).toFixed(1); }).join(' ');
    var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
    var lineCol = u > 0 ? '#7dffa8' : u < 0 ? '#ff7d8a' : '#9fc0ff';

    var svg = '';
    if (mfe != null && mae != null) {
      var yTop = Math.min(yOf(mfe), yOf(mae)), yBot = Math.max(yOf(mfe), yOf(mae));
      svg += '<rect x="' + PADX + '" y="' + yTop.toFixed(1) + '" width="' + iw + '" height="' + (yBot - yTop).toFixed(1) + '" fill="rgba(159,192,255,0.07)"/>';
    }
    if (mfe != null) svg += hline(yOf(mfe), '#7dffa8', '2 2', 0.8);
    if (mae != null) svg += hline(yOf(mae), '#ff7d8a', '2 2', 0.8);
    if (entry != null) svg += hline(yOf(entry), '#8a94b0', '3 2', 1);
    if (stop != null) svg += hline(yOf(stop), '#ff4d5e', '4 2', 1);
    svg += '<polyline points="' + pts + '" fill="none" stroke="' + lineCol + '" stroke-width="1.25" stroke-linejoin="round" stroke-linecap="round"/>';
    svg += '<circle cx="' + xOf(n - 1).toFixed(1) + '" cy="' + yOf(arr[n - 1]).toFixed(1) + '" r="2.5" fill="' + lineCol + '"/>';
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">' + svg + '</svg>';
  }
  function renderChart(p) {
    if (!chartHdr || !chartBody) return;
    if (!p) {
      chartHdr.innerHTML = '';
      chartBody.innerHTML = '<div class="side-chart-empty">no open position</div>';
      return;
    }
    var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
    var hdrHtml = '<span class="vt" style="color:' + vcolor(p.venue) + '">' + esc(vkey(p.venue).toUpperCase()) + '</span>'
      + '<span class="sym">' + esc(String(p.symbol || '').split(':').pop()) + '</span>'
      + (p.strategy_id ? '<span class="strat">' + esc(p.strategy_id) + '</span>' : '')
      + '<span class="side">' + esc(String(p.side || '').toUpperCase()) + '</span>'
      + '<span class="num ' + pnlCls(u) + '">' + usd(u) + '</span>'
      + '<span class="reg">' + regimeLabel(p) + '</span>';
    if (hdrHtml !== chartHdr.__last) { chartHdr.innerHTML = hdrHtml; chartHdr.__last = hdrHtml; }
    chartBody.innerHTML = buildChartSvg(p);
  }
  function setCell(el, txt, cls, flash) {
    if (!el) return;
    if (el.textContent !== txt) {
      var up = flash && parseFloat(txt.replace(/[^0-9.-]/g, '')) > parseFloat(el.textContent.replace(/[^0-9.-]/g, '') || '0');
      el.textContent = txt;
      var nowMs = Date.now();
      if (flash && (!el.__lastFx || nowMs - el.__lastFx > 4000)) {
        el.__lastFx = nowMs;
        el.classList.remove('fx-up', 'fx-dn');
        void el.offsetWidth;
        el.classList.add(up ? 'fx-up' : 'fx-dn');
      }
    }
    if (cls != null && el.className.indexOf(cls) < 0) el.className = 'num ' + cls;
  }
  function renderPositions(s) {
    lastSnap = s;
    // 티커 그룹 = 기본 접힘 (Jin 2026-07-12 "묶어서·접어서, 눌러서 펴게"):
    // 그룹(>1)은 Σ 요약 한 줄만, 클릭으로 펼침(localStorage 유지). 행은
    // 포지션(전략)별 유지 — 펼치면 그대로 보임.
    var all = (s.positions || []).slice();
    var groups = {};
    all.forEach(function (p) {
      var gk = String(p.venue || '') + '|' + String(p.symbol || '');
      (groups[gk] = groups[gk] || []).push(p);
    });
    var ordered = Object.keys(groups).map(function (gk) {
      var g = groups[gk];
      var upnl = g.reduce(function (a, p) { return a + (p.upnl_usd || 0); }, 0);
      g.sort(function (a, b) { return Math.abs(b.upnl_usd || 0) - Math.abs(a.upnl_usd || 0); });
      return { gk: gk, g: g, upnl: upnl };
    }).sort(function (a, b) { return Math.abs(b.upnl) - Math.abs(a.upnl); });
    if (posN) posN.textContent = all.length ? '· ' + all.length : '';
    renderSummary(all);
    var selP = chartPick(all);
    var selKey = selP ? rowKey(selP) : null;
    // 가시 목록: 그룹>1 = 헤더(+펼침 시 자식), 단독 = 행 — 캡 26 가시행
    var visible = [];
    ordered.forEach(function (o) {
      if (o.g.length > 1) {
        visible.push({ hdr: o });
        if (expanded.has(o.gk)) o.g.forEach(function (p) { visible.push({ row: p, ingrp: true }); });
      } else {
        visible.push({ row: o.g[0] });
      }
    });
    visible = visible.slice(0, 26);
    var keys = visible.map(function (v) {
      return v.hdr ? 'H:' + v.hdr.gk + ':' + v.hdr.g.length : rowKey(v.row) + (v.ingrp ? ':i' : '');
    }).join('~') + '|x:' + Array.from(expanded).join(',');
    if (keys !== renderPositions._keys) {
      renderPositions._keys = keys;
      rowIndex.clear(); grpIndex.clear();
      var html = '';
      visible.forEach(function (v) {
        if (v.hdr) {
          var o = v.hdr;
          var open = expanded.has(o.gk);
          html += '<div class="r grp" data-g="' + esc(o.gk) + '" title="click to ' + (open ? 'collapse' : 'expand') + '">'
            + '<span class="vb" style="background:' + vcolor(o.g[0].venue) + '"></span>'
            + '<span class="vt caret">' + (open ? '▾' : '▸') + '</span>'
            + '<span class="sym"><span class="vt" style="color:' + vcolor(o.g[0].venue) + '">' + esc(vkey(o.g[0].venue).toUpperCase()) + '</span> '
            + esc(String(o.g[0].symbol || '').split(':').pop())
            + ' ×' + o.g.length + '</span><span></span><span class="num vt"></span>'
            + '<span class="num gpnl"></span>'
            + '<span class="num"></span><span class="num vt gexp"></span><span></span></div>';
          return;
        }
        var p = v.row;
        html += '<div class="r pos' + (v.ingrp ? ' ingrp' : '') + '" data-k="' + esc(rowKey(p)) + '">'
          + '<span class="vb" style="background:' + vcolor(p.venue) + '"></span>'
          + '<span class="vt">' + esc(vkey(p.venue).toUpperCase()) + '</span>'
          + '<span class="sym">' + esc(String(p.symbol || '').split(':').pop()) + '</span>'
          + '<span class="vt">' + (String(p.side || '').charAt(0).toUpperCase() || '—') + '</span>'
          + '<span class="num cur vt"></span>'
          + '<span class="num pnl"></span>'
          + '<span class="num pct"></span>'
          + '<span class="num vt exp"></span>'
          + '<span class="num spk"></span>'
          + '</div>';
      });
      posRows.innerHTML = html;
      posRows.querySelectorAll('.r[data-k]').forEach(function (el) {
        rowIndex.set(el.getAttribute('data-k'), {
          cur: el.querySelector('.cur'), pnl: el.querySelector('.pnl'),
          pct: el.querySelector('.pct'), exp: el.querySelector('.exp'),
          spk: el.querySelector('.spk'), el: el,
        });
      });
      posRows.querySelectorAll('.r.grp').forEach(function (el) {
        grpIndex.set(el.getAttribute('data-g'), {
          pnl: el.querySelector('.gpnl'), exp: el.querySelector('.gexp'),
        });
      });
    }
    // 라이브 셀 갱신 — 보이는 개별 행
    all.forEach(function (p) {
      var c = rowIndex.get(rowKey(p));
      if (!c) return;
      var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
      var pct = p.upnl_pct != null ? p.upnl_pct : null;
      setCell(c.cur, fmtPx(p.last_price), null, true);
      setCell(c.pnl, usd(u), pnlCls(u), false);
      setCell(c.pct, pct == null ? '—' : (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%', pnlCls(pct), false);
      if (c.exp && !c.exp.textContent) c.exp.textContent = kusd(p.size_usd);
      if (c.spk) {
        var spkSvg = rowSparkSvg(p);
        if (spkSvg !== c.spk.__lastSvg) { c.spk.innerHTML = spkSvg; c.spk.__lastSvg = spkSvg; }
      }
      if (c.el) c.el.classList.toggle('sel', rowKey(p) === selKey);
    });
    // 접힘 그룹 헤더 Σ 라이브 갱신
    ordered.forEach(function (o) {
      if (o.g.length < 2) return;
      var h = grpIndex.get(o.gk);
      if (!h) return;
      var gsz = o.g.reduce(function (a, p) { return a + (p.size_usd || 0); }, 0);
      setCell(h.pnl, usd(o.upnl), pnlCls(o.upnl), false);
      if (h.exp) h.exp.textContent = kusd(gsz);
    });
    renderChart(selP);
  }
  // 그룹 접기/펼치기 + 포지션 행 클릭 = CHART 핀 (위임 1회)
  posRows.addEventListener('click', function (e) {
    var g = e.target.closest ? e.target.closest('.r.grp') : null;
    if (g) {
      var gk = g.getAttribute('data-g');
      if (expanded.has(gk)) expanded.delete(gk); else expanded.add(gk);
      saveExpanded();
      renderPositions._keys = null; // 구조 재렌더 강제
      if (lastSnap) renderPositions(lastSnap);
      return;
    }
    var row = e.target.closest ? e.target.closest('.r.pos') : null;
    if (!row) return;
    var k = row.getAttribute('data-k');
    if (!k) return;
    CHART_STATE.sel = k;
    if (lastSnap) renderPositions(lastSnap); // re-syncs .sel highlight + CHART panel, no rebuild
  });
  // live 4Hz price marks — the "가격 변하는 거 실시간" channel
  try {
    var pes = new EventSource('/stream/prices');
    pes.onmessage = function (ev) {
      var d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      (d.prices || []).forEach(function (m) {
        var c = rowIndex.get(m.key);
        if (!c) return;
        setCell(c.cur, fmtPx(m.last_price), null, true);
        if (m.upnl_usd != null) setCell(c.pnl, usd(m.upnl_usd), pnlCls(m.upnl_usd), false);
        if (m.upnl_pct != null) setCell(c.pct, (m.upnl_pct >= 0 ? '+' : '') + m.upnl_pct.toFixed(1) + '%', pnlCls(m.upnl_pct), false);
      });
    };
  } catch (e) { /* SSE unavailable — 1s poll still ticks */ }

  /* ===== ACTIVITY feed (SSE + seed) ===== */
  var feed = []; // {ts, kind, color, text, val, valCls}
  var MAX_FEED = 60;
  function pushFeed(row) {
    feed.unshift(row);
    if (feed.length > MAX_FEED) feed.pop();
    renderFeed();
  }
  function renderFeed() {
    actRows.innerHTML = feed.map(function (r) {
      return '<div class="r">'
        + '<span class="t">' + hhmmss(r.ts) + '</span>'
        + '<span class="k" style="color:' + r.color + '">' + esc(r.kind) + '</span>'
        + '<span>' + esc(r.text) + '</span>'
        + '<span class="num ' + (r.valCls || 'flat-c') + '">' + esc(r.val || '') + '</span>'
        + '</div>';
    }).join('');
  }
  function base(sym) { return String(sym || '').split(':').pop(); }
  // Jin 2026-07-15 "액티비티 로그 좀더 디스크립티브하게": venue tag + hold
  // duration + R-multiple, parts auto-drop when a field is absent.
  function vlab(v) { return String(v || '').slice(0, 3).toUpperCase(); }
  function dur(sec) { sec = +sec || 0; return sec < 60 ? Math.round(sec) + 's' : sec < 3600 ? Math.round(sec / 60) + 'm' : (sec / 3600).toFixed(1) + 'h'; }
  function jp(parts) { return parts.filter(function (p) { return p != null && p !== ''; }).join(' · '); }
  function fmtPx(p) { p = +p || 0; return p >= 100 ? p.toFixed(1) : p >= 1 ? p.toFixed(2) : p.toPrecision(3); }
  function rtag(r) { return r == null ? '' : ' ' + (r >= 0 ? '+' : '') + (+r).toFixed(1) + 'R'; }

  function onStream(payload) {
    (payload.events || []).forEach(function (e) {
      if (e.type === 'entry') {
        pushFeed({ ts: e.ts || Math.floor(Date.now() / 1000), kind: 'ENTRY', color: vcolor(e.exchange),
          text: jp([vlab(e.exchange) + ' ' + base(e.ticker) + ' ' + (e.side || ''), e.strategy_id, e.entry_price ? '@' + fmtPx(e.entry_price) : '']),
          val: kusd(e.size_usd), valCls: 'flat-c' });
      } else if (e.type === 'exit') {
        var pnl = e.pnl_usd || 0;
        pushFeed({ ts: e.ts || Math.floor(Date.now() / 1000), kind: 'EXIT', color: vcolor(e.exchange),
          text: jp([vlab(e.exchange) + ' ' + base(e.ticker), e.strategy_id, e.reason, e.held_sec ? dur(e.held_sec) : '']),
          val: usd(pnl) + rtag(e.r_units), valCls: pnlCls(pnl) });
      }
    });
    (payload.gate_events || []).forEach(function (g) {
      var ts = g.ts || Math.floor(Date.now() / 1000);
      if (g.gate_id === 2) {
        pushFeed({ ts: ts, kind: 'SIG', color: '#8fb0c8', text: base(g.symbol) + ' · ' + (g.strategy || ''), val: '', valCls: 'flat-c' });
      } else if (g.gate_id === 5 && g.decision === 'SIZED') {
        pushFeed({ ts: ts, kind: 'SIZED', color: '#ffe066', text: base(g.symbol) + ' · ' + (g.strategy || ''), val: '', valCls: 'flat-c' });
      } else if (g.decision === 'KILL') {
        pushFeed({ ts: ts, kind: 'KILL', color: '#7a7f8c', text: 'g' + g.gate_id + ' ' + base(g.symbol) + (g.reason ? ' · ' + g.reason : ''), val: '', valCls: 'flat-c' });
      }
    });
  }

  var seeded = false;
  function poll() {
    fetch('/api/snapshot?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        renderPositions(s);
        if (!seeded) {
          seeded = true;
          var seedRows = [];
          (s.recent_trades || []).slice(0, 12).forEach(function (t) {
            var pnl = t.net_usd != null ? t.net_usd : t.pnl_usd;
            seedRows.push({ ts: t.ts_close || 0, kind: 'EXIT', color: vcolor(t.venue),
              text: jp([vlab(t.venue) + ' ' + base(t.symbol), t.strategy_id, t.exit_reason, t.held_sec ? dur(t.held_sec) : '']),
              val: usd(pnl) + rtag(t.r_units), valCls: pnlCls(pnl) });
          });
          (s.positions || []).forEach(function (pp) {
            var opened = (s.ts_now || 0) - (pp.held_sec || 0);
            seedRows.push({ ts: opened, kind: 'ENTRY', color: vcolor(pp.venue),
              text: jp([vlab(pp.venue) + ' ' + base(pp.symbol) + ' ' + (pp.side || ''), pp.strategy_id, pp.entry_price ? '@' + fmtPx(pp.entry_price) : '', pp.held_sec ? dur(pp.held_sec) : '']),
              val: kusd(pp.size_usd), valCls: 'flat-c' });
          });
          seedRows.sort(function (a, b) { return a.ts - b.ts; }).forEach(pushFeed);
        }
      })
      .catch(function () { /* display-only */ });
  }
  poll();
  setInterval(poll, 1000);
  // events_bus는 이 스크립트보다 늦게 초기화될 수 있음 — 재시도 구독
  (function sub() {
    if (window.PolarisEvents) { window.PolarisEvents.on(onStream); return; }
    setTimeout(sub, 500);
  })();
})();
