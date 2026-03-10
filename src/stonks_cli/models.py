"""Domain models for the stonks portfolio tracker."""

from dataclasses import dataclass, field


@dataclass
class Position:
    """Represents a single holding in the portfolio.

    Attributes:
        symbol: The stock ticker (e.g. 'AAPL').
        quantity: Number of shares held.
        avg_cost: Average cost per share paid.
        currency: Currency of the position (default 'USD').
    """

    symbol: str
    quantity: int
    avg_cost: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Symbol cannot be empty")
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")
        if self.avg_cost <= 0:
            raise ValueError("Average cost must be positive")
        self.symbol = self.symbol.upper()

    def market_value(self, last_price: float) -> float:
        """Return total market value at the given price."""
        return self.quantity * last_price

    def unrealized_pnl(self, last_price: float) -> float:
        """Return unrealized P&L at the given price."""
        return (last_price - self.avg_cost) * self.quantity


@dataclass
class Portfolio:
    """A collection of positions.

    Attributes:
        positions: List of current holdings.
    """

    positions: list[Position] = field(default_factory=list)

    def __post_init__(self) -> None:
        symbols = [p.symbol for p in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("Duplicate symbols in portfolio")

    def get_position(self, symbol: str) -> Position | None:
        """Return the position for *symbol*, or None if not held."""
        symbol = symbol.upper()
        return next((p for p in self.positions if p.symbol == symbol), None)

    def add_position(self, symbol: str, quantity: int, avg_cost: float) -> None:
        """Add shares to the portfolio.

        If the symbol is already held, quantity is increased and avg_cost is
        recalculated as a weighted average.  Otherwise a new position is created.
        """
        existing = self.get_position(symbol)
        if existing is not None:
            total_qty = existing.quantity + quantity
            existing.avg_cost = (
                existing.quantity * existing.avg_cost + quantity * avg_cost
            ) / total_qty
            existing.quantity = total_qty
        else:
            self.positions.append(
                Position(symbol=symbol, quantity=quantity, avg_cost=avg_cost)
            )

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
