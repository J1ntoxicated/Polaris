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
    var rows = (s.positions || []).slice().sort(function (a, b) {
      return Math.abs(b.upnl_usd || 0) - Math.abs(a.upnl_usd || 0);
    }).slice(0, 26);
    if (posN) posN.textContent = rows.length ? '· ' + rows.length : '';
    renderSummary(rows);
    var keys = rows.map(rowKey).join('~');
    if (keys !== renderPositions._keys) {
      renderPositions._keys = keys;
      rowIndex.clear();
      posRows.innerHTML = rows.map(function (p) {
        return '<div class="r" data-k="' + esc(rowKey(p)) + '">'
          + '<span class="vb" style="background:' + vcolor(p.venue) + '"></span>'
          + '<span class="vt">' + esc(vkey(p.venue).toUpperCase()) + '</span>'
          + '<span class="sym">' + esc(String(p.symbol || '').split(':').pop()) + '</span>'
          + '<span class="vt">' + (String(p.side || '').charAt(0).toUpperCase() || '—') + '</span>'
          + '<span class="num cur vt"></span>'
          + '<span class="num pnl"></span>'
          + '<span class="num pct"></span>'
          + '<span class="num vt exp"></span>'
          + '</div>';
      }).join('');
      posRows.querySelectorAll('.r').forEach(function (el) {
        rowIndex.set(el.getAttribute('data-k'), {
          cur: el.querySelector('.cur'), pnl: el.querySelector('.pnl'),
          pct: el.querySelector('.pct'), exp: el.querySelector('.exp'),
        });
      });
    }
    rows.forEach(function (p) {
      var c = rowIndex.get(rowKey(p));
      if (!c) return;
      var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
      var pct = p.upnl_pct != null ? p.upnl_pct : null;
      setCell(c.cur, fmtPx(p.last_price), null, true);
      setCell(c.pnl, usd(u), pnlCls(u), false);
      setCell(c.pct, pct == null ? '—' : (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%', pnlCls(pct), false);
      if (c.exp && !c.exp.textContent) c.exp.textContent = kusd(p.size_usd);
    });
  }
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

  function onStream(payload) {
    (payload.events || []).forEach(function (e) {
      if (e.type === 'entry') {
        pushFeed({ ts: e.ts || Math.floor(Date.now() / 1000), kind: 'ENTRY', color: vcolor(e.exchange),
          text: base(e.ticker) + ' ' + (e.side || '') + ' · ' + (e.strategy_id || ''), val: kusd(e.size_usd), valCls: 'flat-c' });
      } else if (e.type === 'exit') {
        var pnl = e.pnl_usd || 0;
        pushFeed({ ts: e.ts || Math.floor(Date.now() / 1000), kind: 'EXIT', color: vcolor(e.exchange),
          text: base(e.ticker) + ' · ' + (e.reason || e.strategy_id || ''), val: usd(pnl), valCls: pnlCls(pnl) });
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
              text: base(t.symbol) + ' · ' + (t.exit_reason || ''), val: usd(pnl), valCls: pnlCls(pnl) });
          });
          (s.positions || []).forEach(function (pp) {
            var opened = (s.ts_now || 0) - (pp.held_sec || 0);
            seedRows.push({ ts: opened, kind: 'ENTRY', color: vcolor(pp.venue),
              text: base(pp.symbol) + ' ' + (pp.side || '') + ' · ' + (pp.strategy_id || ''), val: kusd(pp.size_usd), valCls: 'flat-c' });
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
