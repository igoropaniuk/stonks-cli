"""Tests for form dialog screens in stonks_cli.forms."""

from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Checkbox, Input, Label

from stonks_cli.app import PortfolioApp
from stonks_cli.dto import BacktestConfig
from stonks_cli.forms import (
    _BacktestFormScreen,
    _CashFormScreen,
    _ConfirmScreen,
    _TypeSelectScreen,
    _validate_positive_float,
    _validate_required,
)
from stonks_cli.models import Portfolio, Position

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USD_RATES = {"USD": {"USD": 1.0}}


def _make_app() -> PortfolioApp:
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)]
    )
    return PortfolioApp(
        portfolios=[portfolio],
        prices={"AAPL": 160.0},
        forex_rates=USD_RATES,
    )


def _make_fake_button_event(button_id: str) -> MagicMock:
    """Create a fake Button.Pressed event with the given button id."""
    event = MagicMock()
    event.button = MagicMock()
    event.button.id = button_id
    return event


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidateRequired:
    def test_empty_string_updates_error_and_returns_false(self):
        err = MagicMock()
        result = _validate_required("", "Symbol", err)
        assert result is False
        err.update.assert_called_once_with("Symbol is required")

    def test_non_empty_returns_true(self):
        err = MagicMock()
        result = _validate_required("AAPL", "Symbol", err)
        assert result is True
        err.update.assert_not_called()


class TestValidatePositiveFloat:
    def test_valid_positive_number(self):
        err = MagicMock()
        result = _validate_positive_float("10.5", "Quantity", err)
        assert result == 10.5
        err.update.assert_not_called()

    def test_zero_updates_error(self):
        err = MagicMock()
        result = _validate_positive_float("0", "Quantity", err)
        assert result is None
        err.update.assert_called_once()

    def test_negative_updates_error(self):
        err = MagicMock()
        result = _validate_positive_float("-5.0", "Quantity", err)
        assert result is None
        err.update.assert_called_once()

    def test_non_numeric_updates_error(self):
        err = MagicMock()
        result = _validate_positive_float("abc", "Quantity", err)
        assert result is None
        err.update.assert_called_once_with("Quantity must be a positive number")

    def test_integer_string_valid(self):
        err = MagicMock()
        result = _validate_positive_float("100", "Amount", err)
        assert result == 100.0


# ---------------------------------------------------------------------------
# _BacktestFormScreen compose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_form_compose():
    """_BacktestFormScreen creates all expected input fields."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen()
        app.push_screen(screen)
        await pilot.pause()

        assert screen.query_one("#benchmark", Input).value == "SPY"
        assert screen.query_one("#start_amount", Input).value == "10000"
        assert screen.query_one("#start_year", Input).value == "2010"
        assert screen.query_one("#cashflows", Input).value == "0"
        assert screen.query_one("#skip_unavailable", Checkbox).value is True

        end_year = screen.query_one("#end_year", Input).value
        assert end_year != ""
        assert len(end_year) == 4 and end_year.isdigit()


@pytest.mark.asyncio
async def test_backtest_form_custom_initial_values():
    """_BacktestFormScreen accepts custom initial values."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(
            benchmark="QQQ",
            start_amount="50000",
            start_year="2015",
            end_year="2023",
            cashflows="5000",
        )
        app.push_screen(screen)
        await pilot.pause()

        assert screen.query_one("#benchmark", Input).value == "QQQ"
        assert screen.query_one("#start_amount", Input).value == "50000"
        assert screen.query_one("#start_year", Input).value == "2015"
        assert screen.query_one("#end_year", Input).value == "2023"
        assert screen.query_one("#cashflows", Input).value == "5000"


# ---------------------------------------------------------------------------
# _BacktestFormScreen cancel / escape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_form_cancel_dismisses_with_none():
    """Cancel button (via on_button_pressed) dismisses with None."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        # Trigger cancel via on_button_pressed directly
        screen.on_button_pressed(_make_fake_button_event("cancel"))
        await pilot.pause()

        assert dismissed_values == [None]


@pytest.mark.asyncio
async def test_backtest_form_escape_dismisses_with_none():
    """Pressing Escape dismisses with None (on_key handler)."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert dismissed_values == [None]


# ---------------------------------------------------------------------------
# _BacktestFormScreen submit - valid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_form_submit_valid_data():
    """Submitting valid data via _submit() produces a BacktestConfig."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="2015", end_year="2023")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen._submit()
        await pilot.pause()

        assert len(dismissed_values) == 1
        config = dismissed_values[0]
        assert isinstance(config, dict)
        assert config["benchmark"] == "SPY"
        assert config["start_amount"] == 10000.0
        assert config["start_year"] == 2015
        assert config["end_year"] == 2023
        assert config["cashflows"] == 0.0
        assert config["rebalance"] == "none"
        assert isinstance(config["skip_unavailable"], bool)


# ---------------------------------------------------------------------------
# _BacktestFormScreen submit - validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_form_empty_benchmark_shows_error():
    """Empty benchmark field shows an error without dismissing."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen.query_one("#benchmark", Input).value = ""
        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert str(err.content) != ""
        assert dismissed_values == []


@pytest.mark.asyncio
async def test_backtest_form_invalid_start_amount_shows_error():
    """Non-numeric start amount shows an error without dismissing."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen.query_one("#start_amount", Input).value = "notanumber"
        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert str(err.content) != ""
        assert dismissed_values == []


@pytest.mark.asyncio
async def test_backtest_form_invalid_year_range_shows_error():
    """Start year after end year shows a year error."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="2025", end_year="2015")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert "year" in str(err.content).lower()
        assert dismissed_values == []


