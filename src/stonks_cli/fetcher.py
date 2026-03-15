"""Market data fetching via yfinance."""

import math
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import time as dtime
from functools import lru_cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf

# (IANA timezone, market_open, market_close) keyed by Yahoo Finance suffix.
# Crypto symbols contain '-' and have no entry here → always "regular".
# Plain symbols (no '.' and no '-') are US equities → "America/New_York".
_EXCHANGE_HOURS: dict[str, tuple[str, dtime, dtime]] = {
    # ── Americas ──────────────────────────────────────────────────────────
    "SA": ("America/Sao_Paulo", dtime(10, 0), dtime(17, 55)),
    "BA": ("America/Argentina/Buenos_Aires", dtime(11, 0), dtime(17, 0)),
    "MX": ("America/Mexico_City", dtime(8, 30), dtime(15, 0)),
    "SN": ("America/Santiago", dtime(9, 30), dtime(16, 0)),
    "LIM": ("America/Lima", dtime(9, 0), dtime(16, 0)),
    "TO": ("America/Toronto", dtime(9, 30), dtime(16, 0)),
    "V": ("America/Toronto", dtime(9, 30), dtime(16, 0)),
    # ── Europe ────────────────────────────────────────────────────────────
    "L": ("Europe/London", dtime(8, 0), dtime(16, 30)),
    "PA": ("Europe/Paris", dtime(9, 0), dtime(17, 30)),
    "AS": ("Europe/Amsterdam", dtime(9, 0), dtime(17, 30)),
    "BR": ("Europe/Brussels", dtime(9, 0), dtime(17, 30)),
    "LS": ("Europe/Lisbon", dtime(8, 0), dtime(16, 30)),
    "MI": ("Europe/Rome", dtime(9, 0), dtime(17, 30)),
    "DE": ("Europe/Berlin", dtime(9, 0), dtime(17, 30)),
    "F": ("Europe/Berlin", dtime(8, 0), dtime(20, 0)),
    "SW": ("Europe/Zurich", dtime(9, 0), dtime(17, 30)),
    "ST": ("Europe/Stockholm", dtime(9, 0), dtime(17, 25)),
    "HE": ("Europe/Helsinki", dtime(10, 0), dtime(18, 25)),
    "CO": ("Europe/Copenhagen", dtime(9, 0), dtime(17, 0)),
    "OL": ("Europe/Oslo", dtime(9, 0), dtime(16, 20)),
    "WA": ("Europe/Warsaw", dtime(9, 0), dtime(17, 35)),
    "AT": ("Europe/Athens", dtime(10, 15), dtime(17, 20)),
    # ── Asia-Pacific ──────────────────────────────────────────────────────
    "AX": ("Australia/Sydney", dtime(10, 0), dtime(16, 0)),
    "NZ": ("Pacific/Auckland", dtime(10, 0), dtime(17, 0)),
    "HK": ("Asia/Hong_Kong", dtime(9, 30), dtime(16, 0)),
    "T": ("Asia/Tokyo", dtime(9, 0), dtime(15, 30)),
    "KS": ("Asia/Seoul", dtime(9, 0), dtime(15, 30)),
    "KQ": ("Asia/Seoul", dtime(9, 0), dtime(15, 30)),
    "TW": ("Asia/Taipei", dtime(9, 0), dtime(13, 30)),
    "TWO": ("Asia/Taipei", dtime(9, 0), dtime(13, 30)),
    "SS": ("Asia/Shanghai", dtime(9, 30), dtime(15, 0)),
    "SZ": ("Asia/Shanghai", dtime(9, 30), dtime(15, 0)),
    "NS": ("Asia/Kolkata", dtime(9, 15), dtime(15, 30)),
    "BO": ("Asia/Kolkata", dtime(9, 15), dtime(15, 30)),
    "JK": ("Asia/Jakarta", dtime(9, 30), dtime(16, 0)),
    "SI": ("Asia/Singapore", dtime(9, 0), dtime(17, 0)),
    "KL": ("Asia/Kuala_Lumpur", dtime(9, 0), dtime(17, 0)),
    "BK": ("Asia/Bangkok", dtime(10, 0), dtime(16, 30)),
    "VN": ("Asia/Ho_Chi_Minh", dtime(9, 15), dtime(14, 45)),
}

