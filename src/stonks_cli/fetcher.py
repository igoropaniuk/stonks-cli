"""Market data fetching via yfinance."""

import importlib.resources
import logging
import math
import threading
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from functools import lru_cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import httpx
import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf

from stonks_cli._session import Session

logger = logging.getLogger(__name__)

# Keep this small: each worker thread opens its own peewee/SQLite connection
# to yfinance's timezone cache (3 fds in WAL mode), so a large pool exhausts
# macOS's default 256-fd limit quickly.
_MAX_EXCHANGE_FETCH_WORKERS = 2


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
    extended_hours: bool = (
        False  # True only for US exchanges (pre/post market via yfinance)
    )


# Exchange metadata keyed either by Yahoo Finance ticker suffix (e.g. "AS", "L")
# or by yfinance exchange code for US exchanges (e.g. "NMS", "NYQ").
# Crypto symbols contain '-' and have no entry;
# plain US tickers (no '.', no '-') use _US_EXCHANGE.
# Vietnam (.VN / XSTC) has calendar_name=None -- not supported by exchange-calendars.
_EXCHANGES: dict[str, ExchangeInfo] = {
    # -- United States (keyed by yfinance exchange code) --
    "NMS": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NMS",), True
    ),
    "NGM": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NGM",), True
    ),
    "NCM": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NASDAQ", ("NCM",), True
    ),
    "NYQ": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "NYSE", ("NYQ",), True
    ),
    "NYA": ExchangeInfo(
        "America/New_York",
        dtime(9, 30),
        dtime(16, 0),
        "XNYS",
        "NYSE AMEX",
        ("NYA",),
        True,
    ),
    "PCX": ExchangeInfo(
        "America/New_York",
        dtime(9, 30),
        dtime(16, 0),
        "XNYS",
        "NYSE Arca",
        ("PCX",),
        True,
    ),
    "BTS": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "CBOE", ("BTS",), True
    ),
    "OBB": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "OTC", ("OBB",), True
    ),
    "PNK": ExchangeInfo(
        "America/New_York", dtime(9, 30), dtime(16, 0), "XNYS", "OTC", ("PNK",), True
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
    "America/New_York",
    dtime(9, 30),
    dtime(16, 0),
    "XNYS",
    "NYSE/NASDAQ",
    extended_hours=True,
)


# Reverse lookup: yfinance exchange code -> display label, derived from _EXCHANGES.
_YF_CODE_TO_LABEL: dict[str, str] = {
    code: info.label for info in _EXCHANGES.values() for code in info.yf_codes
}


def exchange_label(
    symbol: str,
    exchange_code: str | None = None,
    asset_type: str | None = None,
) -> str:
    """Return a short exchange name for *symbol*.

    Resolution order:
    1. asset_type == 'crypto'          -> "Crypto"
    2. Crypto (contains '-')          -> "Crypto"
    3. *exchange_code* in lookup table -> mapped display name
    4. Known exchange suffix           -> short name from the suffix label table
    5. Unknown suffix                  -> the raw suffix (e.g. "XY")
    6. Plain US ticker, no code        -> "NYSE/NASDAQ"
    """
    if asset_type == "crypto" or "-" in symbol:
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
    """Return Session.PRE, Session.REGULAR, or Session.POST given a bar timestamp."""
    try:
        t = ts.astimezone(zoneinfo.ZoneInfo(tz_name)).time()
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        logger.debug("Unknown timezone %r for session detection: %s", tz_name, exc)
        return Session.REGULAR
    if t < open_time:
        return Session.PRE
    if t < close_time:
        return Session.REGULAR
    return Session.POST


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
            now = pd.Timestamp.now(tz=zoneinfo.ZoneInfo("UTC"))
            return bool(cal.is_open_on_minute(now, ignore_breaks=True))
        except (AttributeError, LookupError, ValueError) as exc:
            logger.debug(
                "Calendar load failed for %s, falling back to time check: %s",
                calendar_name,
                exc,
            )

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
            today = pd.Timestamp.now(tz=zoneinfo.ZoneInfo("UTC")).normalize()
            return bool(cal.is_session(today))
        except (AttributeError, LookupError, ValueError) as exc:
            logger.debug(
                "Calendar load failed for %s, falling back to weekend check: %s",
                calendar_name,
                exc,
            )

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


