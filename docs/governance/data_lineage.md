# Data Lineage — INVASION Trading System

## Signal → Trade Flow
Collector (27) → DataCollector._cache → SignalProvider.compute()
→ CompositeScorer.score() → contrarian remap → GateMatrix evaluate
→ AI S1 augment → AI S2 advise → AI S3 judge → Entry execute
→ Position create → ExitEngine.check() → close → DB insert

## Collector Sources
| Collector | Source | Data | Refresh |
|-----------|--------|------|---------|
| coinglass | CoinGlass API | funding, OI, liquidations, heatmap | 5min |
| binance | Binance Futures | funding, OI, LS ratio, taker | 5min |
| alternative_me | Alternative.me | Crypto F&G | 30min |
| blockchain_info | Blockchain.info | BTC hash, mempool | 30min |
| defillama | DeFi Llama | TVL, stablecoin flows | 30min |
| cryptopanic | CryptoPanic | news sentiment + LLM | 15min |
| santiment | Santiment | MVRV, NVT, exchange flow | 1hr |
| google_trends | Google Trends | search interest | 1hr |
| fred_macro | FRED | DXY, VIX, HY | 30min |
| yfinance_macro | Yahoo | macro indices | 30min |
| cnn_feargreed | CNN | F&G index | 30min |

## Signal Providers (26)
Base (8): sentiment, funding, ls_ratio, taker, technical, fear_greed, liquidation, cross_pair
Price (3): momentum, volatility, price_action
Extended (3): cross_exchange, macro_regime, institutional
Breakout (2): dual_thrust, session_breakout
WQ Alpha (2): wq_alpha1, wq_alpha6
Flow (2): order_flow, vwap_reversion
ML (1): ml_signal
OnChain (5): onchain_valuation, basis_spread, liq_cascade, google_trends, llm_sentiment

## Parameter Flow
ParamRegistry._reg() → seed value
live_config.json → hot-reload override (5s)
computed.py → Tier 4 real-time (provider effectiveness, cooldowns)
regime_presets.json → regime-specific overrides on regime change
adaptive_tuner → Thompson Sampling optimization (hourly)
ParamRegistry.save() → dirty-tracking incremental merge back to live_config.json

## Position Lifecycle
Entry: pipeline.scan_cycle() → portfolio.add() → DB insert (status='open')
Hold: exit_monitor (1s) → price update → exit check → AI controller review
Exit: ExitEngine.check() → _close_position() → exchange API close → DB update (status='closed')
Market Closed: MarketClosedError → portfolio.remove() → long cooldown (no retry, no dead letter)
State: portfolio_state.json (crash recovery) + invasion.sqlite (permanent record)

## Gate Matrix (27 gates)
Hard (17): H1-H17 (never overridable)
Soft (10): S1-S4, S7-S12 (AI Governor adjustable)
Removed: S5 (F&G anchor), S6 (trend gate) — Contrarian violations
