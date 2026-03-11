"""Tests for stonks_cli.storage."""

from pathlib import Path

import pytest
import yaml

from stonks_cli.models import CashPosition, Portfolio, Position
from stonks_cli.storage import PortfolioStore


def make_store(tmp_path: Path) -> PortfolioStore:
    return PortfolioStore(path=tmp_path / "portfolio.yaml")


def write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.dump(data, fh)


class TestLoad:
    def test_returns_empty_portfolio_when_file_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = store.load()
        assert portfolio.positions == []

    def test_loads_positions_from_file(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {
                "portfolio": {
                    "positions": [
                        {
                            "symbol": "QCOM",
                            "quantity": 150,
                            "avg_cost": 148.25,
                            "currency": "USD",
                        },
                        {
                            "symbol": "NVDA",
                            "quantity": 200,
                            "avg_cost": 112.00,
                            "currency": "USD",
                        },
                    ]
                }
            },
        )
        portfolio = store.load()
        assert len(portfolio.positions) == 2
        qcom = portfolio.get_position("QCOM")
        assert qcom is not None
        assert qcom.quantity == 150
        assert qcom.avg_cost == 148.25

    def test_defaults_currency_to_usd_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {
                "portfolio": {
                    "positions": [{"symbol": "AAPL", "quantity": 10, "avg_cost": 150.0}]
                }
            },
        )
        portfolio = store.load()
        assert portfolio.positions[0].currency == "USD"

    def test_loads_empty_positions_list(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().positions == []

    def test_raises_on_invalid_yaml(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.path.write_text("portfolio: {\nbad yaml: [")
        with pytest.raises(ValueError, match="Cannot parse portfolio file"):
            store.load()


class TestSave:
    def test_creates_parent_directories(self, tmp_path: Path):
        store = PortfolioStore(path=tmp_path / "nested" / "dir" / "portfolio.yaml")
        store.save(Portfolio())
        assert store.path.exists()

    def test_saves_positions(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = Portfolio(
            positions=[Position(symbol="AAPL", quantity=100, avg_cost=150.0)]
        )
        store.save(portfolio)

        with store.path.open() as fh:
            data = yaml.safe_load(fh)

        positions = data["portfolio"]["positions"]
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["quantity"] == 100
        assert positions[0]["avg_cost"] == 150.0
        assert positions[0]["currency"] == "USD"

    def test_saves_empty_portfolio(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio())

        with store.path.open() as fh:
            data = yaml.safe_load(fh)

        assert data["portfolio"]["positions"] == []

    def test_overwrites_existing_file(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(
            Portfolio(positions=[Position(symbol="AAPL", quantity=100, avg_cost=150.0)])
        )
        store.save(
            Portfolio(positions=[Position(symbol="NVDA", quantity=50, avg_cost=400.0)])
        )

        portfolio = store.load()
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].symbol == "NVDA"


class TestRoundTrip:
    def test_save_and_load_preserves_positions(self, tmp_path: Path):
        store = make_store(tmp_path)
        original = Portfolio(
            positions=[
                Position(symbol="QCOM", quantity=150, avg_cost=148.25, currency="USD"),
                Position(symbol="NVDA", quantity=200, avg_cost=112.00, currency="USD"),
            ]
        )
        store.save(original)
        loaded = store.load()

        assert len(loaded.positions) == 2
        qcom = loaded.get_position("QCOM")
        assert qcom is not None
        assert qcom.quantity == 150
        assert qcom.avg_cost == pytest.approx(148.25)
        assert qcom.currency == "USD"

    def test_round_trip_after_mutation(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = store.load()  # starts empty
        portfolio.add_position("AAPL", 100, 150.0)
        portfolio.add_position("AAPL", 100, 200.0)  # average down/up
        store.save(portfolio)

        loaded = store.load()
        aapl = loaded.get_position("AAPL")
        assert aapl is not None
        assert aapl.quantity == 200
        assert aapl.avg_cost == pytest.approx(175.0)


class TestCashPersistence:
    def test_saves_and_loads_cash(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = Portfolio(
            cash=[CashPosition("USD", 5000.0), CashPosition("EUR", 3000.0)]
        )
        store.save(portfolio)
        loaded = store.load()
        assert len(loaded.cash) == 2
        usd = loaded.get_cash("USD")
        assert usd is not None
        assert usd.amount == pytest.approx(5000.0)

    def test_load_ignores_missing_cash_section(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().cash == []

    def test_round_trip_cash_after_mutation(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = store.load()
        portfolio.add_cash("EUR", 1000.0)
        portfolio.add_cash("EUR", 500.0)
        store.save(portfolio)
        loaded = store.load()
        eur = loaded.get_cash("EUR")
        assert eur is not None
        assert eur.amount == pytest.approx(1500.0)


class TestBaseCurrency:
    def test_default_base_currency_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().base_currency == "USD"

    def test_saves_and_loads_base_currency(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(base_currency="EUR"))
        assert store.load().base_currency == "EUR"

    def test_base_currency_round_trip(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(base_currency="gbp"))
        assert store.load().base_currency == "GBP"
