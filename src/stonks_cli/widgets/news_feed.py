"""NewsFeedWidget and NewsItemRow -- scrollable news panel."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from stonks_cli.news_fetcher import NewsItem


class NewsItemRow(Horizontal):
    """Single-line news item with datetime, clickable headline, and source."""

    class Selected(Message):
        """Posted when a news item row is clicked."""

        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self, item: NewsItem, index: int) -> None:
        super().__init__(classes="news-row")
        self._item = item
        self._index = index

    def compose(self) -> ComposeResult:
        ticker = f" {self._item.symbol}" if self._item.symbol else ""
        yield Static(
            f"{self._item.published_at}{ticker}", classes="news-meta news-prefix"
        )
        yield Static(self._item.headline, classes="news-headline")
        yield Static(f" ({self._item.source})", classes="news-meta")

    def on_click(self) -> None:
        self.post_message(self.Selected(self._index))


class NewsFeedWidget(Widget, can_focus=True, can_focus_children=False):
    """Fixed-height scrollable news panel shown below portfolio tables."""

    class OpenURL(Message):
        """Request to open a URL in the system browser."""

        def __init__(self, url: str) -> None:
            self.url = url
            super().__init__()

    BINDINGS = [
        Binding("up", "select_prev", show=False),
        Binding("down", "select_next", show=False),
        Binding("enter", "open_selected", "Open link", show=False),
    ]

    DEFAULT_CSS = """
    NewsFeedWidget {
        height: 10;
        overflow-y: auto;
        border-top: solid $accent;
        padding: 0 1;
    }
    NewsFeedWidget:focus {
        border-top: solid $accent-lighten-2;
    }
    #news-header {
        height: auto;
    }
    #news-items {
        height: auto;
    }
    .news-headline {
        width: auto;
    }
    .news-meta {
        height: auto;
        color: $text-muted;
        width: auto;
    }
    .news-prefix {
        width: 21;
    }
    .news-row {
        height: auto;
    }
    .news-row.selected {
        background: $boost;
    }
    .news-row.selected .news-headline {
        text-style: bold underline;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._items_data: list[NewsItem] = []
        self._selected_index = 0

    def compose(self) -> ComposeResult:
        yield Static("News", id="news-header")
        yield Vertical(Static("Loading news...", classes="news-meta"), id="news-items")

    def set_items(self, items: list[NewsItem]) -> None:
        self._items_data = items
        if items:
            self._selected_index = min(self._selected_index, len(items) - 1)
        else:
            self._selected_index = 0
        self.query_one("#news-header", Static).update("[bold]News[/bold]")
        self._render_items(items)

    def set_error(self, message: str) -> None:
        self._items_data = []
        self._selected_index = 0
        self.query_one("#news-header", Static).update("[bold]News[/bold]")
        self._render_error(message)

    @work(group="news-panel", exclusive=True, exit_on_error=False)
    async def _render_items(self, items: list[NewsItem]) -> None:
        body = self.query_one("#news-items", Vertical)
        widgets: list[Widget] = []
        if not items:
            widgets.append(Static("No recent news.", classes="news-meta"))
        else:
            for index, item in enumerate(items):
                widgets.append(NewsItemRow(item, index))
        async with body.batch():
            await body.remove_children()
            await body.mount_all(widgets)
        self._apply_selection()

    @work(group="news-panel", exclusive=True, exit_on_error=False)
    async def _render_error(self, message: str) -> None:
        body = self.query_one("#news-items", Vertical)
        async with body.batch():
            await body.remove_children()
            await body.mount(Static(message, classes="news-meta"))

    def watch_has_focus(self, _has_focus: bool) -> None:
        super().watch_has_focus(_has_focus)
        self._apply_selection()

    def _rows(self) -> list[NewsItemRow]:
        return list(self.query(NewsItemRow))

    def _apply_selection(self) -> None:
        rows = self._rows()
        for index, row in enumerate(rows):
            if self.has_focus and index == self._selected_index:
                row.add_class("selected")
                row.scroll_visible(immediate=True)
            else:
                row.remove_class("selected")

    def _select_index(self, index: int) -> None:
        if not self._items_data:
            return
        self._selected_index = max(0, min(index, len(self._items_data) - 1))
        self._apply_selection()

    def action_select_prev(self) -> None:
        self._select_index(self._selected_index - 1)

    def action_select_next(self) -> None:
        self._select_index(self._selected_index + 1)

    def action_open_selected(self) -> None:
        if not self._items_data:
            return
        item = self._items_data[self._selected_index]
        if item.url:
            self.post_message(self.OpenURL(item.url))

    def select_item(self, index: int, open_link: bool = False) -> None:
        self.focus()
        self._select_index(index)
        if open_link:
            self.action_open_selected()

    def on_news_item_row_selected(self, message: NewsItemRow.Selected) -> None:
        self.select_item(message.index, open_link=True)
