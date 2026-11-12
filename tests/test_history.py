"""Tests for stonks_cli.history (portfolio value over time)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stonks_cli.bonds import BondInfo
from stonks_cli.fetcher import QuoteInfo
from stonks_cli.history import PortfolioHistory, fetch_portfolio_history
from stonks_cli.models import CashPosition, Portfolio, Position

_DATES = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])


def _fetcher(quote_info: dict[str, QuoteInfo] | None = None) -> MagicMock:
    fetcher = MagicMock()
    fetcher.fetch_quote_info.return_value = quote_info or {}
    return fetcher


def _run(portfolio: Portfolio, close: pd.DataFrame | None, **kw) -> PortfolioHistory:
    with patch("stonks_cli.history._yf_download_close", return_value=close) as dl:
        result = fetch_portfolio_history(portfolio, "1y", fetcher=_fetcher(**kw))
    dl.assert_called_once()
    return result


def test_values_are_quantity_times_close_plus_cash() -> None:
    p = Portfolio(
        positions=[Position("AAPL", 10, 100.0), Position("NVDA", 2, 50.0)],
        cash=[CashPosition("USD", 500.0)],
    )
    close = pd.DataFrame(
        {"AAPL": [100.0, 110.0, 120.0], "NVDA": [50.0, 40.0, 60.0]}, index=_DATES
    )
    h = _run(p, close)
    assert h.dates == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert h.values == pytest.approx([1600.0, 1680.0, 1820.0])
    assert h.cost == pytest.approx(1000.0 + 100.0 + 500.0)
    assert h.skipped == []


def test_converts_with_daily_forex_rate() -> None:
    p = Portfolio(
        positions=[Position("ASML.AS", 1, 700.0, currency="EUR")],
        cash=[CashPosition("EUR", 100.0)],
    )
    close = pd.DataFrame(
        {"ASML.AS": [800.0, 800.0, 900.0], "EURUSD=X": [1.0, 1.1, 1.2]},
        index=_DATES,
    )
    h = _run(p, close)
    assert h.values == pytest.approx([900.0, 990.0, 1200.0])
    # Cost converted at the latest rate.
    assert h.cost == pytest.approx((700.0 + 100.0) * 1.2)


def test_falls_back_to_inverse_forex_pair() -> None:
    p = Portfolio(positions=[Position("X.KS", 1, 1.0, currency="KRW")])
    close = pd.DataFrame(
        {"X.KS": [1000.0, 1000.0, 1000.0], "USDKRW=X": [1000.0, 500.0, 1000.0]},
        index=_DATES,
    )
    h = _run(p, close)
    assert h.values == pytest.approx([1.0, 2.0, 1.0])


def test_minor_unit_quotes_are_scaled() -> None:
    p = Portfolio(positions=[Position("VOD.L", 100, 0.7, currency="GBP")])
    close = pd.DataFrame(
        {"VOD.L": [70.0, 72.0, 74.0], "GBPUSD=X": [1.0, 1.0, 1.0]}, index=_DATES
    )
    h = _run(p, close, quote_info={"VOD.L": QuoteInfo("LSE", "GBp")})
    assert h.values == pytest.approx([70.0, 72.0, 74.0])


def test_bonds_and_symbols_without_data_are_skipped() -> None:
    bond = BondInfo(maturity=date(2031, 9, 30), coupon=5.0)
    p = Portfolio(
        positions=[
            Position("AAPL", 1, 100.0),
            Position("GONE", 1, 10.0),
            Position("UST", 1, 100.0, bond=bond),
        ],
        cash=[CashPosition("CHF", 10.0)],
    )
    close = pd.DataFrame(
        {"AAPL": [100.0, 101.0, 102.0], "GONE": [float("nan")] * 3}, index=_DATES
    )
    h = _run(p, close)
    assert h.values == pytest.approx([100.0, 101.0, 102.0])
    assert h.skipped == ["UST", "GONE", "CHF cash"]
    assert h.cost == pytest.approx(100.0)


def test_leading_rows_before_all_series_start_are_dropped() -> None:
    p = Portfolio(positions=[Position("A", 1, 1.0), Position("B", 1, 1.0)])
    close = pd.DataFrame(
        {"A": [1.0, 2.0, 3.0], "B": [float("nan"), 10.0, 10.0]}, index=_DATES
    )
    h = _run(p, close)
    assert h.dates == ["2026-01-06", "2026-01-07"]
    assert h.values == pytest.approx([12.0, 13.0])


def test_gaps_are_forward_filled() -> None:
    p = Portfolio(positions=[Position("A", 1, 1.0), Position("BTC-USD", 1, 1.0)])
    close = pd.DataFrame(
        {"A": [1.0, float("nan"), 3.0], "BTC-USD": [10.0, 20.0, 30.0]}, index=_DATES
    )
    h = _run(p, close)
    assert h.values == pytest.approx([11.0, 21.0, 33.0])


def test_raises_without_positions() -> None:
    with pytest.raises(ValueError, match="no positions"):
        fetch_portfolio_history(
            Portfolio(cash=[CashPosition("USD", 1.0)]), "1y", fetcher=_fetcher()
        )


def test_raises_when_download_fails() -> None:
    p = Portfolio(positions=[Position("AAPL", 1, 1.0)])
    with pytest.raises(ValueError, match="No historical data"):
        _run(p, None)


def test_raises_when_no_position_has_data() -> None:
    p = Portfolio(positions=[Position("AAPL", 1, 1.0)])
    close = pd.DataFrame({"AAPL": [float("nan")] * 3}, index=_DATES)
    with pytest.raises(ValueError, match="No price history for: AAPL"):
        _run(p, close)


def test_summary_properties() -> None:
    h = PortfolioHistory(
        dates=["a", "b", "c", "d"], values=[100.0, 120.0, 90.0, 110.0], cost=95.0
    )
    assert h.start_value == 100.0
    assert h.end_value == 110.0
    assert h.change == pytest.approx(10.0)
    assert h.change_pct == pytest.approx(10.0)
    assert h.high == 120.0
    assert h.low == 90.0
    assert h.max_drawdown == pytest.approx(-25.0)


def test_change_pct_none_when_start_is_zero() -> None:
    h = PortfolioHistory(dates=["a", "b"], values=[0.0, 10.0])
    assert h.change_pct is None
