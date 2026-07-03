"""Capital CFD venue adapter — bar fetch + chart-endpoint vol/depth proxy + lifecycle."""

from polaris.venues.capital.adapter import (
    CAPITAL_BASE_DEMO,
    CAPITAL_CONFIRMS_PATH,
    CAPITAL_POSITIONS_PATH,
    CAPITAL_PRICES_PATH,
    CAPITAL_WORKINGORDERS_PATH,
    CapitalAdapter,
    CapitalDealResponse,
    capital_price_row_to_bar,
    fetch_capital_bars,
)
from polaris.venues.capital.constraint_translator import (
    CAPITAL_MARKET_DETAIL_PATH,
    CapitalMarketConstraint,
    fetch_market_detail,
    round_size_to_step,
    size_usd_to_lots,
)
from polaris.venues.capital.market_proxy import (
    CAPITAL_DEPTH_PROXY_FLOOR_USD,
    CAPITAL_VOL_PROXY_FLOOR_USD,
    CapitalProxyConfig,
    compute_depth_proxy,
    compute_vol_24h_proxy,
    fetch_capital_chart_24h,
    passes_proxy_4_axis,
    populate_capital_proxies,
)
from polaris.venues.capital.opening_hours import (
    OpeningHoursWeek,
    seconds_to_next_close,
    seconds_to_next_open,
)
from polaris.venues.capital.session import (
    CAPITAL_BASE_LIVE,
    PING_INTERVAL_SEC,
    TOKEN_REFRESH_DEADLINE_SEC,
    CapitalSession,
    CapitalSessionError,
    CapitalTokens,
)

__all__ = [
    "CAPITAL_BASE_DEMO",
    "CAPITAL_BASE_LIVE",
    "CAPITAL_CONFIRMS_PATH",
    "CAPITAL_DEPTH_PROXY_FLOOR_USD",
    "CAPITAL_MARKET_DETAIL_PATH",
    "CAPITAL_POSITIONS_PATH",
    "CAPITAL_PRICES_PATH",
    "CAPITAL_VOL_PROXY_FLOOR_USD",
    "CAPITAL_WORKINGORDERS_PATH",
    "CapitalAdapter",
    "CapitalDealResponse",
    "CapitalMarketConstraint",
    "CapitalProxyConfig",
    "CapitalSession",
    "CapitalSessionError",
    "CapitalTokens",
    "OpeningHoursWeek",
    "PING_INTERVAL_SEC",
    "TOKEN_REFRESH_DEADLINE_SEC",
    "capital_price_row_to_bar",
    "compute_depth_proxy",
    "compute_vol_24h_proxy",
    "fetch_capital_bars",
    "fetch_capital_chart_24h",
    "fetch_market_detail",
    "passes_proxy_4_axis",
    "populate_capital_proxies",
    "round_size_to_step",
    "seconds_to_next_close",
    "seconds_to_next_open",
    "size_usd_to_lots",
]
