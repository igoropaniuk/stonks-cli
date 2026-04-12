"""CLI entry point for the stonks portfolio tracker."""

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

import click

from stonks_cli import __version__
from stonks_cli.app import DEFAULT_REFRESH_INTERVAL, PortfolioApp
from stonks_cli.log import setup_logging
from stonks_cli.market import build_market_snapshot
from stonks_cli.models import Portfolio
from stonks_cli.show import format_show_table
from stonks_cli.storage import (
    PORTFOLIO_CONFIG_DIR,
    PortfolioStore,
    seed_demo_portfolio,
)

_DEMO_PORTFOLIO_PATH = Path(tempfile.gettempdir()) / "stonks-demo.yaml"


def _load_portfolios(stores: list[PortfolioStore]) -> list[Portfolio]:
    """Load all portfolios from *stores*."""
    return [store.load() for store in stores]


def _is_empty(portfolios: list[Portfolio]) -> bool:
    """Return True when every portfolio has no positions, cash, or watchlist items."""
    return not any(p.positions or p.cash or p.watchlist for p in portfolios)


def _load_mutate_save(
    store: PortfolioStore, fn: Callable[[Portfolio], None]
) -> Portfolio:
    """Load *store*, apply *fn*, persist, and return the mutated portfolio."""
    portfolio = store.load()
    fn(portfolio)
    store.save(portfolio)
    return portfolio


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
@click.option(
    "--log-level",
    default="WARNING",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Minimum log level written to file and stderr.",
)
@click.pass_context
def main(ctx: click.Context, portfolio: tuple[str, ...], log_level: str) -> None:
    """CLI tool for tracking an investment portfolio."""
    setup_logging(level=getattr(logging, log_level.upper()))
    ctx.ensure_object(dict)
    if not portfolio:
        stores = [PortfolioStore()]
    else:
        stores = [
            PortfolioStore(path=PortfolioStore.resolve_path(p)) for p in portfolio
        ]
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
    portfolio = _load_mutate_save(
        store, lambda p: p.add_position(symbol, quantity, price)
    )
    pos = portfolio.get_position(symbol)
    if pos is None:
        raise click.ClickException(
            f"Position '{symbol.upper()}' not found after adding"
        )
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
    try:
        _load_mutate_save(store, lambda p: p.remove_position(symbol, quantity))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed {quantity} * {symbol.upper()}")


@main.command()
@click.option(
    "--refresh",
    default=DEFAULT_REFRESH_INTERVAL,
    show_default=True,
    type=float,
    help="Price refresh interval in seconds.",
)
@click.pass_context
def dashboard(ctx: click.Context, refresh: float) -> None:
    """Display the current portfolio with live prices and P&L."""
    stores: list[PortfolioStore] = ctx.obj["stores"]
    portfolios = _load_portfolios(stores)
    PortfolioApp(
        portfolios=portfolios,
        prices={},
        forex_rates={},
        refresh_interval=refresh,
        stores=stores,
    ).run()


@main.command()
@click.option(
    "--refresh",
    default=DEFAULT_REFRESH_INTERVAL,
    show_default=True,
    type=float,
    help="Price refresh interval in seconds.",
)
def demo(refresh: float) -> None:
    """Launch the TUI with a sample demo portfolio stored in the temp dir."""
    seed_demo_portfolio(_DEMO_PORTFOLIO_PATH)
    click.echo(f"Demo portfolio created at {_DEMO_PORTFOLIO_PATH}")
    store = PortfolioStore(path=_DEMO_PORTFOLIO_PATH)
    portfolio = store.load()
    PortfolioApp(
        portfolios=[portfolio],
        prices={},
        forex_rates={},
        refresh_interval=refresh,
        stores=[store],
    ).run()


