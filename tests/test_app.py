"""Tests for the Textual portfolio TUI."""

import pytest
from textual.widgets import DataTable

from stonks_cli.app import PortfolioApp
from stonks_cli.models import Portfolio, Position


@pytest.fixture
def portfolio() -> Portfolio:
    return Portfolio(
        positions=[
            Position(symbol="AAPL", quantity=100, avg_cost=150.0),
            Position(symbol="NVDA", quantity=200, avg_cost=112.0),
        ]
    )


@pytest.mark.asyncio
async def test_table_row_count(portfolio: Portfolio) -> None:
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolio=portfolio, prices=prices)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_symbols_appear_in_table(portfolio: Portfolio) -> None:
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolio=portfolio, prices=prices)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        cell_aapl = table.get_cell_at((0, 0))
        cell_nvda = table.get_cell_at((1, 0))
        assert str(cell_aapl) == "AAPL"
        assert str(cell_nvda) == "NVDA"


@pytest.mark.asyncio
async def test_profit_pnl_is_green(portfolio: Portfolio) -> None:
    """P&L cell must carry green style when position is in profit."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}  # AAPL profit, NVDA loss
    app = PortfolioApp(portfolio=portfolio, prices=prices)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        pnl_col = 5  # "Unrealized P&L" is the 6th column (index 5)
        pnl_cell = table.get_cell_at((0, pnl_col))  # AAPL row
        assert "green" in pnl_cell.style


@pytest.mark.asyncio
async def test_loss_pnl_is_red(portfolio: Portfolio) -> None:
    """P&L cell must carry red style when position is at a loss."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}  # NVDA at a loss (cost 112)
    app = PortfolioApp(portfolio=portfolio, prices=prices)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        pnl_col = 5
        pnl_cell = table.get_cell_at((1, pnl_col))  # NVDA row
        assert "red" in pnl_cell.style


@pytest.mark.asyncio
async def test_missing_price_shows_na(portfolio: Portfolio) -> None:
    """When a symbol has no price, all computed cells show N/A."""
    app = PortfolioApp(portfolio=portfolio, prices={})  # no prices at all

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 3))) == "N/A"  # Last Price
        assert str(table.get_cell_at((0, 4))) == "N/A"  # Mkt Value
        assert str(table.get_cell_at((0, 5))) == "N/A"  # P&L


@pytest.mark.asyncio
async def test_apply_prices_updates_table(portfolio: Portfolio) -> None:
    """Calling _apply_prices() with new data re-renders the table."""
    app = PortfolioApp(portfolio=portfolio, prices={"AAPL": 160.0, "NVDA": 90.0})

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_prices({"AAPL": 200.0, "NVDA": 50.0})
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 3))) == "200.00"  # AAPL last price updated


@pytest.mark.asyncio
async def test_default_refresh_interval(portfolio: Portfolio) -> None:
    app = PortfolioApp(portfolio=portfolio, prices={})
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.refresh_interval == 5.0


@pytest.mark.asyncio
async def test_custom_refresh_interval(portfolio: Portfolio) -> None:
    app = PortfolioApp(portfolio=portfolio, prices={}, refresh_interval=30.0)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.refresh_interval == 30.0
