import click


@click.group()
def main():
    """CLI tool for tracking investment portfolio."""
    pass


@main.command()
@click.argument("symbol")
@click.argument("quantity", type=int)
@click.argument("price", type=float)
def add(symbol, quantity, price):
    """Add equities to portfolio."""
    click.echo(f"Adding {quantity} of {symbol} at {price}")


@main.command()
@click.argument("symbol")
@click.argument("quantity", type=int)
def remove(symbol, quantity):
    """Remove equities from portfolio."""
    click.echo(f"Removing {quantity} of {symbol}")


@main.command()
def show():
    """Show portfolio."""
    click.echo("Showing portfolio")


if __name__ == "__main__":
    main()
