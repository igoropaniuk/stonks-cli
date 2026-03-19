"""CLI formatted output for portfolio show command."""

from stonks_cli.fetcher import PriceFetcher, exchange_label
from stonks_cli.models import Portfolio

_SESSION_BADGES = {"pre": " PRE", "post": " AH", "closed": " CLS"}


def fetch_portfolio_data(
    portfolios: list[Portfolio],
) -> tuple[
    dict[str, float],
    dict[str, str],
    dict[str, str],
    dict[str, dict[str, float]],
]:
    """Fetch prices, sessions, exchange codes, and forex rates once.

    Returns a tuple of (prices, sessions, exchange_codes, forex_rates).
    """
    fetcher = PriceFetcher()
    all_symbols = list(
        {pos.symbol for portfolio in portfolios for pos in portfolio.positions}
        | {item.symbol for portfolio in portfolios for item in portfolio.watchlist}
    )

    extended = fetcher.fetch_extended_prices(all_symbols)
    prices = {sym: price for sym, (price, _) in extended.items()}
    sessions = {sym: sess for sym, (_, sess) in extended.items()}

    missing = [s for s in all_symbols if s not in prices]
    if missing:
        fallback = fetcher.fetch_prices(missing)
        prices.update(fallback)
        sessions.update({sym: fetcher.current_session(sym) for sym in fallback})

    still_missing = [s for s in missing if s not in prices]
    for sym in still_missing:
        price = fetcher.fetch_price_single(sym)
        if price is not None:
            prices[sym] = price
            sessions[sym] = fetcher.current_session(sym)

    exchange_codes = fetcher.fetch_exchange_names(all_symbols)

    all_currencies = list(
        {pos.currency for portfolio in portfolios for pos in portfolio.positions}
        | {c.currency for portfolio in portfolios for c in portfolio.cash}
    )
    forex_rates: dict[str, dict[str, float]] = {}
    for base in {p.base_currency for p in portfolios}:
        forex_rates[base] = fetcher.fetch_forex_rates(all_currencies, base=base)

    return prices, sessions, exchange_codes, forex_rates


def format_show_table(
    portfolio: Portfolio,
    prices: dict[str, float],
    sessions: dict[str, str],
    exchange_codes: dict[str, str],
    forex_rates: dict[str, dict[str, float]],
) -> str:
    """Build a plain-text table for a single portfolio."""
    headers = (
        "Instrument",
        "Exchange",
        "Qty",
        "Avg Cost",
        "Last Price",
        "Mkt Value",
        "Unrealized P&L",
    )

    rows: list[tuple[str, ...]] = []

    for pos in portfolio.positions:
        symbol = pos.symbol
        exchange = exchange_label(symbol, exchange_codes.get(symbol))
        qty = str(pos.quantity)
        avg_cost = f"{pos.avg_cost:.2f}"
        last_price = prices.get(symbol)
        if last_price is None:
            last_price_str = "N/A"
            mkt_value_str = "N/A"
            pnl_str = "N/A"
        else:
            last_price_str = f"{last_price:.2f}"
            mkt_value = last_price * pos.quantity
            mkt_value_str = f"{mkt_value:,.2f}"
            pnl = (last_price - pos.avg_cost) * pos.quantity
            pnl_str = f"{pnl:+,.2f}" if pnl >= 0 else f"{pnl:,.2f}"
        session = sessions.get(symbol, "regular")
        badge = _SESSION_BADGES.get(session, "")
        instrument = f"{symbol}{badge}"
        rows.append(
            (
                instrument,
                exchange,
                qty,
                avg_cost,
                last_price_str,
                mkt_value_str,
                pnl_str,
            )
        )

    for item in portfolio.watchlist:
        symbol = item.symbol
        exchange = exchange_label(symbol, exchange_codes.get(symbol))
        last_price = prices.get(symbol)
        if last_price is None:
            last_price_str = "N/A"
        else:
            last_price_str = f"{last_price:.2f}"
        session = sessions.get(symbol, "regular")
        badge = _SESSION_BADGES.get(session, "")
        instrument = f"{symbol}{badge}"
        rows.append((instrument, exchange, "-", "-", last_price_str, "-", "-"))

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
                f"{converted:,.2f}",
                "-",
            )
        )

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))
    lines = []
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    lines.append(header_line)
    lines.append("-" * len(header_line))

    for row in rows:
        line = "  ".join(cell.ljust(w) for cell, w in zip(row, col_widths))
        lines.append(line)

    widths = col_widths

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
