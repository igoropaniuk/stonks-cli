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
