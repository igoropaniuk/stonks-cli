"""Pure portfolio mutation helpers used by the TUI action flows."""

import logging

from stonks_cli.dto import CashResult, EquityResult, WatchResult
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem
from stonks_cli.portfolio_table import RowKind

logger = logging.getLogger(__name__)


def watch_item(portfolio: Portfolio, symbol: str) -> WatchlistItem | None:
    """Return the watchlist item for *symbol*, or None if absent."""
    return next((w for w in portfolio.watchlist if w.symbol == symbol), None)


def add_equity(result: EquityResult, portfolio: Portfolio) -> str | None:
    """Apply an add-equity form result to *portfolio*; return an error or None."""
    is_new = portfolio.get_position(result["symbol"]) is None
    try:
        portfolio.add_position(result["symbol"], result["qty"], result["avg_cost"])
    except ValueError as exc:
        logger.warning("Failed to add equity %s: %s", result["symbol"], exc)
        return str(exc)
    pos = portfolio.get_position(result["symbol"])
    if pos:
        if is_new:
            pos.currency = result["currency"]
        pos.asset_type = result.get("asset_type")
        pos.external_id = result.get("external_id")
    return None


def add_cash(result: CashResult, portfolio: Portfolio) -> str | None:
    """Apply an add-cash form result to *portfolio*; return an error or None."""
    try:
        portfolio.add_cash(result["currency"], result["amount"])
    except ValueError as exc:
        logger.warning(
            "Failed to add cash %s %.2f: %s",
            result["currency"],
            result["amount"],
            exc,
        )
        return str(exc)
    return None


def add_watch(result: WatchResult, portfolio: Portfolio) -> str | None:
    """Apply an add-watch form result to *portfolio*; return an error or None."""
    symbol = result["symbol"]
    if watch_item(portfolio, symbol) is not None:
        return f"{symbol} is already in the watchlist"

    try:
        portfolio.watchlist.append(
            WatchlistItem(
                symbol,
                asset_type=result.get("asset_type"),
                external_id=result.get("external_id"),
            )
        )
    except ValueError as exc:
        logger.warning("Failed to add watch item %s: %s", symbol, exc)
        return str(exc)

    return None


def edit_cash(
    portfolio: Portfolio,
    cash_pos: CashPosition,
    result: CashResult,
) -> str | None:
    """Edit an existing cash position; return an error or None."""
    new_currency = result["currency"]
    if (
        new_currency != cash_pos.currency
        and portfolio.get_cash(new_currency) is not None
    ):
        return f"A {new_currency} cash position already exists"
    portfolio.cash.remove(cash_pos)
    try:
        portfolio.add_cash(new_currency, result["amount"])
    except ValueError as exc:
        logger.warning("Failed to edit cash position: %s", exc)
        portfolio.cash.append(cash_pos)
        return str(exc)
    return None


def edit_watch(
    portfolio: Portfolio,
    old_item: WatchlistItem,
    result: WatchResult,
) -> str | None:
    """Edit a watchlist item; return an error or None."""
    new_symbol = result["symbol"]
    if new_symbol != old_item.symbol and watch_item(portfolio, new_symbol) is not None:
        return f"{new_symbol} is already in the watchlist"
    try:
        old_item.update(new_symbol, result.get("asset_type"), result.get("external_id"))
    except ValueError as exc:
        logger.warning("Failed to edit watch item %s: %s", new_symbol, exc)
        return str(exc)
    return None


def edit_position(
    portfolio: Portfolio,
    pos: Position,
    result: EquityResult,
) -> str | None:
    """Edit an existing position; return an error or None."""
    new_symbol = result["symbol"]
    if new_symbol != pos.symbol and portfolio.get_position(new_symbol):
        return f"{new_symbol} already exists in this portfolio"
    try:
        pos.update(
            new_symbol,
            result["qty"],
            result["avg_cost"],
            result["currency"],
            result.get("asset_type"),
            result.get("external_id"),
        )
    except ValueError as exc:
        logger.warning("Failed to edit position %s: %s", new_symbol, exc)
        return str(exc)
    return None


def remove_selected_item(portfolio: Portfolio, kind: RowKind, identifier: str) -> None:
    """Remove the selected item from *portfolio* when it still exists."""
    _REMOVE_HANDLERS[kind](portfolio, identifier)


def _remove_cash_item(portfolio: Portfolio, identifier: str) -> None:
    cash_pos = portfolio.get_cash(identifier)
    if cash_pos:
        portfolio.cash.remove(cash_pos)


def _remove_watch_item(portfolio: Portfolio, identifier: str) -> None:
    item = watch_item(portfolio, identifier)
    if item:
        portfolio.watchlist.remove(item)


def _remove_position_item(portfolio: Portfolio, identifier: str) -> None:
    pos = portfolio.get_position(identifier)
    if pos:
        portfolio.positions.remove(pos)


_REMOVE_HANDLERS = {
    RowKind.CASH: _remove_cash_item,
    RowKind.WATCHLIST: _remove_watch_item,
    RowKind.POSITION: _remove_position_item,
}
