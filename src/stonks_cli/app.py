"""Textual TUI for portfolio display."""

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Header, Label, Static

from stonks_cli.fetcher import PriceFetcher, exchange_label
from stonks_cli.models import Portfolio


class PortfolioApp(App):
    """Full-screen portfolio table with periodic price refresh."""

    TITLE = "Stonks"
    BINDINGS = [("q", "quit", "Quit")]

    CSS = """
    DataTable { height: auto; }
    .total {
        padding: 0 1;
        text-align: left;
        border-top: solid $accent;
    }
    .portfolio-header {
        padding: 1 1 0 1;
        color: $accent;
        text-style: bold;
    }
    #status {
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        portfolios: list[Portfolio],
        prices: dict[str, float],
        forex_rates: dict[str, dict[str, float]],
        sessions: dict[str, str] | None = None,
        refresh_interval: float = 5.0,
    ) -> None:
        super().__init__()
        self.portfolios = portfolios
        self.prices = prices
        self.forex_rates = forex_rates
        self.sessions = sessions or {}
        self.exchange_codes: dict[str, str] = {}
        self.refresh_interval = refresh_interval

    def compose(self) -> ComposeResult:
        yield Header()
        if len(self.portfolios) == 1:
            yield DataTable(zebra_stripes=True)
            yield Static("", id="total", classes="total")
        else:
            with VerticalScroll():
                for i, portfolio in enumerate(self.portfolios):
                    label = portfolio.name or f"Portfolio {i + 1}"
                    yield Label(label, id=f"header-{i}", classes="portfolio-header")
                    yield DataTable(zebra_stripes=True, id=f"table-{i}")
                    yield Static("", id=f"total-{i}", classes="total")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        cols = (
            "Instrument",
            "Exchange",
            "Qty",
            "Avg Cost",
            "Last Price",
            "Mkt Value",
            "Unrealized P&L",
        )
        if len(self.portfolios) == 1:
            self.query_one(DataTable).add_columns(*cols)
        else:
            for i in range(len(self.portfolios)):
                self.query_one(f"#table-{i}", DataTable).add_columns(*cols)
        self._populate_tables()
        self._refresh_prices()
        self.set_interval(self.refresh_interval, self._refresh_prices)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _populate_tables(self) -> None:
        try:
            status = self.query_one("#status", Static)
            if not self.prices:
                status.update("Obtaining market data...")
            else:
                status.update("")
        except NoMatches:
            pass
        if len(self.portfolios) == 1:
            self._populate_single()
        else:
            for i, portfolio in enumerate(self.portfolios):
                self._populate_for(i, portfolio)

    def _populate_single(self) -> None:
        try:
            table = self.query_one(DataTable)
            total_widget = self.query_one("#total", Static)
        except NoMatches:
            return
        self._render_rows(table, self.portfolios[0])
        self._update_total_widget(total_widget, self.portfolios[0])

    def _populate_for(self, i: int, portfolio: Portfolio) -> None:
        try:
            table = self.query_one(f"#table-{i}", DataTable)
            total_widget = self.query_one(f"#total-{i}", Static)
        except NoMatches:
            return
        self._render_rows(table, portfolio)
        self._update_total_widget(total_widget, portfolio)

    def _render_rows(self, table: DataTable, portfolio: Portfolio) -> None:
        saved_cursor = table.cursor_coordinate
        table.clear()
        rates = self.forex_rates.get(portfolio.base_currency, {})
        for pos in portfolio.positions:
            last = self.prices.get(pos.symbol)
            if last is not None:
                mkt_value = pos.market_value(last)
                pnl = pos.unrealized_pnl(last)
                sign = "+" if pnl >= 0 else ""
                pnl_text = Text(
                    f"{sign}{pnl:,.2f}",
                    style="bold green" if pnl >= 0 else "bold red",
                )
                session = self.sessions.get(pos.symbol, "regular")
                price_cell: Text | str
                if session == "pre":
                    price_cell = Text(f"{last:.2f} ").append("PRE", style="bold yellow")
                elif session == "post":
                    price_cell = Text(f"{last:.2f} ").append("AH", style="bold cyan")
                elif session == "closed":
                    price_cell = Text(f"{last:.2f} ").append("CLS", style="bold red")
                else:
                    price_cell = f"{last:.2f}"
                table.add_row(
                    pos.symbol,
                    exchange_label(pos.symbol, self.exchange_codes.get(pos.symbol)),
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    price_cell,
                    f"{mkt_value:,.2f}",
                    pnl_text,
                )
            else:
                table.add_row(
                    pos.symbol,
                    exchange_label(pos.symbol, self.exchange_codes.get(pos.symbol)),
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    "N/A",
                    "N/A",
                    "N/A",
                )
        for cash_pos in portfolio.cash:
            rate = rates.get(cash_pos.currency)
            if rate is not None:
                mkt_value = cash_pos.amount * rate
                price_cell = (
                    f"{rate:.4f}"
                    if cash_pos.currency != portfolio.base_currency
                    else "1.0000"
                )
                table.add_row(
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    price_cell,
                    f"{mkt_value:,.2f}",
                    "--",
                )
            else:
                table.add_row(
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    "N/A",
                    "N/A",
                    "--",
                )
        table.move_cursor(row=saved_cursor.row, column=saved_cursor.column)

    def _update_total_widget(self, widget: Static, portfolio: Portfolio) -> None:
        rates = self.forex_rates.get(portfolio.base_currency, {})
        missing_price = any(
            self.prices.get(pos.symbol) is None for pos in portfolio.positions
        )
        missing_rate = any(
            rates.get(p.currency) is None for p in portfolio.positions
        ) or any(rates.get(c.currency) is None for c in portfolio.cash)
        if missing_price or missing_rate:
            widget.update(
                Text(f"Total ({portfolio.base_currency})  ").append("N/A", style="bold")
            )
            return
        stock_total = sum(
            pos.market_value(self.prices[pos.symbol]) * rates[pos.currency]
            for pos in portfolio.positions
        )
        cash_total = sum(
            cash_pos.amount * rates[cash_pos.currency] for cash_pos in portfolio.cash
        )
        base = portfolio.base_currency
        widget.update(
            Text(f"Total ({base})  ").append(
                f"{stock_total + cash_total:,.2f}", style="bold"
            )
        )

    # ------------------------------------------------------------------
    # Price refresh
    # ------------------------------------------------------------------

    @work(thread=True)
    def _refresh_prices(self) -> None:
        fetcher = PriceFetcher()
        all_symbols = list(
            {p.symbol for portfolio in self.portfolios for p in portfolio.positions}
        )
        extended = fetcher.fetch_extended_prices(all_symbols)
        new_prices = {sym: price for sym, (price, _) in extended.items()}
        new_sessions = {sym: sess for sym, (_, sess) in extended.items()}

        # Fall back to daily batch prices for symbols that had no 1-minute data.
        missing = [s for s in all_symbols if s not in new_prices]
        if missing:
            fallback = fetcher.fetch_prices(missing)
            new_prices.update(fallback)
            new_sessions.update({sym: fetcher.current_session(sym) for sym in fallback})

        # Final fallback: fetch individually for symbols still missing after
        # the batch attempt (cross-exchange DataFrame alignment can silently
        # drop tickers from the batch result).
        still_missing = [s for s in missing if s not in new_prices]
        for sym in still_missing:
            price = fetcher.fetch_price_single(sym)
            if price is not None:
                new_prices[sym] = price
                new_sessions[sym] = fetcher.current_session(sym)
        new_exchange_codes = fetcher.fetch_exchange_names(all_symbols)
        all_currencies = list(
            {p.currency for portfolio in self.portfolios for p in portfolio.positions}
            | {c.currency for portfolio in self.portfolios for c in portfolio.cash}
        )
        new_forex: dict[str, dict[str, float]] = {}
        for base in {p.base_currency for p in self.portfolios}:
            new_forex[base] = fetcher.fetch_forex_rates(all_currencies, base=base)
        self.call_from_thread(
            self._apply_prices, new_prices, new_forex, new_sessions, new_exchange_codes
        )

    def _apply_prices(
        self,
        prices: dict[str, float],
        forex_rates: dict[str, dict[str, float]] | None = None,
        sessions: dict[str, str] | None = None,
        exchange_codes: dict[str, str] | None = None,
    ) -> None:
        self.prices = prices
        if forex_rates is not None:
            self.forex_rates = forex_rates
        if sessions is not None:
            self.sessions = sessions
        if exchange_codes is not None:
            self.exchange_codes = exchange_codes
        self._populate_tables()
