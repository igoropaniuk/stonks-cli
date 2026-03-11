"""Shared test fixtures."""

import pytest

from stonks_cli.app import PortfolioApp


@pytest.fixture(autouse=True)
def no_price_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent live network calls during TUI tests by stubbing the refresh worker."""
    monkeypatch.setattr(PortfolioApp, "_refresh_prices", lambda self: None)
