"""Portfolio history screen: value of the current holdings over time."""

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, LoadingIndicator
from textual_plotext import PlotextPlot

from stonks_cli.backtest_detail import BacktestScreen
from stonks_cli.helpers import (
    DETAIL_SCREEN_CSS,
    SCROLL_BINDINGS,
    ScrollableScreenMixin,
    ThreadGuardMixin,
    kv_row,
    nice_yticks,
)
from stonks_cli.history import (
    DEFAULT_HISTORY_RANGE,
    HISTORY_RANGES,
    PortfolioHistory,
    fetch_portfolio_history,
)
from stonks_cli.models import Portfolio

logger = logging.getLogger(__name__)


class PortfolioHistoryScreen(
    ScrollableScreenMixin, ThreadGuardMixin, Screen, inherit_bindings=False
):
    """Full-screen chart of a portfolio's value over a selectable range."""

    _scroll_id = "hist-scroll"

    BINDINGS = [
        *SCROLL_BINDINGS,
        Binding("plus,equals_sign", "shorter_range", "Shorter"),
        Binding("minus,underscore", "longer_range", "Longer"),
    ]

    CSS = (
        DETAIL_SCREEN_CSS
        + """
    PortfolioHistoryScreen {
        background: $surface;
    }
    #hist-title {
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }
    #hist-subtitle {
        padding: 0 1;
        color: $text-muted;
    }
    #hist-scroll {
        scrollbar-gutter: stable;
    }
    .price-chart {
        height: 30;
        padding: 0 1;
    }
    .skipped-notice {
        padding: 0 1;
        color: $warning;
    }
    """
    )

    def __init__(self, portfolio: Portfolio) -> None:
        super().__init__()
        self._modal = True
        self._portfolio = portfolio
        self._range = DEFAULT_HISTORY_RANGE

    def compose(self) -> ComposeResult:
        pname = self._portfolio.name or "Portfolio"
        yield Label(f"  Portfolio history: {pname}", id="hist-title")
        yield Label("", id="hist-subtitle")
        yield LoadingIndicator(id="loading")
        yield Label("", id="error-msg", markup=False)
        yield VerticalScroll(id="hist-scroll")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    # ------------------------------------------------------------------
    # Range selection
    # ------------------------------------------------------------------

    def action_shorter_range(self) -> None:
        if self._range > 0:
            self._range -= 1
            self._load()

    def action_longer_range(self) -> None:
        if self._range < len(HISTORY_RANGES) - 1:
            self._range += 1
            self._load()

    def _load(self) -> None:
        label, period = HISTORY_RANGES[self._range]
        ranges = "  ".join(
            f"[b reverse] {lbl} [/]" if i == self._range else lbl
            for i, (lbl, _) in enumerate(HISTORY_RANGES)
        )
        self.query_one("#hist-subtitle", Label).update(
            f"  {ranges}  |  Current holdings at historical prices, in "
            f"{self._portfolio.base_currency}  |  +/- change range"
        )
        self.query_one("#loading").display = True
        self.query_one("#error-msg", Label).update("")
        scroll = self._scroll()
        scroll.display = False
        scroll.remove_children()
        self._fetch(period)

    @work(thread=True, exclusive=True, group="history")
    def _fetch(self, period: str) -> None:
        try:
            history = fetch_portfolio_history(self._portfolio, period)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Portfolio history failed")
            self._call_from_thread_if_running(self._show_error, period, str(exc))
            return
        self._call_from_thread_if_running(self._apply_history, period, history)

    def _is_current(self, period: str) -> bool:
        return HISTORY_RANGES[self._range][1] == period

    def _show_error(self, period: str, msg: str) -> None:
        if not self._is_current(period):
            return
        self.query_one("#loading").display = False
        self.query_one("#error-msg", Label).update(f"Error: {msg}")

    def _apply_history(self, period: str, history: PortfolioHistory) -> None:
        if not self._is_current(period):
            return
        self.query_one("#loading").display = False
        scroll = self._scroll()
        scroll.display = True
        if history.skipped:
            scroll.mount(
                Label(
                    "  Not included (no price history): " + ", ".join(history.skipped),
                    classes="skipped-notice",
                )
            )
        self._mount_chart(scroll, history)
        self._mount_summary(scroll, history)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _mount_chart(self, parent: Widget, h: PortfolioHistory) -> None:
        parent.mount(Label("  Portfolio Value", classes="section-title"))
        chart = PlotextPlot(classes="price-chart")
        parent.mount(chart)
        plt = chart.plt

        values, dates = BacktestScreen._downsample(h.values, h.dates)
        x = list(range(len(values)))
        plt.plot(x, values, label="Value", marker="braille")
        plt.plot(x, [h.cost] * len(x), label="Cost", marker="braille")

        step = max(1, len(x) // 8)
        tick_x = x[::step]
        plt.xticks(tick_x, [dates[i] for i in tick_x])  # type: ignore[arg-type]

        ytick_vals, ytick_labels = nice_yticks(values + [h.cost])
        if ytick_vals:
            plt.ylim(ytick_vals[0], ytick_vals[-1])
            plt.yticks(ytick_vals, ytick_labels)  # type: ignore[arg-type]
        plt.ylabel(f"Value ({self._portfolio.base_currency})")
        plt.title("")

    def _mount_summary(self, parent: Widget, h: PortfolioHistory) -> None:
        parent.mount(Label("  Summary", classes="section-title"))
        box = Vertical(classes="summary-col")
        parent.mount(box)
        pct = f" ({h.change_pct:+.2f}%)" if h.change_pct is not None else ""
        kv_row(box, f"Value on {h.dates[0]}", f"{h.start_value:,.2f}")
        kv_row(box, f"Value on {h.dates[-1]}", f"{h.end_value:,.2f}")
        kv_row(box, "Change", f"{h.change:+,.2f}{pct}")
        kv_row(box, "Highest", f"{h.high:,.2f}")
        kv_row(box, "Lowest", f"{h.low:,.2f}")
        kv_row(box, "Max Drawdown", f"{h.max_drawdown:.2f}%")
        kv_row(box, "Cost (holdings + cash)", f"{h.cost:,.2f}")
