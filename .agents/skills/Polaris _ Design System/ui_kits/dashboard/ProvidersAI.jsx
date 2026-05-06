/* global React, C, Hline, Bar */

const PROVIDERS = [
  { name: 'rsi_div',         w: 0.82, n: 1247, hit: 0.61 },
  { name: 'orderflow_imb',   w: 0.74, n: 891,  hit: 0.58 },
  { name: 'funding_rate',    w: 0.71, n: 524,  hit: 0.55 },
  { name: 'fng_contrarian',  w: 0.68, n: 312,  hit: 0.52 },
  { name: 'whale_alert',     w: 0.55, n: 84,   hit: 0.49 },
  { name: 'ai_judge_gemini', w: 0.91, n: 217,  hit: 0.64 },
  { name: 'ai_critic_claude',w: 0.88, n: 41,   hit: 0.71 },
];

function ProvidersAI() {
  return (
    <div style={{ fontSize: 11, lineHeight: 1.5, padding: '0 10px' }}>
      <Hline label="PROVIDERS / AI" width={120} />
      <div style={{ color: C.dim, display: 'grid', gridTemplateColumns: '22ch 6ch 7ch 6ch 18ch', gap: '0 6px' }}>
        <span>provider</span><span style={{ textAlign: 'right' }}>weight</span><span style={{ textAlign: 'right' }}>N(24h)</span><span style={{ textAlign: 'right' }}>hit%</span><span>weight bar</span>
      </div>
      {PROVIDERS.map(p => (
        <div key={p.name} style={{ display: 'grid', gridTemplateColumns: '22ch 6ch 7ch 6ch 18ch', gap: '0 6px', alignItems: 'center' }}>
          <span style={{ color: p.name.startsWith('ai_') ? C.mag : C.cyn }}>{p.name}</span>
          <span style={{ color: C.wht, fontWeight: 700, textAlign: 'right' }}>{p.w.toFixed(2)}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{p.n}</span>
          <span style={{ color: p.hit >= 0.55 ? C.grn : p.hit >= 0.5 ? C.ylw : C.red, fontWeight: 700, textAlign: 'right' }}>{(p.hit * 100).toFixed(0)}%</span>
          <Bar pct={p.w * 100} width={16} />
        </div>
      ))}
      <div style={{ color: C.dim, paddingTop: 4, fontSize: 10 }}>
        <span style={{ color: C.mag, fontWeight: 700 }}>AI escalation:</span>{' '}
        <span style={{ color: C.gry }}>augment → advise → judge → critic → governor → kill (6-stage)</span>
      </div>
    </div>
  );
}

window.ProvidersAI = ProvidersAI;
