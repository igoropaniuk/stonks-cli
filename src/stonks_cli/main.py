"""CLI entry point for the stonks portfolio tracker."""

from pathlib import Path

import click

from stonks_cli import __version__
from stonks_cli.app import PortfolioApp
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
