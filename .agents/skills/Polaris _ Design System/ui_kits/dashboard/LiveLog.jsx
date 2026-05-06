/* global React, C, Hline, useTick */
const { useMemo } = React;

const SEED_LOG = [
  { lvl: 'INFO',  src: 'pipeline',   msg: 'scan_cycle tick=12847 candidates=82 pass=14' },
  { lvl: 'TRADE', src: 'router.okx', msg: 'OPEN BTC-USDT-SWAP L $8,200 @ 62341.0' },
  { lvl: 'AI',    src: 'judge',      msg: 'gemini conviction=0.74 → ENTER  claude(critic)=PASS' },
  { lvl: 'INFO',  src: 'exit_engine',msg: 'TRAIL ETH-USDT-SWAP +0.59% bep_zone=Y' },
  { lvl: 'WARN',  src: 'gate.H9',    msg: 'soft fail dup_cooldown ticker=DOGE-USDT-SWAP' },
  { lvl: 'TRADE', src: 'router.cap', msg: 'CLOSE EURUSD S +0.06% +$3.1 reason=trail' },
  { lvl: 'EVO',   src: 'evolver',    msg: 'mutation type=Bayesian parent=tournament_ai child=tournament_bayes_v3' },
  { lvl: 'INFO',  src: 'cell_lrnr',  msg: 'incremental update +12 cells in 1h' },
  { lvl: 'WARN',  src: 'data_qa',    msg: 'okx ws lag=420ms (warn>300)' },
  { lvl: 'TRADE', src: 'router.okx', msg: 'CLOSE DOGE-USDT-SWAP S -2.36% -$49.6 reason=hard_stop' },
  { lvl: 'CRIT',  src: 'gate.kill',  msg: 'KILL switch armed reason=DD_24h>-3.5% (override Jin)' },
  { lvl: 'INFO',  src: 'pipeline',   msg: 'scan_cycle tick=12848 candidates=79 pass=11' },
];

const LVL_COLOR = { INFO: C.gry, TRADE: C.cyn, AI: C.mag, WARN: C.ylw, EVO: C.blu, CRIT: C.red };

function LiveLog() {
  const t = useTick(1500);
  const visible = useMemo(() => {
    const arr = [];
    for (let i = 0; i < SEED_LOG.length; i++) {
      arr.push(SEED_LOG[(i + t) % SEED_LOG.length]);
    }
    return arr;
  }, [t]);

  return (
    <div style={{ fontSize: 11, lineHeight: 1.5, padding: '0 10px' }}>
      <Hline label="LIVE LOG" width={120} />
      {visible.map((e, i) => {
        const isCrit = e.lvl === 'CRIT';
        return (
          <div key={i} style={{ background: isCrit ? C.bgR : 'transparent', display: 'flex', gap: 6, opacity: 1 - i * 0.02 }}>
            <span style={{ color: C.dim, width: '8ch' }}>14:32:{String(40 - i).padStart(2, '0')}</span>
            <span style={{ color: LVL_COLOR[e.lvl], fontWeight: 700, width: '6ch' }}>{e.lvl}</span>
            <span style={{ color: C.cyn, width: '12ch' }}>{e.src}</span>
            <span style={{ color: isCrit ? '#fff' : C.wht, flex: 1 }}>{e.msg}</span>
          </div>
        );
      })}
    </div>
  );
}

window.LiveLog = LiveLog;
