"""Stock detail screen with charts and financial data."""

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, LoadingIndicator, Static
from textual_plotext import PlotextPlot

from stonks_cli.helpers import ThreadGuardMixin, nice_yticks
from stonks_cli.stock_detail import StockDetail, StockDetailFetcher

logger = logging.getLogger(__name__)

# Recommendation chart colors (RGB)
_COLOR_STRONG_BUY = (0, 100, 0)
_COLOR_BUY = (50, 205, 50)
_COLOR_HOLD = (255, 165, 0)
_COLOR_SELL = (255, 69, 0)
_COLOR_STRONG_SELL = (139, 0, 0)


def _kv_row(container: Widget, label: str, value: str) -> None:
    """Mount a single label/value row into *container*."""
    row = Horizontal(classes="kv-row")
    container.mount(row)
    row.mount(Static(label, classes="kv-label"))
    row.mount(Static(value, classes="kv-value"))


class StockDetailScreen(ThreadGuardMixin, Screen, inherit_bindings=False):
    """Full-screen detail view for a single stock."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", priority=True),
        Binding("up", "scroll_up", "Scroll Up", show=True),
        Binding("down", "scroll_down", "Scroll Down", show=True),
        Binding("pageup", "page_up", "Page Up", show=True),
        Binding("pagedown", "page_down", "Page Down", show=True),
        Binding("g", "chart", "Chart"),
    ]

    CSS = """
    StockDetailScreen {
        background: $surface;
    }
    #company-name {
        padding: 0 1;
        color: $text-muted;
    }
    #detail-scroll {
        scrollbar-gutter: stable;
    }
    .section-title {
        padding: 1 1 0 1;
        text-style: bold;
        color: $accent;
    }
    .summary-grid, .analyst-row, .stats-row, .perf-row {
        height: auto;
        padding: 0 1;
    }
    .summary-col, .stats-col, .analyst-col {
        width: 1fr;
        height: auto;
    }
    .analyst-col {
        padding: 0 1;
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
    .price-chart {
        height: 18;
        padding: 0 1;
    }
    .perf-card {
        width: 1fr;
        height: auto;
        border: solid $accent;
        padding: 0 1;
        margin: 0 1;
    }
    .perf-card-title {
        text-style: bold;
    }
    .perf-positive {
        color: green;
        text-style: bold;
    }
    .perf-negative {
        color: red;
        text-style: bold;
    }
    """

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self._modal = True
        self._symbol = symbol

    def compose(self) -> ComposeResult:
        yield Label(f"  {self._symbol} -- Details", classes="section-title")
        yield Label("", id="company-name")
        yield LoadingIndicator(id="loading")
        yield Label("", id="error-msg")
        yield VerticalScroll(id="detail-scroll")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail-scroll").display = False
        self._load_detail()

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#detail-scroll", VerticalScroll)

    def action_scroll_up(self) -> None:
        self._scroll().scroll_up()

    def action_scroll_down(self) -> None:
        self._scroll().scroll_down()

    def action_page_up(self) -> None:
        self._scroll().scroll_page_up()

    def action_page_down(self) -> None:
        self._scroll().scroll_page_down()

    @work(thread=True)
    def _load_detail(self) -> None:
        try:
            detail = StockDetailFetcher().fetch_stock_detail(self._symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error fetching detail for %s", self._symbol)
            self._call_from_thread_if_running(self._show_error, str(exc))
            return
        self._call_from_thread_if_running(self._apply_detail, detail)

    def _show_error(self, msg: str) -> None:
        self.query_one("#loading").display = False
        self.query_one("#error-msg", Label).update(f"Error: {msg}")

    def _apply_detail(self, d: StockDetail) -> None:
        self.query_one("#loading").display = False
        self.query_one("#company-name", Label).update(f"  {d.name}")
        scroll = self.query_one("#detail-scroll")
        scroll.display = True

        # 0. Performance overview
        self._mount_performance(scroll, d)
        # 1. Financial summary
        self._mount_summary(scroll, d)
        # 2. Price charts
        self._mount_price_chart(scroll, d)
        # 3. Earnings trends (EPS + Revenue vs Earnings)
        self._mount_earnings(scroll, d)
        # 4. Analyst insights
        self._mount_analyst(scroll, d)
        # 5. Statistics
        self._mount_statistics(scroll, d)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _mount_performance(self, parent: Widget, d: StockDetail) -> None:
        if not d.performance:
            return
        parent.mount(Label("  Performance Overview", classes="section-title"))

        items = list(d.performance.items())
        # Two cards per row
        row: Horizontal | None = None
        for idx, (label, (stock_ret, sp_ret)) in enumerate(items):
            if idx % 2 == 0:
                row = Horizontal(classes="perf-row")
                parent.mount(row)
            assert row is not None
            card = Vertical(classes="perf-card")
            row.mount(card)
            card.mount(Static(label, classes="perf-card-title"))

            neg = "perf-negative"
            pos = "perf-positive"
            stock_cls = neg if stock_ret.startswith("-") else pos
            sp_cls = neg if sp_ret.startswith("-") else pos

            stock_row = Horizontal(classes="kv-row")
            card.mount(stock_row)
            stock_row.mount(Static(d.symbol, classes="kv-label"))
            stock_row.mount(Static(stock_ret, classes=stock_cls))

            sp_row = Horizontal(classes="kv-row")
            card.mount(sp_row)
            sp_row.mount(Static("S&P 500", classes="kv-label"))
            sp_row.mount(Static(sp_ret, classes=sp_cls))

    def _mount_price_chart(self, parent: Widget, d: StockDetail) -> None:
        if not d.price_histories:
            return
        for label, (dates, closes) in d.price_histories.items():
            if not closes:
                continue
            chart = PlotextPlot(classes="price-chart")
            parent.mount(Label(f"  Price ({label})", classes="section-title"))
            parent.mount(chart)
            plt = chart.plt
            x = list(range(len(closes)))
            plt.plot(x, closes, marker="braille")
            step = max(1, len(x) // 6)
            tick_x = x[::step]
            # Map each period label to its date-slice function.
            # 1 Day uses HH:MM (already formatted by stock_detail).
            # All others slice YYYY-MM-DD to an appropriate granularity.
            # Unrecognised periods fall back to MM-DD ([5:]).
            _date_slicer = {
                "1 Day": lambda d: d,
                "1 Month": lambda d: d[5:],
                "1 Year": lambda d: d[:7],
                "5 Years": lambda d: d[:4],
            }
            slicer = _date_slicer.get(label, lambda d: d[5:])
            tick_labels = [slicer(dates[i]) for i in tick_x]
            plt.xticks(tick_x, tick_labels)  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
            ytick_vals, ytick_labels = nice_yticks(closes)
            plt.ylim(ytick_vals[0], ytick_vals[-1])
            plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
            plt.ylabel("Price")
            plt.title("")

    def _mount_summary(self, parent: Widget, d: StockDetail) -> None:
        parent.mount(Label("  Financial Summary", classes="section-title"))
        items = list(d.summary.items())
        mid = (len(items) + 1) // 2
        left_items = items[:mid]
        right_items = items[mid:]

        grid = Horizontal(classes="summary-grid")
        parent.mount(grid)

        left = Vertical(classes="summary-col")
        right = Vertical(classes="summary-col")
        grid.mount(left)
        grid.mount(right)

        for label, value in left_items:
            _kv_row(left, label, value)

        for label, value in right_items:
            _kv_row(right, label, value)

    def _mount_earnings(self, parent: Widget, d: StockDetail) -> None:
        if not d.eps_quarters and not d.rev_quarters:
            return
        parent.mount(Label("  Earnings Trends", classes="section-title"))
        self._mount_eps_chart(parent, d)
        self._mount_rev_chart(parent, d)

    def _mount_eps_chart(self, parent: Widget, d: StockDetail) -> None:
        if not d.eps_quarters:
            return
        eps_chart = PlotextPlot(classes="price-chart")
        parent.mount(eps_chart)
        plt = eps_chart.plt
        n = len(d.eps_quarters)
        actual = [v if v is not None else 0.0 for v in d.eps_actual]
        estimate = [v if v is not None else 0.0 for v in d.eps_estimate]
        labels = list(d.eps_quarters)

        # Add next quarter estimate if available
        if d.next_eps_estimate is not None:
            labels.append("Next(est)")
            actual.append(0.0)
            estimate.append(d.next_eps_estimate)
            n += 1

        x = list(range(1, n + 1))
        plt.bar(x, actual, label="Actual EPS", width=0.4)
        plt.bar(x, estimate, label="Estimate", width=0.4)
        plt.xticks(x, labels)  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
        ytick_vals, ytick_labels = nice_yticks(actual + estimate)
        plt.ylim(ytick_vals[0], ytick_vals[-1])
        plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
        plt.title("Earnings Per Share")

        # Annotate bars: actual on top, estimate below
        for i in range(n):
            a = actual[i]
            e = estimate[i]
            top = max(abs(a), abs(e), 0.01)
            if a != 0.0:
                plt.text(f"A:{a:.2f}", x=i + 1, y=top)
            if e != 0.0:
                plt.text(f"E:{e:.2f}", x=i + 1, y=top - abs(top) * 0.6)

    def _mount_rev_chart(self, parent: Widget, d: StockDetail) -> None:
        if not d.rev_quarters:
            return
        rev_chart = PlotextPlot(classes="price-chart")
        parent.mount(rev_chart)
        plt = rev_chart.plt
        n = len(d.rev_quarters)
        x = list(range(1, n + 1))
        plt.bar(x, d.rev_values, label="Revenue ($B)", width=0.4)
        plt.bar(x, d.earn_values, label="Net Income ($B)", width=0.4)
        plt.xticks(x, d.rev_quarters)  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
        ytick_vals, ytick_labels = nice_yticks(d.rev_values + d.earn_values)
        plt.ylim(ytick_vals[0], ytick_vals[-1])
        plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
        plt.title("Revenue vs Earnings")

        # Annotate: revenue on top, net income below
        for i in range(n):
            rv = d.rev_values[i]
            ev = d.earn_values[i]
            if rv != 0.0:
                plt.text(f"R:{rv:.1f}B", x=i + 1, y=rv)
            if ev != 0.0:
                plt.text(f"NI:{ev:.1f}B", x=i + 1, y=rv * 0.2)

    def _mount_analyst(self, parent: Widget, d: StockDetail) -> None:
        no_data = (
            not d.price_targets
            and not d.recommendations
            and d.recommendation_key == "N/A"
        )
        if no_data:
            return
        parent.mount(Label("  Analyst Insights", classes="section-title"))

        # Price targets + Latest rating (side by side)
        row = Horizontal(classes="analyst-row")
        parent.mount(row)

        if d.price_targets:
            col = Vertical(classes="analyst-col")
            row.mount(col)
            col.mount(Static("[b]Analyst Price Targets[/b]"))
            for k in ("low", "current", "mean", "median", "high"):
                if k in d.price_targets:
                    col.mount(Static(f"  {k.title()}: {d.price_targets[k]:.2f}"))

        col3 = Vertical(classes="analyst-col")
        row.mount(col3)
        col3.mount(Static("[b]Latest Rating[/b]"))
        rating = d.recommendation_key.replace("_", " ").title()
        col3.mount(Static(f"  Rating: {rating}"))
        col3.mount(Static(f"  Analysts: {d.num_analysts}"))

        # Recommendations chart (own row)
        if d.recommendations:
            chart = PlotextPlot(classes="price-chart")
            parent.mount(chart)
            plt = chart.plt
            periods = [r["period"] for r in d.recommendations]
            strong_buy = [int(r.get("strongBuy", 0)) for r in d.recommendations]
            buy = [int(r.get("buy", 0)) for r in d.recommendations]
            hold = [int(r.get("hold", 0)) for r in d.recommendations]
            sell = [int(r.get("sell", 0)) for r in d.recommendations]
            strong_sell = [int(r.get("strongSell", 0)) for r in d.recommendations]
            x = list(range(1, len(periods) + 1))
            plt.stacked_bar(  # type: ignore[call-arg]  # plotext stubs missing labels/color params
                x,
                [strong_buy, buy, hold, sell, strong_sell],
                labels=["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
                color=[  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
                    _COLOR_STRONG_BUY,
                    _COLOR_BUY,
                    _COLOR_HOLD,
                    _COLOR_SELL,
                    _COLOR_STRONG_SELL,
                ],
            )
            plt.xticks(x, periods)  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
            plt.yticks([], [])  # type: ignore[arg-type]  # plotext stubs accept str but lists work at runtime
            plt.title("Analyst Recommendations")

    def action_chart(self) -> None:
        """Open the candlestick chart for this symbol."""
        from stonks_cli.chart import CandleChartScreen

        self.app.push_screen(CandleChartScreen(self._symbol))

    def _mount_statistics(self, parent: Widget, d: StockDetail) -> None:
        if not d.valuation and not d.financials:
            return
        parent.mount(Label("  Statistics", classes="section-title"))
        row = Horizontal(classes="stats-row")
        parent.mount(row)

        if d.valuation:
            col = Vertical(classes="stats-col")
            row.mount(col)
            col.mount(Static("[b]Valuation Measures[/b]"))
            for label, value in d.valuation.items():
                _kv_row(col, label, value)

        if d.financials:
            col = Vertical(classes="stats-col")
            row.mount(col)
            col.mount(Static("[b]Financial Highlights[/b]"))
            for label, value in d.financials.items():
                _kv_row(col, label, value)
