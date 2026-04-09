"""Data-transfer objects shared between the UI forms and the mutation layer."""

from typing import TypedDict


class EquityResult(TypedDict):
    symbol: str
    qty: float
    avg_cost: float
    currency: str
    asset_type: str | None
    external_id: str | None


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
