"""CLI entry point for the stonks portfolio tracker."""

from pathlib import Path

import click

from stonks_cli.fetcher import PriceFetcher
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
@click.pass_context
def show(ctx: click.Context) -> None:
    """Display the current portfolio with live prices and P&L."""
    store: PortfolioStore = ctx.obj["store"]
    portfolio = store.load()

    if not portfolio.positions:
        click.echo("Portfolio is empty.")
        return

    symbols = [p.symbol for p in portfolio.positions]
    prices = PriceFetcher().fetch_prices(symbols)

    header = f"{'Instrument':<12} {'Qty':>6} {'Avg Cost':>10} {'Last Price':>12} {'Mkt Value':>12} {'Unrealized P&L':>16}"
    click.echo(header)
    click.echo("-" * len(header))

    for pos in portfolio.positions:
        last = prices.get(pos.symbol)
        if last is not None:
            mkt_value = pos.market_value(last)
            pnl = pos.unrealized_pnl(last)
            last_str = f"{last:>10.2f}"
            mkt_str = f"{mkt_value:>12,.2f}"
            pnl_str = f"{pnl:>+16,.2f}"
        else:
            last_str = f"{'N/A':>10}"
            mkt_str = f"{'N/A':>12}"
            pnl_str = f"{'N/A':>16}"

        click.echo(
            f"{pos.symbol:<12} {pos.quantity:>6} {pos.avg_cost:>10.2f} {last_str} {mkt_str} {pnl_str}"
        )


if __name__ == "__main__":
    main()
