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
