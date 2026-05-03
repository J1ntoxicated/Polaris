# Gate Matrix — Signal / Entry / Portfolio

SSOT: `invasion/gate/matrix.py`. This doc is a quick reference summary.

---

## Layer 1: Signal Engine (signal/engine.py) — 10 gates

| # | Gate | Condition | Reject |
|---|------|-----------|--------|
| 1 | Provider eval | Composite score from all providers | -- |
| 2 | Score threshold | `abs(score) < min_score` (group > regime > config > 40) | `score_below_N` |
| 3 | Direction | `direction == "neutral"` | `neutral_direction` |
| 4 | Factor count | `factor_count < min_factors` (default 3) | `factors_N<M` |
| 5 | Agreement | `fc >= 3 and agreement < min_agreement` (0.5) | `agreement_N<M` |
| 6 | Crypto gates | Long/short strength floor + funding gate | `long/short_strength_floor` |
| 7 | Data blocks | Hour/ticker blocked from trade_stats | `hour/ticker_blocked_data` |
| 8 | Trend gate | Fighting strong trend -> damp score 40% | `trend_gate:mom2m=...` |
| 9 | Quality gate | Bayesian WR check + direction check | `quality_gate/bayesian_contra` |
| G5 | Neutral gate | Neutral regime + abs(score)<45 | `neutral_weak_signal` |
| G6 | F&G gate | F&G<20+short or F&G>80+long | `contrarian_mismatch` |
| G7 | Stale price gate | `price_age > 600s` before F&G override | `stale_price_gate` |

Config priority: group_strategy_params > regime_presets > config > hardcoded

---

## Layer 2: Entry Gate (trade/entry.py) — 7 active gates

| # | Gate | Condition | Reject |
|---|------|-----------|--------|
| 1 | Blacklist | ticker in blacklist or conditional_blacklist | `blacklisted` |
| 2 | Cooldown | `now < unlock_time` | `cooldown_Ns` |
| 3 | Repeat entry | Same ticker >= 2 entries in 1h | `repeat_entry_blocked` |
| 4 | Market hours | Non-crypto: is_market_open() check | `market_closed` |
| 5 | Stale price | `price_timestamp age > 30s` | `stale_price` |
| 8 | Min volatility | `atr_pct < min_atr * group_mult` | `low_volatility` |
| 10 | Tech data | Candle fetch failed | `no_tech_data` |
| 11 | Price deviation | Price outside 5% of 24h range | `price_deviation` |

ATR group multipliers: crypto 1.0, commodity 0.8, stock/index 0.7, forex 0.5

---

## Layer 3: Portfolio Filter (trade/portfolio.py) — 7 filters

| # | Filter | Condition | Reject |
|---|--------|-----------|--------|
| 1 | Max concurrent | `positions >= max_concurrent` | `[]` |
| 2 | Exposure cap | `total_usd > equity * 3.0` | `BLOCK ALL` |
| 3 | Duplicate ticker | Already in portfolio | `duplicate_ticker` |
| 4 | Duplicate underlying | Same base across exchanges | `duplicate_base` |
| 5 | Batch de-dup | Same ticker in batch | `batch_duplicate` |
| 6 | Group correlation | `group_count >= max_correlated` | `max_correlated:group` |
| 7 | Direction bias | `abs(exposure) > max_exposure_ratio` | `exposure:N>M` |

---

## Pre-Signal Filters (pipeline.py)

| Filter | Condition |
|--------|-----------|
| Zero price | `price <= 0` |
| Open position | Already has open position |
| Recent reject | Rejected in last 5min |
| Market closed | Capital.com market blocked |
| Tier filter | Tier not in regime allowed_tiers |
| G5 Neutral | Neutral + abs(strength) < 45 |
| No tech data | In _no_tech_tickers set |

---

## Post-Entry: AI Layer

| Stage | Function |
|-------|----------|
| S1 | Quick AI scan -- skip/approve/reject |
| S3 | Deep entry judge -- conf 0-10, reject if <= 2 |

---

## Sizing Multipliers

| Factor | Effect |
|--------|--------|
| Regime | crisis=1.8, risk_off=1.5, risk_on=1.3, transition=1.0, neutral=0.5 |
| Tier | major=1.18, large=1.0, mid=1.85, micro=0.3, meme=0.5 |
| Score | score / score_size_divisor |
| Streak | Losing streak shrinks size (down to 0.0) |
| WR degrade | 0.5x if WR below threshold |
| Max cap | max_position_pct = 5% of equity |
