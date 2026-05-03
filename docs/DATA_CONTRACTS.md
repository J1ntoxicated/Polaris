# Data Contracts — Field Units & Sources

> Canonical reference for field names, units, and sources across all data pipelines.
> Updated when wire bugs involving unit mismatch are fixed.

## OKX Market Data (REST + WebSocket)

| Field | Source API field | Unit | Notes |
|-------|-----------------|------|-------|
| `vol24h` | `volCcy24h * price` | USD | Converted at ingest (public_tickers.py + ws_feed.py) |
| `vol24h_ccy` | `volCcy24h` | Base currency (BTC/ETH/...) | Preserved for vol_spike ratio calc |
| `vol_spike` | computed | ratio (unitless) | `vol_ccy / avg_vol_ccy` — ratio is unit-agnostic |
| `price` | `last` | USDT | |
| `chg_24h` | computed | % | `(price - open24h) / open24h * 100` |
| `range_pos` | computed | % 0-100 | 0=at_low, 100=at_high |

## Alpaca Market Data

| Field | Unit | Notes |
|-------|------|-------|
| `volume` | shares (not USD) | Multiply by price for USD notional |
| `price` | USD | |

## Capital.com (WS Lightstreamer)

| Field | Source | Unit | Notes |
|-------|--------|------|-------|
| `bid` / `ask` | `ws.get_price(epic)` | instrument quote currency | Returns `[bid, ask]` tuple |
| `spread_pct` | computed | % of mid | Fixed in capital_adapter.py — was always 0 when get_bid/get_ask missing |

## FRED Macro

| Field | Unit | Notes |
|-------|------|-------|
| `hy_spread` | bps (basis points) | High-yield credit spread |
| `move_index` | index points | MOVE = bond vol index |
| `vix` | index points | CBOE VIX |
| `dxy` | index (ICE scale ~98-110) | Promoted from yFinance (canonical). FRED DTWEXBGS stored as `dxy_broad` (different base) |

## CoinGecko /global

| Field | Unit | Source | Cadence |
|-------|------|--------|---------|
| `btc_dominance` | % | `market_cap_percentage.btc` | 30 min (collect_slow) |
| `eth_dominance` | % | `market_cap_percentage.eth` | 30 min |
| `total_mcap_b` | Billion USD | `total_market_cap.usd / 1e9` | 30 min |
| `total_crypto_vol` | USD | `total_volume.usd` | 30 min |

## State Writer (invasion_state.json)

| Field | Source | Notes |
|-------|--------|-------|
| `market_overview.btc_dominance` | `data_collector.latest.btc_dominance` | ~55% realistic |
| `market_overview.total_mcap_b` | `data_collector.latest.total_mcap_b` | Billions |
| `market_overview.spx_pct` | `spx_change_pct` or `spx_pct` | % daily change |
| `market_overview.crypto_vol` | `total_crypto_vol` | USD |

## Invariants

- `vol24h` in OKX cache is always USD. Gate/dashboard thresholds must be in USD.
- `vol_spike` is a ratio — unit-independent, no conversion needed.
- Capital spread_pct requires `ws.get_price()` returning `[bid, ask]`; fallback is 0 (neutral, not error).
