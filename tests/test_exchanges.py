"""Structural validation tests for the _EXCHANGES table."""

import zoneinfo

from stonks_cli.exchanges import _EXCHANGES


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
