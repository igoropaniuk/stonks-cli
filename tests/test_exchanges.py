"""Structural validation tests for the _EXCHANGES table."""

import zoneinfo

import pytest

from stonks_cli.exchanges import (
    _EXCHANGES,
    exchange_info_for,
    exchange_label,
    is_crypto_symbol,
    quote_price_divisor,
)


def test_all_exchanges_have_valid_timezone() -> None:
    """Every _EXCHANGES entry must reference a valid IANA timezone."""
    for key, info in _EXCHANGES.items():
        try:
            zoneinfo.ZoneInfo(info.tz_name)
        except zoneinfo.ZoneInfoNotFoundError as exc:
            raise AssertionError(
                f"Exchange {key!r} has invalid tz_name {info.tz_name!r}"
            ) from exc


def test_all_exchanges_open_before_close() -> None:
    """Every _EXCHANGES entry must have open_time strictly before close_time."""
    for key, info in _EXCHANGES.items():
        assert info.open_time < info.close_time, (
            f"Exchange {key!r}: open_time {info.open_time}"
            f" >= close_time {info.close_time}"
        )


def test_bond_label_is_price_source() -> None:
    assert exchange_label("UST5.0SEP31", "UST", "bond") == "UST"
    assert exchange_label("UST5.0SEP31", "UST model", "bond") == "UST model"
    # A dash in a bond label must not turn it into crypto.
    assert exchange_label("US-T", "Manual", "bond") == "Manual"


def test_bond_etf_keeps_exchange_label() -> None:
    # Bond ETFs tagged asset_type: bond still map their yfinance code.
    assert exchange_label("TLT", "NMS", "bond") == "NASDAQ"
    assert exchange_label("TLT", None, "bond") == "NYSE/NASDAQ"


class TestIsCryptoSymbol:
    @pytest.mark.parametrize("symbol", ["BTC-USD", "eth-eur", "SOL-USDT", "LINK-USD"])
    def test_crypto_pairs(self, symbol):
        assert is_crypto_symbol(symbol)

    @pytest.mark.parametrize("symbol", ["BRK-B", "BF-B", "AAPL", "ASML.AS", "RDS-A"])
    def test_equities(self, symbol):
        assert not is_crypto_symbol(symbol)

    def test_asset_type_is_authoritative(self):
        assert is_crypto_symbol("bitcoin", asset_type="crypto")
        assert is_crypto_symbol("AAPL", asset_type="CRYPTO")
        assert not is_crypto_symbol("BTC-USD", asset_type="equity")

    def test_exchange_info_for_share_class(self):
        info = exchange_info_for("BRK-B")
        assert info is not None and info.tz_name == "America/New_York"
        assert exchange_info_for("BTC-USD") is None
        assert exchange_info_for("BTC-USD", asset_type="equity") is not None


@pytest.mark.parametrize(
    ("currency", "divisor"),
    [
        ("GBp", 100.0),
        ("GBX", 100.0),
        ("ZAc", 100.0),
        ("ILA", 100.0),
        ("GBP", 1.0),
        ("USD", 1.0),
        ("EUR", 1.0),
        (None, 1.0),
    ],
)
def test_quote_price_divisor(currency, divisor):
    assert quote_price_divisor(currency) == divisor
