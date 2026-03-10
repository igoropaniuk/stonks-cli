"""Textual TUI for portfolio display."""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header

from stonks_cli.models import Portfolio


class PortfolioApp(App):
    """Full-screen portfolio table with live P&L colouring."""

    TITLE = "Portfolio"
    BINDINGS = [("q", "quit", "Quit")]

    CSS = """
    DataTable {
        height: auto;
    }
    """

    def __init__(self, portfolio: Portfolio, prices: dict[str, float]) -> None:
        super().__init__()
        self.portfolio = portfolio
        self.prices = prices

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(
            "Instrument", "Qty", "Avg Cost", "Last Price", "Mkt Value", "Unrealized P&L"
        )

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
