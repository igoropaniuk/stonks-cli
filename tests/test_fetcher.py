"""Tests for stonks_cli.fetcher."""

import zoneinfo
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from stonks_cli.fetcher import PriceFetcher

_ET = zoneinfo.ZoneInfo("America/New_York")
_AMS = zoneinfo.ZoneInfo("Europe/Amsterdam")
_TYO = zoneinfo.ZoneInfo("Asia/Tokyo")

_PRE_MARKET_TS = datetime(2026, 3, 10, 7, 0, tzinfo=_ET)  # 07:00 ET  -- US pre
_REGULAR_TS = datetime(2026, 3, 10, 12, 0, tzinfo=_ET)  # 12:00 ET  -- US regular
_POST_MARKET_TS = datetime(2026, 3, 10, 17, 0, tzinfo=_ET)  # 17:00 ET  -- US post
_AMS_PRE_TS = datetime(
    2026, 3, 10, 8, 0, tzinfo=_AMS
)  # 08:00 CET -- before Euronext open
_AMS_REGULAR_TS = datetime(
    2026, 3, 10, 13, 0, tzinfo=_AMS
)  # 13:00 CET -- Euronext regular
_AMS_POST_TS = datetime(
    2026, 3, 10, 18, 0, tzinfo=_AMS
)  # 18:00 CET -- after Euronext close
_TYO_PRE_TS = datetime(2026, 3, 10, 8, 0, tzinfo=_TYO)  # 08:00 JST -- before TSE open
_TYO_REGULAR_TS = datetime(2026, 3, 10, 11, 0, tzinfo=_TYO)  # 11:00 JST -- TSE regular
_TYO_POST_TS = datetime(2026, 3, 10, 16, 0, tzinfo=_TYO)  # 16:00 JST -- after TSE close


def _close_df(prices: dict[str, float], date: str = "2026-03-10") -> pd.DataFrame:
    """Build a minimal yfinance-style DataFrame for the given symbol->price map.

    yfinance returns MultiIndex columns (field, ticker) so data["Close"]
    yields a DataFrame with ticker columns.
    """
    idx = pd.to_datetime([date])
    cols = pd.MultiIndex.from_product([["Close"], list(prices.keys())])
    data = [[v for v in prices.values()]]
    return pd.DataFrame(data, columns=cols, index=idx)


def _extended_close_df(prices: dict[str, float], ts: datetime) -> pd.DataFrame:
    """Like _close_df but with a timezone-aware timestamp for session detection."""
    idx = pd.DatetimeIndex([pd.Timestamp(ts)])
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


_OPEN = "stonks_cli.fetcher._is_exchange_open"


class TestFetchExtendedPrices:
    def test_empty_symbols_returns_empty(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.download") as mock_dl:
            result = fetcher.fetch_extended_prices([])
        assert result == {}
        mock_dl.assert_not_called()

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_post_market_price(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 170.0}, _POST_MARKET_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (170.0, "post")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_pre_market_price(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 155.0}, _PRE_MARKET_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (155.0, "pre")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_regular_price(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "regular")}

    @patch(_OPEN, return_value=False)
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_closed_when_exchange_closed(
        self, mock_dl, _open, fetcher: PriceFetcher
    ):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "closed")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_pre_market(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _AMS_PRE_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "pre")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_regular(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _AMS_REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "regular")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_post_market(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _AMS_POST_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "post")}

    @patch(_OPEN, return_value=False)
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_closed(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _AMS_REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "closed")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_pre_market(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _TYO_PRE_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "pre")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_regular(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _TYO_REGULAR_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "regular")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_post_market(self, mock_dl, _open, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _TYO_POST_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "post")}

    @patch("stonks_cli.fetcher.yf.download")
    def test_crypto_always_regular(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"BTC-USD": 50000.0}, _POST_MARKET_TS)
        result = fetcher.fetch_extended_prices(["BTC-USD"])
        assert result == {"BTC-USD": (50000.0, "regular")}

    @patch("stonks_cli.fetcher.yf.download")
    def test_unknown_suffix_falls_back_to_regular(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"FOO.XX": 100.0}, _POST_MARKET_TS)
        result = fetcher.fetch_extended_prices(["FOO.XX"])
        assert result == {"FOO.XX": (100.0, "regular")}

    @patch(_OPEN, return_value=True)
    @patch("stonks_cli.fetcher.yf.download")
    def test_normalises_symbols_to_uppercase(
        self, mock_dl, _open, fetcher: PriceFetcher
    ):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["aapl"])
        assert "AAPL" in result

    @patch("stonks_cli.fetcher.yf.download")
    def test_empty_download_returns_empty(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = pd.DataFrame()
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {}

    @patch("stonks_cli.fetcher.yf.download")
    def test_symbol_missing_from_download_skipped(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 150.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL", "MSFT"])
        assert "AAPL" in result
        assert "MSFT" not in result


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
