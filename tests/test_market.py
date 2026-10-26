"""Tests for stonks_cli.market.build_market_snapshot."""

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from stonks_cli.bond_fetcher import BondQuote, BondSource
from stonks_cli.bonds import BondInfo
from stonks_cli.fetcher import QuoteInfo
from stonks_cli.market import build_market_snapshot
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_portfolio(
    positions: list[Position] | None = None,
    watchlist: list[WatchlistItem] | None = None,
    cash: list[CashPosition] | None = None,
    base_currency: str = "USD",
) -> Portfolio:
    return Portfolio(
        positions=positions or [],
        watchlist=watchlist or [],
        cash=cash or [],
        base_currency=base_currency,
        name="test",
    )


def _make_fetcher_mock(
    best_equity_prices: dict | None = None,
    prices: dict | None = None,
    exchange_names: dict | None = None,
    prev_closes: dict | None = None,
    forex_rates: dict | None = None,
    quote_currencies: dict | None = None,
) -> MagicMock:
    m = MagicMock()
    m.fetch_best_equity_prices.return_value = best_equity_prices or {}
    m.fetch_prices.return_value = prices or {}
    m.fetch_exchange_names.return_value = exchange_names or {}
    codes = exchange_names or {}
    currencies = quote_currencies or {}
    m.fetch_quote_info.return_value = {
        sym: QuoteInfo(codes.get(sym), currencies.get(sym, "USD"))
        for sym in set(codes) | set(currencies)
    }
    m.fetch_previous_closes.return_value = prev_closes or {}
    m.fetch_forex_rates.return_value = forex_rates or {}
    return m


