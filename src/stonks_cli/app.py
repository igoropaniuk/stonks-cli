"""Textual TUI for portfolio display."""

import logging
import threading
from typing import Any, NamedTuple, TypedDict, TypeVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
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

from stonks_cli._columns import _TABLE_COLUMNS
from stonks_cli._row_model import RowData, RowKind, build_row_data
from stonks_cli._session import Session
from stonks_cli.detail import StockDetailScreen
from stonks_cli.logviewer import LogViewerScreen
from stonks_cli.market import MarketSnapshot, build_market_snapshot
from stonks_cli.models import (
    CashPosition,
    Portfolio,
    Position,
    WatchlistItem,
    portfolio_total,
)
from stonks_cli.storage import PortfolioStore

logger = logging.getLogger(__name__)


class _RowMeta(NamedTuple):
    kind: RowKind
    symbol: str  # ticker for position/watchlist, currency code for cash


DEFAULT_REFRESH_INTERVAL: float = 60.0

_FormResultT = TypeVar("_FormResultT")


class _RowData(NamedTuple):
    """One table row: sort key, display cells, and row metadata."""

    sort_key: tuple[Any, ...]
    cells: tuple[str | Text, ...]
    meta: _RowMeta


_ROW_KIND_LABELS: dict[RowKind, str] = {
    RowKind.POSITION: "position",
    RowKind.CASH: "cash",
    RowKind.WATCHLIST: "watch",
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


class _BaseFormScreen(ModalScreen[_FormResultT | None]):
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


def _validate_required(value: str, label_str: str, err: Label) -> bool:
    """Return True if *value* is non-empty; otherwise update *err* and return False."""
    if not value:
        err.update(f"{label_str} is required")
        return False
    return True


def _validate_positive_float(raw: str, label_str: str, err: Label) -> float | None:
    """Parse *raw* as a positive float; update *err* and return None on failure."""
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError
    except ValueError:
        err.update(f"{label_str} must be a positive number")
        return None
    return val


_ASSET_TYPE_OPTIONS: list[tuple[str, str | None]] = [
    ("Equity (default)", None),
    ("Crypto", "crypto"),
    ("ETF", "etf"),
    ("Bond", "bond"),
    ("Commodity", "commodity"),
    ("Forex", "forex"),
]


class _EquityFormScreen(_BaseFormScreen[_EquityResult]):
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
        if not _validate_required(symbol, "Symbol", err):
            return
        qty = _validate_positive_float(qty_str, "Quantity", err)
        if qty is None:
            return
        avg_cost = _validate_positive_float(avg_cost_str, "Avg cost", err)
        if avg_cost is None:
            return
        self.dismiss(
            _EquityResult(
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                currency=currency,
                asset_type=asset_type,
                external_id=external_id,
            )
        )


class _CashFormScreen(_BaseFormScreen[_CashResult]):
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
        if not _validate_required(currency, "Currency", err):
            return
        amount = _validate_positive_float(amount_str, "Amount", err)
        if amount is None:
            return
        self.dismiss(_CashResult(currency=currency, amount=amount))


class _WatchFormScreen(_BaseFormScreen[_WatchResult]):
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
        if not _validate_required(symbol, "Symbol", err):
            return
        self.dismiss(
            _WatchResult(symbol=symbol, asset_type=asset_type, external_id=external_id)
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


def _format_price_cell(last: float, session: str) -> Text | str:
    """Return a price cell with a session badge appended when applicable."""
    if session == Session.PRE:
        return Text(f"{last:.2f} ").append("PRE", style="bold yellow")
    if session == Session.POST:
        return Text(f"{last:.2f} ").append("AH", style="bold cyan")
    if session == Session.CLOSED:
        return Text(f"{last:.2f} ").append("CLS", style="bold red")
    return f"{last:.2f}"


def _to_tui_rows(row_data: list[RowData]) -> list[_RowData]:
    """Convert shared :class:`RowData` objects to TUI-specific :class:`_RowData`.

    Applies Rich Text styling and builds sort keys on top of the
    presentation-agnostic values produced by :func:`build_row_data`.
    """
    rows: list[_RowData] = []
    for rd in row_data:
        if rd.kind == RowKind.POSITION:
            assert rd.qty is not None and rd.avg_cost is not None
            if rd.last is not None:
                pnl = rd.pnl if rd.pnl is not None else 0.0
                sign = "+" if pnl >= 0 else ""
                pnl_cell: str | Text = Text(
                    f"{sign}{pnl:,.2f}",
                    style="bold green" if pnl >= 0 else "bold red",
                )
                price_cell: str | Text = _format_price_cell(rd.last, rd.session)
                if rd.chg_pct is not None:
                    chg_sign = "+" if rd.chg_pct >= 0 else ""
                    chg_cell: str | Text = Text(
                        f"{chg_sign}{rd.chg_pct:.2f}%",
                        style="green" if rd.chg_pct >= 0 else "red",
                    )
                    chg_val = rd.chg_pct
                else:
                    chg_cell = "--"
                    chg_val = 0.0
                mkt_value = rd.mkt_value if rd.mkt_value is not None else 0.0
                sort_key: tuple = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    rd.avg_cost,
                    rd.last,
                    chg_val,
                    mkt_value,
                    pnl,
                )
                mkt_value_cell: str | Text = f"{mkt_value:,.2f}"
            else:
                price_cell = "N/A"
                chg_cell = "--"
                mkt_value_cell = "N/A"
                pnl_cell = "N/A"
                sort_key = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    rd.avg_cost,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
            display: tuple = (
                rd.symbol,
                rd.exchange,
                str(rd.qty),
                f"{rd.avg_cost:.2f}",
                price_cell,
                chg_cell,
                mkt_value_cell,
                pnl_cell,
            )
            rows.append(
                _RowData(sort_key, display, _RowMeta(RowKind.POSITION, rd.symbol))
            )

        elif rd.kind == RowKind.CASH:
            assert rd.qty is not None
            if rd.last is not None:
                mkt_value_c = rd.mkt_value if rd.mkt_value is not None else 0.0
                sort_key = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    1.0,
                    rd.last,
                    0.0,
                    mkt_value_c,
                    0.0,
                )
                price_cell_c: str = f"{rd.last:.4f}"
                mkt_value_cell_c: str = f"{mkt_value_c:,.2f}"
            else:
                sort_key = (rd.symbol, rd.exchange, rd.qty, 1.0, 0.0, 0.0, 0.0, 0.0)
                price_cell_c = "N/A"
                mkt_value_cell_c = "N/A"
            display_c: tuple = (
                rd.symbol,
                rd.exchange,
                f"{rd.qty:,.2f}",
                "1.00",
                price_cell_c,
                "--",
                mkt_value_cell_c,
                "--",
            )
            rows.append(
                _RowData(sort_key, display_c, _RowMeta(RowKind.CASH, rd.symbol))
            )

        else:  # WATCHLIST
            if rd.last is not None:
                price_cell_w: str | Text = _format_price_cell(rd.last, rd.session)
                if rd.chg_pct is not None:
                    chg_sign_w = "+" if rd.chg_pct >= 0 else ""
                    chg_cell_w: str | Text = Text(
                        f"{chg_sign_w}{rd.chg_pct:.2f}%",
                        style=f"dim {'green' if rd.chg_pct >= 0 else 'red'}",
                    )
                    chg_val_w = rd.chg_pct
                else:
                    chg_cell_w = Text("--", style="dim")
                    chg_val_w = 0.0
                sort_key_w: tuple = (
                    rd.symbol,
                    rd.exchange,
                    0,
                    0.0,
                    rd.last,
                    chg_val_w,
                    0.0,
                    0.0,
                )
            else:
                price_cell_w = Text("N/A", style="dim")
                chg_cell_w = Text("--", style="dim")
                sort_key_w = (rd.symbol, rd.exchange, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
            display_w: tuple = (
                Text(rd.symbol, style="dim"),
                Text(rd.exchange, style="dim"),
                Text("--", style="dim"),
                Text("--", style="dim"),
                price_cell_w,
                chg_cell_w,
                Text("--", style="dim"),
                Text("--", style="dim"),
            )
            rows.append(
                _RowData(sort_key_w, display_w, _RowMeta(RowKind.WATCHLIST, rd.symbol))
            )

    return rows


class PortfolioTableWidget(Widget):
    """DataTable plus a total bar for a single portfolio.

    Owns sort state, row metadata, and all table-rendering logic.
    Call :meth:`refresh_data` to push new portfolio/price data into the widget.
    """

    DEFAULT_CSS = "PortfolioTableWidget { height: auto; }"

    def __init__(
        self,
        widget_id: str | None = None,
        table_id: str | None = None,
        total_id: str | None = None,
    ) -> None:
        super().__init__(id=widget_id)
        self._table_id = table_id
        self._total_id = total_id
        self._sort_column: int | None = None
        self._sort_reverse: bool = False
        self._row_meta: dict[str, _RowMeta] = {}
        # Cached market data: populated by refresh_data(), reused on sort.
        self._portfolio: Portfolio | None = None
        self._snap: MarketSnapshot = MarketSnapshot()

    def compose(self) -> ComposeResult:
        yield DataTable(zebra_stripes=True, cursor_type="row", id=self._table_id)
        yield Static("", id=self._total_id, classes="total")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*_TABLE_COLUMNS)

    def refresh_data(self, portfolio: Portfolio, snap: MarketSnapshot) -> None:
        """Push new data into the widget and repaint."""
        self._portfolio = portfolio
        self._snap = snap
        self._repaint()

    def get_row_meta(self) -> _RowMeta | None:
        """Return the _RowMeta for the row currently under the cursor."""
        table = self.query_one(DataTable)
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (IndexError, AttributeError):
            return None
        return self._row_meta.get(str(row_key.value))

    def get_meta_for_key(self, rkey: str) -> _RowMeta | None:
        """Return the _RowMeta for a specific row key string."""
        return self._row_meta.get(rkey)

    class RowSelected(Message):
        """Posted when the user selects a row in this widget's table."""

        def __init__(self, meta: _RowMeta) -> None:
            self.meta = meta
            super().__init__()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        meta = self.get_meta_for_key(str(event.row_key.value))
        if meta is not None:
            self.post_message(self.RowSelected(meta))

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col = event.column_index
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        self._repaint()

    # ------------------------------------------------------------------
    # Internal rendering
    # ------------------------------------------------------------------

    def _repaint(self) -> None:
        if self._portfolio is None:
            return
        table = self.query_one(DataTable)
        self._render_rows(table)
        self._update_total()

    def _apply_sort(self, rows: list[_RowData]) -> list[_RowData]:
        if self._sort_column is None:
            return rows
        col = self._sort_column
        return sorted(
            rows,
            key=lambda r: r[0][col],
            reverse=self._sort_reverse,
        )

    def _write_rows(self, table: DataTable, rows: list[_RowData]) -> None:
        self._row_meta.clear()
        for _, cells, meta in rows:
            rkey = f"{meta.kind.name}:{meta.symbol}"
            table.add_row(*cells, key=rkey)
            self._row_meta[rkey] = meta

    def _render_rows(self, table: DataTable) -> None:
        portfolio = self._portfolio
        assert portfolio is not None
        saved_cursor = table.cursor_coordinate
        table.clear()
        rates = self._snap.forex_rates.get(portfolio.base_currency, {})
        rows = _to_tui_rows(
            build_row_data(
                portfolio,
                self._snap.prices,
                self._snap.sessions,
                self._snap.prev_closes,
                self._snap.exchange_codes,
                rates,
            )
        )
        self._write_rows(table, self._apply_sort(rows))
        table.move_cursor(row=saved_cursor.row, column=saved_cursor.column)

    def _update_total(self) -> None:
        portfolio = self._portfolio
        assert portfolio is not None
        rates = self._snap.forex_rates.get(portfolio.base_currency, {})
        total = portfolio_total(portfolio, self._snap.prices, rates)
        base = portfolio.base_currency
        widget = self.query_one(Static)
        if total is None:
            widget.update(Text(f"Total ({base})  ").append("N/A", style="bold"))
        else:
            widget.update(
                Text(f"Total ({base})  ").append(f"{total:,.2f}", style="bold")
            )


