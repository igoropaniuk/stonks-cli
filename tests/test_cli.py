"""Tests for stonks_cli.main CLI commands."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from stonks_cli import __version__
from stonks_cli.main import main
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import CashPosition, Portfolio, Position
from stonks_cli.show import format_show_table
from stonks_cli.storage import PORTFOLIO_CONFIG_DIR, PortfolioStore


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
        assert "200.0 shares" in result.output
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
        assert "50.0 held" in result.output


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_empty_portfolio_message(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "dashboard")
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_launches_app(self, mock_app_cls, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        result = invoke(runner, portfolio_file, "dashboard")

        assert result.exit_code == 0
        mock_app_cls.assert_called_once()
        mock_app_cls.return_value.run.assert_called_once()

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_passes_portfolio_to_app(
        self, mock_app_cls, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        invoke(runner, portfolio_file, "add", "NVDA", "10", "800.0")

        invoke(runner, portfolio_file, "dashboard")

        _, kwargs = mock_app_cls.call_args
        symbols = [p.symbol for p in kwargs["portfolios"][0].positions]
        assert "AAPL" in symbols
        assert "NVDA" in symbols

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_starts_with_empty_prices(
        self, mock_app_cls, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        invoke(runner, portfolio_file, "dashboard")

        _, kwargs = mock_app_cls.call_args
        assert kwargs["prices"] == {}
        assert kwargs["forex_rates"] == {}
        assert len(kwargs["portfolios"]) == 1

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_default_refresh_interval(
        self, mock_app_cls, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        invoke(runner, portfolio_file, "dashboard")

        _, kwargs = mock_app_cls.call_args
        assert kwargs["refresh_interval"] == 60.0

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_custom_refresh_interval(
        self, mock_app_cls, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")

        invoke(runner, portfolio_file, "dashboard", "--refresh", "10")

        _, kwargs = mock_app_cls.call_args
        assert kwargs["refresh_interval"] == 10.0

    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_shows_with_only_cash(self, mock_app_cls, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "USD", "5000")

        result = invoke(runner, portfolio_file, "dashboard")

        assert result.exit_code == 0
        mock_app_cls.assert_called_once()


# ---------------------------------------------------------------------------
# add-cash
# ---------------------------------------------------------------------------


class TestAddCash:
    def test_adds_new_cash_position(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "add-cash", "USD", "5000")
        assert result.exit_code == 0
        assert "USD" in result.output

        portfolio = PortfolioStore(path=portfolio_file).load()
        cash = portfolio.get_cash("USD")
        assert cash is not None
        assert cash.amount == pytest.approx(5000.0)

    def test_add_cash_normalises_currency(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "eur", "1000")
        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_cash("EUR") is not None

    def test_add_cash_twice_accumulates(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "USD", "3000")
        invoke(runner, portfolio_file, "add-cash", "USD", "2000")
        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_cash("USD").amount == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# remove-cash
# ---------------------------------------------------------------------------


class TestRemoveCash:
    def test_removes_full_cash_position(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "USD", "1000")
        result = invoke(runner, portfolio_file, "remove-cash", "USD", "1000")
        assert result.exit_code == 0
        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_cash("USD") is None

    def test_removes_partial_cash(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "EUR", "2000")
        invoke(runner, portfolio_file, "remove-cash", "EUR", "500")
        portfolio = PortfolioStore(path=portfolio_file).load()
        assert portfolio.get_cash("EUR").amount == pytest.approx(1500.0)

    def test_error_on_missing_currency(self, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "remove-cash", "USD", "100")
        assert result.exit_code != 0

    def test_error_on_excess_amount(self, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "USD", "500")
        result = invoke(runner, portfolio_file, "remove-cash", "USD", "1000")
        assert result.exit_code != 0
        assert "only 500.00 held" in result.output


# ---------------------------------------------------------------------------
# portfolio name resolution
# ---------------------------------------------------------------------------


class TestResolvePortfolioPath:
    def test_none_returns_none(self):
        assert PortfolioStore.resolve_path(None) is None

    def test_plain_name_resolves_to_config_dir(self):
        result = PortfolioStore.resolve_path("work")
        assert result == PORTFOLIO_CONFIG_DIR / "work.yaml"

    def test_name_with_extension_used_as_is(self):
        assert PortfolioStore.resolve_path("work.yaml") == Path("work.yaml")

    def test_path_with_separator_used_as_is(self):
        assert PortfolioStore.resolve_path("/tmp/my.yaml") == Path("/tmp/my.yaml")

    def test_relative_path_used_as_is(self):
        assert PortfolioStore.resolve_path("subdir/work") == Path("subdir/work")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_lists_yaml_files(self, runner, tmp_path):
        (tmp_path / "personal.yaml").write_text("")
        (tmp_path / "work.yaml").write_text("")
        with patch("stonks_cli.main.PORTFOLIO_CONFIG_DIR", tmp_path):
            result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "personal" in result.output
        assert "work" in result.output

    def test_no_portfolios_message(self, runner, tmp_path):
        with patch("stonks_cli.main.PORTFOLIO_CONFIG_DIR", tmp_path):
            result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "No portfolios found" in result.output

    def test_missing_config_dir(self, runner, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch("stonks_cli.main.PORTFOLIO_CONFIG_DIR", missing):
            result = runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "No portfolios found" in result.output

    def test_non_yaml_files_excluded(self, runner, tmp_path):
        (tmp_path / "notes.txt").write_text("")
        (tmp_path / "work.yaml").write_text("")
        with patch("stonks_cli.main.PORTFOLIO_CONFIG_DIR", tmp_path):
            result = runner.invoke(main, ["list"])
        assert "notes" not in result.output
        assert "work" in result.output


class TestVersion:
    @pytest.mark.parametrize("flag", ["--version", "-V"])
    def test_prints_version(self, runner, flag):
        result = runner.invoke(main, [flag])
        assert result.exit_code == 0
        assert __version__ in result.output


# ---------------------------------------------------------------------------
# seed sample portfolio (no --portfolio flag, empty config dir)
# ---------------------------------------------------------------------------


class TestSeedSamplePortfolio:
    @patch("stonks_cli.main.PortfolioApp")
    def test_creates_sample_portfolio_and_prints_message(
        self, mock_app_cls, runner, tmp_path
    ):
        # No YAML files in config dir -> seed_sample_portfolio() returns True
        with (
            patch("stonks_cli.main.PORTFOLIO_CONFIG_DIR", tmp_path),
            patch("stonks_cli.storage.PORTFOLIO_CONFIG_DIR", tmp_path),
            patch(
                "stonks_cli.storage.DEFAULT_PORTFOLIO_PATH", tmp_path / "portfolio.yaml"
            ),
        ):
            result = runner.invoke(main, [])

        assert result.exit_code == 0
        assert "sample portfolio" in result.output.lower()


# ---------------------------------------------------------------------------
# --log-level option
# ---------------------------------------------------------------------------


class TestLogLevel:
    def test_default_log_level_is_warning(self, runner, portfolio_file):
        with patch("stonks_cli.main.setup_logging") as mock_setup:
            invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        import logging

        mock_setup.assert_called_once_with(level=logging.WARNING)

    def test_debug_log_level_forwarded(self, runner, portfolio_file):
        with patch("stonks_cli.main.setup_logging") as mock_setup:
            runner.invoke(
                main,
                [
                    "--portfolio",
                    str(portfolio_file),
                    "--log-level",
                    "DEBUG",
                    "add",
                    "AAPL",
                    "10",
                    "150.0",
                ],
            )
        import logging

        mock_setup.assert_called_once_with(level=logging.DEBUG)

    def test_invalid_log_level_rejected(self, runner, portfolio_file):
        result = runner.invoke(
            main,
            [
                "--portfolio",
                str(portfolio_file),
                "--log-level",
                "VERBOSE",
                "add",
                "AAPL",
                "10",
                "150.0",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# dashboard with multiple portfolios
# ---------------------------------------------------------------------------


class TestDashboardMultiplePortfolios:
    @patch("stonks_cli.main.PortfolioApp")
    def test_dashboard_shows_separate_portfolios(self, mock_app_cls, runner, tmp_path):
        p1 = tmp_path / "p1.yaml"
        p2 = tmp_path / "p2.yaml"
        runner.invoke(main, ["--portfolio", str(p1), "add", "AAPL", "10", "150"])
        runner.invoke(main, ["--portfolio", str(p2), "add", "NVDA", "5", "800"])

        result = runner.invoke(
            main, ["--portfolio", str(p1), "--portfolio", str(p2), "dashboard"]
        )

        assert result.exit_code == 0
        _, kwargs = mock_app_cls.call_args
        portfolios = kwargs["portfolios"]
        assert len(portfolios) == 2
        all_symbols = [p.symbol for port in portfolios for p in port.positions]
        assert "AAPL" in all_symbols
        assert "NVDA" in all_symbols


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _mock_snapshot(
    prices, sessions=None, exchange_codes=None, forex_rates=None, prev_closes=None
):
    """Return a MarketSnapshot for use as a build_market_snapshot mock return value."""
    return MarketSnapshot(
        prices=prices or {},
        sessions=sessions or {},
        exchange_codes=exchange_codes or {},
        forex_rates=forex_rates or {},
        prev_closes=prev_closes or {},
    )


class TestShow:
    @patch("stonks_cli.main.build_market_snapshot")
    def test_shows_positions_with_prices(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "100", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 175.0},
            sessions={"AAPL": "regular"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "AAPL" in result.output
        assert "175.00" in result.output
        assert "17,500.00" in result.output  # market value
        assert "+2,500.00" in result.output  # unrealized P&L

    @patch("stonks_cli.main.build_market_snapshot")
    def test_empty_portfolio_message(self, mock_fetch, runner, portfolio_file):
        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "empty" in result.output.lower()
        mock_fetch.assert_not_called()

    @patch("stonks_cli.main.build_market_snapshot")
    def test_missing_price_shows_na(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "50", "100.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "N/A" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_cash_only_portfolio(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add-cash", "USD", "5000")
        mock_fetch.return_value = _mock_snapshot(
            prices={},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "USD" in result.output
        assert "Cash" in result.output
        assert "5,000.00" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_negative_pnl(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "200.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 150.0},
            sessions={"AAPL": "regular"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "-500.00" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_session_badge_pre(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 155.0},
            sessions={"AAPL": "pre"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert "PRE" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_session_badge_post(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 155.0},
            sessions={"AAPL": "post"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert "AH" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_session_badge_closed(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 155.0},
            sessions={"AAPL": "closed"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        assert "CLS" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_multi_portfolio(self, mock_fetch, runner, tmp_path):
        p1 = tmp_path / "p1.yaml"
        p2 = tmp_path / "p2.yaml"
        runner.invoke(main, ["--portfolio", str(p1), "add", "AAPL", "10", "150"])
        runner.invoke(main, ["--portfolio", str(p2), "add", "NVDA", "5", "800"])
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 175.0, "NVDA": 950.0},
            sessions={"AAPL": "regular", "NVDA": "regular"},
            exchange_codes={"AAPL": "NMS", "NVDA": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = runner.invoke(
            main, ["--portfolio", str(p1), "--portfolio", str(p2), "show"]
        )

        assert result.exit_code == 0
        assert "Portfolio 1" in result.output
        assert "Portfolio 2" in result.output
        assert "AAPL" in result.output
        assert "NVDA" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_total_shows_na_when_price_missing(
        self, mock_fetch, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={},
            forex_rates={"USD": {"USD": 1.0}},
        )

        result = invoke(runner, portfolio_file, "show")

        # Total line should contain N/A.
        lines = result.output.strip().split("\n")
        total_line = [ln for ln in lines if "Total" in ln]
        assert total_line
        assert "N/A" in total_line[0]

    @patch("stonks_cli.main.build_market_snapshot")
    def test_daily_change_shown(self, mock_fetch, runner, portfolio_file):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 110.0},
            sessions={"AAPL": "regular"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
            prev_closes={"AAPL": 100.0},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "+10.00%" in result.output

    @patch("stonks_cli.main.build_market_snapshot")
    def test_daily_change_suppressed_for_closed_session(
        self, mock_fetch, runner, portfolio_file
    ):
        invoke(runner, portfolio_file, "add", "AAPL", "10", "150.0")
        mock_fetch.return_value = _mock_snapshot(
            prices={"AAPL": 110.0},
            sessions={"AAPL": "closed"},
            exchange_codes={"AAPL": "NMS"},
            forex_rates={"USD": {"USD": 1.0}},
            prev_closes={"AAPL": 100.0},
        )

        result = invoke(runner, portfolio_file, "show")

        assert result.exit_code == 0
        assert "+10.00%" not in result.output


# ---------------------------------------------------------------------------
# format_show_table unit tests
# ---------------------------------------------------------------------------


class TestFormatShowTable:
    def test_columns_are_aligned(self):
        portfolio = Portfolio(
            positions=[Position("AAPL", 100, 150.0)],
            base_currency="USD",
        )
        table = format_show_table(
            portfolio,
            _mock_snapshot(
                prices={"AAPL": 175.0},
                sessions={"AAPL": "regular"},
                exchange_codes={"AAPL": "NMS"},
                forex_rates={"USD": {"USD": 1.0}},
            ),
        )
        lines = table.split("\n")
        # All data lines should have the same length (padded).
        header_len = len(lines[0])
        total_lines = [ln for ln in lines if ln.startswith("Total")]
        assert total_lines, "Expected a Total line in the table output"
        for line in lines[1:]:
            if line.startswith("-"):
                assert len(line) == header_len
                continue
            if line.startswith("Total"):
                break
            assert len(line) == header_len

    def test_total_computed_correctly(self):
        portfolio = Portfolio(
            positions=[
                Position("AAPL", 10, 100.0),
                Position("NVDA", 5, 200.0),
            ],
            cash=[CashPosition("USD", 1000.0)],
            base_currency="USD",
        )
        table = format_show_table(
            portfolio,
            _mock_snapshot(
                prices={"AAPL": 150.0, "NVDA": 300.0},
                forex_rates={"USD": {"USD": 1.0}},
            ),
        )
        # AAPL: 10*150=1500, NVDA: 5*300=1500, cash: 1000 -> total: 4000
        assert "4,000.00" in table

    def test_cash_row_pnl_is_dashes(self):
        portfolio = Portfolio(
            cash=[CashPosition("EUR", 2000.0)],
            base_currency="USD",
        )
        table = format_show_table(
            portfolio,
            _mock_snapshot(
                prices={},
                forex_rates={"USD": {"EUR": 1.08}},
            ),
        )
        assert "--" in table
        assert "Cash" in table

    def test_daily_chg_column_present_and_computed(self):
        portfolio = Portfolio(
            positions=[Position("AAPL", 10, 100.0)],
            base_currency="USD",
        )
        table = format_show_table(
            portfolio,
            _mock_snapshot(
                prices={"AAPL": 110.0},
                sessions={"AAPL": "regular"},
                forex_rates={"USD": {"USD": 1.0}},
                prev_closes={"AAPL": 100.0},
            ),
        )
        assert "Daily Chg" in table
        assert "+10.00%" in table

    def test_daily_chg_suppressed_for_closed_session(self):
        portfolio = Portfolio(
            positions=[Position("AAPL", 10, 100.0)],
            base_currency="USD",
        )
        table = format_show_table(
            portfolio,
            _mock_snapshot(
                prices={"AAPL": 110.0},
                sessions={"AAPL": "closed"},
                forex_rates={"USD": {"USD": 1.0}},
                prev_closes={"AAPL": 100.0},
            ),
        )
        assert "+10.00%" not in table
        assert "--" in table
