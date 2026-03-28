"""Shared row view-model: compute raw row data for portfolio tables.

Both the TUI (app.py) and the CLI show command (show.py) need the same
underlying values per row -- symbol, exchange label, last price, change
percentage, market value, P&L.  This module provides a single computation
layer so neither consumer reimplements the logic independently.

The output is intentionally free of presentation concerns (no Rich Text,
no string formatting).  Each consumer applies its own rendering on top.
"""

from dataclasses import dataclass
from enum import Enum, auto
from itertools import chain

from stonks_cli._session import Session
from stonks_cli.fetcher import exchange_label
from stonks_cli.models import (
    Portfolio,
    Position,
    WatchlistItem,
    daily_change_pct,
)


class RowKind(Enum):
    POSITION = auto()
    WATCHLIST = auto()
    CASH = auto()


@dataclass
class RowData:
    """All computed values for one table row, presentation-agnostic."""

    kind: RowKind
    symbol: str  # ticker for positions/watchlist, currency code for cash
    exchange: str  # exchange label (or "Cash")
    qty: float | None  # shares / cash amount; None for watchlist
    avg_cost: float | None  # per-share cost; None for watchlist/cash
    last: float | None  # last price or FX rate for cash; None when unavailable
    session: Session  # Session.* value
    chg_pct: float | None  # daily change %; None when not computable
    mkt_value: float | None  # qty * last; None when unavailable
    pnl: float | None  # unrealised P&L; None for watchlist/cash


def build_row_data(
    portfolio: Portfolio,
    prices: dict[str, float],
    sessions: dict[str, Session],
    prev_closes: dict[str, float],
    exchange_codes: dict[str, str],
    rates: dict[str, float],
) -> list[RowData]:
    """Return one :class:`RowData` per portfolio item (positions, watchlist, cash).

    Args:
        portfolio: The portfolio to compute rows for.
        prices: Last prices keyed by symbol.
        sessions: Market session labels keyed by symbol.
        prev_closes: Previous closing prices keyed by symbol.
        exchange_codes: yfinance exchange codes keyed by symbol.
        rates: FX rates to base currency, keyed by position/cash currency.
    """
    rows: list[RowData] = []

    pos_and_watch: chain[Position | WatchlistItem] = chain(
        portfolio.positions, portfolio.watchlist
    )
    for item in pos_and_watch:
        last = prices.get(item.symbol)
        session = sessions.get(item.symbol, Session.REGULAR)
        chg_pct = (
            daily_change_pct(last, prev_closes.get(item.symbol), session)
            if last is not None
            else None
        )
        if isinstance(item, Position):
            rows.append(
                RowData(
                    kind=RowKind.POSITION,
                    symbol=item.symbol,
                    exchange=exchange_label(
                        item.symbol, exchange_codes.get(item.symbol), item.asset_type
                    ),
                    qty=item.quantity,
                    avg_cost=item.avg_cost,
                    last=last,
                    session=session,
                    chg_pct=chg_pct,
                    mkt_value=item.market_value(last) if last is not None else None,
                    pnl=item.unrealized_pnl(last) if last is not None else None,
                )
            )
        else:  # WatchlistItem
            rows.append(
                RowData(
                    kind=RowKind.WATCHLIST,
                    symbol=item.symbol,
                    exchange=exchange_label(
                        item.symbol, exchange_codes.get(item.symbol), item.asset_type
                    ),
                    qty=None,
                    avg_cost=None,
                    last=last,
                    session=session,
                    chg_pct=chg_pct,
                    mkt_value=None,
                    pnl=None,
                )
            )

    for cash_pos in portfolio.cash:
        rate = rates.get(cash_pos.currency)
        rows.append(
            RowData(
                kind=RowKind.CASH,
                symbol=cash_pos.currency,
                exchange="Cash",
                qty=cash_pos.amount,
                avg_cost=1.0,
                last=rate,
                session=Session.REGULAR,
                chg_pct=None,
                mkt_value=cash_pos.amount * rate if rate is not None else None,
                pnl=None,
            )
        )

    return rows
