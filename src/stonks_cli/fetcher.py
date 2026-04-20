"""Market data fetching via yfinance."""

import logging
import math
from concurrent.futures import ThreadPoolExecutor

import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf

from stonks_cli.crypto_fetcher import CryptoFetcher as CryptoFetcher  # noqa: F401
from stonks_cli.exchanges import ExchangeSession
from stonks_cli.market_session import Session
from stonks_cli.stock_detail import StockDetail  # re-export for backward compat

logger = logging.getLogger(__name__)

# Keep this small: each worker thread opens its own peewee/SQLite connection
# to yfinance's timezone cache (3 fds in WAL mode), so a large pool exhausts
# macOS's default 256-fd limit quickly.
_MAX_EXCHANGE_FETCH_WORKERS = 2


def _last_close_per_symbol(
    close: "pd.DataFrame",
    symbols: list[str],
) -> dict[str, float]:
    """Return the last non-NaN close value for each symbol in *close*.

    Args:
        close: DataFrame returned by :func:`_yf_download_close` (columns are
            ticker symbols, index is a datetime).
        symbols: Symbols to extract; those absent from *close* are skipped.

    Returns:
        Mapping of symbol -> ``float(series.iloc[-1])`` for every symbol that
        has at least one non-NaN row.
    """
    result: dict[str, float] = {}
    for sym in symbols:
        if sym not in close.columns:
            continue
        series = close[sym].dropna()
        if not series.empty:
            result[sym] = float(series.iloc[-1])
    return result


def _yf_last_prices(
    symbols: list[str],
    period: str,
    description: str = "price",
) -> dict[str, float]:
    """Normalize, batch-download, and return the last closing price per symbol.

    Combines the normalize -> :func:`_yf_download_close` ->
    :func:`_last_close_per_symbol` pipeline that several ``PriceFetcher``
    methods share.  Returns an empty dict when *symbols* is empty or the
    download fails.

    Args:
        symbols: Ticker symbols (will be uppercased).
        period: yfinance period string (e.g. ``"1d"``, ``"5d"``).
        description: Short label used in error log messages.
    """
    if not symbols:
        return {}
    normalized = [s.upper() for s in symbols]
    close = _yf_download_close(normalized, period=period, description=description)
    if close is None:
        return {}
    return _last_close_per_symbol(close, normalized)


def _yf_download_close(
    symbols: list[str],
    *,
    period: str,
    description: str = "price",
    auto_adjust: bool = True,
    interval: str | None = None,
    prepost: bool = False,
) -> "pd.DataFrame | None":
    """Call ``yf.download`` and return the ``Close`` DataFrame, or ``None`` on failure.

    Encapsulates the common normalize -> download -> empty-check pattern shared
    by all batch-download methods.  Each caller is responsible for extracting
    and interpreting the per-symbol series from the returned DataFrame.

    Args:
        symbols: Already-normalised ticker list passed directly to yfinance.
        period: yfinance period string (e.g. ``"1d"``, ``"5d"``).
        description: Short label used in error log messages.
        auto_adjust: Passed to ``yf.download`` (default True).
        interval: Optional bar interval (e.g. ``"1m"``); omitted when None.
        prepost: Whether to include pre/post-market bars (default False).

    Returns:
        The ``data["Close"]`` DataFrame (always a DataFrame, never a Series),
        or ``None`` if the download failed or returned an empty result.
    """
    kw: dict = {
        "tickers": symbols,
        "period": period,
        "auto_adjust": auto_adjust,
        "progress": False,
        "threads": False,
    }
    if interval is not None:
        kw["interval"] = interval
    if prepost:
        kw["prepost"] = True
    try:
        data = yf.download(**kw)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s download failed for %s: %s", description, symbols, exc)
        return None
    if data.empty:
        return None
    close = data["Close"]
    # yfinance returns a Series (not a DataFrame) when a single ticker is
    # requested and the result has no MultiIndex.  Normalise so callers can
    # always assume a DataFrame.
    if isinstance(close, pd.Series):
        close = close.to_frame(name=symbols[0])
    return close


