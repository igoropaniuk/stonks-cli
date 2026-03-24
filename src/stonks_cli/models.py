"""Domain models for the stonks portfolio tracker."""

from dataclasses import dataclass, field


@dataclass
class CashPosition:
    """A cash holding in a given currency.

    Attributes:
        currency: ISO 4217 currency code (e.g. 'USD', 'EUR').
        amount: Amount of cash held (positive).
    """

    currency: str
    amount: float

    def __post_init__(self) -> None:
        if not self.currency:
            raise ValueError("Currency cannot be empty")
        if self.amount <= 0:
            raise ValueError("Amount must be positive")
        self.currency = self.currency.upper()


@dataclass
class Position:
    """Represents a single holding in the portfolio.

    Attributes:
        symbol: The stock ticker (e.g. 'AAPL').
        quantity: Number of shares held.
        avg_cost: Average cost per share paid.
        currency: Currency of the position (default 'USD').
        exchange_suffix: Optional exchange suffix for multi-exchange symbols
            (e.g., '.AS' for Amsterdam, '.L' for London).
    """

    symbol: str
    quantity: int
    avg_cost: float
    currency: str = "USD"
    exchange_suffix: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Symbol cannot be empty")
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")
        if self.avg_cost <= 0:
            raise ValueError("Average cost must be positive")
        self.symbol = self.symbol.upper()
        if self.exchange_suffix is not None:
            self.exchange_suffix = self.exchange_suffix.upper()
            if not self.exchange_suffix.startswith("."):
                self.exchange_suffix = "." + self.exchange_suffix

    def full_symbol(self) -> str:
        """Return the full symbol including exchange suffix."""
        if self.exchange_suffix:
            return f"{self.symbol}{self.exchange_suffix}"
        return self.symbol

    def market_value(self, last_price: float) -> float:
        """Return total market value at the given price."""
        return self.quantity * last_price

    def unrealized_pnl(self, last_price: float) -> float:
        """Return unrealized P&L at the given price."""
        return (last_price - self.avg_cost) * self.quantity


@dataclass
class WatchlistItem:
    """A ticker tracked for price only (no holdings).

    Attributes:
        symbol: The stock ticker (e.g. 'TSLA').
        exchange_suffix: Optional exchange suffix for multi-exchange symbols
            (e.g., '.AS' for Amsterdam, '.L' for London).
    """

    symbol: str
    exchange_suffix: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Symbol cannot be empty")
        self.symbol = self.symbol.upper()
        if self.exchange_suffix is not None:
            self.exchange_suffix = self.exchange_suffix.upper()
            if not self.exchange_suffix.startswith("."):
                self.exchange_suffix = "." + self.exchange_suffix

    def full_symbol(self) -> str:
        """Return the full symbol including exchange suffix."""
        if self.exchange_suffix:
            return f"{self.symbol}{self.exchange_suffix}"
        return self.symbol


@dataclass
class Portfolio:
    """A collection of positions and cash holdings.

    Attributes:
        positions: List of current stock holdings.
        cash: List of cash holdings by currency.
        base_currency: Currency used for the portfolio total (default 'USD').
        name: Human-readable label for the portfolio (optional).
    """

    positions: list[Position] = field(default_factory=list)
    cash: list[CashPosition] = field(default_factory=list)
    watchlist: list[WatchlistItem] = field(default_factory=list)
    base_currency: str = "USD"
    name: str = ""

    def __post_init__(self) -> None:
        self.base_currency = self.base_currency.upper()
        position_keys = [p.full_symbol() for p in self.positions]
        if len(position_keys) != len(set(position_keys)):
            raise ValueError("Duplicate symbols in portfolio")
        watch_keys = [w.full_symbol() for w in self.watchlist]
        if len(watch_keys) != len(set(watch_keys)):
            raise ValueError("Duplicate symbols in watchlist")
        currencies = [c.currency for c in self.cash]
        if len(currencies) != len(set(currencies)):
            raise ValueError("Duplicate currencies in cash positions")

    def get_cash(self, currency: str) -> CashPosition | None:
        """Return the cash position for *currency*, or None if not held."""
        currency = currency.upper()
        return next((c for c in self.cash if c.currency == currency), None)

    def add_cash(self, currency: str, amount: float) -> None:
        """Add *amount* of *currency* cash.

        If a cash position for the currency already exists, the amount is
        increased.  Otherwise a new cash position is created.
        """
        existing = self.get_cash(currency)
        if existing is not None:
            existing.amount += amount
        else:
            self.cash.append(CashPosition(currency=currency, amount=amount))

    def remove_cash(self, currency: str, amount: float) -> None:
        """Remove *amount* of *currency* cash.

        If *amount* equals the full holding the position is deleted.

        Raises:
            ValueError: If the currency is not held or amount exceeds the holding.
        """
        existing = self.get_cash(currency)
        if existing is None:
            raise ValueError(f"No {currency.upper()} cash position in portfolio")
        if amount > existing.amount:
            raise ValueError(
                f"Cannot remove {amount:.2f} {existing.currency}: "
                f"only {existing.amount:.2f} held"
            )
        if amount == existing.amount:
            self.cash.remove(existing)
        else:
            existing.amount -= amount

    def get_position(self, symbol: str) -> Position | None:
        """Return the position for *symbol*, or None if not held.

        Matches by base symbol only (ignores exchange suffix).
        """
        symbol = symbol.upper()
        return next((p for p in self.positions if p.symbol == symbol), None)

    def get_position_by_full_symbol(self, full_symbol: str) -> Position | None:
        """Return the position for the full symbol (with exchange suffix), or None."""
        full_symbol = full_symbol.upper()
        return next((p for p in self.positions if p.full_symbol() == full_symbol), None)

    def add_position(
        self,
        symbol: str,
        quantity: int,
        avg_cost: float,
        exchange_suffix: str | None = None,
    ) -> None:
        """Add shares to the portfolio.

        If the symbol is already held on the same exchange, quantity is increased
        and avg_cost is recalculated as a weighted average.  Otherwise a new
        position is created.
        """
        temp_pos = Position(
            symbol=symbol,
            quantity=quantity,
            avg_cost=avg_cost,
            exchange_suffix=exchange_suffix,
        )
        existing = self.get_position_by_full_symbol(temp_pos.full_symbol())
        if existing is not None:
            total_qty = existing.quantity + quantity
            existing.avg_cost = (
                existing.quantity * existing.avg_cost + quantity * avg_cost
            ) / total_qty
            existing.quantity = total_qty
        else:
            self.positions.append(temp_pos)

    def remove_position(self, symbol: str, quantity: int) -> None:
        """Remove shares from the portfolio.

        If *quantity* equals the full holding the position is deleted.
        If *quantity* is less, the holding is reduced.

        Raises:
            ValueError: If the symbol is not held or quantity exceeds the holding.
        """
        existing = self.get_position(symbol)
        if existing is None:
            raise ValueError(f"Position '{symbol.upper()}' not found in portfolio")
        if quantity > existing.quantity:
            raise ValueError(
                f"Cannot remove {quantity} shares of {existing.symbol}: "
                f"only {existing.quantity} held"
            )
        if quantity == existing.quantity:
            self.positions.remove(existing)
        else:
            existing.quantity -= quantity
