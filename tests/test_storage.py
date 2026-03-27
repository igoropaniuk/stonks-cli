"""Tests for stonks_cli.storage."""

import re
from pathlib import Path

import pytest
import yaml

import stonks_cli.storage as storage_module
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem
from stonks_cli.storage import PortfolioStore, seed_sample_portfolio


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

    def test_raises_on_non_mapping_top_level(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="Invalid portfolio file"):
            store.load()

    def test_raises_on_position_missing_symbol(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [{"quantity": 10, "avg_cost": 150.0}]}},
        )
        with pytest.raises(ValueError, match="Invalid position entry"):
            store.load()

    def test_raises_on_position_missing_quantity(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [{"symbol": "AAPL", "avg_cost": 150.0}]}},
        )
        with pytest.raises(ValueError, match="Invalid position entry"):
            store.load()

    def test_raises_on_position_missing_avg_cost(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [{"symbol": "AAPL", "quantity": 10}]}},
        )
        with pytest.raises(ValueError, match="Invalid position entry"):
            store.load()

    def test_raises_on_cash_missing_currency(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [], "cash": [{"amount": 1000.0}]}},
        )
        with pytest.raises(ValueError, match="Invalid cash entry"):
            store.load()

    def test_raises_on_cash_missing_amount(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [], "cash": [{"currency": "EUR"}]}},
        )
        with pytest.raises(ValueError, match="Invalid cash entry"):
            store.load()

    def test_raises_on_watchlist_missing_symbol(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [], "watchlist": [{"asset_type": "crypto"}]}},
        )
        with pytest.raises(ValueError, match="Invalid watchlist entry"):
            store.load()

    def test_schema_errors_include_file_path(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {"portfolio": {"positions": [{"quantity": 10, "avg_cost": 150.0}]}},
        )
        with pytest.raises(ValueError, match=re.escape(str(store.path))):
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


class TestSeedSamplePortfolio:
    def _patch(self, monkeypatch, tmp_path: Path):
        config_dir = tmp_path / "stonks"
        dest = config_dir / "portfolio.yaml"
        monkeypatch.setattr(storage_module, "PORTFOLIO_CONFIG_DIR", config_dir)
        monkeypatch.setattr(storage_module, "DEFAULT_PORTFOLIO_PATH", dest)
        return config_dir, dest

    def test_creates_portfolio_when_config_dir_absent(
        self, monkeypatch, tmp_path: Path
    ):
        config_dir, dest = self._patch(monkeypatch, tmp_path)
        result = seed_sample_portfolio()
        assert result is True
        assert dest.exists()

    def test_written_file_is_valid_yaml_with_positions(
        self, monkeypatch, tmp_path: Path
    ):
        _, dest = self._patch(monkeypatch, tmp_path)
        seed_sample_portfolio()
        data = yaml.safe_load(dest.read_text())
        assert "portfolio" in data
        assert len(data["portfolio"]["positions"]) > 0

    def test_does_not_overwrite_existing_yaml(self, monkeypatch, tmp_path: Path):
        config_dir, dest = self._patch(monkeypatch, tmp_path)
        config_dir.mkdir(parents=True)
        existing = config_dir / "other.yaml"
        existing.write_text("portfolio: {positions: []}")
        result = seed_sample_portfolio()
        assert result is False
        assert not dest.exists()

    def test_idempotent_after_seed(self, monkeypatch, tmp_path: Path):
        _, dest = self._patch(monkeypatch, tmp_path)
        seed_sample_portfolio()
        first_mtime = dest.stat().st_mtime
        result = seed_sample_portfolio()
        assert result is False
        assert dest.stat().st_mtime == first_mtime


class TestName:
    def test_default_name_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().name == ""

    def test_saves_and_loads_name(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(name="Work"))
        assert store.load().name == "Work"

    def test_name_round_trip_empty(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(name=""))
        assert store.load().name == ""


class TestWatchlist:
    def test_loads_watchlist_from_yaml(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {
                "portfolio": {
                    "positions": [],
                    "watchlist": [{"symbol": "TSLA"}, {"symbol": "NVDA"}],
                }
            },
        )
        portfolio = store.load()
        assert len(portfolio.watchlist) == 2
        assert portfolio.watchlist[0].symbol == "TSLA"
        assert portfolio.watchlist[1].symbol == "NVDA"

    def test_default_empty_watchlist_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().watchlist == []

    def test_saves_watchlist(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = Portfolio(watchlist=[WatchlistItem("AAPL"), WatchlistItem("GOOGL")])
        store.save(portfolio)
        data = yaml.safe_load(store.path.read_text())
        wl = data["portfolio"]["watchlist"]
        assert len(wl) == 2
        assert wl[0] == {"symbol": "AAPL"}
        assert wl[1] == {"symbol": "GOOGL"}

    def test_watchlist_round_trip(self, tmp_path: Path):
        store = make_store(tmp_path)
        original = Portfolio(watchlist=[WatchlistItem("TSLA"), WatchlistItem("META")])
        store.save(original)
        loaded = store.load()
        assert len(loaded.watchlist) == 2
        assert loaded.watchlist[0].symbol == "TSLA"
        assert loaded.watchlist[1].symbol == "META"

    def test_empty_watchlist_round_trip(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(watchlist=[]))
        assert store.load().watchlist == []


class TestAssetType:
    def test_loads_position_asset_type_from_yaml(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {
                "portfolio": {
                    "positions": [
                        {
                            "symbol": "BTC-USD",
                            "quantity": 0.25,
                            "avg_cost": 50000.0,
                            "currency": "USD",
                            "asset_type": "crypto",
                        }
                    ],
                }
            },
        )
        portfolio = store.load()
        assert portfolio.positions[0].asset_type == "crypto"

    def test_default_asset_type_none_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(
            store.path,
            {
                "portfolio": {
                    "positions": [
                        {"symbol": "AAPL", "quantity": 10, "avg_cost": 150.0}
                    ],
                }
            },
        )
        assert store.load().positions[0].asset_type is None

    def test_saves_asset_type_only_when_set(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = Portfolio(
            positions=[
                Position(symbol="AAPL", quantity=10, avg_cost=150.0),
                Position(
                    symbol="BTC-USD",
                    quantity=0.25,
                    avg_cost=50000.0,
                    asset_type="crypto",
                ),
            ]
        )
        store.save(portfolio)
        data = yaml.safe_load(store.path.read_text())
        positions = data["portfolio"]["positions"]
        assert "asset_type" not in positions[0]
        assert positions[1]["asset_type"] == "crypto"

    def test_asset_type_round_trip(self, tmp_path: Path):
        store = make_store(tmp_path)
        original = Portfolio(
            positions=[
                Position(
                    symbol="ETH-USD",
                    quantity=2.5,
                    avg_cost=2800.0,
                    asset_type="crypto",
                )
            ]
        )
        store.save(original)
        loaded = store.load()
        assert loaded.positions[0].asset_type == "crypto"

    def test_watchlist_asset_type_round_trip(self, tmp_path: Path):
        store = make_store(tmp_path)
        original = Portfolio(
            watchlist=[WatchlistItem(symbol="BTC-USD", asset_type="crypto")]
        )
        store.save(original)
        loaded = store.load()
        assert loaded.watchlist[0].asset_type == "crypto"

    def test_watchlist_asset_type_omitted_when_none(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(watchlist=[WatchlistItem(symbol="TSLA")]))
        data = yaml.safe_load(store.path.read_text())
        assert "asset_type" not in data["portfolio"]["watchlist"][0]
