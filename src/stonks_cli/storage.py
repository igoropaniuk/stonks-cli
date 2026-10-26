"""Portfolio persistence: read and write the portfolio YAML file."""

import hashlib
import importlib.resources
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import yaml

from stonks_cli.bonds import DEFAULT_FACE_VALUE, DEFAULT_FREQUENCY, BondInfo
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PortfolioConflictError(Exception):
    """The portfolio file changed on disk since this store last read it.

    Raised by :meth:`PortfolioStore.save` so a long-running process (the
    TUI) doesn't silently overwrite edits made elsewhere (the CLI, an
    editor) while it held an older copy in memory.
    """


# ---------------------------------------------------------------------------
# Parse helpers (YAML dict -> model instance)
# ---------------------------------------------------------------------------


def _parse_bond(b: dict) -> BondInfo:
    if not isinstance(b, dict):
        raise TypeError("'bond' must be a mapping")
    return BondInfo(
        maturity=b["maturity"],
        coupon=float(b["coupon"]),
        face_value=float(b.get("face_value", DEFAULT_FACE_VALUE)),
        frequency=int(b.get("frequency", DEFAULT_FREQUENCY)),
        cusip=str(b["cusip"]) if b.get("cusip") is not None else None,
        price=float(b["price"]) if b.get("price") is not None else None,
    )


def _parse_position(p: dict) -> Position:
    bond = p.get("bond")
    return Position(
        symbol=p["symbol"],
        quantity=p["quantity"],
        avg_cost=p["avg_cost"],
        currency=p.get("currency", "USD"),
        asset_type=p.get("asset_type"),
        external_id=p.get("external_id"),
        bond=_parse_bond(bond) if bond is not None else None,
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


def _serialize_bond(b: BondInfo) -> dict:
    return {
        "maturity": b.maturity,
        "coupon": b.coupon,
        **({"face_value": b.face_value} if b.face_value != DEFAULT_FACE_VALUE else {}),
        **({"frequency": b.frequency} if b.frequency != DEFAULT_FREQUENCY else {}),
        **({"cusip": b.cusip} if b.cusip else {}),
        **({"price": b.price} if b.price is not None else {}),
    }


def _serialize_position(p: Position) -> dict:
    return {
        "symbol": p.symbol,
        "quantity": p.quantity,
        "avg_cost": round(p.avg_cost, 6),
        "currency": p.currency,
        **({"asset_type": p.asset_type} if p.asset_type else {}),
        **({"external_id": p.external_id} if p.external_id else {}),
        **({"bond": _serialize_bond(p.bond)} if p.bond is not None else {}),
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
_UNTRACKED = object()
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


def seed_demo_portfolio(path: Path) -> None:
    """Write the bundled sample portfolio to *path* (always overwrites)."""
    sample = (
        importlib.resources.files("stonks_cli.data")
        .joinpath("sample_portfolio.yaml")
        .read_text(encoding="utf-8")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sample, encoding="utf-8")


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
        # Digest of the file content this store last read or wrote.  ``None``
        # means the file did not exist then; the sentinel ``_UNTRACKED``
        # means the store has never touched the file, so no conflict check
        # is possible.
        self._known_digest: str | None | object = _UNTRACKED

    def _digest_on_disk(self) -> str | None:
        """Return a digest of the file's current bytes, or None if absent."""
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def changed_on_disk(self) -> bool:
        """True when the file differs from what this store last read or wrote."""
        if self._known_digest is _UNTRACKED:
            return False
        return self._digest_on_disk() != self._known_digest

    def _parse_section(
        self, section: dict, key: str, parser: Callable[[dict], T], label: str
    ) -> list[T]:
        """Parse a list section from *section[key]* using *parser*.

        Args:
            section: The ``portfolio:`` mapping from the YAML file.
            key: Section key (``"positions"``, ``"cash"``, or ``"watchlist"``).
            parser: Callable that converts a raw dict to the model instance.
            label: Singular noun used in error messages (e.g. ``"position"``).

        Raises:
            ValueError: If any entry is missing a required field.
        """
        try:
            return [parser(item) for item in section.get(key) or []]
        except KeyError as exc:
            raise ValueError(
                f"Invalid {label} entry in {self.path}: missing required field {exc}"
            ) from exc
        except TypeError as exc:
            raise ValueError(f"Invalid {label} entry in {self.path}: {exc}") from exc

    def load(self) -> Portfolio:
        """Load the portfolio from disk.

        Returns an empty Portfolio if the file does not exist yet.

        Raises:
            ValueError: If the file exists but cannot be parsed.
        """
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            self._known_digest = None
            return Portfolio()
        self._known_digest = hashlib.sha256(raw).hexdigest()

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            logger.error("Failed to parse portfolio file %s: %s", self.path, exc)
            raise ValueError(f"Cannot parse portfolio file {self.path}: {exc}") from exc

        try:
            section = (data or {}).get("portfolio", {}) or {}
        except AttributeError as exc:
            raise ValueError(
                f"Invalid portfolio file {self.path}: "
                f"expected a mapping at the top level, got {type(data).__name__}"
            ) from exc

        return Portfolio(
            positions=self._parse_section(
                section, "positions", _parse_position, "position"
            ),
            cash=self._parse_section(section, "cash", _parse_cash, "cash"),
            watchlist=self._parse_section(
                section, "watchlist", _parse_watchlist_item, "watchlist"
            ),
            base_currency=section.get("base_currency", "USD"),
            name=section.get("name") or None,
        )

    def save(self, portfolio: Portfolio, *, force: bool = False) -> None:
        """Persist the portfolio to disk.

        The file is written to a temporary sibling and renamed into place,
        so a crash mid-write never leaves a truncated portfolio behind.

        Creates parent directories if they do not exist.

        Raises:
            PortfolioConflictError: If the file changed on disk since this
                store last read or wrote it and *force* is False.
        """
        if not force and self.changed_on_disk():
            raise PortfolioConflictError(
                f"{self.path} was modified outside this session; "
                "reload it before saving"
            )
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
        text = yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        # Write bytes, not text: text mode turns "\n" into "\r\n" on
        # Windows, and the digest must match what is actually on disk.
        raw = text.encode("utf-8")
        self._write_atomically(raw)
        self._known_digest = hashlib.sha256(raw).hexdigest()

    def _write_atomically(self, raw: bytes) -> None:
        """Write *raw* to a temp file next to the target, then rename it over."""
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