def _do_add_equity(result: _EquityResult, portfolio: Portfolio) -> None:
    """Apply an add-equity form result to *portfolio*."""
    is_new = portfolio.get_position(result["symbol"]) is None
    portfolio.add_position(result["symbol"], result["qty"], result["avg_cost"])
    if is_new:
        pos = portfolio.get_position(result["symbol"])
        if pos:
            pos.currency = result["currency"]
            pos.asset_type = result.get("asset_type")
            pos.external_id = result.get("external_id")


def _do_add_cash(result: _CashResult, portfolio: Portfolio) -> str | None:
    """Apply an add-cash form result to *portfolio*; return an error string or None."""
    try:
        portfolio.add_cash(result["currency"], result["amount"])
    except ValueError as exc:
        logger.warning(
            "Failed to add cash %s %.2f: %s",
            result["currency"],
            result["amount"],
            exc,
        )
        return str(exc)
    return None


def _do_add_watch(result: _WatchResult, portfolio: Portfolio) -> str | None:
    """Apply an add-watch form result to *portfolio*; return an error string or None."""
    symbol = result["symbol"]
    if any(w.symbol == symbol for w in portfolio.watchlist):
        return f"{symbol} is already in the watchlist"
    portfolio.watchlist.append(
        WatchlistItem(
            symbol,
            asset_type=result.get("asset_type"),
            external_id=result.get("external_id"),
        )
    )
    return None


