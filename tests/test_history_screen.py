"""Tests for the portfolio history screen and its TOTAL-row entry point."""

from unittest.mock import patch

import pytest
from textual.widgets import DataTable, Label
from textual_plotext import PlotextPlot

from stonks_cli.app import PortfolioApp
from stonks_cli.chart import CandleChartScreen
from stonks_cli.history import (
    DEFAULT_HISTORY_RANGE,
    HISTORY_RANGES,
    PortfolioHistory,
)
from stonks_cli.history_screen import PortfolioHistoryScreen
from stonks_cli.models import Portfolio, Position

USD_RATES = {"USD": {"USD": 1.0}}


def _app() -> PortfolioApp:
    portfolio = Portfolio(
        name="Main", positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)]
    )
    return PortfolioApp(
        portfolios=[portfolio], prices={"AAPL": 160.0}, forex_rates=USD_RATES
    )


def _history() -> PortfolioHistory:
    return PortfolioHistory(
        dates=["2026-01-05", "2026-01-06", "2026-01-07"],
        values=[1500.0, 1550.0, 1600.0],
        cost=1500.0,
        skipped=["UST"],
    )


@pytest.mark.asyncio
async def test_chart_key_on_total_row_opens_history() -> None:
    app = _app()
    with patch.object(PortfolioHistoryScreen, "_fetch"):
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            table.focus()
            table.move_cursor(row=table.row_count - 1)
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, PortfolioHistoryScreen)


@pytest.mark.asyncio
async def test_chart_key_on_position_still_opens_candles() -> None:
    app = _app()
    with patch.object(CandleChartScreen, "_load_data"):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one(DataTable).focus()
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, CandleChartScreen)


@pytest.mark.asyncio
async def test_screen_renders_chart_summary_and_skipped() -> None:
    app = _app()
    screen = PortfolioHistoryScreen(app.portfolios[0])
    period = HISTORY_RANGES[DEFAULT_HISTORY_RANGE][1]
    with patch.object(PortfolioHistoryScreen, "_fetch"):
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            screen._apply_history(period, _history())
            await pilot.pause()
            assert len(screen.query(PlotextPlot)) == 1
            text = " ".join(str(lbl.content) for lbl in screen.query(Label))
            assert "Not included (no price history): UST" in text
            assert screen.query_one("#loading").display is False


@pytest.mark.asyncio
async def test_stale_result_for_other_range_is_ignored() -> None:
    app = _app()
    screen = PortfolioHistoryScreen(app.portfolios[0])
    with patch.object(PortfolioHistoryScreen, "_fetch"):
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            screen._apply_history("5y", _history())
            await pilot.pause()
            assert len(screen.query(PlotextPlot)) == 0


@pytest.mark.asyncio
async def test_plus_minus_change_range_within_bounds() -> None:
    app = _app()
    screen = PortfolioHistoryScreen(app.portfolios[0])
    with patch.object(PortfolioHistoryScreen, "_fetch") as fetch:
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            for _ in range(len(HISTORY_RANGES)):
                await pilot.press("minus")
            assert screen._range == len(HISTORY_RANGES) - 1
            fetch.assert_called_with(HISTORY_RANGES[-1][1])
            for _ in range(len(HISTORY_RANGES)):
                await pilot.press("plus")
            assert screen._range == 0
            fetch.assert_called_with(HISTORY_RANGES[0][1])


@pytest.mark.asyncio
async def test_error_is_shown() -> None:
    app = _app()
    screen = PortfolioHistoryScreen(app.portfolios[0])
    period = HISTORY_RANGES[DEFAULT_HISTORY_RANGE][1]
    with patch.object(PortfolioHistoryScreen, "_fetch"):
        async with app.run_test() as pilot:
            await app.push_screen(screen)
            await pilot.pause()
            screen._show_error(period, "boom")
            await pilot.pause()
            assert "Error: boom" in str(screen.query_one("#error-msg", Label).content)
