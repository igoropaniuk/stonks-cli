"""CLI entry point for the stonks portfolio tracker."""

from pathlib import Path

import click

from stonks_cli import __version__
from stonks_cli.app import PortfolioApp
from stonks_cli.fetcher import PriceFetcher, exchange_label
from stonks_cli.models import Portfolio
from stonks_cli.storage import (
    PORTFOLIO_CONFIG_DIR,
    PortfolioStore,
    seed_sample_portfolio,
)


def _resolve_portfolio_path(name_or_path: str | None) -> Path | None:
    """Resolve the -p value to a Path.

    A plain name with no path separators and no extension is treated as a
    shorthand for ``~/.config/stonks/<name>.yaml``.  Anything else is used
    as-is.
    """
    if name_or_path is None:
        return None
    p = Path(name_or_path)
    if p.parent == Path(".") and p.suffix == "":
        return PORTFOLIO_CONFIG_DIR / f"{name_or_path}.yaml"
    return p


@click.group(invoke_without_command=True)
@click.version_option(__version__, "--version", "-V")
@click.option(
    "-p",
    "--portfolio",
    type=click.Path(),
    multiple=True,
    help=(
        "Portfolio YAML file or name (repeatable). "
        "A plain name (e.g. 'work') resolves to "
        "~/.config/stonks/<name>.yaml. "
        "Defaults to ~/.config/stonks/portfolio.yaml."
    ),
)
@click.pass_context
def main(ctx: click.Context, portfolio: tuple[str, ...]) -> None:
    """CLI tool for tracking an investment portfolio."""
    ctx.ensure_object(dict)
    if not portfolio:
        if seed_sample_portfolio():
            dest = PORTFOLIO_CONFIG_DIR / "portfolio.yaml"
            click.echo(f"No portfolio found. Created sample portfolio at {dest}")
        stores = [PortfolioStore()]
    else:
        stores = [PortfolioStore(path=_resolve_portfolio_path(p)) for p in portfolio]
    ctx.obj["stores"] = stores
    ctx.obj["store"] = stores[0]
    if ctx.invoked_subcommand is None:
        ctx.invoke(dashboard)


@main.command()
@click.argument("symbol")
@click.argument("quantity", type=int)
@click.argument("price", type=float)
@click.pass_context
def add(ctx: click.Context, symbol: str, quantity: int, price: float) -> None:
    """Add QUANTITY shares of SYMBOL at PRICE to the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()
    portfolio.add_position(symbol, quantity, price)
    store.save(portfolio)
    pos = portfolio.get_position(symbol)
    assert pos is not None
    click.echo(
        f"Added {quantity} * {symbol.upper()} @ {price:.2f}  "
        f"(position: {pos.quantity} shares, avg cost {pos.avg_cost:.2f})"
    )


@main.command()
@click.argument("symbol")
@click.argument("quantity", type=int)
@click.pass_context
def remove(ctx: click.Context, symbol: str, quantity: int) -> None:
    """Remove QUANTITY shares of SYMBOL from the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()
    try:
        portfolio.remove_position(symbol, quantity)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    store.save(portfolio)
    click.echo(f"Removed {quantity} * {symbol.upper()}")


@main.command()
@click.option(
    "--refresh",
    default=5.0,
    show_default=True,
    type=float,
    help="Price refresh interval in seconds.",
)
@click.pass_context
def dashboard(ctx: click.Context, refresh: float) -> None:
    """Display the current portfolio with live prices and P&L."""
    stores: list[PortfolioStore] = ctx.obj["stores"]
    portfolios = [store.load() for store in stores]

    if all(not p.positions and not p.cash for p in portfolios):
        click.echo("Portfolio is empty.")
        return

    PortfolioApp(
        portfolios=portfolios,
        prices={},
        forex_rates={},
        refresh_interval=refresh,
        stores=stores,
    ).run()


@main.command("add-cash")
@click.argument("currency")
@click.argument("amount", type=float)
@click.pass_context
def add_cash(ctx: click.Context, currency: str, amount: float) -> None:
    """Add AMOUNT of CURRENCY cash to the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()
    portfolio.add_cash(currency, amount)
    store.save(portfolio)
    cash = portfolio.get_cash(currency)
    assert cash is not None
    click.echo(f"Added {amount:.2f} {currency.upper()}  (total: {cash.amount:.2f})")


@main.command("remove-cash")
@click.argument("currency")
@click.argument("amount", type=float)
@click.pass_context
def remove_cash(ctx: click.Context, currency: str, amount: float) -> None:
    """Remove AMOUNT of CURRENCY cash from the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()
    try:
        portfolio.remove_cash(currency, amount)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    store.save(portfolio)
    click.echo(f"Removed {amount:.2f} {currency.upper()}")


