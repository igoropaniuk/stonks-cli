"""Tests for stonks_cli.market.build_market_snapshot."""

from unittest.mock import MagicMock, patch

import httpx

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
    extended: dict | None = None,
    prices: dict | None = None,
    price_single: float | None = None,
    exchange_names: dict | None = None,
    prev_closes: dict | None = None,
    current_session: str = "regular",
    forex_rates: dict | None = None,
) -> MagicMock:
    m = MagicMock()
    m.fetch_extended_prices.return_value = extended or {}
    m.fetch_prices.return_value = prices or {}
    m.fetch_price_single.return_value = price_single
    m.fetch_exchange_names.return_value = exchange_names or {}
    m.fetch_previous_closes.return_value = prev_closes or {}
    m.current_session.return_value = current_session
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
            extended={"AAPL": (175.0, "regular")},
            exchange_names={"AAPL": "NMS"},
            prev_closes={"AAPL": 172.0},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["AAPL"] == 175.0
        assert snap.sessions["AAPL"] == "regular"
        assert snap.exchange_codes["AAPL"] == "NMS"
        assert snap.prev_closes["AAPL"] == 172.0
        fetcher.fetch_prices.assert_not_called()

    def test_equity_batch_fallback(self) -> None:
        pos = Position(symbol="MSFT", quantity=5, avg_cost=300.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            extended={},  # extended misses MSFT
            prices={"MSFT": 310.0},
            exchange_names={"MSFT": "NMS"},
            prev_closes={},
            current_session="regular",
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["MSFT"] == 310.0
        fetcher.fetch_prices.assert_called_once_with(["MSFT"])

    def test_equity_single_fallback(self) -> None:
        pos = Position(symbol="TSLA", quantity=2, avg_cost=200.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(
            extended={},
            prices={},  # batch also misses
            price_single=250.0,
            current_session="post",
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        assert snap.prices["TSLA"] == 250.0
        assert snap.sessions["TSLA"] == "post"
        fetcher.fetch_price_single.assert_called_once_with("TSLA")

    def test_equity_single_fallback_none(self) -> None:
        pos = Position(symbol="DEAD", quantity=1, avg_cost=1.0)
        portfolio = _make_portfolio(positions=[pos])
        fetcher = _make_fetcher_mock(extended={}, prices={}, price_single=None)
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
            extended={"LVMH.PA": (750.0, "regular")},
            forex_rates={"EUR": 1.08},
        )
        with patch("stonks_cli.market.PriceFetcher", return_value=fetcher):
            snap = build_market_snapshot([portfolio])

        fetcher.fetch_forex_rates.assert_called_once()
        assert "USD" in snap.forex_rates

    def test_watchlist_symbols_included(self) -> None:
        item = WatchlistItem(symbol="NVDA")
        portfolio = _make_portfolio(watchlist=[item])
        fetcher = _make_fetcher_mock(extended={"NVDA": (900.0, "regular")})
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
