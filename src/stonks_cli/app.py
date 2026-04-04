"""Textual TUI for portfolio display."""

import logging
import threading
from collections import deque
from collections.abc import Callable
from functools import partial
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Header, Label, Static

from stonks_cli import app_actions
from stonks_cli.chat import ChatScreen
from stonks_cli.detail import StockDetailScreen
from stonks_cli.dto import CashResult, EquityResult, WatchResult
from stonks_cli.forms import (
    _CashFormScreen,
    _ConfirmScreen,
    _EquityFormScreen,
    _TypeSelectScreen,
    _WatchFormScreen,
)
from stonks_cli.helpers import ThreadGuardMixin
from stonks_cli.logviewer import LogViewerScreen
from stonks_cli.market import MarketSnapshot, build_market_snapshot
from stonks_cli.messages import HistoryUpdated
from stonks_cli.models import (
    CashPosition,
    Portfolio,
    Position,
    WatchlistItem,
)
from stonks_cli.news_fetcher import NewsFetcher, NewsItem
from stonks_cli.portfolio_table import (
    _ROW_KIND_LABELS,
    RowKind,
    _RowMeta,
)
from stonks_cli.storage import PortfolioStore
from stonks_cli.widgets.news_feed import NewsFeedWidget
from stonks_cli.widgets.portfolio_table import PortfolioTableWidget

logger = logging.getLogger(__name__)


DEFAULT_REFRESH_INTERVAL: float = 60.0
NEWS_HISTORY_LIMIT: int = 100

_AddFormFactory = Callable[[], Any]
_ActiveSelection = tuple[Portfolio, int, str, _RowMeta]
_ScreenCallback = Callable[[Any], None]


