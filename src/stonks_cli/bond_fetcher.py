"""Price quotes for bond positions.

Each bond is priced from the first source that can price it:

1. **Manual** -- the ``price`` set in the portfolio file.
2. **FedInvest** -- the U.S. Treasury's end-of-day price for the bond's
   CUSIP, published on TreasuryDirect after about 1 p.m. Eastern.
3. **Yield-curve model** -- the bond's remaining coupons discounted at the
   Treasury par yield for its remaining maturity, from the daily curve
   published on home.treasury.gov.

Sources 2 and 3 only apply to U.S. Treasuries, recognised by their CUSIP
prefix ``912``.  Any other bond needs a manual price.

Both public sources publish end-of-day data, so responses are cached for
:data:`CACHE_TTL_SECONDS` across fetcher instances; the TUI refreshes prices
far more often than the data changes.
"""

import csv
import io
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TypeVar
from zoneinfo import ZoneInfo

import httpx

from stonks_cli.bonds import BondInfo, price_from_yield

logger = logging.getLogger(__name__)

T = TypeVar("T")

TREASURY_CUSIP_PREFIX = "912"
CACHE_TTL_SECONDS = 3600
HTTP_TIMEOUT_SECONDS = 20.0
# How many calendar days to walk back looking for a published price file.
# Covers long weekends plus a holiday.
_MAX_LOOKBACK_DAYS = 7

_FEDINVEST_FORM_URL = (
    "https://www.treasurydirect.gov/GA-FI/FedInvest/selectSecurityPriceDate"
)
_FEDINVEST_CSV_URL = (
    "https://www.treasurydirect.gov/GA-FI/FedInvest/securityPriceDetail"
)
_CURVE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/daily-treasury-rates.csv/{year}/all"
)
_CSRF_RE = re.compile(r'name="_csrf"\s+value="([^"]+)"')
_TENOR_RE = re.compile(r"([\d.]+)\s*(Mo|Month|Yr|Year)", re.IGNORECASE)

_EASTERN = ZoneInfo("America/New_York")


class BondSource(str, Enum):
    MANUAL = "manual"
    FEDINVEST = "fedinvest"
    CURVE = "curve"


# Short label shown in the dashboard's Exchange column.
SOURCE_LABEL: dict[BondSource, str] = {
    BondSource.MANUAL: "Manual",
    BondSource.FEDINVEST: "UST",
    BondSource.CURVE: "UST model",
}

# Longer description for the bond detail screen.
SOURCE_DESCRIPTION: dict[BondSource, str] = {
    BondSource.MANUAL: "Manual price from the portfolio file",
    BondSource.FEDINVEST: "U.S. Treasury FedInvest end-of-day price",
    BondSource.CURVE: "Model price from the Treasury par yield curve",
}


@dataclass(frozen=True)
class BondQuote:
    """A clean price in percent of par and where it came from."""

    price: float
    prev_close: float | None
    source: BondSource
    as_of: date | None  # date of the underlying data; None for manual prices


YieldCurve = dict[float, float]  # tenor in years -> par yield in percent


def is_treasury(bond: BondInfo) -> bool:
    """True when *bond* has a U.S. Treasury CUSIP."""
    return bool(bond.cusip and bond.cusip.startswith(TREASURY_CUSIP_PREFIX))


def eastern_today() -> date:
    """Today's date in New York, where Treasury publishes its data."""
    return datetime.now(_EASTERN).date()


# ---------------------------------------------------------------------------
# Process-wide cache
# ---------------------------------------------------------------------------

_cache: dict[object, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: object, loader: Callable[[], T]) -> T:
    """Return the cached value for *key*, calling *loader* when missing or stale.

    The loader runs outside the lock so a slow request doesn't block
    readers of other keys; two concurrent misses may both fetch, which is
    harmless.
    """
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]  # type: ignore[return-value]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    """Drop all cached responses (used by tests and ``doctor``)."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Parsers (pure, unit-tested against saved responses)
# ---------------------------------------------------------------------------


