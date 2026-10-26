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
    PortfolioConflictError,
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
    store: PortfolioStore, fn: Callable[[Portfolio], object]
) -> Portfolio:
    """Load *store*, apply *fn*, persist, and return the mutated portfolio.

    Raises:
        click.ClickException: If loading or applying *fn* raises ``ValueError``.
    """
    try:
        portfolio = store.load()
        fn(portfolio)
        store.save(portfolio)
    except (ValueError, PortfolioConflictError) as exc:
        raise click.ClickException(str(exc)) from exc
    return portfolio


def _mutable_store(ctx: click.Context) -> PortfolioStore:
    """Return the single store a mutating command should write to.

    Mutating commands (``add``, ``remove``, ``watchlist``, ``set``, ...)
    operate on exactly one portfolio file.  Passing several ``-p`` flags
    would be ambiguous, so it is rejected instead of silently picking the
    first one.
    """
    stores: list[PortfolioStore] = ctx.obj["stores"]
    if len(stores) > 1:
        names = ", ".join(str(s.path) for s in stores)
        raise click.ClickException(
            f"'{ctx.command.name}' modifies a single portfolio but "
            f"{len(stores)} were given (-p {names}). Pass exactly one -p flag."
        )
    return stores[0]


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
    if ctx.invoked_subcommand is None:
        ctx.invoke(dashboard)


@main.command()
@click.argument("symbol")
@click.argument("quantity", type=int)
@click.argument("price", type=float)
@click.pass_context
def add(ctx: click.Context, symbol: str, quantity: int, price: float) -> None:
    """Add QUANTITY shares of SYMBOL at PRICE to the portfolio."""
    store = _mutable_store(ctx)
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
    store = _mutable_store(ctx)
    _load_mutate_save(store, lambda p: p.remove_position(symbol, quantity))
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
    store = _mutable_store(ctx)
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
    store = _mutable_store(ctx)
    _load_mutate_save(store, lambda p: p.remove_cash(currency, amount))
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
    """Check portfolio file(s) and connectivity to market data APIs."""
    from stonks_cli.doctor import run_doctor

    stores: list[PortfolioStore] = ctx.obj["stores"]
    failures = run_doctor([s.path for s in stores])
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
# watchlist group
# ---------------------------------------------------------------------------


@main.group("watchlist")
def watchlist_group() -> None:
    """Manage symbols tracked for price only (subcommands: add, remove, list)."""


@watchlist_group.command("add")
@click.argument("symbol")
@click.option(
    "--asset-type",
    "asset_type",
    default=None,
    help="Asset class (e.g. 'crypto' to route pricing via CoinGecko).",
)
@click.option(
    "--external-id",
    "external_id",
    default=None,
    help="Provider-specific ID for unambiguous lookup (e.g. CoinGecko coin ID).",
)
@click.pass_context
def watchlist_add(
    ctx: click.Context,
    symbol: str,
    asset_type: str | None,
    external_id: str | None,
) -> None:
    """Add SYMBOL to the watchlist."""
    store = _mutable_store(ctx)
    _load_mutate_save(store, lambda p: p.add_watch(symbol, asset_type, external_id))
    click.echo(f"Added {symbol.upper()} to the watchlist")


@watchlist_group.command("remove")
@click.argument("symbol")
@click.pass_context
def watchlist_remove(ctx: click.Context, symbol: str) -> None:
    """Remove SYMBOL from the watchlist."""
    store = _mutable_store(ctx)
    _load_mutate_save(store, lambda p: p.remove_watch(symbol))
    click.echo(f"Removed {symbol.upper()} from the watchlist")


@watchlist_group.command("list")
@click.pass_context
def watchlist_list(ctx: click.Context) -> None:
    """Print the watchlist symbols to stdout."""
    stores: list[PortfolioStore] = ctx.obj["stores"]
    try:
        portfolios = _load_portfolios(stores)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    for i, portfolio in enumerate(portfolios):
        if len(portfolios) > 1:
            label = portfolio.name or f"Portfolio {i + 1}"
            if i:
                click.echo()
            click.echo(label)
            click.echo("=" * len(label))
        if not portfolio.watchlist:
            click.echo("Watchlist is empty.")
            continue
        for item in portfolio.watchlist:
            extras = [x for x in (item.asset_type, item.external_id) if x]
            suffix = f"  ({', '.join(extras)})" if extras else ""
            click.echo(f"{item.symbol}{suffix}")


# ---------------------------------------------------------------------------
# set group
# ---------------------------------------------------------------------------


@main.group("set")
def set_group() -> None:
    """Change portfolio settings (subcommands: name, base-currency)."""


@set_group.command("name")
@click.argument("name", required=False)
@click.option("--clear", is_flag=True, help="Remove the portfolio name.")
@click.pass_context
def set_name(ctx: click.Context, name: str | None, clear: bool) -> None:
    """Set the display NAME shown for the portfolio in the dashboard."""
    if clear == bool(name):
        raise click.UsageError("Provide either NAME or --clear, not both.")
    store = _mutable_store(ctx)
    _load_mutate_save(store, lambda p: p.set_name(None if clear else name))
    if clear:
        click.echo("Cleared portfolio name")
    else:
        click.echo(f"Set portfolio name to '{name}'")


@set_group.command("base-currency")
@click.argument("currency")
@click.pass_context
def set_base_currency(ctx: click.Context, currency: str) -> None:
    """Set the base CURRENCY used for the portfolio total (e.g. EUR)."""
    store = _mutable_store(ctx)
    portfolio = _load_mutate_save(store, lambda p: p.set_base_currency(currency))
    click.echo(f"Set base currency to {portfolio.base_currency}")


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

    store = _mutable_store(ctx)

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
    # The same symbol can appear more than once (several accounts, several
    # lots).  Merge those rows into one position with a weighted average
    # cost; Portfolio rejects duplicate symbols, so appending them as-is
    # would write a file that can no longer be loaded.
    skipped: list[str] = []
    merged: list[str] = []
    for pos in ibkr_positions:
        existing = portfolio.get_position(pos.symbol)
        try:
            if existing is None:
                portfolio.positions.append(
                    Position(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        avg_cost=pos.avg_price,
                        currency=pos.currency,
                    )
                )
            elif existing.currency != pos.currency.upper():
                raise ValueError(
                    f"currency {pos.currency} differs from the "
                    f"{existing.currency} row already imported"
                )
            else:
                portfolio.add_position(pos.symbol, pos.quantity, pos.avg_price)
                merged.append(pos.symbol.upper())
        except ValueError as exc:
            skipped.append(f"{pos.symbol}: {exc}")

    try:
        store.save(portfolio)
    except PortfolioConflictError as exc:
        raise click.ClickException(str(exc)) from exc

    imported = len(portfolio.positions)
    click.echo(f"+ Imported {imported} position(s) from Interactive Brokers export")
    if merged:
        names = ", ".join(sorted(set(merged)))
        click.echo(f"  Merged duplicate rows for: {names}")
    if skipped:
        click.echo(f"  Skipped {len(skipped)} row(s):")
        for msg in skipped:
            click.echo(f"    - {msg}")


if __name__ == "__main__":
    main()