def _make_crypto_mock(
    prices: dict | None = None,
    prev_closes: dict | None = None,
) -> MagicMock:
    m = MagicMock()
    m.fetch_prices_and_changes.return_value = (prices or {}, prev_closes or {})
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildMarketSnapshot:
    def test_equity_all_found_in_extended(self) -> None:
        pos = Position(symbol="AAPL", quantity=10, avg_cost=150.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            best_equity_prices={"AAPL": (175.0, "regular")},
            exchange_names={"AAPL": "NMS"},
            prev_closes={"AAPL": 172.0},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["AAPL"] == 175.0
        assert snap.sessions["AAPL"] == "regular"
        assert snap.exchange_codes["AAPL"] == "NMS"
        assert snap.prev_closes["AAPL"] == 172.0
        fetcher.fetch_best_equity_prices.assert_called_once_with(["AAPL"])

    def test_equity_batch_fallback(self) -> None:
        pos = Position(symbol="MSFT", quantity=5, avg_cost=300.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            best_equity_prices={"MSFT": (310.0, "regular")},
            exchange_names={"MSFT": "NMS"},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["MSFT"] == 310.0
        assert snap.sessions["MSFT"] == "regular"

    def test_equity_single_fallback(self) -> None:
        pos = Position(symbol="TSLA", quantity=2, avg_cost=200.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            best_equity_prices={"TSLA": (250.0, "post")},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["TSLA"] == 250.0
        assert snap.sessions["TSLA"] == "post"

    def test_equity_single_fallback_none(self) -> None:
        pos = Position(symbol="DEAD", quantity=1, avg_cost=1.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(best_equity_prices={})
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert "DEAD" not in snap.prices

    def test_crypto_coingecko_success(self) -> None:
        pos = Position(
            symbol="BTC-USD", quantity=0.5, avg_cost=30000.0, asset_type="crypto"
        )
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock()
        crypto = _make_crypto_mock(
            prices={"BTC-USD": 50000.0}, prev_closes={"BTC-USD": 48000.0}
        )
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.CryptoFetcher", return_value=crypto),
        ):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["BTC-USD"] == 50000.0
        assert snap.prev_closes["BTC-USD"] == 48000.0
        assert snap.sessions["BTC-USD"] == "regular"

    def test_crypto_http_status_error_fallback(self) -> None:
        pos = Position(
            symbol="ETH-USD", quantity=1.0, avg_cost=2000.0, asset_type="crypto"
        )
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            prices={"ETH-USD": 2100.0},
            prev_closes={"ETH-USD": 2050.0},
        )
        crypto = MagicMock()
        crypto.fetch_prices_and_changes.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=MagicMock()
        )
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.CryptoFetcher", return_value=crypto),
        ):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["ETH-USD"] == 2100.0
        fetcher.fetch_prices.assert_called_with(["ETH-USD"])

    def test_crypto_request_error_fallback(self) -> None:
        pos = Position(
            symbol="SOL-USD", quantity=10.0, avg_cost=100.0, asset_type="crypto"
        )
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(prices={"SOL-USD": 120.0})
        crypto = MagicMock()
        crypto.fetch_prices_and_changes.side_effect = httpx.RequestError("timeout")
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.CryptoFetcher", return_value=crypto),
        ):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["SOL-USD"] == 120.0

    def test_crypto_unexpected_exception_logged(self) -> None:
        pos = Position(
            symbol="XRP-USD", quantity=100.0, avg_cost=0.5, asset_type="crypto"
        )
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock()
        crypto = MagicMock()
        crypto.fetch_prices_and_changes.side_effect = RuntimeError("boom")
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.CryptoFetcher", return_value=crypto),
        ):
            snap = build_market_snapshot([portfolio])

        # Unexpected exception is swallowed; prices absent
        assert "XRP-USD" not in snap.prices

    def test_external_ids_passed_to_crypto_fetcher(self) -> None:
        pos = Position(
            symbol="BTC-USD",
            quantity=1.0,
            avg_cost=30000.0,
            asset_type="crypto",
            external_id="bitcoin",
        )
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock()
        crypto = _make_crypto_mock(prices={"BTC-USD": 50000.0})
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.CryptoFetcher", return_value=crypto),
        ):
            build_market_snapshot([portfolio])

        crypto.fetch_prices_and_changes.assert_called_once_with(
            ["BTC-USD"], external_ids={"BTC-USD": "bitcoin"}
        )

    def test_forex_rates_collected(self) -> None:
        pos = Position(symbol="LVMH.PA", quantity=2, avg_cost=700.0, currency="EUR")
        cash = CashPosition(currency="EUR", amount=1000.0)
        portfolio = _make_portfolio(positions=[pos], cash=[cash], base_currency="USD")
        fetcher = _make_fetcher_mock(
            best_equity_prices={"LVMH.PA": (750.0, "regular")},
            forex_rates={"EUR": 1.08},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        fetcher.fetch_forex_rates.assert_called_once()
        assert "USD" in snap.forex_rates

    def test_watchlist_symbols_included(self) -> None:
        item = WatchlistItem(symbol="NVDA")
        portfolio = _make_portfolio(watchlist=[item])
        fetcher = _make_fetcher_mock(best_equity_prices={"NVDA": (900.0, "regular")})
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["NVDA"] == 900.0

    def test_empty_portfolio(self) -> None:
        portfolio = _make_portfolio()
        fetcher = _make_fetcher_mock()
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices == {}
        assert snap.sessions == {}


# ---------------------------------------------------------------------------
# Bonds
# ---------------------------------------------------------------------------


class TestBondsInSnapshot:
    def _bond_position(self):
        return Position(
            symbol="UST5.0SEP31",
            quantity=15,
            avg_cost=100.47,
            bond=BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3"),
        )

    def test_bonds_bypass_yfinance_and_carry_source_label(self):
        bond = self._bond_position()
        stock = Position(symbol="AAPL", quantity=1, avg_cost=100.0)
        portfolio = _make_portfolio(positions=[bond, stock])
        fetcher = _make_fetcher_mock(best_equity_prices={"AAPL": (175.0, "regular")})
        bond_fetcher = MagicMock()
        quote = BondQuote(99.75, 100.0, BondSource.FEDINVEST, date(2026, 10, 14))
        bond_fetcher.fetch_quotes.return_value = {"UST5.0SEP31": quote}
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=fetcher),
            patch("stonks_cli.market.BondPriceFetcher", return_value=bond_fetcher),
        ):
            snap = build_market_snapshot([portfolio])

        fetcher.fetch_best_equity_prices.assert_called_once_with(["AAPL"])
        fetcher.fetch_quote_info.assert_called_once_with(["AAPL"])
        bond_fetcher.fetch_quotes.assert_called_once_with({"UST5.0SEP31": bond.bond})
        assert snap.prices["UST5.0SEP31"] == 99.75
        assert snap.prev_closes["UST5.0SEP31"] == 100.0
        assert snap.sessions["UST5.0SEP31"] == "regular"
        assert snap.exchange_codes["UST5.0SEP31"] == "UST"
        assert snap.bond_quotes["UST5.0SEP31"] == quote

    def test_unpriced_bond_is_labelled_bond(self):
        portfolio = _make_portfolio(positions=[self._bond_position()])
        bond_fetcher = MagicMock()
        bond_fetcher.fetch_quotes.return_value = {}
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=_make_fetcher_mock()),
            patch("stonks_cli.market.BondPriceFetcher", return_value=bond_fetcher),
        ):
            snap = build_market_snapshot([portfolio])
        assert "UST5.0SEP31" not in snap.prices
        assert snap.exchange_codes["UST5.0SEP31"] == "Bond"

    def test_no_bonds_skips_bond_fetcher(self):
        portfolio = _make_portfolio(
            positions=[
                Position(symbol="TLT", quantity=1, avg_cost=90.0, asset_type="bond")
            ]
        )
        with (
            patch("stonks_cli.market.PriceFetcher", return_value=_make_fetcher_mock()),
            patch("stonks_cli.market.BondPriceFetcher") as bond_cls,
        ):
            build_market_snapshot([portfolio])
        bond_cls.assert_not_called()


