/* global React, C, Hline, ThreatBar, useTick */
const { useMemo } = React;

const POSITIONS_INIT = [
  { ticker: 'BTC-USDT-SWAP', exch: 'OKX', dir: 'L', size: 8200, entry: 62341, mark: 62910, pnlPct: 0.91, ageS: 720,  strat: 'tournament_bayes' },
  { ticker: 'EURUSD',        exch: 'CAP', dir: 'S', size: 5000, entry: 1.0822, mark: 1.0815, pnlPct: 0.06, ageS: 180, strat: 'forex_specialist' },
  { ticker: 'SPY',           exch: 'ALP', dir: 'L', size: 3250, entry: 512.40, mark: 510.80, pnlPct: -0.31, ageS: 240, strat: 'etf_specialist[P]' },
  { ticker: 'ETH-USDT-SWAP', exch: 'OKX', dir: 'L', size: 4100, entry: 2480.5, mark: 2495.2, pnlPct: 0.59, ageS: 1100, strat: 'tournament_ai' },
  { ticker: 'XAUUSD',        exch: 'CAP', dir: 'S', size: 2900, entry: 2387.4, mark: 2381.1, pnlPct: 0.26, ageS: 900,  strat: 'contrarian_commodity' },
  { ticker: 'DOGE-USDT-SWAP',exch: 'OKX', dir: 'S', size: 2100, entry: 0.1483, mark: 0.1518, pnlPct: -2.36, ageS: 2700, strat: 'contrarian_meme' },
  { ticker: 'NVDA',          exch: 'ALP', dir: 'L', size: 1800, entry: 901.2, mark: 905.8, pnlPct: 0.51, ageS: 360, strat: 'stock_specialist[P]' },
];

const exchColor = (e) => ({ OKX: C.cyn, CAP: C.blu, ALP: C.ylw, BIN: C.grn }[e] || C.dim);

const cols = [
  { k: 'ticker', label: 'TICKER',     w: 18, align: 'L' },
  { k: 'exch',   label: 'EXCH',       w: 5,  align: 'L' },
  { k: 'dir',    label: 'DIR',        w: 4,  align: 'L' },
  { k: 'size',   label: 'SIZE',       w: 9,  align: 'R' },
  { k: 'entry',  label: 'ENTRY',      w: 10, align: 'R' },
  { k: 'mark',   label: 'MARK',       w: 10, align: 'R' },
  { k: 'pnlPct', label: 'PNL%',       w: 9,  align: 'R' },
  { k: 'pnl$',   label: '$PNL',       w: 10, align: 'R' },
  { k: 'age',    label: 'AGE',        w: 6,  align: 'R' },
  { k: 'threat', label: 'THREAT',     w: 8,  align: 'L' },
  { k: 'strat',  label: 'STRAT',      w: 22, align: 'L' },
];

const fmtAge = (s) => s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s/60)}m` : `${(s/3600).toFixed(1)}h`;

function Row({ p }) {
  const pnlUsd = (p.size * p.pnlPct) / 100;
  const isAlert = p.pnlPct < -2;
  const cellPad = (s, w, a) => {
    const txt = String(s);
    if (txt.length >= w) return txt.slice(0, w);
    const pad = ' '.repeat(w - txt.length);
    return a === 'R' ? pad + txt : txt + pad;
  };

  return (
    <div style={{ background: isAlert ? C.bgR : 'transparent', display: 'flex', gap: 8, padding: '0 10px' }}>
      <span style={{ color: C.wht, fontWeight: 700, width: '15ch' }}>{p.ticker}</span>
      <span style={{ color: exchColor(p.exch), width: '4ch' }}>{p.exch}</span>
      <span style={{ color: p.dir === 'L' ? C.grn : C.red, fontWeight: 700, width: '3ch' }}>{p.dir}</span>
      <span style={{ color: C.wht, fontWeight: 700, width: '8ch', textAlign: 'right' }}>${p.size.toLocaleString()}</span>
      <span style={{ color: C.wht, fontWeight: 700, width: '9ch', textAlign: 'right' }}>{p.entry.toLocaleString()}</span>
      <span style={{ color: C.wht, fontWeight: 700, width: '9ch', textAlign: 'right' }}>{p.mark.toLocaleString()}</span>
      <span style={{ color: p.pnlPct >= 0 ? C.grn : C.red, fontWeight: 700, width: '8ch', textAlign: 'right' }}>
        {p.pnlPct >= 0 ? '+' : ''}{p.pnlPct.toFixed(2)}%
      </span>
      <span style={{ color: pnlUsd >= 0 ? C.grn : C.red, fontWeight: 700, width: '9ch', textAlign: 'right' }}>
        {pnlUsd >= 0 ? '+' : ''}${Math.abs(pnlUsd).toFixed(1)}
      </span>
      <span style={{ color: p.ageS > 1800 ? C.ylw : C.cyn, width: '5ch', textAlign: 'right' }}>{fmtAge(p.ageS)}</span>
      <span style={{ width: '6ch' }}><ThreatBar pnl={p.pnlPct} ageS={p.ageS} /></span>
      <span style={{ color: C.cyn, flex: 1 }}>{p.strat}</span>
    </div>
  );
}

function LivePositions() {
  const t = useTick(1000);
  // Live wiggle: tiny mark drift + age increment so the panel feels alive
  const positions = useMemo(() => POSITIONS_INIT.map(p => {
    const drift = (Math.sin(t * 0.7 + p.ticker.length) * 0.0015);
    const newPct = p.pnlPct + drift * 100;
    return { ...p, pnlPct: newPct, ageS: p.ageS + t };
  }), [t]);

  const totalPnl = positions.reduce((a, p) => a + (p.size * p.pnlPct) / 100, 0);
  const totalSize = positions.reduce((a, p) => a + p.size, 0);

  return (
    <div style={{ fontSize: 11, lineHeight: 1.55 }}>
      <div style={{ padding: '0 10px' }}>
        <Hline label="LIVE POSITIONS" width={140} color={C.org} labelColor={C.wht} />
      </div>
      <div style={{ padding: '0 10px', color: C.dim, display: 'flex', gap: 8 }}>
        <span style={{ width: '15ch' }}>TICKER</span>
        <span style={{ width: '4ch' }}>EXCH</span>
        <span style={{ width: '3ch' }}>DIR</span>
        <span style={{ width: '8ch', textAlign: 'right' }}>SIZE</span>
        <span style={{ width: '9ch', textAlign: 'right' }}>ENTRY</span>
        <span style={{ width: '9ch', textAlign: 'right' }}>MARK</span>
        <span style={{ width: '8ch', textAlign: 'right' }}>PNL%</span>
        <span style={{ width: '9ch', textAlign: 'right' }}>$PNL</span>
        <span style={{ width: '5ch', textAlign: 'right' }}>AGE</span>
        <span style={{ width: '6ch' }}>THREAT</span>
        <span>STRAT</span>
      </div>
      {positions.map((p, i) => <Row key={p.ticker} p={p} />)}
      <div style={{ padding: '4px 10px 0', color: C.gry }}>
        <span>Total Exposure: </span><span style={{ color: C.wht, fontWeight: 700 }}>${totalSize.toLocaleString()}</span>
        {'  '}
        <span>Unrealized: </span>
        <span style={{ color: totalPnl >= 0 ? C.grn : C.red, fontWeight: 700 }}>
          {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(1)}
        </span>
        {'  '}
        <span style={{ color: C.dim }}>{positions.length} live · 0 ghost hidden</span>
      </div>
    </div>
  );
}

window.LivePositions = LivePositions;
