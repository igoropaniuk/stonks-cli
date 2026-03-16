"""Tests for stonks_cli.fetcher."""

import zoneinfo
from datetime import datetime
from datetime import time as dtime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stonks_cli.fetcher import (
    PriceFetcher,
    _exchange_calendar_name,
    _finite,
    _is_exchange_open,
    _is_trading_day,
    _market_session,
    exchange_label,
)

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
_TRADING_DAY = "stonks_cli.fetcher._is_trading_day"
_CURRENT_SESSION = "stonks_cli.fetcher.PriceFetcher.current_session"


class TestFetchExtendedPrices:
    def test_empty_symbols_returns_empty(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.download") as mock_dl:
            result = fetcher.fetch_extended_prices([])
        assert result == {}
        mock_dl.assert_not_called()

    @patch(_CURRENT_SESSION, return_value="post")
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_post_market_price(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 170.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (170.0, "post")}

    @patch(_CURRENT_SESSION, return_value="pre")
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_pre_market_price(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 155.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (155.0, "pre")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_regular_price(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "regular")}

    @patch(_CURRENT_SESSION, return_value="closed")
    @patch("stonks_cli.fetcher.yf.download")
    def test_returns_closed_when_exchange_closed(
        self, mock_dl, _cs, fetcher: PriceFetcher
    ):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {"AAPL": (160.0, "closed")}

    @patch(_CURRENT_SESSION, return_value="pre")
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_pre_market(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "pre")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_regular(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "regular")}

    @patch(_CURRENT_SESSION, return_value="post")
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_post_market(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "post")}

    @patch(_CURRENT_SESSION, return_value="closed")
    @patch("stonks_cli.fetcher.yf.download")
    def test_eu_closed(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"ASML.AS": 800.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["ASML.AS"])
        assert result == {"ASML.AS": (800.0, "closed")}

    @patch(_CURRENT_SESSION, return_value="pre")
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_pre_market(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "pre")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_regular(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "regular")}

    @patch(_CURRENT_SESSION, return_value="post")
    @patch("stonks_cli.fetcher.yf.download")
    def test_asia_post_market(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"7203.T": 2000.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["7203.T"])
        assert result == {"7203.T": (2000.0, "post")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_crypto_always_regular(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"BTC-USD": 50000.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["BTC-USD"])
        assert result == {"BTC-USD": (50000.0, "regular")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_unknown_suffix_falls_back_to_regular(
        self, mock_dl, _cs, fetcher: PriceFetcher
    ):
        mock_dl.return_value = _extended_close_df({"FOO.XX": 100.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["FOO.XX"])
        assert result == {"FOO.XX": (100.0, "regular")}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_normalises_symbols_to_uppercase(self, mock_dl, _cs, fetcher: PriceFetcher):
        mock_dl.return_value = _extended_close_df({"AAPL": 160.0}, _REGULAR_TS)
        result = fetcher.fetch_extended_prices(["aapl"])
        assert "AAPL" in result

    @patch("stonks_cli.fetcher.yf.download")
    def test_empty_download_returns_empty(self, mock_dl, fetcher: PriceFetcher):
        mock_dl.return_value = pd.DataFrame()
        result = fetcher.fetch_extended_prices(["AAPL"])
        assert result == {}

    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_symbol_missing_from_download_skipped(
        self, mock_dl, _cs, fetcher: PriceFetcher
    ):
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


def _make_ticker(exchange_code: str | None):
    """Return a mock yf.Ticker whose fast_info.exchange is *exchange_code*."""
    ticker = MagicMock()
    ticker.fast_info.exchange = exchange_code
    return ticker


class TestFetchExchangeNames:
    def test_empty_symbols_returns_empty(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            result = fetcher.fetch_exchange_names([])
        assert result == {}
        mock_ticker.assert_not_called()

    def test_crypto_symbols_skipped(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            result = fetcher.fetch_exchange_names(["BTC-USD", "ETH-EUR"])
        assert result == {}
        mock_ticker.assert_not_called()

    def test_us_symbols_resolved(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.side_effect = lambda sym: _make_ticker(
                {"AAPL": "NMS", "JPM": "NYQ"}[sym]
            )
            result = fetcher.fetch_exchange_names(["AAPL", "JPM"])
        assert result == {"AAPL": "NMS", "JPM": "NYQ"}

    def test_eu_symbol_resolved(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.side_effect = lambda sym: _make_ticker({"ASML.AS": "AMS"}[sym])
            result = fetcher.fetch_exchange_names(["ASML.AS"])
        assert result == {"ASML.AS": "AMS"}

    def test_asia_symbol_resolved(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.side_effect = lambda sym: _make_ticker({"7203.T": "TKY"}[sym])
            result = fetcher.fetch_exchange_names(["7203.T"])
        assert result == {"7203.T": "TKY"}

    def test_mixed_symbols(self, fetcher: PriceFetcher):
        codes = {"AAPL": "NMS", "ASML.AS": "AMS", "7203.T": "TKY"}
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.side_effect = lambda sym: _make_ticker(codes[sym])
            result = fetcher.fetch_exchange_names(
                ["AAPL", "ASML.AS", "7203.T", "BTC-USD"]
            )
        # crypto excluded, equities resolved
        assert result == {"AAPL": "NMS", "ASML.AS": "AMS", "7203.T": "TKY"}
        assert "BTC-USD" not in result

    def test_failed_fetch_omitted(self, fetcher: PriceFetcher):
        def _side(sym):
            if sym == "BAD":
                raise RuntimeError("network error")
            return _make_ticker("NMS")

        with patch("stonks_cli.fetcher.yf.Ticker", side_effect=_side):
            result = fetcher.fetch_exchange_names(["AAPL", "BAD"])
        assert "AAPL" in result
        assert "BAD" not in result

    def test_none_code_omitted(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value = _make_ticker(None)
            result = fetcher.fetch_exchange_names(["AAPL"])
        assert result == {}


class TestExchangeLabel:
    def test_crypto_returns_crypto(self):
        assert exchange_label("BTC-USD") == "Crypto"
        assert exchange_label("ETH-EUR") == "Crypto"

    def test_us_with_known_code(self):
        assert exchange_label("AAPL", "NMS") == "NASDAQ"
        assert exchange_label("JPM", "NYQ") == "NYSE"

    def test_us_without_code_fallback(self):
        assert exchange_label("AAPL") == "NYSE/NASDAQ"

    def test_eu_suffix_via_yf_code(self):
        assert exchange_label("ASML.AS", "AMS") == "ENX AMS"
        assert exchange_label("HSBA.L", "LSE") == "LSE"

    def test_eu_suffix_fallback_no_code(self):
        assert exchange_label("ASML.AS") == "ENX AMS"

    def test_eu_suffix_unknown_yf_code_falls_back_to_suffix(self):
        # Unknown yfinance code -- suffix label used
        assert exchange_label("ASML.AS", "ZZZZZ") == "ENX AMS"

    def test_asia_suffix_via_yf_code(self):
        assert exchange_label("7203.T", "TKY") == "TSE"
        assert exchange_label("005930.KS", "KRX") == "KRX"

    def test_asia_suffix_fallback_no_code(self):
        assert exchange_label("7203.T") == "TSE"

    def test_unknown_suffix_returns_raw_suffix(self):
        assert exchange_label("FOO.XX") == "XX"


class TestFinite:
    def test_none_returns_none(self):
        assert _finite(None) is None

    def test_non_numeric_string_returns_none(self):
        assert _finite("not_a_number") is None

    def test_inf_returns_none(self):
        assert _finite(float("inf")) is None

    def test_nan_returns_none(self):
        assert _finite(float("nan")) is None

    def test_valid_float_returned(self):
        assert _finite(42.5) == pytest.approx(42.5)

    def test_zero_returned(self):
        assert _finite(0.0) == pytest.approx(0.0)


class TestMarketSession:
    _OPEN = dtime(9, 30)
    _CLOSE = dtime(16, 0)
    _ET = zoneinfo.ZoneInfo("America/New_York")

    def test_invalid_timezone_returns_regular(self):
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=self._ET)
        result = _market_session(ts, "Invalid/Timezone", self._OPEN, self._CLOSE)
        assert result == "regular"

    def test_pre_session(self):
        ts = datetime(2026, 3, 10, 7, 0, tzinfo=self._ET)
        assert _market_session(ts, "America/New_York", self._OPEN, self._CLOSE) == "pre"

    def test_regular_session(self):
        ts = datetime(2026, 3, 10, 12, 0, tzinfo=self._ET)
        assert (
            _market_session(ts, "America/New_York", self._OPEN, self._CLOSE)
            == "regular"
        )

    def test_post_session(self):
        ts = datetime(2026, 3, 10, 17, 0, tzinfo=self._ET)
        assert (
            _market_session(ts, "America/New_York", self._OPEN, self._CLOSE) == "post"
        )


class TestIsExchangeOpen:
    _TZ = "America/New_York"
    _OPEN = dtime(9, 30)
    _CLOSE = dtime(16, 0)

    def test_calendar_success_returns_calendar_result(self):
        with patch("stonks_cli.fetcher._load_calendar") as mock_cal:
            mock_cal.return_value.is_open_on_minute.return_value = True
            result = _is_exchange_open(self._TZ, self._OPEN, self._CLOSE, "XNYS")
        assert result is True

    def test_calendar_exception_falls_back_to_time_check(self):
        with patch("stonks_cli.fetcher._load_calendar", side_effect=LookupError):
            with patch("stonks_cli.fetcher.datetime") as mock_dt:
                mock_now = MagicMock()
                mock_now.weekday.return_value = 0  # Monday
                mock_now.time.return_value = dtime(12, 0)
                mock_dt.now.return_value = mock_now
                result = _is_exchange_open(self._TZ, self._OPEN, self._CLOSE, "XNYS")
        assert result is True

    def test_weekend_returns_false(self):
        with patch("stonks_cli.fetcher.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 6  # Sunday
            mock_dt.now.return_value = mock_now
            result = _is_exchange_open(self._TZ, self._OPEN, self._CLOSE)
        assert result is False

    def test_weekday_within_hours_returns_true(self):
        with patch("stonks_cli.fetcher.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 1  # Tuesday
            mock_now.time.return_value = dtime(12, 0)
            mock_dt.now.return_value = mock_now
            result = _is_exchange_open(self._TZ, self._OPEN, self._CLOSE)
        assert result is True

    def test_weekday_outside_hours_returns_false(self):
        with patch("stonks_cli.fetcher.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 1  # Tuesday
            mock_now.time.return_value = dtime(20, 0)
            mock_dt.now.return_value = mock_now
            result = _is_exchange_open(self._TZ, self._OPEN, self._CLOSE)
        assert result is False


class TestExchangeCalendarName:
    def test_crypto_returns_none(self):
        assert _exchange_calendar_name("BTC-USD") is None

    def test_us_ticker_returns_us_calendar(self):
        from stonks_cli.fetcher import _US_EXCHANGE

        assert _exchange_calendar_name("AAPL") == _US_EXCHANGE.calendar_name

    def test_known_suffix_returns_mic(self):
        result = _exchange_calendar_name("ASML.AS")
        assert result is not None  # Amsterdam -> XAMS

    def test_unknown_suffix_returns_none(self):
        assert _exchange_calendar_name("FOO.XX") is None


class TestFetchExtendedPricesSkipsEmptySeries:
    @patch(_CURRENT_SESSION, return_value="regular")
    @patch("stonks_cli.fetcher.yf.download")
    def test_skips_symbol_with_all_nan_series(
        self, mock_dl, _cs, fetcher: PriceFetcher
    ):
        idx = pd.DatetimeIndex([pd.Timestamp(_REGULAR_TS)])
        cols = pd.MultiIndex.from_product([["Close"], ["AAPL", "NVDA"]])
        df = pd.DataFrame([[float("nan"), 900.0]], columns=cols, index=idx)
        mock_dl.return_value = df

        result = fetcher.fetch_extended_prices(["AAPL", "NVDA"])
        assert "AAPL" not in result
        assert result["NVDA"][0] == pytest.approx(900.0)


class TestFetchForexRatesEmptySeries:
    @patch("stonks_cli.fetcher.yf.download")
    def test_skips_currency_with_all_nan_series(self, mock_dl, fetcher: PriceFetcher):
        idx = pd.to_datetime(["2026-03-10"])
        cols = pd.MultiIndex.from_product([["Close"], ["EURUSD=X", "GBPUSD=X"]])
        df = pd.DataFrame([[float("nan"), 1.27]], columns=cols, index=idx)
        mock_dl.return_value = df

        rates = fetcher.fetch_forex_rates(["EUR", "GBP"], base="USD")
        assert "EUR" not in rates
        assert rates["GBP"] == pytest.approx(1.27)


class TestIsTradingDay:
    _TZ = "America/New_York"

    def test_returns_true_when_calendar_says_session(self):
        with patch("stonks_cli.fetcher._load_calendar") as mock_cal:
            mock_cal.return_value.is_session.return_value = True
            assert _is_trading_day(self._TZ, calendar_name="XNYS") is True

    def test_returns_false_when_calendar_says_no_session(self):
        with patch("stonks_cli.fetcher._load_calendar") as mock_cal:
            mock_cal.return_value.is_session.return_value = False
            assert _is_trading_day(self._TZ, calendar_name="XNYS") is False

    def test_calendar_exception_falls_through_to_weekday_check(self):
        with patch("stonks_cli.fetcher._load_calendar", side_effect=LookupError):
            with patch("stonks_cli.fetcher.datetime") as mock_dt:
                mock_dt.now.return_value.weekday.return_value = 1  # Tuesday
                assert _is_trading_day(self._TZ, calendar_name="XNYS") is True

    def test_weekend_returns_false_without_calendar(self):
        with patch("stonks_cli.fetcher.datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = 6  # Sunday
            assert _is_trading_day(self._TZ) is False

    def test_weekday_returns_true_without_calendar(self):
        with patch("stonks_cli.fetcher.datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = 2  # Wednesday
            assert _is_trading_day(self._TZ) is True


class TestFetchPricesRuntimeError:
    @patch("stonks_cli.fetcher.yf.download", side_effect=RuntimeError("race"))
    def test_returns_empty_dict_on_runtime_error(self, _mock, fetcher: PriceFetcher):
        assert fetcher.fetch_prices(["AAPL"]) == {}


class TestFetchExtendedPricesRuntimeError:
    @patch("stonks_cli.fetcher.yf.download", side_effect=RuntimeError("race"))
    def test_returns_empty_dict_on_runtime_error(self, _mock, fetcher: PriceFetcher):
        assert fetcher.fetch_extended_prices(["AAPL"]) == {}

    @patch(
        "stonks_cli.fetcher.yf.download",
        side_effect=ValueError("cannot reindex on an axis with duplicate labels"),
    )
    def test_returns_empty_dict_on_pandas_concat_error(
        self, _mock, fetcher: PriceFetcher
    ):
        # yf.download() can raise ValueError (or TypeError) when tickers from
        # different exchanges return DataFrames with incompatible index types
        # (e.g. tz-aware daily forex vs tz-naive minute equity bars).
        assert fetcher.fetch_extended_prices(["AAPL", "GBPUSD=X"]) == {}


class TestFetchForexRatesRuntimeError:
    @patch("stonks_cli.fetcher.yf.download", side_effect=RuntimeError("race"))
    def test_returns_base_rate_on_runtime_error(self, _mock, fetcher: PriceFetcher):
        rates = fetcher.fetch_forex_rates(["EUR"], base="USD")
        assert rates == {"USD": 1.0}


class TestCurrentSession:
    def test_crypto_always_regular(self, fetcher: PriceFetcher):
        assert fetcher.current_session("BTC-USD") == "regular"

    @patch(_TRADING_DAY, return_value=False)
    def test_non_trading_day_returns_closed(self, _td, fetcher: PriceFetcher):
        assert fetcher.current_session("AAPL") == "closed"

    @patch(_TRADING_DAY, return_value=True)
    @patch("stonks_cli.fetcher._market_session", return_value="pre")
    def test_trading_day_delegates_to_market_session(
        self, mock_ms, _td, fetcher: PriceFetcher
    ):
        result = fetcher.current_session("AAPL")
        assert result == "pre"
        assert mock_ms.called


class TestFetchPriceSingle:
    def test_returns_price_on_success(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.fast_info.last_price = 175.5
            assert fetcher.fetch_price_single("AAPL") == pytest.approx(175.5)
            mock_ticker.assert_called_once_with("AAPL")

    def test_returns_none_for_nan_price(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.fast_info.last_price = float("nan")
            assert fetcher.fetch_price_single("AAPL") is None

    def test_returns_none_for_none_price(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.fast_info.last_price = None
            assert fetcher.fetch_price_single("AAPL") is None

    def test_returns_none_on_key_error(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker", side_effect=KeyError("no data")):
            assert fetcher.fetch_price_single("AAPL") is None

    def test_returns_none_on_type_error(self, fetcher: PriceFetcher):
        # yfinance raises TypeError("'NoneType' object is not subscriptable") when
        # the HTTP response is None (e.g. network failure for illiquid tickers)
        with patch("stonks_cli.fetcher.yf.Ticker", side_effect=TypeError("NoneType")):
            assert fetcher.fetch_price_single("AAPL") is None

    def test_uppercases_symbol(self, fetcher: PriceFetcher):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.fast_info.last_price = 100.0
            fetcher.fetch_price_single("aapl")
            mock_ticker.assert_called_once_with("AAPL")
