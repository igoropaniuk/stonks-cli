"""Exchange metadata, session detection, and calendar utilities."""

import logging
import zoneinfo
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from functools import lru_cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]

from stonks_cli.market_session import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExchangeInfo:
    """All static metadata for a single exchange.

    Each entry in _EXCHANGES uses four distinct identifiers, each consumed by
    a different system:

    - The dict key (e.g. "BA") is the Yahoo Finance ticker suffix.  Tickers
      traded on this exchange carry it as a dot-suffix (e.g. "YPF.BA").
      exchange_info_for() looks up the entry via symbol.rsplit(".", 1)[1].
      US exchanges use yfinance exchange codes as keys instead (e.g. "NMS",
      "NYQ") since US tickers have no suffix.

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

# Extended-hours bounds in each exchange's local time.  Only applied when
# ExchangeInfo.extended_hours is True; all such entries today are NYSE /
# NASDAQ (US) with the same 04:00 / 20:00 ET window.  If a non-US exchange
# ever enables extended hours with different bounds, push these down onto
# ExchangeInfo.
_EXTENDED_PRE_OPEN = dtime(4, 0)
_EXTENDED_POST_CLOSE = dtime(20, 0)


# Reverse lookup: yfinance exchange code -> display label, derived from _EXCHANGES.
_YF_CODE_TO_LABEL: dict[str, str] = {
    code: info.label for info in _EXCHANGES.values() for code in info.yf_codes
}


def exchange_info_for(symbol: str) -> ExchangeInfo | None:
    """Return the :class:`ExchangeInfo` for *symbol*, or ``None`` for crypto.

    Resolution order:
    - Symbol contains ``'-'``           -> crypto, return ``None``
    - Symbol contains ``'.'``           -> look up suffix in ``_EXCHANGES``
    - Plain ticker (no dot, no dash)    -> return ``_US_EXCHANGE``
    """
    if "-" in symbol:
        return None  # crypto
    if "." in symbol:
        return _EXCHANGES.get(symbol.rsplit(".", 1)[1])
    return _US_EXCHANGE


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


class ExchangeSession:
    """Session detection and exchange-calendar utilities.

    All lookup methods are exposed as static methods so they can be called
    without an instance.  The :meth:`current_session` instance method
    orchestrates them into a single wall-clock-time-based session label.

    ``PriceFetcher`` holds one instance (``self._session``) and delegates
    its ``current_session`` call to it.
    """

    @staticmethod
    @lru_cache(maxsize=64)
    def load_calendar(name: str):
        """Return the ``exchange-calendars`` calendar for *name* (cached)."""
        return xcals.get_calendar(name)

    @staticmethod
    def market_session(ts, tz_name: str, open_time: dtime, close_time: dtime) -> str:
        """Return ``Session.PRE``, ``Session.REGULAR``, or ``Session.POST``.

        Args:
            ts: A timezone-aware timestamp (bar close or wall-clock now).
            tz_name: IANA timezone name for the exchange (e.g. 'America/New_York').
            open_time: Exchange regular open (local time).
            close_time: Exchange regular close (local time).
        """
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

    @staticmethod
    def is_exchange_open(
        tz_name: str,
        open_time: dtime,
        close_time: dtime,
        calendar_name: str | None = None,
    ) -> bool:
        """Return ``True`` if the exchange is currently open.

        When *calendar_name* is provided, delegates to exchange-calendars for
        accurate holiday awareness.  Falls back to a weekend + trading-hours
        check (no holiday awareness) if the calendar cannot be loaded.
        """
        if calendar_name:
            try:
                cal = ExchangeSession.load_calendar(calendar_name)
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

    @staticmethod
    def is_trading_day(
        tz_name: str,
        calendar_name: str | None = None,
    ) -> bool:
        """Return ``True`` if today is a trading day for this exchange.

        Unlike :meth:`is_exchange_open`, this ignores the time of day and
        only answers "does this exchange have a session today?".  This
        allows session labels (pre/regular/post) to be derived from the bar
        timestamp even when the current clock time is outside regular hours.
        """
        if calendar_name:
            try:
                cal = ExchangeSession.load_calendar(calendar_name)
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

    @staticmethod
    def calendar_name_for(symbol: str) -> str | None:
        """Return the exchange-calendars MIC for *symbol*'s exchange, or ``None``."""
        info = exchange_info_for(symbol)
        return info.calendar_name if info else None

    @staticmethod
    def hours_for(symbol: str) -> tuple[str, dtime, dtime] | None:
        """Return ``(tz_name, open, close)`` for *symbol*, or ``None`` for crypto."""
        info = exchange_info_for(symbol)
        if info is None:
            return None
        return (info.tz_name, info.open_time, info.close_time)

    @staticmethod
    def extended_hours_for(symbol: str) -> bool:
        """Return ``True`` if *symbol*'s exchange supports extended-hours data."""
        info = exchange_info_for(symbol)
        return bool(info and info.extended_hours)

    def current_session(self, symbol: str) -> str:
        """Return the current market session label for *symbol*.

        Uses the current wall-clock time rather than a bar timestamp, so it
        can assign a meaningful session label to prices that were fetched via
        a fallback path that has no associated bar.

        Returns one of ``'pre'``, ``'regular'``, ``'post'``, or ``'closed'``.
        """
        hours = ExchangeSession.hours_for(symbol)
        if hours is None:
            return Session.REGULAR  # crypto -- always regular
        calendar_name = ExchangeSession.calendar_name_for(symbol)
        if not ExchangeSession.is_trading_day(hours[0], calendar_name=calendar_name):
            return Session.CLOSED
        now = pd.Timestamp.now(tz="UTC")
        session = ExchangeSession.market_session(now, *hours)
        if session == Session.REGULAR:
            return session
        if not ExchangeSession.extended_hours_for(symbol):
            return Session.CLOSED
        # ``market_session`` reports PRE for *any* time before the regular
        # open and POST for *any* time after the regular close, so the dead
        # of night gets labelled pre-market.  Narrow those windows to the
        # real extended-hours bounds so 02:00 ET isn't treated as NYSE
        # pre-market (which actually starts at 04:00 ET).
        local_time = now.astimezone(zoneinfo.ZoneInfo(hours[0])).time()
        if session == Session.PRE and local_time < _EXTENDED_PRE_OPEN:
            return Session.CLOSED
        if session == Session.POST and local_time >= _EXTENDED_POST_CLOSE:
            return Session.CLOSED
        return session