def parse_fedinvest_csv(text: str) -> dict[str, float]:
    """Return ``{cusip: clean price}`` from a FedInvest price CSV.

    The file has no header.  Columns are CUSIP, security type, rate,
    maturity date, call date, buy, sell and end-of-day price.  The
    end-of-day price is used when published; until then the sell (bid)
    price, and failing that the buy price.
    """
    prices: dict[str, float] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 8:
            continue
        cusip = row[0].strip().upper()
        candidates: list[float] = []
        for raw in (row[7], row[6], row[5]):
            try:
                candidates.append(float(raw))
            except ValueError:
                continue
        price = next((p for p in candidates if p > 0), None)
        if cusip and price is not None:
            prices[cusip] = price
    return prices


def _parse_tenor(header: str) -> float | None:
    match = _TENOR_RE.search(header)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    return amount / 12 if unit.startswith("mo") else amount


def parse_yield_curve_csv(text: str) -> list[tuple[date, YieldCurve]]:
    """Return ``[(date, {tenor_years: yield_pct})]`` sorted newest first."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return []
    tenors = [_parse_tenor(h) for h in header]
    curves: list[tuple[date, YieldCurve]] = []
    for row in reader:
        if not row:
            continue
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        curve: YieldCurve = {}
        for tenor, raw in zip(tenors[1:], row[1:]):
            if tenor is None or not raw.strip():
                continue
            try:
                curve[tenor] = float(raw)
            except ValueError:
                continue
        if curve:
            curves.append((day, curve))
    curves.sort(key=lambda item: item[0], reverse=True)
    return curves


def interpolate_yield(curve: YieldCurve, years: float) -> float:
    """Linearly interpolate the curve at *years*, clamping at both ends."""
    if not curve:
        raise ValueError("Yield curve is empty")
    points = sorted(curve.items())
    if years <= points[0][0]:
        return points[0][1]
    if years >= points[-1][0]:
        return points[-1][1]
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        if t0 <= years <= t1:
            return y0 + (y1 - y0) * (years - t0) / (t1 - t0)
    return points[-1][1]  # unreachable; keeps mypy satisfied


def model_price(bond: BondInfo, curve: YieldCurve, settle: date) -> float:
    """Clean price implied by the par curve yield at the bond's maturity."""
    years = max((bond.maturity - settle).days, 0) / 365.25
    return price_from_yield(bond, interpolate_yield(curve, years), settle)


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class BondPriceFetcher:
    """Resolves a :class:`BondQuote` for each bond position.

    Args:
        client: Optional ``httpx.Client`` (injected by tests).  When omitted
            a client is created per call to :meth:`fetch_quotes` and closed
            afterwards.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._csrf: str | None = None

    def fetch_quotes(
        self,
        bonds: dict[str, BondInfo],
        settle: date | None = None,
    ) -> dict[str, BondQuote]:
        """Return a quote for every symbol in *bonds* that can be priced.

        Symbols that no source can price are omitted, so the dashboard
        shows ``N/A`` for them.  Network errors are logged, never raised.
        """
        settle = settle or date.today()
        quotes: dict[str, BondQuote] = {}
        pending: dict[str, BondInfo] = {}

        for symbol, bond in bonds.items():
            if bond.price is not None:
                quotes[symbol] = BondQuote(bond.price, None, BondSource.MANUAL, None)
            elif is_treasury(bond):
                pending[symbol] = bond
            else:
                logger.info("Bond %s has no Treasury CUSIP and no manual price", symbol)

        if not pending:
            return quotes

        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
        )
        try:
            quotes.update(self._from_fedinvest(client, pending))
            remaining = {s: b for s, b in pending.items() if s not in quotes}
            if remaining:
                quotes.update(self._from_curve(client, remaining, settle))
        finally:
            if owns_client:
                client.close()

        for symbol in pending:
            if symbol not in quotes:
                logger.warning("No price available for bond %s", symbol)
        return quotes

    # -- FedInvest ---------------------------------------------------------

    def _fedinvest_day(self, client: httpx.Client, day: date) -> dict[str, float]:
        """Return all FedInvest prices for *day* (empty when none published)."""

        def load() -> dict[str, float]:
            if self._csrf is None:
                form = client.get(_FEDINVEST_FORM_URL)
                form.raise_for_status()
                match = _CSRF_RE.search(form.text)
                if match is None:
                    raise ValueError("FedInvest form has no CSRF token")
                self._csrf = match.group(1)
            response = client.post(
                _FEDINVEST_CSV_URL,
                data={
                    "priceDateDay": str(day.day),
                    "priceDateMonth": str(day.month),
                    "priceDateYear": str(day.year),
                    "fileType": "csv",
                    "csv": "CSV FORMAT",
                    "_csrf": self._csrf,
                },
            )
            response.raise_for_status()
            return parse_fedinvest_csv(response.text)

        return _cached(("fedinvest", day), load)

    def _latest_fedinvest_days(
        self, client: httpx.Client, count: int
    ) -> list[tuple[date, dict[str, float]]]:
        """Return up to *count* most recent non-empty FedInvest days."""
        found: list[tuple[date, dict[str, float]]] = []
        day = eastern_today()
        # Search a window long enough to find *count* published days.
        for _ in range(_MAX_LOOKBACK_DAYS * count):
            prices = self._fedinvest_day(client, day)
            if prices:
                found.append((day, prices))
                if len(found) == count:
                    break
            day -= timedelta(days=1)
        return found

    def _from_fedinvest(
        self, client: httpx.Client, bonds: dict[str, BondInfo]
    ) -> dict[str, BondQuote]:
        try:
            days = self._latest_fedinvest_days(client, count=2)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("FedInvest price fetch failed: %s", exc)
            return {}
        if not days:
            logger.warning("FedInvest returned no prices in the last week")
            return {}
        latest_day, latest = days[0]
        previous = days[1][1] if len(days) > 1 else {}
        quotes: dict[str, BondQuote] = {}
        for symbol, bond in bonds.items():
            assert bond.cusip is not None  # guaranteed by is_treasury()
            price = latest.get(bond.cusip)
            if price is None:
                logger.info("CUSIP %s not listed by FedInvest", bond.cusip)
                continue
            quotes[symbol] = BondQuote(
                price=price,
                prev_close=previous.get(bond.cusip),
                source=BondSource.FEDINVEST,
                as_of=latest_day,
            )
        return quotes

    # -- Yield curve -------------------------------------------------------

    def _curve_year(
        self, client: httpx.Client, year: int
    ) -> list[tuple[date, YieldCurve]]:
        def load() -> list[tuple[date, YieldCurve]]:
            response = client.get(
                _CURVE_URL.format(year=year),
                params={
                    "type": "daily_treasury_yield_curve",
                    "field_tdr_date_value": str(year),
                    "page": "",
                    "_format": "csv",
                },
            )
            response.raise_for_status()
            return parse_yield_curve_csv(response.text)

        return _cached(("curve", year), load)

    def _latest_curves(self, client: httpx.Client) -> list[tuple[date, YieldCurve]]:
        """Return at least the two most recent curves when available."""
        year = eastern_today().year
        curves = self._curve_year(client, year)
        if len(curves) < 2:
            # Early January: the previous close is in last year's file.
            curves = curves + self._curve_year(client, year - 1)
        return curves

    def _from_curve(
        self,
        client: httpx.Client,
        bonds: dict[str, BondInfo],
        settle: date,
    ) -> dict[str, BondQuote]:
        try:
            curves = self._latest_curves(client)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Treasury yield curve fetch failed: %s", exc)
            return {}
        if not curves:
            logger.warning("Treasury yield curve file had no data")
            return {}
        latest_day, latest = curves[0]
        previous = curves[1][1] if len(curves) > 1 else None
        quotes: dict[str, BondQuote] = {}
        for symbol, bond in bonds.items():
            # Price both days at the same settlement date so the daily
            # change reflects the move in yields, not the passage of time.
            quotes[symbol] = BondQuote(
                price=model_price(bond, latest, settle),
                prev_close=(model_price(bond, previous, settle) if previous else None),
                source=BondSource.CURVE,
                as_of=latest_day,
            )
        return quotes
