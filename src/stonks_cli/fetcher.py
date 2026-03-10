"""Market data fetching via yfinance."""

import yfinance as yf


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
