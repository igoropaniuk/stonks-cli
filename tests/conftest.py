"""Shared test fixtures."""

import pytest

from stonks_cli.app import PortfolioApp


@pytest.fixture(autouse=True)
def stub_refresh_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent live network calls during TUI tests by stubbing the refresh workers."""
    monkeypatch.setattr(PortfolioApp, "_refresh_prices", lambda self: None)
    monkeypatch.setattr(PortfolioApp, "_refresh_news", lambda self: None)


@pytest.fixture(autouse=True)
def no_logging_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress logging setup so tests don't write to disk or add handlers."""
    monkeypatch.setattr("stonks_cli.main.setup_logging", lambda **_: None)