class PortfolioApp(ThreadGuardMixin, App[None]):
    """Full-screen portfolio table with periodic price refresh."""

    TITLE = "Stonks"
    AUTO_FOCUS = "DataTable"
    BINDINGS = [
        ("q", "quit", "Quit"),
        Binding("tab", "focus_next", "Next", show=True, priority=True),
        ("a", "add", "Add"),
        ("e", "edit", "Edit"),
        ("r", "remove", "Remove"),
        ("l", "view_logs", "Logs"),
        ("n", "toggle_news", "News"),
        ("c", "chat", "Chat"),
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
        self._news_items: deque[NewsItem] = deque(maxlen=NEWS_HISTORY_LIMIT)
        self._chat_history: list[dict[str, str]] = []
        self._row_metas: dict[int, _RowMeta | None] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        if len(self.portfolios) == 1:
            with Vertical():
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
        yield NewsFeedWidget(id="news-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_tables()
        self._refresh_prices()
        self.set_interval(self.refresh_interval, self._refresh_prices)
        self._refresh_news()
        self.set_interval(self.refresh_interval, self._refresh_news)

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

    def _show_mutation_error(self, err: str | None) -> bool:
        """Show *err* and return False, or clear the error bar and return True."""
        if err:
            self._show_error(err)
            return False
        self._show_error("")
        return True

    def _handle_add_equity(self, idx: int, result: EquityResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_equity(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_add_cash(self, idx: int, result: CashResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_cash(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _handle_add_watch(self, idx: int, result: WatchResult | None) -> None:
        if result is None:
            return
        err = app_actions.add_watch(result, self.portfolios[idx])
        if not self._show_mutation_error(err):
            return
        self._save_and_refresh(idx)

    def _push_add_form(self, pos_type: str | None, idx: int, pname: str) -> None:
        """Open the selected add-form flow for portfolio *idx*."""
        handlers: dict[str, tuple[_AddFormFactory, _ScreenCallback]] = {
            "equity": (
                lambda: _EquityFormScreen(title=f"[{pname}] Add Equity Position"),
                partial(self._handle_add_equity, idx),
            ),
            "cash": (
                lambda: _CashFormScreen(title=f"[{pname}] Add Cash Position"),
                partial(self._handle_add_cash, idx),
            ),
            "watch": (
                lambda: _WatchFormScreen(title=f"[{pname}] Add Watch Item"),
                partial(self._handle_add_watch, idx),
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
        result: CashResult | None,
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
        result: WatchResult | None,
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
        result: EquityResult | None,
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
            partial(self._push_add_form, idx=idx, pname=pname),
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
            partial(self._handle_edit_cash, portfolio, idx, cash_pos),
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
            partial(self._handle_edit_watch, portfolio, idx, old_item),
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
            partial(self._handle_edit_position, portfolio, idx, pos),
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
            partial(
                self._handle_remove_confirmation,
                portfolio,
                idx,
                meta.kind,
                identifier,
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

    def action_chat(self) -> None:
        if isinstance(self.screen, ChatScreen):
            return
        self.push_screen(
            ChatScreen(
                self.portfolios,
                lambda: self._snap,
                self._news_items,
                self._chat_history,
            )
        )

    def on_history_updated(self, event: HistoryUpdated) -> None:
        self._chat_history = event.history

    def action_toggle_news(self) -> None:
        try:
            widget = self.query_one(NewsFeedWidget)
            widget.display = not widget.display
        except NoMatches:
            pass

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def on_portfolio_table_widget_row_selected(
        self, event: PortfolioTableWidget.RowSelected
    ) -> None:
        if event.meta.kind == RowKind.CASH:
            return
        self.push_screen(StockDetailScreen(event.meta.symbol))

    def on_news_feed_widget_open_url(self, message: NewsFeedWidget.OpenURL) -> None:
        self.open_url(message.url)

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

    def on_portfolio_table_widget_row_highlighted(
        self, event: PortfolioTableWidget.RowHighlighted
    ) -> None:
        self._row_metas[id(event.widget)] = event.meta

    def _get_row_meta(self, table: DataTable) -> _RowMeta | None:
        """Return the last highlighted _RowMeta for the widget owning *table*."""
        for widget in self.query(PortfolioTableWidget):
            if widget.query_one(DataTable) is table:
                return self._row_metas.get(id(widget))
        return None

    # ------------------------------------------------------------------
    # News refresh
    # ------------------------------------------------------------------

    def _collect_symbols(self) -> list[str]:
        """Return unique equity/watchlist symbols across all portfolios."""
        symbols: set[str] = set()
        for portfolio in self.portfolios:
            for pos in portfolio.positions:
                symbols.add(pos.symbol)
            for item in portfolio.watchlist:
                symbols.add(item.symbol)
        return list(symbols)

    @work(thread=True)
    def _refresh_news(self) -> None:
        symbols = self._collect_symbols()
        if not symbols:
            return
        try:
            items = NewsFetcher().fetch_for_symbols(
                symbols, max_items=NEWS_HISTORY_LIMIT
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("News refresh failed: %s", exc)
            self._call_from_thread_if_running(
                self._show_news_error, f"News unavailable: {exc}"
            )
            return
        self._call_from_thread_if_running(self._update_news_panel, items)

    def _merge_news_items(self, items: list[NewsItem]) -> list[NewsItem]:
        """Merge newly fetched items into a fixed-size in-session news ring buffer."""
        seen_urls = {
            item.url or f"{item.timestamp}:{item.headline}" for item in self._news_items
        }

        for item in sorted(items, key=lambda item: item.timestamp):
            key = item.url or f"{item.timestamp}:{item.headline}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            self._news_items.append(item)

        return list(reversed(self._news_items))

    def _update_news_panel(self, items: list[NewsItem]) -> None:
        try:
            merged_items = self._merge_news_items(items)
            self.query_one(NewsFeedWidget).set_items(merged_items)
        except NoMatches:
            pass

    def _show_news_error(self, message: str) -> None:
        try:
            self.query_one(NewsFeedWidget).set_error(message)
        except NoMatches:
            pass

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
