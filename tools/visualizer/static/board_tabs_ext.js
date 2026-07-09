/* board_tabs_ext.js — Polaris E2 Trading-IA tab renderers (part 2 of 2).
 *
 * The analytics tabs (EXIT / AI / EDGE / RISK) split out of board_tabs.js to
 * keep each module within the LOC guideline. Loaded AFTER board_tabs.js;
 * registers its renderers into the shared dispatch via
 * ``window.PolarisBoardTabs.register``. Pure DOM render off the /api/snapshot
 * frame — display-only, no fetch / sizing / order / exit logic.
 */
(function () {
  'use strict';

  const B = window.PolarisBoard;
  const T = window.PolarisBoardTabs;
  if (!B || !T) { return; }   // board.js + board_tabs.js must load first
  const { $, fmtUsd, fmtPct, fmtSignedPct, fmtPx, fmtR, sparkline, symCell, pn, esc, hms,
    hhmmss, venueStream, getActiveExchange, venueMatches, venueFilter,
    legacyKpiHtml, renderStreamsInto } = B;
  const setCnt = T.setCnt;

  // E3 (Jin 2026-05-31): analytics tabs scope where the data carries venue
  // (EDGE edge_validation.exchange / RISK cell.exchange / last_rotation.venue);
  // genuinely cross-venue metrics (FSM / exit reasons / GPT / shadow agreement /
  // admission / alerts / equity / confidence) render with an 'all venues' note
  // rather than a fabricated per-venue split. ``scopeLabel`` = '' on ALL.
  function scopeLabel() {
    const ex = getActiveExchange();
    return ex === 'all' ? '' : ex.toUpperCase();
  }
  // A small 'all venues (cross-venue metric)' note row for global panels.
  function globalNote() {
    const sc = scopeLabel();
    return sc
      ? `<div class="row" style="grid-template-columns:1fr;color:var(--p-dim);font-size:9px">scope ${esc(sc)} — cross-venue metric (all venues)</div>`
      : '';
  }
  // in-loop GPT calls/h across all gates (W3 cutover target = 0). When 0 the
  // core is running AI-free, so GPT/shadow panels are empty BY DESIGN — render
  // a calm explanatory note instead of a blank panel.
  function inLoopGptH(d) {
    return (d.gpt_stats || []).reduce((a, g) => a + (g.calls_per_h || 0), 0);
  }
  function aiFreeNote(d) {
    return inLoopGptH(d) === 0
      ? '<div class="empty">AI-FREE · in-loop GPT disabled (W3 deterministic cutover) — GPT runs async shadow/sentinel only</div>'
      : null;
  }

  // ── TAB 5 · EXIT ──────────────────────────────────────────────────────────
  function renderExit(d) {
    const surf = d.exit_surface || {};
    // FSM state chips
    const fsm = surf.fsm_states || {};
    const fsmEl = $('exit-fsm');
    if (fsmEl) {
      const order = ['open', 'touched', 'protected', 'harvest'];
      const keys = Object.keys(fsm).sort((a, b) => {
        const ia = order.indexOf(a), ib = order.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      const chips = keys.length ? keys.map(k =>
        `<span class="chip"><span class="cl">${esc(k)}</span> <span class="cn">${fsm[k]}</span></span>`
      ).join('') : '<span class="b-flat" style="padding:4px 10px">no open positions</span>';
      const lt = surf.loser_timeout_n || 0;
      fsmEl.innerHTML = `<span class="chip" style="border-left:3px solid var(--p-mag)"><span class="cl">FSM</span></span>` + chips
        + `<span class="chip" title="losing trades exited past the timeout boundary"><span class="cl">loser-timeout</span> <span class="cn ${lt ? 'b-neg' : ''}">${lt}</span></span>`;
    }
    // Exit-reason histogram
    const reasons = surf.reasons || [];
    const rbody = $('exit-reasons');
    if (rbody) {
      const max = reasons.reduce((m, r) => Math.max(m, r.count), 1);
      rbody.innerHTML = globalNote() + (reasons.length ? reasons.map(r => `
        <div class="row" style="grid-template-columns:90px 1fr 40px" title="${esc(r.reason)} ×${r.count}">
          <span class="name">${esc(r.reason)}</span>
          <span class="hbar"><i style="width:${(r.count / max * 100).toFixed(0)}%"></i></span>
          <span class="num b-flat">${r.count}</span>
        </div>`).join('') : '<div class="empty">no closed trades</div>');
    }
    // G6 / G7 gate decisions
    const gbody = $('exit-gates');
    if (gbody) {
      const g6 = surf.g6_decisions || {}, g7 = surf.g7_decisions || {};
      const sect = (label, obj) => {
        const keys = Object.keys(obj);
        const rows = keys.length ? keys.map(k =>
          `<div class="row" style="grid-template-columns:1fr 50px"><span class="name b-flat">${esc(k)}</span><span class="num b-flat">${obj[k]}</span></div>`
        ).join('') : `<div class="row" style="grid-template-columns:1fr"><span class="b-flat">no decisions (1h)</span></div>`;
        return `<div class="row" style="grid-template-columns:1fr;color:var(--polaris-blue);font-weight:700">${esc(label)}</div>` + rows;
      };
      gbody.innerHTML = sect('G6 · Position Monitor', g6) + sect('G7 · Adaptive Exit', g7);
    }
  }

  // ── TAB 6 · AI ────────────────────────────────────────────────────────────
  function renderAi(d) {
    // Per-gate GPT
    const gpt = d.gpt_stats || [];
    const gbody = $('ai-gpt');
    if (gbody) {
      gbody.innerHTML = globalNote() + (gpt.length ? gpt.map(g => {
        const ok = (g.ok_pct == null) ? 100 : g.ok_pct;
        const okStyle = ok < 50 ? 'color:var(--p-red);font-weight:700'
          : ok < 90 ? 'color:var(--p-ylw);font-weight:700' : 'color:var(--p-grn)';
        const errTip = (g.err_n || 0) > 0 ? ` (${g.err_n} err)` : '';
        return `<div class="row" style="grid-template-columns:1fr 54px 50px 64px"
          title="${esc(g.model)} ok ${ok.toFixed(0)}%${errTip} · ${(g.calls_per_h || 0)}/h · 24h proj ${fmtUsd(g.cost_24h_proj_usd, 2)}">
          <span class="name b-flat">${esc(g.model)}</span>
          <span class="num b-flat">${(g.calls_per_h || 0)}/h</span>
          <span class="num" style="${okStyle}">${ok.toFixed(0)}%</span>
          <span class="num b-flat">${fmtUsd(g.cost_24h_proj_usd, 2)}</span>
        </div>`;
      }).join('') : (aiFreeNote(d) || '<div class="empty">deterministic · no GPT calls (1h)</div>'));
    }
    // Conductor shadow agreement + admission summary
    const ai = d.ai_shadow || {};
    const sbody = $('ai-shadow');
    if (sbody) {
      const sa = ai.shadow_agreement || [];
      let html = sa.length ? sa.map(r => {
        const ag = r.agree_pct || 0;
        const barCls = ag >= 80 ? 'bar-pos' : ag >= 50 ? 'bar-warn' : 'bar-neg';
        const noGpt = r.n_no_gpt ? ` <span class="b-flat">(+${r.n_no_gpt} det-only)</span>` : '';
        return `<div class="row" style="grid-template-columns:74px 1fr 46px"
          title="G${r.gate_id} ${esc(r.regime)} · n=${r.n} mismatch=${r.mismatch_n} det-only=${r.n_no_gpt}">
          <span class="name">G${r.gate_id} ${esc(r.regime)}</span>
          <span class="hbar ${r.n ? barCls : ''}"><i style="width:${r.n ? ag.toFixed(0) : 0}%"></i></span>
          <span class="num b-flat">${r.n ? ag.toFixed(0) + '%' : '—'}${noGpt}</span>
        </div>`;
      }).join('') : (aiFreeNote(d) || '<div class="empty">no shadow events (1h)</div>');
      const tot = ai.admission_total_n || 0, sup = ai.admission_suppress_n || 0;
      const supPct = tot ? (sup / tot * 100) : 0;
      html += `<div class="row" style="grid-template-columns:1fr;color:var(--polaris-blue);font-weight:700;margin-top:4px">Entry-Admission Shadow (would-suppress)</div>`
        + `<div class="row" style="grid-template-columns:1fr 110px" title="edge-first rule would suppress ${sup}/${tot} entries net of the real round-trip fee (SHADOW — never blocks)">`
        + `<span class="name b-flat">total ${tot} · suppress</span>`
        + `<span class="num ${sup ? 'b-neg' : 'b-flat'}">${sup} (${supPct.toFixed(0)}%)</span></div>`;
      sbody.innerHTML = html;
    }
  }

  // ── TAB 7 · EDGE (equity curve + confidence + edge validation) ────────────
  function eqPath(curve, x, y, withArea) {
    const n = curve.length;
    let line = '', area = `M ${x(0)} 90 `;
    curve.forEach((v, i) => {
      const px = x(i).toFixed(1), py = y(v).toFixed(1);
      line += (i === 0 ? 'M' : 'L') + ' ' + px + ' ' + py + ' ';
      area += 'L ' + px + ' ' + py + ' ';
    });
    area += `L ${x(n - 1)} 90 Z`;
    return withArea ? { line, area } : { line, area: '' };
  }
  function renderEquity(d) {
    const svg = $('eq-svg'); if (!svg) return;
    const real = d.equity_curve_real_fee_net || [];
    const demo = d.equity_curve || [];
    if (real.length < 2 && demo.length < 2) { svg.innerHTML = ''; return; }
    const W = 600, H = 90, pad = 2;
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
      html += `<path d="${dp.line}" fill="none" stroke="rgba(158,158,158,0.55)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
    }
    if (real.length >= 2) {
      const rp = eqPath(real, x, y, true);
      html += `<path d="${rp.area}" fill="${rFill}" stroke="none"/>`;
      html += `<path d="${rp.line}" fill="none" stroke="${rStroke}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
    }
    svg.innerHTML = html;
    if (real.length >= 2) {
      const dv = real[real.length - 1] - real[0];
      const el = $('eq-real-delta'); if (el) { el.textContent = fmtUsd(dv, 0); el.className = 'v ' + pn(dv); }
    }
    if (demo.length >= 2) {
      const dv = demo[demo.length - 1] - demo[0];
      const el = $('eq-demo-delta'); if (el) el.textContent = fmtUsd(dv, 0);
    }
    const wedge = (d.demo_fee_total || 0) - (d.real_fee_total || 0);
    const we = $('eq-fee-wedge'); if (we) { we.textContent = '+' + fmtUsd(wedge, 0); we.className = 'v b-pos'; }
  }
  function renderConfidence(d) {
    const el = $('b-confidence'); if (!el) return;
    const c = d.confidence;
    if (!c || !c.n_closed) { el.innerHTML = '<span class="b-flat">confidence warming up (no closed trades)</span>'; return; }
    const pf = (c.profit_factor >= 9.99) ? '∞' : (c.profit_factor || 0).toFixed(2);
    const turn = (c.turnover_ratio || 0).toFixed(2) + '×';
    const overall =
      `<span><span class="ck">WR</span> <span class="cv">${fmtPct(c.win_rate_pct, 1)}</span></span>` +
      `<span><span class="ck">PF</span> <span class="cv">${pf}</span></span>` +
      `<span title="Σ notional / starting equity (churn proxy)"><span class="ck">Turn</span> <span class="cv">${turn}</span></span>` +
      `<span title="Real fee drag (R) under the REAL OKX schedule"><span class="ck">FeeR(real)</span> <span class="cv b-neg">-${(c.fee_drag_real_r || 0).toFixed(1)}</span></span>` +
      `<span title="Demo fee drag (R) — the punitive 0.7% demo drain (7x real)"><span class="ck">FeeR(demo)</span> <span class="cv b-flat">-${(c.fee_drag_demo_r || 0).toFixed(1)}</span></span>`;
    const cells = (c.cells || []).slice(0, 8).map(cell => {
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
  function renderBenchmark(d) {
    const el = $('b-benchmark'); if (!el) return;
    const r = (d.confidence && d.confidence.replay) || {};
    if (!r.present) { el.innerHTML = '<span class="b-flat">benchmark · no offline replay run yet</span>'; return; }
    const v = (r.verdict || '').toUpperCase();
    const vcls = v.includes('PASS') ? 'b-pos' : (v.includes('WIDE') || v.includes('EDGE')) ? '' : 'b-neg';
    const dd = (r.max_dd || 0) * 100;
    const lcb = (r.net_pnl_r_lcb || 0), ucb = (r.net_pnl_r_ucb || 0);
    const head =
      `<span title="Offline replay run id"><span class="ck">Replay</span> <span class="cv">${esc((r.run_id || '').slice(0, 8))}</span></span>` +
      `<span><span class="ck">Verdict</span> <span class="cv ${vcls}">${esc(v || 'n/a')}</span></span>` +
      `<span title="Real-fee-net pnl over the replay (real OKX 0.10% taker)"><span class="ck">Net(real)</span> <span class="cv ${pn(r.net_pnl_real_fee || 0)}">${fmtUsd(r.net_pnl_real_fee || 0, 0)}</span></span>` +
      `<span title="Per-trade Sharpe (real-fee-net, baseline clock)"><span class="ck">Sharpe</span> <span class="cv">${(r.sharpe || 0).toFixed(2)}</span></span>` +
      `<span title="Probabilistic Sharpe Ratio (prob true Sharpe > 0)"><span class="ck">PSR</span> <span class="cv">${(r.psr || 0).toFixed(2)}</span></span>` +
      `<span title="Deflated Sharpe (trial-adjusted PSR — anti-overfit)"><span class="ck">DSR</span> <span class="cv">${(r.deflated_sharpe || 0).toFixed(2)}</span></span>` +
      `<span title="Net-R posterior CI band (NIG LCB..UCB)"><span class="ck">CI(R)</span> <span class="cv ${pn(lcb)}">${lcb.toFixed(2)}..${ucb.toFixed(2)}</span></span>` +
      `<span title="Max drawdown"><span class="ck">MaxDD</span> <span class="cv b-neg">${dd.toFixed(1)}%</span></span>` +
      `<span title="Σ notional turned over"><span class="ck">Turn</span> <span class="cv">${fmtUsd(r.turnover || 0, 0)}</span></span>` +
      `<span title="Real fee drag (USD)"><span class="ck">FeeDrag</span> <span class="cv b-neg">-${(r.fee_drag_real_r || 0).toFixed(1)}</span></span>` +
      `<span title="In-sample vs out-of-sample Sharpe spread (walk-forward overfit flag; large gap = overfit)"><span class="ck">IS−OOS</span> <span class="cv">${(r.is_oos_spread || 0).toFixed(2)}</span></span>` +
      `<span><span class="ck">n</span> <span class="cv">${r.n || 0}</span></span>`;
    const sp = r.sharpe_spreads || {};
    const spreads = Object.keys(sp).map(k => {
      const lbl = k.replace('sharpe_spread_vs_', 'Δ');
      const val = sp[k] || 0;
      return `<span class="cell ${val > 0 ? 'lcb-pos' : val < 0 ? 'lcb-neg' : ''}" title="Bot Sharpe spread vs ${esc(k.replace('sharpe_spread_vs_', ''))} baseline">
        <span class="nm">${esc(lbl)}</span><span class="cv ${pn(val)}">${val >= 0 ? '+' : ''}${val.toFixed(2)}</span></span>`;
    }).join('');
    const tiers = (r.tiers || []).filter(t => !t.baseline).map(t => {
      const cls = t.passed ? 'lcb-pos' : '';
      return `<span class="cell ${cls}" title="${esc(t.note)}"><span class="nm">${esc(t.tier)}</span>
        <span class="cv ${t.passed ? 'b-pos' : 'b-flat'}">${t.passed ? 'PASS' : '—'}</span></span>`;
    }).join('');
    el.innerHTML = head + spreads + tiers;
  }
  function renderGateValue(d) {
    // G3/G4 gate-kill counterfactual value (07-08 BUILD) — SELECT-only /debate
    // evidence, gate_kill_value.compute_kill_value_hints. Never auto-applied;
    // this panel never feeds a live gate threshold.
    const el = $('b-gate-value'); if (!el) return;
    const g = d.gate_kill_value;
    if (!g || !g.present) { el.innerHTML = '<span class="b-flat">gate value · no stratified cohort yet</span>'; return; }
    const cells = (g.rows || []).map(r => {
      const sep = r.separation || 0;
      const cls = r.anti_edge ? 'lcb-neg' : (sep > 0 ? 'lcb-pos' : '');
      const warn = r.anti_edge ? ' ⚠' : '';
      return `<span class="cell ${cls}" title="G${r.gate_id} · ${esc(r.cohort)} · killed n=${r.n_killed} E[R]=${(r.mean_killed_fwd_r || 0).toFixed(2)} · passed n=${r.n_passed} E[R]=${(r.mean_passed_fwd_r || 0).toFixed(2)} · separation=${sep.toFixed(2)}${r.anti_edge ? ' — anti-edge: gate killed winners' : ''}">
        <span class="nm">G${r.gate_id} ${esc(r.cohort)}</span>
        <span class="cv ${pn(sep)}">${sep >= 0 ? '+' : ''}${sep.toFixed(2)}R${warn}</span>
      </span>`;
    }).join('');
    el.innerHTML = cells;
  }
  function renderEdge(d) {
    // edge_validation carries an ``exchange`` venue → scope it (E3).
    const rows = venueFilter(d.edge_validation, 'exchange');
    const body = $('edge-body'); if (!body) return;
    setCnt('edge-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">edge validation warming up${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    body.innerHTML = rows.map(e => {
      const v = (e.verdict || '').toLowerCase();
      const cls = v.includes('anti') ? 'verdict-anti' : v.includes('edge') || v.includes('valid') ? 'verdict-edge' : 'verdict-neutral';
      return `<div class="row" style="grid-template-columns:1fr 70px 56px 1fr"
        title="${esc(e.exchange)}/${esc(e.strategy)}/${esc(e.ticker)} · p+ ${(e.p_pos || 0).toFixed(3)} n=${e.n_samples} ${esc(e.regime)}${e.est_cost ? ' (est cost)' : ''}">
        <span class="name">${esc(e.ticker)} <span class="sub">${esc(e.strategy)}</span></span>
        <span class="num ${e.cost_adj_exp >= 0 ? 'b-pos' : 'b-neg'}">${(e.cost_adj_exp || 0).toFixed(2)}</span>
        <span class="num b-flat">p${(e.p_pos || 0).toFixed(2)}</span>
        <span class="${cls}" style="text-align:right;font-size:9px;overflow:hidden;text-overflow:ellipsis">${esc(e.verdict)}</span>
      </div>`;
    }).join('');
  }

  // ── TAB 8 · RISK (rotation + cells + alerts + admission) ──────────────────
  function renderRotation(d) {
    const el = $('b-rotation'); if (!el) return;
    const rc = d.rotation_count || 0;
    const sfe = d.session_forced_exit_count || 0;
    // Totals are cross-venue session counts (global); the last-rotation detail
    // carries a venue → hide it when scoped to a different venue (E3).
    const last = (d.last_rotation && venueMatches(d.last_rotation.venue))
      ? d.last_rotation : null;
    let lastHtml;
    if (last) {
      const eNew = last.e_new || 0, eHeld = last.e_held || 0;
      lastHtml = `<span class="rt-last" title="last rotation: ${esc(last.venue)} closed ${esc(last.victim_symbol)} (${esc(last.victim_strategy)}) → E$new ${eNew.toFixed(2)} vs E$held ${eHeld.toFixed(2)}">`
        + `last <b>${esc(last.victim_symbol)}</b> `
        + `<span class="${pn(eNew - eHeld)}">E$new ${eNew.toFixed(1)}</span> vs E$held ${eHeld.toFixed(1)} `
        + `· mgn ${(last.margin || 0).toFixed(1)} · cost ${(last.cost || 0).toFixed(2)}</span>`;
    } else {
      const sc = scopeLabel();
      lastHtml = `<span class="rt-last rt-none">${sc ? 'no recent rotation · ' + esc(sc) : 'no rotations yet'}</span>`;
    }
    // Totals span all venues; note it when scoped (counts are not per-venue).
    const sc = scopeLabel();
    const scopeKv = sc ? `<span class="rt-kv"><span class="lk">scope</span> <span class="lv">${esc(sc)} (totals all venues)</span></span>` : '';
    el.innerHTML =
      `<span class="rt-title" title="capital-rotation = finite-capital opportunity-cost redeploy (display-only telemetry)">Rotation</span>`
      + `<span class="rt-kv"><span class="lk">Rotations</span> <span class="lv">${rc}</span></span>`
      + `<span class="rt-kv"><span class="lk">Session Exits</span> <span class="lv">${sfe}</span></span>`
      + scopeKv
      + lastHtml;
  }
  function renderCells(d) {
    // Cell rows carry an ``exchange`` venue → scope them (E3).
    const top = venueFilter(d.cell_top, 'exchange').map(c => ({ ...c, _side: 'top' }));
    const bot = venueFilter(d.cell_bottom, 'exchange').map(c => ({ ...c, _side: 'bot' }));
    const rows = top.concat(bot);
    const body = $('cell-body'); if (!body) return;
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">cold start · building${sc ? ' · ' + esc(sc) : ''}<br><span class="b-flat">(need n≥20 samples)</span></div>`;
      return;
    }
    body.innerHTML = rows.map(c => {
      const cls = c.score > 0 ? 'b-pos' : c.score < 0 ? 'b-neg' : 'b-flat';
      return `<div class="row" style="grid-template-columns:1fr 50px 44px" title="${esc(c.exchange)}/${esc(c.strategy)}/${esc(c.regime)} n=${(c.n_eff || 0).toFixed(0)} [${c._side}]">
        <span class="name">${esc(c.ticker)} <span class="sub">${esc(c.strategy)}</span></span>
        <span class="num ${cls}">${(c.score || 0).toFixed(3)}</span>
        <span class="num b-flat">${(c.mult || 1).toFixed(1)}×</span>
      </div>`;
    }).join('')
      // top cells present but no bottom (low-EV) cells → say so, don't look broken.
      + (top.length && !bot.length
        ? '<div class="empty" style="text-align:left">no low-EV cells in window</div>' : '');
  }
  function renderAlerts(d) {
    const alerts = (d.alerts || []).slice(0, 12);
    const body = $('alert-body'); if (!body) return;
    setCnt('alert-body-cnt', alerts.length);
    body.innerHTML = globalNote() + (alerts.length ? alerts.map(a => `
      <div class="row" style="grid-template-columns:54px 1fr" title="[${esc(a.level)}] ${esc(a.module)} ${hhmmss(a.ts)} — ${esc(a.msg || '')}">
        <span class="lvl-${esc(a.level)}">${esc(a.level)}</span>
        <span class="name b-flat" style="font-weight:400">${esc(a.msg || '')}</span>
      </div>`).join('') : '<div class="empty">no alerts</div>');
  }
  function renderRiskAdmit(d) {
    const ai = d.ai_shadow || {};
    const rows = ai.admission || [];
    const body = $('risk-admit'); if (!body) return;
    body.innerHTML = globalNote() + (rows.length ? rows.map(r => {
      const sp = r.suppress_pct || 0;
      const barCls = sp >= 50 ? 'bar-neg' : sp >= 20 ? 'bar-warn' : '';
      return `<div class="row" style="grid-template-columns:1fr 1fr 56px" title="${esc(r.regime)} · would-suppress ${r.would_suppress_n}/${r.n} net of the real round-trip fee (SHADOW)">
        <span class="name">${esc(r.regime)}</span>
        <span class="hbar ${barCls}"><i style="width:${sp.toFixed(0)}%"></i></span>
        <span class="num b-flat">${r.would_suppress_n}/${r.n}</span>
      </div>`;
    }).join('') : '<div class="empty">no admission-shadow rows (1h)</div>');
  }

  // ── CSS for the 5 new sub-renderers (2026-06-22 composite redesign) ───────
  // Uses ONLY the existing #board palette tokens (no new colours). Display-only.
  const EXT_CSS = `
  /* gate-feed / probe DECISION cell colouring (family-tinted, table cells). */
  #board td.gv-pass { color: var(--p-grn); font-weight: 700; }
  #board td.gv-kill { color: var(--p-red); font-weight: 700; }
  #board td.gv-other { color: var(--p-ylw); font-weight: 700; }
  #board td.gv-info { color: var(--p-cyn); font-weight: 700; }
  /* learner tremor (로직) — center-anchored |Δ1h| bar. */
  #board .lrn { display: grid; grid-template-columns: 1fr 70px 58px; align-items: center; gap: 8px; padding: 2px 10px; border-bottom: 1px dotted rgba(95,135,175,0.08); }
  #board .lrn .nm { color: var(--p-wht); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lrn .nm .sub { color: var(--p-dim); font-size: 9px; margin-left: 4px; }
  #board .lrn .trem { position: relative; height: 7px; background: rgba(95,135,175,0.12); }
  #board .lrn .trem .mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,0.18); }
  #board .lrn .trem i { position: absolute; top: 0; bottom: 0; }
  #board .lrn .trem i.up { left: 50%; background: var(--p-grn); }
  #board .lrn .trem i.dn { right: 50%; background: var(--p-red); }
  /* AI-free banner (로직). */
  #board .aifree-banner { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border: 1px solid rgba(95,135,175,0.22); border-left: 4px solid var(--p-grn);
    background: rgba(15,19,26,0.55); padding: 5px 12px; font-size: 11px; min-width: 0; }
  #board .aifree-banner.has-gpt { border-left-color: var(--p-ylw); }
  #board .aifree-banner .af-title { font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--p-grn); }
  #board .aifree-banner.has-gpt .af-title { color: var(--p-ylw); }
  #board .aifree-banner .af-kv { white-space: nowrap; }
  #board .aifree-banner .af-kv .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .aifree-banner .af-kv .lv { font-variant-numeric: tabular-nums; color: var(--p-wht); font-weight: 700; }
  /* decision pathway (로직) — one scannable line per active decision flow.
     strategy · regime · exit-style · venue · cell mult · edge. Monospace,
     pipe-separated, dense rows. No card chrome. */
  #board .pw { display: grid; grid-template-columns: 1fr 56px 64px; align-items: center; gap: 10px;
    padding: 2px 10px; border-bottom: 1px dotted rgba(95,135,175,0.08);
    font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; }
  #board .pw .flow { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--p-gry); }
  #board .pw .flow .sym { color: var(--p-wht); font-weight: 700; }
  #board .pw .flow .strat { color: var(--p-cyn); }
  #board .pw .flow .rgm { color: var(--p-mag); }
  #board .pw .flow .exit { color: var(--p-ylw); }
  #board .pw .flow .ven { color: var(--p-dim); }
  #board .pw .flow .sep { color: var(--ghost); margin: 0 5px; }
  #board .pw .mlt { text-align: right; color: var(--p-wht); }
  #board .pw .edge { text-align: right; }
  /* (h) active-strategy group (로직) — strategy header row + per-regime children.
     Each row binds regime explicitly + shows open count / uPnL / size. The
     strategy description (graceful) sits dim under the header. */
  #board .sg-head { display: grid; grid-template-columns: 1fr 54px 84px; align-items: baseline; gap: 8px;
    padding: 3px 10px 1px; border-bottom: 1px solid var(--ghost); }
  #board .sg-head .nm { color: var(--p-cyn); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .sg-head .k { color: var(--p-dim); font-size: 9px; letter-spacing: 0.06em; text-transform: uppercase; text-align: right; }
  #board .sg-head .pnl { text-align: right; font-variant-numeric: tabular-nums; font-weight: 700; }
  #board .sg-desc { color: var(--p-dim); font-size: 9px; padding: 0 10px 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board .sg-child { display: grid; grid-template-columns: 14px 1fr 110px 70px; align-items: baseline; gap: 8px;
    padding: 1px 10px 1px 16px; border-bottom: 1px dotted rgba(95,135,175,0.08);
    font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; }
  #board .sg-child .rgm { color: var(--p-mag); overflow: hidden; text-overflow: ellipsis; }
  #board .sg-child .sym { color: var(--p-wht); font-weight: 700; }
  #board .sg-child .held { color: var(--p-dim); font-size: 9px; margin-left: 6px; }
  #board .sg-child .pnl { text-align: right; }
  #board .sg-child .bullet { color: var(--ghost); }
  /* (h) active exits (로직) — exit-FSM left 'open' → state + why, regime bound. */
  #board .ax-row { display: grid; grid-template-columns: 1fr 84px 1fr 70px; align-items: baseline; gap: 8px;
    padding: 2px 10px; border-bottom: 1px dotted rgba(95,135,175,0.08);
    font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; }
  #board .ax-row .sym { color: var(--p-wht); font-weight: 700; overflow: hidden; text-overflow: ellipsis; }
  #board .ax-row .sym .strat { color: var(--p-cyn); font-weight: 400; font-size: 9px; margin-left: 5px; }
  #board .ax-row .st { color: var(--p-ylw); font-weight: 700; letter-spacing: 0.04em; text-align: right; }
  #board .ax-row .why { color: var(--p-gry); overflow: hidden; text-overflow: ellipsis; }
  #board .ax-row .why .rgm { color: var(--p-mag); }
  #board .ax-row .pnl { text-align: right; }
  /* P3 placeholder panes (개발 / 가야할길 / 배운것). */
  #board .ph { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 7px; color: var(--p-dim); text-align: center; }
  #board .ph .ph-icon { font-size: 20px; color: var(--ghost); letter-spacing: 0.3em; }
  #board .ph .ph-msg { font-size: 11px; letter-spacing: 0.06em; }
  #board .ph .ph-ep { font-size: 9px; color: var(--p-dim); letter-spacing: 0.10em; text-transform: uppercase; }
  `;
  (function injectExtStyle() {
    const s = document.createElement('style');
    s.id = 'board-tabs-ext-style';
    s.textContent = EXT_CSS;
    document.head.appendChild(s);
  })();

  // ── gate DECISIONS (활동) — d.gate_decisions[] per-gate meaningful output ────
  // Replaces the old pass/kill funnel. The bot is flow_not_block — gates never
  // block, so a pass/kill ratio was forever 100% / zero-information. Each row now
  // shows what that gate actually DECIDED for the window: G1 focus/excluded,
  // G2 signals fired, G5 the SIZE + tier mix, G6 N monitored + mode, G7 exit
  // action + reason, G8 lesson mix. One dense line per gate (no bar, no card).
  function renderGateFunnel(d) {
    const rows = d.gate_decisions || [];
    const body = $('gate-funnel'); if (!body) return;
    setCnt('gate-funnel-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">no gate decisions (1h)</div>'; return; }
    const trs = rows.map(g => `<tr title="G${g.gate_id} ${esc(g.label)} · ${esc(g.headline)} · n=${g.n}">
        <td class="l tk">G${g.gate_id}</td>
        <td class="l" style="color:var(--p-dim);font-size:10px">${esc(g.label || '')}</td>
        <td class="l" style="color:var(--p-grn)">${esc(g.headline || '—')}</td>
        <td>${g.n}</td>
      </tr>`).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:9%"><col style="width:24%"><col style="width:55%"><col style="width:12%">
       </colgroup><thead><tr>
        <th class="l">GATE</th><th class="l">NAME</th><th class="l">WHAT IT DECIDED</th><th>n</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── NEW · (g) live gate-activity feed (활동) — d.recent_gate_events[] ──────
  // Graceful when absent (backend builder adds the field later): shows a quiet
  // 'no gate events yet' line instead of erroring. Format per row:
  //   G# · PASS/KILL · strategy ticker · reason · hh:mm:ss   (newest first)
  // KILL red / PASS green. Field names bound defensively (decision|verdict|action,
  // strategy_id|strategy, symbol|ticker, reason|note, ts|ts_unix).
  // flow_not_block: gates emit meaningful decisions, not pass/kill. Keep the
  // verbatim decision verb and colour it by FAMILY — green for pass-through
  // (PASS/PROCEED/SIZED/REFLECTED), info-cyan for steady HOLD, amber for an
  // active intervention (ADJUST_EXIT/SWAP), red ONLY for a hard exit (EXIT_NOW).
  function gateVerdict(e) {
    const raw = String(e.decision || e.verdict || e.action || '').toUpperCase();
    if (!raw) return { txt: '—', cls: 'other' };
    if (/EXIT_NOW|KILL|REJECT|STOP_HIT/.test(raw)) return { txt: raw, cls: 'kill' };
    if (/ADJUST|WIDEN|SWAP/.test(raw)) return { txt: raw, cls: 'other' };
    if (/PASS|PROCEED|SIZED|REFLECTED|ADMIT|VALID|ENTER/.test(raw)) return { txt: raw, cls: 'pass' };
    if (/HOLD/.test(raw)) return { txt: raw, cls: 'info' };
    return { txt: raw, cls: 'other' };
  }
  // Inline per-gate detail string for the feed (G5 T4 size line / G8 lesson /
  // G1 focus). Returns '' when the event carries no detail → row unchanged.
  function gateDetailStr(e) {
    const x = e.detail; if (!x || typeof x !== 'object') return '';
    if (e.gate_id === 5) {
      const bits = [];
      if (x.risk_pct != null) bits.push(`${(+x.risk_pct).toFixed(2)}%`);
      if (x.notional_usd != null) bits.push('$' + Math.round(+x.notional_usd).toLocaleString());
      if (x.scalar != null) bits.push('×' + (+x.scalar).toFixed(2));
      if (x.tier != null) bits.push('t' + (+x.tier) + 'x');
      if (x.cell != null) bits.push('c' + (+x.cell));
      return bits.join(' ');
    }
    if (e.gate_id === 8) {
      const bits = [];
      if (x.lesson_type) bits.push(String(x.lesson_type));
      if (x.confidence != null) bits.push('conf ' + (+x.confidence).toFixed(2));
      return bits.join(' ');
    }
    if (e.gate_id === 1 && x.focus_n != null) return 'focus ' + x.focus_n;
    return '';
  }
  // Venue lane ('a'/'b'/'c'/'') for a gate-event token. Prefers an explicit venue
  // string (via venueStream A/B/C → a/b/c); else infers from a crypto symbol
  // (…-USDT/USDC/USD → okx lane 'a'). Returns '' when unknown (no mis-colour).
  function gfVenueLane(token) {
    const t = String(token || '');
    const st = venueStream(t);   // explicit venue → A/B/C, else 'X'
    if (st === 'A') return 'a';
    if (st === 'B') return 'b';
    if (st === 'C') return 'c';
    if (/-USDT$|-USDC$|-USD$/.test(t.toUpperCase()) && t.indexOf('-') > 0) return 'a';
    return '';
  }
  const GF_VENUE_TAG = { a: 'OKX', b: 'CAP', c: 'ALP' };
  function gfVenueTag(lane) { return GF_VENUE_TAG[lane] || ''; }

  // Clean trading symbol from a probe row. The backend now parses
  // pos_<hash>_<venue>_<SYMBOL>_<ts> correctly, but defend in JS too so a raw
  // id never leaks into a cell regardless of restart timing. Returns {venue, sym}.
  const _POS_ID_RE = /_(okx|capital|alpaca)_(.+)_\d+$/i;
  function probeVenueSym(pr) {
    const raw = String(pr.ticker || pr.symbol || '');
    let venue = pr.venue || '';
    let sym = raw;
    const m = raw.match(_POS_ID_RE);
    if (m) { if (!venue) venue = m[1].toLowerCase(); sym = m[2]; }
    return { venue, sym };
  }
  function probeRows(d) {
    return (d.probe_events || d.probes || []).map(pr => {
      const vs = probeVenueSym(pr);
      return {
        name: pr.name || pr.probe || pr.id || 'probe',
        sym: vs.sym, venue: vs.venue,
        kind: pr.kind || '',
        lean: (pr.lean != null) ? +pr.lean : null,
        conf: (pr.confidence != null) ? +pr.confidence : null,
        action: pr.action || '',
        reason: pr.reason || (pr.reading != null ? String(pr.reading) : ''),
        ts: pr.ts || pr.ts_unix || 0,
        gate_id: pr.gate_id,
      };
    });
  }

  let _lastFeedD = null;   // last snapshot for SSE-driven live re-render
  // ── Live Gate Activity (활동) — d.recent_gate_events[] + SSE, as a TABLE ───
  // Probe rows are NOT merged here (they have their own Probe Activity panel).
  // Columns align row-to-row: TIME | G# | DECISION | STRATEGY | SYMBOL | DETAIL.
  function renderGateFeed(d) {
    _lastFeedD = d;
    const body = $('gate-feed'); if (!body) return;
    // Two sources, one time-sorted stream: the 1s snapshot poll
    // (d.recent_gate_events, the window backfill) + the live SSE gate-decision
    // buffer (window.__gateStream — pushed between polls for smooth realtime).
    // Deduped by (gate_id|ts|symbol|decision) so a streamed row and its later
    // poll-backfill twin collapse to one line.
    const stream = (window.__gateStream || []);
    const merged = (d.recent_gate_events || []).slice().concat(stream);
    const seen = new Set();
    const rows = [];
    for (const e of merged) {
      const k = (e.gate_id != null ? e.gate_id : '?') + '|'
        + (e.ts || e.ts_unix || 0) + '|' + (e.symbol || e.ticker || '') + '|'
        + (e.decision || '');
      if (seen.has(k)) continue;
      seen.add(k); rows.push(e);
    }
    setCnt('gate-feed-cnt', rows.length || '');
    if (!rows.length) { body.innerHTML = '<div class="empty">no notable gate decisions in window</div>'; return; }
    rows.sort((a, b) => (b.ts || b.ts_unix || 0) - (a.ts || a.ts_unix || 0));
    const trs = rows.slice(0, 150).map(e => {
      const sym = e.symbol || e.ticker || '';
      const why = e.reason || e.note || '';
      const ts = e.ts || e.ts_unix;
      const tstr = ts ? hhmmss(ts) : '';
      const v = gateVerdict(e);
      const g = (e.gate_id != null) ? ('G' + e.gate_id) : 'G?';
      const label = e.label || e.gate_label || '';
      const strat = e.strategy_id || e.strategy || '';
      const det = gateDetailStr(e);   // G5 T4 size / G8 lesson / G1 focus inline
      const detail = det || why;
      return `<tr title="${esc(g)} ${esc(label)} ${esc(v.txt)} · ${esc(strat)} ${esc(sym)} · ${esc(detail)} · ${esc(tstr)}">
        <td class="l" style="color:var(--p-dim)">${esc(tstr)}</td>
        <td class="l" style="color:var(--p-cyn);font-weight:700">${esc(g)}</td>
        <td class="l gv-${v.cls}">${esc(v.txt)}</td>
        <td class="l" style="color:var(--p-cyn)">${esc(strat || '—')}</td>
        <td class="l tk">${esc(sym || '—')}</td>
        <td class="l" style="color:var(--p-gry)">${det ? '<span style=\"color:var(--p-ylw)\">' + esc(det) + '</span>' : esc(why)}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:11%"><col style="width:7%"><col style="width:15%"><col style="width:18%">
        <col style="width:17%"><col style="width:32%">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">G#</th><th class="l">DECISION</th><th class="l">STRATEGY</th>
        <th class="l">SYMBOL</th><th class="l">DETAIL</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── Probe List (활동) — positions currently under probe, grouped by symbol ──
  // One row per probed (venue, symbol): #probes · mean lean · mean conf · gate.
  function renderProbeList(d) {
    const body = $('probe-list'); if (!body) return;
    const all = probeRows(d);
    if (!all.length) { setCnt('probe-list-cnt', ''); body.innerHTML = '<div class="empty">no positions under probe</div>'; return; }
    const groups = {};
    all.forEach(p => {
      const k = p.venue + '|' + p.sym;
      const g = groups[k] || (groups[k] = { venue: p.venue, sym: p.sym, n: 0, leanSum: 0, leanN: 0, confSum: 0, confN: 0, gate: p.gate_id });
      g.n += 1;
      if (p.lean != null) { g.leanSum += p.lean; g.leanN += 1; }
      if (p.conf != null) { g.confSum += p.conf; g.confN += 1; }
    });
    const rows = Object.values(groups).sort((a, b) => b.n - a.n);
    setCnt('probe-list-cnt', rows.length);
    const trs = rows.map(g => {
      const lean = g.leanN ? g.leanSum / g.leanN : null;
      const conf = g.confN ? g.confSum / g.confN : null;
      const leanCls = lean == null ? 'b-flat' : pn(lean);
      return `<tr title="${esc(g.venue)} ${esc(g.sym)} · ${g.n} probes · lean ${lean == null ? '—' : lean.toFixed(2)} · conf ${conf == null ? '—' : conf.toFixed(2)} · G${g.gate}">
        <td class="l tk">${esc(g.sym)}</td>
        <td class="l ex">${esc(g.venue || '—')}</td>
        <td>${g.n}</td>
        <td class="${leanCls}">${lean == null ? '—' : (lean >= 0 ? '+' : '') + lean.toFixed(2)}</td>
        <td class="b-flat">${conf == null ? '—' : conf.toFixed(2)}</td>
        <td>G${g.gate}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:30%"><col style="width:18%"><col style="width:13%"><col style="width:15%">
        <col style="width:14%"><col style="width:10%">
       </colgroup><thead><tr>
        <th class="l">SYMBOL</th><th class="l">VEN</th><th title="probes on this position">#PRB</th>
        <th title="mean probe lean">LEAN</th><th title="mean probe confidence">CONF</th><th>GATE</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── Probe Activity (활동) — per-probe readings stream, newest first, TABLE ──
  // TIME | PROBE | SYMBOL | KIND | READING (lean/conf) | ACTION.
  function renderProbeActivity(d) {
    const body = $('probe-activity'); if (!body) return;
    const rows = probeRows(d).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));
    setCnt('probe-activity-cnt', rows.length || '');
    if (!rows.length) { body.innerHTML = '<div class="empty">no probe readings (1h)</div>'; return; }
    const trs = rows.slice(0, 120).map(p => {
      const tstr = p.ts ? hhmmss(p.ts) : '';
      const reading = (p.lean != null || p.conf != null)
        ? `${p.lean == null ? '' : (p.lean >= 0 ? '+' : '') + p.lean.toFixed(2)}${p.conf == null ? '' : ' / ' + p.conf.toFixed(2)}`
        : esc(p.reason);
      const leanCls = p.lean == null ? 'b-flat' : pn(p.lean);
      return `<tr title="PROBE ${esc(p.name)} · ${esc(p.sym)} · ${esc(p.kind)} · lean ${p.lean == null ? '—' : p.lean.toFixed(2)} conf ${p.conf == null ? '—' : p.conf.toFixed(2)} · ${esc(p.action || '')} · ${esc(tstr)}">
        <td class="l" style="color:var(--p-dim)">${esc(tstr)}</td>
        <td class="l" style="color:var(--p-cyn)">${esc(p.name)}</td>
        <td class="l tk">${esc(p.sym)}</td>
        <td class="l" style="color:var(--p-dim)">${esc(p.kind)}</td>
        <td class="${leanCls}">${reading}</td>
        <td class="l" style="color:var(--p-ylw)">${esc(p.action || '—')}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:12%"><col style="width:20%"><col style="width:16%"><col style="width:15%">
        <col style="width:21%"><col style="width:16%">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">PROBE</th><th class="l">SYMBOL</th><th class="l">KIND</th>
        <th title="lean / confidence">READING</th><th class="l">ACTION</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── NEW · per-ticker R (퍼포먼스) — d.ticker_stats[] cumulative R by symbol ─
  function renderTickerStats(d) {
    const rows = venueFilter(d.ticker_stats);   // E3 venue scope (carries venue)
    const body = $('ticker-body'); if (!body) return;
    setCnt('ticker-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no per-ticker R yet${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    const sorted = rows.slice().sort((a, b) => (a.sum_r || 0) - (b.sum_r || 0));   // worst→best
    const trs = sorted.map(t => {
      const r = t.sum_r || 0;
      return `<tr title="${esc(t.venue)} ${esc(t.symbol)} · n=${t.n} · WR ${(t.wr_pct || 0).toFixed(0)}% · ΣR ${r.toFixed(2)}">
        <td class="l tk">${symCell(t.symbol, t.name, sparkline(t.spark))}</td>
        <td class="l ex">${esc(t.venue)}</td>
        <td class="b-flat">${t.n || 0}</td>
        <td class="b-flat">${(t.wr_pct || 0).toFixed(0)}%</td>
        <td class="${pn(r)}">${r >= 0 ? '+' : ''}${r.toFixed(2)}R</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:34%"><col style="width:20%"><col style="width:13%"><col style="width:15%"><col style="width:18%">
       </colgroup><thead><tr>
        <th class="l">SYMBOL</th><th class="l">VEN</th><th>N</th><th>WR</th><th>ΣR</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── NEW · learner network (로직) — d.learners[] value + Δ1h tremor ─────────
  function renderLearners(d) {
    const rows = d.learners || [];
    const body = $('learner-body'); if (!body) return;
    setCnt('learner-body-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">no learner slots yet</div>'; return; }
    const maxAbs = rows.reduce((m, l) => Math.max(m, Math.abs(l.delta_1h || 0)), 0) || 1;
    body.innerHTML = rows.map(l => {
      const dlt = l.delta_1h || 0;
      const w = (Math.abs(dlt) / maxAbs) * 50;   // half-width = full tremor
      const dir = dlt > 0 ? 'up' : dlt < 0 ? 'dn' : '';
      return `<div class="lrn" title="${esc(l.learner_id)} · ${esc(l.key)} = ${(l.value || 0).toFixed(4)} · Δ1h ${dlt >= 0 ? '+' : ''}${dlt.toFixed(4)} · n_eff ${(l.n_eff || 0).toFixed(0)}">
        <span class="nm">${esc(l.key)}<span class="sub">${esc(l.learner_id)}</span></span>
        <span class="trem"><span class="mid"></span>${dir ? `<i class="${dir}" style="width:${w.toFixed(1)}%"></i>` : ''}</span>
        <span class="num ${pn(dlt)}" style="text-align:right;font-size:10px">${dlt >= 0 ? '+' : ''}${dlt.toFixed(3)}</span>
      </div>`;
    }).join('');
  }

  // ── NEW · decision pathways (로직) — one scannable line per active flow ────
  // Bloomberg de-grid (2026-06-22): the 9-panel Logic grid becomes a pathway
  // LIST. Each open position is one decision flow rendered inline:
  //   SYM · strategy · regime · exit-style · venue   [cell mult] [edge ±R]
  // Reuses live snapshot bindings only (positions = active flows; edge/conf
  // cells supply the per-(strategy×regime) expectancy; no new data, no mock).
  const REGIME_PLAIN_PW = {
    bull_trend: 'trending up', bear_trend: 'trending down', chop: 'choppy',
    crisis: 'volatile', range: 'range-bound', trend: 'trending',
    calm: 'calm', quiet: 'quiet', neutral: 'neutral',
  };
  function regimePlainPw(r) {
    if (!r) return '—';
    const k = String(r).toLowerCase();
    return REGIME_PLAIN_PW[k] || k.replace(/_/g, ' ');
  }
  // exit-style label from the FSM state + whether a protective stop is set.
  const EXIT_STYLE = {
    open: 'ATR-trail exit', touched: 'target touched', protected: 'stop locked',
    harvest: 'harvesting', '': 'ATR-trail exit',
  };
  function exitStyle(p) {
    const st = String(p.exit_state || '').toLowerCase();
    if (EXIT_STYLE[st]) return EXIT_STYLE[st];
    return (p.stop_price > 0) ? 'stop-guarded exit' : 'ATR-trail exit';
  }
  // Build a (strategy|regime) → expectancy R lookup from confidence cells +
  // edge_validation (whichever carries the pair). Display-only edge context.
  function edgeLookup(d) {
    const map = {};
    const cells = (d.confidence && d.confidence.cells) || [];
    cells.forEach(c => {
      const k = (c.strategy_id + '|' + c.regime).toLowerCase();
      if (map[k] == null) map[k] = c.lcb_real_fee_net_r;
    });
    (d.edge_validation || []).forEach(e => {
      const k = (e.strategy + '|' + e.regime).toLowerCase();
      if (map[k] == null) map[k] = e.cost_adj_exp;
    });
    return map;
  }
  function renderPathways(d) {
    const body = $('pathway-body'); if (!body) return;
    const rows = venueFilter(d.positions);   // active flows = open positions (E3 scope)
    setCnt('pathway-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no active decision flows${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    const edge = edgeLookup(d);
    const flows = rows.slice().sort((a, b) => (b.size_usd || 0) - (a.size_usd || 0));
    body.innerHTML = flows.map(p => {
      const sep = '<span class="sep">·</span>';
      const mlt = (p.cell_mult || 1);
      const ek = (String(p.strategy_id) + '|' + String(p.regime)).toLowerCase();
      const er = edge[ek];
      const edgeHtml = (er == null)
        ? '<span class="b-flat">—</span>'
        : `<span class="${pn(er)}">${er >= 0 ? '+' : ''}${er.toFixed(2)}R</span>`;
      const flow = `<span class="sym">${esc(p.symbol)}</span>${sep}`
        + `<span class="strat">${esc(p.strategy_id)}</span>${sep}`
        + `<span class="rgm">${esc(regimePlainPw(p.regime))}</span>${sep}`
        + `<span class="exit">${esc(exitStyle(p))}</span>${sep}`
        + `<span class="ven">${esc(p.venue)}</span>`;
      return `<div class="pw" title="${esc(p.symbol)} · ${esc(p.strategy_id)} · regime ${esc(p.regime || '—')} · ${esc(exitStyle(p))} · ${esc(p.venue)} · cell ${mlt.toFixed(1)}× · edge ${er == null ? 'n/a' : (er >= 0 ? '+' : '') + er.toFixed(2) + 'R'} · uPnL ${fmtUsd(p.upnl_usd, 2)}">
        <span class="flow">${flow}</span>
        <span class="mlt">${mlt.toFixed(1)}×</span>
        <span class="edge">${edgeHtml}</span>
      </div>`;
    }).join('');
  }

  // ── NEW · (h) active strategies grouped (로직) ────────────────────────────
  // Groups open positions by strategy, then by regime, so at a glance you see
  // which strategy in which regime is performing how. strategy_descriptions
  // (graceful — absent until the backend adds it) renders as a one-line dim
  // caption, omitted when absent. Edge expectancy per (strategy×regime) reused
  // from the confidence/edge lookup. No new data, no mock.
  function renderStrategyGroups(d) {
    const body = $('stratgrp-body'); if (!body) return;
    const rows = venueFilter(d.positions);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no active strategies${sc ? ' · ' + esc(sc) : ''}</div>`;
      setCnt('stratgrp-body-cnt', '');
      return;
    }
    const desc = d.strategy_descriptions || {};   // graceful: {} when absent
    const edge = edgeLookup(d);
    // group: strategy_id → { posList, pnl, size }
    const groups = {};
    rows.forEach(p => {
      const sid = p.strategy_id || '—';
      const g = groups[sid] || (groups[sid] = { sid, pos: [], pnl: 0, size: 0 });
      g.pos.push(p); g.pnl += (p.upnl_usd || 0); g.size += (p.size_usd || 0);
    });
    const ordered = Object.values(groups).sort((a, b) => b.size - a.size);
    setCnt('stratgrp-body-cnt', ordered.length);
    body.innerHTML = ordered.map(g => {
      const dline = desc[g.sid] ? `<div class="sg-desc" title="${esc(desc[g.sid])}">${esc(desc[g.sid])}</div>` : '';
      const head =
        `<div class="sg-head" title="${esc(g.sid)} · ${g.pos.length} open · uPnL ${fmtUsd(g.pnl, 2)} · notional ${fmtUsd(g.size, 0)}">
          <span class="nm">${esc(g.sid)}</span>
          <span class="k">${g.pos.length} open</span>
          <span class="pnl ${pn(g.pnl)}">${fmtUsd(g.pnl, 0)}</span>
        </div>${dline}`;
      const kids = g.pos.slice().sort((a, b) => (b.size_usd || 0) - (a.size_usd || 0)).map(p => {
        const ek = (String(p.strategy_id) + '|' + String(p.regime)).toLowerCase();
        const er = edge[ek];
        const eHtml = (er == null) ? '<span class="b-flat">—</span>'
          : `<span class="${pn(er)}">${er >= 0 ? '+' : ''}${er.toFixed(2)}R</span>`;
        return `<div class="sg-child" title="${esc(p.symbol)} · regime ${esc(p.regime || '—')} · uPnL ${fmtUsd(p.upnl_usd, 2)} · held ${hms(p.held_sec)} · edge ${er == null ? 'n/a' : (er >= 0 ? '+' : '') + er.toFixed(2) + 'R'}">
          <span class="bullet">└</span>
          <span class="rgm">${esc(regimePlainPw(p.regime))} <span class="sym">${esc(p.symbol)}</span><span class="held">${hms(p.held_sec)}</span></span>
          <span class="pnl ${pn(p.upnl_usd)}">${fmtUsd(p.upnl_usd, 2)}</span>
          <span class="pnl">${eHtml}</span>
        </div>`;
      }).join('');
      return head + kids;
    }).join('');
  }

  // ── NEW · (h) active exits (로직) — positions leaving 'open' + why ─────────
  // Active exit = a position whose exit FSM state is NOT 'open' (touched /
  // protected / harvest …). Shows the FSM state, an English why (exit style +
  // regime binding), and live uPnL. Empty when all positions are still 'open'.
  function renderActiveExits(d) {
    const body = $('activeexit-body'); if (!body) return;
    const rows = venueFilter(d.positions).filter(p => {
      const st = String(p.exit_state || '').toLowerCase();
      return st && st !== 'open';
    });
    setCnt('activeexit-body-cnt', rows.length || '');
    if (!rows.length) { body.innerHTML = '<div class="empty">no active exits — all open positions still riding</div>'; return; }
    body.innerHTML = rows.slice().sort((a, b) => (b.size_usd || 0) - (a.size_usd || 0)).map(p => {
      const st = String(p.exit_state || '').toUpperCase();
      return `<div class="ax-row" title="${esc(p.symbol)} · ${esc(p.strategy_id)} · exit ${esc(st)} · ${esc(exitStyle(p))} · regime ${esc(p.regime || '—')} · uPnL ${fmtUsd(p.upnl_usd, 2)}">
        <span class="sym">${esc(p.symbol)}<span class="strat">${esc(p.strategy_id)}</span></span>
        <span class="st">${esc(st)}</span>
        <span class="why">${esc(exitStyle(p))} · <span class="rgm">${esc(regimePlainPw(p.regime))}</span></span>
        <span class="pnl ${pn(p.upnl_usd)}">${fmtUsd(p.upnl_usd, 2)}</span>
      </div>`;
    }).join('');
  }

  // ── NEW · AI-free banner (로직) — in-loop GPT calls = 0 (W3 cutover) ───────
  function renderAiFreeBanner(d) {
    const el = $('aifree-banner'); if (!el) return;
    const gpt = d.gpt_stats || [];
    const callsH = gpt.reduce((a, g) => a + (g.calls_per_h || 0), 0);
    const ai = d.ai_shadow || {};
    const sa = ai.shadow_agreement || [];
    const nShadow = sa.reduce((a, r) => a + (r.n || 0), 0);
    const hasGpt = callsH > 0;
    el.className = 'aifree-banner' + (hasGpt ? ' has-gpt' : '');
    el.innerHTML =
      `<span class="af-title">${hasGpt ? 'in-loop GPT active' : 'AI-FREE CORE · in-loop GPT = 0'}</span>`
      + `<span class="af-kv" title="GPT calls/h across all gates in the live decision loop (W3 cutover target = 0)"><span class="lk">in-loop GPT/h</span> <span class="lv ${hasGpt ? 'b-neg' : 'b-pos'}">${callsH}</span></span>`
      + `<span class="af-kv" title="deterministic-vs-GPT shadow comparisons observed (1h) — observe-only, never blocks"><span class="lk">shadow obs (1h)</span> <span class="lv">${nShadow}</span></span>`
      + `<span class="af-kv"><span class="lk">decisioning</span> <span class="lv">deterministic G3/G4/G7</span></span>`;
  }

  // ── NEW · worst strategies (Performance) — moved from the old KPI BLEED block.
  // Strategy PnL, worst (most negative) first. Plain English, no "bleed" jargon.
  function renderWorst(d) {
    const body = $('worst-body'); if (!body) return;
    const rows = (d.strategy_stats || []).slice()
      .sort((a, b) => (a.pnl_usd || 0) - (b.pnl_usd || 0)).slice(0, 6);
    setCnt('worst-body-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">no strategy stats yet</div>'; return; }
    body.innerHTML = rows.map(s =>
      `<div class="row" style="grid-template-columns:1fr 84px 70px 44px"
        title="${esc(s.strategy_id)} · PnL ${fmtUsd(s.pnl_usd, 2)} · profit factor ${(s.pf == null ? 0 : s.pf).toFixed(2)} · ${s.closed_n || 0} closed trades">
        <span class="name">${esc(s.strategy_id)}</span>
        <span class="num ${pn(s.pnl_usd)}">${fmtUsd(s.pnl_usd, 0)}</span>
        <span class="num b-flat" title="profit factor">PF ${(s.pf == null ? 0 : s.pf).toFixed(2)}</span>
        <span class="num b-flat" title="closed trades">${s.closed_n || 0}t</span>
      </div>`).join('');
  }

  // ── NEW · Per-Venue breakdown (Performance, Jin 2026-06-26) — the equity-curve
  // story split per stream lane (OKX / Capital / Alpaca). Pure read off the
  // already-present d.streams (StreamSummary) rollup — no new endpoint, no sizing
  // touch. Columns: VEN | NET$ | TRD | OPEN | EXPOSED | uPnL$. Always renders all
  // three lanes (StreamSummary emits a row per lane even at zero activity).
  function renderVenueBreakdown(d) {
    const body = $('venue-body'); if (!body) return;
    const rows = d.streams || [];
    setCnt('venue-body-cnt', rows.length || '');
    if (!rows.length) { body.innerHTML = '<div class="empty">no stream summary</div>'; return; }
    const trs = rows.map(s => {
      const lc = venueStream(s.venue).toLowerCase();
      const net = s.net_pnl_usd || 0;
      const up = s.upnl_usd || 0;
      const closed = (s.closed_n != null ? s.closed_n : (s.daily_trades || 0));
      return `<tr class="row-${lc}" title="${esc(s.label || s.venue)} · net ${fmtUsd(net, 2)} · ${closed} trades (${s.daily_trades || 0} close fills) · ${s.open_positions_n || 0} open · exposed ${fmtUsd(s.exposed_usd, 0)} · uPnL ${fmtUsd(up, 2)}">
          <td class="l ex">${esc(s.label || s.venue)}</td>
          <td class="num ${pn(net)}">${fmtUsd(net, 2)}</td>
          <td class="num b-flat">${closed}</td>
          <td class="num b-flat">${s.open_positions_n || 0}</td>
          <td class="num b-flat">${fmtUsd(s.exposed_usd, 0)}</td>
          <td class="num ${pn(up)}">${fmtUsd(up, 2)}</td>
        </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:26%"><col style="width:18%"><col style="width:11%"><col style="width:12%">
        <col style="width:17%"><col style="width:16%">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th title="session realised, net of fees">NET$</th><th title="closed trades">TRD</th>
        <th title="open positions">OPEN</th><th title="deployed notional">EXPOSED</th><th title="unrealised PnL">uPnL$</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── NEW · costs & hidden losses (Performance) — moved from the old KPI HIDDEN
  // block. Plain English: slippage, net after costs, drift losses from un-exited
  // positions, forced exits. No "BLEED / orphan-drift / net-after-cost" jargon.
  function renderCosts(d) {
    const body = $('costs-body'); if (!body) return;
    const st = d.streams || [];
    const slip = st.reduce((a, s) => a + (s.slippage_usd || 0), 0);
    const fee = st.reduce((a, s) => a + (s.fee_usd || 0), 0);
    const nac = st.reduce((a, s) => a + (s.net_after_cost_usd || 0), 0);
    // Hardening #1: PRIMARY = actual realized Σ pnl_usd over reconciled positions
    // (real lost $); the mae_r×risk est is the labeled secondary fallback.
    const driftActual = d.reconciled_realized_usd || 0;
    const driftEst = d.reconciled_loss_usd || 0;
    const driftUsd = driftActual !== 0 ? driftActual : driftEst;
    const driftIsEst = driftActual === 0 && driftEst !== 0;
    const driftN = d.reconciled_loss_n || 0;
    const forced = d.session_forced_exit_count || 0;
    const row = (label, valHtml, tip) =>
      `<div class="row" style="grid-template-columns:1fr 110px" title="${esc(tip || label)}">
        <span class="name b-flat" style="font-weight:400">${esc(label)}</span>
        <span class="num">${valHtml}</span></div>`;
    body.innerHTML =
      row('Fees paid', `<span class="b-neg">${fmtUsd(fee, 0)}</span>`,
        'total trading fees paid this session')
      + row('Slippage', `<span class="b-neg">${fmtUsd(slip, 0)}</span>`,
        'execution slippage vs intended price')
      + row('Net profit after all costs', `<span class="${pn(nac)}">${fmtUsd(nac, 0)}</span>`,
        'realized profit after fees and slippage')
      + row(`Tracking failures, not trades (${driftN})`, `<span class="b-neg">${fmtUsd(-Math.abs(driftUsd), 0)}${driftIsEst ? ' est' : ''}</span>`,
        (driftIsEst
          ? 'rough $ estimate (mae_r × risk) of drift from positions reconciled away without a clean exit'
          : 'actual realized $ from positions reconciled away without a clean exit')
        + ' — excluded from the R ledger / PF / win-rate')
      + row('Forced exits', `<span class="b-flat">${forced}</span>`,
        'positions the system was forced to close this session');
  }

  // ── NEW · dual equity sparkline (퍼포먼스) ─────────────────────────────────
  // Reuses the legacy renderEquity (eq-svg / eq-real-delta / eq-demo-delta /
  // eq-fee-wedge) — it already draws BOTH curves (real-fee-net headline + demo
  // dashed) with the GAP visible. No duplicate sparkline logic.
  function renderDualEquity(d) { renderEquity(d); }

  // ── NEW · CONTEXT/INTEL (alt-data inputs the bot weighs) ──────────────────
  // One Bloomberg-dense line per source from d.context_intel[] (the LATEST
  // altdata_snapshot row per source, summarised server-side). Shows source ·
  // asset-class · latest value · freshness (green=fresh / grey=stale) · lean
  // (bullish/bearish/neutral). Read-only "bot's eyes" — never feeds trading.
  const _CTX_LABEL = {
    okx_funding: 'OKX Funding', crypto_fg: 'Crypto Fear&Greed',
    fred_macro: 'FRED Macro', cftc_cot: 'CFTC COT',
    news_sentiment: 'News Sentiment', coinglass: 'Coinglass',
    myfxbook: 'MyFxBook',
  };
  function _ctxAge(sec) {
    const s = Math.max(0, sec | 0);
    if (s < 90) return s + 's';
    if (s < 5400) return Math.round(s / 60) + 'm';
    return Math.round(s / 3600) + 'h';
  }
  function renderContextIntel(d) {
    const rows = d.context_intel || [];
    const body = $('context-body'); if (!body) return;
    setCnt('context-body-cnt', rows.length);
    if (!rows.length) {
      body.innerHTML = '<div class="empty">no context inputs yet · collectors warming up (funding · F&amp;G · FRED · COT · news)</div>';
      return;
    }
    const trs = rows.map(c => {
      const sig = String(c.signal || 'neutral');
      const sigCls = sig === 'bullish' ? 'b-pos' : sig === 'bearish' ? 'b-neg' : 'b-flat';
      const freshCls = c.fresh ? 'b-pos' : 'b-flat';
      const freshTxt = (c.fresh ? '● ' : '○ ') + _ctxAge(c.age_sec);
      const name = _CTX_LABEL[c.source] || c.source;
      return `<tr title="${esc(c.source)} · ${esc(c.asset_class)} · age ${c.age_sec | 0}s · ${c.fresh ? 'fresh' : 'stale'} · ${esc(sig)}">
        <td class="l tk">${esc(name)}</td>
        <td class="l ex">${esc(c.asset_class)}</td>
        <td class="l">${esc(c.latest_value)}</td>
        <td class="${freshCls}" style="font-size:9px">${esc(freshTxt)}</td>
        <td class="${sigCls}">${esc(sig)}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:18%"><col style="width:11%"><col style="width:45%"><col style="width:12%"><col style="width:14%">
       </colgroup><thead><tr>
        <th class="l">SOURCE</th><th class="l">CLASS</th><th class="l">LATEST</th><th>FRESH</th><th>LEAN</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── NEW · P3 placeholder (개발 / 가야할길 / 배운것) ────────────────────────
  function placeholder(bodyId, msg, endpoint) {
    const body = $(bodyId); if (!body) return;
    body.innerHTML = `<div class="ph"><div class="ph-icon">· · ·</div>`
      + `<div class="ph-msg">${esc(msg)}</div>`
      + `<div class="ph-ep">P3 planned · ${esc(endpoint)}</div></div>`;
  }

  // ── register the 6 COMPOSITE tab renderers ────────────────────────────────
  // Each composite reuses the base table renderers (T.renderers) + the legacy
  // analytics renderers + the new sub-renderers. No mock data — every field is
  // a live /api/snapshot value.
  const R = T.renderers;   // { positions, trades, regime, strategy }
  T.register('activity', (d) => {
    R.positions(d);
    R.trades(d);
  });
  // Gates tab (Jin 2026-06-24): the gate/probe quad moved out of Activity.
  T.register('gates', (d) => {
    renderGateFunnel(d);
    renderProbeList(d);
    renderGateFeed(d);
    renderProbeActivity(d);
  });
  T.register('performance', (d) => {
    renderDualEquity(d); renderConfidence(d); renderBenchmark(d); renderGateValue(d);
    renderVenueBreakdown(d);
    R.strategy(d);
    renderTickerStats(d);
    renderEdge(d);
    renderWorst(d);
    renderCosts(d);
  });
  T.register('logic', (d) => {
    renderAiFreeBanner(d);
    renderRotation(d);
    renderStrategyGroups(d);
    renderActiveExits(d);
    renderPathways(d);
    R.regime(d);
    renderCells(d);
    renderLearners(d);
    renderExit(d);          // exit-fsm chips + exit-reasons + exit-gates
    renderAi(d);            // ai-gpt + ai-shadow
    renderRiskAdmit(d);
    renderAlerts(d);
  });
  // SSE-driven live feed re-render: the gate-decision stream (globe-flows.js)
  // pushes into window.__gateStream then calls this to re-merge between polls.
  // No-op until the first snapshot has primed _lastFeedD. Display-only.
  T.renderGateFeedLive = () => { if (_lastFeedD) renderGateFeed(_lastFeedD); };

  T.register('context', (d) => { renderContextIntel(d); });
  T.register('build', () => placeholder('build-body', 'commit timeline · wave digest · activity heat · test-health', 'GET /api/buildlog'));
  T.register('path', () => placeholder('path-body', 'phase ladder P0→P6 · plan kanban · next strikes · blockers', 'GET /api/roadmap'));
  T.register('learned', () => placeholder('learned-body', 'lessons feed · anti-pattern wall · root-cause · debate verdicts', 'GET /api/lessons'));

  // LEGACY tab (Jin 2026-07-07 virtual-primary restructure) — pre-virtual
  // real-venue accounting, only reachable here in VIRTUAL mode (the header/
  // strip show the VIRTUAL $100k×3 primary readout instead). In REAL mode
  // (virtual OFF) this pane is intentionally left blank — that content still
  // renders in its original header/strip spot, unchanged.
  T.register('legacy', (d) => {
    const kpiEl = $('legacy-kpi-body');
    if (kpiEl) kpiEl.innerHTML = d.virtual_account_enabled ? legacyKpiHtml(d) : '';
    if (d.virtual_account_enabled) renderStreamsInto(d, 'legacy-streams-body');
    else { const sEl = $('legacy-streams-body'); if (sEl) sEl.innerHTML = ''; }
  });
})();
