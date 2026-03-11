"""Portfolio persistence: read and write the portfolio YAML file."""

from pathlib import Path

import yaml

from stonks_cli.models import CashPosition, Portfolio, Position

PORTFOLIO_CONFIG_DIR = Path.home() / ".config" / "stonks"
DEFAULT_PORTFOLIO_PATH = PORTFOLIO_CONFIG_DIR / "portfolio.yaml"


class PortfolioStore:
    """Reads and writes the portfolio to a YAML file on disk.

    Args:
        path: Path to the portfolio file.
              Defaults to ``~/.config/stonks/portfolio.yaml``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PORTFOLIO_PATH

    def load(self) -> Portfolio:
        """Load the portfolio from disk.

        Returns an empty Portfolio if the file does not exist yet.

        Raises:
            ValueError: If the file exists but cannot be parsed.
        """
        if not self.path.exists():
            return Portfolio()

        with self.path.open() as fh:
            try:
                data = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Cannot parse portfolio file {self.path}: {exc}"
                ) from exc

        section = (data or {}).get("portfolio", {})

        raw_positions = section.get("positions") or []
        positions = [
            Position(
                symbol=p["symbol"],
                quantity=p["quantity"],
                avg_cost=p["avg_cost"],
                currency=p.get("currency", "USD"),
            )
            for p in raw_positions
        ]

        raw_cash = section.get("cash") or []
        cash = [
            CashPosition(currency=c["currency"], amount=c["amount"]) for c in raw_cash
        ]

        base_currency = section.get("base_currency", "USD")
        name = section.get("name", "")

        return Portfolio(
            positions=positions, cash=cash, base_currency=base_currency, name=name
        )

    def save(self, portfolio: Portfolio) -> None:
        """Persist the portfolio to disk.

        Creates parent directories if they do not exist.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "portfolio": {
                "name": portfolio.name,
                "base_currency": portfolio.base_currency,
                "positions": [
                    {
                        "symbol": p.symbol,
                        "quantity": p.quantity,
                        "avg_cost": round(p.avg_cost, 6),
                        "currency": p.currency,
                    }
                    for p in portfolio.positions
                ],
                "cash": [
                    {
                        "currency": c.currency,
                        "amount": round(c.amount, 2),
                    }
                    for c in portfolio.cash
                ],
            }
        }
        with self.path.open("w") as fh:
            yaml.dump(
                data,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