class PriceFetcher:
    """Fetches the latest closing price for a list of stock symbols.

    Uses yfinance.download() to batch all symbols in a single API call.
    """

    def __init__(self) -> None:
        self._session = ExchangeSession()

    def fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return the most recent closing price for each symbol.

        Symbols with no available data are silently omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> latest closing price.
        """
        # With multi_level_index=True (yfinance default), data["Close"] is
        # always a DataFrame whose columns are the ticker symbols.
        return _yf_last_prices(symbols, period="1d", description="price")

    def fetch_previous_closes(self, symbols: list[str]) -> dict[str, float]:
        """Return the previous trading day's closing price for each symbol.

        Uses a 5-day window to account for weekends and holidays.
        Symbols with fewer than 2 data points are omitted.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> previous closing price.
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]
        close = _yf_download_close(
            normalized, period="5d", description="previous-close"
        )
        if close is None:
            return {}

        # Keep only rows strictly before today so we always get the
        # last *completed* trading day's close, regardless of whether
        # yfinance already includes a partial row for today.
        today = pd.Timestamp("today").normalize()
        filtered = close[close.index.normalize() < today]
        return _last_close_per_symbol(filtered, normalized)

    def current_session(self, symbol: str) -> str:
        """Return the current market session label for *symbol*.

        Delegates to :meth:`ExchangeSession.current_session`.
        Returns one of ``'pre'``, ``'regular'``, ``'post'``, or ``'closed'``.
        """
        return self._session.current_session(symbol)

    def fetch_price_single(self, symbol: str) -> float | None:
        """Return the most recent price for *symbol* using an individual lookup.

        Uses ``yf.Ticker.fast_info`` so it is not affected by the DataFrame
        alignment issues that can occur when batch-downloading tickers from
        multiple exchanges.  Returns ``None`` when no price is available.
        """
        try:
            price = yf.Ticker(symbol.upper()).fast_info.last_price
            if price is None or (isinstance(price, float) and math.isnan(price)):
                return None
            return float(price)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            logger.debug("Cannot read fast_info price for %s: %s", symbol, exc)
            return None

    def _extract_latest_with_session(
        self, close: "pd.DataFrame", symbols: list[str]
    ) -> dict[str, tuple[float, str]]:
        """Return ``{symbol: (price, session)}`` from a Close DataFrame.

        If a symbol's last bar is from a previous day, the session is set to
        ``Session.STALE`` when the exchange is supposed to be trading (so the
        table can flag the price as non-live) or ``Session.CLOSED`` when the
        exchange itself is closed (weekend / holiday).
        """
        today = pd.Timestamp.now(tz="UTC").normalize()
        result: dict[str, tuple[float, str]] = {}
        for symbol in symbols:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if series.empty:
                continue
            price = float(series.iloc[-1])
            last_bar_ts = series.index[-1]
            if last_bar_ts.tzinfo is None:
                last_bar_ts = last_bar_ts.tz_localize("UTC")
            last_bar_date = last_bar_ts.tz_convert("UTC").normalize()
            live_session = self.current_session(symbol)
            if last_bar_date < today:
                result[symbol] = (
                    price,
                    Session.CLOSED if live_session == Session.CLOSED else Session.STALE,
                )
            else:
                result[symbol] = (price, live_session)
        return result

    def fetch_extended_prices(self, symbols: list[str]) -> dict[str, tuple[float, str]]:
        """Return the best available price for each symbol, including extended hours.

        Uses a single batched ``yf.download()`` call with ``prepost=True`` and a
        1-minute interval so that pre- and post-market bars are included.  The
        session label is derived from the timestamp of each symbol's last bar
        relative to its exchange's market hours.  If the exchange is currently
        closed (weekend or holiday), the session is "closed".  If the exchange
        should be trading but the last bar is from a previous day, the session
        is "stale".

        * Equities on supported exchanges: pre/regular/post/closed/stale
        * Crypto or equities on unknown exchanges: always "regular"

        Symbols with no available data are silently omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> (price, session).
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]
        close = _yf_download_close(
            normalized,
            period="1d",
            description="extended-price",
            interval="1m",
            prepost=True,
        )
        if close is None:
            return {}

        return self._extract_latest_with_session(close, normalized)

    def fetch_daily_prices_with_session(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, str]]:
        """Return ``{symbol: (price, session)}`` from the most recent daily bar.

        Like :meth:`fetch_prices`, but also returns the session label and flags
        stale bars (last bar from a previous day) so the table can render the
        row without a spurious zero-change.
        """
        if not symbols:
            return {}
        normalized = [s.upper() for s in symbols]
        close = _yf_download_close(normalized, period="1d", description="price")
        if close is None:
            return {}
        return self._extract_latest_with_session(close, normalized)

    def fetch_best_equity_prices(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, str]]:
        """Return the best available price and session for each equity symbol.

        Applies a 3-tier yfinance fallback strategy:
        1. Extended-hours batch (``prepost=True``, 1-minute bars)
        2. Regular daily batch for symbols still missing after tier 1
        3. Individual ``fast_info`` lookup for any remaining symbols

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> (price, session).
        """
        if not symbols:
            return {}

        result: dict[str, tuple[float, str]] = {}

        # Tier 1: extended-hours batch.
        result.update(self.fetch_extended_prices(symbols))

        # Tier 2: regular daily batch for symbols still missing.  Uses the
        # daily-bars variant that flags stale data so a Friday-close returned
        # on a Monday-morning query doesn't render as a spurious zero-change.
        missing = [s for s in symbols if s.upper() not in result]
        if missing:
            result.update(self.fetch_daily_prices_with_session(missing))

        # Tier 3: individual fast_info for any still-missing symbols.
        still_missing = [s for s in symbols if s.upper() not in result]
        for sym in still_missing:
            single = self.fetch_price_single(sym)
            if single is not None:
                result[sym.upper()] = (single, self.current_session(sym))

        return result

    def fetch_exchange_names(self, symbols: list[str]) -> dict[str, str]:
        """Return yfinance exchange codes for all equity symbols (US and non-US).

        Crypto symbols (containing '-') are skipped -- they have no exchange
        code in yfinance.  All other symbols (plain US tickers and those with
        a dot suffix) are queried concurrently.

        Uses a thread pool so that N symbols are fetched concurrently rather
        than sequentially.

        Args:
            symbols: List of ticker symbols (mixed US and non-US).

        Returns:
            Mapping of uppercase symbol -> yfinance exchange code
            (e.g. ``{"AAPL": "NMS", "ASML.AS": "AMS", "7203.T": "TKY"}``).
        """
        equities = [s.upper() for s in symbols if "-" not in s]
        if not equities:
            return {}

        def _get(sym: str) -> tuple[str, str | None]:
            try:
                return sym, yf.Ticker(sym).fast_info.exchange
            except Exception as exc:  # noqa: BLE001
                logger.debug("Cannot fetch exchange name for %s: %s", sym, exc)
                return sym, None

        result: dict[str, str] = {}
        with ThreadPoolExecutor(
            max_workers=min(_MAX_EXCHANGE_FETCH_WORKERS, len(equities))
        ) as pool:
            for sym, code in pool.map(_get, equities):
                if code:
                    result[sym] = code
        return result

    def fetch_forex_rates(
        self, currencies: list[str], base: str = "USD"
    ) -> dict[str, float]:
        """Return exchange rates: 1 unit of currency -> how many base units.

        Uses yfinance forex pairs (e.g. EURUSD=X for EUR->USD).
        The base currency is always included as 1.0. Currencies for which
        no rate can be fetched are omitted from the result.

        Args:
            currencies: ISO 4217 currency codes (e.g. ['EUR', 'GBP']).
            base: Target/base currency code (default 'USD').

        Returns:
            Mapping of currency code -> exchange rate in base.
        """
        base = base.upper()
        rates: dict[str, float] = {base: 1.0}

        non_base = [c.upper() for c in currencies if c.upper() != base]
        if not non_base:
            return rates

        symbols = [f"{c}{base}=X" for c in non_base]
        close = _yf_download_close(
            symbols, period="1d", description="forex", auto_adjust=False
        )
        if close is None:
            return rates

        last = _last_close_per_symbol(close, symbols)
        for currency, symbol in zip(non_base, symbols):
            if symbol in last:
                rates[currency] = last[symbol]
        return rates

    def fetch_stock_detail(self, symbol: str) -> StockDetail:
        """Fetch comprehensive detail for a single ticker.

        Delegates to :class:`~stonks_cli.stock_detail.StockDetailFetcher`.
        Kept on ``PriceFetcher`` for backward compatibility with callers that
        already hold a ``PriceFetcher`` instance.
        """
        from stonks_cli.stock_detail import StockDetailFetcher

        return StockDetailFetcher().fetch_stock_detail(symbol)
