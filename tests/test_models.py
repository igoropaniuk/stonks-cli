"""Tests for stonks_cli.models."""

import pytest

from stonks_cli.models import Portfolio, Position


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
