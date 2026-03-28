"""Tests for stonks_cli.models."""

import pytest

from stonks_cli.models import (
    CashPosition,
    Portfolio,
    Position,
    WatchlistItem,
    daily_change_pct,
    portfolio_total,
)


class TestCashPosition:
    def test_valid_creation(self):
        cash = CashPosition(currency="usd", amount=1000.0)
        assert cash.currency == "USD"
        assert cash.amount == 1000.0

    def test_currency_uppercase(self):
        assert CashPosition(currency="eur", amount=500.0).currency == "EUR"

    def test_empty_currency_raises(self):
        with pytest.raises(ValueError, match="Currency cannot be empty"):
            CashPosition(currency="", amount=100.0)

    def test_negative_amount_raises(self):
        with pytest.raises(ValueError, match="Amount must be positive"):
            CashPosition(currency="USD", amount=-1.0)

    def test_zero_amount_raises(self):
        with pytest.raises(ValueError, match="Amount must be positive"):
            CashPosition(currency="USD", amount=0.0)


class TestPosition:
    def test_valid_creation(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0, currency="USD")
        assert pos.symbol == "AAPL"
        assert pos.quantity == 100
        assert pos.avg_cost == 150.0
        assert pos.currency == "USD"

    def test_symbol_uppercase(self):
        pos = Position(symbol="aapl", quantity=100, avg_cost=150.0)
        assert pos.symbol == "AAPL"

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="Symbol cannot be empty"):
            Position(symbol="", quantity=100, avg_cost=150.0)

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError, match="Quantity must be positive"):
            Position(symbol="AAPL", quantity=-1, avg_cost=150.0)

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError, match="Quantity must be positive"):
            Position(symbol="AAPL", quantity=0, avg_cost=150.0)

    def test_negative_avg_cost_raises(self):
        with pytest.raises(ValueError, match="Average cost must be positive"):
            Position(symbol="AAPL", quantity=100, avg_cost=-1.0)

    def test_zero_avg_cost_raises(self):
        with pytest.raises(ValueError, match="Average cost must be positive"):
            Position(symbol="AAPL", quantity=100, avg_cost=0.0)

    def test_market_value(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.market_value(160.0) == 16000.0

    def test_unrealized_pnl_profit(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.unrealized_pnl(160.0) == 1000.0

    def test_unrealized_pnl_loss(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.unrealized_pnl(140.0) == -1000.0

    def test_asset_type_default_none(self):
        pos = Position(symbol="AAPL", quantity=10, avg_cost=150.0)
        assert pos.asset_type is None

    def test_asset_type_crypto(self):
        pos = Position(
            symbol="BTC-USD", quantity=0.25, avg_cost=50000.0, asset_type="crypto"
        )
        assert pos.asset_type == "crypto"

    def test_asset_type_lowercased(self):
        pos = Position(
            symbol="BTC-USD", quantity=1, avg_cost=100.0, asset_type="CRYPTO"
        )
        assert pos.asset_type == "crypto"

    def test_fractional_quantity(self):
        pos = Position(symbol="BTC-USD", quantity=0.25, avg_cost=50000.0)
        assert pos.quantity == 0.25
        assert pos.market_value(60000.0) == pytest.approx(15000.0)


class TestWatchlistItem:
    def test_valid_creation(self):
        item = WatchlistItem(symbol="TSLA")
        assert item.symbol == "TSLA"

    def test_symbol_uppercase(self):
        item = WatchlistItem(symbol="tsla")
        assert item.symbol == "TSLA"

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="Symbol cannot be empty"):
            WatchlistItem(symbol="")

    def test_asset_type_default_none(self):
        assert WatchlistItem(symbol="TSLA").asset_type is None

    def test_asset_type_crypto(self):
        item = WatchlistItem(symbol="BTC-USD", asset_type="crypto")
        assert item.asset_type == "crypto"

    def test_asset_type_lowercased(self):
        item = WatchlistItem(symbol="BTC-USD", asset_type="CRYPTO")
        assert item.asset_type == "crypto"


class TestPortfolio:
    @pytest.fixture
    def sample_positions(self):
        return [
            Position(symbol="AAPL", quantity=100, avg_cost=150.0),
            Position(symbol="GOOGL", quantity=50, avg_cost=2800.0),
        ]

    def test_valid_creation(self, sample_positions):
        portfolio = Portfolio(positions=sample_positions)
        assert len(portfolio.positions) == 2

    def test_empty_portfolio(self):
        assert Portfolio().positions == []

    def test_default_base_currency_is_usd(self):
        assert Portfolio().base_currency == "USD"

    def test_base_currency_normalised_to_uppercase(self):
        assert Portfolio(base_currency="eur").base_currency == "EUR"

    def test_default_name_is_none(self):
        assert Portfolio().name is None

    def test_name_preserved(self):
        assert Portfolio(name="Work").name == "Work"

    def test_duplicate_symbols_raises(self):
        positions = [
            Position(symbol="AAPL", quantity=100, avg_cost=150.0),
            Position(symbol="AAPL", quantity=50, avg_cost=160.0),
        ]
        with pytest.raises(ValueError, match="Duplicate symbols in portfolio"):
            Portfolio(positions=positions)

    def test_get_position_existing(self, sample_positions):
        pos = Portfolio(positions=sample_positions).get_position("AAPL")
        assert pos is not None
        assert pos.symbol == "AAPL"

    def test_get_position_case_insensitive(self, sample_positions):
        pos = Portfolio(positions=sample_positions).get_position("aapl")
        assert pos is not None
        assert pos.symbol == "AAPL"

    def test_get_position_missing(self, sample_positions):
        assert Portfolio(positions=sample_positions).get_position("MSFT") is None

    # --- add_position ---

    def test_add_new_position(self):
        p = Portfolio()
        p.add_position("AAPL", 100, 150.0)
        pos = p.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == 150.0

    def test_add_position_normalises_symbol(self):
        p = Portfolio()
        p.add_position("aapl", 10, 100.0)
        assert p.get_position("AAPL") is not None

    def test_add_position_averages_cost(self):
        p = Portfolio()
        p.add_position("AAPL", 100, 100.0)
        p.add_position("AAPL", 100, 200.0)
        pos = p.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 200
        assert pos.avg_cost == pytest.approx(150.0)

    # --- remove_position ---

    def test_remove_full_position(self):
        p = Portfolio()
        p.add_position("AAPL", 100, 150.0)
        p.remove_position("AAPL", 100)
        assert p.get_position("AAPL") is None

    def test_remove_partial_position(self):
        p = Portfolio()
        p.add_position("AAPL", 100, 150.0)
        p.remove_position("AAPL", 40)
        pos = p.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 60

    def test_remove_missing_symbol_raises(self):
        p = Portfolio()
        with pytest.raises(ValueError, match="not found"):
            p.remove_position("MSFT", 10)

    def test_remove_excess_quantity_raises(self):
        p = Portfolio()
        p.add_position("AAPL", 50, 150.0)
        with pytest.raises(ValueError, match="only 50 held"):
            p.remove_position("AAPL", 100)

    # --- cash ---

    def test_add_cash_new_currency(self):
        p = Portfolio()
        p.add_cash("USD", 1000.0)
        cash = p.get_cash("USD")
        assert cash is not None
        assert cash.amount == 1000.0

    def test_add_cash_accumulates(self):
        p = Portfolio()
        p.add_cash("EUR", 500.0)
        p.add_cash("EUR", 300.0)
        assert p.get_cash("EUR").amount == pytest.approx(800.0)

    def test_add_cash_normalises_currency(self):
        p = Portfolio()
        p.add_cash("eur", 100.0)
        assert p.get_cash("EUR") is not None

    def test_get_cash_missing_returns_none(self):
        assert Portfolio().get_cash("USD") is None

    def test_remove_cash_full(self):
        p = Portfolio()
        p.add_cash("USD", 1000.0)
        p.remove_cash("USD", 1000.0)
        assert p.get_cash("USD") is None

    def test_remove_cash_partial(self):
        p = Portfolio()
        p.add_cash("USD", 1000.0)
        p.remove_cash("USD", 400.0)
        assert p.get_cash("USD").amount == pytest.approx(600.0)

    def test_remove_cash_missing_raises(self):
        p = Portfolio()
        with pytest.raises(ValueError, match="No USD cash position"):
            p.remove_cash("USD", 100.0)

    def test_remove_cash_excess_raises(self):
        p = Portfolio()
        p.add_cash("USD", 500.0)
        with pytest.raises(ValueError, match="only 500.00 held"):
            p.remove_cash("USD", 1000.0)

    def test_duplicate_cash_currencies_raises(self):
        with pytest.raises(ValueError, match="Duplicate currencies"):
            Portfolio(cash=[CashPosition("USD", 100.0), CashPosition("USD", 200.0)])

    # --- watchlist ---

    def test_watchlist_default_empty(self):
        assert Portfolio().watchlist == []

    def test_watchlist_preserved(self):
        items = [WatchlistItem("TSLA"), WatchlistItem("NVDA")]
        p = Portfolio(watchlist=items)
        assert len(p.watchlist) == 2
        assert p.watchlist[0].symbol == "TSLA"

    def test_duplicate_watchlist_raises(self):
        items = [WatchlistItem("TSLA"), WatchlistItem("TSLA")]
        with pytest.raises(ValueError, match="Duplicate symbols in watchlist"):
            Portfolio(watchlist=items)


class TestDailyChangePct:
    def test_positive_change(self):
        assert daily_change_pct(110.0, 100.0, "regular") == pytest.approx(10.0)

    def test_negative_change(self):
        assert daily_change_pct(90.0, 100.0, "regular") == pytest.approx(-10.0)

    def test_none_when_prev_is_none(self):
        assert daily_change_pct(110.0, None, "regular") is None

    def test_none_when_prev_is_zero(self):
        assert daily_change_pct(110.0, 0.0, "regular") is None

    def test_none_when_session_closed(self):
        assert daily_change_pct(110.0, 100.0, "closed") is None

    def test_non_closed_sessions_compute(self):
        assert daily_change_pct(110.0, 100.0, "pre") == pytest.approx(10.0)
        assert daily_change_pct(110.0, 100.0, "post") == pytest.approx(10.0)


class TestPortfolioTotal:
    def test_positions_and_cash(self):
        p = Portfolio(
            positions=[Position("AAPL", 10, 150.0)],
            cash=[CashPosition("USD", 500.0)],
        )
        prices = {"AAPL": 200.0}
        rates = {"USD": 1.0}
        assert portfolio_total(p, prices, rates) == pytest.approx(2500.0)

    def test_none_when_price_missing(self):
        p = Portfolio(positions=[Position("AAPL", 10, 150.0)])
        assert portfolio_total(p, {}, {"USD": 1.0}) is None

    def test_none_when_position_rate_missing(self):
        p = Portfolio(positions=[Position("AAPL", 10, 150.0, currency="EUR")])
        assert portfolio_total(p, {"AAPL": 200.0}, {}) is None

    def test_none_when_cash_rate_missing(self):
        p = Portfolio(cash=[CashPosition("EUR", 1000.0)])
        assert portfolio_total(p, {}, {}) is None

    def test_empty_portfolio(self):
        assert portfolio_total(Portfolio(), {}, {}) == pytest.approx(0.0)

    def test_forex_conversion(self):
        p = Portfolio(
            positions=[Position("VOW3", 5, 100.0, currency="EUR")],
        )
        prices = {"VOW3": 120.0}
        rates = {"EUR": 1.1}  # EUR -> USD
        assert portfolio_total(p, prices, rates) == pytest.approx(5 * 120.0 * 1.1)
