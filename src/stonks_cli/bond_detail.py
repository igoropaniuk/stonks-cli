"""Bond detail screen: terms, price source, yield and value breakdown."""

from datetime import date

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from stonks_cli.bond_fetcher import SOURCE_DESCRIPTION, BondQuote
from stonks_cli.bonds import (
    accrued_interest,
    clean_value,
    coupon_period,
    years_to_maturity,
    yield_to_maturity,
)
from stonks_cli.models import Position

_FREQUENCY_NAMES = {1: "annually", 2: "semi-annually", 4: "quarterly", 12: "monthly"}


def _signed(value: float, places: int = 2) -> Text:
    sign = "+" if value >= 0 else ""
    return Text(f"{sign}{value:,.{places}f}", style="green" if value >= 0 else "red")


def build_bond_rows(
    pos: Position, quote: BondQuote | None, settle: date
) -> list[tuple[str, list[tuple[str, str | Text]]]]:
    """Return ``[(section, [(label, value)])]`` describing a bond position.

    Kept separate from the widget so the content can be unit-tested
    without running the TUI.
    """
    bond = pos.bond
    if bond is None:
        raise ValueError(f"{pos.symbol} is not a bond position")
    ccy = pos.currency
    matured = settle >= bond.maturity

    terms: list[tuple[str, str | Text]] = [
        ("CUSIP", bond.cusip or "--"),
        (
            "Coupon",
            f"{bond.coupon:.3f}% paid {_FREQUENCY_NAMES[bond.frequency]} "
            f"({bond.coupon_amount:,.2f} {ccy} per bond)",
        ),
        (
            "Maturity",
            f"{bond.maturity.isoformat()}"
            + (
                " (matured)"
                if matured
                else f" ({years_to_maturity(bond, settle):.2f} years)"
            ),
        ),
    ]
    if not matured:
        terms.append(("Next coupon", coupon_period(bond, settle)[1].isoformat()))

    position: list[tuple[str, str | Text]] = [
        (
            "Quantity",
            f"{pos.quantity:g} bonds x {bond.face_value:,.2f} {ccy} face "
            f"= {pos.quantity * bond.face_value:,.2f} {ccy}",
        ),
        ("Avg cost", f"{pos.avg_cost:.4f} (% of par)"),
    ]

    pricing: list[tuple[str, str | Text]]
    value: list[tuple[str, str | Text]]
    if quote is None:
        pricing = [
            ("Clean price", "N/A"),
            (
                "Source",
                "No price available. Add a Treasury cusip or a manual price.",
            ),
        ]
        value = [("Market value", "N/A")]
    else:
        source = SOURCE_DESCRIPTION[quote.source]
        if quote.as_of is not None:
            source += f", as of {quote.as_of.isoformat()}"
        pricing = [
            ("Clean price", f"{quote.price:.4f} (% of par)"),
            ("Source", source),
        ]
        if quote.prev_close is not None:
            pricing.append(
                (
                    "Previous close",
                    Text(f"{quote.prev_close:.4f}  (")
                    .append_text(_signed(quote.price - quote.prev_close, 4))
                    .append(")"),
                )
            )
        ytm = yield_to_maturity(bond, quote.price, settle)
        pricing.append(
            ("Yield to maturity", f"{ytm:.3f}%" if ytm is not None else "--")
        )

        accrued_each = accrued_interest(bond, settle)
        clean = clean_value(bond, pos.quantity, quote.price)
        accrued = accrued_each * pos.quantity
        value = [
            ("Clean value", f"{clean:,.2f} {ccy}"),
            (
                "Accrued interest",
                f"{accrued:,.2f} {ccy} ({accrued_each:,.2f} per bond)",
            ),
            ("Market value", f"{clean + accrued:,.2f} {ccy}"),
            ("Unrealized P&L", _signed(pos.unrealized_pnl(quote.price))),
        ]

    return [
        ("Terms", terms),
        ("Position", position),
        ("Pricing", pricing),
        ("Value", value),
    ]


def _render_table(rows: list[tuple[str, list[tuple[str, str | Text]]]]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    for i, (section, items) in enumerate(rows):
        if i:
            table.add_row("", "")
        table.add_row(Text(section, style="bold underline"), "")
        for label, value in items:
            table.add_row(label, value)
    return table


class BondDetailScreen(ModalScreen[None]):
    """Modal showing the terms, price source and value of a bond position."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    CSS = """
    BondDetailScreen { align: center middle; }
    BondDetailScreen > VerticalScroll {
        width: 80;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        border: solid $accent;
        padding: 1 2;
        background: $surface;
    }
    BondDetailScreen #bond-title { text-style: bold; color: $accent; }
    BondDetailScreen #bond-hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(
        self,
        pos: Position,
        quote: BondQuote | None,
        settle: date | None = None,
    ) -> None:
        super().__init__()
        self._pos = pos
        self._quote = quote
        self._settle = settle or date.today()

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(f"{self._pos.symbol}  (bond)", id="bond-title", markup=False)
            yield Static(
                _render_table(build_bond_rows(self._pos, self._quote, self._settle)),
                id="bond-body",
            )
            yield Label("Esc or Q to close", id="bond-hint")
