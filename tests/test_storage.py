"""Tests for stonks_cli.storage."""

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

import stonks_cli.storage as storage_module
from stonks_cli.bonds import BondInfo
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem
from stonks_cli.storage import (
    PortfolioConflictError,
    PortfolioStore,
)


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


class TestName:
    def test_default_name_when_missing(self, tmp_path: Path):
        store = make_store(tmp_path)
        write_yaml(store.path, {"portfolio": {"positions": []}})
        assert store.load().name is None

    def test_saves_and_loads_name(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio(name="Work"))
        assert store.load().name == "Work"

    def test_name_round_trip_none(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(Portfolio())
        assert store.load().name is None


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


# ---------------------------------------------------------------------------
# Bond positions
# ---------------------------------------------------------------------------


class TestBondStorage:
    BOND_YAML = {
        "portfolio": {
            "positions": [
                {
                    "symbol": "UST5.0SEP31",
                    "quantity": 15,
                    "avg_cost": 100.47,
                    "currency": "USD",
                    "bond": {
                        "maturity": "2031-09-30",
                        "coupon": 5.0,
                        "cusip": "91282crn3",
                    },
                }
            ]
        }
    }

    def test_load_bond_with_defaults(self, tmp_path):
        store = make_store(tmp_path)
        write_yaml(store.path, self.BOND_YAML)
        pos = store.load().positions[0]
        assert pos.asset_type == "bond"
        assert pos.bond is not None
        assert pos.bond.maturity == date(2031, 9, 30)
        assert pos.bond.coupon == 5.0
        assert pos.bond.face_value == 1000.0
        assert pos.bond.frequency == 2
        assert pos.bond.cusip == "91282CRN3"
        assert pos.bond.price is None

    def test_yaml_date_maturity_is_accepted(self, tmp_path):
        # Unquoted 2031-09-30 is parsed by YAML as a date, not a string.
        store = make_store(tmp_path)
        store.path.write_text(
            "portfolio:\n"
            "  positions:\n"
            "    - symbol: B\n"
            "      quantity: 1\n"
            "      avg_cost: 99\n"
            "      bond:\n"
            "        maturity: 2031-09-30\n"
            "        coupon: 5\n"
        )
        assert store.load().positions[0].bond is not None

    def test_round_trip_omits_defaults(self, tmp_path):
        store = make_store(tmp_path)
        bond = BondInfo(maturity=date(2031, 9, 30), coupon=5.0, price=100.47)
        store.save(
            Portfolio(positions=[Position("UST5.0SEP31", 15, 100.47, bond=bond)])
        )
        raw = yaml.safe_load(store.path.read_text())
        saved = raw["portfolio"]["positions"][0]
        assert saved["asset_type"] == "bond"
        assert saved["bond"] == {
            "maturity": date(2031, 9, 30),
            "coupon": 5.0,
            "price": 100.47,
        }
        assert store.load().positions[0].bond == bond

    def test_round_trip_keeps_non_default_terms(self, tmp_path):
        store = make_store(tmp_path)
        bond = BondInfo(
            maturity=date(2030, 6, 1),
            coupon=4.0,
            face_value=100.0,
            frequency=1,
            cusip="037833AK6",
        )
        store.save(Portfolio(positions=[Position("CORP", 10, 98.0, bond=bond)]))
        assert store.load().positions[0].bond == bond

    def test_missing_bond_field_reports_file(self, tmp_path):
        store = make_store(tmp_path)
        data = {
            "portfolio": {
                "positions": [
                    {
                        "symbol": "B",
                        "quantity": 1,
                        "avg_cost": 99,
                        "bond": {"maturity": "2031-09-30"},
                    }
                ]
            }
        }
        write_yaml(store.path, data)
        with pytest.raises(ValueError, match="Invalid position entry.*'coupon'"):
            store.load()

    def test_bond_not_a_mapping_reports_file(self, tmp_path):
        store = make_store(tmp_path)
        data = {
            "portfolio": {
                "positions": [
                    {"symbol": "B", "quantity": 1, "avg_cost": 99, "bond": "yes"}
                ]
            }
        }
        write_yaml(store.path, data)
        with pytest.raises(ValueError, match="Invalid position entry.*mapping"):
            store.load()

    def test_invalid_bond_terms_raise(self, tmp_path):
        store = make_store(tmp_path)
        data = {
            "portfolio": {
                "positions": [
                    {
                        "symbol": "B",
                        "quantity": 1,
                        "avg_cost": 99,
                        "bond": {"maturity": "2031-09-30", "coupon": 5, "frequency": 3},
                    }
                ]
            }
        }
        write_yaml(store.path, data)
        with pytest.raises(ValueError, match="frequency must be one of"):
            store.load()


# ---------------------------------------------------------------------------
# Atomic writes and conflict detection
# ---------------------------------------------------------------------------


def _aapl() -> Portfolio:
    return Portfolio(positions=[Position(symbol="AAPL", quantity=1, avg_cost=1.0)])


class TestAtomicSave:
    def test_no_temp_files_left_behind(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(_aapl())
        assert [p.name for p in tmp_path.iterdir()] == ["portfolio.yaml"]

    def test_failed_write_keeps_previous_file(self, tmp_path: Path, monkeypatch):
        store = make_store(tmp_path)
        store.save(_aapl())
        before = store.path.read_bytes()

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(storage_module.os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            store.save(Portfolio())
        assert store.path.read_bytes() == before
        assert [p.name for p in tmp_path.iterdir()] == ["portfolio.yaml"]


class TestConflictDetection:
    def test_external_change_after_load_is_rejected(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(_aapl())
        portfolio = store.load()
        # Another process (e.g. the CLI) edits the file meanwhile.
        other = make_store(tmp_path)
        other.save(Portfolio(cash=[CashPosition(currency="EUR", amount=5.0)]))

        assert store.changed_on_disk()
        with pytest.raises(PortfolioConflictError, match="modified outside"):
            store.save(portfolio)
        # The other process's edit survived.
        assert make_store(tmp_path).load().get_cash("EUR") is not None

    def test_force_overwrites(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(_aapl())
        portfolio = store.load()
        make_store(tmp_path).save(Portfolio())
        store.save(portfolio, force=True)
        assert make_store(tmp_path).load().get_position("AAPL") is not None

    def test_own_save_updates_known_state(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = store.load()  # file absent
        store.save(portfolio)
        assert not store.changed_on_disk()
        store.save(portfolio)  # second save must not conflict with the first

    def test_file_created_externally_after_empty_load(self, tmp_path: Path):
        store = make_store(tmp_path)
        portfolio = store.load()  # file absent
        make_store(tmp_path).save(_aapl())
        with pytest.raises(PortfolioConflictError):
            store.save(portfolio)

    def test_untracked_store_never_conflicts(self, tmp_path: Path):
        make_store(tmp_path).save(_aapl())
        fresh = make_store(tmp_path)  # never loaded
        assert not fresh.changed_on_disk()
        fresh.save(Portfolio())
        assert make_store(tmp_path).load().positions == []

    def test_load_resets_conflict(self, tmp_path: Path):
        store = make_store(tmp_path)
        store.save(_aapl())
        store.load()
        make_store(tmp_path).save(Portfolio())
        reloaded = store.load()
        assert reloaded.positions == []
        store.save(reloaded)  # no conflict after reloading
