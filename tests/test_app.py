"""Tests for the Textual portfolio TUI."""

import pytest
from textual.widgets import DataTable, Static

from stonks_cli.app import PortfolioApp
from stonks_cli.models import Portfolio, Position

USD_RATES = {"USD": {"USD": 1.0}}


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
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_symbols_appear_in_table(portfolio: Portfolio) -> None:
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 0))) == "AAPL"
        assert str(table.get_cell_at((1, 0))) == "NVDA"


@pytest.mark.asyncio
async def test_profit_pnl_is_green(portfolio: Portfolio) -> None:
    """P&L cell must carry green style when position is in profit."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}  # AAPL profit, NVDA loss
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        pnl_col = 6  # "Unrealized P&L" is the 7th column (index 6)
        pnl_cell = table.get_cell_at((0, pnl_col))  # AAPL row
        assert "green" in pnl_cell.style


@pytest.mark.asyncio
async def test_loss_pnl_is_red(portfolio: Portfolio) -> None:
    """P&L cell must carry red style when position is at a loss."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}  # NVDA at a loss (cost 112)
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        pnl_cell = table.get_cell_at((1, 6))  # NVDA row, P&L col
        assert "red" in pnl_cell.style


@pytest.mark.asyncio
async def test_missing_price_shows_na(portfolio: Portfolio) -> None:
    """When a symbol has no price, all computed cells show N/A."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 4))) == "N/A"  # Last Price
        assert str(table.get_cell_at((0, 5))) == "N/A"  # Mkt Value
        assert str(table.get_cell_at((0, 6))) == "N/A"  # P&L


@pytest.mark.asyncio
async def test_apply_prices_updates_table(portfolio: Portfolio) -> None:
    """Calling _apply_prices() with new data re-renders the table."""
    app = PortfolioApp(
        portfolios=[portfolio],
        prices={"AAPL": 160.0, "NVDA": 90.0},
        forex_rates=USD_RATES,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_prices({"AAPL": 200.0, "NVDA": 50.0})
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 4))) == "200.00"  # AAPL last price updated


@pytest.mark.asyncio
async def test_default_refresh_interval(portfolio: Portfolio) -> None:
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.refresh_interval == 5.0


@pytest.mark.asyncio
async def test_custom_refresh_interval(portfolio: Portfolio) -> None:
    app = PortfolioApp(
        portfolios=[portfolio], prices={}, forex_rates=USD_RATES, refresh_interval=30.0
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.refresh_interval == 30.0


@pytest.mark.asyncio
async def test_total_usd_single_currency(portfolio: Portfolio) -> None:
    """Total reflects sum of all position market values (all USD)."""
    # AAPL: 100 × 160 = 16 000, NVDA: 200 × 90 = 18 000  →  total = 34 000
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "34,000.00" in str(label.content)


@pytest.mark.asyncio
async def test_total_converts_foreign_currency() -> None:
    """Market value of a EUR position is converted to USD in the total."""
    portfolio = Portfolio(
        positions=[
            Position(symbol="ASML", quantity=10, avg_cost=700.0, currency="EUR"),
        ]
    )
    # ASML last price = 800 EUR, EUR/USD = 1.1  →  total = 10 × 800 × 1.1 = 8 800
    prices = {"ASML": 800.0}
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "8,800.00" in str(label.content)


@pytest.mark.asyncio
async def test_total_excludes_positions_with_missing_price(
    portfolio: Portfolio,
) -> None:
    """Positions with no price are excluded from the total."""
    # Only AAPL has a price: 100 × 160 = 16 000
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "16,000.00" in str(label.content)


@pytest.mark.asyncio
async def test_pre_market_badge_shown(portfolio: Portfolio) -> None:
    """Price cell for a 'pre' session symbol contains 'PRE'."""
    prices = {"AAPL": 158.0, "NVDA": 90.0}
    sessions = {"AAPL": "pre"}
    app = PortfolioApp(
        portfolios=[portfolio], prices=prices, forex_rates=USD_RATES, sessions=sessions
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        price_cell = table.get_cell_at((0, 4))  # AAPL Last Price
        assert "PRE" in str(price_cell)


@pytest.mark.asyncio
async def test_after_hours_badge_shown(portfolio: Portfolio) -> None:
    """Price cell for a 'post' session symbol contains 'AH'."""
    prices = {"AAPL": 162.0, "NVDA": 90.0}
    sessions = {"AAPL": "post"}
    app = PortfolioApp(
        portfolios=[portfolio], prices=prices, forex_rates=USD_RATES, sessions=sessions
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        price_cell = table.get_cell_at((0, 4))  # AAPL Last Price
        assert "AH" in str(price_cell)


@pytest.mark.asyncio
async def test_regular_session_no_badge(portfolio: Portfolio) -> None:
    """Price cell for 'regular' session is a plain number with no badge."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    sessions = {"AAPL": "regular"}
    app = PortfolioApp(
        portfolios=[portfolio], prices=prices, forex_rates=USD_RATES, sessions=sessions
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        price_cell = str(table.get_cell_at((0, 4)))  # AAPL Last Price
        assert price_cell == "160.00"
        assert "PRE" not in price_cell
        assert "AH" not in price_cell


@pytest.mark.asyncio
async def test_multiple_portfolios_separate_tables() -> None:
    """Two portfolios render in separate tables."""
    p1 = Portfolio(
        name="Work",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    p2 = Portfolio(
        name="Personal",
        positions=[Position(symbol="NVDA", quantity=5, avg_cost=800.0)],
    )
    prices = {"AAPL": 160.0, "NVDA": 850.0}
    app = PortfolioApp(portfolios=[p1, p2], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        t0 = app.query_one("#table-0", DataTable)
        t1 = app.query_one("#table-1", DataTable)
        assert t0.row_count == 1
        assert t1.row_count == 1
        assert str(t0.get_cell_at((0, 0))) == "AAPL"
        assert str(t1.get_cell_at((0, 0))) == "NVDA"
