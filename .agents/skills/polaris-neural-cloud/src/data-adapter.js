/* Polaris Data Adapter v4 — bind real entities to concentric-shell topology
 *
 * v4 topology mapping (vs v3 cluster names):
 *   v3 'market'    → v4 shell 'market'
 *   v3 'providers' → v4 shell 'providers'
 *   v3 'pipeline'  → v4 shell 'gate'   (open positions / scans = gate-shell entities)
 *   v3 'strategy'  → v4 shell 'strategy'
 *   v3 'exit'      → v4 satellite 'sat_exit'   (entities NOT attached as nodes)
 *   v3 'obs'       → v4 satellite 'sat_obs'
 *   v3 'action'    → v4 satellite 'sat_action'
 *
 *   Open positions also fire to inner-core sub-spheres (OKX/CAP/ALP/BIN) via fireTrade.
 *   AI judges in providers ALSO get bound to AI MID satellites (visual cross-link).
 */

(function () {
  const PC = window.PolarisCloud;
  if (!PC) { console.warn('[PolarisData v4] PolarisCloud not found'); return; }

  const STATE = {
    regime: null, providers: null, pipeline: null,
    strategies: null, exit: null, obs: null, actions: null,
    events: [],
  };

  async function loadAll() {
    const files = ['regime', 'providers', 'pipeline', 'strategies', 'exit', 'obs', 'actions'];
    await Promise.all(files.map(async (f) => {
      try { const r = await fetch(`data/${f}.json`); STATE[f] = await r.json(); }
      catch (e) { console.warn(`[v4 data] ${f}`, e); }
    }));
    try {
      const r = await fetch('data/events.jsonl');
      const txt = await r.text();
      STATE.events = txt.trim().split('\n').filter(Boolean).map(l => JSON.parse(l));
    } catch (e) { console.warn('[v4 data] events.jsonl', e); }
    apply(); startEventTail(); populateLegend();
  }

  const ENTITY_INDEX = { byClusterId: {}, all: [] };

  function apply() {
    const ents = [];

    if (STATE.regime) {
      const r = STATE.regime;
      ents.push({ cluster: 'market', id: 'regime',  label: `regime: ${r.current}`,  importance: 1.0, kind: 'regime', data: r });
      ents.push({ cluster: 'market', id: 'fg',      label: `F&G: ${r.fg_index}`,    importance: 0.9, kind: 'macro',  data: { value: r.fg_index } });
      ents.push({ cluster: 'market', id: 'vix',     label: `VIX: ${r.vix}`,         importance: 0.8, kind: 'macro',  data: { value: r.vix } });
      ents.push({ cluster: 'market', id: 'btc_dom', label: `BTC.D: ${r.btc_dom}%`,  importance: 0.7, kind: 'macro',  data: { value: r.btc_dom } });
    }

    if (STATE.providers) {
      STATE.providers.ai_judges.forEach(p => ents.push({
        cluster: 'providers', id: p.id, label: `AI · ${p.id}`,
        importance: 0.9 + p.weight * 0.1, kind: 'ai', data: p,
        warn: p.lag_ms > 800 || p.ok_rate < 0.97,
      }));
      STATE.providers.data.forEach(p => ents.push({
        cluster: 'providers', id: p.id, label: `DATA · ${p.id}`,
        importance: p.kind === 'ws' ? 0.85 : 0.6, kind: 'data', data: p,
        warn: p.lag_ms > 600 || p.ok_rate < 0.99,
      }));
    }

    // Pipeline → gate shell (open positions + scan candidates)
    if (STATE.pipeline) {
      STATE.pipeline.open_positions.forEach(p => ents.push({
        cluster: 'pipeline', id: `pos:${p.ticker}`,
        label: `${p.ticker} ${p.dir} ${p.pnl_pct>=0?'+':''}${p.pnl_pct.toFixed(2)}%`,
        importance: 1.0, kind: 'position', data: p,
        pnlSign: p.pnl_pct >= 0 ? 1 : -1,
      }));
      STATE.pipeline.scan_candidates.forEach(p => ents.push({
        cluster: 'pipeline', id: `scan:${p.ticker}`,
        label: `scan · ${p.ticker} (${p.score.toFixed(2)})`,
        importance: 0.4 + p.score * 0.4, kind: 'scan', data: p,
      }));
    }

    if (STATE.strategies) {
      STATE.strategies.cells.forEach(c => ents.push({
        cluster: 'strategy', id: c.id, label: `${c.id} · elo ${c.elo}`,
        importance: 0.4 + Math.min(0.6, (c.elo - 1400) / 800),
        kind: 'strategy', data: c,
        pnlSign: c.pnl_pct >= 0 ? 1 : -1,
        dim: c.status === 'dormant' || c.status === 'probation',
      }));
    }

    // EXIT/OBS/ACTION are NOT attached as shell nodes — they're satellites.
    // We keep their entities in ENTITY_INDEX for the side-panel, but skip attachEntities.
    const SAT_ENTITIES = [];
    if (STATE.exit) STATE.exit.metrics.forEach(m => SAT_ENTITIES.push({
      cluster: 'exit', satId: 'sat_exit', id: m.id, label: `${m.label}: ${(m.value*100).toFixed(0)}%`,
      importance: 0.5 + m.value * 0.5, kind: 'exit', data: m,
    }));
    if (STATE.obs) STATE.obs.checks.forEach(c => SAT_ENTITIES.push({
      cluster: 'obs', satId: 'sat_obs', id: c.id, label: `${c.label}: ${c.value}${c.unit}`,
      importance: c.ok ? 0.7 : 1.0, kind: 'obs', data: c, warn: !c.ok,
    }));
    if (STATE.actions) STATE.actions.queue.forEach(a => SAT_ENTITIES.push({
      cluster: 'action', satId: 'sat_action', id: a.id, label: `${a.sev} · ${a.label}`,
      importance: a.sev === 'CRIT' ? 1.0 : a.sev === 'WARN' ? 0.8 : 0.6,
      kind: 'action', data: a, warn: a.sev === 'CRIT' || a.sev === 'WARN',
    }));

    ENTITY_INDEX.all = [...ents, ...SAT_ENTITIES];
    ENTITY_INDEX.byClusterId = {};
    ENTITY_INDEX.all.forEach(e => {
      ENTITY_INDEX.byClusterId[e.cluster] = ENTITY_INDEX.byClusterId[e.cluster] || {};
      ENTITY_INDEX.byClusterId[e.cluster][e.id] = e;
    });

    PC.attachEntities(ents);

    // Persistent edges (within shells only — satellite firings happen as beams)
    if (PC.attachRelations) {
      const rels = [];
      ents.filter(e => e.cluster === 'providers' && e.kind === 'ai').forEach(p => {
        rels.push({ a: { cluster: 'market', id: 'regime' }, b: { cluster: 'providers', id: p.id }, weight: 0.6 });
      });
      const topStrats = ents.filter(e => e.cluster === 'strategy')
        .sort((a, b) => b.data.elo - a.data.elo).slice(0, 6);
      ents.filter(e => e.cluster === 'providers' && e.kind === 'ai').forEach(p => {
        topStrats.forEach(s => rels.push({
          a: { cluster: 'providers', id: p.id }, b: { cluster: 'strategy', id: s.id },
          weight: 0.5 * p.data.weight,
        }));
      });
      STATE.pipeline?.open_positions.forEach((p, i) => {
        const stratPick = topStrats[i % topStrats.length];
        if (!stratPick) return;
        rels.push({
          a: { cluster: 'strategy', id: stratPick.id },
          b: { cluster: 'pipeline', id: `pos:${p.ticker}` },
          weight: 0.8,
        });
      });
      PC.attachRelations(rels);
    }

    // Auto-bind AI providers (data) to AI MID satellites for visual cross-link
    if (PC.bindAIModules && STATE.providers?.ai_judges) {
      const judges = STATE.providers.ai_judges;
      PC.bindAIModules({
        // No real CLI mapping yet — just label-swap MID satellites with judge names if available
        mid: judges.slice(0, 3).map(j => ({ id: 'ai_mid_' + j.id, name: 'AI · ' + j.id.toUpperCase() })),
      });
    }

    if (STATE.regime) PC.setRegime(STATE.regime.current);
    if (STATE.obs) {
      const okN = STATE.obs.checks.filter(c => c.ok).length;
      PC.setNSI(okN / STATE.obs.checks.length);
    }
  }

  function startEventTail() {
    let idx = 0;
    setInterval(() => {
      if (!STATE.events.length) return;
      handleEvent(STATE.events[idx % STATE.events.length]); idx++;
    }, 1400);
  }

  function handleEvent(ev) {
    switch (ev.kind) {
      case 'regime':
        PC.setRegime(ev.to); PC.pulseShell('market', 4); break;
      case 'signal':
        // signals shell pulse + radial down to strategy
        PC.pulseShell('signals', 2); PC.pulseEdge('signals', 'strategy'); break;
      case 'ai_judge':
      case 'ai_critic':
        // AI MID/HIGH satellite beams the strategy shell
        PC.pulseShell('strategy', 2); break;
      case 'gate_pass':
      case 'gate_fail':
        PC.pulseShell('gate', 3); break;
      case 'trade_open':
      case 'trade_close':
        PC.fireTrade({ ticker: ev.ticker, dir: ev.dir, exchange: ev.exchange || 'okx',
                        pnl: ev.pnl_pct ? ev.pnl_pct * 100 : 0 });
        break;
      case 'exit_trail':
      case 'exit_bep':
        PC.pulseCluster('exit', 2); break;
      case 'evo_mutation':
        PC.pulseShell('strategy', 4); break;
      case 'scan':
        PC.pulseShell('signals', 3); PC.pulseEdge('providers', 'signals'); break;
      case 'obs_warn':
        PC.pulseCluster('obs', 3); break;
      case 'obs_ok':
        PC.pulseCluster('obs', 1); break;
      case 'action_emit':
        PC.pulseCluster('action', 3); break;
    }
    if (window._pushTicker) window._pushTicker(eventLine(ev));
  }

  function eventLine(ev) {
    const ts = ev.ts || new Date().toTimeString().slice(0, 8);
    switch (ev.kind) {
      case 'regime':       return `${ts}  REGIME ${ev.from} → ${ev.to}`;
      case 'signal':       return `${ts}  SIG    ${ev.strategy} ${ev.ticker} conviction=${ev.conviction.toFixed(2)}`;
      case 'ai_judge':     return `${ts}  AI     ${ev.model} ${ev.verdict} for ${ev.strategy}`;
      case 'ai_critic':    return `${ts}  AI     ${ev.model} CRITIC ${ev.verdict} for ${ev.strategy}`;
      case 'gate_pass':    return `${ts}  GATE   PASS ${ev.strategy} ${ev.ticker}`;
      case 'gate_fail':    return `${ts}  GATE   FAIL ${ev.strategy} ${ev.ticker} reason=${ev.reason}`;
      case 'trade_open':   return `${ts}  TRADE  OPEN ${ev.ticker} ${ev.dir} $${ev.size_usd} (${ev.strategy})`;
      case 'trade_close':  return `${ts}  TRADE  CLOSE ${ev.ticker} ${ev.dir} ${ev.pnl_pct>=0?'+':''}${ev.pnl_pct}% reason=${ev.reason}`;
      case 'exit_trail':   return `${ts}  EXIT   TRAIL ${ev.ticker} +${ev.pnl_pct}% (${ev.strategy})`;
      case 'exit_bep':     return `${ts}  EXIT   BEP ${ev.ticker} +${ev.pnl_pct}%`;
      case 'evo_mutation': return `${ts}  EVO    ${ev.method} parent=${ev.parent} child=${ev.child}`;
      case 'scan':         return `${ts}  SCAN   ${ev.exchange} candidates=${ev.candidates}`;
      case 'obs_warn':     return `${ts}  WARN   ${ev.check}=${ev.value}`;
      case 'obs_ok':       return `${ts}  OK     ${ev.check}=${ev.value}`;
      case 'action_emit':  return `${ts}  ACT    ${ev.sev} · ${ev.label}`;
      default:             return `${ts}  ${ev.kind}`;
    }
  }

  function populateLegend() {
    const el = document.getElementById('cluster-legend-list');
    if (!el) return;
    const counts = {};
    ENTITY_INDEX.all.forEach(e => counts[e.cluster] = (counts[e.cluster] || 0) + 1);
    const shells = [
      { id: 'market',    name: 'MARKET / REGIME', color: '#d7afff', kind: 'shell' },
      { id: 'providers', name: 'PROVIDERS',       color: '#87d7ff', kind: 'shell' },
      { id: 'signals',   name: 'SIGNALS / SCAN',  color: '#87afd7', kind: 'shell' },
      { id: 'strategy',  name: 'STRATEGY × CELL', color: '#87d787', kind: 'shell' },
      { id: 'pipeline',  name: 'GATE / RISK',     color: '#ffaf87', kind: 'shell' },
    ];
    const sats = [
      { id: 'exit',   name: 'EXIT QUALITY',     color: '#ff875f' },
      { id: 'obs',    name: 'OBS / SAFETY',     color: '#d7d787' },
      { id: 'action', name: 'ACTION ITEMS',     color: '#d78787' },
    ];
    el.innerHTML = `
      <div class="sub-head">SHELLS · OUTSIDE → INSIDE</div>
      ${shells.map(o => `
        <div class="row" data-cluster="${o.id}">
          <span class="swatch" style="background:${o.color};color:${o.color}"></span>
          <span class="name">${o.name}</span>
          <span class="v">${counts[o.id] || 0}</span>
        </div>`).join('')}
      <div class="sub-head" style="margin-top:8px">SATELLITES</div>
      ${sats.map(o => `
        <div class="row" data-cluster="${o.id}">
          <span class="swatch sq" style="background:${o.color};color:${o.color}"></span>
          <span class="name">${o.name}</span>
          <span class="v">${counts[o.id] || 0}</span>
        </div>`).join('')}
    `;
  }

  window.PolarisData = { STATE, ENTITY_INDEX, handleEvent, reload: loadAll };
  document.addEventListener('DOMContentLoaded', loadAll);
  if (document.readyState === 'complete' || document.readyState === 'interactive') loadAll();
})();