@dataclass
class StockDetail:
    """Comprehensive detail data for a single stock."""

    symbol: str
    name: str  # full company/fund name
    # Performance overview (trailing returns vs S&P 500)
    performance: dict[str, tuple[str, str]]  # label -> (stock_return, sp500_return)
    # Price history per period: label -> (dates, closes)
    price_histories: dict[str, tuple[list[str], list[float]]]
    # Financial summary (label -> formatted value)
    summary: dict[str, str]
    # Earnings trends (quarterly)
    eps_quarters: list[str]
    eps_actual: list[float | None]
    eps_estimate: list[float | None]
    eps_diff: list[float | None]
    next_earnings_date: str
    next_eps_estimate: float | None
    # Revenue vs Earnings (quarterly)
    rev_quarters: list[str]
    rev_values: list[float]
    earn_values: list[float]
    # Analyst
    price_targets: dict[str, float]
    recommendations: list[dict[str, int | str]]
    recommendation_key: str
    num_analysts: int
    # Statistics
    valuation: dict[str, str]
    financials: dict[str, str]


def _period_to_month(period: str) -> str:
    """Convert '0m', '-1m', '-2m' etc. to 'Mar 2026' style labels."""
    try:
        offset = int(period.replace("m", ""))
    except (ValueError, AttributeError) as exc:
        logger.debug("Cannot parse period %r: %s", period, exc)
        return period
    today = datetime.now(tz=zoneinfo.ZoneInfo("UTC"))
    # Shift month by offset, handling year boundaries
    month = today.month + offset
    year = today.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return datetime(year, month, 1).strftime("%b %Y")


def _fmt_price(v) -> str:
    f = _finite(v)
    return f"{f:.2f}" if f is not None else "N/A"


def _fmt_bid_ask(price, size) -> str:
    p = _finite(price)
    s = _finite(size)
    if p is None:
        return "N/A"
    size_str = f" x {int(s)}" if s is not None else ""
    return f"{p:.2f}{size_str}"


def _fmt_range(low, high) -> str:
    lo = _finite(low)
    hi = _finite(high)
    if lo is None or hi is None:
        return "N/A"
    return f"{lo:.2f} - {hi:.2f}"


def _fmt_int(v) -> str:
    f = _finite(v)
    return f"{int(f):,}" if f is not None else "N/A"


def _fmt_large(v) -> str:
    f = _finite(v)
    if f is None:
        return "N/A"
    af = abs(f)
    if af >= 1e12:
        return f"{f / 1e12:.2f}T"
    if af >= 1e9:
        return f"{f / 1e9:.2f}B"
    if af >= 1e6:
        return f"{f / 1e6:.2f}M"
    return f"{f:,.0f}"


def _fmt_dec(v, places: int = 2) -> str:
    f = _finite(v)
    return f"{f:.{places}f}" if f is not None else "N/A"


def _fmt_pct(v) -> str:
    f = _finite(v)
    return f"{f * 100:.2f}%" if f is not None else "N/A"


def _fiscal_quarter(ts) -> str:
    """Convert a pandas Timestamp to a fiscal quarter label like 'Q1 FY25'."""
    month = ts.month
    year = ts.year
    q = (month - 1) // 3 + 1
    return f"Q{q} FY{year % 100}"


def _trailing_return(hist) -> str:
    """Calculate trailing total return from a price history DataFrame."""
    if hist is None or hist.empty or len(hist) < 2:
        return "N/A"
    try:
        first = float(hist["Close"].iloc[0])
        last = float(hist["Close"].iloc[-1])
        if first == 0:
            return "N/A"
        ret = (last - first) / first * 100
        sign = "+ " if ret >= 0 else "- "
        return f"{sign}{abs(ret):.2f}%"
    except (KeyError, IndexError, TypeError):
        return "N/A"


def _calc_performance(
    symbol: str,
) -> dict[str, tuple[str, str]]:
    """Compute trailing returns for *symbol* vs S&P 500 (^GSPC).

    Returns a dict like ``{"YTD Return": ("+8.14%", "+3.08%"), ...}``.
    """
    periods = {
        "YTD Return": "ytd",
        "1-Year Return": "1y",
        "3-Year Return": "3y",
        "5-Year Return": "5y",
    }
    result: dict[str, tuple[str, str]] = {}
    try:
        stock_ticker = yf.Ticker(symbol)
        sp_ticker = yf.Ticker("^GSPC")
        for label, period in periods.items():
            stock_hist = stock_ticker.history(period=period)
            sp_hist = sp_ticker.history(period=period)
            result[label] = (_trailing_return(stock_hist), _trailing_return(sp_hist))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Performance calculation failed for %s: %s", symbol, exc)
    return result


