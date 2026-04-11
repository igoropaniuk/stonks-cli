"""Tests for the backtest detail screen."""

from unittest.mock import patch

import pytest
from textual.widgets import Label

from stonks_cli.app import PortfolioApp
from stonks_cli.backtest import BacktestResult
from stonks_cli.backtest_detail import BacktestScreen
from stonks_cli.dto import BacktestConfig
from stonks_cli.models import Portfolio, Position

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USD_RATES = {"USD": {"USD": 1.0}}

_AAPL_POS = Position(symbol="AAPL", quantity=10, avg_cost=150.0)


def _make_app() -> PortfolioApp:
    portfolio = Portfolio(positions=[_AAPL_POS])
    return PortfolioApp(
        portfolios=[portfolio],
        prices={"AAPL": 160.0},
        forex_rates=USD_RATES,
    )


def _default_config(**overrides: object) -> BacktestConfig:
    defaults: BacktestConfig = {
        "benchmark": "SPY",
        "start_amount": 10000,
        "start_year": 2020,
        "end_year": 2022,
        "cashflows": 0,
        "rebalance": "none",
        "skip_unavailable": False,
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _minimal_result() -> BacktestResult:
    """A result with enough data to exercise all rendering paths."""
    return BacktestResult(
        dates=["2020-01-02", "2020-06-15", "2021-01-04", "2021-06-15", "2022-01-03"],
        portfolio_values=[10000.0, 11000.0, 12000.0, 13000.0, 14000.0],
        benchmark_values=[10000.0, 10500.0, 11000.0, 11500.0, 12000.0],
        annual_years=["2020", "2021"],
        annual_portfolio_returns=[20.0, 16.7],
        annual_benchmark_returns=[10.0, 9.1],
        portfolio_cagr=18.3,
        benchmark_cagr=9.5,
        portfolio_max_drawdown=-5.2,
        benchmark_max_drawdown=-3.1,
        portfolio_sharpe=1.5,
        benchmark_sharpe=0.9,
        portfolio_best_year="2020 (+20.0%)",
        portfolio_worst_year="2021 (+16.7%)",
        benchmark_best_year="2020 (+10.0%)",
        benchmark_worst_year="2021 (+9.1%)",
        portfolio_final=14000.0,
        benchmark_final=12000.0,
        total_contributions=10000.0,
        skipped_symbols=[],
    )


def _empty_result() -> BacktestResult:
    """A result with minimal/empty data."""
    return BacktestResult()


def _result_with_skipped() -> BacktestResult:
    """A result with skipped symbols."""
    r = _minimal_result()
    r.skipped_symbols = ["GOOG", "TSLA"]
    return r


_DEFAULT_PORTFOLIO = Portfolio(positions=[_AAPL_POS])
_DEFAULT_CONFIG = _default_config()


# ---------------------------------------------------------------------------
# Compose / initial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_screen_compose():
    """BacktestScreen creates expected initial widgets."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        loading = screen.query_one("#loading")
        assert loading is not None
        error = screen.query_one("#error-msg", Label)
        assert error is not None
        scroll = screen.query_one("#bt-scroll")
        assert scroll.display is False


@pytest.mark.asyncio
async def test_backtest_screen_title_shows_portfolio_and_config():
    """Title and subtitle reflect portfolio name and config."""
    app = _make_app()
    portfolio = Portfolio(positions=[_AAPL_POS], name="My Portfolio")
    config = _default_config(
        start_year=2015, end_year=2025, start_amount=50000, cashflows=5000
    )

    async with app.run_test() as pilot:
        screen = BacktestScreen(portfolio, config)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        title = screen.query_one("#bt-title", Label)
        assert "My Portfolio" in str(title.content)
        assert "SPY" in str(title.content)

        subtitle = screen.query_one("#bt-subtitle", Label)
        sub_text = str(subtitle.content)
        assert "2015" in sub_text
        assert "2025" in sub_text
        assert "50,000" in sub_text
        assert "5,000" in sub_text


@pytest.mark.asyncio
async def test_backtest_screen_default_portfolio_name():
    """When portfolio has no name, title falls back to 'Portfolio'."""
    app = _make_app()
    portfolio = Portfolio(positions=[_AAPL_POS])  # no name

    async with app.run_test() as pilot:
        screen = BacktestScreen(portfolio, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        title = screen.query_one("#bt-title", Label)
        assert "Portfolio" in str(title.content)


# ---------------------------------------------------------------------------
# Error display
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_screen_shows_error():
    """_show_error hides loading and displays error message."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._show_error("No historical data")
        await pilot.pause()

        assert screen.query_one("#loading").display is False
        err = screen.query_one("#error-msg", Label)
        assert "No historical data" in str(err.content)


# ---------------------------------------------------------------------------
# _apply_result - full data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_result_shows_all_sections():
    """_apply_result populates the scroll area with all sections."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_minimal_result())
        await pilot.pause()

        assert screen.query_one("#loading").display is False
        assert screen.query_one("#bt-scroll").display is True

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert any("Portfolio Growth" in t for t in label_texts)
        assert any("Annual Returns" in t for t in label_texts)
        assert any("Backtest Summary" in t for t in label_texts)


@pytest.mark.asyncio
async def test_apply_result_empty_data():
    """_apply_result with empty result shows summary but no charts."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_empty_result())
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        # Summary always rendered
        assert any("Backtest Summary" in t for t in label_texts)
        # Charts need data
        assert not any("Portfolio Growth" in t for t in label_texts)
        assert not any("Annual Returns" in t for t in label_texts)


# ---------------------------------------------------------------------------
# Skipped symbols
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_result_skipped_symbols():
    """Skipped symbols are shown as a notice."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_result_with_skipped())
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert any("GOOG" in t and "TSLA" in t for t in label_texts)


@pytest.mark.asyncio
async def test_apply_result_no_skipped_symbols():
    """When no symbols are skipped, no notice is shown."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_minimal_result())
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert not any("Skipped" in t for t in label_texts)


# ---------------------------------------------------------------------------
# Summary section values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_shows_portfolio_and_benchmark_stats():
    """Summary section displays key stats for both portfolio and benchmark."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_minimal_result())
        await pilot.pause()

        statics = screen.query("Static")
        texts = [str(s.content) for s in statics]
        # Portfolio label
        assert any("Portfolio" in t for t in texts)
        # Benchmark label
        assert any("SPY" in t for t in texts)
        # Final values
        assert any("14,000" in t for t in texts)
        assert any("12,000" in t for t in texts)


# ---------------------------------------------------------------------------
# Downsample
# ---------------------------------------------------------------------------


class TestDownsample:
    def test_no_downsample_when_within_limit(self):
        values = [1.0, 2.0, 3.0]
        dates = ["a", "b", "c"]
        v, d = BacktestScreen._downsample(values, dates, max_points=10)
        assert v == values
        assert d == dates

    def test_downsample_reduces_points(self):
        n = 1000
        values = list(range(n))
        dates = [str(i) for i in range(n)]
        v, d = BacktestScreen._downsample(values, dates, max_points=100)
        assert len(v) == 101
        assert len(v) == len(d)
        # First and last values preserved
        assert v[0] == 0
        assert v[-1] == n - 1

    def test_downsample_exact_boundary(self):
        values = [1.0] * 500
        dates = ["d"] * 500
        v, d = BacktestScreen._downsample(values, dates, max_points=500)
        assert len(v) == 500


# ---------------------------------------------------------------------------
# Worker thread integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_backtest_worker_success():
    """Worker thread calls _apply_result on success."""
    app = _make_app()
    result = _minimal_result()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        with patch("stonks_cli.backtest_detail.run_backtest", return_value=result):
            with patch.object(screen, "_call_from_thread_if_running") as mock_call:
                # Call the unwrapped worker function directly
                BacktestScreen._run_backtest.__wrapped__(screen)
                assert mock_call.called
                # First call should be _apply_result
                args = mock_call.call_args_list[0]
                assert args[0][0] == screen._apply_result


@pytest.mark.asyncio
async def test_run_backtest_worker_error():
    """Worker thread calls _show_error on failure."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        with patch(
            "stonks_cli.backtest_detail.run_backtest",
            side_effect=ValueError("No data"),
        ):
            with patch.object(screen, "_call_from_thread_if_running") as mock_call:
                BacktestScreen._run_backtest.__wrapped__(screen)
                assert mock_call.called
                args = mock_call.call_args_list[0]
                assert args[0][0] == screen._show_error
                assert "No data" in args[0][1]


