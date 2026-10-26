"""Tests for stonks_cli.doctor symbol checks involving bonds."""

from datetime import date
from unittest.mock import MagicMock, patch

from stonks_cli.bond_fetcher import BondQuote, BondSource
from stonks_cli.bonds import BondInfo
from stonks_cli.doctor import check_symbols
from stonks_cli.models import Position

BOND = Position(
    "UST5.0SEP31",
    15,
    100.47,
    bond=BondInfo(maturity=date(2031, 9, 30), coupon=5.0, cusip="91282CRN3"),
)


def _bond_fetcher(quotes: dict[str, BondQuote]) -> MagicMock:
    fetcher = MagicMock()
    fetcher.fetch_quotes.return_value = quotes
    return fetcher


def test_priced_bond_passes_without_yfinance(capsys):
    quote = BondQuote(99.75, 100.0, BondSource.FEDINVEST, date(2026, 10, 14))
    fetcher = _bond_fetcher({"UST5.0SEP31": quote})
    with (
        patch("stonks_cli.bond_fetcher.BondPriceFetcher", return_value=fetcher),
        patch("stonks_cli.doctor.yf") as yf,
    ):
        assert check_symbols([BOND]) is True
    yf.Ticker.assert_not_called()
    out = capsys.readouterr().out
    assert "UST5.0SEP31" in out
    assert "99.7500 (UST)" in out


def test_unpriced_bond_fails_with_hint(capsys):
    with patch(
        "stonks_cli.bond_fetcher.BondPriceFetcher", return_value=_bond_fetcher({})
    ):
        assert check_symbols([BOND]) is False
    assert "add a Treasury cusip or a manual price" in capsys.readouterr().out
