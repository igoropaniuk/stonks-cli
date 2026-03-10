"""Tests for stonks_cli.main CLI commands."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from stonks_cli.main import main
from stonks_cli.storage import PortfolioStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def portfolio_file(tmp_path: Path) -> Path:
    return tmp_path / "portfolio.yaml"


def invoke(runner: CliRunner, portfolio_file: Path, *args: str):
    return runner.invoke(main, ["--portfolio", str(portfolio_file), *args])


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestAdd:
    def test_adds_new_position(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        assert result.exit_code == 0
        assert "AAPL" in result.output

        portfolio = PortfolioStore(path=portfolio_file).load()
        pos = portfolio.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == pytest.approx(150.0)

    def test_add_normalises_symbol_to_uppercase(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "add", "aapl", "10", "150.0")
        assert result.exit_code == 0
        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_position("AAPL") is not None

    def test_add_twice_averages_cost(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "100.0")
        invoke(runner, portfolio_file, "add", "AAPL", "100", "200.0")

        portfolio = PortfolioStore(path=portfolio_file).load()
        pos = portfolio.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 200
        assert pos.avg_cost == pytest.approx(150.0)

    def test_output_shows_updated_position(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "100.0")
        result = invoke(runner, portfolio_file, "add", "AAPL", "100", "200.0")
        assert "200 shares" in result.output
        assert "150.00" in result.output  # new avg cost

    def test_creates_portfolio_file_if_missing(self, runner, portfolio_file):
        assert not portfolio_file.exists()
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        assert portfolio_file.exists()


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_removes_full_position(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        result = invoke(runner, portfolio_file, "remove", "AAPL", "100")
        assert result.exit_code == 0

        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_position("AAPL") is None

    def test_removes_partial_position(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        invoke(runner, portfolio_file, "remove", "AAPL", "40")

        portfolio = PortfolioStore(path=portfolio_file).load()
        pos = portfolio.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 60

    def test_error_on_unknown_symbol(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "remove", "MSFT", "10")
        assert result.exit_code != 0
        assert "MSFT" in result.output

    def test_error_on_excess_quantity(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "50", "150.0")
        result = invoke(runner, portfolio_file, "remove", "AAPL", "100")
        assert result.exit_code != 0
        assert "50 held" in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_empty_portfolio_message(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "show")
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    @patch("stonks_cli.main.PriceFetcher")
    def test_show_renders_table(self, mock_fetcher_cls, runner, portfolio_file):
        mock_fetcher_cls.return_value.fetch_prices.return_value = {"AAPL": 160.0}
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        result = invoke(runner, portfolio_file, "show")
        assert result.exit_code == 0
        assert "AAPL" in result.output
        assert "160.00" in result.output  # last price
        assert "16,000.00" in result.output  # mkt value (100 × 160)
        assert "+1,000.00" in result.output  # P&L (100 × 10)

    @patch("stonks_cli.main.PriceFetcher")
    def test_show_handles_missing_price(self, mock_fetcher_cls, runner, portfolio_file):
        mock_fetcher_cls.return_value.fetch_prices.return_value = {}
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        result = invoke(runner, portfolio_file, "show")
        assert result.exit_code == 0
        assert "AAPL" in result.output
        assert "N/A" in result.output

    @patch("stonks_cli.main.PriceFetcher")
    def test_show_multiple_positions(self, mock_fetcher_cls, runner, portfolio_file):
        mock_fetcher_cls.return_value.fetch_prices.return_value = {
            "AAPL": 160.0,
            "NVDA": 900.0,
        }
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        invoke(runner, portfolio_file, "add", "NVDA", "10", "800.0")

        result = invoke(runner, portfolio_file, "show")
        assert "AAPL" in result.output
        assert "NVDA" in result.output
