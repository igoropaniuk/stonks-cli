"""Tests for stonks_cli.fetcher."""

from unittest.mock import patch

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
