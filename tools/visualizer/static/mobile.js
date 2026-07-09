// Polaris mobile page — lightweight, independent of the desktop board_*.js.
// Polls /api/snapshot every 1s (matches desktop; snapshot is ~1ms + upnl is
// recomputed live from latest prices, so P&L ticks in real time) and paints a
// single-column iPhone view focused on
// the two things Jin watches on the phone: OPEN POSITIONS + live GATE ACTIVITY.
// A compact status strip (equity + today P&L) sits above; the equity/win-rate/PF/
// drawdown stat block and the venues/recent-trades lists are intentionally dropped
// to keep the screen scannable. DEMO/PAPER, display-only. English chrome; live
// data (symbols/strategy ids) may be venue-native. No new colors — #board palette.
'use strict';

(function () {
  var POLL_MS = 1000;
  var $ = function (id) { return document.getElementById(id); };

  function usd(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '-' : '';
    var a = Math.abs(n);
    return sign + '$' + a.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }
  function signed(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    // usd() already prefixes '-$...' for negatives — keep the minus so losses
    // read as losses; only add '+' for non-negatives.
    return n >= 0 ? '+' + usd(n) : usd(n);
  }
  function pnlClass(n) {
    if (!n) return 'flat';
    return n > 0 ? 'pos' : 'neg';
  }
  // Compact USD for exposure (size_usd): 1580 → "$1.6k", 158000 → "$158k", 950 → "$950".
  function kusd(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '-' : '';
    var a = Math.abs(n);
    if (a >= 1000) return sign + '$' + (a / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k';
    return sign + '$' + a.toFixed(0);
  }
  // Price with scale-adaptive precision (BTC 108250 · AUDUSD 0.6543 · $5.02).
  function fmtPx(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var a = Math.abs(n);
    var dp = a >= 1000 ? 0 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
    return n.toFixed(dp);
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  // Venue normalizer + short tag — mirrors board_exchange.normVenue (mobile.js is
  // standalone, no board_*.js import). Tolerates full ('capital') + short ('cap')
  // forms. Returns {key, tag}: key drives the --stream-a/b/c colour class
  // (v-okx/v-capital/v-alpaca), tag is the uppercase chip text (OKX/CAP/ALP).
  function venueInfo(v) {
    var m = String(v || '').toLowerCase();
    if (m === 'okx') return { key: 'okx', tag: 'OKX' };
    if (m === 'cap' || m === 'capital') return { key: 'capital', tag: 'CAP' };
    if (m === 'alp' || m === 'alpaca') return { key: 'alpaca', tag: 'ALP' };
    return { key: 'other', tag: (m ? m.slice(0, 3).toUpperCase() : '—') };
  }
  // hh:mm:ss — matches the desktop gate feed (board_tabs_ext hhmmss).
  function hhmmss(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var hh = ('0' + d.getHours()).slice(-2);
    var mm = ('0' + d.getMinutes()).slice(-2);
    var ss = ('0' + d.getSeconds()).slice(-2);
    return hh + ':' + mm + ':' + ss;
  }

  // Verdict mapping mirrors desktop board_tabs_ext.gateVerdict — keep the
  // verbatim decision verb, colour by FAMILY (flow_not_block: gates emit
  // decisions, not pass/kill). green pass-through / cyan HOLD / amber intervene
  // / red hard exit.
  function gateVerdict(e) {
    var raw = String(e.decision || e.verdict || e.action || '').toUpperCase();
    if (!raw) return { txt: '—', cls: 'other' };
    if (/EXIT_NOW|KILL|REJECT|STOP_HIT/.test(raw)) return { txt: raw, cls: 'kill' };
    if (/ADJUST|WIDEN|SWAP/.test(raw)) return { txt: raw, cls: 'other' };
    if (/PASS|PROCEED|SIZED|REFLECTED|ADMIT|VALID|ENTER/.test(raw)) return { txt: raw, cls: 'pass' };
    if (/HOLD/.test(raw)) return { txt: raw, cls: 'info' };
    return { txt: raw, cls: 'other' };
  }

  // Clean trading symbol from a probe row. Backend parses
  // pos_<hash>_<venue>_<SYMBOL>_<ts> now, but defend in JS so a raw 40-char id
  // never leaks into a cell. Returns {sym, venue}.
  var POS_ID_RE = /_(okx|capital|alpaca)_(.+)_\d+$/i;
  function probeSym(pr) {
    var raw = String(pr.ticker || pr.symbol || '');
    var venue = pr.venue || '';
    var sym = raw;
    var m = raw.match(POS_ID_RE);
    if (m) { if (!venue) venue = m[1].toLowerCase(); sym = m[2]; }
    return { sym: sym, venue: venue };
  }
  // Normalize the probe stream into clean rows (NOT merged into the gate feed —
  // probes get their own section). Returns [] when absent (graceful).
  function probeRows(s) {
    var out = [];
    var pe = s.probe_events || s.probes || [];
    for (var i = 0; i < pe.length; i++) {
      var pr = pe[i] || {};
      var vs = probeSym(pr);
      out.push({
        name: pr.name || pr.probe || pr.id || 'probe',
        sym: vs.sym, venue: vs.venue,
        kind: pr.kind || '',
        lean: (pr.lean != null) ? +pr.lean : null,
        conf: (pr.confidence != null) ? +pr.confidence : null,
        action: pr.action || '',
        ts: pr.ts || pr.ts_unix || 0,
      });
    }
    return out;
  }

  // SINCE RESET — forward edge since the latest main-logic reset. Mirrors the
  // desktop board sinceResetHtml, condensed to one phone line. Hidden when no
  // reset is stamped. Net/PF colour by winning (pf>=1 or net>=0).
  // VIRTUAL mode (Jin 2026-07-08 dashboard-live-net fix): reads
  // ``virtual_since_reset`` (fresh $100k×3 ledger, per-venue anchor) instead
  // of the LEGACY ``since_reset`` (unfiltered fills scan, pre-virtual history
  // mixed in) — this is the main board Jin actually watches on the phone, so
  // it must show the SAME scope the mode banner claims, never the legacy one.
  function paintSinceReset(s) {
    var el = $('sreset'); if (!el) return;
    var virt = !!s.virtual_account_enabled;
    var sr = virt ? s.virtual_since_reset : s.since_reset;
    if (!sr) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    var pf = (sr.pf >= 9.99) ? '∞' : (sr.pf || 0).toFixed(2);
    var winning = (sr.pf != null && sr.pf >= 1) || (sr.net_usd >= 0);
    var netCls = winning ? 'pos' : 'neg';
    var lbl = virt ? 'virtual ledger' : (sr.label ? esc(sr.label) : 'reset');
    var tag = virt ? 'SINCE VIRTUAL RESET' : 'SINCE RESET';
    el.innerHTML =
      '<span class="tag">' + tag + '</span>' +
      '<span class="lbl" title="' + lbl + '">' + lbl + '</span>' +
      '<span class="kv">Net<b class="' + netCls + '">' + signed(sr.net_usd) + '</b></span>' +
      '<span class="kv">Trades<b>' + (sr.n || 0) + '</b></span>' +
      '<span class="kv">Win<b>' + (sr.win_pct || 0).toFixed(0) + '%</b></span>' +
      '<span class="kv">PF<b class="' + netCls + '">' + pf + '</b></span>';
  }

  // Recent Trades — TIME · SYM · side · P&L · R · why (newest first).
  function paintRecentTrades(s) {
    var body = $('recenttrades'); if (!body) return;
    var rt = (s.recent_trades || s.trades || []).slice()
      .sort(function (a, b) { return (b.ts_close || 0) - (a.ts_close || 0); });
    if ($('rt-cnt')) $('rt-cnt').textContent = rt.length || '';
    if (!rt.length) { body.innerHTML = '<div class="empty">no closed trades yet</div>'; return; }
    var hdr = '<div class="thr rt-grid"><span>TIME</span><span>SYM</span><span class="ta-c">S</span>' +
      '<span class="ta-r">P&amp;L</span><span class="ta-r">R</span><span class="ta-r">WHY</span></div>';
    body.innerHTML = hdr + rt.slice(0, 60).map(function (t) {
      var tstr = t.ts_close ? hhmmss(t.ts_close) : '';
      // issue 2: tag by POSITION direction (long/short), not the close FILL
      // side (sell = a long exit, which mislabelled longs as shorts). Fall back
      // to the close-side inference when position_side is absent (legacy rows).
      var ps = String(t.position_side || '').toLowerCase();
      var isShort = ps ? (ps.charAt(0) === 's')
        : (String(t.side_close || '').toLowerCase().charAt(0) === 'b');
      var sideTag = isShort ? 'S' : 'L';
      var r = (t.r_units != null) ? ((t.r_units >= 0 ? '+' : '') + t.r_units.toFixed(2) + 'R') : '—';
      return '<div class="tr rt-grid" title="' + esc(t.symbol) + ' ' + esc(t.strategy_id) +
          ' · ' + esc(t.exit_reason) + ' · ' + esc(tstr) + '">' +
        '<span class="t num">' + esc(tstr) + '</span>' +
        '<span class="sym" title="' + esc(t.symbol) + '">' + esc(t.symbol) + '</span>' +
        '<span class="s ' + (sideTag === 'S' ? 'side-short' : 'side-long') + '">' + sideTag + '</span>' +
        '<span class="pl num ' + pnlClass(t.pnl_usd) + '">' + signed(t.pnl_usd) + '</span>' +
        '<span class="r num">' + r + '</span>' +
        '<span class="why" title="' + esc(t.exit_reason) + '">' + esc(t.exit_reason || '—') + '</span>' +
        '</div>';
    }).join('');
  }

  // LOGIC tab — per-strategy edge + learners + regime, all from the snapshot.
  function paintLogic(s) {
    var sl = $('stratlist');
    if (sl) {
      var ss = (s.strategy_stats || []).slice()
        .sort(function (a, b) { return (b.pnl_usd || 0) - (a.pnl_usd || 0); });
      if ($('strat-cnt')) $('strat-cnt').textContent = ss.length || '';
      if (!ss.length) { sl.innerHTML = '<div class="empty">no strategy stats</div>'; }
      else {
        var sh = '<div class="thr kv-grid"><span>STRATEGY</span><span class="ta-r">WR</span><span class="ta-r">PF</span><span class="ta-r">P&amp;L</span></div>';
        sl.innerHTML = sh + ss.map(function (x) {
          var pf = (x.pf >= 9.99) ? '∞' : (x.pf || 0).toFixed(1);
          return '<div class="tr kv-grid" title="' + esc(x.strategy_id) + ' · n=' + (x.closed_n || 0) + '">' +
            '<span class="nm" title="' + esc(x.strategy_id) + '">' + esc(x.strategy_id) + '</span>' +
            '<span class="a num">' + (x.wr_pct || 0).toFixed(0) + '%</span>' +
            '<span class="b num">' + pf + '</span>' +
            '<span class="c num ' + pnlClass(x.pnl_usd) + '">' + signed(x.pnl_usd) + '</span>' +
            '</div>';
        }).join('');
      }
    }
    var ll = $('lrnlist');
    if (ll) {
      var lr = s.learners || [];
      if ($('lrn-cnt')) $('lrn-cnt').textContent = lr.length || '';
      if (!lr.length) { ll.innerHTML = '<div class="empty">no learners</div>'; }
      else {
        var lh = '<div class="thr kv-grid"><span>LEARNER</span><span class="ta-r">VAL</span><span class="ta-r">Δ1h</span><span class="ta-r">n</span></div>';
        ll.innerHTML = lh + lr.map(function (x) {
          return '<div class="tr kv-grid" title="' + esc(x.learner_id) + ' ' + esc(x.key) + '">' +
            '<span class="nm" title="' + esc(x.key) + '">' + esc(x.key || x.learner_id) + '</span>' +
            '<span class="a num">' + (+x.value || 0).toFixed(2) + '</span>' +
            '<span class="b num ' + pnlClass(x.delta_1h) + '">' + ((x.delta_1h >= 0 ? '+' : '') + (+x.delta_1h || 0).toFixed(2)) + '</span>' +
            '<span class="c num">' + Math.round(x.n_eff || 0) + '</span>' +
            '</div>';
        }).join('');
      }
    }
    var rl = $('rgmlist');
    if (rl) {
      var rb = (s.regime_bars || []).slice().sort(function (a, b) { return (b.count || 0) - (a.count || 0); });
      if ($('rgm-cnt')) $('rgm-cnt').textContent = rb.length || '';
      if (!rb.length) { rl.innerHTML = '<div class="empty">no regime data</div>'; }
      else {
        rl.innerHTML = rb.slice(0, 20).map(function (x) {
          return '<div class="li"><span class="what">' + esc(x.regime || '—') + '</span>' +
            '<span class="when num">' + (x.count || 0) + '</span></div>';
        }).join('');
      }
    }
  }

  // AI tab — admission shadow + gpt stats (AI-free core: these are observe-only).
  function paintAi(s) {
    var al = $('ailist'); if (!al) return;
    var sh = s.ai_shadow || {};
    var adm = sh.admission || [];
    if ($('ai-cnt')) $('ai-cnt').textContent = adm.length || '';
    var html = '';
    html += '<div class="li"><span class="what">in-loop GPT calls</span><span class="when num">0</span></div>';
    html += '<div class="li"><span class="what">admission shadow (would-suppress)</span><span class="when num">' +
      (sh.admission_suppress_n || 0) + ' / ' + (sh.admission_total_n || 0) + '</span></div>';
    if (adm.length) {
      html += adm.map(function (a) {
        return '<div class="tr kv-grid" title="' + esc(a.regime) + '">' +
          '<span class="nm">' + esc(a.regime || '—') + '</span>' +
          '<span class="a num">n=' + (a.n || 0) + '</span>' +
          '<span class="b num">sup ' + (a.would_suppress_n || 0) + '</span>' +
          '<span class="c num">' + (a.suppress_pct || 0).toFixed(0) + '%</span>' +
          '</div>';
      }).join('');
    }
    var gs = s.gpt_stats || [];
    if (gs.length) {
      html += '<div class="ph-head">GPT calls (dev/debate)</div>';
      html += gs.map(function (g) {
        return '<div class="li"><span class="what">' + esc(g.model || g.kind || 'gpt') + '</span>' +
          '<span class="when num">' + (g.n || g.count || 0) + '</span></div>';
      }).join('');
    }
    al.innerHTML = html;
  }

  function paint(s) {
    // Header status — alive if we have a snapshot.
    $('dot').classList.add('live');
    $('status').textContent = 'live';
    paintSinceReset(s);
    paintRecentTrades(s);
    paintLogic(s);
    paintAi(s);

    // Compact status strip: equity + today P&L (small, not a hero block).
    // VIRTUAL mode (Jin 2026-07-08 dashboard-live-net fix): shows the fresh
    // $100k×3 virtual ledger (Σ streams[].virtual_equity_usd / virtual_daily_
    // pnl_usd) instead of the LEGACY equity_now/daily_pnl_usd (an unfiltered
    // fills-table scan that still mixes in the pre-virtual real-roundtrip
    // history) — this status strip is the main board Jin actually watches on
    // the phone, so it must match the mode banner's claimed scope. REAL mode
    // (virtual OFF) stays byte-identical to before this change.
    var virt = !!s.virtual_account_enabled;
    var eq = $('equity');
    var eqSub = $('equity-sub');
    var pnlVal, dayStartEq;
    if (virt) {
      var streams = s.streams || [];
      var virtEq = streams.reduce(function (a, x) { return a + (x.virtual_equity_usd != null ? x.virtual_equity_usd : 0); }, 0);
      var virtSeed = streams.reduce(function (a, x) { return a + (x.virtual_seed_usd != null ? x.virtual_seed_usd : 0); }, 0);
      eq.textContent = usd(virtEq) + ' virtual';
      eq.title = 'VIRTUAL $100k x 3 ledger — zero venue calls, seed ' + usd(virtSeed);
      if (eqSub) eqSub.textContent = 'seed ' + usd(virtSeed);
      pnlVal = s.virtual_daily_pnl_usd;
      dayStartEq = virtEq;
    } else {
      // Show BOTH: demo (70bps fee-burdened) · real-fee-net (go-live truth at 10bps).
      eq.textContent = usd(s.equity_now) + ' demo';
      eq.title = 'OKX demo charges 70bps (7x real); real-fee-net = equity at live 10bps fees';
      if (eqSub) eqSub.textContent = usd(s.equity_now_real_fee_net) + ' real-fee-net';
      pnlVal = s.daily_pnl_usd;
      dayStartEq = s.equity_now || 0;
    }
    var pnl = $('pnl');
    pnl.textContent = signed(pnlVal);
    pnl.className = 'v num ' + pnlClass(pnlVal);
    var pnlPct = $('pnl-pct');
    if (pnlPct) {
      // today's return on day-start equity (current equity − today's P&L).
      var dayStart = dayStartEq - (pnlVal || 0);
      var dp = dayStart > 0 ? (pnlVal / dayStart) * 100 : 0;
      pnlPct.textContent = (dp >= 0 ? '+' : '') + dp.toFixed(2) + '%';
      pnlPct.className = 'sub num ' + pnlClass(dp);
    }

    // ── Open Positions — aligned table w/ header. chip · symbol · side ·
    // current(live) · profit · % · exp. ENTRY dropped on phone (overflow fix). ──
    var positions = $('positions');
    var ps = s.positions || [];
    $('pos-cnt').textContent = ps.length || '';
    if (!ps.length) {
      positions.innerHTML = '<div class="empty">No open positions</div>';
    } else {
      var posHdr = '<div class="thr pos-grid">' +
        '<span></span><span>SYM</span><span class="ta-c">S</span>' +
        '<span class="ta-r">CUR</span><span class="ta-r">P&amp;L</span>' +
        '<span class="ta-r">%</span><span class="ta-r">EXP</span></div>';
      positions.innerHTML = posHdr + ps.slice(0, 30).map(function (p) {
        var sideCls = (String(p.side || '').toLowerCase().indexOf('s') === 0 ||
          String(p.side || '').toLowerCase() === 'short') ? 'side-short' : 'side-long';
        var vi = venueInfo(p.venue);
        var pct = (p.upnl_pct || 0);
        var sideTag = (String(p.side || '').toLowerCase().charAt(0) === 's') ? 'S' : 'L';
        // data-key matches the server /stream/prices key (venue|symbol|strategy|side)
        // so streamed cell pushes flash the live CUR / P&L / % cells of THIS row.
        var key = [p.venue, p.symbol, p.strategy_id, p.side].join('|');
        return '<div class="tr row pos-grid" data-key="' + esc(key) + '">' +
          '<span class="vchip v-' + vi.key + '" title="' + esc(p.venue) + '">' + esc(vi.tag) + '</span>' +
          '<span class="a" title="' + esc(p.symbol) + '">' + esc(p.symbol) + '</span>' +
          '<span class="b ' + sideCls + '" title="' + esc(p.side) + '">' + sideTag + '</span>' +
          '<span class="pxc num" title="current (live)">' + fmtPx(p.last_price) + '</span>' +
          '<span class="sp num ' + pnlClass(p.upnl_usd) + '">' + signed(p.upnl_usd) + '</span>' +
          '<span class="pct num ' + pnlClass(pct) + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%</span>' +
          '<span class="exp num" title="exposure (size USD)">' + kusd(p.size_usd) + '</span>' +
          '</div>';
      }).join('');
    }

    // ── Gate Decisions — G# · what it decided · n (aligned table). ──
    var gdec = $('gatedec');
    var gd = s.gate_decisions || [];
    if ($('gd-cnt')) $('gd-cnt').textContent = gd.length || '';
    if (gdec) {
      if (!gd.length) {
        gdec.innerHTML = '<div class="empty">no gate decisions</div>';
      } else {
        var gdHdr = '<div class="thr gd-grid"><span>G#</span><span>DECIDED</span><span class="ta-r">n</span></div>';
        gdec.innerHTML = gdHdr + gd.map(function (g) {
          return '<div class="tr gd-grid" title="G' + esc(g.gate_id) + ' ' + esc(g.label) + ' · ' + esc(g.headline) + '">' +
            '<span class="g">G' + esc(g.gate_id) + '</span>' +
            '<span class="hl">' + esc(g.headline || '—') + '</span>' +
            '<span class="n num">' + esc(g.n) + '</span>' +
            '</div>';
        }).join('');
      }
    }

    // ── Gate Activity — gate events only (probes de-merged to own section).
    // Aligned grid: TIME · G# · DECISION · SYMBOL, newest first. ──
    var feed = $('gatefeed');
    var ge = (s.recent_gate_events || []).slice();
    $('gate-cnt').textContent = ge.length || '';
    if (!ge.length) {
      feed.innerHTML = '<div class="empty">no gate events yet</div>';
    } else {
      ge.sort(function (a, b) {
        return (b.ts || b.ts_unix || 0) - (a.ts || a.ts_unix || 0);
      });
      var gaHdr = '<div class="thr ga-grid"><span>TIME</span><span>G#</span><span>DECIDED</span><span>SYM</span></div>';
      feed.innerHTML = gaHdr + ge.slice(0, 60).map(function (e) {
        var sym = e.symbol || e.ticker || '';
        var ts = e.ts || e.ts_unix;
        var tstr = ts ? hhmmss(ts) : '';
        var v = gateVerdict(e);
        var g = (e.gate_id != null) ? ('G' + e.gate_id) : 'G?';
        var strat = e.strategy_id || e.strategy || '';
        var why = e.reason || e.note || '';
        return '<div class="tr ga-grid" title="' + esc(g) + ' ' + esc(v.txt) +
            ' · ' + esc(strat) + ' ' + esc(sym) + ' · ' + esc(why) + ' · ' + esc(tstr) + '">' +
          '<span class="t num">' + esc(tstr) + '</span>' +
          '<span class="g">' + esc(g) + '</span>' +
          '<span class="v ' + v.cls + '">' + esc(v.txt) + '</span>' +
          '<span class="sym" title="' + esc(sym) + '">' + esc(sym || '—') + '</span>' +
          '</div>';
      }).join('');
    }

    // ── Probe Activity — per-probe readings, newest first (aligned table).
    // TIME · PROBE · SYMBOL · LEAN/CONF. ──
    var pfeed = $('probefeed');
    var pr = probeRows(s).sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
    if ($('probe-cnt')) $('probe-cnt').textContent = pr.length || '';
    if (pfeed) {
      if (!pr.length) {
        pfeed.innerHTML = '<div class="empty">no probe readings</div>';
      } else {
        var prHdr = '<div class="thr pr-grid"><span>TIME</span><span>PROBE</span><span>SYM</span><span class="ta-r">LEAN/CONF</span></div>';
        pfeed.innerHTML = prHdr + pr.slice(0, 60).map(function (p) {
          var tstr = p.ts ? hhmmss(p.ts) : '';
          var rd = (p.lean == null ? '—' : (p.lean >= 0 ? '+' : '') + p.lean.toFixed(2)) +
            (p.conf == null ? '' : ' / ' + p.conf.toFixed(2));
          return '<div class="tr pr-grid" title="' + esc(p.name) + ' ' + esc(p.sym) + ' ' + esc(p.kind) +
              ' · lean ' + (p.lean == null ? '—' : p.lean.toFixed(2)) + ' conf ' + (p.conf == null ? '—' : p.conf.toFixed(2)) +
              ' · ' + esc(p.action || '') + ' · ' + esc(tstr) + '">' +
            '<span class="t num">' + esc(tstr) + '</span>' +
            '<span class="pn" title="' + esc(p.name) + '">' + esc(p.name) + '</span>' +
            '<span class="sym" title="' + esc(p.sym) + '">' + esc(p.sym) + '</span>' +
            '<span class="rd num ' + pnlClass(p.lean) + '">' + rd + '</span>' +
            '</div>';
        }).join('');
      }
    }

    // Bridge optional probe data to the Neural Cloud globe (display-only): probes
    // pulse their ticker node + venue galaxy. Graceful no-op when absent.
    if (window.PolarisGlobe && window.PolarisGlobe.showProbes) {
      window.PolarisGlobe.showProbes(s.probe_events || s.probes || []);
    }
  }

  // Collapsible sections (every .sec.collapsible — i.e. all but Open Positions).
  // Restore the persisted collapse state on load, then delegate the h2 toggle.
  // Key = the section's data-ck; value '1' = collapsed. Default expanded.
  function wireCollapse() {
    var secs = document.querySelectorAll('.sec.collapsible');
    for (var i = 0; i < secs.length; i++) {
      var sec = secs[i];
      var key = sec.getAttribute('data-ck');
      var collapsed = false;
      try { collapsed = localStorage.getItem(key) === '1'; } catch (e) { /* private mode */ }
      if (collapsed) {
        sec.classList.add('collapsed');
        var h = sec.querySelector('h2');
        if (h) h.setAttribute('aria-expanded', 'false');
      }
    }
    function toggle(h2) {
      var sec = h2.closest('.sec.collapsible'); if (!sec) return;
      var isCol = sec.classList.toggle('collapsed');
      h2.setAttribute('aria-expanded', isCol ? 'false' : 'true');
      var key = sec.getAttribute('data-ck');
      try { if (key) localStorage.setItem(key, isCol ? '1' : '0'); } catch (e) { /* private mode */ }
    }
    function findHead(target) {
      var h2 = target.closest ? target.closest('h2') : null;
      if (h2 && h2.parentElement && h2.parentElement.classList.contains('collapsible')) return h2;
      return null;
    }
    document.addEventListener('click', function (e) {
      var h2 = findHead(e.target);
      if (h2) toggle(h2);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var h2 = findHead(e.target);
      if (h2) { e.preventDefault(); toggle(h2); }
    });
  }

  function tick() {
    fetch('/api/snapshot', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(paint)
      .catch(function () {
        $('dot').classList.remove('live');
        $('status').textContent = 'offline';
      });
  }

  // ── Live price SSE push ───────────────────────────────────────────────────
  // Open-position price cells flash per-cell as marks STREAM in — not a poll.
  // /stream/prices pushes ONLY the cells that moved on the server; each message
  // updates just the CUR / P&L / % cells of the matching keyed row + a brief
  // up/down flash. The 1s snapshot poll still owns structure (rows added/removed).
  var _pxLast = {};   // key → { px, upnl } last numeric value for flash direction
  function _flashDir(prev, cur) {
    if (prev == null || cur == null || cur === prev) return '';
    return cur > prev ? 'up' : 'down';
  }
  function _flashCell(el, dir) {
    if (!el || !dir) return;
    el.classList.remove('px-flash-up', 'px-flash-down');
    void el.offsetWidth;   // restart the animation on a re-flash
    el.classList.add(dir === 'up' ? 'px-flash-up' : 'px-flash-down');
  }
  function _setCell(el, text, cls, dir) {
    if (!el) return;
    var changed = el.textContent !== text;
    if (changed) el.textContent = text;
    if (cls != null) el.className = cls;
    if (changed) _flashCell(el, dir);
  }
  function applyPriceCell(p) {
    var pos = $('positions'); if (!pos || !p || !p.key) return;
    var row = pos.querySelector('.row[data-key="' +
      (window.CSS && CSS.escape ? CSS.escape(p.key) : p.key) + '"]');
    if (!row) return;
    var prev = _pxLast[p.key] || {};
    var pxDir = _flashDir(prev.px, p.last_price);
    var upDir = _flashDir(prev.upnl, p.upnl_usd);
    _pxLast[p.key] = { px: p.last_price, upnl: p.upnl_usd };
    var pct = (p.upnl_pct || 0);
    _setCell(row.querySelector('.pxc'), fmtPx(p.last_price), 'pxc num', pxDir);
    _setCell(row.querySelector('.sp'), signed(p.upnl_usd), 'sp num ' + pnlClass(p.upnl_usd), upDir);
    _setCell(row.querySelector('.pct'), (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%', 'pct num ' + pnlClass(pct), '');
    // Jin 2026-06-24 SYNC: a moved cell (flash) → pulse the same symbol's globe
    // node on the same event. key = venue|symbol|strategy|side. Display-only.
    if ((pxDir || upDir) && window.PolarisGlobe && window.PolarisGlobe.pulseSymbol) {
      var parts = String(p.key).split('|');
      try { window.PolarisGlobe.pulseSymbol(parts[1], parts[0]); } catch (e) { /* display-only */ }
    }
  }
  var _priceES = null;
  function connectPriceStream() {
    if (_priceES || typeof EventSource === 'undefined') return;
    try {
      _priceES = new EventSource('/stream/prices');
      _priceES.onmessage = function (ev) {
        try {
          var j = JSON.parse(ev.data);
          if (j && j.prices) { for (var i = 0; i < j.prices.length; i++) applyPriceCell(j.prices[i]); }
        } catch (e) { /* ignore a malformed frame */ }
      };
      _priceES.onerror = function () { /* EventSource auto-reconnects */ };
    } catch (e) { /* SSE unavailable — cells fall back to the 1s snapshot poll */ }
  }

  // ── Tabs (Jin 2026-06-24) — Main / Logic / Build / Roadmap / AI ────────────
  // Main + Logic + AI are fed by the 1s snapshot poll (paint). Build + Roadmap
  // pull from dedicated fetch-once endpoints (like the desktop reference tabs),
  // loaded lazily the first time their tab is opened.
  var _loaded = {};
  function showTab(name) {
    var panes = document.querySelectorAll('.tabpane');
    for (var i = 0; i < panes.length; i++) panes[i].classList.toggle('on', panes[i].id === 'tab-' + name);
    var btns = document.querySelectorAll('#tabbar button');
    for (var j = 0; j < btns.length; j++) btns[j].classList.toggle('on', btns[j].getAttribute('data-tab') === name);
    try { localStorage.setItem('m.tab', name); } catch (e) { /* private mode */ }
    if (name === 'build') loadBuild();
    if (name === 'roadmap') loadRoadmap();
  }
  function wireTabs() {
    var bar = $('tabbar'); if (!bar) return;
    bar.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-tab]') : null;
      if (b) showTab(b.getAttribute('data-tab'));
    });
    var saved = 'main';
    try { saved = localStorage.getItem('m.tab') || 'main'; } catch (e) { /* private mode */ }
    showTab(saved);
  }
  // Build tab — recent commits + test count (GET /api/buildlog, fetch-once).
  function loadBuild() {
    if (_loaded.build) return; _loaded.build = true;
    var body = $('buildlist'); if (!body) return;
    body.innerHTML = '<div class="empty">loading…</div>';
    fetch('/api/buildlog', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        var commits = (d && d.commits) || [];
        if ($('build-cnt')) $('build-cnt').textContent = d && d.test_count ? (d.test_count + ' tests') : (commits.length || '');
        if (!commits.length) { body.innerHTML = '<div class="empty">no build log</div>'; return; }
        body.innerHTML = commits.slice(0, 40).map(function (c) {
          var when = (c.date || '').slice(5, 10);
          return '<div class="li"><span class="when">' + esc(when) + '</span>' +
            '<span class="tg">' + esc((c.type || '').slice(0, 8)) + '</span>' +
            '<span class="what" title="' + esc(c.subject) + '">' + esc(c.subject) + '</span></div>';
        }).join('');
      }).catch(function () { body.innerHTML = '<div class="empty">/api/buildlog unavailable</div>'; _loaded.build = false; });
  }
  // Roadmap tab — phase items + next (GET /api/roadmap, fetch-once).
  function loadRoadmap() {
    if (_loaded.roadmap) return; _loaded.roadmap = true;
    var body = $('roadlist'); if (!body) return;
    body.innerHTML = '<div class="empty">loading…</div>';
    fetch('/api/roadmap', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        var phases = (d && d.phases) || [];
        if ($('road-cnt')) $('road-cnt').textContent = phases.length || '';
        if (!phases.length) { body.innerHTML = '<div class="empty">no roadmap</div>'; return; }
        var html = '';
        phases.slice(0, 12).forEach(function (ph) {
          html += '<div class="ph-head" title="' + esc(ph.phase) + '">' + esc(ph.phase) + '</div>';
          (ph.items || []).slice(0, 20).forEach(function (it) {
            var txt = String(it.text || it || '');
            var tg = /\[DONE\]/i.test(txt) ? 'done' : /\[BUILD\]/i.test(txt) ? 'build' : '';
            var clean = txt.replace(/\*\*/g, '').replace(/\[(DONE|BUILD|DEBATE|DECISION)\]/ig, '').trim();
            html += '<div class="li">' + (tg ? '<span class="tg ' + tg + '">' + tg.toUpperCase() + '</span>' : '') +
              '<span class="what" title="' + esc(clean) + '">' + esc(clean) + '</span></div>';
          });
        });
        body.innerHTML = html;
      }).catch(function () { body.innerHTML = '<div class="empty">/api/roadmap unavailable</div>'; _loaded.roadmap = false; });
  }

  wireCollapse();
  wireTabs();
  tick();
  setInterval(tick, POLL_MS);
  connectPriceStream();
})();
