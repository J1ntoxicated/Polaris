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

  /* ===== OPEN POSITIONS (1s poll, memo write) ===== */
  var lastPosHtml = '';
  function renderPositions(s) {
    var rows = (s.positions || []).slice().sort(function (a, b) {
      return Math.abs(b.upnl_usd || 0) - Math.abs(a.upnl_usd || 0);
    });
    if (posN) posN.textContent = rows.length ? '· ' + rows.length : '';
    var html = rows.slice(0, 26).map(function (p) {
      var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
      var pct = p.upnl_pct != null ? p.upnl_pct : null;
      return '<div class="r">'
        + '<span class="vb" style="background:' + vcolor(p.venue) + '"></span>'
        + '<span class="vt">' + esc(vkey(p.venue).toUpperCase()) + '</span>'
        + '<span class="sym">' + esc(String(p.symbol || '').split(':').pop()) + '</span>'
        + '<span class="vt">' + (String(p.side || '').charAt(0).toUpperCase() || '—') + '</span>'
        + '<span class="num ' + pnlCls(u) + '">' + usd(u) + '</span>'
        + '<span class="num ' + pnlCls(pct) + '">' + (pct == null ? '—' : (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%') + '</span>'
        + '<span class="num vt">' + kusd(p.size_usd) + '</span>'
        + '</div>';
    }).join('');
    if (html !== lastPosHtml) { posRows.innerHTML = html; lastPosHtml = html; }
  }

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
          (s.recent_trades || []).slice(0, 15).reverse().forEach(function (t) {
            var pnl = t.net_usd != null ? t.net_usd : t.pnl_usd;
            pushFeed({ ts: t.ts_close || t.ts || 0, kind: 'EXIT', color: vcolor(t.venue),
              text: base(t.symbol) + ' · ' + (t.exit_reason || ''), val: usd(pnl), valCls: pnlCls(pnl) });
          });
        }
      })
      .catch(function () { /* display-only */ });
  }
  poll();
  setInterval(poll, 1000);
  if (window.PolarisEvents) window.PolarisEvents.on(onStream);
})();