_US_HOURS: tuple[str, dtime, dtime] = ("America/New_York", dtime(9, 30), dtime(16, 0))

# exchange-calendars MIC identifiers keyed by Yahoo Finance suffix.
# Vietnam (.VN / XSTC) is intentionally absent — not supported by exchange-calendars;
# the time-based fallback in _is_exchange_open handles it.
_EXCHANGE_CALENDAR: dict[str, str] = {
    # ── Americas ──────────────────────────────────────────────────────────
    "SA": "BVMF",  # B3 São Paulo
    "BA": "XBUE",  # Buenos Aires
    "MX": "XMEX",  # Bolsa Mexicana
    "SN": "XSGO",  # Santiago
    "LIM": "XLIM",  # Lima
    "TO": "XTSE",  # Toronto
    "V": "XTSE",  # TSX Venture → same calendar
    # ── Europe ────────────────────────────────────────────────────────────
    "L": "XLON",  # London
    "PA": "XPAR",  # Paris
    "AS": "XAMS",  # Amsterdam
    "BR": "XBRU",  # Brussels
    "LS": "XLIS",  # Lisbon
    "MI": "XMIL",  # Milan
    "DE": "XETR",  # XETRA
    "F": "XFRA",  # Frankfurt
    "SW": "XSWX",  # SIX Swiss
    "ST": "XSTO",  # Stockholm
    "HE": "XHEL",  # Helsinki
    "CO": "XCSE",  # Copenhagen
    "OL": "XOSL",  # Oslo Børs
    "WA": "XWAR",  # Warsaw
    "AT": "ASEX",  # Athens
    # ── Asia-Pacific ──────────────────────────────────────────────────────
    "AX": "XASX",  # ASX
    "NZ": "XNZE",  # NZX
    "HK": "XHKG",  # Hong Kong
    "T": "XTKS",  # Tokyo
    "KS": "XKRX",  # KOSPI
    "KQ": "XKRX",  # KOSDAQ → same calendar
    "TW": "XTAI",  # Taiwan SE
    "TWO": "XTAI",  # Taiwan OTC → same calendar
    "SS": "XSHG",  # Shanghai
    "SZ": "XSHE",  # Shenzhen
    "NS": "XNSE",  # NSE India
    "BO": "XBOM",  # BSE India
    "JK": "XIDX",  # Indonesia
    "SI": "XSES",  # Singapore
    "KL": "XKLS",  # Malaysia
    "BK": "XBKK",  # Thailand
}
_US_CALENDAR = "XNYS"


@lru_cache(maxsize=64)
def _load_calendar(name: str):
    return xcals.get_calendar(name)


def _finite(value) -> float | None:
    """Convert *value* to float, returning None for None/NaN/inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _market_session(ts, tz_name: str, open_time: dtime, close_time: dtime) -> str:
    """Return 'pre', 'regular', or 'post' given a bar timestamp and exchange hours."""
    try:
        t = ts.astimezone(zoneinfo.ZoneInfo(tz_name)).time()
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return "regular"
    if t < open_time:
        return "pre"
    if t < close_time:
        return "regular"
    return "post"


def _is_exchange_open(
    tz_name: str,
    open_time: dtime,
    close_time: dtime,
    calendar_name: str | None = None,
) -> bool:
    """Return True if the exchange is currently open.

    When *calendar_name* is provided, delegates to exchange-calendars for
    accurate holiday awareness.  Falls back to a weekend + trading-hours
    check (no holiday awareness) if the calendar cannot be loaded.
    """
    if calendar_name:
        try:
            cal = _load_calendar(calendar_name)
            now = pd.Timestamp.now(tz="UTC")
            return bool(cal.is_open_on_minute(now, ignore_breaks=True))
        except (AttributeError, LookupError, ValueError):
            pass  # fall through to time-based check

    now_local = datetime.now(zoneinfo.ZoneInfo(tz_name))
    if now_local.weekday() >= 5:
        return False
    return open_time <= now_local.time() < close_time


def _exchange_calendar_name(symbol: str) -> str | None:
    """Return the exchange-calendars MIC for *symbol*'s exchange, or None."""
    if "-" in symbol:
        return None  # crypto -- no calendar
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1]
        return _EXCHANGE_CALENDAR.get(suffix)
    return _US_CALENDAR  # plain US ticker


