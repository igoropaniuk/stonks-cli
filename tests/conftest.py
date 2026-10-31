"""Shared test fixtures."""

import pytest
from curl_cffi import Curl

from stonks_cli.app import PortfolioApp


class NetworkBlockedError(RuntimeError):
    """Raised when a test makes a real request through curl_cffi."""


@pytest.fixture(autouse=True)
def stub_refresh_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent live network calls during TUI tests by stubbing the refresh workers."""
    monkeypatch.setattr(PortfolioApp, "_refresh_prices", lambda self: None)
    monkeypatch.setattr(PortfolioApp, "_refresh_news", lambda self: None)


@pytest.fixture(autouse=True)
def no_logging_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress logging setup so tests don't write to disk or add handlers."""
    monkeypatch.setattr("stonks_cli.main.setup_logging", lambda **_: None)


@pytest.fixture(autouse=True)
def block_curl_requests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any real HTTP request made through curl_cffi.

    pytest-socket (configured in pyproject.toml) blocks Python sockets, but
    yfinance sends its requests through curl_cffi, which is libcurl and never
    touches the socket module.  A test that needs the network can opt out
    of both with ``@pytest.mark.enable_socket``.
    """
    if request.node.get_closest_marker("enable_socket"):
        return

    def blocked(self: Curl, *args: object, **kwargs: object) -> None:
        raise NetworkBlockedError(
            "Network access is blocked in tests: curl_cffi (used by yfinance) "
            "tried to make a request. Mock the yfinance call instead."
        )

    monkeypatch.setattr(Curl, "perform", blocked)
