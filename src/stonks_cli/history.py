"""Historical value of a portfolio's current holdings.

The portfolio file records what is held now, not when it was bought, so
the history answers "what would today's holdings have been worth on each
past day?": current quantities times each day's closing price, converted
to the base currency at that day's forex rate, plus cash balances.
"""

import logging
from dataclasses import dataclass, field

import pandas as pd  # type: ignore[import-untyped]

from stonks_cli.backtest import _max_drawdown
from stonks_cli.exchanges import quote_price_divisor
from stonks_cli.fetcher import PriceFetcher, _yf_download_close
from stonks_cli.models import Portfolio

logger = logging.getLogger(__name__)

# (label, yfinance period) in order from shortest to longest.
HISTORY_RANGES: tuple[tuple[str, str], ...] = (
    ("1M", "1mo"),
    ("3M", "3mo"),
    ("6M", "6mo"),
    ("1Y", "1y"),
    ("2Y", "2y"),
    ("5Y", "5y"),
)
DEFAULT_HISTORY_RANGE = 3  # "1Y"


@dataclass
class PortfolioHistory:
    """Daily value of the current holdings in base currency."""

    dates: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    cost: float = 0.0  # cost basis of the charted holdings plus cash
    skipped: list[str] = field(default_factory=list)

    @property
    def start_value(self) -> float:
        return self.values[0]

    @property
    def end_value(self) -> float:
        return self.values[-1]

    @property
    def change(self) -> float:
        return self.end_value - self.start_value

    @property
    def change_pct(self) -> float | None:
        if self.start_value == 0:
            return None
        return self.change / self.start_value * 100

    @property
    def high(self) -> float:
        return max(self.values)

    @property
    def low(self) -> float:
        return min(self.values)

    @property
    def max_drawdown(self) -> float:
        return _max_drawdown(self.values)


def _has_data(close: pd.DataFrame, symbol: str) -> bool:
    return symbol in close.columns and bool(close[symbol].notna().any())


def _fx_series(
    close: pd.DataFrame, currencies: list[str], base: str
) -> dict[str, pd.Series]:
    """Return a per-day rate series (currency -> base) for each currency."""
    fx: dict[str, pd.Series] = {}
    for cur in currencies:
        direct, inverse = f"{cur}{base}=X", f"{base}{cur}=X"
        if _has_data(close, direct):
            fx[cur] = close[direct]
        elif _has_data(close, inverse):
            fx[cur] = 1.0 / close[inverse]
    return fx


def fetch_portfolio_history(
    portfolio: Portfolio,
    period: str,
    fetcher: PriceFetcher | None = None,
) -> PortfolioHistory:
    """Return the daily value of *portfolio*'s holdings over *period*.

    Bonds have no price history on Yahoo, so they are left out and listed in
    :attr:`PortfolioHistory.skipped`, as are positions and cash whose price
    or forex history is unavailable.

    Raises:
        ValueError: when no position has price history to chart.
    """
    base = portfolio.base_currency
    positions = [p for p in portfolio.positions if not p.is_bond]
    skipped = sorted(p.symbol for p in portfolio.positions if p.is_bond)
    if not positions:
        raise ValueError("Portfolio has no positions with price history")

    symbols = list(dict.fromkeys(p.symbol for p in positions))
    currencies = sorted(
        {p.currency for p in positions} | {c.currency for c in portfolio.cash}
    )
    currencies = [c for c in currencies if c != base]
    fx_symbols = [f"{c}{base}=X" for c in currencies]
    fx_symbols += [f"{base}{c}=X" for c in currencies]

    close = _yf_download_close(
        symbols + fx_symbols,
        period=period,
        description="history",
        auto_adjust=False,
    )
    if close is None:
        raise ValueError("No historical data available")
    close = close.sort_index().ffill()

    # LSE pence, JSE cents, TASE agorot -> major unit, as the dashboard does.
    quote_info = (fetcher or PriceFetcher()).fetch_quote_info(symbols)
    for sym, info in quote_info.items():
        divisor = quote_price_divisor(info.currency)
        if divisor != 1.0 and sym in close.columns:
            close[sym] = close[sym] / divisor

    fx = _fx_series(close, currencies, base)
    total = pd.Series(0.0, index=close.index)
    included = 0
    for pos in positions:
        if not _has_data(close, pos.symbol) or (
            pos.currency != base and pos.currency not in fx
        ):
            skipped.append(pos.symbol)
            continue
        value = close[pos.symbol] * pos.quantity
        if pos.currency != base:
            value = value * fx[pos.currency]
        total = total + value
        included += 1
    if not included:
        raise ValueError("No price history for: " + ", ".join(skipped))

    rate_now = {cur: float(series.dropna().iloc[-1]) for cur, series in fx.items()}
    rate_now[base] = 1.0
    cost = sum(
        p.cost_basis() * rate_now[p.currency]
        for p in positions
        if p.symbol not in skipped
    )
    for cash in portfolio.cash:
        if cash.currency == base:
            total = total + cash.amount
        elif cash.currency in fx:
            total = total + cash.amount * fx[cash.currency]
        else:
            skipped.append(f"{cash.currency} cash")
            continue
        cost += cash.amount * rate_now[cash.currency]

    # Rows before every series has started are NaN; drop them.
    total = total.dropna()
    if total.empty:
        raise ValueError("No overlapping history for the portfolio's holdings")
    logger.info("History for %s: %d points", portfolio.name or "portfolio", len(total))
    return PortfolioHistory(
        dates=[d.strftime("%Y-%m-%d") for d in total.index],
        values=[float(v) for v in total],
        cost=cost,
        skipped=skipped,
    )
