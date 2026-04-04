"""PortfolioTableWidget -- DataTable plus total bar for a single portfolio."""

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Static

from stonks_cli.market import MarketSnapshot
from stonks_cli.models import Portfolio, portfolio_total
from stonks_cli.portfolio_table import (
    TABLE_COLUMNS,
    _RowData,
    _RowMeta,
    _to_tui_rows,
    build_row_data,
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
        self.query_one(DataTable).add_columns(*TABLE_COLUMNS)

    def refresh_data(self, portfolio: Portfolio, snap: MarketSnapshot) -> None:
        """Push new data into the widget and repaint."""
        self._portfolio = portfolio
        self._snap = snap
        self._repaint()

    def _get_meta_for_key(self, rkey: str) -> _RowMeta | None:
        return self._row_meta.get(rkey)

    class RowSelected(Message):
        """Posted when the user selects a row in this widget's table."""

        def __init__(self, meta: _RowMeta) -> None:
            self.meta = meta
            super().__init__()

    class RowHighlighted(Message):
        """Posted when the cursor moves to a different row."""

        def __init__(
            self, widget: "PortfolioTableWidget", meta: _RowMeta | None
        ) -> None:  # noqa: E501
            self.widget = widget
            self.meta = meta
            super().__init__()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        meta = self._get_meta_for_key(str(event.row_key.value))
        if meta is not None:
            self.post_message(self.RowSelected(meta))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        meta = (
            self._get_meta_for_key(str(event.row_key.value))
            if event.row_key is not None
            else None
        )
        self.post_message(self.RowHighlighted(self, meta))

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
