"""Modal form dialogs for add/edit operations in the portfolio TUI."""

from typing import Any, TypeVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select
from textual.widgets._select import NoSelection

from stonks_cli.dto import BacktestConfig, CashResult, EquityResult, WatchResult

_FormResultT = TypeVar("_FormResultT")


# ---------------------------------------------------------------------------
# Shared CSS template
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

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Asset type options (shared across equity and watchlist forms)
# ---------------------------------------------------------------------------

_ASSET_TYPE_OPTIONS: list[tuple[str, str | None]] = [
    ("Equity (default)", None),
    ("Crypto", "crypto"),
    ("ETF", "etf"),
    ("Bond", "bond"),
    ("Commodity", "commodity"),
    ("Forex", "forex"),
]


# ---------------------------------------------------------------------------
# Base form screen
# ---------------------------------------------------------------------------


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
        elif event.key == "enter" and isinstance(self.focused, Input):
            event.stop()
            self._submit()

    def _submit(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Concrete form screens
# ---------------------------------------------------------------------------


class _TypeSelectScreen(ModalScreen[str | None]):
    """Ask whether the new position is equity or cash."""

    CSS = _MODAL_CSS.format(cls="_TypeSelectScreen")
    AUTO_FOCUS = "#equity"

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


class _EquityFormScreen(_BaseFormScreen[EquityResult]):
    """Form for adding or editing an equity position."""

    AUTO_FOCUS = "#symbol"

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
            EquityResult(
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                currency=currency,
                asset_type=asset_type,
                external_id=external_id,
            )
        )


class _CashFormScreen(_BaseFormScreen[CashResult]):
    """Form for adding or editing a cash position."""

    AUTO_FOCUS = "#currency"

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
        self.dismiss(CashResult(currency=currency, amount=amount))


class _WatchFormScreen(_BaseFormScreen[WatchResult]):
    """Form for adding or editing a watchlist item."""

    AUTO_FOCUS = "#symbol"

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
            WatchResult(symbol=symbol, asset_type=asset_type, external_id=external_id)
        )


class _ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    AUTO_FOCUS = "#yes"
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


_REBALANCE_OPTIONS: list[tuple[str, str]] = [
    ("Don't rebalance", "none"),
    ("Rebalance monthly", "monthly"),
    ("Rebalance annually", "annual"),
]


class _BacktestFormScreen(_BaseFormScreen[BacktestConfig]):
    """Form for configuring a portfolio backtest."""

    AUTO_FOCUS = "#benchmark"

    def __init__(
        self,
        title: str = "Backtest Configuration",
        benchmark: str = "SPY",
        start_amount: str = "10000",
        start_year: str = "2010",
        end_year: str = "",
        cashflows: str = "0",
        rebalance: str = "none",
    ) -> None:
        super().__init__(title)
        self._benchmark = benchmark
        self._start_amount = start_amount
        self._start_year = start_year
        from datetime import datetime

        self._end_year = end_year or str(datetime.now().year)
        self._cashflows = cashflows
        self._rebalance = rebalance

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label("Benchmark Symbol", classes="field-label")
            yield Input(value=self._benchmark, placeholder="e.g. SPY", id="benchmark")
            yield Label("Starting Amount", classes="field-label")
            yield Input(
                value=self._start_amount, placeholder="e.g. 10000", id="start_amount"
            )
            yield Label("Start Year", classes="field-label")
            yield Input(
                value=self._start_year, placeholder="e.g. 2010", id="start_year"
            )
            yield Label("End Year", classes="field-label")
            yield Input(value=self._end_year, placeholder="e.g. 2026", id="end_year")
            yield Label("Yearly Cashflows", classes="field-label")
            yield Input(
                value=self._cashflows,
                placeholder="e.g. 0 (yearly contribution)",
                id="cashflows",
            )
            yield Label("Rebalancing", classes="field-label")
            yield Select(
                _REBALANCE_OPTIONS,
                value=self._rebalance,
                allow_blank=False,
                id="rebalance",
            )
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Run Backtest", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def _submit(self) -> None:
        benchmark = self.query_one("#benchmark", Input).value.strip().upper()
        start_str = self.query_one("#start_amount", Input).value.strip()
        start_year_str = self.query_one("#start_year", Input).value.strip()
        end_year_str = self.query_one("#end_year", Input).value.strip()
        cashflows_str = self.query_one("#cashflows", Input).value.strip()
        rebalance = self.query_one("#rebalance", Select).value
        err = self.query_one("#error", Label)
        if not _validate_required(benchmark, "Benchmark symbol", err):
            return
        start_amount = _validate_positive_float(start_str, "Starting amount", err)
        if start_amount is None:
            return
        try:
            start_year = int(start_year_str)
            end_year = int(end_year_str)
            if start_year < 1970 or end_year < start_year:
                raise ValueError
        except ValueError:
            err.update("Invalid year range")
            return
        try:
            cashflows = float(cashflows_str)
            if cashflows < 0:
                raise ValueError
        except ValueError:
            err.update("Cashflows must be a non-negative number")
            return
        self.dismiss(
            BacktestConfig(
                benchmark=benchmark,
                start_amount=start_amount,
                start_year=start_year,
                end_year=end_year,
                cashflows=cashflows,
                rebalance=str(rebalance),
            )
        )
