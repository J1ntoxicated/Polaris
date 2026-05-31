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
  const { $, fmtUsd, fmtPct, fmtSignedPct, fmtPx, fmtR, pn, esc, hms, hhmmss,
    venueStream } = B;
  const setCnt = T.setCnt;

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
      rbody.innerHTML = reasons.length ? reasons.map(r => `
        <div class="row" style="grid-template-columns:90px 1fr 40px" title="${esc(r.reason)} ×${r.count}">
          <span class="name">${esc(r.reason)}</span>
          <span class="hbar"><i style="width:${(r.count / max * 100).toFixed(0)}%"></i></span>
          <span class="num b-flat">${r.count}</span>
        </div>`).join('') : '<div class="empty">no closed trades</div>';
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
      gbody.innerHTML = gpt.length ? gpt.map(g => {
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
      }).join('') : '<div class="empty">deterministic · no GPT calls (1h)</div>';
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
      }).join('') : '<div class="empty">no shadow events (1h)</div>';
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
  function renderEdge(d) {
    const rows = d.edge_validation || [];
    const body = $('edge-body'); if (!body) return;
    setCnt('edge-body-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">edge validation warming up</div>'; return; }
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
    const last = d.last_rotation || null;
    let lastHtml;
    if (last) {
      const eNew = last.e_new || 0, eHeld = last.e_held || 0;
      lastHtml = `<span class="rt-last" title="last rotation: ${esc(last.venue)} closed ${esc(last.victim_symbol)} (${esc(last.victim_strategy)}) → E$new ${eNew.toFixed(2)} vs E$held ${eHeld.toFixed(2)}">`
        + `last <b>${esc(last.victim_symbol)}</b> `
        + `<span class="${pn(eNew - eHeld)}">E$new ${eNew.toFixed(1)}</span> vs E$held ${eHeld.toFixed(1)} `
        + `· mgn ${(last.margin || 0).toFixed(1)} · cost ${(last.cost || 0).toFixed(2)}</span>`;
    } else {
      lastHtml = `<span class="rt-last rt-none">no rotations yet</span>`;
    }
    el.innerHTML =
      `<span class="rt-title" title="capital-rotation = finite-capital opportunity-cost redeploy (display-only telemetry)">Rotation</span>`
      + `<span class="rt-kv"><span class="lk">Rotations</span> <span class="lv">${rc}</span></span>`
      + `<span class="rt-kv"><span class="lk">Session Exits</span> <span class="lv">${sfe}</span></span>`
      + lastHtml;
  }
  function renderCells(d) {
    const top = (d.cell_top || []).map(c => ({ ...c, _side: 'top' }));
    const bot = (d.cell_bottom || []).map(c => ({ ...c, _side: 'bot' }));
    const rows = top.concat(bot);
    const body = $('cell-body'); if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<div class="empty">cold start · building<br><span class="b-flat">(need n≥20 samples)</span></div>';
      return;
    }
    body.innerHTML = rows.map(c => {
      const cls = c.score > 0 ? 'b-pos' : c.score < 0 ? 'b-neg' : 'b-flat';
      return `<div class="row" style="grid-template-columns:1fr 50px 44px" title="${esc(c.exchange)}/${esc(c.strategy)}/${esc(c.regime)} n=${(c.n_eff || 0).toFixed(0)} [${c._side}]">
        <span class="name">${esc(c.ticker)} <span class="sub">${esc(c.strategy)}</span></span>
        <span class="num ${cls}">${(c.score || 0).toFixed(3)}</span>
        <span class="num b-flat">${(c.mult || 1).toFixed(1)}×</span>
      </div>`;
    }).join('');
  }
  function renderAlerts(d) {
    const alerts = (d.alerts || []).slice(0, 12);
    const body = $('alert-body'); if (!body) return;
    setCnt('alert-body-cnt', alerts.length);
    body.innerHTML = alerts.length ? alerts.map(a => `
      <div class="row" style="grid-template-columns:54px 1fr" title="[${esc(a.level)}] ${esc(a.module)} ${hhmmss(a.ts)} — ${esc(a.msg || '')}">
        <span class="lvl-${esc(a.level)}">${esc(a.level)}</span>
        <span class="name b-flat" style="font-weight:400">${esc(a.msg || '')}</span>
      </div>`).join('') : '<div class="empty">no alerts</div>';
  }
  function renderRiskAdmit(d) {
    const ai = d.ai_shadow || {};
    const rows = ai.admission || [];
    const body = $('risk-admit'); if (!body) return;
    body.innerHTML = rows.length ? rows.map(r => {
      const sp = r.suppress_pct || 0;
      const barCls = sp >= 50 ? 'bar-neg' : sp >= 20 ? 'bar-warn' : '';
      return `<div class="row" style="grid-template-columns:1fr 1fr 56px" title="${esc(r.regime)} · would-suppress ${r.would_suppress_n}/${r.n} net of the real round-trip fee (SHADOW)">
        <span class="name">${esc(r.regime)}</span>
        <span class="hbar ${barCls}"><i style="width:${sp.toFixed(0)}%"></i></span>
        <span class="num b-flat">${r.would_suppress_n}/${r.n}</span>
      </div>`;
    }).join('') : '<div class="empty">no admission-shadow rows (1h)</div>';
  }

  // ── register EXIT / AI / EDGE / RISK into the shared dispatch ──────────────
  T.register('exit', renderExit);
  T.register('ai', renderAi);
  T.register('edge', (d) => { renderEquity(d); renderConfidence(d); renderEdge(d); });
  T.register('risk', (d) => { renderRotation(d); renderCells(d); renderAlerts(d); renderRiskAdmit(d); });
})();
