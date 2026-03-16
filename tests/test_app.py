"""Tests for the Textual portfolio TUI."""

from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from stonks_cli.app import PortfolioApp
from stonks_cli.models import CashPosition, Portfolio, Position

# Capture before autouse fixture in conftest.py replaces it with a lambda
_REAL_REFRESH_PRICES = PortfolioApp.__dict__["_refresh_prices"]

USD_RATES = {"USD": {"USD": 1.0}}

_COLS = (
    "Instrument",
    "Exchange",
    "Qty",
    "Avg Cost",
    "Last Price",
    "Mkt Value",
    "Unrealized P&L",
)
_COL_LAST = _COLS.index("Last Price")
_COL_MKT = _COLS.index("Mkt Value")
_COL_PNL = _COLS.index("Unrealized P&L")


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
        pnl_cell = table.get_cell_at((0, _COL_PNL))  # AAPL row
        assert "green" in pnl_cell.style


@pytest.mark.asyncio
async def test_loss_pnl_is_red(portfolio: Portfolio) -> None:
    """P&L cell must carry red style when position is at a loss."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}  # NVDA at a loss (cost 112)
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        pnl_cell = table.get_cell_at((1, _COL_PNL))  # NVDA row
        assert "red" in pnl_cell.style


@pytest.mark.asyncio
async def test_missing_price_shows_na(portfolio: Portfolio) -> None:
    """When a symbol has no price, all computed cells show N/A."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, _COL_LAST))) == "N/A"
        assert str(table.get_cell_at((0, _COL_MKT))) == "N/A"
        assert str(table.get_cell_at((0, _COL_PNL))) == "N/A"


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
        assert str(table.get_cell_at((0, _COL_LAST))) == "200.00"


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
    # AAPL: 100 * 160 = 16 000, NVDA: 200 * 90 = 18 000  ->  total = 34 000
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
    # ASML last price = 800 EUR, EUR/USD = 1.1  ->  total = 10 * 800 * 1.1 = 8 800
    prices = {"ASML": 800.0}
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "8,800.00" in str(label.content)


@pytest.mark.asyncio
async def test_status_bar_shows_obtaining_when_no_prices(portfolio: Portfolio) -> None:
    """Status bar shows 'Obtaining market data...' before any prices are loaded."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status", Static)
        assert "Obtaining market data" in str(status.content)


@pytest.mark.asyncio
async def test_status_bar_clears_after_prices_loaded(portfolio: Portfolio) -> None:
    """Status bar is empty once prices have been loaded."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status", Static)
        assert str(status.content).strip() == ""


@pytest.mark.asyncio
async def test_total_shows_na_when_any_price_missing(
    portfolio: Portfolio,
) -> None:
    """Total shows N/A when any equity position has no price yet."""
    prices = {"AAPL": 160.0}  # NVDA price is missing
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "N/A" in str(label.content)


@pytest.mark.asyncio
async def test_total_shows_na_when_equity_forex_rate_missing() -> None:
    """Total shows N/A when the forex rate for an equity's currency is missing."""
    portfolio = Portfolio(
        positions=[
            Position(symbol="ASML.AS", quantity=10, avg_cost=700.0, currency="EUR")
        ],
        base_currency="USD",
    )
    # EUR rate is absent from forex_rates
    forex_rates: dict[str, dict[str, float]] = {"USD": {"USD": 1.0}}
    prices = {"ASML.AS": 800.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        assert "N/A" in str(label.content)


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
        price_cell = table.get_cell_at((0, _COL_LAST))
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
        price_cell = table.get_cell_at((0, _COL_LAST))
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
        price_cell = str(table.get_cell_at((0, _COL_LAST)))
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


@pytest.mark.asyncio
async def test_closed_session_badge_shown(portfolio: Portfolio) -> None:
    """Price cell for a 'closed' session symbol contains 'CLS'."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    sessions = {"AAPL": "closed"}
    app = PortfolioApp(
        portfolios=[portfolio], prices=prices, forex_rates=USD_RATES, sessions=sessions
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        price_cell = table.get_cell_at((0, _COL_LAST))
        assert "CLS" in str(price_cell)


@pytest.mark.asyncio
async def test_cash_position_with_known_rate_shown() -> None:
    """Cash row with a known forex rate renders amount and market value."""
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
        cash=[CashPosition(currency="EUR", amount=5000.0)],
        base_currency="USD",
    )
    prices = {"AAPL": 160.0}
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        # Cash row follows the equity row (row index 1)
        assert str(table.get_cell_at((1, 0))) == "EUR"
        assert str(table.get_cell_at((1, 1))) == "Cash"
        # Market value = 5000 * 1.1 = 5500
        assert "5,500.00" in str(table.get_cell_at((1, _COL_MKT)))


@pytest.mark.asyncio
async def test_cash_position_without_rate_shows_na() -> None:
    """Cash row with no forex rate shows N/A for price and market value."""
    portfolio = Portfolio(
        positions=[],
        cash=[CashPosition(currency="JPY", amount=100000.0)],
        base_currency="USD",
    )
    forex_rates: dict[str, dict[str, float]] = {"USD": {"USD": 1.0}}
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 0))) == "JPY"
        assert str(table.get_cell_at((0, _COL_LAST))) == "N/A"
        assert str(table.get_cell_at((0, _COL_MKT))) == "N/A"


@pytest.mark.asyncio
async def test_apply_prices_with_all_optional_args(portfolio: Portfolio) -> None:
    """_apply_prices updates sessions and exchange_codes when provided."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_prices(
            {"AAPL": 170.0},
            forex_rates=USD_RATES,
            sessions={"AAPL": "pre"},
            exchange_codes={"AAPL": "NMS"},
        )
        await pilot.pause()
        assert app.sessions == {"AAPL": "pre"}
        assert app.exchange_codes == {"AAPL": "NMS"}
        table = app.query_one(DataTable)
        assert "PRE" in str(table.get_cell_at((0, _COL_LAST)))


