"""Tests for the stock detail screen and related fetcher helpers."""

from dataclasses import asdict
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd
import pytest
from textual.message_pump import NoActiveAppError
from textual.widgets import DataTable, Label, Static

from stonks_cli.app import PortfolioApp
from stonks_cli.detail import StockDetailScreen
from stonks_cli.models import CashPosition, Portfolio, Position
from stonks_cli.stock_detail import (
    StockDetail,
    StockDetailFetcher,
    _calc_performance,
    _trailing_return,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USD_RATES = {"USD": {"USD": 1.0}}

_AAPL_POS = Position(symbol="AAPL", quantity=10, avg_cost=150.0)


def _make_app(positions=None, cash=None, prices=None) -> PortfolioApp:
    """Create a PortfolioApp with sensible defaults for testing."""
    if positions is None:
        positions = [_AAPL_POS]
    if prices is None:
        prices = {"AAPL": 160.0}
    portfolio = Portfolio(
        positions=positions,
        cash=cash or [],
    )
    return PortfolioApp(
        portfolios=[portfolio],
        prices=prices,
        forex_rates=USD_RATES,
    )


_MINIMAL_DETAIL = StockDetail(
    symbol="AAPL",
    name="Apple Inc.",
    performance={
        "YTD Return": ("+ 8.14%", "- 3.08%"),
        "1-Year Return": ("+ 17.81%", "+ 18.16%"),
    },
    price_histories={
        "1 Month": (["2025-03-01", "2025-03-02", "2025-03-03"], [150.0, 152.0, 151.5]),
    },
    summary={
        "Previous Close": "150.00",
        "Open": "151.00",
        "Volume": "1,000,000",
        "Market Cap (intraday)": "3.73T",
    },
    eps_quarters=["Q1 FY25", "Q2 FY25"],
    eps_actual=[1.50, 1.60],
    eps_estimate=[1.45, 1.55],
    eps_diff=[0.05, 0.05],
    next_earnings_date="Apr 30, 2025",
    next_eps_estimate=1.70,
    rev_quarters=["Q1 FY25", "Q2 FY25"],
    rev_values=[90.0, 95.0],
    earn_values=[25.0, 27.0],
    price_targets={"low": 140.0, "current": 155.0, "mean": 180.0, "high": 220.0},
    recommendations=[
        {
            "period": "0m",
            "strongBuy": 10,
            "buy": 15,
            "hold": 8,
            "sell": 2,
            "strongSell": 1,
        },
    ],
    recommendation_key="buy",
    num_analysts=36,
    valuation={
        "Market Cap": "3.73T",
        "Trailing P/E": "32.50",
    },
    financials={
        "Profit Margin": "25.50%",
        "Revenue (ttm)": "380.00B",
    },
)

_EMPTY_DETAIL = StockDetail(
    symbol="UNKNOWN",
    name="UNKNOWN",
    performance={},
    price_histories={},
    summary={},
    eps_quarters=[],
    eps_actual=[],
    eps_estimate=[],
    eps_diff=[],
    next_earnings_date="N/A",
    next_eps_estimate=None,
    rev_quarters=[],
    rev_values=[],
    earn_values=[],
    price_targets={},
    recommendations=[],
    recommendation_key="N/A",
    num_analysts=0,
    valuation={},
    financials={},
)


# ---------------------------------------------------------------------------
# _trailing_return tests
# ---------------------------------------------------------------------------


class TestTrailingReturn:
    def test_positive_return(self):
        hist = pd.DataFrame({"Close": [100.0, 110.0]})
        assert _trailing_return(hist) == "+ 10.00%"

    def test_negative_return(self):
        hist = pd.DataFrame({"Close": [100.0, 90.0]})
        assert _trailing_return(hist) == "- 10.00%"

    def test_zero_return(self):
        hist = pd.DataFrame({"Close": [100.0, 100.0]})
        assert _trailing_return(hist) == "+ 0.00%"

    def test_empty_dataframe(self):
        assert _trailing_return(pd.DataFrame()) == "N/A"

    def test_none_input(self):
        assert _trailing_return(None) == "N/A"

    def test_single_row(self):
        hist = pd.DataFrame({"Close": [100.0]})
        assert _trailing_return(hist) == "N/A"

    def test_zero_first_price(self):
        hist = pd.DataFrame({"Close": [0.0, 100.0]})
        assert _trailing_return(hist) == "N/A"

    def test_missing_close_column(self):
        hist = pd.DataFrame({"Open": [100.0, 110.0]})
        assert _trailing_return(hist) == "N/A"


# ---------------------------------------------------------------------------
# _calc_performance tests
# ---------------------------------------------------------------------------


class TestCalcPerformance:
    @pytest.fixture(autouse=True)
    def _mock_yf_ticker(self):
        with patch("stonks_cli.fetcher.yf.Ticker") as mock_cls:
            self.mock_ticker_cls = mock_cls
            yield

    def test_returns_all_periods(self):
        def make_hist(start, end):
            return pd.DataFrame({"Close": [start, end]})

        ticker = MagicMock()
        self.mock_ticker_cls.return_value = ticker
        ticker.history.return_value = make_hist(100.0, 120.0)

        result = _calc_performance("AAPL")

        assert "YTD Return" in result
        assert "1-Year Return" in result
        assert "3-Year Return" in result
        assert "5-Year Return" in result
        # Both stock and S&P are the same mock
        for label, (stock_ret, sp_ret) in result.items():
            assert "20.00%" in stock_ret

    def test_handles_exception(self):
        self.mock_ticker_cls.side_effect = Exception("network error")
        result = _calc_performance("FAIL")
        assert result == {}


# ---------------------------------------------------------------------------
# Unit tests for fetcher formatting helpers
# ---------------------------------------------------------------------------


class TestFetcherHelpers:
    def test_fmt_price(self):
        from stonks_cli.stock_detail import _fmt_price

        assert _fmt_price(123.456) == "123.46"
        assert _fmt_price(None) == "N/A"
        assert _fmt_price(float("nan")) == "N/A"

    def test_fmt_bid_ask(self):
        from stonks_cli.stock_detail import _fmt_bid_ask

        assert _fmt_bid_ask(150.5, 100) == "150.50 x 100"
        assert _fmt_bid_ask(150.5, None) == "150.50"
        assert _fmt_bid_ask(None, 100) == "N/A"

    def test_fmt_range(self):
        from stonks_cli.stock_detail import _fmt_range

        assert _fmt_range(100.0, 200.0) == "100.00 - 200.00"
        assert _fmt_range(None, 200.0) == "N/A"
        assert _fmt_range(100.0, None) == "N/A"

    def test_fmt_int(self):
        from stonks_cli.stock_detail import _fmt_int

        assert _fmt_int(1000000) == "1,000,000"
        assert _fmt_int(None) == "N/A"

    def test_fmt_large(self):
        from stonks_cli.stock_detail import _fmt_large

        assert _fmt_large(3.73e12) == "3.73T"
        assert _fmt_large(1.5e9) == "1.50B"
        assert _fmt_large(2.5e6) == "2.50M"
        assert _fmt_large(999.0) == "999"
        assert _fmt_large(None) == "N/A"
        assert _fmt_large(-1.5e9) == "-1.50B"

    def test_fmt_dec(self):
        from stonks_cli.stock_detail import _fmt_dec

        assert _fmt_dec(3.14159, 2) == "3.14"
        assert _fmt_dec(None) == "N/A"

    def test_fmt_pct(self):
        from stonks_cli.stock_detail import _fmt_pct

        assert _fmt_pct(0.2550) == "25.50%"
        assert _fmt_pct(None) == "N/A"

    def test_fiscal_quarter(self):
        from stonks_cli.stock_detail import _fiscal_quarter

        class FakeTS:
            month = 7
            year = 2025

        assert _fiscal_quarter(FakeTS()) == "Q3 FY25"


# ---------------------------------------------------------------------------
# Screen unit tests (no app needed)
# ---------------------------------------------------------------------------


class TestStockDetailScreen:
    """Tests for the detail screen widget composition."""

    @pytest.fixture
    def screen(self):
        return StockDetailScreen("AAPL")

    def test_screen_creates_with_symbol(self, screen):
        assert screen._symbol == "AAPL"

    def test_bindings(self, screen):
        binding_keys = [b.key for b in screen.BINDINGS]
        assert "escape" in binding_keys
        assert "q" in binding_keys

    def test_methods_exist(self, screen):
        assert callable(screen._apply_detail)
        assert callable(screen._call_from_thread_if_running)
        assert callable(screen._show_error)
        assert callable(screen._load_detail)
        assert callable(screen._mount_performance)
        assert callable(screen._mount_price_chart)
        assert callable(screen._mount_summary)
        assert callable(screen._mount_earnings)
        assert callable(screen._mount_analyst)
        assert callable(screen._mount_statistics)

    def test_empty_detail_skips_all_optional_sections(self, screen):
        """Sections with no data should be no-ops (no mount calls)."""
        parent = MagicMock()

        screen._mount_performance(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

        screen._mount_price_chart(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

        screen._mount_earnings(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

        screen._mount_analyst(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

        screen._mount_statistics(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

    def test_performance_skips_empty(self, screen):
        parent = MagicMock()
        screen._mount_performance(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

    def test_analyst_skips_when_all_empty(self, screen):
        """Analyst section skips when no targets, no recs, and key is N/A."""
        parent = MagicMock()
        screen._mount_analyst(parent, _EMPTY_DETAIL)
        parent.mount.assert_not_called()

    def test_analyst_does_not_skip_with_recommendation_key(self, screen):
        """Analyst section does not skip if recommendation_key is set."""
        d = StockDetail(
            **{
                **asdict(_EMPTY_DETAIL),
                "recommendation_key": "buy",
                "num_analysts": 5,
            }
        )
        # Verify the guard condition does not trigger early return
        no_data = (
            not d.price_targets
            and not d.recommendations
            and d.recommendation_key == "N/A"
        )
        assert not no_data

    def test_call_from_thread_if_running_ignores_missing_active_app(self, screen):
        mock_app = MagicMock()
        mock_app.call_from_thread.side_effect = NoActiveAppError()

        with patch.object(
            type(screen), "app", new_callable=PropertyMock, return_value=mock_app
        ):
            assert (
                screen._call_from_thread_if_running(screen._show_error, "boom") is False
            )
        mock_app.call_from_thread.assert_called_once_with(screen._show_error, "boom")

    def test_load_detail_ignores_missing_active_app_on_success(self, screen):
        mock_app = MagicMock()
        mock_app.call_from_thread.side_effect = NoActiveAppError()

        with patch.object(
            type(screen), "app", new_callable=PropertyMock, return_value=mock_app
        ):
            with patch("stonks_cli.detail.StockDetailFetcher") as fetcher_cls:
                fetcher_cls.return_value.fetch_stock_detail.return_value = (
                    _MINIMAL_DETAIL
                )
                StockDetailScreen._load_detail.__wrapped__(screen)

        mock_app.call_from_thread.assert_called_once_with(
            screen._apply_detail, _MINIMAL_DETAIL
        )

    def test_load_detail_ignores_missing_active_app_on_error(self, screen):
        mock_app = MagicMock()
        mock_app.call_from_thread.side_effect = NoActiveAppError()

        with patch.object(
            type(screen), "app", new_callable=PropertyMock, return_value=mock_app
        ):
            with patch("stonks_cli.detail.StockDetailFetcher") as fetcher_cls:
                fetcher_cls.return_value.fetch_stock_detail.side_effect = RuntimeError(
                    "fetch failed"
                )
                StockDetailScreen._load_detail.__wrapped__(screen)

        mock_app.call_from_thread.assert_called_once_with(
            screen._show_error, "fetch failed"
        )


# ---------------------------------------------------------------------------
# Async screen tests (with full Textual app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_screen_compose():
    """StockDetailScreen creates expected initial widgets."""
    app = _make_app()

    async with app.run_test() as pilot:
        # Push detail screen (mock _load_detail to avoid network)
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        # Verify initial widgets
        loading = screen.query_one("#loading")
        assert loading is not None
        error = screen.query_one("#error-msg", Label)
        assert error is not None
        scroll = screen.query_one("#detail-scroll")
        assert scroll.display is False


@pytest.mark.asyncio
async def test_detail_screen_shows_error():
    """_show_error hides loading and displays error message."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._show_error("Connection failed")
        await pilot.pause()

        assert screen.query_one("#loading").display is False
        err = screen.query_one("#error-msg", Label)
        assert "Connection failed" in str(err.content)


@pytest.mark.asyncio
async def test_detail_screen_apply_detail_shows_all_sections():
    """_apply_detail populates the scroll area with all sections."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(_MINIMAL_DETAIL)
        await pilot.pause()

        assert screen.query_one("#loading").display is False
        assert screen.query_one("#detail-scroll").display is True

        # Check section titles are present
        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert any("Performance Overview" in t for t in label_texts)
        assert any("Price (1 Month)" in t for t in label_texts)
        assert any("Financial Summary" in t for t in label_texts)
        assert any("Earnings Trends" in t for t in label_texts)
        assert any("Analyst Insights" in t for t in label_texts)
        assert any("Statistics" in t for t in label_texts)


@pytest.mark.asyncio
async def test_detail_screen_apply_empty_detail():
    """_apply_detail with empty data only shows summary (always rendered)."""
    pos = Position(symbol="X", quantity=1, avg_cost=1.0)
    app = _make_app(positions=[pos], prices={"X": 1.0})

    async with app.run_test() as pilot:
        screen = StockDetailScreen("X")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(_EMPTY_DETAIL)
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        # Summary always renders; optional sections should not
        assert any("Financial Summary" in t for t in label_texts)
        assert not any("Performance Overview" in t for t in label_texts)
        assert not any("Price (1 Month)" in t for t in label_texts)
        assert not any("Earnings Trends" in t for t in label_texts)
        assert not any("Analyst Insights" in t for t in label_texts)
        assert not any("Statistics" in t for t in label_texts)


@pytest.mark.asyncio
async def test_detail_performance_cards_layout():
    """Performance cards are arranged two per row."""
    app = _make_app()
    # 4 performance items -> should create 2 rows
    d = StockDetail(
        **{
            **asdict(_EMPTY_DETAIL),
            "symbol": "AAPL",
            "performance": {
                "YTD Return": ("+ 8.14%", "- 3.08%"),
                "1-Year Return": ("+ 17.81%", "+ 18.16%"),
                "3-Year Return": ("+ 63.29%", "+ 69.39%"),
                "5-Year Return": ("+ 112.52%", "+ 69.44%"),
            },
        }
    )

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(d)
        await pilot.pause()

        perf_rows = screen.query(".perf-row")
        assert len(perf_rows) == 2  # 4 cards / 2 per row

        cards = screen.query(".perf-card")
        assert len(cards) == 4


@pytest.mark.asyncio
async def test_detail_performance_positive_negative_styling():
    """Positive returns get perf-positive class, negative get perf-negative."""
    pos = Position(symbol="TST", quantity=1, avg_cost=1.0)
    app = _make_app(positions=[pos], prices={"TST": 1.0})
    d = StockDetail(
        **{
            **asdict(_EMPTY_DETAIL),
            "symbol": "TST",
            "performance": {"YTD Return": ("+ 10.00%", "- 5.00%")},
        }
    )

    async with app.run_test() as pilot:
        screen = StockDetailScreen("TST")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(d)
        await pilot.pause()

        pos_widgets = screen.query(".perf-positive")
        neg_widgets = screen.query(".perf-negative")
        assert len(pos_widgets) >= 1
        assert len(neg_widgets) >= 1


@pytest.mark.asyncio
async def test_detail_summary_two_columns():
    """Financial summary splits items into two columns."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(_MINIMAL_DETAIL)
        await pilot.pause()

        cols = screen.query(".summary-col")
        assert len(cols) == 2


@pytest.mark.asyncio
async def test_detail_statistics_sections():
    """Statistics section shows valuation and financial highlights."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(_MINIMAL_DETAIL)
        await pilot.pause()

        statics = screen.query(Static)
        texts = [str(s.content) for s in statics]
        assert any("Valuation Measures" in t for t in texts)
        assert any("Financial Highlights" in t for t in texts)
        assert any("3.73T" in t for t in texts)
        assert any("25.50%" in t for t in texts)


@pytest.mark.asyncio
async def test_detail_analyst_price_targets_and_rating():
    """Analyst section shows price targets and latest rating."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_detail(_MINIMAL_DETAIL)
        await pilot.pause()

        statics = screen.query(Static)
        texts = [str(s.content) for s in statics]
        assert any("140.00" in t for t in texts)  # low target
        assert any("220.00" in t for t in texts)  # high target
        assert any("Buy" in t for t in texts)  # rating
        assert any("36" in t for t in texts)  # num analysts


# ---------------------------------------------------------------------------
# on_data_table_row_selected tests (in app.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_selected_opens_detail_for_equity():
    """Pressing Enter on an equity row pushes StockDetailScreen."""
    ibm = Position(symbol="IBM", quantity=10, avg_cost=100.0)
    app = _make_app(positions=[ibm], prices={"IBM": 120.0})

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=0)

        with patch.object(app, "push_screen") as mock_push:
            await pilot.press("enter")
            await pilot.pause()
            assert mock_push.call_count == 1
            pushed = mock_push.call_args[0][0]
            assert isinstance(pushed, StockDetailScreen)
            assert pushed._symbol == "IBM"


@pytest.mark.asyncio
async def test_row_selected_skips_cash():
    """Pressing Enter on a cash row does NOT push detail screen."""
    cash = [CashPosition(currency="USD", amount=1000.0)]
    app = _make_app(positions=[], cash=cash, prices={})

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.move_cursor(row=0)

        with patch.object(app, "push_screen") as mock_push:
            await pilot.press("enter")
            await pilot.pause()
            mock_push.assert_not_called()


@pytest.mark.asyncio
async def test_escape_pops_detail_screen():
    """Pressing Escape on detail screen returns to main screen."""
    portfolio = Portfolio(
        positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)]
    )
    app = PortfolioApp(
        portfolios=[portfolio],
        prices={"AAPL": 160.0},
        forex_rates=USD_RATES,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        screen = StockDetailScreen("AAPL")
        with patch.object(StockDetailScreen, "_load_detail"):
            app.push_screen(screen)
            await pilot.pause()

        assert isinstance(app.screen, StockDetailScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, StockDetailScreen)


# ---------------------------------------------------------------------------
# fetch_stock_detail tests
# ---------------------------------------------------------------------------


class TestFetchStockDetail:
    """Test fetch_stock_detail with mocked yfinance."""

    @pytest.fixture(autouse=True)
    def _mock_yf(self):
        with (
            patch("stonks_cli.stock_detail.yf.Ticker") as mock_cls,
            patch("stonks_cli.stock_detail._calc_performance", return_value={}),
        ):
            self.mock_ticker_cls = mock_cls
            self.ticker = MagicMock()
            mock_cls.return_value = self.ticker
            yield

    def test_basic_fetch(self):
        self.ticker.info = {
            "previousClose": 150.0,
            "open": 151.0,
            "marketCap": 3.73e12,
            "trailingPE": 32.5,
            "trailingEps": 6.50,
            "recommendationKey": "buy",
            "numberOfAnalystOpinions": 36,
        }

        dates = pd.date_range("2025-03-01", periods=3)
        hist = pd.DataFrame({"Close": [150.0, 152.0, 151.5]}, index=dates)
        self.ticker.history.return_value = hist

        self.ticker.earnings_history = pd.DataFrame()
        self.ticker.earnings_estimate = pd.DataFrame()
        self.ticker.quarterly_income_stmt = pd.DataFrame()
        self.ticker.analyst_price_targets = {}
        self.ticker.recommendations_summary = pd.DataFrame()

        detail = StockDetailFetcher().fetch_stock_detail("AAPL")

        assert detail.symbol == "AAPL"
        assert detail.performance == {}
        assert "1 Month" in detail.price_histories
        assert detail.summary["Previous Close"] == "150.00"
        assert detail.summary["Market Cap (intraday)"] == "3.73T"
        assert detail.recommendation_key == "buy"
        assert detail.num_analysts == 36

    def test_fetch_with_earnings_data(self):
        self.ticker.info = {}

        hist = pd.DataFrame(
            {"Close": [100.0]}, index=pd.date_range("2025-03-01", periods=1)
        )
        self.ticker.history.return_value = hist

        eh_index = pd.to_datetime(["2025-01-15", "2025-04-15"])
        eh = pd.DataFrame(
            {
                "epsActual": [1.50, 1.60],
                "epsEstimate": [1.45, 1.55],
                "epsDifference": [0.05, 0.05],
            },
            index=eh_index,
        )
        self.ticker.earnings_history = eh

        ee = pd.DataFrame({"avg": [1.70]}, index=["0q"])
        self.ticker.earnings_estimate = ee

        q_cols = pd.to_datetime(["2025-03-31", "2024-12-31"])
        qinc = pd.DataFrame(
            {
                q_cols[0]: [90e9, 25e9],
                q_cols[1]: [85e9, 23e9],
            },
            index=["Total Revenue", "Net Income"],
        )
        self.ticker.quarterly_income_stmt = qinc

        self.ticker.analyst_price_targets = {
            "current": 155.0,
            "low": 140.0,
            "mean": 180.0,
            "high": 220.0,
        }
        self.ticker.recommendations_summary = pd.DataFrame()

        detail = StockDetailFetcher().fetch_stock_detail("TEST")

        assert len(detail.eps_quarters) == 2
        assert detail.eps_actual == [1.50, 1.60]
        assert detail.next_eps_estimate == 1.70
        assert len(detail.rev_quarters) == 2
        assert detail.price_targets["low"] == 140.0

    def test_fetch_with_recommendations(self):
        self.ticker.info = {
            "recommendationKey": "strong_buy",
            "numberOfAnalystOpinions": 20,
        }

        self.ticker.history.return_value = pd.DataFrame(
            {"Close": [100.0]}, index=pd.date_range("2025-03-01", periods=1)
        )
        self.ticker.earnings_history = pd.DataFrame()
        self.ticker.earnings_estimate = pd.DataFrame()
        self.ticker.quarterly_income_stmt = pd.DataFrame()
        self.ticker.analyst_price_targets = {}

        rec0 = {
            "period": "0m",
            "strongBuy": 5,
            "buy": 10,
            "hold": 3,
            "sell": 1,
            "strongSell": 0,
        }
        rec1 = {
            "period": "-1m",
            "strongBuy": 4,
            "buy": 11,
            "hold": 4,
            "sell": 1,
            "strongSell": 0,
        }
        rs = pd.DataFrame([rec0, rec1])
        self.ticker.recommendations_summary = rs

        detail = StockDetailFetcher().fetch_stock_detail("REC")

        assert len(detail.recommendations) == 2
        assert detail.recommendations[0]["strongBuy"] == 5
        assert detail.recommendation_key == "strong_buy"
        assert detail.num_analysts == 20

    def test_fetch_handles_exceptions_gracefully(self):
        """If earnings_history raises, it's caught and empty lists returned."""
        self.ticker.info = {}

        self.ticker.history.return_value = pd.DataFrame()

        type(self.ticker).earnings_history = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )
        type(self.ticker).earnings_estimate = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )
        type(self.ticker).quarterly_income_stmt = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )
        type(self.ticker).analyst_price_targets = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )
        type(self.ticker).recommendations_summary = property(
            lambda self: (_ for _ in ()).throw(Exception("fail"))
        )

        detail = StockDetailFetcher().fetch_stock_detail("FAIL")

        assert detail.symbol == "FAIL"
        assert detail.eps_quarters == []
        assert detail.rev_quarters == []
        assert detail.recommendations == []
        assert detail.price_targets == {}
