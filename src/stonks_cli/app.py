"""Textual TUI for portfolio display."""

import threading

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from stonks_cli.detail import StockDetailScreen
from stonks_cli.fetcher import PriceFetcher, exchange_label
from stonks_cli.models import Portfolio, WatchlistItem
from stonks_cli.storage import PortfolioStore

# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------

_MODAL_CSS = """
{cls} {{ align: center middle; }}
{cls} > Vertical {{
    width: 52;
    height: auto;
    border: solid $accent;
    padding: 1 2;
    background: $surface;
}}
{cls} .field-label {{ margin-top: 1; }}
{cls} .buttons {{ height: auto; margin-top: 1; }}
{cls} Button {{ width: 1fr; }}
{cls} .error {{ color: $error; height: 1; }}
"""


class _TypeSelectScreen(ModalScreen[str | None]):
    """Ask whether the new position is equity or cash."""

    CSS = _MODAL_CSS.format(cls="_TypeSelectScreen")

    def __init__(self, portfolio_name: str = "") -> None:
        super().__init__()
        self._portfolio_name = portfolio_name

    def compose(self) -> ComposeResult:
        with Vertical():
            if self._portfolio_name:
                yield Label(f"Portfolio: {self._portfolio_name}")
            yield Label("What type of position?")
            yield Button("Equity/Crypto/ETF", id="equity")
            yield Button("Cash", id="cash")
            yield Button("Watch", id="watch")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class _EquityFormScreen(ModalScreen[dict | None]):
    """Form for adding or editing an equity position."""

    CSS = _MODAL_CSS.format(cls="_EquityFormScreen")

    def __init__(
        self,
        title: str = "Add Equity Position",
        symbol: str = "",
        qty: str = "",
        avg_cost: str = "",
        currency: str = "USD",
    ) -> None:
        super().__init__()
        self._title = title
        self._symbol = symbol
        self._qty = qty
        self._avg_cost = avg_cost
        self._currency = currency

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Symbol", classes="field-label")
            yield Input(value=self._symbol, placeholder="e.g. AAPL", id="symbol")
            yield Label("Quantity (integer)", classes="field-label")
            yield Input(value=self._qty, placeholder="e.g. 10", id="qty")
            yield Label("Avg Cost", classes="field-label")
            yield Input(value=self._avg_cost, placeholder="e.g. 150.00", id="avg_cost")
            yield Label("Currency", classes="field-label")
            yield Input(value=self._currency, placeholder="USD", id="currency")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._submit()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def _submit(self) -> None:
        symbol = self.query_one("#symbol", Input).value.strip().upper()
        qty_str = self.query_one("#qty", Input).value.strip()
        avg_cost_str = self.query_one("#avg_cost", Input).value.strip()
        currency = self.query_one("#currency", Input).value.strip().upper() or "USD"
        err = self.query_one("#error", Label)
        if not symbol:
            err.update("Symbol is required")
            return
        try:
            qty = int(qty_str)
            if qty <= 0:
                raise ValueError
        except ValueError:
            err.update("Quantity must be a positive integer")
            return
        try:
            avg_cost = float(avg_cost_str)
            if avg_cost <= 0:
                raise ValueError
        except ValueError:
            err.update("Avg cost must be a positive number")
            return
        self.dismiss(
            {"symbol": symbol, "qty": qty, "avg_cost": avg_cost, "currency": currency}
        )


