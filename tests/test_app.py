"""Tests for the Textual portfolio TUI."""

from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Label, Static

from stonks_cli.app import PortfolioApp
from stonks_cli.forms import (
    _CashFormScreen,
    _ConfirmScreen,
    _EquityFormScreen,
    _TypeSelectScreen,
    _WatchFormScreen,
)
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem

# Capture before autouse fixture in conftest.py replaces it with a lambda
_REAL_REFRESH_PRICES = PortfolioApp.__dict__["_refresh_prices"]

USD_RATES = {"USD": {"USD": 1.0}}

_COLS = (
    "Instrument",
    "Exchange",
    "Qty",
    "Avg Cost",
    "Last Price",
    "Daily Chg",
    "Mkt Value",
    "Unrealized P&L",
)
_COL_LAST = _COLS.index("Last Price")
_COL_CHG = _COLS.index("Daily Chg")
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
async def test_apply_snapshot_updates_table(portfolio: Portfolio) -> None:
    """Calling _apply_snapshot() with new data re-renders the table."""
    app = PortfolioApp(
        portfolios=[portfolio],
        prices={"AAPL": 160.0, "NVDA": 90.0},
        forex_rates=USD_RATES,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_snapshot(
            MarketSnapshot(prices={"AAPL": 200.0, "NVDA": 50.0}, forex_rates=USD_RATES)
        )
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, _COL_LAST))) == "200.00"


@pytest.mark.asyncio
async def test_default_refresh_interval(portfolio: Portfolio) -> None:
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.refresh_interval == 60.0


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
async def test_apply_snapshot_sets_all_fields(portfolio: Portfolio) -> None:
    """_apply_snapshot stores the full snapshot and triggers re-render."""
    app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._apply_snapshot(
            MarketSnapshot(
                prices={"AAPL": 170.0},
                forex_rates=USD_RATES,
                sessions={"AAPL": "pre"},
                exchange_codes={"AAPL": "NMS"},
            )
        )
        await pilot.pause()
        assert app._snap.sessions == {"AAPL": "pre"}
        assert app._snap.exchange_codes == {"AAPL": "NMS"}
        table = app.query_one(DataTable)
        assert "PRE" in str(table.get_cell_at((0, _COL_LAST)))


@pytest.mark.asyncio
async def test_apply_snapshot_replaces_snap_atomically(
    portfolio: Portfolio,
) -> None:
    """_apply_snapshot replaces the entire stored snapshot."""
    app = PortfolioApp(
        portfolios=[portfolio],
        prices={},
        forex_rates=USD_RATES,
        sessions={"AAPL": "post"},
    )
    app._snap.exchange_codes = {"AAPL": "NMS"}

    async with app.run_test() as pilot:
        await pilot.pause()
        new_snap = MarketSnapshot(
            prices={"AAPL": 160.0}, forex_rates=USD_RATES, sessions={"AAPL": "post"}
        )
        app._apply_snapshot(new_snap)
        await pilot.pause()
        assert app._snap is new_snap


@pytest.mark.asyncio
async def test_refresh_prices_applies_snapshot(portfolio: Portfolio) -> None:
    """_refresh_prices forwards the MarketSnapshot returned by build_market_snapshot."""
    snap = MarketSnapshot(
        prices={"AAPL": 160.0, "NVDA": 90.0},
        sessions={"AAPL": "regular", "NVDA": "regular"},
        exchange_codes={"AAPL": "NMS", "NVDA": "NMS"},
        forex_rates={"USD": {"USD": 1.0}},
        prev_closes={"AAPL": 155.0, "NVDA": 85.0},
    )

    with patch("stonks_cli.app.build_market_snapshot", return_value=snap) as mock_bms:
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

    mock_bms.assert_called_once_with([portfolio])
    assert app._snap.prices == {"AAPL": 160.0, "NVDA": 90.0}
    assert app._snap.sessions == {"AAPL": "regular", "NVDA": "regular"}
    assert app._snap.exchange_codes == {"AAPL": "NMS", "NVDA": "NMS"}
    assert app._snap.prev_closes == {"AAPL": 155.0, "NVDA": 85.0}


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
async def test_refresh_prices_missing_symbol_absent_from_prices(
    portfolio: Portfolio,
) -> None:
    """A symbol absent from the snapshot is absent from app.prices after refresh."""
    snap = MarketSnapshot(
        prices={"AAPL": 160.0},  # NVDA missing
        sessions={"AAPL": "regular"},
        exchange_codes={},
        forex_rates={"USD": {"USD": 1.0}},
        prev_closes={},
    )

    with patch("stonks_cli.app.build_market_snapshot", return_value=snap):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

    assert "NVDA" not in app._snap.prices