@main.command("add-cash")
@click.argument("currency")
@click.argument("amount", type=float)
@click.pass_context
def add_cash(ctx: click.Context, currency: str, amount: float) -> None:
    """Add AMOUNT of CURRENCY cash to the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = _load_mutate_save(store, lambda p: p.add_cash(currency, amount))
    cash = portfolio.get_cash(currency)
    if cash is None:
        raise click.ClickException(
            f"Cash position '{currency.upper()}' not found after adding"
        )
    click.echo(f"Added {amount:.2f} {currency.upper()}  (total: {cash.amount:.2f})")


@main.command("remove-cash")
@click.argument("currency")
@click.argument("amount", type=float)
@click.pass_context
def remove_cash(ctx: click.Context, currency: str, amount: float) -> None:
    """Remove AMOUNT of CURRENCY cash from the portfolio."""
    store: PortfolioStore = ctx.obj["store"]
    try:
        _load_mutate_save(store, lambda p: p.remove_cash(currency, amount))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed {amount:.2f} {currency.upper()}")


@main.command()
@click.pass_context
def show(ctx: click.Context) -> None:
    """Print a snapshot of portfolio positions with current prices to stdout."""
    stores: list[PortfolioStore] = ctx.obj["stores"]
    portfolios = _load_portfolios(stores)

    if _is_empty(portfolios):
        click.echo("Portfolio is empty.")
        return

    snap = build_market_snapshot(portfolios)

    for i, portfolio in enumerate(portfolios):
        if len(portfolios) > 1:
            label = portfolio.name or f"Portfolio {i + 1}"
            click.echo(f"\n{label} ({portfolio.base_currency})")
            click.echo("=" * len(f"{label} ({portfolio.base_currency})"))
        table = format_show_table(portfolio, snap)
        click.echo(table)
        if i < len(portfolios) - 1:
            click.echo()


@main.command()
@click.argument("symbol")
def detail(symbol: str) -> None:
    """Print detailed financial information for SYMBOL to stdout."""
    from stonks_cli.show_detail import format_detail
    from stonks_cli.stock_detail import StockDetailFetcher

    try:
        d = StockDetailFetcher().fetch_stock_detail(symbol)
    except Exception as exc:
        raise click.ClickException(
            f"Failed to fetch detail for {symbol.upper()}: {exc}"
        )
    click.echo(format_detail(d))


@main.command()
@click.argument("symbol")
@click.option(
    "--count", default=10, show_default=True, help="Number of articles to fetch."
)
def feed(symbol: str, count: int) -> None:
    """Print the latest news articles for SYMBOL to stdout."""
    from stonks_cli.news_fetcher import NewsFetcher
    from stonks_cli.show_news import format_news

    try:
        items = NewsFetcher().fetch(symbol, limit=count)
    except Exception as exc:
        raise click.ClickException(f"Failed to fetch news for {symbol.upper()}: {exc}")
    click.echo(format_news(symbol, items))


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check the portfolio file and connectivity to market data APIs."""
    from stonks_cli.doctor import run_doctor

    store: PortfolioStore = ctx.obj["store"]
    failures = run_doctor(store.path)
    ctx.exit(min(failures, 1))


@main.command("list")
def list_portfolios() -> None:
    """List all portfolios in ~/.config/stonks/."""
    files = sorted(PORTFOLIO_CONFIG_DIR.glob("*.yaml"))
    if not files:
        click.echo("No portfolios found.")
        return
    for f in files:
        click.echo(f.stem)


# ---------------------------------------------------------------------------
# import group
# ---------------------------------------------------------------------------


@main.group("import")
def import_group() -> None:
    """Import portfolio data from external sources."""


@import_group.command("ibkr")
@click.argument(
    "csv_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.pass_context
def import_ibkr(ctx: click.Context, csv_file: Path) -> None:
    """Import positions from an Interactive Brokers Flex Query CSV export.

    CSV_FILE is the path to the IBKR Flex Query CSV file.

    Use the global -p option to choose a target portfolio:

    \b
        stonks -p work import ibkr positions.csv

    If the target portfolio already contains positions you will be asked to
    confirm before they are replaced.
    """
    from stonks_cli.ibkr_importer import IBKRImportError, IBKRPosition, parse_ibkr_csv
    from stonks_cli.models import Position

    store: PortfolioStore = ctx.obj["store"]

    # Parse CSV ---------------------------------------------------------------
    try:
        ibkr_positions: list[IBKRPosition] = parse_ibkr_csv(csv_file)
    except IBKRImportError as exc:
        raise click.ClickException(str(exc)) from exc

    if not ibkr_positions:
        raise click.ClickException(
            "No valid equity positions found in the CSV.\n"
            "Check that the file contains rows with a positive Position quantity\n"
            "and an AssetClass of STK (or no AssetClass column at all)."
        )

    # Confirm replacement if portfolio already has positions ------------------
    portfolio = store.load()
    if portfolio.positions:
        n = len(portfolio.positions)
        click.echo(f"Portfolio '{store.path}' already contains {n} position(s).")
        click.confirm(
            "Replace all existing positions with the imported data?",
            abort=True,
        )

    portfolio.positions.clear()

    # Import positions --------------------------------------------------------
    skipped: list[str] = []
    imported = 0
    for pos in ibkr_positions:
        try:
            portfolio.positions.append(
                Position(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    avg_cost=pos.avg_price,
                    currency=pos.currency,
                )
            )
            imported += 1
        except ValueError as exc:
            skipped.append(f"{pos.symbol}: {exc}")

    store.save(portfolio)

    click.echo(f"+ Imported {imported} position(s) from Interactive Brokers export")
    if skipped:
        click.echo(f"  Skipped {len(skipped)} row(s):")
        for msg in skipped:
            click.echo(f"    - {msg}")


if __name__ == "__main__":
    main()