class _CashFormScreen(ModalScreen[dict | None]):
    """Form for adding or editing a cash position."""

    CSS = _MODAL_CSS.format(cls="_CashFormScreen")

    def __init__(
        self,
        title: str = "Add Cash Position",
        currency: str = "",
        amount: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._currency = currency
        self._amount = amount

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Currency", classes="field-label")
            yield Input(value=self._currency, placeholder="e.g. EUR", id="currency")
            yield Label("Amount", classes="field-label")
            yield Input(value=self._amount, placeholder="e.g. 1000.00", id="amount")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._submit()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def _submit(self) -> None:
        currency = self.query_one("#currency", Input).value.strip().upper()
        amount_str = self.query_one("#amount", Input).value.strip()
        err = self.query_one("#error", Label)
        if not currency:
            err.update("Currency is required")
            return
        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except ValueError:
            err.update("Amount must be a positive number")
            return
        self.dismiss({"currency": currency, "amount": amount})


class _WatchFormScreen(ModalScreen[str | None]):
    """Form for adding or editing a watchlist item (symbol only)."""

    CSS = _MODAL_CSS.format(cls="_WatchFormScreen")

    def __init__(self, title: str = "Add Watch Item", symbol: str = "") -> None:
        super().__init__()
        self._title = title
        self._symbol = symbol

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Symbol", classes="field-label")
            yield Input(value=self._symbol, placeholder="e.g. TSLA", id="symbol")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._submit()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def _submit(self) -> None:
        symbol = self.query_one("#symbol", Input).value.strip().upper()
        err = self.query_one("#error", Label)
        if not symbol:
            err.update("Symbol is required")
            return
        self.dismiss(symbol)


class _ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    CSS = _MODAL_CSS.format(cls="_ConfirmScreen").replace(
        "border: solid $accent", "border: solid $error"
    )

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._message)
            with Horizontal(classes="buttons"):
                yield Button("Remove", variant="error", id="yes")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


