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


def _collect_asset_symbols(
    portfolios: list[Portfolio],
) -> tuple[dict[str, str | None], dict[str, str]]:
    """Return per-symbol asset-type and external-id maps across all portfolios."""
    asset_types: dict[str, str | None] = {}
    external_ids: dict[str, str] = {}
    for portfolio in portfolios:
        for item in portfolio.positions + portfolio.watchlist:
            asset_types[item.symbol] = item.asset_type
            if item.external_id:
                external_ids[item.symbol] = item.external_id
    return asset_types, external_ids


def _fetch_equity_data(
    fetcher: PriceFetcher,
    equity_symbols: list[str],
) -> tuple[dict[str, float], dict[str, str], dict[str, float]]:
    """Return prices, sessions, and previous closes for equity symbols."""
    equity = fetcher.fetch_best_equity_prices(equity_symbols)
    prices = {sym: price for sym, (price, _) in equity.items()}
    sessions = {sym: sess for sym, (_, sess) in equity.items()}
    prev_closes = fetcher.fetch_previous_closes(equity_symbols)
    return prices, sessions, prev_closes


def _fetch_crypto_data(
    fetcher: PriceFetcher,
    crypto_symbols: list[str],
    external_ids: dict[str, str],
) -> tuple[dict[str, float], dict[str, str], dict[str, float]]:
    """Return prices, sessions, and previous closes for crypto symbols.

    Tries CoinGecko first; falls back to yfinance on network/HTTP errors.
    Returns empty dicts if an unexpected error occurs.
    """
    try:
        crypto_fetcher = CryptoFetcher()
        prices, prev_closes = crypto_fetcher.fetch_prices_and_changes(
            crypto_symbols, external_ids=external_ids
        )
        sessions = {sym: "regular" for sym in prices}
        return prices, sessions, prev_closes
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning(
            "CoinGecko fetch failed (%s); falling back to yfinance for crypto.", exc
        )
        prices = fetcher.fetch_prices(crypto_symbols)
        sessions = {sym: "regular" for sym in prices}
        prev_closes = fetcher.fetch_previous_closes(crypto_symbols)
        return prices, sessions, prev_closes
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error during CoinGecko fetch: %s", exc)
        return {}, {}, {}


def _fetch_forex_data(
    fetcher: PriceFetcher,
    portfolios: list[Portfolio],
) -> dict[str, dict[str, float]]:
    """Return forex rates keyed by base currency, then by position currency."""
    all_currencies = list(
        {p.currency for portfolio in portfolios for p in portfolio.positions}
        | {c.currency for portfolio in portfolios for c in portfolio.cash}
    )
    return {
        base: fetcher.fetch_forex_rates(all_currencies, base=base)
        for base in {p.base_currency for p in portfolios}
    }


def build_market_snapshot(portfolios: list[Portfolio]) -> MarketSnapshot:
    """Fetch all market data required to display the given portfolios.

    Equity prices are sourced from yfinance (3-tier: extended -> daily batch
    -> individual).  Crypto prices are sourced from CoinGecko with a yfinance
    fallback.  Sessions for batch/single fallbacks are derived via
    ``current_session()`` rather than being hardcoded.
    """
    fetcher = PriceFetcher()
    snap = MarketSnapshot()

    asset_types, external_ids = _collect_asset_symbols(portfolios)
    all_symbols = list(asset_types)
    crypto_symbols = [s for s in all_symbols if asset_types.get(s) == "crypto"]
    equity_symbols = [s for s in all_symbols if s not in crypto_symbols]

    eq_prices, eq_sessions, eq_prev = _fetch_equity_data(fetcher, equity_symbols)
    snap.prices.update(eq_prices)
    snap.sessions.update(eq_sessions)
    snap.prev_closes.update(eq_prev)

    snap.exchange_codes = fetcher.fetch_exchange_names(all_symbols)

    if crypto_symbols:
        cr_prices, cr_sessions, cr_prev = _fetch_crypto_data(
            fetcher, crypto_symbols, external_ids
        )
        snap.prices.update(cr_prices)
        snap.sessions.update(cr_sessions)
        snap.prev_closes.update(cr_prev)

    snap.forex_rates = _fetch_forex_data(fetcher, portfolios)

    return snap
