"""Market data fetching via yfinance."""

import math
import warnings

import yfinance as yf


def _finite(value) -> float | None:
    """Convert *value* to float, returning None for None/NaN/inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


class PriceFetcher:
    """Fetches the latest closing price for a list of stock symbols.

    Uses yfinance.download() to batch all symbols in a single API call.
    """

    def fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return the most recent closing price for each symbol.

        Symbols with no available data are silently omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol → latest closing price.
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]

        data = yf.download(
            tickers=normalized,
            period="1d",
            auto_adjust=True,
            progress=False,
        )

        if data.empty:
            return {}

        # With multi_level_index=True (yfinance default), data["Close"] is
        # always a DataFrame whose columns are the ticker symbols.
        close = data["Close"]

        result: dict[str, float] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                result[symbol] = float(series.iloc[-1])

        return result

    def fetch_extended_prices(self, symbols: list[str]) -> dict[str, tuple[float, str]]:
        """Return the best available price for each symbol, including extended hours.

        For each symbol, queries ``yf.Ticker(symbol).info`` and picks the
        first finite price in priority order: postMarketPrice → preMarketPrice →
        regularMarketPrice.  The session label reflects which source was used:
        "post", "pre", or "regular".

        Symbols with no valid price or where the Ticker call raises are silently
        omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol → (price, session).
        """
        if not symbols:
            return {}

        result: dict[str, tuple[float, str]] = {}
        for raw in symbols:
            symbol = raw.upper()
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", module="yfinance")
                    info = yf.Ticker(symbol).info
                post = _finite(info.get("postMarketPrice"))
                if post is not None:
                    result[symbol] = (post, "post")
                    continue
                pre = _finite(info.get("preMarketPrice"))
                if pre is not None:
                    result[symbol] = (pre, "pre")
                    continue
                regular = _finite(
                    info.get("regularMarketPrice") or info.get("currentPrice")
                )
                if regular is not None:
                    result[symbol] = (regular, "regular")
            except Exception:
                pass

        return result

    def fetch_forex_rates(
        self, currencies: list[str], base: str = "USD"
    ) -> dict[str, float]:
        """Return exchange rates: 1 unit of currency → how many base units.

        Uses yfinance forex pairs (e.g. EURUSD=X for EUR→USD).
        The base currency is always included as 1.0. Currencies for which
        no rate can be fetched are omitted from the result.

        Args:
            currencies: ISO 4217 currency codes (e.g. ['EUR', 'GBP']).
            base: Target/base currency code (default 'USD').

        Returns:
            Mapping of currency code → exchange rate in base.
        """
        base = base.upper()
        rates: dict[str, float] = {base: 1.0}

        non_base = [c.upper() for c in currencies if c.upper() != base]
        if not non_base:
            return rates

        symbols = [f"{c}{base}=X" for c in non_base]

        data = yf.download(
            tickers=symbols,
            period="1d",
            auto_adjust=False,
            progress=False,
        )

        if data.empty:
            return rates

        close = data["Close"]
        for currency, symbol in zip(non_base, symbols):
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                rates[currency] = float(series.iloc[-1])

        return rates
