"""Backtest results screen with charts and summary statistics."""

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, LoadingIndicator, Static
from textual_plotext import PlotextPlot

from stonks_cli.backtest import BacktestResult, run_backtest
from stonks_cli.dto import BacktestConfig
from stonks_cli.helpers import ThreadGuardMixin, nice_yticks
from stonks_cli.models import Portfolio

logger = logging.getLogger(__name__)


def _kv_row(container: Widget, label: str, value: str) -> None:
    """Mount a single label/value row into *container*."""
    row = Horizontal(classes="kv-row")
    container.mount(row)
    row.mount(Static(label, classes="kv-label"))
    row.mount(Static(value, classes="kv-value"))


class BacktestScreen(ThreadGuardMixin, Screen, inherit_bindings=False):
    """Full-screen view showing backtest results with charts."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", priority=True),
        Binding("up", "scroll_up", "Scroll Up", show=True),
        Binding("down", "scroll_down", "Scroll Down", show=True),
        Binding("pageup", "page_up", "Page Up", show=True),
        Binding("pagedown", "page_down", "Page Down", show=True),
    ]

    CSS = """
    BacktestScreen {
        background: $surface;
    }
    #bt-title {
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }
    #bt-subtitle {
        padding: 0 1;
        color: $text-muted;
    }
    #bt-scroll {
        scrollbar-gutter: stable;
    }
    .section-title {
        padding: 1 1 0 1;
        text-style: bold;
        color: $accent;
    }
    .price-chart {
        height: 36;
        padding: 0 1;
    }
    .annual-chart {
        height: 28;
        padding: 0 1;
    }
    .summary-grid {
        height: auto;
        padding: 0 1;
    }
    .summary-col {
        width: 1fr;
        height: auto;
    }
    .kv-row {
        height: 1;
        padding: 0 1;
    }
    .kv-label {
        width: 30;
        color: $text-muted;
    }
    .kv-value {
        width: 1fr;
        text-style: bold;
    }
    #loading {
        height: 3;
        content-align: center middle;
    }
    #error-msg {
        padding: 1;
        color: $error;
    }
    .skipped-notice {
        padding: 0 1;
        color: $warning;
    }
    .positive { color: green; text-style: bold; }
    .negative { color: red; text-style: bold; }
    """

    def __init__(self, portfolio: Portfolio, config: BacktestConfig) -> None:
        super().__init__()
        self._modal = True
        self._portfolio = portfolio
        self._config = config

    def compose(self) -> ComposeResult:
        pname = self._portfolio.name or "Portfolio"
        yield Label(
            f"  Backtest: {pname} vs {self._config['benchmark']}",
            id="bt-title",
        )
        yield Label(
            f"  {self._config['start_year']}-{self._config['end_year']}  |  "
            f"Start: ${self._config['start_amount']:,.0f}  |  "
            f"Yearly contribution: ${self._config['cashflows']:,.0f}",
            id="bt-subtitle",
        )
        yield LoadingIndicator(id="loading")
        yield Label("", id="error-msg")
        yield VerticalScroll(id="bt-scroll")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bt-scroll").display = False
        self._run_backtest()

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#bt-scroll", VerticalScroll)

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up()

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down()

    def action_page_up(self) -> None:
        self._scroll().scroll_page_up()

    def action_page_down(self) -> None:
        self._scroll().scroll_page_down()

    @work(thread=True)
    def _run_backtest(self) -> None:
        try:
            result = run_backtest(self._portfolio, self._config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backtest failed")
            self._call_from_thread_if_running(self._show_error, str(exc))
            return
        self._call_from_thread_if_running(self._apply_result, result)

    def _show_error(self, msg: str) -> None:
        self.query_one("#loading").display = False
        self.query_one("#error-msg", Label).update(f"Error: {msg}")

    def _apply_result(self, r: BacktestResult) -> None:
        self.query_one("#loading").display = False
        scroll = self.query_one("#bt-scroll")
        scroll.display = True

        if r.skipped_symbols:
            names = ", ".join(r.skipped_symbols)
            scroll.mount(
                Label(
                    f"  Skipped (no historical data): {names}",
                    classes="skipped-notice",
                )
            )

        self._mount_growth_chart(scroll, r)
        self._mount_annual_chart(scroll, r)
        self._mount_summary(scroll, r)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    @staticmethod
    def _downsample(
        values: list[float], dates: list[str], max_points: int = 500
    ) -> tuple[list[float], list[str]]:
        """Reduce data to at most *max_points* evenly-spaced samples."""
        n = len(values)
        if n <= max_points:
            return values, dates
        step = max(1, n // max_points)
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)
        return [values[i] for i in idx], [dates[i] for i in idx]

    def _mount_growth_chart(self, parent: Widget, r: BacktestResult) -> None:
        if not r.portfolio_values:
            return
        parent.mount(Label("  Portfolio Growth", classes="section-title"))
        chart = PlotextPlot(classes="price-chart")
        parent.mount(chart)
        plt = chart.plt

        pv, dates = self._downsample(r.portfolio_values, r.dates)
        bv, _ = self._downsample(r.benchmark_values, r.dates)

        x = list(range(len(pv)))
        plt.plot(x, pv, label="Portfolio", marker="braille")
        bench = self._config["benchmark"]
        plt.plot(x, bv, label=bench, marker="braille")

        # X-axis: show year labels
        step = max(1, len(x) // 8)
        tick_x = x[::step]
        tick_labels = [dates[i][:4] for i in tick_x]
        plt.xticks(tick_x, tick_labels)  # type: ignore[arg-type]

        all_vals = pv + bv
        ytick_vals, ytick_labels = nice_yticks(all_vals)
        if ytick_vals:
            plt.ylim(ytick_vals[0], ytick_vals[-1])
            plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
        plt.ylabel("Value ($)")
        plt.title("")

    def _mount_annual_chart(self, parent: Widget, r: BacktestResult) -> None:
        if not r.annual_years:
            return
        parent.mount(Label("  Annual Returns", classes="section-title"))
        chart = PlotextPlot(classes="annual-chart")
        parent.mount(chart)
        plt = chart.plt

        n = len(r.annual_years)
        x_base = list(range(1, n + 1))
        x_port = [v - 0.2 for v in x_base]
        x_bench = [v + 0.2 for v in x_base]
        bench = self._config["benchmark"]
        plt.bar(
            x_port,
            r.annual_portfolio_returns,
            label="Portfolio",
            width=0.35,
            minimum=0,
        )
        plt.bar(
            x_bench,
            r.annual_benchmark_returns,
            label=bench,
            width=0.35,
            minimum=0,
        )
        plt.xticks(x_base, r.annual_years)  # type: ignore[arg-type]

        all_rets = r.annual_portfolio_returns + r.annual_benchmark_returns
        ytick_vals, ytick_labels = nice_yticks(all_rets + [0])
        if ytick_vals:
            plt.ylim(ytick_vals[0], ytick_vals[-1])
            plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
        plt.ylabel("Return (%)")

        # Annotate bars with return values
        for i in range(n):
            pr = r.annual_portfolio_returns[i]
            br = r.annual_benchmark_returns[i]
            if pr != 0.0:
                plt.text(f"P:{pr:+.1f}%", x=x_port[i], y=pr)
            if br != 0.0:
                plt.text(f"B:{br:+.1f}%", x=x_bench[i], y=br)
        plt.title("")

    def _mount_summary(self, parent: Widget, r: BacktestResult) -> None:
        parent.mount(Label("  Backtest Summary", classes="section-title"))

        grid = Horizontal(classes="summary-grid")
        parent.mount(grid)

        # Portfolio column
        left = Vertical(classes="summary-col")
        grid.mount(left)
        left.mount(Static("[b]Portfolio[/b]"))
        _kv_row(left, "Final Value", f"${r.portfolio_final:,.2f}")
        _kv_row(left, "Total Contributions", f"${r.total_contributions:,.2f}")
        _kv_row(left, "CAGR", f"{r.portfolio_cagr:+.2f}%")
        _kv_row(left, "Max Drawdown", f"{r.portfolio_max_drawdown:.2f}%")
        _kv_row(left, "Sharpe Ratio", f"{r.portfolio_sharpe:.2f}")
        _kv_row(left, "Best Year", r.portfolio_best_year)
        _kv_row(left, "Worst Year", r.portfolio_worst_year)

        # Benchmark column
        right = Vertical(classes="summary-col")
        grid.mount(right)
        bench = self._config["benchmark"]
        right.mount(Static(f"[b]{bench}[/b]"))
        _kv_row(right, "Final Value", f"${r.benchmark_final:,.2f}")
        _kv_row(right, "Total Contributions", f"${r.total_contributions:,.2f}")
        _kv_row(right, "CAGR", f"{r.benchmark_cagr:+.2f}%")
        _kv_row(right, "Max Drawdown", f"{r.benchmark_max_drawdown:.2f}%")
        _kv_row(right, "Sharpe Ratio", f"{r.benchmark_sharpe:.2f}")
        _kv_row(right, "Best Year", r.benchmark_best_year)
        _kv_row(right, "Worst Year", r.benchmark_worst_year)
