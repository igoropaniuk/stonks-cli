"""Unit tests for TUI action mutation helpers."""

from stonks_cli import app_actions
from stonks_cli.market_session import Session
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem
from stonks_cli.portfolio_table import RowKind


def test_add_equity_sets_metadata_for_new_position() -> None:
    portfolio = Portfolio()

    app_actions.add_equity(
        {
            "symbol": "BTC-USD",
            "qty": 1.5,
            "avg_cost": 42000.0,
            "currency": "USD",
            "asset_type": "crypto",
            "external_id": "bitcoin",
        },
        portfolio,
    )

    pos = portfolio.get_position("BTC-USD")
    assert pos is not None
    assert pos.quantity == 1.5
    assert pos.asset_type == "crypto"
    assert pos.external_id == "bitcoin"


def test_add_equity_updates_metadata_for_existing_position() -> None:
    pos = Position(
        "BTC-USD", 1.0, 30000.0, currency="USD", asset_type=None, external_id=None
    )
    portfolio = Portfolio(positions=[pos])

    app_actions.add_equity(
        {
            "symbol": "BTC-USD",
            "qty": 0.5,
            "avg_cost": 40000.0,
            "currency": "EUR",  # should be ignored for existing position
            "asset_type": "crypto",
            "external_id": "bitcoin",
        },
        portfolio,
    )

    assert pos.quantity == 1.5
    assert pos.currency == "USD"  # unchanged
    assert pos.asset_type == "crypto"
    assert pos.external_id == "bitcoin"


def test_add_equity_returns_error_on_invalid_quantity() -> None:
    portfolio = Portfolio()

    err = app_actions.add_equity(
        {
            "symbol": "BTC-USD",
            "qty": -1.0,
            "avg_cost": 42000.0,
            "currency": "USD",
            "asset_type": "crypto",
            "external_id": "bitcoin",
        },
        portfolio,
    )

    assert err == "Quantity must be positive"
    assert portfolio.positions == []


def test_add_cash_returns_error_on_invalid_amount() -> None:
    portfolio = Portfolio()

    err = app_actions.add_cash({"currency": "USD", "amount": -10.0}, portfolio)

    assert err == "Amount must be positive"
    assert portfolio.cash == []


def test_add_watch_returns_duplicate_error() -> None:
    portfolio = Portfolio(watchlist=[WatchlistItem("TSLA")])

    err = app_actions.add_watch(
        {"symbol": "TSLA", "asset_type": None, "external_id": None}, portfolio
    )

    assert err == "TSLA is already in the watchlist"
    assert len(portfolio.watchlist) == 1


def test_add_watch_returns_error_on_invalid_symbol() -> None:
    portfolio = Portfolio()

    err = app_actions.add_watch(
        {"symbol": "", "asset_type": None, "external_id": None},
        portfolio,
    )

    assert err == "Symbol cannot be empty"
    assert portfolio.watchlist == []


def test_edit_cash_restores_original_position_on_failure() -> None:
    cash_pos = CashPosition("EUR", 1000.0)
    portfolio = Portfolio(cash=[cash_pos])

    err = app_actions.edit_cash(
        portfolio,
        cash_pos,
        {"currency": "EUR", "amount": -1.0},
    )

    assert err == "Amount must be positive"
    assert portfolio.get_cash("EUR") is cash_pos
    assert cash_pos.amount == 1000.0


def test_edit_watch_returns_duplicate_error() -> None:
    eth = WatchlistItem("ETH-USD")
    portfolio = Portfolio(watchlist=[WatchlistItem("BTC-USD"), eth])

    err = app_actions.edit_watch(
        portfolio,
        eth,
        {"symbol": "BTC-USD", "asset_type": "crypto", "external_id": "bitcoin"},
    )

    assert err == "BTC-USD is already in the watchlist"
    assert eth.symbol == "ETH-USD"


def test_edit_watch_returns_error_on_invalid_symbol() -> None:
    item = WatchlistItem("ETH-USD")
    portfolio = Portfolio(watchlist=[item])

    err = app_actions.edit_watch(
        portfolio,
        item,
        {"symbol": "", "asset_type": "crypto", "external_id": "ethereum"},
    )

    assert err == "Symbol cannot be empty"
    assert item.symbol == "ETH-USD"


def test_edit_position_returns_duplicate_error() -> None:
    btc = Position("BTC-USD", 1.0, 30000.0, asset_type="crypto")
    portfolio = Portfolio(
        positions=[
            btc,
            Position("ETH-USD", 2.0, 2000.0, asset_type="crypto"),
        ]
    )

    err = app_actions.edit_position(
        portfolio,
        btc,
        {
            "symbol": "ETH-USD",
            "qty": 1.0,
            "avg_cost": 30000.0,
            "currency": "USD",
            "asset_type": "crypto",
            "external_id": "ethereum",
        },
    )

    assert err == "ETH-USD already exists in this portfolio"
    assert btc.symbol == "BTC-USD"


def test_edit_position_returns_error_on_invalid_quantity() -> None:
    btc = Position("BTC-USD", 1.0, 30000.0, asset_type="crypto")
    portfolio = Portfolio(positions=[btc])

    err = app_actions.edit_position(
        portfolio,
        btc,
        {
            "symbol": "BTC-USD",
            "qty": 0.0,
            "avg_cost": 30000.0,
            "currency": "USD",
            "asset_type": "crypto",
            "external_id": "bitcoin",
        },
    )

    assert err == "Quantity must be positive"
    assert btc.quantity == 1.0


def test_remove_selected_item_removes_expected_kind() -> None:
    portfolio = Portfolio(
        positions=[Position("AAPL", 1, 100.0)],
        cash=[CashPosition("USD", 50.0)],
        watchlist=[WatchlistItem("MSFT")],
    )

    app_actions.remove_selected_item(portfolio, RowKind.CASH, "USD")
    app_actions.remove_selected_item(portfolio, RowKind.WATCHLIST, "MSFT")
    app_actions.remove_selected_item(portfolio, RowKind.POSITION, "AAPL")

    assert portfolio.cash == []
    assert portfolio.watchlist == []
    assert portfolio.positions == []


def test_watch_item_returns_matching_symbol() -> None:
    item = WatchlistItem("NVDA")
    portfolio = Portfolio(watchlist=[item])

    found = app_actions.watch_item(portfolio, "NVDA")

    assert found is item


def test_session_enum_still_behaves_like_string() -> None:
    assert Session.REGULAR == "regular"