class PortfolioApp(App[None]):
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
        prices: dict[str, float] | None = None,
        forex_rates: dict[str, dict[str, float]] | None = None,
        sessions: dict[str, str] | None = None,
        prev_closes: dict[str, float] | None = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
        stores: list[PortfolioStore] | None = None,
    ) -> None:
        super().__init__()
        self.portfolios = portfolios
        self._snap = MarketSnapshot(
            prices=prices or {},
            forex_rates=forex_rates or {},
            sessions=sessions or {},
            prev_closes=prev_closes or {},
        )
        self.refresh_interval = refresh_interval
        self.stores = stores or []
        self._refresh_lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        if len(self.portfolios) == 1:
            yield PortfolioTableWidget(total_id="total")
        else:
            with VerticalScroll():
                for i, portfolio in enumerate(self.portfolios):
                    label = portfolio.name or f"Portfolio {i + 1}"
                    yield Label(label, id=f"header-{i}", classes="portfolio-header")
                    yield PortfolioTableWidget(
                        widget_id=f"pf-{i}",
                        table_id=f"table-{i}",
                        total_id=f"total-{i}",
                    )
        yield Static("", id="status")
        yield Static("", id="error")
        yield Footer()

    def on_mount(self) -> None:
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

                def on_equity(result: _EquityResult | None) -> None:
                    if result is None:
                        return
                    _do_add_equity(result, self.portfolios[idx])
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _EquityFormScreen(title=f"[{pname}] Add Equity Position"),
                    on_equity,
                )
            elif pos_type == "cash":

                def on_cash(result: _CashResult | None) -> None:
                    if result is None:
                        return
                    err = _do_add_cash(result, self.portfolios[idx])
                    if err:
                        self._show_error(err)
                        return
                    self._show_error("")
                    self._save(idx)
                    self._populate_tables()

                self.push_screen(
                    _CashFormScreen(title=f"[{pname}] Add Cash Position"),
                    on_cash,
                )
            elif pos_type == "watch":

                def on_watch(result: _WatchResult | None) -> None:
                    if result is None:
                        return
                    err = _do_add_watch(result, self.portfolios[idx])
                    if err:
                        self._show_error(err)
                        return
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

        def on_cash_edit(result: _CashResult | None) -> None:
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

        def on_watch_edit(result: _WatchResult | None) -> None:
            if result is None:
                return
            new_symbol = result["symbol"]
            if new_symbol != old_item.symbol and any(
                w.symbol == new_symbol for w in portfolio.watchlist
            ):
                self._show_error(f"{new_symbol} is already in the watchlist")
                return
            self._show_error("")
            old_item.update(
                new_symbol, result.get("asset_type"), result.get("external_id")
            )
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

        def on_equity_edit(result: _EquityResult | None) -> None:
            if result is None:
                return
            new_symbol = result["symbol"]
            if new_symbol != pos.symbol and portfolio.get_position(new_symbol):
                self._show_error(f"{new_symbol} already exists in this portfolio")
                return
            self._show_error("")
            pos.update(
                new_symbol,
                result["qty"],
                result["avg_cost"],
                result["currency"],
                result.get("asset_type"),
                result.get("external_id"),
            )
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

        if meta.kind == RowKind.CASH:
            cash_pos = portfolio.get_cash(identifier)
            if cash_pos is None:
                return
            self._edit_cash(portfolio, idx, pname, cash_pos)
        elif meta.kind == RowKind.WATCHLIST:
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
            if meta.kind == RowKind.CASH:
                cash_pos = portfolio.get_cash(identifier)
                if cash_pos:
                    portfolio.cash.remove(cash_pos)
            elif meta.kind == RowKind.WATCHLIST:
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

    def on_portfolio_table_widget_row_selected(
        self, event: PortfolioTableWidget.RowSelected
    ) -> None:
        if event.meta.kind == RowKind.CASH:
            return
        self.push_screen(StockDetailScreen(event.meta.symbol))

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _populate_tables(self) -> None:
        try:
            status = self.query_one("#status", Static)
            if not self._snap.prices:
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
            widget = self.query_one(PortfolioTableWidget)
        except NoMatches:
            return
        self._refresh_widget(widget, self.portfolios[0])

    def _populate_for(self, i: int, portfolio: Portfolio) -> None:
        try:
            widget = self.query_one(f"#pf-{i}", PortfolioTableWidget)
        except NoMatches:
            return
        self._refresh_widget(widget, portfolio)

    def _refresh_widget(
        self, widget: PortfolioTableWidget, portfolio: Portfolio
    ) -> None:
        widget.refresh_data(portfolio, self._snap)

    def _get_row_meta(self, table: DataTable) -> _RowMeta | None:
        """Return the _RowMeta for the row currently under the cursor, or None."""
        for widget in self.query(PortfolioTableWidget):
            if widget.query_one(DataTable) is table:
                return widget.get_row_meta()
        return None

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
        self.call_from_thread(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap: MarketSnapshot) -> None:
        self._snap = snap
        self._show_error("")
        self._populate_tables()
