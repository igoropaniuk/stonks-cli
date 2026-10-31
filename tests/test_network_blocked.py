"""The test suite must never reach the network."""

import socket

import pytest
from curl_cffi import requests as curl_requests
from pytest_socket import SocketConnectBlockedError

from tests.conftest import NetworkBlockedError

# TEST-NET-1 (RFC 5737): never routed, and no DNS lookup is needed.
UNROUTABLE = ("192.0.2.1", 80)


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_python_sockets_are_blocked() -> None:
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(UNROUTABLE, timeout=1)


def test_curl_cffi_requests_are_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        curl_requests.get(f"http://{UNROUTABLE[0]}/", timeout=1)


def test_loopback_is_allowed() -> None:
    """asyncio builds its self-pipe on loopback; that must keep working."""
    a, b = socket.socketpair()
    a.close()
    b.close()
