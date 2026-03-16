"""Market data fetching via yfinance."""

import math
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from functools import lru_cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf

_MAX_EXCHANGE_FETCH_WORKERS = 8


@dataclass(frozen=True)
class ExchangeInfo:
    """All static metadata for a single exchange.

    Each entry in _EXCHANGES uses four distinct identifiers, each consumed by
    a different system:

    - The dict key (e.g. "BA") is the Yahoo Finance ticker suffix.  Tickers
      traded on this exchange carry it as a dot-suffix (e.g. "YPF.BA").
      _exchange_hours() and _exchange_calendar_name() look up the entry via
      symbol.rsplit(".", 1)[1].  US exchanges use yfinance exchange codes as
      keys instead (e.g. "NMS", "NYQ") since US tickers have no suffix.

    - calendar_name (e.g. "XBUE") is the ISO 10383 MIC used by the
      exchange-calendars library.  It is passed to xcals.get_calendar() for
      holiday-aware open/closed detection.  None when the exchange is not
      supported by exchange-calendars (e.g. Vietnam/HOSE).

    - label (e.g. "BYMA") is the display string shown in the Exchange column
      of the TUI.  It reflects the current operator name rather than the
      legacy exchange name (e.g. BYMA replaced MERVAL/BCBA in 2017).

    - yf_codes (e.g. ("BUE",)) lists the exchange code(s) that
      yf.Ticker(sym).fast_info.exchange returns for securities on this
      exchange.  _YF_CODE_TO_LABEL is built from these so that
      exchange_label() can resolve a display name from the code alone,
      without needing the ticker suffix.
    """

    tz_name: str
    open_time: dtime
    close_time: dtime
    calendar_name: str | None
    label: str
    yf_codes: tuple[str, ...] = ()


