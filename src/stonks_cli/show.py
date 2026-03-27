"""CLI formatted output for portfolio show command."""

from stonks_cli.fetcher import exchange_label
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import Portfolio

_SESSION_BADGES = {"pre": " PRE", "post": " AH", "closed": " CLS"}


def _daily_chg_str(last: float, prev: float | None, session: str) -> str:
    """Return a plain-text daily change string for the given price and session."""
    if prev is None or prev == 0 or session == "closed":
        return "--"
    pct = (last - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def format_show_table(portfolio: Portfolio, snap: MarketSnapshot) -> str:
    """Build a plain-text table for a single portfolio."""
    prices = snap.prices
    sessions = snap.sessions
    exchange_codes = snap.exchange_codes
    forex_rates = snap.forex_rates
    prev_closes = snap.prev_closes

    headers = (
        "Instrument",
        "Exchange",
        "Qty",
        "Avg Cost",
        "Last Price",
        "Daily chg",
        "Mkt Value",
        "Unrealized P&L",
    )

    rows: list[tuple[str, ...]] = []

    for pos in portfolio.positions:
        symbol = pos.symbol
        exchange = exchange_label(symbol, exchange_codes.get(symbol), pos.asset_type)
        qty = str(pos.quantity)
        avg_cost = f"{pos.avg_cost:.2f}"
        last_price = prices.get(symbol)
        session = sessions.get(symbol, "regular")
        badge = _SESSION_BADGES.get(session, "")
        instrument = f"{symbol}{badge}"
        if last_price is None:
            rows.append(
                (instrument, exchange, qty, avg_cost, "N/A", "--", "N/A", "N/A")
            )
        else:
            last_price_str = f"{last_price:.2f}"
            daily_chg_str = _daily_chg_str(last_price, prev_closes.get(symbol), session)
            mkt_value_str = f"{last_price * pos.quantity:,.2f}"
            pnl = (last_price - pos.avg_cost) * pos.quantity
            pnl_str = f"{pnl:+,.2f}"
            rows.append(
                (
                    instrument,
                    exchange,
                    qty,
                    avg_cost,
                    last_price_str,
                    daily_chg_str,
                    mkt_value_str,
                    pnl_str,
                )
            )

    for item in portfolio.watchlist:
        symbol = item.symbol
        exchange = exchange_label(symbol, exchange_codes.get(symbol), item.asset_type)
        last_price = prices.get(symbol)
        session = sessions.get(symbol, "regular")
        badge = _SESSION_BADGES.get(session, "")
        instrument = f"{symbol}{badge}"
        if last_price is None:
            daily_chg_str = "--"
            last_price_str = "N/A"
        else:
            last_price_str = f"{last_price:.2f}"
            daily_chg_str = _daily_chg_str(last_price, prev_closes.get(symbol), session)
        rows.append(
            (instrument, exchange, "-", "-", last_price_str, daily_chg_str, "-", "-")
        )

    for cash_pos in portfolio.cash:
        currency = cash_pos.currency
        amount = cash_pos.amount
        rates = forex_rates.get(portfolio.base_currency, {})
        rate = rates.get(currency, 1.0)
        converted = amount * rate
        rows.append(
            (
                f"{currency} Cash",
                "-",
                f"{amount:,.2f}",
                "1.00",
                f"{rate:.4f}",
                "--",
                f"{converted:,.2f}",
                "-",
            )
        )

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    lines = [header_line, "-" * len(header_line)]

    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, col_widths)))

    rates = forex_rates.get(portfolio.base_currency, {})
    missing_price = any(prices.get(pos.symbol) is None for pos in portfolio.positions)
    missing_rate = any(
        rates.get(p.currency) is None for p in portfolio.positions
    ) or any(rates.get(c.currency) is None for c in portfolio.cash)

    if missing_price or missing_rate:
        total_str = "N/A"
    else:
        stock_total = sum(
            pos.market_value(prices[pos.symbol]) * rates[pos.currency]
            for pos in portfolio.positions
        )
        cash_total = sum(
            cash_pos.amount * rates[cash_pos.currency] for cash_pos in portfolio.cash
        )
        total_str = f"{stock_total + cash_total:,.2f}"

    total_label = f"Total ({portfolio.base_currency})"
    # Align total value under the Mkt Value column.
    mkt_value_idx = headers.index("Mkt Value")
    pre_width = sum(col_widths[:mkt_value_idx]) + mkt_value_idx * 2
    total_line = (
        total_label
        + " "
        * (pre_width + col_widths[mkt_value_idx] - len(total_label) - len(total_str))
        + total_str
    )
    lines.append(total_line)

    return "\n".join(lines)