@pytest.mark.asyncio
async def test_refresh_prices_exception_shown_in_error_bar(
    portfolio: Portfolio,
) -> None:
    """An exception in build_market_snapshot is surfaced via the #error bar."""
    with patch(
        "stonks_cli.app.build_market_snapshot", side_effect=RuntimeError("network down")
    ):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

            err = app.query_one("#error", Static)
            assert err.has_class("visible")
            assert "network down" in str(err.render())


@pytest.mark.asyncio
async def test_refresh_prices_clears_error_bar_on_success(
    portfolio: Portfolio,
) -> None:
    """A successful refresh clears a previously shown error."""
    snap = MarketSnapshot(
        prices={"AAPL": 160.0, "NVDA": 90.0},
        sessions={},
        exchange_codes={},
        forex_rates={"USD": {"USD": 1.0}},
        prev_closes={},
    )

    with patch(
        "stonks_cli.app.build_market_snapshot", side_effect=RuntimeError("network down")
    ):
        app = PortfolioApp(portfolios=[portfolio], prices={}, forex_rates=USD_RATES)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
            _REAL_REFRESH_PRICES.__wrapped__(app)
            await pilot.pause()

            # Confirm error is shown, then run a successful refresh
            assert app.query_one("#error", Static).has_class("visible")

            with patch("stonks_cli.app.build_market_snapshot", return_value=snap):
                _REAL_REFRESH_PRICES.__wrapped__(app)
                await pilot.pause()

            assert not app.query_one("#error", Static).has_class("visible")


@pytest.mark.asyncio
async def test_sort_by_column_header(portfolio: Portfolio) -> None:
    """Clicking a column header sorts the table by that column."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        # Default order: AAPL, NVDA
        assert str(table.get_cell_at((0, 0))) == "AAPL"

        col_keys = list(table.columns.keys())
        col0_key = col_keys[0]
        col0_label = table.columns[col0_key].label

        # Sort by Instrument column (index 0) -- ascending
        table.post_message(
            DataTable.HeaderSelected(table, col0_key, 0, label=col0_label)
        )
        await pilot.pause()
        assert str(table.get_cell_at((0, 0))) == "AAPL"
        assert str(table.get_cell_at((1, 0))) == "NVDA"

        # Click same column again -- reverse to descending
        table.post_message(
            DataTable.HeaderSelected(table, col0_key, 0, label=col0_label)
        )
        await pilot.pause()
        assert str(table.get_cell_at((0, 0))) == "NVDA"
        assert str(table.get_cell_at((1, 0))) == "AAPL"


@pytest.mark.asyncio
async def test_sort_by_different_column(portfolio: Portfolio) -> None:
    """Switching sort to a different column resets direction to ascending."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        col_keys = list(table.columns.keys())

        # Sort by Mkt Value column (index 5) -- ascending
        # AAPL: 100*160=16000, NVDA: 200*90=18000
        mkt_key = col_keys[_COL_MKT]
        table.post_message(
            DataTable.HeaderSelected(
                table, mkt_key, _COL_MKT, label=table.columns[mkt_key].label
            )
        )
        await pilot.pause()
        assert str(table.get_cell_at((0, 0))) == "AAPL"  # 16000 < 18000

        # Now switch to Instrument column (index 0) -- should reset to ascending
        col0_key = col_keys[0]
        table.post_message(
            DataTable.HeaderSelected(
                table, col0_key, 0, label=table.columns[col0_key].label
            )
        )
        await pilot.pause()
        assert str(table.get_cell_at((0, 0))) == "AAPL"


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


# ------------------------------------------------------------------
# Helper method tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pname_with_named_portfolio() -> None:
    """_pname returns the portfolio name when set."""
    p = Portfolio(
        name="Work", positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)]
    )
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._pname(0) == "Work"


@pytest.mark.asyncio
async def test_pname_unnamed_portfolio() -> None:
    """_pname returns 'Portfolio N' when name is empty."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._pname(0) == "Portfolio 1"


@pytest.mark.asyncio
async def test_table_to_portfolio_index_single() -> None:
    """Single portfolio always returns index 0."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(portfolios=[p], prices={"AAPL": 100.0}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert app._table_to_portfolio_index(table) == 0


