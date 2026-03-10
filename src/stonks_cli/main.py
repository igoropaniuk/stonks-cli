"""CLI entry point for the stonks portfolio tracker."""

from pathlib import Path

import click

from stonks_cli.app import PortfolioApp
from stonks_cli.storage import PortfolioStore


@click.group()
@click.option(
    "-p",
    "--portfolio",
    type=click.Path(),
    default=None,
    help="Portfolio YAML file (default: ~/.config/stonks/portfolio.yaml).",
)
@click.pass_context
def main(ctx: click.Context, portfolio: str | None) -> None:
    """CLI tool for tracking an investment portfolio."""
    ctx.ensure_object(dict)
    path = Path(portfolio) if portfolio else None
    ctx.obj["store"] = PortfolioStore(path=path)


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
        f"Added {quantity} × {symbol.upper()} @ {price:.2f}  "
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
    click.echo(f"Removed {quantity} × {symbol.upper()}")


@main.command()
@click.option(
    "--refresh",
    default=5.0,
    show_default=True,
    type=float,
    help="Price refresh interval in seconds.",
)
@click.pass_context
def show(ctx: click.Context, refresh: float) -> None:
    """Display the current portfolio with live prices and P&L."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()

    if not portfolio.positions:
        click.echo("Portfolio is empty.")
        return

    PortfolioApp(
        portfolio=portfolio,
        prices={},
        forex_rates={},
        refresh_interval=refresh,
    ).run()


if __name__ == "__main__":
    main()
