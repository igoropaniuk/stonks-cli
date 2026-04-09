"""Unit tests for the backtesting engine."""

from unittest.mock import patch

import pandas as pd
import pytest

from stonks_cli.backtest import (
    BacktestResult,
    _best_worst_year,
    _cagr,
    _max_drawdown,
    _sharpe,
    run_backtest,
)
from stonks_cli.dto import BacktestConfig
from stonks_cli.models import Portfolio, Position

# ---------------------------------------------------------------------------
# Helper: build a fake price DataFrame returned by yf.download
# ---------------------------------------------------------------------------


def _make_prices(
    dates: list[str],
    columns: dict[str, list[float]],
) -> pd.DataFrame:
    """Build a DataFrame that mimics ``yf.download(...)[\"Close\"]``."""
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame(columns, index=idx)


def _default_config(**overrides) -> BacktestConfig:
    defaults: BacktestConfig = {
        "benchmark": "SPY",
        "start_amount": 10000,
        "start_year": 2020,
        "end_year": 2022,
        "cashflows": 0,
        "rebalance": "none",
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _one_stock_portfolio(symbol: str = "AAPL") -> Portfolio:
    return Portfolio(positions=[Position(symbol, 10, 150.0)])


def _two_stock_portfolio() -> Portfolio:
    return Portfolio(
        positions=[
            Position("AAPL", 10, 150.0),  # 1500 cost
            Position("GOOG", 5, 100.0),  # 500 cost  -> 25% weight
        ]
    )


# ---------------------------------------------------------------------------
# _max_drawdown
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_no_drawdown(self):
        assert _max_drawdown([100, 110, 120, 130]) == 0.0

    def test_simple_drawdown(self):
        dd = _max_drawdown([100, 80, 90, 100])
        assert dd == pytest.approx(-20.0)

    def test_single_value(self):
        assert _max_drawdown([100]) == 0.0

    def test_empty(self):
        assert _max_drawdown([]) == 0.0

    def test_full_drawdown(self):
        dd = _max_drawdown([100, 50, 25])
        assert dd == pytest.approx(-75.0)

    def test_recovery_after_drawdown(self):
        dd = _max_drawdown([100, 60, 80, 50])
        # Peak 100 -> 50 = -50%
        assert dd == pytest.approx(-50.0)


# ---------------------------------------------------------------------------
# _cagr
# ---------------------------------------------------------------------------


class TestCAGR:
    def test_doubling_in_one_year(self):
        assert _cagr(100, 200, 1) == pytest.approx(100.0)

    def test_no_growth(self):
        assert _cagr(100, 100, 5) == pytest.approx(0.0)

    def test_negative_growth(self):
        result = _cagr(100, 50, 1)
        assert result == pytest.approx(-50.0)

    def test_zero_start(self):
        assert _cagr(0, 100, 1) == 0.0

    def test_zero_years(self):
        assert _cagr(100, 200, 0) == 0.0

    def test_multi_year(self):
        # 100 -> 121 in 2 years = 10% CAGR
        assert _cagr(100, 121, 2) == pytest.approx(10.0, abs=0.1)


# ---------------------------------------------------------------------------
# _sharpe
# ---------------------------------------------------------------------------


class TestSharpe:
    def test_single_return(self):
        assert _sharpe([10.0]) == 0.0

    def test_zero_std(self):
        assert _sharpe([5.0, 5.0, 5.0]) == 0.0

    def test_positive_sharpe(self):
        # High returns with low variance
        result = _sharpe([12.0, 14.0, 13.0, 15.0], risk_free=2.0)
        assert result > 0

    def test_negative_sharpe(self):
        # Returns below risk-free rate
        result = _sharpe([-5.0, -3.0, -4.0], risk_free=2.0)
        assert result < 0

    def test_empty(self):
        assert _sharpe([]) == 0.0


# ---------------------------------------------------------------------------
# _best_worst_year
# ---------------------------------------------------------------------------


class TestBestWorstYear:
    def test_basic(self):
        best, worst = _best_worst_year(["2020", "2021", "2022"], [10.0, -5.0, 20.0])
        assert "2022" in best
        assert "+20.0%" in best
        assert "2021" in worst
        assert "-5.0%" in worst

    def test_empty(self):
        assert _best_worst_year([], []) == ("N/A", "N/A")

    def test_single_year(self):
        best, worst = _best_worst_year(["2020"], [15.0])
        assert "2020" in best
        assert "2020" in worst


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------

# Dates spanning 3 years for integration-style tests
_DATES_3Y = pd.bdate_range("2020-01-02", "2022-12-30").strftime("%Y-%m-%d").tolist()


def _flat_prices(value: float, n: int) -> list[float]:
    return [value] * n


def _linear_prices(start: float, end: float, n: int) -> list[float]:
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


class TestRunBacktest:
    def test_empty_portfolio_raises(self):
        portfolio = Portfolio()
        with pytest.raises(ValueError, match="no equity positions"):
            run_backtest(portfolio, _default_config())

    @patch("stonks_cli.backtest.yf.download")
    def test_empty_data_raises(self, mock_download):
        mock_download.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="No historical data"):
            run_backtest(_one_stock_portfolio(), _default_config())

    @patch("stonks_cli.backtest.yf.download")
    def test_symbol_missing_from_start_year_raises(self, mock_download):
        """ETF introduced in 2021 should fail when backtest starts in 2020."""
        dates_late = (
            pd.bdate_range("2021-01-04", "2022-12-30").strftime("%Y-%m-%d").tolist()
        )
        n_late = len(dates_late)
        # AAPL has no data for 2020 -- only starts in 2021
        n_full = len(_DATES_3Y)
        spy_prices = _flat_prices(400, n_full)
        aapl_prices = [float("nan")] * (n_full - n_late) + _flat_prices(150, n_late)
        df = _make_prices(
            _DATES_3Y,
            {"AAPL": aapl_prices, "SPY": spy_prices},
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        with pytest.raises(ValueError, match="Quotes are not available.*AAPL.*2021"):
            run_backtest(_one_stock_portfolio(), _default_config())

    @patch("stonks_cli.backtest.yf.download")
    def test_all_symbols_present_from_start_passes(self, mock_download):
        """When all symbols have data from start year, no error is raised."""
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {"AAPL": _flat_prices(150, n), "SPY": _flat_prices(400, n)},
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)
        # Should not raise
        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert len(result.dates) > 0

    @patch("stonks_cli.backtest.yf.download")
    def test_benchmark_missing_from_start_year_raises(self, mock_download):
        """Benchmark that doesn't cover start year should also fail."""
        dates_late = (
            pd.bdate_range("2021-01-04", "2022-12-30").strftime("%Y-%m-%d").tolist()
        )
        n_late = len(dates_late)
        n_full = len(_DATES_3Y)
        aapl_prices = _flat_prices(150, n_full)
        spy_prices = [float("nan")] * (n_full - n_late) + _flat_prices(400, n_late)
        df = _make_prices(
            _DATES_3Y,
            {"AAPL": aapl_prices, "SPY": spy_prices},
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        with pytest.raises(ValueError, match="Quotes are not available.*SPY.*2021"):
            run_backtest(_one_stock_portfolio(), _default_config())

    @patch("stonks_cli.backtest.yf.download")
    def test_flat_prices_no_growth(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {"AAPL": _flat_prices(150, n), "SPY": _flat_prices(400, n)},
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert isinstance(result, BacktestResult)
        assert len(result.dates) == n
        # With flat prices, portfolio value should stay constant
        assert result.portfolio_values[0] == pytest.approx(10000, rel=0.01)
        assert result.portfolio_values[-1] == pytest.approx(10000, rel=0.01)
        assert result.portfolio_cagr == pytest.approx(0.0, abs=0.1)

    @patch("stonks_cli.backtest.yf.download")
    def test_growing_prices_positive_cagr(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _linear_prices(150, 300, n),
                "SPY": _linear_prices(400, 500, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert result.portfolio_cagr > 0
        assert result.portfolio_final > result.portfolio_values[0]

    @patch("stonks_cli.backtest.yf.download")
    def test_benchmark_tracked(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _flat_prices(150, n),
                "SPY": _linear_prices(400, 800, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert result.benchmark_final > result.benchmark_values[0]
        assert result.benchmark_cagr > 0

    @patch("stonks_cli.backtest.yf.download")
    def test_annual_returns_populated(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _linear_prices(150, 200, n),
                "SPY": _linear_prices(400, 500, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert len(result.annual_years) >= 2
        assert len(result.annual_portfolio_returns) == len(result.annual_years)
        assert len(result.annual_benchmark_returns) == len(result.annual_years)

    @patch("stonks_cli.backtest.yf.download")
    def test_max_drawdown_computed(self, mock_download):
        n = len(_DATES_3Y)
        # Price drops then recovers
        prices = _linear_prices(150, 75, n // 2) + _linear_prices(75, 150, n - n // 2)
        df = _make_prices(
            _DATES_3Y,
            {"AAPL": prices, "SPY": _flat_prices(400, n)},
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert result.portfolio_max_drawdown < 0

    @patch("stonks_cli.backtest.yf.download")
    def test_cashflows_increase_value(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _flat_prices(150, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        config_no_cash = _default_config(cashflows=0)
        result_no = run_backtest(_one_stock_portfolio(), config_no_cash)

        config_cash = _default_config(cashflows=5000)
        result_yes = run_backtest(_one_stock_portfolio(), config_cash)

        # With cashflows and flat prices, final value should be higher
        assert result_yes.portfolio_final > result_no.portfolio_final
        assert result_yes.total_contributions > result_no.total_contributions

    @patch("stonks_cli.backtest.yf.download")
    def test_rebalance_annual(self, mock_download):
        n = len(_DATES_3Y)
        # AAPL doubles, GOOG stays flat -> annual rebalance shifts weight
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _linear_prices(150, 300, n),
                "GOOG": _flat_prices(100, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        config_no = _default_config(rebalance="none")
        result_no = run_backtest(_two_stock_portfolio(), config_no)

        config_yes = _default_config(rebalance="annual")
        result_yes = run_backtest(_two_stock_portfolio(), config_yes)

        # Results should differ due to rebalancing
        assert result_no.portfolio_final != pytest.approx(
            result_yes.portfolio_final, rel=0.001
        )

    @patch("stonks_cli.backtest.yf.download")
    def test_rebalance_monthly(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _linear_prices(150, 300, n),
                "GOOG": _flat_prices(100, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        config_annual = _default_config(rebalance="annual")
        result_annual = run_backtest(_two_stock_portfolio(), config_annual)

        config_monthly = _default_config(rebalance="monthly")
        result_monthly = run_backtest(_two_stock_portfolio(), config_monthly)

        # Monthly rebalancing should produce different results than annual
        assert result_annual.portfolio_final != pytest.approx(
            result_monthly.portfolio_final, rel=0.001
        )

    @patch("stonks_cli.backtest.yf.download")
    def test_two_stock_weights(self, mock_download):
        """Portfolio weights are derived from cost basis."""
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _flat_prices(150, n),
                "GOOG": _flat_prices(100, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_two_stock_portfolio(), _default_config())
        # With flat prices, value should stay at start_amount
        assert result.portfolio_final == pytest.approx(10000, rel=0.01)

    @patch("stonks_cli.backtest.yf.download")
    def test_summary_stats_populated(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _linear_prices(150, 200, n),
                "SPY": _linear_prices(400, 500, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert result.portfolio_best_year != ""
        assert result.portfolio_worst_year != ""
        assert result.benchmark_best_year != ""
        assert result.benchmark_worst_year != ""
        assert isinstance(result.portfolio_sharpe, float)
        assert isinstance(result.benchmark_sharpe, float)

    @patch("stonks_cli.backtest.yf.download")
    def test_dates_match_values_length(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _flat_prices(150, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        result = run_backtest(_one_stock_portfolio(), _default_config())
        assert len(result.dates) == len(result.portfolio_values)
        assert len(result.dates) == len(result.benchmark_values)

    @patch("stonks_cli.backtest.yf.download")
    def test_total_contributions_with_cashflows(self, mock_download):
        n = len(_DATES_3Y)
        df = _make_prices(
            _DATES_3Y,
            {
                "AAPL": _flat_prices(150, n),
                "SPY": _flat_prices(400, n),
            },
        )
        mock_download.return_value = pd.concat({"Close": df}, axis=1)

        config = _default_config(cashflows=1000)
        result = run_backtest(_one_stock_portfolio(), config)
        # start_amount + cashflows for 2021 and 2022
        assert result.total_contributions == pytest.approx(12000)