@pytest.mark.asyncio
async def test_table_to_portfolio_index_multi() -> None:
    """Multi-portfolio maps table id to correct index."""
    p1 = Portfolio(
        name="A", positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)]
    )
    p2 = Portfolio(
        name="B", positions=[Position(symbol="NVDA", quantity=1, avg_cost=100.0)]
    )
    app = PortfolioApp(
        portfolios=[p1, p2],
        prices={"AAPL": 100.0, "NVDA": 100.0},
        forex_rates=USD_RATES,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        t1 = app.query_one("#table-1", DataTable)
        assert app._table_to_portfolio_index(t1) == 1


@pytest.mark.asyncio
async def test_get_active_table_and_index_returns_focused(portfolio: Portfolio) -> None:
    """Returns the focused DataTable and its index."""
    prices = {"AAPL": 100.0, "NVDA": 100.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        result = app._get_active_table_and_index()
        assert result is not None
        assert result[0] is table
        assert result[1] == 0


@pytest.mark.asyncio
async def test_get_active_table_and_index_fallback(portfolio: Portfolio) -> None:
    """Falls back to the single DataTable when nothing is focused."""
    prices = {"AAPL": 100.0, "NVDA": 100.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Clear focus so focused is None (not a DataTable)
        app.set_focus(None)
        await pilot.pause()
        result = app._get_active_table_and_index()
        assert result is not None
        assert result[1] == 0


@pytest.mark.asyncio
async def test_save_calls_store() -> None:
    """_save delegates to the PortfolioStore."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    mock_store = MagicMock()
    app = PortfolioApp(
        portfolios=[p], prices={}, forex_rates=USD_RATES, stores=[mock_store]
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app._save(0)
        mock_store.save.assert_called_once_with(p)


@pytest.mark.asyncio
async def test_save_no_store_does_not_raise() -> None:
    """_save with no stores is a no-op."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=USD_RATES)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._save(0)  # must not raise


# ------------------------------------------------------------------
# Modal screen tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_type_select_screen_equity() -> None:
    """TypeSelectScreen dismisses with 'equity' when equity button is pressed."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: str | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_TypeSelectScreen(portfolio_name="Test"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        btn = app.screen.query_one("#equity", Button)
        btn.press()
        await pilot.pause()
    assert result == "equity"


@pytest.mark.asyncio
async def test_type_select_screen_cash() -> None:
    """TypeSelectScreen dismisses with 'cash' when cash button is pressed."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: str | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_TypeSelectScreen(portfolio_name="Test"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        btn = app.screen.query_one("#cash", Button)
        btn.press()
        await pilot.pause()
    assert result == "cash"


@pytest.mark.asyncio
async def test_type_select_screen_cancel() -> None:
    """TypeSelectScreen dismisses with None when cancel is pressed."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: str | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_TypeSelectScreen(portfolio_name="Test"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        btn = app.screen.query_one("#cancel", Button)
        btn.press()
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_type_select_screen_escape() -> None:
    """TypeSelectScreen dismisses with None on escape."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: str | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_TypeSelectScreen(), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_equity_form_submit() -> None:
    """EquityFormScreen returns dict with validated fields on submit."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(
                _EquityFormScreen(
                    title="Add", symbol="AAPL", qty="10", avg_cost="150.00"
                ),
                on_dismiss,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
    assert result == {
        "symbol": "AAPL",
        "qty": 10.0,
        "avg_cost": 150.0,
        "currency": "USD",
        "asset_type": None,
        "external_id": None,
    }


@pytest.mark.asyncio
async def test_equity_form_cancel() -> None:
    """EquityFormScreen returns None on cancel."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_EquityFormScreen(title="Add"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_equity_form_validation_empty_symbol() -> None:
    """EquityFormScreen shows error when symbol is empty."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _EquityFormScreen(title="Add", symbol="", qty="10", avg_cost="100"),
                lambda _: self.exit(),
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Symbol" in str(err.content)


@pytest.mark.asyncio
async def test_equity_form_validation_bad_qty() -> None:
    """EquityFormScreen shows error for non-integer quantity."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _EquityFormScreen(
                    title="Add", symbol="AAPL", qty="abc", avg_cost="100"
                ),
                lambda _: self.exit(),
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Quantity" in str(err.content)


@pytest.mark.asyncio
async def test_equity_form_validation_bad_avg_cost() -> None:
    """EquityFormScreen shows error for non-numeric avg cost."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _EquityFormScreen(title="Add", symbol="AAPL", qty="10", avg_cost="xyz"),
                lambda _: self.exit(),
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "cost" in str(err.content).lower()


@pytest.mark.asyncio
async def test_equity_form_escape() -> None:
    """EquityFormScreen returns None on escape."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_EquityFormScreen(title="Add"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_cash_form_submit() -> None:
    """CashFormScreen returns dict with validated fields on submit."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(
                _CashFormScreen(title="Add", currency="EUR", amount="5000"),
                on_dismiss,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
    assert result == {"currency": "EUR", "amount": 5000.0}


@pytest.mark.asyncio
async def test_cash_form_cancel() -> None:
    """CashFormScreen returns None on cancel."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_CashFormScreen(title="Add"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_cash_form_validation_empty_currency() -> None:
    """CashFormScreen shows error when currency is empty."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _CashFormScreen(title="Add", currency="", amount="100"),
                lambda _: self.exit(),
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Currency" in str(err.content)


@pytest.mark.asyncio
async def test_cash_form_validation_bad_amount() -> None:
    """CashFormScreen shows error for non-numeric amount."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _CashFormScreen(title="Add", currency="USD", amount="abc"),
                lambda _: self.exit(),
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Amount" in str(err.content)


@pytest.mark.asyncio
async def test_cash_form_escape() -> None:
    """CashFormScreen returns None on escape."""
    result = "sentinel"

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: dict | None) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_CashFormScreen(title="Add"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result is None


@pytest.mark.asyncio
async def test_confirm_screen_yes() -> None:
    """ConfirmScreen returns True when Remove is pressed."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: bool) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_ConfirmScreen("Delete?"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()
    assert result is True


@pytest.mark.asyncio
async def test_confirm_screen_no() -> None:
    """ConfirmScreen returns False when Cancel is pressed."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: bool) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_ConfirmScreen("Delete?"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#no", Button).press()
        await pilot.pause()
    assert result is False


@pytest.mark.asyncio
async def test_confirm_screen_escape() -> None:
    """ConfirmScreen returns False on escape."""
    result = None

    class TestApp(App):
        def on_mount(self) -> None:
            def on_dismiss(value: bool) -> None:
                nonlocal result
                result = value
                self.exit()

            self.push_screen(_ConfirmScreen("Delete?"), on_dismiss)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result is False


# ------------------------------------------------------------------
# Action integration tests (add / edit / remove)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_add_equity_new_position() -> None:
    """Pressing 'a' -> equity -> filling form adds a new position."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    mock_store = MagicMock()
    app = PortfolioApp(
        portfolios=[p], prices=prices, forex_rates=USD_RATES, stores=[mock_store]
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        # Trigger add action
        await pilot.press("a")
        await pilot.pause()

        # Type selector visible -- press equity
        app.screen.query_one("#equity", Button).press()
        await pilot.pause()

        # Fill the equity form
        symbol_input = app.screen.query_one("#symbol", Input)
        symbol_input.value = "NVDA"
        app.screen.query_one("#qty", Input).value = "5"
        app.screen.query_one("#avg_cost", Input).value = "800.00"
        app.screen.query_one("#currency", Input).value = "USD"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert p.get_position("NVDA") is not None
    assert p.get_position("NVDA").quantity == 5
    mock_store.save.assert_called()


@pytest.mark.asyncio
async def test_action_add_equity_existing_position_weighted_avg() -> None:
    """Adding shares to an existing position computes weighted avg cost."""
    p = Portfolio(
        name="Test",
        positions=[
            Position(symbol="AAPL", quantity=10, avg_cost=100.0, currency="EUR")
        ],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#equity", Button).press()
        await pilot.pause()

        app.screen.query_one("#symbol", Input).value = "AAPL"
        app.screen.query_one("#qty", Input).value = "10"
        app.screen.query_one("#avg_cost", Input).value = "200.00"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    pos = p.get_position("AAPL")
    assert pos is not None
    assert pos.quantity == 20
    assert pos.avg_cost == pytest.approx(150.0)
    # Currency must NOT be overwritten to USD
    assert pos.currency == "EUR"


@pytest.mark.asyncio
async def test_action_add_cash() -> None:
    """Pressing 'a' -> cash -> filling form adds a cash position."""
    p = Portfolio(
        name="Test", positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)]
    )
    prices = {"AAPL": 100.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#cash", Button).press()
        await pilot.pause()

        app.screen.query_one("#currency", Input).value = "EUR"
        app.screen.query_one("#amount", Input).value = "5000"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert p.get_cash("EUR") is not None
    assert p.get_cash("EUR").amount == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_action_edit_equity() -> None:
    """Pressing 'e' on an equity row opens pre-filled form and applies changes."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        # Form should be pre-filled; update qty
        app.screen.query_one("#qty", Input).value = "20"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert p.positions[0].quantity == 20


@pytest.mark.asyncio
async def test_action_edit_cash() -> None:
    """Pressing 'e' on a cash row opens pre-filled form and applies changes."""
    p = Portfolio(
        name="Test",
        positions=[],
        cash=[CashPosition(currency="EUR", amount=1000.0)],
    )
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        app.screen.query_one("#amount", Input).value = "2000"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert p.get_cash("EUR") is not None
    assert p.get_cash("EUR").amount == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_action_remove_equity() -> None:
    """Pressing 'r' then confirming removes the equity position."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    mock_store = MagicMock()
    app = PortfolioApp(
        portfolios=[p], prices=prices, forex_rates=USD_RATES, stores=[mock_store]
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        # Confirm removal
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

    assert len(p.positions) == 0
    mock_store.save.assert_called()


@pytest.mark.asyncio
async def test_action_remove_cancel() -> None:
    """Pressing 'r' then cancelling preserves the position."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        # Cancel removal
        app.screen.query_one("#no", Button).press()
        await pilot.pause()

    assert len(p.positions) == 1


@pytest.mark.asyncio
async def test_action_remove_cash() -> None:
    """Pressing 'r' on a cash row and confirming removes it."""
    p = Portfolio(
        name="Test",
        positions=[],
        cash=[CashPosition(currency="EUR", amount=1000.0)],
    )
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    mock_store = MagicMock()
    app = PortfolioApp(
        portfolios=[p], prices={}, forex_rates=forex_rates, stores=[mock_store]
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

    assert len(p.cash) == 0
    mock_store.save.assert_called()


@pytest.mark.asyncio
async def test_bindings_include_tab() -> None:
    """Tab binding is declared with show=True so it appears in the footer."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=USD_RATES)
    tab_binding = None
    for b in app.BINDINGS:
        if isinstance(b, Binding) and b.key == "tab":
            tab_binding = b
            break
    assert tab_binding is not None
    assert tab_binding.show is True


@pytest.mark.asyncio
async def test_equity_form_validation_zero_qty() -> None:
    """EquityFormScreen rejects qty <= 0."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _EquityFormScreen(title="Add", symbol="AAPL", qty="0", avg_cost="100"),
                lambda _: None,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Quantity" in str(err.content)


@pytest.mark.asyncio
async def test_equity_form_validation_zero_avg_cost() -> None:
    """EquityFormScreen rejects avg_cost <= 0."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _EquityFormScreen(title="Add", symbol="AAPL", qty="10", avg_cost="0"),
                lambda _: None,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "cost" in str(err.content).lower()


@pytest.mark.asyncio
async def test_cash_form_validation_zero_amount() -> None:
    """CashFormScreen rejects amount <= 0."""

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                _CashFormScreen(title="Add", currency="USD", amount="0"),
                lambda _: None,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        err = app.screen.query_one("#error", Label)
        assert "Amount" in str(err.content)


@pytest.mark.asyncio
async def test_get_active_table_returns_none_when_no_tables() -> None:
    """_get_active_table_and_index returns None when all widgets are gone."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=100.0)])
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        with patch.object(app, "query_one", side_effect=NoMatches):
            with patch.object(
                type(app), "focused", new_callable=lambda: property(lambda self: None)
            ):
                assert app._get_active_table_and_index() is None


@pytest.mark.asyncio
async def test_action_add_cancel_type_select() -> None:
    """Pressing 'a' then cancelling the type selector does nothing."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        # Cancel the type selector
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

    # Portfolio unchanged
    assert len(p.positions) == 1
    assert p.get_position("AAPL") is not None


@pytest.mark.asyncio
async def test_action_add_equity_cancel_form() -> None:
    """Pressing 'a' -> equity -> cancel does nothing."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#equity", Button).press()
        await pilot.pause()

        # Cancel the form
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

    assert len(p.positions) == 1


@pytest.mark.asyncio
async def test_action_add_cash_cancel_form() -> None:
    """Pressing 'a' -> cash -> cancel does nothing."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#cash", Button).press()
        await pilot.pause()

        # Cancel the form
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

    assert len(p.cash) == 0


@pytest.mark.asyncio
async def test_action_edit_equity_cancel() -> None:
    """Pressing 'e' then cancelling preserves the position."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

    assert p.positions[0].quantity == 10


@pytest.mark.asyncio
async def test_action_edit_cash_cancel() -> None:
    """Pressing 'e' on cash row then cancelling preserves the cash position."""
    p = Portfolio(
        name="Test",
        positions=[],
        cash=[CashPosition(currency="EUR", amount=1000.0)],
    )
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

    assert p.cash[0].amount == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_action_edit_equity_rename_symbol() -> None:
    """Editing a position to a new unused symbol renames it."""
    p = Portfolio(
        name="Test",
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        app.screen.query_one("#symbol", Input).value = "MSFT"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert p.positions[0].symbol == "MSFT"
    assert p.get_position("AAPL") is None


@pytest.mark.asyncio
async def test_action_edit_equity_rename_to_existing_blocked() -> None:
    """Editing a position symbol to one that already exists is a no-op."""
    p = Portfolio(
        name="Test",
        positions=[
            Position(symbol="AAPL", quantity=10, avg_cost=150.0),
            Position(symbol="NVDA", quantity=5, avg_cost=800.0),
        ],
    )
    prices = {"AAPL": 160.0, "NVDA": 850.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        # Try to rename AAPL -> NVDA (already exists)
        app.screen.query_one("#symbol", Input).value = "NVDA"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        # AAPL should still exist unchanged
        assert p.positions[0].symbol == "AAPL"
        assert p.positions[0].quantity == 10
        assert len(p.positions) == 2
        # Error bar should be visible with an informative message
        err = app.query_one("#error", Static)
        assert err.has_class("visible")
        assert "NVDA" in str(err.render())


@pytest.mark.asyncio
async def test_action_edit_cash_currency_conflict_blocked() -> None:
    """Editing cash to an existing currency shows an error; both positions stay."""
    p = Portfolio(
        name="Test",
        positions=[],
        cash=[
            CashPosition(currency="EUR", amount=1000.0),
            CashPosition(currency="USD", amount=500.0),
        ],
    )
    forex_rates = {"USD": {"USD": 1.0, "EUR": 1.1}}
    app = PortfolioApp(portfolios=[p], prices={}, forex_rates=forex_rates)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        # Cursor should be on EUR (first row)
        await pilot.press("e")
        await pilot.pause()

        # Attempt to change currency from EUR to USD (which already exists)
        app.screen.query_one("#currency", Input).value = "USD"
        app.screen.query_one("#amount", Input).value = "1000"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        # Both positions must remain unchanged
        eur = p.get_cash("EUR")
        assert eur is not None
        assert eur.amount == pytest.approx(1000.0)
        usd = p.get_cash("USD")
        assert usd is not None
        assert usd.amount == pytest.approx(500.0)
        # Error bar must be visible with an informative message
        err = app.query_one("#error", Static)
        assert err.has_class("visible")
        assert "USD" in str(err.render())


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchlist_rows_appear_in_table() -> None:
    """Watchlist items appear as rows in the table."""
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
        watchlist=[WatchlistItem("TSLA"), WatchlistItem("NVDA")],
    )
    prices = {"AAPL": 160.0, "TSLA": 250.0, "NVDA": 130.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        # 1 equity + 2 watchlist = 3 rows
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_watchlist_rows_have_dim_style() -> None:
    """Watchlist rows render with dim style."""
    portfolio = Portfolio(
        watchlist=[WatchlistItem("TSLA")],
    )
    prices = {"TSLA": 250.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        symbol_cell = table.get_cell_at((0, 0))
        assert "dim" in symbol_cell.style


@pytest.mark.asyncio
async def test_watchlist_rows_show_dashes_for_qty_and_cost() -> None:
    """Watchlist rows show '--' for Qty and Avg Cost columns."""
    portfolio = Portfolio(
        watchlist=[WatchlistItem("TSLA")],
    )
    prices = {"TSLA": 250.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, 2))) == "--"  # Qty
        assert str(table.get_cell_at((0, 3))) == "--"  # Avg Cost
        assert str(table.get_cell_at((0, _COL_MKT))) == "--"  # Mkt Value
        assert str(table.get_cell_at((0, _COL_PNL))) == "--"  # P&L


@pytest.mark.asyncio
async def test_watchlist_excluded_from_total() -> None:
    """Watchlist items are not included in the portfolio total."""
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=100, avg_cost=150.0)],
        watchlist=[WatchlistItem("TSLA")],
    )
    # AAPL: 100 * 160 = 16000
    prices = {"AAPL": 160.0, "TSLA": 250.0}
    app = PortfolioApp(portfolios=[portfolio], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        label = app.query_one("#total", Static)
        # Total should be 16,000 (AAPL only), not including TSLA
        assert "16,000.00" in str(label.content)


@pytest.mark.asyncio
async def test_add_watch_item_via_hotkey() -> None:
    """Pressing 'a' then selecting Watch adds a watchlist item."""
    p = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        # Click "Watch" button in type selector
        app.screen.query_one("#watch", Button).press()
        await pilot.pause()

        # Fill in the symbol
        app.screen.query_one("#symbol", Input).value = "TSLA"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert len(p.watchlist) == 1
    assert p.watchlist[0].symbol == "TSLA"


@pytest.mark.asyncio
async def test_edit_watch_item_via_hotkey() -> None:
    """Pressing 'e' on a watchlist row opens the watch form with one input."""
    p = Portfolio(
        watchlist=[WatchlistItem("TSLA")],
    )
    prices = {"TSLA": 250.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        # Should be a _WatchFormScreen with only a symbol input
        screen = app.screen
        assert isinstance(screen, _WatchFormScreen)
        screen.query_one("#symbol", Input).value = "NVDA"
        screen.query_one("#ok", Button).press()
        await pilot.pause()

    assert len(p.watchlist) == 1
    assert p.watchlist[0].symbol == "NVDA"


@pytest.mark.asyncio
async def test_remove_watch_item_via_hotkey() -> None:
    """Pressing 'r' on a watchlist row removes it after confirmation."""
    p = Portfolio(
        watchlist=[WatchlistItem("TSLA")],
    )
    prices = {"TSLA": 250.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("r")
        await pilot.pause()

        # Confirm removal
        app.screen.query_one("#yes", Button).press()
        await pilot.pause()

    assert len(p.watchlist) == 0


@pytest.mark.asyncio
async def test_type_selector_shows_watch_option() -> None:
    """The type selector includes a Watch button."""
    p = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        buttons = [b.id for b in app.screen.query(Button)]
        assert "watch" in buttons


# ---------------------------------------------------------------------------
# Error bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_bar_hidden_on_startup() -> None:
    """The #error widget starts hidden."""
    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    app = PortfolioApp(portfolios=[p], prices={"AAPL": 160.0}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        err = app.query_one("#error", Static)
        assert not err.has_class("visible")


@pytest.mark.asyncio
async def test_error_bar_shown_on_duplicate_watchlist_add() -> None:
    """Adding a duplicate watchlist symbol shows an error in the #error bar."""
    p = Portfolio(watchlist=[WatchlistItem("TSLA")])
    app = PortfolioApp(portfolios=[p], prices={"TSLA": 250.0}, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#watch", Button).press()
        await pilot.pause()

        # Submit the same symbol again
        app.screen.query_one("#symbol", Input).value = "TSLA"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        assert len(p.watchlist) == 1
        err = app.query_one("#error", Static)
        assert err.has_class("visible")
        assert "TSLA" in str(err.render())


@pytest.mark.asyncio
async def test_error_bar_shown_on_duplicate_watchlist_edit() -> None:
    """Renaming a watchlist item to an existing symbol shows an error."""
    p = Portfolio(watchlist=[WatchlistItem("TSLA"), WatchlistItem("META")])
    app = PortfolioApp(
        portfolios=[p],
        prices={"TSLA": 250.0, "META": 500.0},
        forex_rates=USD_RATES,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        app.screen.query_one("#symbol", Input).value = "META"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        assert p.watchlist[0].symbol == "TSLA"
        err = app.query_one("#error", Static)
        assert err.has_class("visible")
        assert "META" in str(err.render())


@pytest.mark.asyncio
async def test_error_bar_cleared_on_successful_edit() -> None:
    """A successful edit clears a previously shown error."""
    p = Portfolio(
        positions=[
            Position(symbol="AAPL", quantity=10, avg_cost=150.0),
            Position(symbol="NVDA", quantity=5, avg_cost=800.0),
        ]
    )
    prices = {"AAPL": 160.0, "NVDA": 850.0}
    app = PortfolioApp(portfolios=[p], prices=prices, forex_rates=USD_RATES)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()

        # First edit: trigger an error (rename AAPL -> NVDA)
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#symbol", Input).value = "NVDA"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        err = app.query_one("#error", Static)
        assert err.has_class("visible")

        # Second edit: valid rename clears the error
        await pilot.press("e")
        await pilot.pause()
        app.screen.query_one("#symbol", Input).value = "MSFT"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()

        err = app.query_one("#error", Static)
        assert not err.has_class("visible")


# ---------------------------------------------------------------------------
# Daily Change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_session_shows_cls_and_no_daily_chg() -> None:
    """Tickers with session 'closed' show CLS suffix and '--' daily change."""
    portfolio = Portfolio(
        positions=[Position(symbol="UI", quantity=5, avg_cost=500.0)],
    )
    prices = {"UI": 760.0}
    prev_closes = {"UI": 760.0}
    app = PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
        prev_closes=prev_closes,
        sessions={"UI": "closed"},
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        price_cell = str(table.get_cell_at((0, _COLS.index("Last Price"))))
        assert "CLS" in price_cell
        assert "760.00" in price_cell
        assert str(table.get_cell_at((0, _COL_CHG))) == "--"


@pytest.mark.asyncio
async def test_daily_chg_zero_is_green(portfolio: Portfolio) -> None:
    """Daily change cell is green and shows +0.00% for an unchanged price."""
    prices = {"AAPL": 150.0, "NVDA": 90.0}
    prev_closes = {"AAPL": 150.0, "NVDA": 85.0}
    app = PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
        prev_closes=prev_closes,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        chg_cell = table.get_cell_at((0, _COL_CHG))
        assert "green" in chg_cell.style
        assert str(chg_cell) == "+0.00%"


@pytest.mark.asyncio
async def test_daily_chg_positive_shown_green(portfolio: Portfolio) -> None:
    """Daily change cell is green for a positive percentage."""
    prices = {"AAPL": 160.0, "NVDA": 90.0}
    prev_closes = {"AAPL": 150.0, "NVDA": 85.0}
    app = PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
        prev_closes=prev_closes,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        chg_cell = table.get_cell_at((0, _COL_CHG))
        assert "green" in chg_cell.style
        assert "+" in str(chg_cell)
        assert "%" in str(chg_cell)


@pytest.mark.asyncio
async def test_daily_chg_negative_shown_red(portfolio: Portfolio) -> None:
    """Daily change cell is red for a negative percentage."""
    prices = {"AAPL": 140.0, "NVDA": 90.0}
    prev_closes = {"AAPL": 150.0, "NVDA": 100.0}
    app = PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
        prev_closes=prev_closes,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        chg_cell = table.get_cell_at((0, _COL_CHG))
        assert "red" in chg_cell.style
        assert "-" in str(chg_cell)


@pytest.mark.asyncio
async def test_daily_chg_missing_prev_close_shows_dash() -> None:
    """Daily change shows '--' when previous close is unavailable."""
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
    )
    prices = {"AAPL": 160.0}
    app = PortfolioApp(
        portfolios=[portfolio], prices=prices, forex_rates=USD_RATES, prev_closes={}
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert str(table.get_cell_at((0, _COL_CHG))) == "--"


@pytest.mark.asyncio
async def test_daily_chg_watchlist_dim_style() -> None:
    """Watchlist daily change cell uses dim style."""
    portfolio = Portfolio(watchlist=[WatchlistItem("TSLA")])
    prices = {"TSLA": 260.0}
    prev_closes = {"TSLA": 250.0}
    app = PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
        prev_closes=prev_closes,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        chg_cell = table.get_cell_at((0, _COL_CHG))
        assert "dim" in chg_cell.style
        assert "%" in str(chg_cell)
