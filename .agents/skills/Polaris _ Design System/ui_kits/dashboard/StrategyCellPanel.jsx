/* global React, C, Hline, Spark */

const STRATS = [
  { id: 'tournament_bayes',     elo: 1612, n: 184, wr: 0.58, pf: 1.55, sharpe: 1.34, last: '2m',  fam: 'crypto' },
  { id: 'forex_specialist',     elo: 1547, n: 97,  wr: 0.61, pf: 1.41, sharpe: 1.18, last: '4m',  fam: 'forex'  },
  { id: 'tournament_ai',        elo: 1502, n: 61,  wr: 0.52, pf: 1.22, sharpe: 0.89, last: '6m',  fam: 'crypto' },
  { id: 'etf_specialist[P]',    elo: 1488, n: 52,  wr: 0.54, pf: 1.18, sharpe: 0.78, last: '8m',  fam: 'etf'    },
  { id: 'contrarian_commodity', elo: 1471, n: 44,  wr: 0.50, pf: 1.09, sharpe: 0.61, last: '12m', fam: 'comd'   },
  { id: 'stock_specialist[P]',  elo: 1452, n: 38,  wr: 0.47, pf: 1.02, sharpe: 0.44, last: '18m', fam: 'stock'  },
  { id: 'contrarian_meme',      elo: 1391, n: 29,  wr: 0.41, pf: 0.81, sharpe: -0.21, last: '45m', fam: 'crypto' },
];

const CELLS_TOP = [
  { ex: 'okx', grp: 'crypto', sid: 'tournament_bayes', score: +0.41 },
  { ex: 'cap', grp: 'forex',  sid: 'forex_specialist', score: +0.33 },
];
const CELLS_WST = [
  { ex: 'okx', grp: 'crypto', sid: 'contrarian_meme',  score: -0.28 },
  { ex: 'alp', grp: 'stock',  sid: 'stock_specialist', score: -0.11 },
];

function StrategyCellPanel() {
  return (
    <div style={{ fontSize: 11, lineHeight: 1.5, padding: '0 10px' }}>
      <Hline label="STRATEGY × CELL" width={120} />
      <div style={{ color: C.cyn, fontWeight: 700, padding: '2px 0' }}> -- STRAT PERF --</div>
      <div style={{ color: C.dim, display: 'grid', gridTemplateColumns: '22ch 6ch 5ch 5ch 5ch 6ch 6ch 6ch', gap: '0 6px' }}>
        <span>strategy</span><span style={{ textAlign: 'right' }}>Elo</span><span style={{ textAlign: 'right' }}>N</span><span style={{ textAlign: 'right' }}>WR</span><span style={{ textAlign: 'right' }}>PF</span><span style={{ textAlign: 'right' }}>Sharpe</span><span style={{ textAlign: 'right' }}>last</span><span>fam</span>
      </div>
      {STRATS.map(s => (
        <div key={s.id} style={{ display: 'grid', gridTemplateColumns: '22ch 6ch 5ch 5ch 5ch 6ch 6ch 6ch', gap: '0 6px' }}>
          <span style={{ color: C.cyn }}>{s.id}</span>
          <span style={{ color: C.wht, fontWeight: 700, textAlign: 'right' }}>{s.elo}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{s.n}</span>
          <span style={{ color: s.wr >= 0.5 ? C.grn : C.red, fontWeight: 700, textAlign: 'right' }}>{(s.wr * 100).toFixed(0)}%</span>
          <span style={{ color: s.pf >= 1 ? C.grn : C.red, fontWeight: 700, textAlign: 'right' }}>{s.pf.toFixed(2)}</span>
          <span style={{ color: s.sharpe >= 0.8 ? C.grn : s.sharpe >= 0 ? C.ylw : C.red, fontWeight: 700, textAlign: 'right' }}>{s.sharpe.toFixed(2)}</span>
          <span style={{ color: C.dim, textAlign: 'right' }}>{s.last}</span>
          <span style={{ color: C.gry }}>{s.fam}</span>
        </div>
      ))}

      <div style={{ color: C.cyn, fontWeight: 700, padding: '4px 0 2px' }}> -- CELL MATRIX --</div>
      <div>
        <span style={{ color: C.gry }}>6d</span> <span style={{ color: C.wht, fontWeight: 700 }}>184</span>
        {'  '}<span style={{ color: C.gry }}>8d</span> <span style={{ color: C.wht, fontWeight: 700 }}>1,247</span>
        {'  '}<span style={{ color: C.gry }}>incr1h</span> <span style={{ color: C.cyn, fontWeight: 700 }}>+12</span>
      </div>
      {CELLS_TOP.map(c => (
        <div key={'t'+c.sid}>
          <span style={{ color: C.gry }}>top </span>
          <span style={{ color: C.cyn }}>{c.ex}/{c.grp}/{c.sid}</span>
          {' '}<span style={{ color: c.score >= 0 ? C.grn : C.red, fontWeight: 700 }}>{c.score >= 0 ? '+' : ''}{c.score.toFixed(2)}</span>
        </div>
      ))}
      {CELLS_WST.map(c => (
        <div key={'w'+c.sid}>
          <span style={{ color: C.gry }}>wst </span>
          <span style={{ color: C.ylw }}>{c.ex}/{c.grp}/{c.sid}</span>
          {' '}<span style={{ color: c.score < 0 ? C.red : C.ylw, fontWeight: 700 }}>{c.score >= 0 ? '+' : ''}{c.score.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

window.StrategyCellPanel = StrategyCellPanel;
