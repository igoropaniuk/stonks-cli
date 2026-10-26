"""Modal form dialogs for add/edit operations in the portfolio TUI."""

from datetime import date
from typing import Any, TypeVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select
from textual.widgets._select import NoSelection

from stonks_cli.bonds import DEFAULT_FACE_VALUE, DEFAULT_FREQUENCY, BondInfo
from stonks_cli.dto import (
    BacktestConfig,
    BondFields,
    CashResult,
    EquityResult,
    WatchResult,
)

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


def _validate_non_negative_float(raw: str, label_str: str, err: Label) -> float | None:
    """Parse *raw* as a float >= 0; update *err* and return None on failure."""
    try:
        val = float(raw)
        if val < 0:
            raise ValueError
    except ValueError:
        err.update(f"{label_str} must be zero or a positive number")
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

_BOND_ASSET_TYPE = "bond"

_FREQUENCY_OPTIONS: list[tuple[str, int]] = [
    ("Semi-annual", 2),
    ("Annual", 1),
    ("Quarterly", 4),
    ("Monthly", 12),
]


# ---------------------------------------------------------------------------
# Base form screen
# ---------------------------------------------------------------------------


class BaseFormScreen(ModalScreen[_FormResultT | None]):
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


class TypeSelectScreen(ModalScreen[str | None]):
    """Ask whether the new position is equity or cash."""

    CSS = _MODAL_CSS.format(cls="TypeSelectScreen")
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


