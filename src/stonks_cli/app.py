"""Textual TUI for portfolio display."""

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from stonks_cli.fetcher import PriceFetcher
from stonks_cli.models import Portfolio


class PortfolioApp(App):
    """Full-screen portfolio table with periodic price refresh."""

    TITLE = "Portfolio"
    BINDINGS = [("q", "quit", "Quit")]

    CSS = """
    DataTable { height: auto; }
    #total {
        padding: 0 1;
        text-align: left;
        border-top: solid $accent;
    }
    """

    def __init__(
        self,
        portfolio: Portfolio,
        prices: dict[str, float],
        forex_rates: dict[str, float],
        refresh_interval: float = 5.0,
    ) -> None:
        super().__init__()
        self.portfolio = portfolio
        self.prices = prices
        self.forex_rates = forex_rates
        self.refresh_interval = refresh_interval

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(zebra_stripes=True)
        yield Static("", id="total")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(
            "Instrument", "Qty", "Avg Cost", "Last Price", "Mkt Value", "Unrealized P&L"
        )
        self._populate_table()
        self._refresh_prices()
        self.set_interval(self.refresh_interval, self._refresh_prices)

    def _populate_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for pos in self.portfolio.positions:
            last = self.prices.get(pos.symbol)
            if last is not None:
                mkt_value = pos.market_value(last)
                pnl = pos.unrealized_pnl(last)
                sign = "+" if pnl >= 0 else ""
                pnl_text = Text(
                    f"{sign}{pnl:,.2f}",
                    style="bold green" if pnl >= 0 else "bold red",
                )
                table.add_row(
                    pos.symbol,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    f"{last:.2f}",
                    f"{mkt_value:,.2f}",
                    pnl_text,
                )
            else:
                table.add_row(
                    pos.symbol,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    "N/A",
                    "N/A",
                    "N/A",
                )
        self._update_total()

    def _update_total(self) -> None:
        total = sum(
            pos.market_value(last) * rate
            for pos in self.portfolio.positions
            if (last := self.prices.get(pos.symbol)) is not None
            if (rate := self.forex_rates.get(pos.currency)) is not None
        )
        self.query_one("#total", Static).update(
            Text("Total (USD)  ").append(f"{total:,.2f}", style="bold")
        )

    @work(thread=True)
    def _refresh_prices(self) -> None:
        fetcher = PriceFetcher()
        symbols = [p.symbol for p in self.portfolio.positions]
        new_prices = fetcher.fetch_prices(symbols)
        currencies = list({p.currency for p in self.portfolio.positions})
        new_forex = fetcher.fetch_forex_rates(currencies)
        self.call_from_thread(self._apply_prices, new_prices, new_forex)

    def _apply_prices(
        self,
        prices: dict[str, float],
        forex_rates: dict[str, float] | None = None,
    ) -> None:
        self.prices = prices
        if forex_rates is not None:
            self.forex_rates = forex_rates
        self._populate_table()
