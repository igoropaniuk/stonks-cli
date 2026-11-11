"""Tests for the TOTAL row helpers in stonks_cli.portfolio_table."""

import pytest

from stonks_cli.models import PortfolioTotals
from stonks_cli.portfolio_table import (
    TABLE_COLUMNS,
    total_chg_pct,
    total_row_cells,
)


def _totals(**kwargs) -> PortfolioTotals:
    args = {"cost": 900.0, "daily_chg": 10.0, "mkt_value": 1010.0, "pnl": 110.0}
    return PortfolioTotals(**{**args, **kwargs})


class TestTotalChgPct:
    def test_relative_to_previous_value(self):
        # Yesterday's value: 1010 - 10 = 1000 -> +1%
        assert total_chg_pct(_totals()) == pytest.approx(1.0)

    def test_negative_change(self):
        t = _totals(daily_chg=-50.0, mkt_value=950.0)
        assert total_chg_pct(t) == pytest.approx(-50.0 / 1000.0 * 100)

    def test_none_without_daily_change(self):
        assert total_chg_pct(_totals(daily_chg=None)) is None

    def test_none_without_market_value(self):
        assert total_chg_pct(_totals(mkt_value=None)) is None

    def test_none_when_previous_value_is_zero(self):
        assert total_chg_pct(_totals(daily_chg=10.0, mkt_value=10.0)) is None


class TestTotalRowCells:
    def _cells(self, totals: PortfolioTotals) -> dict[str, str]:
        cells = total_row_cells(totals, "EUR")
        return {col: str(cell) for col, cell in zip(TABLE_COLUMNS, cells)}

    def test_values(self):
        cells = self._cells(_totals())
        assert cells == {
            "Instrument": "TOTAL",
            "Exchange": "EUR",
            "Qty": "--",
            "Avg Cost": "900.00",
            "Last Price": "--",
            "Daily Chg": "+10.00",
            "Daily Chg %": "+1.00%",
            "Mkt Value": "1,010.00",
            "Unrealized P&L": "+110.00",
        }

    def test_unknown_totals_show_dash(self):
        cells = self._cells(
            PortfolioTotals(cost=None, daily_chg=None, mkt_value=None, pnl=None)
        )
        for col in TABLE_COLUMNS[2:]:
            assert cells[col] == "--", col