@pytest.mark.asyncio
async def test_apply_prices_without_optional_args_preserves_state(
    portfolio: Portfolio,
) -> None:
    """_apply_prices called with only prices leaves existing sessions/codes intact."""
    app = PortfolioApp(
        portfolios=[portfolio],
        prices={},
        forex_rates=USD_RATES,
        sessions={"AAPL": "post"},
    )
    app.exchange_codes = {"AAPL": "NMS"}

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_prices({"AAPL": 160.0})
        await pilot.pause()
        # sessions and exchange_codes unchanged
        assert app.sessions == {"AAPL": "post"}
        assert app.exchange_codes == {"AAPL": "NMS"}


@pytest.mark.asyncio
async def test_refresh_prices_calls_fetcher_and_applies(portfolio: Portfolio) -> None:
    """_refresh_prices fetches prices and forwards them to _apply_prices."""
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_extended_prices.return_value = {
        "AAPL": (160.0, "regular"),
        "NVDA": (90.0, "regular"),
    }
    mock_fetcher.fetch_exchange_names.return_value = {"AAPL": "NMS", "NVDA": "NMS"}
    mock_fetcher.fetch_forex_rates.return_value = {"USD": 1.0}

    with patch("stonks_cli.app.PriceFetcher", return_value=mock_fetcher):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            # call_from_thread requires a real worker thread -- stub it to call directly
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()
    assert mock_fetcher.fetch_extended_prices.called
    assert app.prices == {"AAPL": 160.0, "NVDA": 90.0}
    assert app.sessions == {"AAPL": "regular", "NVDA": "regular"}
    assert app.exchange_codes == {"AAPL": "NMS", "NVDA": "NMS"}


@pytest.mark.asyncio
async def test_refresh_prices_tier2_fallback(portfolio: Portfolio) -> None:
    """Symbols missing from extended prices are fetched via daily batch fallback."""
    mock_fetcher = MagicMock()
    # Extended fetch only returns AAPL; NVDA is missing
    mock_fetcher.fetch_extended_prices.return_value = {"AAPL": (160.0, "regular")}
    mock_fetcher.fetch_prices.return_value = {"NVDA": 90.0}
    mock_fetcher.fetch_price_single.return_value = None
    mock_fetcher.current_session.return_value = "regular"
    mock_fetcher.fetch_exchange_names.return_value = {}
    mock_fetcher.fetch_forex_rates.return_value = {"USD": 1.0}

    with patch("stonks_cli.app.PriceFetcher", return_value=mock_fetcher):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

    assert mock_fetcher.fetch_prices.called
    assert app.prices.get("NVDA") == pytest.approx(90.0)
    assert app.sessions.get("NVDA") == "regular"


@pytest.mark.asyncio
async def test_refresh_prices_tier3_fallback(portfolio: Portfolio) -> None:
    """Symbols still missing after daily batch are fetched individually."""
    mock_fetcher = MagicMock()
    # Both tiers 1 and 2 return nothing for NVDA
    mock_fetcher.fetch_extended_prices.return_value = {"AAPL": (160.0, "regular")}
    mock_fetcher.fetch_prices.return_value = {}
    mock_fetcher.fetch_price_single.return_value = 88.0
    mock_fetcher.current_session.return_value = "pre"
    mock_fetcher.fetch_exchange_names.return_value = {}
    mock_fetcher.fetch_forex_rates.return_value = {"USD": 1.0}

    with patch("stonks_cli.app.PriceFetcher", return_value=mock_fetcher):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

    mock_fetcher.fetch_price_single.assert_called_once_with("NVDA")
    assert app.prices.get("NVDA") == pytest.approx(88.0)
    assert app.sessions.get("NVDA") == "pre"


@pytest.mark.asyncio
async def test_populate_tables_no_matches_is_silent(portfolio: Portfolio) -> None:
    """_populate_tables swallows NoMatches from the status widget gracefully."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        with patch.object(app, "query_one", side_effect=NoMatches):
            # Must not raise
            app._populate_tables()


@pytest.mark.asyncio
async def test_populate_single_no_matches_returns_early(portfolio: Portfolio) -> None:
    """_populate_single returns without error when widgets are gone."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        with patch.object(app, "query_one", side_effect=NoMatches):
            app._populate_single()  # must not raise


@pytest.mark.asyncio
async def test_refresh_prices_tier3_none_price_skipped(portfolio: Portfolio) -> None:
    """Tier-3 fetch returning None leaves the symbol absent from prices."""
    mock_fetcher = MagicMock()
    mock_fetcher.fetch_extended_prices.return_value = {"AAPL": (160.0, "regular")}
    mock_fetcher.fetch_prices.return_value = {}
    mock_fetcher.fetch_price_single.return_value = None  # NVDA unreachable
    mock_fetcher.fetch_exchange_names.return_value = {}
    mock_fetcher.fetch_forex_rates.return_value = {"USD": 1.0}

    with patch("stonks_cli.app.PriceFetcher", return_value=mock_fetcher):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

    assert "NVDA" not in app.prices


@pytest.mark.asyncio
async def test_populate_for_no_matches_returns_early() -> None:
    """_populate_for returns without error when widgets are gone."""
    p1 = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    p2 = Portfolio(positions=[Position(symbol="NVDA", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(
        portfolios=[p1, p2],
        prices={"AAPL": 100.0, "NVDA": 100.0},
        forex_rates=USD_RATES,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        with patch.object(app, "query_one", side_effect=NoMatches):
            app._populate_for(0, p1)  # must not raise