class TestMinorUnitQuotes:
    def test_pence_quotes_are_converted_to_pounds(self) -> None:
        bp = Position(symbol="BP.L", quantity=100, avg_cost=5.0, currency="GBP")
        aapl = Position(symbol="AAPL", quantity=1, avg_cost=100.0)
        portfolio = _make_portfolio(positions=[bp, aapl], base_currency="GBP")
        fetcher = _make_fetcher_mock(
            best_equity_prices={"BP.L": (625.7, "regular"), "AAPL": (200.0, "regular")},
            prev_closes={"BP.L": 620.0, "AAPL": 198.0},
            exchange_names={"BP.L": "LSE", "AAPL": "NMS"},
            quote_currencies={"BP.L": "GBp", "AAPL": "USD"},
            forex_rates={"GBP": 1.0, "USD": 0.8},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["BP.L"] == pytest.approx(6.257)
        assert snap.prev_closes["BP.L"] == pytest.approx(6.20)
        assert snap.exchange_codes["BP.L"] == "LSE"
        # Other quotes untouched.
        assert snap.prices["AAPL"] == 200.0
        assert snap.prev_closes["AAPL"] == 198.0
        # 100 shares x 6.257 GBP, not x 625.7.
        assert bp.market_value(snap.prices["BP.L"]) == pytest.approx(625.7)

    @pytest.mark.parametrize("currency", ["GBX", "ZAc", "ILA"])
    def test_other_minor_units(self, currency: str) -> None:
        pos = Position(symbol="X.Y", quantity=1, avg_cost=1.0)
        fetcher = _make_fetcher_mock(
            best_equity_prices={"X.Y": (1000.0, "regular")},
            quote_currencies={"X.Y": currency},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([_make_portfolio(positions=[pos])])
        assert snap.prices["X.Y"] == pytest.approx(10.0)

    def test_real_pound_quote_is_not_scaled(self) -> None:
        pos = Position(symbol="X.L", quantity=1, avg_cost=1.0, currency="GBP")
        fetcher = _make_fetcher_mock(
            best_equity_prices={"X.L": (12.5, "regular")},
            quote_currencies={"X.L": "GBP"},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([_make_portfolio(positions=[pos])])
        assert snap.prices["X.L"] == 12.5