class PortfolioApp(App):
    """Full-screen portfolio table with periodic price refresh."""

    TITLE = "Stonks"
    BINDINGS = [
        ("q", "quit", "Quit"),
        Binding("tab", "focus_next", "Next", show=True, priority=True),
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("r", "remove", "Remove"),
    ]

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
        prev_closes: dict[str, float] | None = None,
        refresh_interval: float = 60.0,
        stores: list[PortfolioStore] | None = None,
    ) -> None:
        super().__init__()
        self.portfolios = portfolios
        self.prices = prices
        self.forex_rates = forex_rates
        self.sessions = sessions or {}
        self.prev_closes: dict[str, float] = prev_closes or {}
        self.exchange_codes: dict[str, str] = {}
        self.refresh_interval = refresh_interval
        self.stores = stores or []
        # Sort state keyed by table widget id ("" for the single-portfolio table).
        self._sort_column: dict[str, int] = {}
        self._sort_reverse: dict[str, bool] = {}
        self._refresh_lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        if len(self.portfolios) == 1:
            yield DataTable(zebra_stripes=True, cursor_type="row")
            yield Static("", id="total", classes="total")
        else:
            with VerticalScroll():
                for i, portfolio in enumerate(self.portfolios):
                    label = portfolio.name or f"Portfolio {i + 1}"
                    yield Label(label, id=f"header-{i}", classes="portfolio-header")
                    yield DataTable(
                        zebra_stripes=True, cursor_type="row", id=f"table-{i}"
                    )
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
            "Daily Chg",
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
    # Portfolio editing helpers
    # ------------------------------------------------------------------

    def _get_active_table_and_index(self) -> tuple[DataTable, int] | None:
        """Return the focused DataTable and its portfolio index, or None."""
        focused = self.focused
        if isinstance(focused, DataTable):
            return focused, self._table_to_portfolio_index(focused)
        try:
            table = self.query_one(DataTable)
            return table, 0
        except NoMatches:
            return None

    def _table_to_portfolio_index(self, table: DataTable) -> int:
        if len(self.portfolios) == 1:
            return 0
        tid = table.id or ""
        if tid.startswith("table-"):
            try:
                return int(tid[6:])
            except ValueError:
                pass
        return 0

    def _save(self, idx: int) -> None:
        if idx < len(self.stores):
            self.stores[idx].save(self.portfolios[idx])

    def _pname(self, idx: int) -> str:
        """Return a display name for portfolio *idx*."""
        p = self.portfolios[idx]
        return p.name or f"Portfolio {idx + 1}"

    def action_add(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        _, idx = active
        pname = self._pname(idx)

        def on_type(pos_type: str | None) -> None:
            if pos_type == "equity":

                def on_equity(result: dict | None) -> None:
                    if result is None:
                        return
                    portfolio = self.portfolios[idx]
                    is_new = portfolio.get_position(result["symbol"]) is None
                    portfolio.add_position(
                        result["symbol"], result["qty"], result["avg_cost"]
                    )
                    if is_new:
                        pos = portfolio.get_position(result["symbol"])
                        if pos:
                            pos.currency = result["currency"]
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _EquityFormScreen(title=f"[{pname}] Add Equity Position"),
                    on_equity,
                )
            elif pos_type == "cash":

                def on_cash(result: dict | None) -> None:
                    if result is None:
                        return
                    try:
                        self.portfolios[idx].add_cash(
                            result["currency"], result["amount"]
                        )
                    except ValueError:
                        return
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _CashFormScreen(title=f"[{pname}] Add Cash Position"),
                    on_cash,
                )
            elif pos_type == "watch":

                def on_watch(symbol: str | None) -> None:
                    if symbol is None:
                        return
                    portfolio = self.portfolios[idx]
                    if any(w.symbol == symbol for w in portfolio.watchlist):
                        return
                    portfolio.watchlist.append(WatchlistItem(symbol))
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _WatchFormScreen(title=f"[{pname}] Add Watch Item"),
                    on_watch,
                )

        self.push_screen(_TypeSelectScreen(portfolio_name=pname), on_type)

    def action_edit(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        table, idx = active
        portfolio = self.portfolios[idx]
        pname = self._pname(idx)
        row = table.get_row_at(table.cursor_row)
        if not row:
            return
        identifier = str(row[0])
        is_cash = str(row[1]) == "Cash"

        is_watch = any(w.symbol == identifier for w in portfolio.watchlist)

        if is_cash:
            cash_pos = portfolio.get_cash(identifier)
            if cash_pos is None:
                return

            def on_cash_edit(result: dict | None) -> None:
                if result is None:
                    return
                portfolio.cash.remove(cash_pos)
                try:
                    portfolio.add_cash(result["currency"], result["amount"])
                except ValueError:
                    portfolio.cash.append(cash_pos)
                    return
                self._save(idx)
                self._populate_tables()

            self.push_screen(
                _CashFormScreen(
                    title=f"[{pname}] Edit Cash Position",
                    currency=cash_pos.currency,
                    amount=str(cash_pos.amount),
                ),
                on_cash_edit,
            )
        elif is_watch:
            old_item = next(w for w in portfolio.watchlist if w.symbol == identifier)

            def on_watch_edit(symbol: str | None) -> None:
                if symbol is None:
                    return
                if symbol != old_item.symbol and any(
                    w.symbol == symbol for w in portfolio.watchlist
                ):
                    return
                old_item.symbol = symbol
                self._save(idx)
                self._populate_tables()

            self.push_screen(
                _WatchFormScreen(
                    title=f"[{pname}] Edit Watch Item",
                    symbol=old_item.symbol,
                ),
                on_watch_edit,
            )
        else:
            pos = portfolio.get_position(identifier)
            if pos is None:
                return

            def on_equity_edit(result: dict | None) -> None:
                if result is None:
                    return
                new_symbol = result["symbol"]
                if new_symbol != pos.symbol and portfolio.get_position(new_symbol):
                    return
                pos.symbol = new_symbol
                pos.quantity = result["qty"]
                pos.avg_cost = result["avg_cost"]
                pos.currency = result["currency"]
                self._save(idx)
                self._populate_tables()

            self.push_screen(
                _EquityFormScreen(
                    title=f"[{pname}] Edit Equity Position",
                    symbol=pos.symbol,
                    qty=str(pos.quantity),
                    avg_cost=str(pos.avg_cost),
                    currency=pos.currency,
                ),
                on_equity_edit,
            )

    def action_remove(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        table, idx = active
        portfolio = self.portfolios[idx]
        pname = self._pname(idx)
        row = table.get_row_at(table.cursor_row)
        if not row:
            return
        identifier = str(row[0])
        is_cash = str(row[1]) == "Cash"
        is_watch = any(w.symbol == identifier for w in portfolio.watchlist)
        kind = "cash" if is_cash else ("watch" if is_watch else "position")

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            if is_cash:
                cash_pos = portfolio.get_cash(identifier)
                if cash_pos:
                    portfolio.cash.remove(cash_pos)
            elif is_watch:
                item = next(
                    (w for w in portfolio.watchlist if w.symbol == identifier), None
                )
                if item:
                    portfolio.watchlist.remove(item)
            else:
                pos = portfolio.get_position(identifier)
                if pos:
                    portfolio.positions.remove(pos)
            self._save(idx)
            self._populate_tables()

        self.push_screen(
            _ConfirmScreen(f"[{pname}] Remove {kind}: {identifier}?"), on_confirm
        )

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.data_table.get_row(event.row_key)
        if not row or str(row[1]) == "Cash":
            return
        self.push_screen(StockDetailScreen(str(row[0])))

    # ------------------------------------------------------------------
    # Column sorting
    # ------------------------------------------------------------------

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        tid = event.data_table.id or ""
        col = event.column_index
        if self._sort_column.get(tid) == col:
            self._sort_reverse[tid] = not self._sort_reverse.get(tid, False)
        else:
            self._sort_column[tid] = col
            self._sort_reverse[tid] = False
        self._populate_tables()

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

    @staticmethod
    def _daily_chg_cell(
        last: float,
        prev: float | None,
        dim: bool = False,
        session: str = "regular",
    ) -> tuple[Text | str, float]:
        """Return (display_cell, sort_value) for the daily change column."""
        if prev is None or prev == 0 or session == "closed":
            cell: Text | str = Text("--", style="dim") if dim else "--"
            return cell, 0.0
        pct = (last - prev) / prev * 100
        sign = "+" if pct >= 0 else ""
        label = f"{sign}{pct:.2f}%"
        color = "green" if pct >= 0 else "red"
        style = f"dim {color}" if dim else color
        return Text(label, style=style), pct

    def _render_rows(self, table: DataTable, portfolio: Portfolio) -> None:
        saved_cursor = table.cursor_coordinate
        table.clear()
        tid = table.id or ""
        rates = self.forex_rates.get(portfolio.base_currency, {})

        # Build (sort_key, display_cells) pairs for every row.
        rows: list[tuple[tuple, tuple]] = []

        for pos in portfolio.positions:
            label = exchange_label(pos.symbol, self.exchange_codes.get(pos.symbol))
            last = self.prices.get(pos.symbol)
            sort_key: tuple[object, ...]
            display: tuple[object, ...]
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
                chg_cell, chg_val = self._daily_chg_cell(
                    last, self.prev_closes.get(pos.symbol), session=session
                )
                sort_key = (
                    pos.symbol,
                    label,
                    pos.quantity,
                    pos.avg_cost,
                    last,
                    chg_val,
                    mkt_value,
                    pnl,
                )
                display = (
                    pos.symbol,
                    label,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    price_cell,
                    chg_cell,
                    f"{mkt_value:,.2f}",
                    pnl_text,
                )
            else:
                sort_key = (
                    pos.symbol,
                    label,
                    pos.quantity,
                    pos.avg_cost,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
                display = (
                    pos.symbol,
                    label,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    "N/A",
                    "--",
                    "N/A",
                    "N/A",
                )
            rows.append((sort_key, display))

        for cash_pos in portfolio.cash:
            rate = rates.get(cash_pos.currency)
            if rate is not None:
                mkt_value = cash_pos.amount * rate
                price_cell = (
                    f"{rate:.4f}"
                    if cash_pos.currency != portfolio.base_currency
                    else "1.0000"
                )
                sort_key = (
                    cash_pos.currency,
                    "Cash",
                    cash_pos.amount,
                    1.0,
                    rate,
                    0.0,
                    mkt_value,
                    0.0,
                )
                display = (
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    price_cell,
                    "--",
                    f"{mkt_value:,.2f}",
                    "--",
                )
            else:
                sort_key = (
                    cash_pos.currency,
                    "Cash",
                    cash_pos.amount,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
                display = (
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    "N/A",
                    "--",
                    "N/A",
                    "--",
                )
            rows.append((sort_key, display))

        for watch in portfolio.watchlist:
            label = exchange_label(watch.symbol, self.exchange_codes.get(watch.symbol))
            last = self.prices.get(watch.symbol)
            if last is not None:
                session = self.sessions.get(watch.symbol, "regular")
                if session == "pre":
                    price_cell = Text(f"{last:.2f} ").append("PRE", style="bold yellow")
                elif session == "post":
                    price_cell = Text(f"{last:.2f} ").append("AH", style="bold cyan")
                elif session == "closed":
                    price_cell = Text(f"{last:.2f} ").append("CLS", style="bold red")
                else:
                    price_cell = f"{last:.2f}"
                chg_cell, chg_val = self._daily_chg_cell(
                    last,
                    self.prev_closes.get(watch.symbol),
                    dim=True,
                    session=session,
                )
                sort_key = (watch.symbol, label, 0, 0.0, last, chg_val, 0.0, 0.0)
                display = (
                    Text(watch.symbol, style="dim"),
                    Text(label, style="dim"),
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                    price_cell,
                    chg_cell,
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                )
            else:
                sort_key = (watch.symbol, label, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
                display = (
                    Text(watch.symbol, style="dim"),
                    Text(label, style="dim"),
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                    Text("N/A", style="dim"),
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                )
            rows.append((sort_key, display))

        if tid in self._sort_column:
            col = self._sort_column[tid]
            rows.sort(
                key=lambda r: r[0][col], reverse=self._sort_reverse.get(tid, False)
            )

        for _, cells in rows:
            table.add_row(*cells)
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
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            self._do_refresh_prices()
        finally:
            self._refresh_lock.release()

    def _do_refresh_prices(self) -> None:
        fetcher = PriceFetcher()
        all_symbols = list(
            {p.symbol for portfolio in self.portfolios for p in portfolio.positions}
            | {w.symbol for portfolio in self.portfolios for w in portfolio.watchlist}
        )
        extended = fetcher.fetch_extended_prices(all_symbols)
        new_prices = {sym: price for sym, (price, _) in extended.items()}
        new_sessions = {sym: sess for sym, (_, sess) in extended.items()}

        # Fall back to daily batch prices for symbols that had no 1-minute data.
        missing = [s for s in all_symbols if s not in new_prices]
        if missing:
            fallback = fetcher.fetch_prices(missing)
            new_prices.update(fallback)
            # No intraday data for today -- mark as closed.
            new_sessions.update({sym: "closed" for sym in fallback})

        # Final fallback: fetch individually for symbols still missing after
        # the batch attempt (cross-exchange DataFrame alignment can silently
        # drop tickers from the batch result).
        still_missing = [s for s in missing if s not in new_prices]
        for sym in still_missing:
            price = fetcher.fetch_price_single(sym)
            if price is not None:
                new_prices[sym] = price
                new_sessions[sym] = "closed"
        new_exchange_codes = fetcher.fetch_exchange_names(all_symbols)
        new_prev_closes = fetcher.fetch_previous_closes(all_symbols)
        all_currencies = list(
            {p.currency for portfolio in self.portfolios for p in portfolio.positions}
            | {c.currency for portfolio in self.portfolios for c in portfolio.cash}
        )
        new_forex: dict[str, dict[str, float]] = {}
        for base in {p.base_currency for p in self.portfolios}:
            new_forex[base] = fetcher.fetch_forex_rates(all_currencies, base=base)
        self.call_from_thread(
            self._apply_prices,
            new_prices,
            new_forex,
            new_sessions,
            new_exchange_codes,
            new_prev_closes,
        )

    def _apply_prices(
        self,
        prices: dict[str, float],
        forex_rates: dict[str, dict[str, float]] | None = None,
        sessions: dict[str, str] | None = None,
        exchange_codes: dict[str, str] | None = None,
        prev_closes: dict[str, float] | None = None,
    ) -> None:
        self.prices = prices
        if forex_rates is not None:
            self.forex_rates = forex_rates
        if sessions is not None:
            self.sessions = sessions
        if exchange_codes is not None:
            self.exchange_codes = exchange_codes
        if prev_closes is not None:
            self.prev_closes = prev_closes
        self._populate_tables()
