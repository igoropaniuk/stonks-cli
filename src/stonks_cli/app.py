"""Textual TUI for portfolio display."""

import logging
import threading
from enum import Enum, auto
from typing import Any, NamedTuple, TypedDict

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)
from textual.widgets._select import NoSelection

from stonks_cli.detail import StockDetailScreen
from stonks_cli.fetcher import exchange_label
from stonks_cli.logviewer import LogViewerScreen
from stonks_cli.market import build_market_snapshot
from stonks_cli.models import (
    CashPosition,
    Portfolio,
    Position,
    WatchlistItem,
    daily_change_pct,
    portfolio_total,
)
from stonks_cli.show import _TABLE_COLUMNS
from stonks_cli.storage import PortfolioStore

logger = logging.getLogger(__name__)


class _RowKind(Enum):
    POSITION = auto()
    CASH = auto()
    WATCHLIST = auto()


class _RowMeta(NamedTuple):
    kind: _RowKind
    symbol: str  # ticker for position/watchlist, currency code for cash


DEFAULT_REFRESH_INTERVAL: float = 60.0

_ROW_KIND_LABELS: dict[_RowKind, str] = {
    _RowKind.POSITION: "position",
    _RowKind.CASH: "cash",
    _RowKind.WATCHLIST: "watch",
}


class _EquityResult(TypedDict):
    symbol: str
    qty: float
    avg_cost: float
    currency: str
    asset_type: str | None
    external_id: str | None


class _CashResult(TypedDict):
    currency: str
    amount: float


class _WatchResult(TypedDict):
    symbol: str
    asset_type: str | None
    external_id: str | None


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


class _BaseFormScreen(ModalScreen[dict[str, Any] | None]):
    """Shared boilerplate for add/edit form dialogs.

    Subclasses must implement :meth:`_submit`.  CSS is generated
    automatically from the concrete class name.
    """

    def __init_subclass__(cls, **kwargs: bool) -> None:
        super().__init_subclass__(**kwargs)
        cls.CSS = _MODAL_CSS.format(cls=cls.__name__)

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self._title = title

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._submit()

    def on_key(self, event: Any) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def _submit(self) -> None:
        raise NotImplementedError


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

    def on_key(self, event: Any) -> None:
        if event.key == "escape":
            self.dismiss(None)


_ASSET_TYPE_OPTIONS: list[tuple[str, str | None]] = [
    ("Equity (default)", None),
    ("Crypto", "crypto"),
    ("ETF", "etf"),
    ("Bond", "bond"),
    ("Commodity", "commodity"),
    ("Forex", "forex"),
]


class _EquityFormScreen(_BaseFormScreen):
    """Form for adding or editing an equity position."""

    def __init__(
        self,
        title: str = "Add Equity Position",
        symbol: str = "",
        qty: str = "",
        avg_cost: str = "",
        currency: str = "USD",
        asset_type: str | None = None,
        external_id: str = "",
    ) -> None:
        super().__init__(title)
        self._symbol = symbol
        self._qty = qty
        self._avg_cost = avg_cost
        self._currency = currency
        self._asset_type = asset_type
        self._external_id = external_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Symbol", classes="field-label")
            yield Input(value=self._symbol, placeholder="e.g. AAPL", id="symbol")
            yield Label("Quantity", classes="field-label")
            yield Input(value=self._qty, placeholder="e.g. 10 or 0.25", id="qty")
            yield Label("Avg Cost", classes="field-label")
            yield Input(value=self._avg_cost, placeholder="e.g. 150.00", id="avg_cost")
            yield Label("Currency", classes="field-label")
            yield Input(value=self._currency, placeholder="USD", id="currency")
            yield Label("Asset Type", classes="field-label")
            yield Select(
                [(label, val) for label, val in _ASSET_TYPE_OPTIONS],
                value=self._asset_type,
                allow_blank=False,
                id="asset_type",
            )
            yield Label("External ID (e.g. CoinGecko coin ID)", classes="field-label")
            yield Input(
                value=self._external_id,
                placeholder="e.g. bitcoin",
                id="external_id",
            )
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _submit(self) -> None:
        symbol = self.query_one("#symbol", Input).value.strip().upper()
        qty_str = self.query_one("#qty", Input).value.strip()
        avg_cost_str = self.query_one("#avg_cost", Input).value.strip()
        currency = self.query_one("#currency", Input).value.strip().upper() or "USD"
        asset_type_val = self.query_one("#asset_type", Select).value
        asset_type: str | None = (
            None if isinstance(asset_type_val, NoSelection) else asset_type_val
        )
        external_id = self.query_one("#external_id", Input).value.strip() or None
        err = self.query_one("#error", Label)
        if not symbol:
            err.update("Symbol is required")
            return
        try:
            qty = float(qty_str)
            if qty <= 0:
                raise ValueError
        except ValueError:
            err.update("Quantity must be a positive number")
            return
        try:
            avg_cost = float(avg_cost_str)
            if avg_cost <= 0:
                raise ValueError
        except ValueError:
            err.update("Avg cost must be a positive number")
            return
        self.dismiss(
            _EquityResult(  # type: ignore[arg-type]
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                currency=currency,
                asset_type=asset_type,
                external_id=external_id,
            )
        )


