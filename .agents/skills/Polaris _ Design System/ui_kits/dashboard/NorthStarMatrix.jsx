/* global React, C, Hline */

const ROWS = [
  { m: 'PF',      d: [' 1.42', ' 1.31', ' 1.27', ' 0.94'], pfL: '1.55', pfS: '1.18', tgt: '1.30',  delta: '+0.12', peer: '1.05', trend: '↑', best: 'tour_bayes' },
  { m: 'WR',      d: [' 56% ', ' 54% ', ' 51% ', ' 47% '], pfL: '—',    pfS: '—',    tgt: '52%',   delta: '+2.0',  peer: '48%',  trend: '→', best: 'forex_spec' },
  { m: 'AvgWin',  d: [' 0.91', ' 0.83', ' 0.79', ' 0.62'], pfL: '—',    pfS: '—',    tgt: '0.80',  delta: '+0.03', peer: '—',    trend: '↑', best: '—' },
  { m: 'AvgLoss', d: ['-0.42', '-0.51', '-0.55', '-0.71'], pfL: '—',    pfS: '—',    tgt: '-0.50', delta: '-0.05', peer: '—',    trend: '→', best: '—' },
  { m: 'Asym',    d: [' 2.17', ' 1.63', ' 1.44', ' 0.87'], pfL: '—',    pfS: '—',    tgt: '1.60',  delta: '+0.03', peer: '—',    trend: '↑', best: '—' },
  { m: 'DD',      d: ['-1.2%', '-2.8%', '-4.1%', '-3.3%'], pfL: '—',    pfS: '—',    tgt: '-3.0%', delta: '+0.2',  peer: '—',    trend: '→', best: '—' },
  { m: 'Sharpe',  d: [' 1.34', ' 1.12', ' 0.98', ' 0.41'], pfL: '—',    pfS: '—',    tgt: '1.00',  delta: '+0.12', peer: '0.81', trend: '↑', best: 'tour_bayes' },
];

const colorFor = (s) => {
  const v = parseFloat(s);
  if (Number.isNaN(v)) return C.dim;
  if (s.includes('%')) {
    if (s.startsWith('-')) return v <= -3 ? C.red : C.ylw;
    return v >= 50 ? C.grn : v >= 45 ? C.ylw : C.red;
  }
  return v >= 1 ? C.grn : v >= 0 ? C.ylw : C.red;
};

function NorthStarMatrix() {
  return (
    <div style={{ fontSize: 11, lineHeight: 1.5, padding: '0 10px' }}>
      <Hline label="NORTH STAR MATRIX" width={140} />
      <div style={{ color: C.dim, display: 'grid', gridTemplateColumns: '11ch 6ch 6ch 6ch 6ch 6ch 6ch 4ch 7ch 7ch 6ch 12ch', gap: '0 6px' }}>
        <span>Metric</span><span style={{ textAlign: 'right' }}>1h</span><span style={{ textAlign: 'right' }}>24h</span><span style={{ textAlign: 'right' }}>All</span><span style={{ textAlign: 'right' }}>Yest</span><span style={{ textAlign: 'right' }}>PF_L</span><span style={{ textAlign: 'right' }}>PF_S</span><span style={{ textAlign: 'center' }}>Trd</span><span style={{ textAlign: 'right' }}>Target</span><span style={{ textAlign: 'right' }}>Δ</span><span style={{ textAlign: 'right' }}>Peer</span><span>Best</span>
      </div>
      {ROWS.map((r, i) => (
        <div key={r.m} style={{ display: 'grid', gridTemplateColumns: '11ch 6ch 6ch 6ch 6ch 6ch 6ch 4ch 7ch 7ch 6ch 12ch', gap: '0 6px' }}>
          <span style={{ color: C.wht, fontWeight: 700 }}>{r.m}</span>
          {r.d.map((v, j) => <span key={j} style={{ color: colorFor(v), fontWeight: 700, textAlign: 'right' }}>{v}</span>)}
          <span style={{ color: r.pfL === '—' ? C.dim : C.grn, fontWeight: 700, textAlign: 'right' }}>{r.pfL}</span>
          <span style={{ color: r.pfS === '—' ? C.dim : C.grn, fontWeight: 700, textAlign: 'right' }}>{r.pfS}</span>
          <span style={{ color: r.trend === '↑' ? C.grn : r.trend === '↓' ? C.red : C.dim, textAlign: 'center', fontWeight: 700 }}>{r.trend}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{r.tgt}</span>
          <span style={{ color: r.delta.startsWith('-') ? C.red : C.grn, textAlign: 'right' }}>{r.delta}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{r.peer}</span>
          <span style={{ color: C.cyn }}>{r.best}</span>
        </div>
      ))}
    </div>
  );
}

window.NorthStarMatrix = NorthStarMatrix;