def _exchange_hours(symbol: str) -> tuple[str, dtime, dtime] | None:
    """Return (tz_name, open, close) for *symbol*, or None for crypto/unknown."""
    if "-" in symbol:
        return None  # crypto
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1]
        return _EXCHANGE_HOURS.get(suffix)  # None if unrecognised
    return _US_HOURS  # plain US ticker


class PriceFetcher:
    """Fetches the latest closing price for a list of stock symbols.

    Uses yfinance.download() to batch all symbols in a single API call.
    """

    def fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return the most recent closing price for each symbol.

        Symbols with no available data are silently omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol → latest closing price.
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]

        data = yf.download(
            tickers=normalized,
            period="1d",
            auto_adjust=True,
            progress=False,
        )

        if data.empty:
            return {}

        # With multi_level_index=True (yfinance default), data["Close"] is
        # always a DataFrame whose columns are the ticker symbols.
        close = data["Close"]

        result: dict[str, float] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                result[symbol] = float(series.iloc[-1])

        return result

    def fetch_extended_prices(self, symbols: list[str]) -> dict[str, tuple[float, str]]:
        """Return the best available price for each symbol, including extended hours.

        Uses a single batched ``yf.download()`` call with ``prepost=True`` and a
        1-minute interval so that pre- and post-market bars are included.  The
        session label is derived from the timestamp of each symbol's last bar
        relative to its exchange's market hours.  If the exchange is currently
        closed (weekend or holiday), the session is "closed".

        * Equities on supported exchanges: "pre" / "regular" / "post" / "closed"
        * Crypto or equities on unknown exchanges: always "regular"

        Symbols with no available data are silently omitted from the result.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> (price, session).
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]

        data = yf.download(
            tickers=normalized,
            period="1d",
            interval="1m",
            prepost=True,
            auto_adjust=True,
            progress=False,
        )

        if data.empty:
            return {}

        close = data["Close"]

        result: dict[str, tuple[float, str]] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if series.empty:
                continue
            price = float(series.iloc[-1])
            ts = series.index[-1]
            hours = _exchange_hours(symbol)
            if hours is None:
                session = "regular"
            elif not _is_exchange_open(
                *hours, calendar_name=_exchange_calendar_name(symbol)
            ):
                session = "closed"
            else:
                session = _market_session(ts, *hours)
            result[symbol] = (price, session)

        return result

    def fetch_forex_rates(
        self, currencies: list[str], base: str = "USD"
    ) -> dict[str, float]:
        """Return exchange rates: 1 unit of currency → how many base units.

        Uses yfinance forex pairs (e.g. EURUSD=X for EUR→USD).
        The base currency is always included as 1.0. Currencies for which
        no rate can be fetched are omitted from the result.

        Args:
            currencies: ISO 4217 currency codes (e.g. ['EUR', 'GBP']).
            base: Target/base currency code (default 'USD').

        Returns:
            Mapping of currency code → exchange rate in base.
        """
        base = base.upper()
        rates: dict[str, float] = {base: 1.0}

        non_base = [c.upper() for c in currencies if c.upper() != base]
        if not non_base:
            return rates

        symbols = [f"{c}{base}=X" for c in non_base]

        data = yf.download(
            tickers=symbols,
            period="1d",
            auto_adjust=False,
            progress=False,
        )

        if data.empty:
            return rates

        close = data["Close"]
        for currency, symbol in zip(non_base, symbols):
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                rates[currency] = float(series.iloc[-1])

        return rates
