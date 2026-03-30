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
from typing import Any, NamedTuple

from rich.text import Text

from stonks_cli.exchanges import exchange_label
from stonks_cli.market_session import SESSION_BADGE, Session
from stonks_cli.models import (
    Portfolio,
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
    session: str  # Session.* value
    chg_pct: float | None  # daily change %; None when not computable
    mkt_value: (
        float | None
    )  # qty * last (base currency for cash); None when unavailable
    pnl: float | None  # unrealised P&L; None for watchlist/cash


def build_row_data(
    portfolio: Portfolio,
    prices: dict[str, float],
    sessions: dict[str, str],
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

    for pos in portfolio.positions:
        last = prices.get(pos.symbol)
        session = sessions.get(pos.symbol, Session.REGULAR)
        chg_pct = (
            daily_change_pct(last, prev_closes.get(pos.symbol), session)
            if last is not None
            else None
        )
        rows.append(
            RowData(
                kind=RowKind.POSITION,
                symbol=pos.symbol,
                exchange=exchange_label(
                    pos.symbol, exchange_codes.get(pos.symbol), pos.asset_type
                ),
                qty=pos.quantity,
                avg_cost=pos.avg_cost,
                last=last,
                session=session,
                chg_pct=chg_pct,
                mkt_value=pos.market_value(last) if last is not None else None,
                pnl=pos.unrealized_pnl(last) if last is not None else None,
            )
        )

    for item in portfolio.watchlist:
        last = prices.get(item.symbol)
        session = sessions.get(item.symbol, Session.REGULAR)
        chg_pct = (
            daily_change_pct(last, prev_closes.get(item.symbol), session)
            if last is not None
            else None
        )
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


# ---------------------------------------------------------------------------
# TUI-specific row types and rendering
# ---------------------------------------------------------------------------


class _RowMeta(NamedTuple):
    """Identifies a table row's kind and subject symbol/currency."""

    kind: RowKind
    symbol: str  # ticker for position/watchlist, currency code for cash


class _RowData(NamedTuple):
    """One table row: sort key, display cells, and row metadata."""

    sort_key: tuple[Any, ...]
    cells: tuple[str | Text, ...]
    meta: _RowMeta


_ROW_KIND_LABELS: dict[RowKind, str] = {
    RowKind.POSITION: "position",
    RowKind.CASH: "cash",
    RowKind.WATCHLIST: "watch",
}


def _fmt_qty(qty: float) -> str:
    """Format a position quantity, dropping the decimal point for whole numbers."""
    return str(int(qty)) if float(qty).is_integer() else str(qty)


def _format_chg_cell(
    chg_pct: float | None, dim: bool = False
) -> tuple[Text | str, float]:
    """Return ``(display_cell, sort_value)`` for the daily change column."""
    if chg_pct is None:
        cell: Text | str = Text("--", style="dim") if dim else "--"
        return cell, 0.0
    sign = "+" if chg_pct >= 0 else ""
    label = f"{sign}{chg_pct:.2f}%"
    color = "green" if chg_pct >= 0 else "red"
    style = f"dim {color}" if dim else color
    return Text(label, style=style), chg_pct


_SESSION_BADGE_STYLE: dict[str, str] = {
    Session.PRE: "bold yellow",
    Session.POST: "bold cyan",
    Session.CLOSED: "bold red",
}


def _format_price_cell(last: float, session: str) -> Text | str:
    """Return a price cell with a session badge appended when applicable."""
    badge = SESSION_BADGE.get(session)
    if badge:
        style = _SESSION_BADGE_STYLE.get(session, "")
        return Text(f"{last:.2f} ").append(badge, style=style)
    return f"{last:.2f}"


def _to_tui_rows(row_data: list[RowData]) -> list[_RowData]:
    """Convert shared :class:`RowData` objects to TUI-specific :class:`_RowData`.

    Applies Rich Text styling and builds sort keys on top of the
    presentation-agnostic values produced by :func:`build_row_data`.
    """
    rows: list[_RowData] = []
    for rd in row_data:
        if rd.kind == RowKind.POSITION:
            assert rd.qty is not None and rd.avg_cost is not None
            if rd.last is not None:
                pnl = rd.pnl if rd.pnl is not None else 0.0
                sign = "+" if pnl >= 0 else ""
                pnl_cell: str | Text = Text(
                    f"{sign}{pnl:,.2f}",
                    style="bold green" if pnl >= 0 else "bold red",
                )
                price_cell: str | Text = _format_price_cell(rd.last, rd.session)
                chg_cell: str | Text
                chg_cell, chg_val = _format_chg_cell(rd.chg_pct)
                mkt_value = rd.mkt_value if rd.mkt_value is not None else 0.0
                sort_key: tuple = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    rd.avg_cost,
                    rd.last,
                    chg_val,
                    mkt_value,
                    pnl,
                )
                mkt_value_cell: str | Text = f"{mkt_value:,.2f}"
            else:
                price_cell = "N/A"
                chg_cell = "--"
                mkt_value_cell = "N/A"
                pnl_cell = "N/A"
                sort_key = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    rd.avg_cost,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
            display: tuple = (
                rd.symbol,
                rd.exchange,
                _fmt_qty(rd.qty),
                f"{rd.avg_cost:.2f}",
                price_cell,
                chg_cell,
                mkt_value_cell,
                pnl_cell,
            )
            rows.append(
                _RowData(sort_key, display, _RowMeta(RowKind.POSITION, rd.symbol))
            )

        elif rd.kind == RowKind.CASH:
            assert rd.qty is not None
            if rd.last is not None:
                mkt_value_c = rd.mkt_value if rd.mkt_value is not None else 0.0
                sort_key = (
                    rd.symbol,
                    rd.exchange,
                    rd.qty,
                    1.0,
                    rd.last,
                    0.0,
                    mkt_value_c,
                    0.0,
                )
                price_cell_c: str = f"{rd.last:.4f}"
                mkt_value_cell_c: str = f"{mkt_value_c:,.2f}"
            else:
                sort_key = (rd.symbol, rd.exchange, rd.qty, 1.0, 0.0, 0.0, 0.0, 0.0)
                price_cell_c = "N/A"
                mkt_value_cell_c = "N/A"
            display_c: tuple = (
                rd.symbol,
                rd.exchange,
                f"{rd.qty:,.2f}",
                "1.00",
                price_cell_c,
                "--",
                mkt_value_cell_c,
                "--",
            )
            rows.append(
                _RowData(sort_key, display_c, _RowMeta(RowKind.CASH, rd.symbol))
            )

        else:  # WATCHLIST
            if rd.last is not None:
                price_cell_w: str | Text = _format_price_cell(rd.last, rd.session)
                chg_cell_w: str | Text
                chg_cell_w, chg_val_w = _format_chg_cell(rd.chg_pct, dim=True)
                sort_key_w: tuple = (
                    rd.symbol,
                    rd.exchange,
                    0,
                    0.0,
                    rd.last,
                    chg_val_w,
                    0.0,
                    0.0,
                )
            else:
                price_cell_w = Text("N/A", style="dim")
                chg_cell_w = Text("--", style="dim")
                sort_key_w = (rd.symbol, rd.exchange, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
            display_w: tuple = (
                Text(rd.symbol, style="dim"),
                Text(rd.exchange, style="dim"),
                Text("--", style="dim"),
                Text("--", style="dim"),
                price_cell_w,
                chg_cell_w,
                Text("--", style="dim"),
                Text("--", style="dim"),
            )
            rows.append(
                _RowData(sort_key_w, display_w, _RowMeta(RowKind.WATCHLIST, rd.symbol))
            )

    return rows