# Exchange metadata keyed either by Yahoo Finance ticker suffix (e.g. "AS", "L")
# or by yfinance exchange code for US exchanges (e.g. "NMS", "NYQ").
# Crypto symbols contain '-' and have no entry;
# plain US tickers (no '.', no '-') use _US_EXCHANGE.
# Vietnam (.VN / XSTC) has calendar_name=None -- not supported by exchange-calendars.
_EXCHANGES: dict[str, ExchangeInfo] = {
    # -- United States (keyed by yfinance exchange code) --
    "NMS": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NMS",)
    ),
    "NGM": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NGM",)
    ),
    "NCM": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NCM",)
    ),
    "NYQ": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NYSE", ("NYQ",)
    ),
    "NYA": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NYSE AMEX", ("NYA",)
    ),
    "PCX": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NYSE Arca", ("PCX",)
    ),
    "BTS": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "CBOE", ("BTS",)
    ),
    "OBB": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "OTC", ("OBB",)
    ),
    "PNK": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "OTC", ("PNK",)
    ),
    # -- Americas --
    "SA": ExchangeInfo(
        "America/Sao_Paulo", dtime(10, 0), dtime(17, 55), "BVMF", "B3", ("SAO",)
    ),
    "BA": ExchangeInfo(
        "America/Argentina/Buenos_Aires",
        dtime(11, 0),
        dtime(17, 0),
        "XBUE",
        "BYMA",
        ("BUE",),
    ),
    "MX": ExchangeInfo(
        "America/Mexico_City", dtime(8, 30), dtime(15, 0), "XMEX", "BMV", ("MEX",)
    ),
    "SN": ExchangeInfo(
        "America/Santiago", dtime(9, 30), dtime(16, 0), "XSGO", "BCS", ("SAN",)
    ),
    "LIM": ExchangeInfo(
        "America/Lima", dtime(9, 0), dtime(16, 0), "XLIM", "BVL", ("LIM",)
    ),
    "TO": ExchangeInfo(
        "America/Toronto", dtime(9, 30), dtime(16, 0), "XTSE", "TSX", ("TOR",)
    ),
    "V": ExchangeInfo(
        "America/Toronto", dtime(9, 30), dtime(16, 0), "XTSE", "TSXV", ("VAN",)
    ),
    # -- Europe --
    "L": ExchangeInfo(
        "Europe/London", dtime(8, 0), dtime(16, 30), "XLON", "LSE", ("LSE", "IOB")
    ),
    "PA": ExchangeInfo(
        "Europe/Paris", dtime(9, 0), dtime(17, 30), "XPAR", "ENX Paris", ("PAR",)
    ),
    "AS": ExchangeInfo(
        "Europe/Amsterdam", dtime(9, 0), dtime(17, 30), "XAMS", "ENX AMS", ("AMS",)
    ),
    "BR": ExchangeInfo(
        "Europe/Brussels", dtime(9, 0), dtime(17, 30), "XBRU", "ENX BRU", ("BRU",)
    ),
    "LS": ExchangeInfo(
        "Europe/Lisbon", dtime(8, 0), dtime(16, 30), "XLIS", "ENX LIS", ("LIS",)
    ),
    "MI": ExchangeInfo(
        "Europe/Rome", dtime(9, 0), dtime(17, 30), "XMIL", "Borsa IT", ("MIL",)
    ),
    "DE": ExchangeInfo(
        "Europe/Berlin", dtime(9, 0), dtime(17, 30), "XETR", "XETRA", ("ETR",)
    ),
    "F": ExchangeInfo(
        "Europe/Berlin", dtime(8, 0), dtime(20, 0), "XFRA", "FSE", ("FRA",)
    ),
    "SW": ExchangeInfo(
        "Europe/Zurich", dtime(9, 0), dtime(17, 30), "XSWX", "SIX", ("EBS",)
    ),
    "ST": ExchangeInfo(
        "Europe/Stockholm", dtime(9, 0), dtime(17, 25), "XSTO", "NASDAQ", ("STO",)
    ),
    "HE": ExchangeInfo(
        "Europe/Helsinki", dtime(10, 0), dtime(18, 25), "XHEL", "NASDAQ", ("HEL",)
    ),
    "CO": ExchangeInfo(
        "Europe/Copenhagen", dtime(9, 0), dtime(17, 0), "XCSE", "NASDAQ", ("CPH",)
    ),
    "OL": ExchangeInfo(
        "Europe/Oslo", dtime(9, 0), dtime(16, 20), "XOSL", "Oslo Bors", ("OSL",)
    ),
    "WA": ExchangeInfo(
        "Europe/Warsaw", dtime(9, 0), dtime(17, 35), "XWAR", "GPW", ("WSE",)
    ),
    "AT": ExchangeInfo(
        "Europe/Athens", dtime(10, 15), dtime(17, 20), "ASEX", "ATHEX", ("ATH",)
    ),
    # -- Asia-Pacific --
    "AX": ExchangeInfo(
        "Australia/Sydney", dtime(10, 0), dtime(16, 0), "XASX", "ASX", ("ASX",)
    ),
    "NZ": ExchangeInfo(
        "Pacific/Auckland", dtime(10, 0), dtime(17, 0), "XNZE", "NZX", ("NZE",)
    ),
    "HK": ExchangeInfo(
        "Asia/Hong_Kong", dtime(9, 30), dtime(16, 0), "XHKG", "HKEX", ("HKG",)
    ),
    "T": ExchangeInfo(
        "Asia/Tokyo", dtime(9, 0), dtime(15, 30), "XTKS", "TSE", ("TKY", "JPX")
    ),
    "KS": ExchangeInfo(
        "Asia/Seoul", dtime(9, 0), dtime(15, 30), "XKRX", "KRX", ("KRX",)
    ),
    "KQ": ExchangeInfo(
        "Asia/Seoul", dtime(9, 0), dtime(15, 30), "XKRX", "KOSDAQ", ("KOQ",)
    ),
    "TW": ExchangeInfo(
        "Asia/Taipei", dtime(9, 0), dtime(13, 30), "XTAI", "TWSE", ("TAI",)
    ),
    "TWO": ExchangeInfo(
        "Asia/Taipei", dtime(9, 0), dtime(13, 30), "XTAI", "TPEX", ("TWO",)
    ),
    "SS": ExchangeInfo(
        "Asia/Shanghai", dtime(9, 30), dtime(15, 0), "XSHG", "SSE", ("SHH",)
    ),
    "SZ": ExchangeInfo(
        "Asia/Shanghai", dtime(9, 30), dtime(15, 0), "XSHE", "SZSE", ("SHZ",)
    ),
    "NS": ExchangeInfo(
        "Asia/Kolkata", dtime(9, 15), dtime(15, 30), "XNSE", "NSE", ("NSI",)
    ),
    "BO": ExchangeInfo(
        "Asia/Kolkata", dtime(9, 15), dtime(15, 30), "XBOM", "BSE", ("BSE",)
    ),
    "JK": ExchangeInfo(
        "Asia/Jakarta", dtime(9, 30), dtime(16, 0), "XIDX", "IDX", ("JKT",)
    ),
    "SI": ExchangeInfo(
        "Asia/Singapore", dtime(9, 0), dtime(17, 0), "XSES", "SGX", ("SGX",)
    ),
    "KL": ExchangeInfo(
        "Asia/Kuala_Lumpur", dtime(9, 0), dtime(17, 0), "XKLS", "Bursa", ("KLS",)
    ),
    "BK": ExchangeInfo(
        "Asia/Bangkok", dtime(10, 0), dtime(16, 30), "XBKK", "SET", ("BKK",)
    ),
    "VN": ExchangeInfo("Asia/Ho_Chi_Minh", dtime(9, 15), dtime(14, 45), None, "HOSE"),
}

