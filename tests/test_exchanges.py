"""Structural validation tests for the _EXCHANGES table."""

import zoneinfo

from stonks_cli.exchanges import _EXCHANGES, exchange_label


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
