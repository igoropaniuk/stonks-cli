"""Tests for stonks_cli.crypto_fetcher."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

import stonks_cli.crypto_fetcher as cf_module
from stonks_cli.crypto_fetcher import (
    CryptoFetcher,
    _coingecko_error_summary,
    _crypto_base,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_crypto_base_btc_usd():
    assert _crypto_base("BTC-USD") == "BTC"


def test_crypto_base_lowercase():
    assert _crypto_base("eth-usd") == "ETH"


def test_crypto_base_no_dash():
    assert _crypto_base("SOL") == "SOL"


def test_coingecko_error_summary_rate_limit_401():
    exc = MagicMock(spec=httpx.HTTPStatusError)
    exc.response = MagicMock()
    exc.response.status_code = 401
    assert "rate limit" in _coingecko_error_summary(exc).lower()


def test_coingecko_error_summary_rate_limit_429():
    exc = MagicMock(spec=httpx.HTTPStatusError)
    exc.response = MagicMock()
    exc.response.status_code = 429
    assert "rate limit" in _coingecko_error_summary(exc).lower()


def test_coingecko_error_summary_other_http():
    exc = MagicMock(spec=httpx.HTTPStatusError)
    exc.response = MagicMock()
    exc.response.status_code = 500
    assert "HTTP 500" in _coingecko_error_summary(exc)


def test_coingecko_error_summary_non_http():
    exc = ValueError("timeout")
    result = _coingecko_error_summary(exc)
    assert "ValueError" in result


# ---------------------------------------------------------------------------
# CryptoFetcher.__init__ with API key
# ---------------------------------------------------------------------------


def test_init_with_api_key():
    with patch.dict("os.environ", {"COINGECKO_DEMO_API_KEY": "testkey"}):
        fetcher = CryptoFetcher()
        assert fetcher._http.headers.get("x-cg-demo-api-key") == "testkey"


def test_init_without_api_key():
    with patch.dict("os.environ", {}, clear=True):
        # Remove the key if present
        import os

        os.environ.pop("COINGECKO_DEMO_API_KEY", None)
        fetcher = CryptoFetcher()
        assert "x-cg-demo-api-key" not in fetcher._http.headers


# ---------------------------------------------------------------------------
# _ensure_coin_list
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_coin_cache():
    """Reset the module-level cache before each test."""
    original_cache = cf_module._cg_symbol_to_id.copy()
    original_loaded = cf_module._cg_coin_list_loaded
    cf_module._cg_symbol_to_id.clear()
    cf_module._cg_coin_list_loaded = False
    yield
    cf_module._cg_symbol_to_id.clear()
    cf_module._cg_symbol_to_id.update(original_cache)
    cf_module._cg_coin_list_loaded = original_loaded


def test_ensure_coin_list_loads_mapping():
    fake_json = json.dumps({"BTC": "bitcoin", "ETH": "ethereum"})
    mock_resource = MagicMock()
    mock_resource.read_text.return_value = fake_json

    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value = mock_resource
        CryptoFetcher._ensure_coin_list()

    assert cf_module._cg_symbol_to_id.get("BTC") == "bitcoin"
    assert cf_module._cg_symbol_to_id.get("ETH") == "ethereum"
    assert cf_module._cg_coin_list_loaded is True


def test_ensure_coin_list_skips_if_already_loaded():
    cf_module._cg_coin_list_loaded = True
    cf_module._cg_symbol_to_id["BTC"] = "bitcoin"

    with patch("importlib.resources.files") as mock_files:
        CryptoFetcher._ensure_coin_list()
        mock_files.assert_not_called()


def test_ensure_coin_list_file_not_found():
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.read_text.side_effect = (
            FileNotFoundError()
        )
        CryptoFetcher._ensure_coin_list()  # should not raise

    assert cf_module._cg_coin_list_loaded is False


def test_ensure_coin_list_malformed_json():
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.read_text.return_value = (
            "not-json{{{"
        )
        CryptoFetcher._ensure_coin_list()  # should not raise

    assert cf_module._cg_coin_list_loaded is False


def test_ensure_coin_list_unexpected_exception():
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.read_text.side_effect = (
            RuntimeError("disk error")
        )
        CryptoFetcher._ensure_coin_list()  # should not raise

    assert cf_module._cg_coin_list_loaded is False


# ---------------------------------------------------------------------------
# _resolve_via_search
# ---------------------------------------------------------------------------


def test_resolve_via_search_success():
    fetcher = CryptoFetcher()
    fetcher._http = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {
        "coins": [
            {"symbol": "btc", "id": "bitcoin"},
            {"symbol": "eth", "id": "ethereum"},
        ]
    }
    fetcher._http.get.return_value = resp

    result = fetcher._resolve_via_search("BTC")
    assert result == "bitcoin"


def test_resolve_via_search_no_match():
    fetcher = CryptoFetcher()
    fetcher._http = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"coins": [{"symbol": "eth", "id": "ethereum"}]}
    fetcher._http.get.return_value = resp

    result = fetcher._resolve_via_search("BTC")
    assert result is None


def test_resolve_via_search_http_error():
    fetcher = CryptoFetcher()
    fetcher._http = MagicMock()
    fetcher._http.get.side_effect = httpx.RequestError("timeout")

    result = fetcher._resolve_via_search("BTC")
    assert result is None


def test_resolve_via_search_unexpected_error():
    fetcher = CryptoFetcher()
    fetcher._http = MagicMock()
    fetcher._http.get.side_effect = RuntimeError("unexpected")

    result = fetcher._resolve_via_search("BTC")
    assert result is None


# ---------------------------------------------------------------------------
# _fetch_coingecko_batch error paths
# ---------------------------------------------------------------------------


def test_fetch_coingecko_batch_batch_fails_individual_retry():
    """When batch fails, individual retries are attempted."""
    fetcher = CryptoFetcher()

    call_count = 0

    def fake_fetch(ids_str: str) -> dict:
        nonlocal call_count
        call_count += 1
        if "," in ids_str:
            # Batch fails
            raise httpx.RequestError("network error")
        # Individual succeeds
        return {ids_str: {"usd": 50000.0, "usd_24h_change": 2.0}}

    fetcher._fetch_simple_price = fake_fetch
    result = fetcher._fetch_coingecko_batch(["bitcoin", "ethereum"])

    # batch (1) + 2 individual retries = 3 calls
    assert call_count == 3
    assert "bitcoin" in result and "ethereum" in result


def test_fetch_coingecko_batch_individual_http_error():
    """Individual retry failing with HTTP error is logged and skipped."""
    fetcher = CryptoFetcher()

    def fake_fetch(ids_str: str) -> dict:
        raise httpx.RequestError("network error")

    fetcher._fetch_simple_price = fake_fetch
    result = fetcher._fetch_coingecko_batch(["bitcoin"])
    assert result == {}


def test_fetch_coingecko_batch_individual_unexpected_error():
    """Individual retry failing with unexpected error is caught."""
    fetcher = CryptoFetcher()

    batch_called = False

    def fake_fetch(ids_str: str) -> dict:
        nonlocal batch_called
        if not batch_called:
            batch_called = True
            raise httpx.RequestError("batch fail")
        raise RuntimeError("unexpected individual error")

    fetcher._fetch_simple_price = fake_fetch
    result = fetcher._fetch_coingecko_batch(["bitcoin"])
    assert result == {}


def test_fetch_coingecko_batch_unexpected_batch_error():
    """Unexpected exception during batch is caught."""
    fetcher = CryptoFetcher()
    fetcher._fetch_simple_price = MagicMock(side_effect=RuntimeError("disk error"))
    result = fetcher._fetch_coingecko_batch(["bitcoin"])
    assert result == {}


# ---------------------------------------------------------------------------
# _parse_coingecko_response
# ---------------------------------------------------------------------------


def test_parse_coingecko_response_normal():
    result = {"bitcoin": {"usd": 50000.0, "usd_24h_change": 2.0}}
    id_to_syms = {"bitcoin": ["BTC-USD"]}
    prices, prev_closes = CryptoFetcher._parse_coingecko_response(result, id_to_syms)
    assert prices["BTC-USD"] == 50000.0
    assert "BTC-USD" in prev_closes


def test_parse_coingecko_response_missing_usd_price():
    """Items with no 'usd' price are skipped."""
    result = {"bitcoin": {"usd_24h_change": 2.0}}  # no 'usd' key
    id_to_syms = {"bitcoin": ["BTC-USD"]}
    prices, prev_closes = CryptoFetcher._parse_coingecko_response(result, id_to_syms)
    assert "BTC-USD" not in prices


def test_parse_coingecko_response_none_usd_price():
    """Items with usd=None are skipped."""
    result = {"bitcoin": {"usd": None, "usd_24h_change": 2.0}}
    id_to_syms = {"bitcoin": ["BTC-USD"]}
    prices, prev_closes = CryptoFetcher._parse_coingecko_response(result, id_to_syms)
    assert "BTC-USD" not in prices


def test_parse_coingecko_response_no_change():
    """Items with no change still produce a price."""
    result = {"bitcoin": {"usd": 50000.0}}
    id_to_syms = {"bitcoin": ["BTC-USD"]}
    prices, prev_closes = CryptoFetcher._parse_coingecko_response(result, id_to_syms)
    assert prices["BTC-USD"] == 50000.0
    assert "BTC-USD" not in prev_closes


def test_parse_coingecko_response_value_error():
    """ValueError from non-numeric price is caught and logged."""
    # float("not-a-number") raises ValueError
    result = {"bitcoin": {"usd": "not-a-number", "usd_24h_change": 2.0}}
    id_to_syms = {"bitcoin": ["BTC-USD"]}
    prices, prev_closes = CryptoFetcher._parse_coingecko_response(result, id_to_syms)
    assert "BTC-USD" not in prices


# ---------------------------------------------------------------------------
# fetch_prices_and_changes integration
# ---------------------------------------------------------------------------


def test_fetch_prices_empty_symbols():
    fetcher = CryptoFetcher()
    prices, prev = fetcher.fetch_prices_and_changes([])
    assert prices == {}
    assert prev == {}
