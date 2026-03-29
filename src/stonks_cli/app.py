"""Textual TUI for portfolio display."""

import logging
import threading
from collections.abc import Callable
from typing import Any

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

from stonks_cli import app_actions
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

_AddFormFactory = Callable[[], Any]
_AddResultHandler = Callable[[Any], None]
_ActiveSelection = tuple[Portfolio, int, str, _RowMeta]


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

    def _save_and_refresh(self, idx: int) -> None:
        """Persist portfolio *idx* and repaint the tables."""
        self._save(idx)
        self._populate_tables()

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

    def _call_from_thread_if_running(self, fn, *args) -> bool:
        """Call *fn* via Textual when the app loop is still alive.

        Worker threads may finish during shutdown, at which point
        ``call_from_thread`` raises ``RuntimeError("App is not running")``.
        Treat that race as a harmless no-op.
        """
        try:
            self.call_from_thread(fn, *args)
        except RuntimeError as exc:
            if exc.args != ("App is not running",):
                raise
            logger.debug(
                "Skipping UI callback %s because the app is shutting down",
                getattr(fn, "__name__", repr(fn)),
            )
            return False
        return True

    def _show_mutation_error(self, err: str | None) -> bool:
        """Show *err* and return False, or clear the error bar and return True."""
        if err:
            self._show_error(err)
            return False
        self._show_error("")
        return True

    def _handle_add_equity(self, idx: int, result: _EquityResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_equity(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_add_cash(self, idx: int, result: _CashResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_cash(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_add_watch(self, idx: int, result: _WatchResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_watch(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _push_add_form(self, pos_type: str | None, idx: int, pname: str) -> None:
        """Open the selected add-form flow for portfolio *idx*."""
        handlers: dict[str, tuple[_AddFormFactory, _AddResultHandler]] = {
            "equity": (
                lambda: _EquityFormScreen(title=f"[{pname}] Add Equity Position"),
                lambda result: self._handle_add_equity(idx, result),
            ),
            "cash": (
                lambda: _CashFormScreen(title=f"[{pname}] Add Cash Position"),
                lambda result: self._handle_add_cash(idx, result),
            ),
            "watch": (
                lambda: _WatchFormScreen(title=f"[{pname}] Add Watch Item"),
                lambda result: self._handle_add_watch(idx, result),
            ),
        }
        screen_and_handler = handlers.get(pos_type or "")
        if screen_and_handler is None:
            return
        make_screen, handler = screen_and_handler
        self.push_screen(make_screen(), handler)

    def _handle_edit_cash(
        self,
        portfolio: Portfolio,
        idx: int,
        cash_pos: CashPosition,
        result: _CashResult | None,
    ) -> None:
        if result is None:
            return
        err = app_actions.edit_cash(portfolio, cash_pos, result)
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_edit_watch(
        self,
        portfolio: Portfolio,
        idx: int,
        old_item: WatchlistItem,
        result: _WatchResult | None,
    ) -> None:
        if result is None:
            return
        err = app_actions.edit_watch(portfolio, old_item, result)
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_edit_position(
        self,
        portfolio: Portfolio,
        idx: int,
        pos: Position,
        result: _EquityResult | None,
    ) -> None:
        if result is None:
            return
        err = app_actions.edit_position(portfolio, pos, result)
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_remove_confirmation(
        self,
        portfolio: Portfolio,
        idx: int,
        kind: RowKind,
        identifier: str,
        confirmed: bool | None,
    ) -> None:
        if not confirmed:
            return
        app_actions.remove_selected_item(portfolio, kind, identifier)
        self._save_and_refresh(idx)

    def _get_active_selection(self) -> _ActiveSelection | None:
        """Return the currently selected portfolio context, or None."""
        active = self._get_active_table_and_index()
        if active is None:
            return None
        table, idx = active
        meta = self._get_row_meta(table)
        if meta is None:
            return None
        portfolio = self.portfolios[idx]
        return portfolio, idx, self._pname(idx), meta

    def action_add(self) -> None:
        active = self._get_active_table_and_index()
        if active is None:
            return
        _, idx = active
        pname = self._pname(idx)
        self.push_screen(
            _TypeSelectScreen(portfolio_name=pname),
            lambda pos_type: self._push_add_form(pos_type, idx, pname),
        )

    def _edit_cash(
        self, portfolio: Portfolio, idx: int, pname: str, cash_pos: CashPosition
    ) -> None:
        """Push the cash-edit form and apply the result."""
        self.push_screen(
            _CashFormScreen(
                title=f"[{pname}] Edit Cash Position",
                currency=cash_pos.currency,
                amount=str(cash_pos.amount),
            ),
            lambda result: self._handle_edit_cash(portfolio, idx, cash_pos, result),
        )

    def _edit_watch(
        self, portfolio: Portfolio, idx: int, pname: str, old_item: WatchlistItem
    ) -> None:
        """Push the watchlist-edit form and apply the result."""
        self.push_screen(
            _WatchFormScreen(
                title=f"[{pname}] Edit Watch Item",
                symbol=old_item.symbol,
                asset_type=old_item.asset_type,
                external_id=old_item.external_id or "",
            ),
            lambda result: self._handle_edit_watch(portfolio, idx, old_item, result),
        )

    def _edit_position(
        self, portfolio: Portfolio, idx: int, pname: str, pos: Position
    ) -> None:
        """Push the equity-edit form and apply the result."""
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
            lambda result: self._handle_edit_position(portfolio, idx, pos, result),
        )

    def _dispatch_edit_cash(
        self, portfolio: Portfolio, idx: int, pname: str, identifier: str
    ) -> None:
        cash_pos = portfolio.get_cash(identifier)
        if cash_pos is None:
            return
        self._edit_cash(portfolio, idx, pname, cash_pos)

    def _dispatch_edit_watch(
        self, portfolio: Portfolio, idx: int, pname: str, identifier: str
    ) -> None:
        old_item = app_actions.watch_item(portfolio, identifier)
        if old_item is None:
            logger.warning("Could not find watchlist item %s to edit", identifier)
            return
        self._edit_watch(portfolio, idx, pname, old_item)

    def _dispatch_edit_position(
        self, portfolio: Portfolio, idx: int, pname: str, identifier: str
    ) -> None:
        pos = portfolio.get_position(identifier)
        if pos is None:
            return
        self._edit_position(portfolio, idx, pname, pos)

    def _dispatch_edit_selection(self, selection: _ActiveSelection) -> None:
        """Open the correct edit flow for the selected row."""
        portfolio, idx, pname, meta = selection
        handlers: dict[RowKind, Callable[[Portfolio, int, str, str], None]] = {
            RowKind.CASH: self._dispatch_edit_cash,
            RowKind.WATCHLIST: self._dispatch_edit_watch,
            RowKind.POSITION: self._dispatch_edit_position,
        }
        handlers[meta.kind](portfolio, idx, pname, meta.symbol)

    def action_edit(self) -> None:
        selection = self._get_active_selection()
        if selection is None:
            return
        self._dispatch_edit_selection(selection)

    def _prompt_remove_selection(self, selection: _ActiveSelection) -> None:
        """Open the remove confirmation dialog for the selected row."""
        portfolio, idx, pname, meta = selection
        identifier = meta.symbol
        kind = _ROW_KIND_LABELS[meta.kind]
        self.push_screen(
            _ConfirmScreen(f"[{pname}] Remove {kind}: {identifier}?"),
            lambda confirmed: self._handle_remove_confirmation(
                portfolio, idx, meta.kind, identifier, confirmed
            ),
        )

    def action_remove(self) -> None:
        selection = self._get_active_selection()
        if selection is None:
            return
        self._prompt_remove_selection(selection)

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
            self._call_from_thread_if_running(
                self._show_error, f"Price refresh failed: {exc}"
            )
        finally:
            self._refresh_lock.release()

    def _do_refresh_prices(self) -> None:
        snap = build_market_snapshot(self.portfolios)
        self._call_from_thread_if_running(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap: MarketSnapshot) -> None:
        self._snap = snap
        self._show_error("")
        self._populate_tables()