_US_EXCHANGE = ExchangeInfo(
    "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NYSE/NASDAQ"
)


# Reverse lookup: yfinance exchange code -> display label, derived from _EXCHANGES.
_YF_CODE_TO_LABEL: dict[str, str] = {
    code: info.label for info in _EXCHANGES.values() for code in info.yf_codes
}


def exchange_label(symbol: str, exchange_code: str | None = None) -> str:
    """Return a short exchange name for *symbol*.

    Resolution order:
    1. Crypto (contains '-')          -> "Crypto"
    2. *exchange_code* in lookup table -> mapped display name
    3. Known exchange suffix           -> short name from the suffix label table
    4. Unknown suffix                  -> the raw suffix (e.g. "XY")
    5. Plain US ticker, no code        -> "NYSE/NASDAQ"
    """
    if "-" in symbol:
        return "Crypto"
    if exchange_code and exchange_code in _YF_CODE_TO_LABEL:
        return _YF_CODE_TO_LABEL[exchange_code]
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1]
        info = _EXCHANGES.get(suffix)
        return info.label if info else suffix
    return "NYSE/NASDAQ"


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


def _is_trading_day(
    tz_name: str,
    calendar_name: str | None = None,
) -> bool:
    """Return True if today is a trading day for this exchange.

    Unlike :func:`_is_exchange_open`, this ignores the time of day and only
    answers the question "does this exchange have a session today?".  This
    allows session labels (pre/regular/post) to be derived from the bar
    timestamp even when the current clock time is outside regular hours.
    """
    if calendar_name:
        try:
            cal = _load_calendar(calendar_name)
            today = pd.Timestamp.now(tz="UTC").normalize()
            return bool(cal.is_session(today))
        except (AttributeError, LookupError, ValueError):
            pass  # fall through to weekend check

    now_local = datetime.now(zoneinfo.ZoneInfo(tz_name))
    return now_local.weekday() < 5


def _exchange_calendar_name(symbol: str) -> str | None:
    """Return the exchange-calendars MIC for *symbol*'s exchange, or None."""
    if "-" in symbol:
        return None  # crypto -- no calendar
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1]
        info = _EXCHANGES.get(suffix)
        return info.calendar_name if info else None
    return _US_EXCHANGE.calendar_name  # plain US ticker