@pytest.mark.asyncio
async def test_backtest_form_negative_cashflows_shows_error():
    """Negative cashflows show an error."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="2015", end_year="2023")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen.query_one("#cashflows", Input).value = "-100"
        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert "cashflow" in str(err.content).lower()
        assert dismissed_values == []


@pytest.mark.asyncio
async def test_backtest_form_year_before_1970_shows_error():
    """Start year before 1970 shows a year error."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="1960", end_year="2023")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert "year" in str(err.content).lower()
        assert dismissed_values == []


@pytest.mark.asyncio
async def test_backtest_form_non_numeric_year_shows_error():
    """Non-numeric start year shows a year error."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="abc", end_year="2023")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen._submit()
        await pilot.pause()

        err = screen.query_one("#error", Label)
        assert "year" in str(err.content).lower()
        assert dismissed_values == []


# ---------------------------------------------------------------------------
# _BacktestFormScreen -- enter key on focused Input triggers submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_form_enter_on_input_triggers_submit():
    """Pressing Enter while an Input is focused calls _submit."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _BacktestFormScreen(start_year="2015", end_year="2023")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        # Focus the benchmark input and press enter
        await pilot.click("#benchmark")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        # Valid form should be dismissed
        assert len(dismissed_values) == 1


# ---------------------------------------------------------------------------
# _TypeSelectScreen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_type_select_equity_dismisses_with_equity():
    """Clicking equity dismisses with 'equity'."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _TypeSelectScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.click("#equity")
        await pilot.pause()

        assert dismissed_values == ["equity"]


@pytest.mark.asyncio
async def test_type_select_cash_dismisses_with_cash():
    """Clicking cash dismisses with 'cash'."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _TypeSelectScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.click("#cash")
        await pilot.pause()

        assert dismissed_values == ["cash"]


@pytest.mark.asyncio
async def test_type_select_cancel_dismisses_with_none():
    """Clicking cancel dismisses with None."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _TypeSelectScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.click("#cancel")
        await pilot.pause()

        assert dismissed_values == [None]


@pytest.mark.asyncio
async def test_type_select_escape_dismisses_with_none():
    """Pressing escape dismisses with None."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _TypeSelectScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert dismissed_values == [None]


@pytest.mark.asyncio
async def test_type_select_portfolio_name_shown():
    """Portfolio name is shown in the dialog when provided."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = _TypeSelectScreen(portfolio_name="My Fund")
        app.push_screen(screen)
        await pilot.pause()

        labels = screen.query(Label)
        texts = [str(lb.content) for lb in labels]
        assert any("My Fund" in t for t in texts)


# ---------------------------------------------------------------------------
# _ConfirmScreen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_screen_yes_dismisses_true():
    """Clicking Remove dismisses with True."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _ConfirmScreen("Delete this position?")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.click("#yes")
        await pilot.pause()

        assert dismissed_values == [True]


@pytest.mark.asyncio
async def test_confirm_screen_no_dismisses_false():
    """Clicking Cancel dismisses with False."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _ConfirmScreen("Delete this position?")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.click("#no")
        await pilot.pause()

        assert dismissed_values == [False]


@pytest.mark.asyncio
async def test_confirm_screen_escape_dismisses_false():
    """Pressing escape dismisses with False."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _ConfirmScreen("Are you sure?")
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert dismissed_values == [False]


# ---------------------------------------------------------------------------
# _CashFormScreen -- enter key on input triggers submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_form_enter_on_input_triggers_submit():
    """Pressing Enter while an Input is focused triggers _submit."""
    app = _make_app()
    dismissed_values: list = []

    async with app.run_test() as pilot:
        screen = _CashFormScreen()
        app.push_screen(screen, callback=dismissed_values.append)
        await pilot.pause()

        screen.query_one("#currency", Input).value = "EUR"
        screen.query_one("#amount", Input).value = "1000"

        # Focus the currency input and press Enter
        await pilot.click("#currency")
        await pilot.press("enter")
        await pilot.pause()

        assert len(dismissed_values) == 1
        assert dismissed_values[0]["currency"] == "EUR"
        assert dismissed_values[0]["amount"] == 1000.0


# ---------------------------------------------------------------------------
# ScrollableScreenMixin via BacktestScreen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrollable_mixin_actions():
    """ScrollableScreenMixin scroll action methods delegate to VerticalScroll."""
    from stonks_cli.backtest_detail import BacktestScreen
    from stonks_cli.models import Portfolio, Position

    portfolio = Portfolio(positions=[Position("AAPL", 10, 150.0)])
    config: BacktestConfig = {
        "benchmark": "SPY",
        "start_amount": 10000,
        "start_year": 2020,
        "end_year": 2022,
        "cashflows": 0,
        "rebalance": "none",
        "skip_unavailable": False,
    }
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(portfolio, config)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        scroll_widget = screen.query_one("#bt-scroll")
        scroll_widget.scroll_up = MagicMock()
        scroll_widget.scroll_down = MagicMock()
        scroll_widget.scroll_page_up = MagicMock()
        scroll_widget.scroll_page_down = MagicMock()

        screen.action_scroll_up()
        screen.action_scroll_down()
        screen.action_page_up()
        screen.action_page_down()

        scroll_widget.scroll_up.assert_called_once()
        scroll_widget.scroll_down.assert_called_once()
        scroll_widget.scroll_page_up.assert_called_once()
        scroll_widget.scroll_page_down.assert_called_once()