class _CashFormScreen(_BaseFormScreen):
    """Form for adding or editing a cash position."""

    def __init__(
        self,
        title: str = "Add Cash Position",
        currency: str = "",
        amount: str = "",
    ) -> None:
        super().__init__(title)
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
        self.dismiss(_CashResult(currency=currency, amount=amount))  # type: ignore[arg-type]


class _WatchFormScreen(_BaseFormScreen):
    """Form for adding or editing a watchlist item."""

    def __init__(
        self,
        title: str = "Add Watch Item",
        symbol: str = "",
        asset_type: str | None = None,
        external_id: str = "",
    ) -> None:
        super().__init__(title)
        self._symbol = symbol
        self._asset_type = asset_type
        self._external_id = external_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Symbol", classes="field-label")
            yield Input(value=self._symbol, placeholder="e.g. TSLA", id="symbol")
            yield Label("Asset Type", classes="field-label")
            yield Select(
                [(label, val) for label, val in _ASSET_TYPE_OPTIONS],
                value=self._asset_type,
                allow_blank=False,
                id="asset_type",
            )
            yield Label("External ID (e.g. CoinGecko coin ID)", classes="field-label")
            yield Input(
                value=self._external_id,
                placeholder="e.g. bitcoin",
                id="external_id",
            )
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _submit(self) -> None:
        symbol = self.query_one("#symbol", Input).value.strip().upper()
        asset_type_val = self.query_one("#asset_type", Select).value
        asset_type: str | None = (
            None if isinstance(asset_type_val, NoSelection) else asset_type_val
        )
        external_id = self.query_one("#external_id", Input).value.strip() or None
        err = self.query_one("#error", Label)
        if not symbol:
            err.update("Symbol is required")
            return
        self.dismiss(
            _WatchResult(symbol=symbol, asset_type=asset_type, external_id=external_id)  # type: ignore[arg-type]
        )


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

    def on_key(self, event: Any) -> None:
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
        ("l", "view_logs", "Logs"),
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
    #error {
        padding: 0 1;
        color: $error;
        display: none;
    }
    #error.visible {
        display: block;
    }
    """

    def __init__(
        self,
        portfolios: list[Portfolio],
        prices: dict[str, float],
        forex_rates: dict[str, dict[str, float]],
        sessions: dict[str, str] | None = None,
        prev_closes: dict[str, float] | None = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
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
        # Row metadata: (table_id, row_key_str) -> _RowMeta.
        self._row_meta: dict[tuple[str, str], _RowMeta] = {}

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
        yield Static("", id="error")
        yield Footer()

    def on_mount(self) -> None:
        if len(self.portfolios) == 1:
            self.query_one(DataTable).add_columns(*_TABLE_COLUMNS)
        else:
            for i in range(len(self.portfolios)):
                self.query_one(f"#table-{i}", DataTable).add_columns(*_TABLE_COLUMNS)
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

    def _show_error(self, msg: str) -> None:
        """Display *msg* in the #error bar, or hide it when *msg* is empty."""
        try:
            widget = self.query_one("#error", Static)
        except NoMatches:
            return
        widget.update(msg)
        if msg:
            widget.add_class("visible")
        else:
            widget.remove_class("visible")

    def action_add(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        _, idx = active
        pname = self._pname(idx)

        def on_type(pos_type: str | None) -> None:
            if pos_type == "equity":

                def on_equity(result: dict[str, Any] | None) -> None:
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
                            pos.asset_type = result.get("asset_type")
                            pos.external_id = result.get("external_id")
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _EquityFormScreen(title=f"[{pname}] Add Equity Position"),
                    on_equity,
                )
            elif pos_type == "cash":

                def on_cash(result: dict[str, Any] | None) -> None:
                    if result is None:
                        return
                    try:
                        self.portfolios[idx].add_cash(
                            result["currency"], result["amount"]
                        )
                    except ValueError as exc:
                        logger.warning(
                            "Failed to add cash %s %.2f: %s",
                            result["currency"],
                            result["amount"],
                            exc,
                        )
                        self._show_error(str(exc))
                        return
                    self._show_error("")
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _CashFormScreen(title=f"[{pname}] Add Cash Position"),
                    on_cash,
                )
            elif pos_type == "watch":

                def on_watch(result: dict[str, Any] | None) -> None:
                    if result is None:
                        return
                    symbol = result["symbol"]
                    portfolio = self.portfolios[idx]
                    if any(w.symbol == symbol for w in portfolio.watchlist):
                        self._show_error(f"{symbol} is already in the watchlist")
                        return
                    portfolio.watchlist.append(
                        WatchlistItem(
                            symbol,
                            asset_type=result.get("asset_type"),
                            external_id=result.get("external_id"),
                        )
                    )
                    self._show_error("")
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _WatchFormScreen(title=f"[{pname}] Add Watch Item"),
                    on_watch,
                )

        self.push_screen(_TypeSelectScreen(portfolio_name=pname), on_type)

    def _edit_cash(
        self, portfolio: Portfolio, idx: int, pname: str, cash_pos: CashPosition
    ) -> None:
        """Push the cash-edit form and apply the result."""

        def on_cash_edit(result: dict[str, Any] | None) -> None:
            if result is None:
                return
            new_currency = result["currency"]
            if (
                new_currency != cash_pos.currency
                and portfolio.get_cash(new_currency) is not None
            ):
                self._show_error(f"A {new_currency} cash position already exists")
                return
            portfolio.cash.remove(cash_pos)
            try:
                portfolio.add_cash(new_currency, result["amount"])
            except ValueError as exc:
                logger.warning("Failed to edit cash position: %s", exc)
                portfolio.cash.append(cash_pos)
                self._show_error(str(exc))
                return
            self._show_error("")
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

    def _edit_watch(
        self, portfolio: Portfolio, idx: int, pname: str, old_item: WatchlistItem
    ) -> None:
        """Push the watchlist-edit form and apply the result."""

        def on_watch_edit(result: dict[str, Any] | None) -> None:
            if result is None:
                return
            new_symbol = result["symbol"]
            if new_symbol != old_item.symbol and any(
                w.symbol == new_symbol for w in portfolio.watchlist
            ):
                self._show_error(f"{new_symbol} is already in the watchlist")
                return
            self._show_error("")
            old_item.symbol = new_symbol
            old_item.asset_type = result.get("asset_type")
            old_item.external_id = result.get("external_id")
            self._save(idx)
            self._populate_tables()

        self.push_screen(
            _WatchFormScreen(
                title=f"[{pname}] Edit Watch Item",
                symbol=old_item.symbol,
                asset_type=old_item.asset_type,
                external_id=old_item.external_id or "",
            ),
            on_watch_edit,
        )

    def _edit_position(
        self, portfolio: Portfolio, idx: int, pname: str, pos: Position
    ) -> None:
        """Push the equity-edit form and apply the result."""

        def on_equity_edit(result: dict[str, Any] | None) -> None:
            if result is None:
                return
            new_symbol = result["symbol"]
            if new_symbol != pos.symbol and portfolio.get_position(new_symbol):
                self._show_error(f"{new_symbol} already exists in this portfolio")
                return
            self._show_error("")
            pos.symbol = new_symbol
            pos.quantity = result["qty"]
            pos.avg_cost = result["avg_cost"]
            pos.currency = result["currency"]
            pos.asset_type = result.get("asset_type")
            pos.external_id = result.get("external_id")
            self._save(idx)
            self._populate_tables()

        self.push_screen(
            _EquityFormScreen(
                title=f"[{pname}] Edit Equity Position",
                symbol=pos.symbol,
                qty=str(pos.quantity),
                avg_cost=str(pos.avg_cost),
                currency=pos.currency,
                asset_type=pos.asset_type,
                external_id=pos.external_id or "",
            ),
            on_equity_edit,
        )

    def action_edit(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        table, idx = active
        portfolio = self.portfolios[idx]
        pname = self._pname(idx)
        meta = self._get_row_meta(table)
        if meta is None:
            return
        identifier = meta.symbol

        if meta.kind == _RowKind.CASH:
            cash_pos = portfolio.get_cash(identifier)
            if cash_pos is None:
                return
            self._edit_cash(portfolio, idx, pname, cash_pos)
        elif meta.kind == _RowKind.WATCHLIST:
            old_item = next(
                (w for w in portfolio.watchlist if w.symbol == identifier), None
            )
            if old_item is None:
                logger.warning("Could not find watchlist item %s to edit", identifier)
                return
            self._edit_watch(portfolio, idx, pname, old_item)
        else:
            pos = portfolio.get_position(identifier)
            if pos is None:
                return
            self._edit_position(portfolio, idx, pname, pos)

    def action_remove(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        table, idx = active
        portfolio = self.portfolios[idx]
        pname = self._pname(idx)
        meta = self._get_row_meta(table)
        if meta is None:
            return
        identifier = meta.symbol
        kind = _ROW_KIND_LABELS[meta.kind]

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            if meta.kind == _RowKind.CASH:
                cash_pos = portfolio.get_cash(identifier)
                if cash_pos:
                    portfolio.cash.remove(cash_pos)
            elif meta.kind == _RowKind.WATCHLIST:
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
    # Log viewer
    # ------------------------------------------------------------------

    def action_view_logs(self) -> None:
        self.push_screen(LogViewerScreen())

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        tid = event.data_table.id or ""
        meta = self._row_meta.get((tid, str(event.row_key.value)))
        if meta is None or meta.kind == _RowKind.CASH:
            return
        self.push_screen(StockDetailScreen(meta.symbol))

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
        pct = daily_change_pct(last, prev, session)
        if pct is None:
            cell: Text | str = Text("--", style="dim") if dim else "--"
            return cell, 0.0
        sign = "+" if pct >= 0 else ""
        label = f"{sign}{pct:.2f}%"
        color = "green" if pct >= 0 else "red"
        style = f"dim {color}" if dim else color
        return Text(label, style=style), pct

    @staticmethod
    def _format_price_cell(last: float, session: str) -> Text | str:
        """Return a price cell with a session badge appended when applicable."""
        if session == "pre":
            return Text(f"{last:.2f} ").append("PRE", style="bold yellow")
        if session == "post":
            return Text(f"{last:.2f} ").append("AH", style="bold cyan")
        if session == "closed":
            return Text(f"{last:.2f} ").append("CLS", style="bold red")
        return f"{last:.2f}"

    def _render_position_rows(
        self,
        portfolio: Portfolio,
        rates: dict[str, float],
    ) -> list[tuple[tuple, tuple, _RowMeta]]:
        """Build (sort_key, display_cells, meta) triples for held positions."""
        rows: list[tuple[tuple, tuple, _RowMeta]] = []
        for pos in portfolio.positions:
            label = exchange_label(
                pos.symbol, self.exchange_codes.get(pos.symbol), pos.asset_type
            )
            last = self.prices.get(pos.symbol)
            if last is not None:
                session = self.sessions.get(pos.symbol, "regular")
                mkt_value = pos.market_value(last)
                pnl = pos.unrealized_pnl(last)
                sign = "+" if pnl >= 0 else ""
                pnl_text = Text(
                    f"{sign}{pnl:,.2f}",
                    style="bold green" if pnl >= 0 else "bold red",
                )
                chg_cell, chg_val = self._daily_chg_cell(
                    last, self.prev_closes.get(pos.symbol), session=session
                )
                sort_key: tuple = (
                    pos.symbol,
                    label,
                    pos.quantity,
                    pos.avg_cost,
                    last,
                    chg_val,
                    mkt_value,
                    pnl,
                )
                display: tuple = (
                    pos.symbol,
                    label,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    self._format_price_cell(last, session),
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
            rows.append((sort_key, display, _RowMeta(_RowKind.POSITION, pos.symbol)))
        return rows

    def _render_cash_rows(
        self,
        portfolio: Portfolio,
        rates: dict[str, float],
    ) -> list[tuple[tuple, tuple, _RowMeta]]:
        """Build (sort_key, display_cells, meta) triples for cash positions."""
        rows: list[tuple[tuple, tuple, _RowMeta]] = []
        for cash_pos in portfolio.cash:
            rate = rates.get(cash_pos.currency)
            if rate is not None:
                mkt_value = cash_pos.amount * rate
                price_cell: str = (
                    f"{rate:.4f}"
                    if cash_pos.currency != portfolio.base_currency
                    else "1.0000"
                )
                sort_key: tuple = (
                    cash_pos.currency,
                    "Cash",
                    cash_pos.amount,
                    1.0,
                    rate,
                    0.0,
                    mkt_value,
                    0.0,
                )
                display: tuple = (
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
            rows.append((sort_key, display, _RowMeta(_RowKind.CASH, cash_pos.currency)))
        return rows

    def _render_watchlist_rows(
        self,
        portfolio: Portfolio,
    ) -> list[tuple[tuple, tuple, _RowMeta]]:
        """Build (sort_key, display_cells, meta) triples for watchlist items."""
        rows: list[tuple[tuple, tuple, _RowMeta]] = []
        for watch in portfolio.watchlist:
            label = exchange_label(
                watch.symbol, self.exchange_codes.get(watch.symbol), watch.asset_type
            )
            last = self.prices.get(watch.symbol)
            if last is not None:
                session = self.sessions.get(watch.symbol, "regular")
                chg_cell, chg_val = self._daily_chg_cell(
                    last, self.prev_closes.get(watch.symbol), dim=True, session=session
                )
                sort_key: tuple = (watch.symbol, label, 0, 0.0, last, chg_val, 0.0, 0.0)
                display: tuple = (
                    Text(watch.symbol, style="dim"),
                    Text(label, style="dim"),
                    Text("--", style="dim"),
                    Text("--", style="dim"),
                    self._format_price_cell(last, session),
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
            rows.append((sort_key, display, _RowMeta(_RowKind.WATCHLIST, watch.symbol)))
        return rows

    def _render_rows(self, table: DataTable, portfolio: Portfolio) -> None:
        saved_cursor = table.cursor_coordinate
        table.clear()
        tid = table.id or ""
        rates = self.forex_rates.get(portfolio.base_currency, {})

        rows = (
            self._render_position_rows(portfolio, rates)
            + self._render_cash_rows(portfolio, rates)
            + self._render_watchlist_rows(portfolio)
        )

        if tid in self._sort_column:
            col = self._sort_column[tid]
            rows.sort(
                key=lambda r: r[0][col], reverse=self._sort_reverse.get(tid, False)
            )

        # Rebuild per-table row metadata and write rows with explicit keys.
        for key in [k for k in self._row_meta if k[0] == tid]:
            del self._row_meta[key]
        for _, cells, meta in rows:
            rkey = f"{meta.kind.name}:{meta.symbol}"
            table.add_row(*cells, key=rkey)
            self._row_meta[(tid, rkey)] = meta
        table.move_cursor(row=saved_cursor.row, column=saved_cursor.column)

    def _get_row_meta(self, table: DataTable) -> _RowMeta | None:
        """Return the _RowMeta for the row currently under the cursor, or None."""
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (IndexError, AttributeError):
            return None
        tid = table.id or ""
        return self._row_meta.get((tid, str(row_key.value)))

    def _update_total_widget(self, widget: Static, portfolio: Portfolio) -> None:
        rates = self.forex_rates.get(portfolio.base_currency, {})
        total = portfolio_total(portfolio, self.prices, rates)
        base = portfolio.base_currency
        if total is None:
            widget.update(Text(f"Total ({base})  ").append("N/A", style="bold"))
        else:
            widget.update(
                Text(f"Total ({base})  ").append(f"{total:,.2f}", style="bold")
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
        except Exception as exc:  # noqa: BLE001
            logger.error("Price refresh failed: %s", exc, exc_info=True)
            self.call_from_thread(self._show_error, f"Price refresh failed: {exc}")
        finally:
            self._refresh_lock.release()

    def _do_refresh_prices(self) -> None:
        snap = build_market_snapshot(self.portfolios)
        self.call_from_thread(
            self._apply_prices,
            snap.prices,
            snap.forex_rates,
            snap.sessions,
            snap.exchange_codes,
            snap.prev_closes,
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
        self._show_error("")
        self._populate_tables()