# Module-level cache: base symbol (uppercase, e.g. "BTC") -> CoinGecko ID.
# Populated lazily by CryptoFetcher._ensure_coin_list() (bulk) and
# _resolve_via_search() (per-symbol fallback).
_cg_symbol_to_id: dict[str, str] = {}
_cg_coin_list_loaded: bool = False
_cg_lock = threading.Lock()


def _coingecko_error_summary(exc: BaseException) -> str:
    """Return a one-line summary for a CoinGecko HTTP exception."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 429):
            return f"CoinGecko rate limit reached ({code})"
        return f"HTTP {code}"
    return type(exc).__name__


class CryptoFetcher:
    """Fetches cryptocurrency prices from the CoinGecko API.

    Uses direct HTTP requests via ``httpx``.  If ``COINGECKO_DEMO_API_KEY``
    is set, it is sent as the ``x-cg-demo-api-key`` header for higher rate
    limits.  Without a key the public endpoint is used with no auth header
    (required -- a fake key triggers 401 on multi-coin batch requests).

    Symbol resolution for each ticker (e.g. ``BTC-USD``):

    1. Module-level cache (survives across refreshes within a process).
    2. Bulk ``/coins/list`` lookup -- unambiguous symbols (only one coin
       uses that ticker) are resolved immediately.
    3. ``/search`` endpoint for ambiguous symbols -- returns results ranked
       by market cap so the first exact-symbol match is the canonical coin.
    4. Lowercased base symbol as a last-resort heuristic.
    """

    _BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self) -> None:
        import os

        api_key = os.environ.get("COINGECKO_DEMO_API_KEY")
        headers: dict[str, str] = {}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        self._http = httpx.Client(
            base_url=self._BASE_URL,
            headers=headers,
            timeout=30.0,
        )

    @staticmethod
    def _ensure_coin_list() -> None:
        """Load the bundled coin-list mapping into the module cache.

        The mapping file (``data/coingecko_coins.json``) is a JSON dict of
        ``SYMBOL -> coingecko_id`` for all unambiguous symbols (exactly one
        coin uses that ticker).  It is regenerated at release time.

        Ambiguous symbols (multiple coins share a ticker) are not in the
        file and will fall through to ``_resolve_via_search``.
        """
        global _cg_coin_list_loaded  # noqa: PLW0603
        if _cg_coin_list_loaded:
            return
        with _cg_lock:
            if _cg_coin_list_loaded:
                return
            _cg_coin_list_loaded = True
        try:
            import json

            data = (
                importlib.resources.files("stonks_cli.data")
                .joinpath("coingecko_coins.json")
                .read_text(encoding="utf-8")
            )
            mapping: dict[str, str] = json.loads(data)
            for sym, cg_id in mapping.items():
                if sym not in _cg_symbol_to_id:
                    _cg_symbol_to_id[sym] = cg_id
        except FileNotFoundError:
            logger.warning("CoinGecko coin list file not found.")
        except json.JSONDecodeError:
            logger.warning("CoinGecko coin list file is malformed JSON.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error loading CoinGecko coin list: %s", exc)

    def _resolve_via_search(self, base_symbol: str) -> str | None:
        """Use ``/search`` to find the CoinGecko ID for *base_symbol*.

        The endpoint returns coins ranked by market cap, so the first
        result whose ticker matches exactly is the canonical coin.
        """
        try:
            resp = self._http.get("/search", params={"query": base_symbol})
            resp.raise_for_status()
            for coin in resp.json().get("coins") or []:
                sym = coin.get("symbol", "")
                if sym and sym.upper() == base_symbol:
                    return coin.get("id")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "CoinGecko search API failed for %s (%s)",
                base_symbol,
                _coingecko_error_summary(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Unexpected error during CoinGecko search for %s: %s", base_symbol, exc
            )
        return None

    @staticmethod
    def _resolve_from_external_ids(
        symbols: list[str],
        ext: dict[str, str],
    ) -> tuple[dict[str, str], list[str]]:
        """Resolve symbols using caller-supplied external IDs.

        Args:
            symbols: Yahoo-style tickers (original case preserved).
            ext: Uppercase-keyed symbol -> CoinGecko ID mapping.

        Returns:
            ``(mapping, unresolved)`` where *mapping* holds the resolved
            entries and *unresolved* lists symbols not found in *ext*.
        """
        mapping: dict[str, str] = {}
        unresolved: list[str] = []
        for sym in symbols:
            upper = sym.upper()
            base = upper.split("-")[0]
            if upper in ext:
                mapping[sym] = ext[upper]
            elif base in _cg_symbol_to_id:
                mapping[sym] = _cg_symbol_to_id[base]
            else:
                unresolved.append(sym)
        return mapping, unresolved

    @staticmethod
    def _resolve_from_cache(
        symbols: list[str],
    ) -> tuple[dict[str, str], list[str]]:
        """Resolve symbols from the module-level coin-list cache.

        Intended to be called after :meth:`_ensure_coin_list` so the cache
        has been populated.

        Args:
            symbols: Unresolved Yahoo-style tickers.

        Returns:
            ``(mapping, needs_search)`` where *needs_search* lists symbols
            still unknown after consulting the cache.
        """
        mapping: dict[str, str] = {}
        needs_search: list[str] = []
        for sym in symbols:
            base = sym.upper().split("-")[0]
            if base in _cg_symbol_to_id:
                mapping[sym] = _cg_symbol_to_id[base]
            else:
                needs_search.append(sym)
        return mapping, needs_search

    def _resolve_from_api(self, symbols: list[str]) -> dict[str, str]:
        """Resolve symbols via the CoinGecko /search endpoint.

        Populates the module-level cache as a side-effect so subsequent
        calls skip the API for already-resolved symbols.

        Args:
            symbols: Tickers that could not be resolved from cache.

        Returns:
            Mapping of symbol -> CoinGecko ID; falls back to the
            lowercase base symbol when the API returns no match.
        """
        mapping: dict[str, str] = {}
        for sym in symbols:
            base = sym.upper().split("-")[0]
            cg_id = self._resolve_via_search(base)
            if cg_id:
                _cg_symbol_to_id[base] = cg_id
                mapping[sym] = cg_id
            else:
                mapping[sym] = base.lower()
        return mapping

    def _resolve_ids(
        self,
        symbols: list[str],
        external_ids: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Map Yahoo-style symbols to CoinGecko coin IDs.

        Orchestrates three resolution passes in order of cost:
        1. Caller-supplied *external_ids* + module-level cache (no I/O).
        2. Bulk ``/coins/list`` cache after :meth:`_ensure_coin_list`.
        3. Per-symbol ``/search`` API calls for anything still unknown.

        If *external_ids* contains an entry for a symbol it is used
        directly, skipping both the bulk coin-list and per-symbol search.
        """
        # Normalise external_ids keys to uppercase for case-insensitive lookup.
        ext = {k.upper(): v for k, v in (external_ids or {}).items()}

        # Pass 1: external_ids + module-level cache (no API calls).
        mapping, unresolved = self._resolve_from_external_ids(symbols, ext)
        if not unresolved:
            return mapping

        # Pass 2: bulk /coins/list for unambiguous symbols.
        self._ensure_coin_list()
        cache_hits, needs_search = self._resolve_from_cache(unresolved)
        mapping.update(cache_hits)

        # Pass 3: /search for ambiguous or unknown symbols.
        mapping.update(self._resolve_from_api(needs_search))
        return mapping

    def fetch_prices_and_changes(
        self,
        symbols: list[str],
        external_ids: dict[str, str] | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Return ``(prices, prev_closes)`` for crypto symbols.

        Args:
            symbols: Yahoo-style tickers (e.g. ``["BTC-USD"]``).
            external_ids: Optional mapping of symbol -> CoinGecko coin ID
                from the portfolio YAML ``external_id`` field.  When
                present these take priority over automatic resolution.

        Uses the CoinGecko ``simple/price`` endpoint with 24-hour change.
        The previous close is derived as ``price / (1 + change_24h / 100)``.
        """
        if not symbols:
            return {}, {}

        sym_to_id = self._resolve_ids(symbols, external_ids)
        # Reverse lookup: CoinGecko ID -> list of portfolio symbols
        id_to_syms: dict[str, list[str]] = {}
        for sym, cg_id in sym_to_id.items():
            id_to_syms.setdefault(cg_id, []).append(sym)

        # Deduplicated CoinGecko IDs for the batch request.
        all_cg_ids = list(set(sym_to_id.values()))
        ids_str = ",".join(all_cg_ids)

        def _fetch(ids: str) -> dict:
            resp = self._http.get(
                "/simple/price",
                params={
                    "ids": ids,
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
            )
            resp.raise_for_status()
            return resp.json()

        result: dict = {}
        try:
            result = _fetch(ids_str)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            # Batch failed -- retry each CoinGecko ID individually so one bad
            # ID (e.g. a typo in external_id) cannot block all other coins.
            # A 401 on the batch does not mean global rate-limit; individual
            # single-ID requests may still succeed.
            logger.warning(
                "CoinGecko batch request failed (%s); retrying individually",
                _coingecko_error_summary(exc),
            )
            for cg_id in all_cg_ids:
                try:
                    result.update(_fetch(cg_id))
                except (httpx.HTTPStatusError, httpx.RequestError) as exc_individual:
                    logger.warning(
                        "CoinGecko request failed for %s (%s)",
                        cg_id,
                        _coingecko_error_summary(exc_individual),
                    )
                except Exception as exc_individual:  # noqa: BLE001
                    logger.warning(
                        "Unexpected error during individual CoinGecko fetch for %s: %s",
                        cg_id,
                        exc_individual,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected error during initial CoinGecko batch fetch: %s", exc
            )

        prices: dict[str, float] = {}
        prev_closes: dict[str, float] = {}
        for cg_id, item in result.items():
            usd_price = item.get("usd")
            change_24h = item.get("usd_24h_change")
            if usd_price is None:
                continue
            price = float(usd_price)
            for sym in id_to_syms.get(cg_id, []):
                prices[sym] = price
                if change_24h is not None:
                    prev_closes[sym] = price / (1 + float(change_24h) / 100)
        return prices, prev_closes


def _yf_download_close(
    symbols: list[str],
    *,
    period: str,
    description: str = "price",
    auto_adjust: bool = True,
    interval: str | None = None,
    prepost: bool = False,
) -> "pd.DataFrame | None":
    """Call ``yf.download`` and return the ``Close`` DataFrame, or ``None`` on failure.

    Encapsulates the common normalize -> download -> empty-check pattern shared
    by all batch-download methods.  Each caller is responsible for extracting
    and interpreting the per-symbol series from the returned DataFrame.

    Args:
        symbols: Already-normalised ticker list passed directly to yfinance.
        period: yfinance period string (e.g. ``"1d"``, ``"5d"``).
        description: Short label used in error log messages.
        auto_adjust: Passed to ``yf.download`` (default True).
        interval: Optional bar interval (e.g. ``"1m"``); omitted when None.
        prepost: Whether to include pre/post-market bars (default False).

    Returns:
        The ``data["Close"]`` DataFrame, or ``None`` if the download failed or
        returned an empty result.
    """
    kw: dict = {
        "tickers": symbols,
        "period": period,
        "auto_adjust": auto_adjust,
        "progress": False,
        "threads": False,
    }
    if interval is not None:
        kw["interval"] = interval
    if prepost:
        kw["prepost"] = True
    try:
        data = yf.download(**kw)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s download failed for %s: %s", description, symbols, exc)
        return None
    if data.empty:
        return None
    return data["Close"]


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
        # With multi_level_index=True (yfinance default), data["Close"] is
        # always a DataFrame whose columns are the ticker symbols.
        close = _yf_download_close(normalized, period="1d", description="price")
        if close is None:
            return {}

        result: dict[str, float] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                result[symbol] = float(series.iloc[-1])

        return result

    def fetch_previous_closes(self, symbols: list[str]) -> dict[str, float]:
        """Return the previous trading day's closing price for each symbol.

        Uses a 5-day window to account for weekends and holidays.
        Symbols with fewer than 2 data points are omitted.

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> previous closing price.
        """
        if not symbols:
            return {}

        normalized = [s.upper() for s in symbols]
        close = _yf_download_close(
            normalized, period="5d", description="previous-close"
        )
        if close is None:
            return {}

        if isinstance(close, pd.Series):
            # yfinance returns a Series (not a DataFrame) when a single ticker
            # is requested and the result is a flat (non-MultiIndex) DataFrame.
            # Normalise to a one-column DataFrame keyed by the requested symbol.
            close = close.to_frame(name=normalized[0])

        today = pd.Timestamp("today").normalize()
        result: dict[str, float] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            # Keep only rows strictly before today so we always get the
            # last *completed* trading day's close, regardless of whether
            # yfinance already includes a partial row for today.
            before_today = series[series.index.normalize() < today]
            if not before_today.empty:
                result[symbol] = float(before_today.iloc[-1])

        return result

    def current_session(self, symbol: str) -> str:
        """Return the current market session label for *symbol*.

        Uses the current wall-clock time rather than a bar timestamp, so it
        can assign a meaningful session label to prices that were fetched via
        a fallback path that has no associated bar.

        Returns one of ``'pre'``, ``'regular'``, ``'post'``, or ``'closed'``.
        """
        hours = _exchange_hours(symbol)
        if hours is None:
            return Session.REGULAR  # crypto -- always regular
        calendar_name = _exchange_calendar_name(symbol)
        if not _is_trading_day(hours[0], calendar_name=calendar_name):
            return Session.CLOSED
        now = pd.Timestamp.now(tz="UTC")
        session = _market_session(now, *hours)
        if session != Session.REGULAR:
            suffix = symbol.rsplit(".", 1)[1] if "." in symbol else None
            info = _EXCHANGES.get(suffix) if suffix else _US_EXCHANGE
            if not info or not info.extended_hours:
                return Session.CLOSED
        return session

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
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            logger.debug("Cannot read fast_info price for %s: %s", symbol, exc)
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
        close = _yf_download_close(
            normalized,
            period="1d",
            description="extended-price",
            interval="1m",
            prepost=True,
        )
        if close is None:
            return {}

        today = pd.Timestamp.now(tz="UTC").normalize()
        result: dict[str, tuple[float, str]] = {}
        for symbol in normalized:
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if series.empty:
                continue
            price = float(series.iloc[-1])
            # If the last bar is from a previous day, the ticker has no
            # intraday data for today (not trading in extended hours).
            last_bar_date = series.index[-1].tz_convert("UTC").normalize()
            if last_bar_date < today:
                result[symbol] = (price, Session.CLOSED)
            else:
                result[symbol] = (price, self.current_session(symbol))

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
            except Exception as exc:  # noqa: BLE001
                logger.debug("Cannot fetch exchange name for %s: %s", sym, exc)
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
        close = _yf_download_close(
            symbols, period="1d", description="forex", auto_adjust=False
        )
        if close is None:
            return rates

        for currency, symbol in zip(non_base, symbols):
            if symbol not in close.columns:
                continue
            series = close[symbol].dropna()
            if not series.empty:
                rates[currency] = float(series.iloc[-1])

        return rates

    @staticmethod
    def _fetch_price_histories(
        t: yf.Ticker,
        symbol: str,
    ) -> dict[str, tuple[list[str], list[float]]]:
        chart_periods = {
            "1 Day": ("1d", "5m"),
            "1 Month": ("1mo", None),
            "1 Year": ("1y", None),
            "5 Years": ("5y", None),
        }
        result: dict[str, tuple[list[str], list[float]]] = {}
        for label, (period, interval) in chart_periods.items():
            try:
                kw: dict[str, str] = {"period": period}
                if interval:
                    kw["interval"] = interval
                h = t.history(**kw)
                if h is not None and not h.empty:
                    fmt = "%H:%M" if interval else "%Y-%m-%d"
                    dates = [d.strftime(fmt) for d in h.index]
                    closes = [float(v) for v in h["Close"].tolist()]
                    result[label] = (dates, closes)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Cannot fetch price history for %s (%s): %s", symbol, period, exc
                )
        return result

    @staticmethod
    def _fetch_summary(info: dict) -> dict[str, str]:
        earnings_ts = _finite(info.get("earningsTimestampStart")) or _finite(
            info.get("earningsTimestamp")
        )
        earnings_date = "N/A"
        if earnings_ts is not None:
            earnings_date = datetime.fromtimestamp(int(earnings_ts)).strftime(
                "%b %d, %Y"
            )

        ex_div_ts = _finite(info.get("exDividendDate"))
        ex_div_date = "N/A"
        if ex_div_ts is not None:
            ex_div_date = datetime.fromtimestamp(int(ex_div_ts)).strftime("%b %d, %Y")

        div_rate = _finite(info.get("dividendRate"))
        div_yield = _finite(info.get("dividendYield"))
        if div_rate is not None and div_yield is not None:
            fwd_div = f"{div_rate:.2f} ({div_yield * 100:.2f}%)"
        else:
            fwd_div = "N/A"

        return {
            "Previous Close": _fmt_price(info.get("previousClose")),
            "Open": _fmt_price(info.get("open")),
            "Bid": _fmt_bid_ask(info.get("bid"), info.get("bidSize")),
            "Ask": _fmt_bid_ask(info.get("ask"), info.get("askSize")),
            "Day's Range": _fmt_range(info.get("dayLow"), info.get("dayHigh")),
            "52 Week Range": _fmt_range(
                info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
            ),
            "Volume": _fmt_int(info.get("volume")),
            "Avg. Volume": _fmt_int(info.get("averageVolume")),
            "Market Cap (intraday)": _fmt_large(info.get("marketCap")),
            "Beta (5Y Monthly)": _fmt_dec(info.get("beta")),
            "PE Ratio (TTM)": _fmt_dec(info.get("trailingPE")),
            "EPS (TTM)": _fmt_price(info.get("trailingEps")),
            "Earnings Date (est.)": earnings_date,
            "Forward Dividend & Yield": fwd_div,
            "Ex-Dividend Date": ex_div_date,
            "1y Target Est": _fmt_price(info.get("targetMeanPrice")),
        }

    @staticmethod
    def _fetch_earnings(
        t: yf.Ticker, symbol: str, earnings_date: str
    ) -> tuple[
        list[str],
        list[float | None],
        list[float | None],
        list[float | None],
        str,
        float | None,
    ]:
        eps_quarters: list[str] = []
        eps_actual: list[float | None] = []
        eps_estimate: list[float | None] = []
        eps_diff: list[float | None] = []
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                for q_ts in eh.index:
                    eps_quarters.append(_fiscal_quarter(q_ts))
                    eps_actual.append(_finite(eh.loc[q_ts, "epsActual"]))
                    eps_estimate.append(_finite(eh.loc[q_ts, "epsEstimate"]))
                    eps_diff.append(_finite(eh.loc[q_ts, "epsDifference"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch earnings history for %s: %s", symbol, exc)

        next_eps_estimate: float | None = None
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty and "0q" in ee.index:
                next_eps_estimate = _finite(ee.loc["0q", "avg"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch EPS estimate for %s: %s", symbol, exc)

        return (
            eps_quarters,
            eps_actual,
            eps_estimate,
            eps_diff,
            earnings_date,
            next_eps_estimate,
        )

    @staticmethod
    def _fetch_revenue(
        t: yf.Ticker,
        symbol: str,
    ) -> tuple[list[str], list[float], list[float]]:
        rev_quarters: list[str] = []
        rev_values: list[float] = []
        earn_values: list[float] = []
        try:
            qinc = t.quarterly_income_stmt
            if qinc is not None and not qinc.empty:
                cols = list(reversed(qinc.columns))[:5]
                for col in cols:
                    rev_quarters.append(_fiscal_quarter(col))
                    rev = (
                        _finite(qinc.loc["Total Revenue", col])
                        if "Total Revenue" in qinc.index
                        else None
                    )
                    earn = (
                        _finite(qinc.loc["Net Income", col])
                        if "Net Income" in qinc.index
                        else None
                    )
                    rev_values.append((rev or 0.0) / 1e9)
                    earn_values.append((earn or 0.0) / 1e9)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch revenue data for %s: %s", symbol, exc)
        return rev_quarters, rev_values, earn_values

    @staticmethod
    def _fetch_analyst(
        t: yf.Ticker, symbol: str, info: dict
    ) -> tuple[dict[str, float], list[dict[str, int | str]], str, int]:
        price_targets: dict[str, float] = {}
        try:
            apt = t.analyst_price_targets
            if isinstance(apt, dict):
                for k in ("current", "low", "mean", "median", "high"):
                    v = _finite(apt.get(k))
                    if v is not None:
                        price_targets[k] = v
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch analyst targets for %s: %s", symbol, exc)

        recommendations: list[dict[str, int | str]] = []
        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                for _, row in rs.iterrows():
                    recommendations.append(
                        {
                            "period": _period_to_month(str(row.get("period", ""))),
                            "strongBuy": int(row.get("strongBuy", 0)),
                            "buy": int(row.get("buy", 0)),
                            "hold": int(row.get("hold", 0)),
                            "sell": int(row.get("sell", 0)),
                            "strongSell": int(row.get("strongSell", 0)),
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot fetch recommendations for %s: %s", symbol, exc)

        recommendation_key = str(info.get("recommendationKey", "N/A"))
        num_analysts = int(_finite(info.get("numberOfAnalystOpinions")) or 0)
        return price_targets, recommendations, recommendation_key, num_analysts

    @staticmethod
    def _fetch_statistics(info: dict) -> tuple[dict[str, str], dict[str, str]]:
        valuation: dict[str, str] = {
            "Market Cap": _fmt_large(info.get("marketCap")),
            "Enterprise Value": _fmt_large(info.get("enterpriseValue")),
            "Trailing P/E": _fmt_dec(info.get("trailingPE")),
            "Forward P/E": _fmt_dec(info.get("forwardPE")),
            "PEG Ratio (5yr expected)": _fmt_dec(info.get("pegRatio")),
            "Price/Sales (ttm)": _fmt_dec(info.get("priceToSalesTrailing12Months")),
            "Price/Book (mrq)": _fmt_dec(info.get("priceToBook")),
            "Enterprise Value/Revenue": _fmt_dec(info.get("enterpriseToRevenue")),
            "Enterprise Value/EBITDA": _fmt_dec(info.get("enterpriseToEbitda")),
        }
        financials: dict[str, str] = {
            "Profit Margin": _fmt_pct(info.get("profitMargins")),
            "Return on Assets (ttm)": _fmt_pct(info.get("returnOnAssets")),
            "Return on Equity (ttm)": _fmt_pct(info.get("returnOnEquity")),
            "Revenue (ttm)": _fmt_large(info.get("totalRevenue")),
            "Net Income Avail to Common (ttm)": _fmt_large(
                info.get("netIncomeToCommon")
            ),
            "Diluted EPS (ttm)": _fmt_price(info.get("trailingEps")),
            "Total Cash (mrq)": _fmt_large(info.get("totalCash")),
            "Total Debt/Equity (mrq)": _fmt_dec(info.get("debtToEquity")),
        }
        return valuation, financials

    def fetch_stock_detail(self, symbol: str) -> StockDetail:
        """Fetch comprehensive detail for a single ticker."""
        t = yf.Ticker(symbol.upper())
        info = t.info if isinstance(t.info, dict) else {}

        performance = _calc_performance(symbol.upper())
        sym = symbol.upper()
        price_histories = self._fetch_price_histories(t, sym)
        summary = self._fetch_summary(info)
        earnings_date = summary.get("Earnings Date (est.)", "N/A")
        (
            eps_quarters,
            eps_actual,
            eps_estimate,
            eps_diff,
            next_earnings_date,
            next_eps_estimate,
        ) = self._fetch_earnings(t, sym, earnings_date)
        rev_quarters, rev_values, earn_values = self._fetch_revenue(t, sym)
        price_targets, recommendations, recommendation_key, num_analysts = (
            self._fetch_analyst(t, sym, info)
        )
        valuation, financials_dict = self._fetch_statistics(info)

        return StockDetail(
            symbol=symbol.upper(),
            name=str(info.get("longName") or info.get("shortName") or symbol.upper()),
            performance=performance,
            price_histories=price_histories,
            summary=summary,
            eps_quarters=eps_quarters,
            eps_actual=eps_actual,
            eps_estimate=eps_estimate,
            eps_diff=eps_diff,
            next_earnings_date=next_earnings_date,
            next_eps_estimate=next_eps_estimate,
            rev_quarters=rev_quarters,
            rev_values=rev_values,
            earn_values=earn_values,
            price_targets=price_targets,
            recommendations=recommendations,
            recommendation_key=recommendation_key,
            num_analysts=num_analysts,
            valuation=valuation,
            financials=financials_dict,
        )
