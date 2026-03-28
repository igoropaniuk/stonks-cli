"""Textual TUI for portfolio display."""

import logging
import threading

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    Static,
)

from stonks_cli._columns import _TABLE_COLUMNS
from stonks_cli._row_model import (
    _ROW_KIND_LABELS,
    RowKind,
    _RowData,
    _RowMeta,
    _to_tui_rows,
    build_row_data,
)
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


DEFAULT_REFRESH_INTERVAL: float = 60.0

from stonks_cli.forms import (  # noqa: E402
    _CashFormScreen,
    _CashResult,
    _ConfirmScreen,
    _EquityFormScreen,
    _EquityResult,
    _TypeSelectScreen,
    _WatchFormScreen,
    _WatchResult,
)


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
