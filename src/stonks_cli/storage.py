"""Portfolio persistence: read and write the portfolio YAML file."""

import importlib.resources
import logging
from pathlib import Path

import yaml

from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem

logger = logging.getLogger(__name__)

PORTFOLIO_CONFIG_DIR = Path.home() / ".config" / "stonks"
DEFAULT_PORTFOLIO_PATH = PORTFOLIO_CONFIG_DIR / "portfolio.yaml"


def seed_sample_portfolio() -> bool:
    """Copy the bundled sample portfolio to ~/.config/stonks/portfolio.yaml.

    Only acts when the config directory contains no .yaml files at all.

    Returns:
        True if the sample was written, False if portfolios already exist.
    """
    if PORTFOLIO_CONFIG_DIR.exists() and any(PORTFOLIO_CONFIG_DIR.glob("*.yaml")):
        return False
    sample = (
        importlib.resources.files("stonks_cli.data")
        .joinpath("sample_portfolio.yaml")
        .read_text(encoding="utf-8")
    )
    DEFAULT_PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PORTFOLIO_PATH.write_text(sample, encoding="utf-8")
    return True


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
                logger.error("Failed to parse portfolio file %s: %s", self.path, exc)
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
                asset_type=p.get("asset_type"),
                external_id=p.get("external_id"),
            )
            for p in raw_positions
        ]

        raw_cash = section.get("cash") or []
        cash = [
            CashPosition(currency=c["currency"], amount=c["amount"]) for c in raw_cash
        ]

        raw_watchlist = section.get("watchlist") or []
        watchlist = [
            WatchlistItem(
                symbol=w["symbol"],
                asset_type=w.get("asset_type"),
                external_id=w.get("external_id"),
            )
            for w in raw_watchlist
        ]

        base_currency = section.get("base_currency", "USD")
        name = section.get("name", "")

        return Portfolio(
            positions=positions,
            cash=cash,
            watchlist=watchlist,
            base_currency=base_currency,
            name=name,
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
                        **({"asset_type": p.asset_type} if p.asset_type else {}),
                        **({"external_id": p.external_id} if p.external_id else {}),
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
                "watchlist": [
                    {
                        "symbol": w.symbol,
                        **({"asset_type": w.asset_type} if w.asset_type else {}),
                        **({"external_id": w.external_id} if w.external_id else {}),
                    }
                    for w in portfolio.watchlist
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
