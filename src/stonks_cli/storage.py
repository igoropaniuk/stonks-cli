"""Portfolio persistence: read and write the portfolio YAML file."""

import importlib.resources
import logging
from pathlib import Path

import yaml

from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parse helpers (YAML dict -> model instance)
# ---------------------------------------------------------------------------


def _parse_position(p: dict) -> Position:
    return Position(
        symbol=p["symbol"],
        quantity=p["quantity"],
        avg_cost=p["avg_cost"],
        currency=p.get("currency", "USD"),
        asset_type=p.get("asset_type"),
        external_id=p.get("external_id"),
    )


def _parse_cash(c: dict) -> CashPosition:
    return CashPosition(currency=c["currency"], amount=c["amount"])


def _parse_watchlist_item(w: dict) -> WatchlistItem:
    return WatchlistItem(
        symbol=w["symbol"],
        asset_type=w.get("asset_type"),
        external_id=w.get("external_id"),
    )


# ---------------------------------------------------------------------------
# Serialize helpers (model instance -> YAML dict)
# ---------------------------------------------------------------------------


def _serialize_position(p: Position) -> dict:
    return {
        "symbol": p.symbol,
        "quantity": p.quantity,
        "avg_cost": round(p.avg_cost, 6),
        "currency": p.currency,
        **({"asset_type": p.asset_type} if p.asset_type else {}),
        **({"external_id": p.external_id} if p.external_id else {}),
    }


def _serialize_cash(c: CashPosition) -> dict:
    return {"currency": c.currency, "amount": round(c.amount, 2)}


def _serialize_watchlist_item(w: WatchlistItem) -> dict:
    return {
        "symbol": w.symbol,
        **({"asset_type": w.asset_type} if w.asset_type else {}),
        **({"external_id": w.external_id} if w.external_id else {}),
    }


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

    @classmethod
    def resolve_path(cls, name_or_path: str | None) -> Path | None:
        """Resolve a ``-p`` value to a Path.

        A plain name with no path separators and no extension is treated as a
        shorthand for ``~/.config/stonks/<name>.yaml``.  Anything else is
        used as-is.  Returns ``None`` when *name_or_path* is ``None``.
        """
        if name_or_path is None:
            return None
        p = Path(name_or_path)
        if p.parent == Path(".") and p.suffix == "":
            return PORTFOLIO_CONFIG_DIR / f"{name_or_path}.yaml"
        return p

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

        try:
            section = (data or {}).get("portfolio", {}) or {}
        except AttributeError as exc:
            raise ValueError(
                f"Invalid portfolio file {self.path}: "
                f"expected a mapping at the top level, got {type(data).__name__}"
            ) from exc

        try:
            positions = [_parse_position(p) for p in section.get("positions") or []]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Invalid position entry in {self.path}: missing required field {exc}"
            ) from exc

        try:
            cash = [_parse_cash(c) for c in section.get("cash") or []]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Invalid cash entry in {self.path}: missing required field {exc}"
            ) from exc

        try:
            watchlist = [
                _parse_watchlist_item(w) for w in section.get("watchlist") or []
            ]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Invalid watchlist entry in {self.path}: missing required field {exc}"
            ) from exc

        base_currency = section.get("base_currency", "USD")
        name = section.get("name") or None

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
                **({"name": portfolio.name} if portfolio.name else {}),
                "base_currency": portfolio.base_currency,
                "positions": [_serialize_position(p) for p in portfolio.positions],
                "cash": [_serialize_cash(c) for c in portfolio.cash],
                "watchlist": [
                    _serialize_watchlist_item(w) for w in portfolio.watchlist
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
