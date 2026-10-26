"""Data-transfer objects shared between the UI forms and the mutation layer."""

from datetime import date
from typing import NotRequired, TypedDict


class BondFields(TypedDict):
    maturity: date
    coupon: float  # annual rate in percent
    face_value: float
    frequency: int  # coupons per year
    cusip: str | None
    price: float | None  # manual clean price, percent of par


class EquityResult(TypedDict):
    symbol: str
    qty: float
    avg_cost: float
    currency: str
    asset_type: str | None
    external_id: str | None
    # Present (and not None) only when the position is a bond.
    bond: NotRequired[BondFields | None]


class CashResult(TypedDict):
    currency: str
    amount: float


class WatchResult(TypedDict):
    symbol: str
    asset_type: str | None
    external_id: str | None


class BacktestConfig(TypedDict):
    benchmark: str  # ticker to compare against, e.g. "SPY"
    start_amount: float  # starting investment in portfolio currency
    start_year: int  # backtest start year, e.g. 2010
    end_year: int  # backtest end year, e.g. 2026
    cashflows: float  # yearly contribution, e.g. 0
    rebalance: str  # "none" | "monthly" | "annual"
    skip_unavailable: bool  # skip positions without historical data
