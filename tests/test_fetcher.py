"""Tests for stonks_cli.fetcher."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stonks_cli.fetcher import PriceFetcher


def _close_df(prices: dict[str, float], date: str = "2026-03-10") -> pd.DataFrame:
    """Build a minimal yfinance-style DataFrame for the given symbol→price map.

    yfinance returns MultiIndex columns (field, ticker) so data["Close"]
    yields a DataFrame with ticker columns.
    """
    idx = pd.to_datetime([date])
    cols = pd.MultiIndex.from_product([["Close"], list(prices.keys())])
    data = [[v for v in prices.values()]]
    return pd.DataFrame(data, columns=cols, index=idx)


@pytest.fixture
def fetcher() -> PriceFetcher:
    return PriceFetcher()


class TestFetchPrices:
    def test_empty_symbols_skips_download(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.download") as mock_dl:
            result = fetcher.fetch_prices([])
        assert result == {}
        mock_dl.assert_not_called()

    @patch("stonks_cli.fetcher.yf.download")
    def test_single_symbol(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"AAPL": 150.0})
        assert fetcher.fetch_prices(["AAPL"]) == {"AAPL": 150.0}

    @patch("stonks_cli.fetcher.yf.download")
    def test_multiple_symbols(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"AAPL": 150.0, "NVDA": 900.0})
        prices = fetcher.fetch_prices(["AAPL", "NVDA"])
        assert prices == {"AAPL": 150.0, "NVDA": 900.0}

    @patch("stonks_cli.fetcher.yf.download")
    def test_normalises_symbols_to_uppercase(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"AAPL": 150.0})
        prices = fetcher.fetch_prices(["aapl"])
        assert "AAPL" in prices
        # yf.download should receive the uppercased list
        mock_dl.assert_called_once_with(
            tickers=["AAPL"],
            period="1d",
            auto_adjust=True,
            progress=False,
        )

    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_empty_dict_on_empty_download(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = pd.DataFrame()
        assert fetcher.fetch_prices(["AAPL"]) == {}

    @patch("stonks_cli.fetcher.yf.download")
    def test_skips_symbol_with_all_nan_prices(self, mock_dl, fetcher: PriceFetcher):
        idx = pd.to_datetime(["2026-03-10"])
        cols = pd.MultiIndex.from_product([["Close"], ["AAPL", "NVDA"]])
        df = pd.DataFrame([[float("nan"), 900.0]], columns=cols, index=idx)
        mock_dl.return_value = df

        prices = fetcher.fetch_prices(["AAPL", "NVDA"])
        assert "AAPL" not in prices
        assert prices["NVDA"] == pytest.approx(900.0)

    @patch("stonks_cli.fetcher.yf.download")
    def test_skips_unknown_symbol(self, mock_dl, fetcher: PriceFetcher):
        # yfinance simply won't include unknown tickers in the result columns
        mock_dl.return_value = _close_df({"AAPL": 150.0})
        prices = fetcher.fetch_prices(["AAPL", "UNKNOWN"])
        assert prices == {"AAPL": 150.0}
        assert "UNKNOWN" not in prices


class TestFetchExtendedPrices:
    def _make_info(self, data: dict):
        mock = MagicMock()
        mock.get = lambda key, default=None: data.get(key, default)
        return mock

    def test_empty_symbols_returns_empty(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            result = fetcher.fetch_extended_prices([])
        assert result == {}
        mock_ticker.assert_not_called()

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_returns_post_market_price_when_available(
        self, mock_ticker, fetcher: PriceFetcher
    ):
        info = self._make_info(
            {
                "postMarketPrice": 170.0,
                "preMarketPrice": 155.0,
                "regularMarketPrice": 160.0,
            }
        )
        mock_ticker.return_value.info = info
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (170.0, "post")}

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_returns_pre_market_price_when_priority(
        self, mock_ticker, fetcher: PriceFetcher
    ):
        info = self._make_info(
            {
                "postMarketPrice": None,
                "preMarketPrice": 155.0,
                "regularMarketPrice": 160.0,
            }
        )
        mock_ticker.return_value.info = info
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (155.0, "pre")}

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_falls_back_to_regular_price(self, mock_ticker, fetcher: PriceFetcher):
        info = self._make_info(
            {
                "postMarketPrice": None,
                "preMarketPrice": None,
                "regularMarketPrice": 160.0,
            }
        )
        mock_ticker.return_value.info = info
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "regular")}

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_falls_back_to_current_price(self, mock_ticker, fetcher: PriceFetcher):
        info = self._make_info({"currentPrice": 160.0})
        mock_ticker.return_value.info = info
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "regular")}

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_normalises_symbols_to_uppercase(self, mock_ticker, fetcher: PriceFetcher):
        info = self._make_info({"regularMarketPrice": 160.0})
        mock_ticker.return_value.info = info
        result = fetcher.fetch_extended_prices(["aapl"])
        assert "AAPL" in result
        mock_ticker.assert_called_once_with("AAPL")

    @patch("stonks_cli.fetcher.yf.Ticker")
    def test_skips_symbol_on_exception(self, mock_ticker, fetcher: PriceFetcher):
        mock_ticker.side_effect = RuntimeError("network error")
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {}


class TestFetchForexRates:
    def test_base_currency_always_one(self, fetcher: PriceFetcher):
        rates = fetcher.fetch_forex_rates([], base="USD")
        assert rates == {"USD": 1.0}

    def test_only_base_currency_skips_download(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.download") as mock_dl:
            rates = fetcher.fetch_forex_rates(["USD"], base="USD")
        assert rates == {"USD": 1.0}
        mock_dl.assert_not_called()

    @patch("stonks_cli.fetcher.yf.download")
    def test_single_non_base_currency(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"EURUSD=X": 1.085})
        rates = fetcher.fetch_forex_rates(["EUR"], base="USD")
        assert rates["USD"] == 1.0
        assert rates["EUR"] == pytest.approx(1.085)

    @patch("stonks_cli.fetcher.yf.download")
    def test_multiple_currencies(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"EURUSD=X": 1.085, "GBPUSD=X": 1.27})
        rates = fetcher.fetch_forex_rates(["EUR", "GBP"], base="USD")
        assert rates["EUR"] == pytest.approx(1.085)
        assert rates["GBP"] == pytest.approx(1.27)
        assert rates["USD"] == 1.0

    @patch("stonks_cli.fetcher.yf.download")
    def test_uses_correct_yfinance_symbols(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _close_df({"EURUSD=X": 1.085})
        fetcher.fetch_forex_rates(["EUR"], base="USD")
        mock_dl.assert_called_once_with(
            tickers=["EURUSD=X"],
            period="1d",
            auto_adjust=False,
            progress=False,
        )

    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_base_only_on_empty_download(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = pd.DataFrame()
        rates = fetcher.fetch_forex_rates(["EUR"], base="USD")
        assert rates == {"USD": 1.0}

    @patch("stonks_cli.fetcher.yf.download")
    def test_omits_currency_with_no_data(self, mock_dl, fetcher: PriceFetcher):
        # Only EUR returned, GBP missing from columns
        mock_dl.return_value = _close_df({"EURUSD=X": 1.085})
        rates = fetcher.fetch_forex_rates(["EUR", "GBP"], base="USD")
        assert "EUR" in rates
        assert "GBP" not in rates