def _exchange_hours(symbol: str) -> tuple[str, dtime, dtime] | None:
    """Return (tz_name, open, close) for *symbol*, or None for crypto/unknown."""
    if "-" in symbol:
        return None  # crypto
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[1]
        info = _EXCHANGES.get(suffix)
        if info is None:
            return None
        return (info.tz_name, info.open_time, info.close_time)
    return (_US_EXCHANGE.tz_name, _US_EXCHANGE.open_time, _US_EXCHANGE.close_time)


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
            Mapping of uppercase symbol -> latest closing price.
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]

        try:
            data = yf.download(
                tickers=normalized,
                period="1d",
                auto_adjust=True,
                progress=False,
            )
        except RuntimeError:
            return {}

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

    def fetch_price_single(self, symbol: str) -> float | None:
        """Return the most recent price for *symbol* using an individual lookup.

        Uses ``yf.Ticker.fast_info`` so it is not affected by the DataFrame
        alignment issues that can occur when batch-downloading tickers from
        multiple exchanges.  Returns ``None`` when no price is available.
        """
        try:
            price = yf.Ticker(symbol.upper()).fast_info.last_price
            if price is None or (isinstance(price, float) and math.isnan(price)):
                return None
            return float(price)
        except (ValueError, KeyError, AttributeError):
            return None

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

        try:
            data = yf.download(
                tickers=normalized,
                period="1d",
                interval="1m",
                prepost=True,
                auto_adjust=True,
                progress=False,
            )
        except RuntimeError:
            return {}

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
            elif not _is_trading_day(
                hours[0], calendar_name=_exchange_calendar_name(symbol)
            ):
                session = "closed"
            else:
                session = _market_session(ts, *hours)
            result[symbol] = (price, session)

        return result

    def fetch_exchange_names(self, symbols: list[str]) -> dict[str, str]:
        """Return yfinance exchange codes for all equity symbols (US and non-US).

        Crypto symbols (containing '-') are skipped -- they have no exchange
        code in yfinance.  All other symbols (plain US tickers and those with
        a dot suffix) are queried concurrently.

        Uses a thread pool so that N symbols are fetched concurrently rather
        than sequentially.

        Args:
            symbols: List of ticker symbols (mixed US and non-US).

        Returns:
            Mapping of uppercase symbol -> yfinance exchange code
            (e.g. ``{"AAPL": "NMS", "ASML.AS": "AMS", "7203.T": "TKY"}``).
        """
        equities = [s.upper() for s in symbols if "-" not in s]
        if not equities:
            return {}

        def _get(sym: str) -> tuple[str, str | None]:
            try:
                return sym, yf.Ticker(sym).fast_info.exchange
            except Exception:
                return sym, None

        result: dict[str, str] = {}
        with ThreadPoolExecutor(
            max_workers=min(_MAX_EXCHANGE_FETCH_WORKERS, len(equities))
        ) as pool:
            for sym, code in pool.map(_get, equities):
                if code:
                    result[sym] = code
        return result

    def fetch_forex_rates(
        self, currencies: list[str], base: str = "USD"
    ) -> dict[str, float]:
        """Return exchange rates: 1 unit of currency -> how many base units.

        Uses yfinance forex pairs (e.g. EURUSD=X for EUR->USD).
        The base currency is always included as 1.0. Currencies for which
        no rate can be fetched are omitted from the result.

        Args:
            currencies: ISO 4217 currency codes (e.g. ['EUR', 'GBP']).
            base: Target/base currency code (default 'USD').

        Returns:
            Mapping of currency code -> exchange rate in base.
        """
        base = base.upper()
        rates: dict[str, float] = {base: 1.0}

        non_base = [c.upper() for c in currencies if c.upper() != base]
        if not non_base:
            return rates

        symbols = [f"{c}{base}=X" for c in non_base]

        try:
            data = yf.download(
                tickers=symbols,
                period="1d",
                auto_adjust=False,
                progress=False,
            )
        except RuntimeError:
            return rates

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