# ---------------------------------------------------------------------------
# Growth chart rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_growth_chart_renders_with_data():
    """Growth chart mounts when portfolio_values has data."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        screen._apply_result(_minimal_result())
        await pilot.pause()

        from textual_plotext import PlotextPlot

        charts = screen.query(PlotextPlot)
        # At least growth + annual = 2 charts
        assert len(charts) >= 2


@pytest.mark.asyncio
async def test_growth_chart_skipped_with_no_values():
    """Growth chart not mounted when portfolio_values is empty."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        r = _empty_result()
        screen._apply_result(r)
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert not any("Portfolio Growth" in t for t in label_texts)


# ---------------------------------------------------------------------------
# Annual chart rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_annual_chart_skipped_with_no_years():
    """Annual chart not mounted when annual_years is empty."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        r = _minimal_result()
        r.annual_years = []
        r.annual_portfolio_returns = []
        r.annual_benchmark_returns = []
        screen._apply_result(r)
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert not any("Annual Returns" in t for t in label_texts)


@pytest.mark.asyncio
async def test_annual_chart_with_zero_returns():
    """Annual chart handles zero return values without error."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        r = _minimal_result()
        r.annual_portfolio_returns = [0.0, 0.0]
        r.annual_benchmark_returns = [0.0, 0.0]
        screen._apply_result(r)
        await pilot.pause()

        labels = screen.query(Label)
        label_texts = [str(lb.content) for lb in labels]
        assert any("Annual Returns" in t for t in label_texts)


# ---------------------------------------------------------------------------
# Scroll bindings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scroll_bindings_present():
    """BacktestScreen has scroll bindings from SCROLL_BINDINGS."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        binding_actions = {b.action for b in screen.BINDINGS}
        assert "scroll_up" in binding_actions
        assert "scroll_down" in binding_actions
        assert "page_up" in binding_actions
        assert "page_down" in binding_actions
        assert "app.pop_screen" in binding_actions


@pytest.mark.asyncio
async def test_escape_pops_backtest_screen():
    """Pressing escape pops the backtest screen."""
    app = _make_app()

    async with app.run_test() as pilot:
        screen = BacktestScreen(_DEFAULT_PORTFOLIO, _DEFAULT_CONFIG)
        with patch.object(BacktestScreen, "_run_backtest"):
            app.push_screen(screen)
            await pilot.pause()

        assert isinstance(app.screen, BacktestScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, BacktestScreen)
