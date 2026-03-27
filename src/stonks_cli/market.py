"""Shared market-data pipeline used by both the TUI and the show command."""

import logging
from dataclasses import dataclass, field

import httpx

from stonks_cli.fetcher import CryptoFetcher, PriceFetcher
from stonks_cli.models import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """All market data needed to render a portfolio view."""

    prices: dict[str, float] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    exchange_codes: dict[str, str] = field(default_factory=dict)
    # Keyed by base currency, then by position currency.
    forex_rates: dict[str, dict[str, float]] = field(default_factory=dict)
    prev_closes: dict[str, float] = field(default_factory=dict)


def build_market_snapshot(portfolios: list[Portfolio]) -> MarketSnapshot:
    """Fetch all market data required to display the given portfolios.

    Equity prices are sourced from yfinance (3-tier: extended -> daily batch
    -> individual).  Crypto prices are sourced from CoinGecko with a yfinance
    fallback.  Sessions for batch/single fallbacks are derived via
    ``current_session()`` rather than being hardcoded.
    """
    fetcher = PriceFetcher()
    snap = MarketSnapshot()

    # Build per-symbol metadata from positions and watchlist across all portfolios.
    asset_types: dict[str, str | None] = {}
    external_ids: dict[str, str] = {}
    for portfolio in portfolios:
        for item in portfolio.positions + portfolio.watchlist:
            asset_types[item.symbol] = item.asset_type
            if item.external_id:
                external_ids[item.symbol] = item.external_id

    all_symbols = list(asset_types)
    crypto_symbols = [s for s in all_symbols if asset_types.get(s) == "crypto"]
    equity_symbols = [s for s in all_symbols if s not in crypto_symbols]

    # --- Equity prices (yfinance, 3-tier fallback) ---
    extended = fetcher.fetch_extended_prices(equity_symbols)
    snap.prices.update({sym: price for sym, (price, _) in extended.items()})
    snap.sessions.update({sym: sess for sym, (_, sess) in extended.items()})

    missing = [s for s in equity_symbols if s not in snap.prices]
    if missing:
        fallback = fetcher.fetch_prices(missing)
        snap.prices.update(fallback)
        snap.sessions.update({sym: fetcher.current_session(sym) for sym in fallback})

    still_missing = [s for s in missing if s not in snap.prices]
    for sym in still_missing:
        price = fetcher.fetch_price_single(sym)
        if price is not None:
            snap.prices[sym] = price
            snap.sessions[sym] = fetcher.current_session(sym)

    snap.exchange_codes = fetcher.fetch_exchange_names(all_symbols)
    snap.prev_closes = fetcher.fetch_previous_closes(equity_symbols)

    # --- Crypto prices (CoinGecko, yfinance fallback) ---
    if crypto_symbols:
        try:
            crypto_fetcher = CryptoFetcher()
            crypto_prices, crypto_prev = crypto_fetcher.fetch_prices_and_changes(
                crypto_symbols, external_ids=external_ids
            )
            snap.prices.update(crypto_prices)
            snap.sessions.update({sym: "regular" for sym in crypto_prices})
            snap.prev_closes.update(crypto_prev)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "CoinGecko fetch failed (%s); falling back to yfinance for crypto.",
                exc,
            )
            yf_crypto = fetcher.fetch_prices(crypto_symbols)
            snap.prices.update(yf_crypto)
            snap.sessions.update({sym: "regular" for sym in yf_crypto})
            snap.prev_closes.update(fetcher.fetch_previous_closes(crypto_symbols))
        except Exception as exc:
            logger.error("Unexpected error during CoinGecko fetch: %s", exc)

    # --- Forex rates (one dict per base currency) ---
    all_currencies = list(
        {p.currency for portfolio in portfolios for p in portfolio.positions}
        | {c.currency for portfolio in portfolios for c in portfolio.cash}
    )
    for base in {p.base_currency for p in portfolios}:
        snap.forex_rates[base] = fetcher.fetch_forex_rates(all_currencies, base=base)

    return snap
