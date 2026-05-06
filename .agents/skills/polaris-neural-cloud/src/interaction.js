/* Polaris Interaction v4 — hover/click → tooltip + side panel + chain
 *
 * Adapts v3 interaction to v4 events:
 *   sphere fires 'hover'    {node|null, satellite|null, screen}
 *   sphere fires 'node-click' {idx, node}
 *   sphere fires 'sat-click'  {idx, sat}
 */

(function () {
  const canvas = document.getElementById('sphere');
  const tooltip = document.getElementById('tooltip');
  const detail = document.getElementById('detail');
  const dCluster = document.getElementById('d-cluster');
  const dTitle = document.getElementById('d-title');
  const dGrid = document.getElementById('d-grid');
  const dChain = document.getElementById('d-chain');
  const dClose = document.getElementById('d-close');
  const titleBlock = document.getElementById('title-block');
  const ticker = document.getElementById('ticker');

  const CLUSTER_COLORS = {
    market:    '#d7afff', providers: '#87d7ff',
    signals:   '#87afd7', strategy:  '#87d787',
    pipeline:  '#ffaf87', // gate shell (legacy mapping)
    exit:      '#ff875f', obs: '#d7d787', action: '#d78787',
  };
  const CLUSTER_LABELS = {
    market: 'MARKET', providers: 'PROVIDERS', signals: 'SIGNALS',
    strategy: 'STRATEGY', pipeline: 'GATE',
    exit: 'EXIT', obs: 'OBS', action: 'ACTION',
  };
  const TIER_LABELS = {
    high: 'AI · HIGH ORBIT', mid: 'AI · MID ORBIT', low: 'AI · LOW ORBIT',
    tool: 'DIRECT TOOL', exit: 'SATELLITE · EXIT',
    obs: 'SATELLITE · OBS', action: 'SATELLITE · ACTION',
  };

  // Hover tooltip
  canvas.addEventListener('hover', (e) => {
    const { node, satellite, screen } = e.detail;
    if (node && node.entity) {
      const ent = node.entity;
      const color = CLUSTER_COLORS[ent.cluster] || '#fff';
      tooltip.innerHTML = `<span class="cl" style="color:${color}">${CLUSTER_LABELS[ent.cluster] || ent.cluster}</span>${ent.label}`;
      tooltip.style.left = screen.x + 'px';
      tooltip.style.top = screen.y + 'px';
      tooltip.classList.add('visible');
    } else if (satellite) {
      const color = `rgb(${satellite.color.join(',')})`;
      tooltip.innerHTML = `<span class="cl" style="color:${color}">${TIER_LABELS[satellite.tier] || satellite.tier.toUpperCase()}</span>${satellite.name}`;
      tooltip.style.left = screen.x + 'px';
      tooltip.style.top = screen.y + 'px';
      tooltip.classList.add('visible');
    } else {
      tooltip.classList.remove('visible');
    }
  });
  canvas.addEventListener('mouseleave', () => tooltip.classList.remove('visible'));

  // Click → detail
  canvas.addEventListener('node-click', (e) => {
    const { idx, node } = e.detail;
    if (!node || !node.entity) return;
    openDetail(idx, node);
    if (window.PolarisCloud?.highlightChain) window.PolarisCloud.highlightChain(idx);
  });
  canvas.addEventListener('sat-click', (e) => {
    openSatDetail(e.detail.sat);
  });

  function openDetail(idx, node) {
    const ent = node.entity;
    const color = CLUSTER_COLORS[ent.cluster] || '#fff';
    dCluster.textContent = CLUSTER_LABELS[ent.cluster] || ent.cluster;
    dCluster.style.color = color;
    dCluster.style.borderColor = color;
    dTitle.textContent = ent.label;
    dGrid.innerHTML = renderEntityFields(ent);
    dChain.innerHTML = renderChain(idx);
    detail.classList.add('visible');
    titleBlock.classList.add('hidden');
  }
  function openSatDetail(sat) {
    const color = `rgb(${sat.color.join(',')})`;
    dCluster.textContent = TIER_LABELS[sat.tier] || sat.tier.toUpperCase();
    dCluster.style.color = color;
    dCluster.style.borderColor = color;
    dTitle.textContent = sat.name;
    const rows = [];
    rows.push(`<span class="k">id</span><span class="v">${sat.id}</span>`);
    rows.push(`<span class="k">tier</span><span class="v">${sat.tier}</span>`);
    rows.push(`<span class="k">orbit r</span><span class="v">${sat.orbitR.toFixed(2)}</span>`);
    rows.push(`<span class="k">speed</span><span class="v">${sat.speed.toFixed(2)}</span>`);
    rows.push(`<span class="k">kind</span><span class="v">${sat.kind}</span>`);

    // If satellite has bound entities (exit/obs/action), list them
    const PD = window.PolarisData;
    let entRows = '';
    if (PD && (sat.tier === 'exit' || sat.tier === 'obs' || sat.tier === 'action')) {
      const ck = sat.tier === 'exit' ? 'exit' : sat.tier === 'obs' ? 'obs' : 'action';
      const ents = Object.values(PD.ENTITY_INDEX.byClusterId[ck] || {});
      if (ents.length) {
        entRows = `<div class="chain-head" style="margin-top:10px">CARRIED ENTITIES</div>` +
          ents.slice(0, 8).map(en => {
            const c = CLUSTER_COLORS[en.cluster] || '#fff';
            const wcls = en.warn ? 'neg' : '';
            return `<div class="chain-step"><span class="swatch" style="background:${c};color:${c}"></span><span class="lbl ${wcls}">${en.label}</span></div>`;
          }).join('');
      }
    }
    dGrid.innerHTML = rows.join('');
    dChain.innerHTML = entRows || '<span style="color:var(--p-dim)">no payload</span>';
    detail.classList.add('visible');
    titleBlock.classList.add('hidden');
    if (window.PolarisCloud?.clearChain) window.PolarisCloud.clearChain();
  }

  function closeDetail() {
    detail.classList.remove('visible');
    titleBlock.classList.remove('hidden');
    if (window.PolarisCloud?.clearChain) window.PolarisCloud.clearChain();
  }
  dClose.addEventListener('click', closeDetail);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

  function renderEntityFields(ent) {
    const d = ent.data || {};
    const rows = [];
    const push = (k, v, cls = '') => rows.push(`<span class="k">${k}</span><span class="v ${cls}">${v}</span>`);
    const pct = (v) => `${v >= 0 ? '+' : ''}${(v).toFixed(2)}%`;
    const cls = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';

    switch (ent.kind) {
      case 'regime':
        push('regime', d.current); push('score', d.score?.toFixed(2));
        push('F&G', d.fg_index); push('VIX', d.vix);
        push('BTC.D', d.btc_dom + '%');
        break;
      case 'macro': push('value', d.value); break;
      case 'ai':
        push('role', d.role); push('weight', d.weight?.toFixed(2));
        push('calls 24h', (d.calls_24h || 0).toLocaleString());
        push('lag', d.lag_ms + 'ms', d.lag_ms > 800 ? 'neg' : 'pos');
        push('ok rate', ((d.ok_rate || 0) * 100).toFixed(1) + '%', d.ok_rate < 0.97 ? 'neg' : 'pos');
        break;
      case 'data':
        push('kind', d.kind);
        push('lag', d.lag_ms + 'ms', d.lag_ms > 600 ? 'neg' : 'pos');
        push('ok rate', ((d.ok_rate || 0) * 100).toFixed(1) + '%', d.ok_rate < 0.99 ? 'neg' : 'pos');
        push('msgs 24h', (d.msgs_24h || 0).toLocaleString());
        break;
      case 'position':
        push('exchange', d.exchange); push('dir', d.dir);
        push('size', '$' + (d.size_usd || 0).toLocaleString());
        push('PnL', pct(d.pnl_pct), cls(d.pnl_pct));
        push('age', d.age_min + 'm');
        break;
      case 'scan': push('exchange', d.exchange); push('score', d.score?.toFixed(2)); break;
      case 'strategy':
        push('elo', d.elo); push('status', d.status); push('kind', d.kind);
        push('trades 24h', d.trades_24h);
        push('win rate', ((d.win || 0) * 100).toFixed(0) + '%');
        push('PnL', pct(d.pnl_pct), cls(d.pnl_pct));
        break;
    }
    return rows.join('');
  }

  function renderChain(seedNodeIdx) {
    const PC = window.PolarisCloud;
    const PD = window.PolarisData;
    if (!PC || !PD) return '';
    // v4 ordered shells from outside → inside
    const order = ['market', 'providers', 'signals', 'strategy', 'pipeline'];
    const seedNode = PC._getNode(seedNodeIdx);
    const seedEnt = seedNode.entity;
    if (!seedEnt) return '';
    const result = [];
    order.forEach(cid => {
      const ents = Object.values(PD.ENTITY_INDEX.byClusterId[cid] || {});
      if (!ents.length) return;
      let best = null, bestD = Infinity;
      ents.forEach(e => {
        const ni = PC._entityNodeIdx[`${cid}:${e.id}`];
        if (ni === undefined) return;
        const n = PC._getNode(ni);
        const dx = n.x - seedNode.x, dy = n.y - seedNode.y, dz = n.z - seedNode.z;
        const d = dx*dx + dy*dy + dz*dz;
        if (d < bestD) { bestD = d; best = e; }
      });
      if (best) result.push(best);
    });

    return result.map((e, i) => {
      const color = CLUSTER_COLORS[e.cluster] || '#fff';
      const isSeed = e.id === seedEnt.id && e.cluster === seedEnt.cluster;
      const arrow = i < result.length - 1 ? '<div class="arrow">↓</div>' : '';
      return `
        <div class="chain-step" ${isSeed ? 'style="font-weight:700;color:#fff5d2"' : ''}>
          <span class="swatch" style="background:${color};color:${color}"></span>
          <span class="lbl">${e.label}</span>
        </div>${arrow}
      `;
    }).join('');
  }

  window._pushTicker = (line) => { if (ticker) ticker.textContent = line; };

  // Legend click → pulse that shell/satellite
  document.addEventListener('click', (e) => {
    const row = e.target.closest('#cluster-legend-list .row');
    if (!row) return;
    const cid = row.dataset.cluster;
    if (!cid || !window.PolarisCloud) return;
    window.PolarisCloud.pulseCluster(cid, 8);
  });
})();