def _fetch_portfolio_data(
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
    )

    # Extended-hours prices (1-min bars with prepost).
    extended = fetcher.fetch_extended_prices(all_symbols)
    prices = {sym: price for sym, (price, _) in extended.items()}
    sessions = {sym: sess for sym, (_, sess) in extended.items()}

    # Fallback: daily batch for symbols with no 1-minute data.
    missing = [s for s in all_symbols if s not in prices]
    if missing:
        fallback = fetcher.fetch_prices(missing)
        prices.update(fallback)
        sessions.update({sym: fetcher.current_session(sym) for sym in fallback})

    # Final fallback: individual lookups.
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


_SESSION_BADGES = {"pre": " PRE", "post": " AH", "closed": " CLS"}


def _format_show_table(
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
    rates = forex_rates.get(portfolio.base_currency, {})

    for pos in portfolio.positions:
        last = prices.get(pos.symbol)
        exch = exchange_label(pos.symbol, exchange_codes.get(pos.symbol))
        if last is not None:
            badge = _SESSION_BADGES.get(sessions.get(pos.symbol, "regular"), "")
            pnl = pos.unrealized_pnl(last)
            sign = "+" if pnl >= 0 else ""
            rows.append(
                (
                    pos.symbol,
                    exch,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    f"{last:.2f}{badge}",
                    f"{pos.market_value(last):,.2f}",
                    f"{sign}{pnl:,.2f}",
                )
            )
        else:
            rows.append(
                (
                    pos.symbol,
                    exch,
                    str(pos.quantity),
                    f"{pos.avg_cost:.2f}",
                    "N/A",
                    "N/A",
                    "N/A",
                )
            )

    for cash_pos in portfolio.cash:
        rate = rates.get(cash_pos.currency)
        if rate is not None:
            price_str = (
                f"{rate:.4f}"
                if cash_pos.currency != portfolio.base_currency
                else "1.0000"
            )
            mkt_value = cash_pos.amount * rate
            rows.append(
                (
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    price_str,
                    f"{mkt_value:,.2f}",
                    "--",
                )
            )
        else:
            rows.append(
                (
                    cash_pos.currency,
                    "Cash",
                    f"{cash_pos.amount:,.2f}",
                    "1.00",
                    "N/A",
                    "N/A",
                    "--",
                )
            )

    # Calculate column widths.
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            # Right-align numeric columns (Qty onward).
            if i >= 2:
                parts.append(cell.rjust(widths[i]))
            else:
                parts.append(cell.ljust(widths[i]))
        return "  ".join(parts)

    lines: list[str] = []
    lines.append(fmt_row(headers))
    separator = "-" * len(lines[0])
    lines.append(separator)
    for row in rows:
        lines.append(fmt_row(row))
    lines.append(separator)

    # Total line.
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
    # Right-align the total value under the Mkt Value column.
    pre_width = sum(widths[:5]) + 5 * 2  # 5 columns + 5 two-char gaps
    total_line = total_label + " " * (pre_width + widths[5] - len(total_label) - len(total_str)) + total_str
    lines.append(total_line)

    return "\n".join(lines)


@main.command()
@click.pass_context
def show(ctx: click.Context) -> None:
    """Print portfolio holdings with live prices to stdout (one-shot)."""
    stores: list[PortfolioStore] = ctx.obj["stores"]
    portfolios = [store.load() for store in stores]

    if all(not p.positions and not p.cash for p in portfolios):
        click.echo("Portfolio is empty.")
        return

    prices, sessions, exchange_codes, forex_rates = _fetch_portfolio_data(
        portfolios,
    )

    for i, portfolio in enumerate(portfolios):
        if len(portfolios) > 1:
            label = portfolio.name or f"Portfolio {i + 1}"
            click.echo(f"\n{label} ({portfolio.base_currency})")
            click.echo("=" * len(f"{label} ({portfolio.base_currency})"))
        table = _format_show_table(
            portfolio, prices, sessions, exchange_codes, forex_rates
        )
        click.echo(table)
        if i < len(portfolios) - 1:
            click.echo()


@main.command("list")
def list_portfolios() -> None:
    """List all portfolios in ~/.config/stonks/."""
    files = sorted(PORTFOLIO_CONFIG_DIR.glob("*.yaml"))
    if not files:
        click.echo("No portfolios found.")
        return
    for f in files:
        click.echo(f.stem)


if __name__ == "__main__":
    main()