class EquityFormScreen(BaseFormScreen[EquityResult]):
    """Form for adding or editing an equity, crypto, ETF or bond position.

    Choosing the "Bond" asset type reveals the bond-terms fields and hides
    the external-ID field, which bonds don't use.
    """

    AUTO_FOCUS = "#symbol"

    # The bond fields make the form taller than small terminals; let it scroll.
    DEFAULT_CSS = """
    EquityFormScreen > Vertical { max-height: 100%; overflow-y: auto; }
    EquityFormScreen .pair { height: auto; }
    EquityFormScreen .pair > Vertical { width: 1fr; height: auto; }
    EquityFormScreen .pair > Vertical:first-child { margin-right: 1; }
    EquityFormScreen #bond-fields { height: auto; }
    EquityFormScreen #external-fields { height: auto; }
    EquityFormScreen .hint { color: $text-muted; }
    """

    def __init__(
        self,
        title: str = "Add Equity Position",
        symbol: str = "",
        qty: str = "",
        avg_cost: str = "",
        currency: str = "USD",
        asset_type: str | None = None,
        external_id: str = "",
        bond: BondInfo | None = None,
    ) -> None:
        super().__init__(title)
        self._symbol = symbol
        self._qty = qty
        self._avg_cost = avg_cost
        self._currency = currency
        self._asset_type = _BOND_ASSET_TYPE if bond is not None else asset_type
        self._external_id = external_id
        self._bond = bond

    def compose(self) -> ComposeResult:
        bond = self._bond
        with Vertical():
            yield Label(self._title)
            yield Label("Symbol", classes="field-label")
            yield Input(value=self._symbol, placeholder="e.g. AAPL", id="symbol")
            yield Label("Quantity", classes="field-label", id="qty-label")
            yield Input(value=self._qty, placeholder="e.g. 10 or 0.25", id="qty")
            yield Label("Avg Cost", classes="field-label", id="avg-cost-label")
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
            with Vertical(id="external-fields"):
                yield Label(
                    "External ID (e.g. CoinGecko coin ID)", classes="field-label"
                )
                yield Input(
                    value=self._external_id,
                    placeholder="e.g. bitcoin",
                    id="external_id",
                )
            with Vertical(id="bond-fields"):
                yield Label(
                    "Leave maturity and coupon blank for a bond ETF.",
                    classes="field-label hint",
                )
                with Horizontal(classes="pair"):
                    with Vertical():
                        yield Label("Maturity", classes="field-label")
                        yield Input(
                            value=bond.maturity.isoformat() if bond else "",
                            placeholder="YYYY-MM-DD",
                            id="maturity",
                        )
                    with Vertical():
                        yield Label("Coupon %", classes="field-label")
                        yield Input(
                            value=f"{bond.coupon:g}" if bond else "",
                            placeholder="e.g. 5.0",
                            id="coupon",
                        )
                with Horizontal(classes="pair"):
                    with Vertical():
                        yield Label("Face value", classes="field-label")
                        yield Input(
                            value=f"{bond.face_value:g}"
                            if bond
                            else f"{DEFAULT_FACE_VALUE:g}",
                            id="face_value",
                        )
                    with Vertical():
                        yield Label("Coupons", classes="field-label")
                        yield Select(
                            _FREQUENCY_OPTIONS,
                            value=bond.frequency if bond else DEFAULT_FREQUENCY,
                            allow_blank=False,
                            id="frequency",
                        )
                with Horizontal(classes="pair"):
                    with Vertical():
                        yield Label("CUSIP (optional)", classes="field-label")
                        yield Input(
                            value=(bond.cusip or "") if bond else "",
                            placeholder="e.g. 91282CRN3",
                            id="cusip",
                        )
                    with Vertical():
                        yield Label("Price % (optional)", classes="field-label")
                        yield Input(
                            value=f"{bond.price:g}"
                            if bond and bond.price is not None
                            else "",
                            placeholder="auto",
                            id="bond_price",
                        )
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync_bond_fields(self._asset_type)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "asset_type":
            value = event.value
            self._sync_bond_fields(None if isinstance(value, NoSelection) else value)

    def _sync_bond_fields(self, asset_type: object) -> None:
        """Show bond fields for bonds, external ID for everything else."""
        is_bond = asset_type == _BOND_ASSET_TYPE
        self.query_one("#bond-fields").display = is_bond
        self.query_one("#external-fields").display = not is_bond
        self.query_one("#qty-label", Label).update(
            "Quantity (number of bonds)" if is_bond else "Quantity"
        )
        self.query_one("#avg-cost-label", Label).update(
            "Avg Cost (% of par)" if is_bond else "Avg Cost"
        )

    def _read_bond_fields(self, err: Label) -> BondFields | None:
        """Validate the bond inputs; update *err* and return None on failure."""
        maturity_raw = self.query_one("#maturity", Input).value.strip()
        if not _validate_required(maturity_raw, "Maturity", err):
            return None
        try:
            maturity = date.fromisoformat(maturity_raw)
        except ValueError:
            err.update("Maturity must be a date like 2031-09-30")
            return None
        coupon = _validate_non_negative_float(
            self.query_one("#coupon", Input).value.strip(), "Coupon", err
        )
        if coupon is None:
            return None
        face_value = _validate_positive_float(
            self.query_one("#face_value", Input).value.strip(), "Face value", err
        )
        if face_value is None:
            return None
        frequency_val = self.query_one("#frequency", Select).value
        frequency = (
            DEFAULT_FREQUENCY
            if isinstance(frequency_val, NoSelection)
            else int(frequency_val)
        )
        cusip = self.query_one("#cusip", Input).value.strip().upper() or None
        if cusip is not None and (len(cusip) != 9 or not cusip.isalnum()):
            err.update("CUSIP must be 9 letters or digits")
            return None
        price_raw = self.query_one("#bond_price", Input).value.strip()
        price: float | None = None
        if price_raw:
            price = _validate_positive_float(price_raw, "Price", err)
            if price is None:
                return None
        return BondFields(
            maturity=maturity,
            coupon=coupon,
            face_value=face_value,
            frequency=frequency,
            cusip=cusip,
            price=price,
        )

    def _submit(self) -> None:
        symbol = self.query_one("#symbol", Input).value.strip().upper()
        qty_str = self.query_one("#qty", Input).value.strip()
        avg_cost_str = self.query_one("#avg_cost", Input).value.strip()
        currency = self.query_one("#currency", Input).value.strip().upper() or "USD"
        asset_type_val = self.query_one("#asset_type", Select).value
        asset_type: str | None = (
            None if isinstance(asset_type_val, NoSelection) else asset_type_val
        )
        is_bond = asset_type == _BOND_ASSET_TYPE
        external_id = (
            None
            if is_bond
            else self.query_one("#external_id", Input).value.strip() or None
        )
        err = self.query_one("#error", Label)
        if not _validate_required(symbol, "Symbol", err):
            return
        qty = _validate_positive_float(qty_str, "Quantity", err)
        if qty is None:
            return
        avg_cost = _validate_positive_float(avg_cost_str, "Avg cost", err)
        if avg_cost is None:
            return
        result = EquityResult(
            symbol=symbol,
            qty=qty,
            avg_cost=avg_cost,
            currency=currency,
            asset_type=asset_type,
            external_id=external_id,
        )
        if is_bond and self._has_bond_terms():
            bond = self._read_bond_fields(err)
            if bond is None:
                return
            result["bond"] = bond
        self.dismiss(result)

    def _has_bond_terms(self) -> bool:
        """False when maturity and coupon are both blank (e.g. a bond ETF)."""
        return any(
            self.query_one(f"#{field}", Input).value.strip()
            for field in ("maturity", "coupon")
        )


class CashFormScreen(BaseFormScreen[CashResult]):
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


class WatchFormScreen(BaseFormScreen[WatchResult]):
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


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    AUTO_FOCUS = "#yes"
    CSS = _MODAL_CSS.format(cls="ConfirmScreen").replace(
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


class BacktestFormScreen(BaseFormScreen[BacktestConfig]):
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
            yield Checkbox(
                "Skip positions without historical data",
                value=True,
                id="skip_unavailable",
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
        skip_unavailable = self.query_one("#skip_unavailable", Checkbox).value
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
                skip_unavailable=skip_unavailable,
            )
        )
