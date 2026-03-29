"""Market data fetching via yfinance."""

import importlib.resources
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf

from stonks_cli.exchanges import ExchangeSession
from stonks_cli.market_session import Session
from stonks_cli.stock_detail import StockDetail  # re-export for backward compat

logger = logging.getLogger(__name__)

# Keep this small: each worker thread opens its own peewee/SQLite connection
# to yfinance's timezone cache (3 fds in WAL mode), so a large pool exhausts
# macOS's default 256-fd limit quickly.
_MAX_EXCHANGE_FETCH_WORKERS = 2


# Module-level cache: base symbol (uppercase, e.g. "BTC") -> CoinGecko ID.
# Populated lazily by CryptoFetcher._ensure_coin_list() (bulk) and
# _resolve_via_search() (per-symbol fallback).
_cg_symbol_to_id: dict[str, str] = {}
_cg_coin_list_loaded: bool = False
_cg_lock = threading.Lock()


def _crypto_base(symbol: str) -> str:
    """Return the uppercase base ticker from a Yahoo-style crypto symbol.

    ``"BTC-USD"`` -> ``"BTC"``, ``"eth-usd"`` -> ``"ETH"``.
    """
    return symbol.upper().split("-")[0]


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
                _cg_coin_list_loaded = True
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
            base = _crypto_base(sym)
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
            base = _crypto_base(sym)
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
            base = _crypto_base(sym)
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

    def _fetch_simple_price(self, ids: str) -> dict:
        """Call ``/simple/price`` for a comma-joined string of CoinGecko IDs.

        Raises :class:`httpx.HTTPStatusError` or :class:`httpx.RequestError`
        on failure; callers handle retries.
        """
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

    def _fetch_coingecko_batch(self, cg_ids: list[str]) -> dict:
        """Fetch ``/simple/price`` for *cg_ids* with per-item fallback.

        Tries all IDs in a single batch request.  On failure retries each ID
        individually so that one bad ID cannot block all other coins.

        Returns the raw JSON dict (CoinGecko ID -> price/change payload).
        """
        ids_str = ",".join(cg_ids)
        result: dict = {}
        try:
            result = self._fetch_simple_price(ids_str)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            # Batch failed -- retry each CoinGecko ID individually so one bad
            # ID (e.g. a typo in external_id) cannot block all other coins.
            # A 401 on the batch does not mean global rate-limit; individual
            # single-ID requests may still succeed.
            logger.warning(
                "CoinGecko batch request failed (%s); retrying individually",
                _coingecko_error_summary(exc),
            )
            for cg_id in cg_ids:
                try:
                    result.update(self._fetch_simple_price(cg_id))
                except (httpx.HTTPStatusError, httpx.RequestError) as exc_i:
                    logger.warning(
                        "CoinGecko request failed for %s (%s)",
                        cg_id,
                        _coingecko_error_summary(exc_i),
                    )
                except Exception as exc_i:  # noqa: BLE001
                    logger.warning(
                        "Unexpected error during individual CoinGecko fetch for %s: %s",
                        cg_id,
                        exc_i,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error during CoinGecko batch fetch: %s", exc)
        return result

    @staticmethod
    def _parse_coingecko_response(
        result: dict,
        id_to_syms: dict[str, list[str]],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Extract ``(prices, prev_closes)`` from a raw ``/simple/price`` response.

        Args:
            result: Raw JSON dict keyed by CoinGecko ID.
            id_to_syms: Mapping of CoinGecko ID -> list of portfolio symbols.

        The previous close is derived as ``price / (1 + change_24h / 100)``.
        """
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

        all_cg_ids = list(set(sym_to_id.values()))
        result = self._fetch_coingecko_batch(all_cg_ids)
        return self._parse_coingecko_response(result, id_to_syms)


def _last_close_per_symbol(
    close: "pd.DataFrame",
    symbols: list[str],
) -> dict[str, float]:
    """Return the last non-NaN close value for each symbol in *close*.

    Args:
        close: DataFrame returned by :func:`_yf_download_close` (columns are
            ticker symbols, index is a datetime).
        symbols: Symbols to extract; those absent from *close* are skipped.

    Returns:
        Mapping of symbol -> ``float(series.iloc[-1])`` for every symbol that
        has at least one non-NaN row.
    """
    result: dict[str, float] = {}
    for sym in symbols:
        if sym not in close.columns:
            continue
        series = close[sym].dropna()
        if not series.empty:
            result[sym] = float(series.iloc[-1])
    return result


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
        The ``data["Close"]`` DataFrame (always a DataFrame, never a Series),
        or ``None`` if the download failed or returned an empty result.
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
    close = data["Close"]
    # yfinance returns a Series (not a DataFrame) when a single ticker is
    # requested and the result has no MultiIndex.  Normalise so callers can
    # always assume a DataFrame.
    if isinstance(close, pd.Series):
        close = close.to_frame(name=symbols[0])
    return close


class PriceFetcher:
    """Fetches the latest closing price for a list of stock symbols.

    Uses yfinance.download() to batch all symbols in a single API call.
    """

    def __init__(self) -> None:
        self._session = ExchangeSession()

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
        return _last_close_per_symbol(close, normalized)

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

        # Keep only rows strictly before today so we always get the
        # last *completed* trading day's close, regardless of whether
        # yfinance already includes a partial row for today.
        today = pd.Timestamp("today").normalize()
        filtered = close[close.index.normalize() < today]
        return _last_close_per_symbol(filtered, normalized)

    def current_session(self, symbol: str) -> str:
        """Return the current market session label for *symbol*.

        Delegates to :meth:`ExchangeSession.current_session`.
        Returns one of ``'pre'``, ``'regular'``, ``'post'``, or ``'closed'``.
        """
        return self._session.current_session(symbol)

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

    def fetch_best_equity_prices(
        self, symbols: list[str]
    ) -> dict[str, tuple[float, str]]:
        """Return the best available price and session for each equity symbol.

        Applies a 3-tier yfinance fallback strategy:
        1. Extended-hours batch (``prepost=True``, 1-minute bars)
        2. Regular daily batch for symbols still missing after tier 1
        3. Individual ``fast_info`` lookup for any remaining symbols

        Args:
            symbols: List of ticker symbols (e.g. ['AAPL', 'NVDA']).

        Returns:
            Mapping of uppercase symbol -> (price, session).
        """
        if not symbols:
            return {}

        result: dict[str, tuple[float, str]] = {}

        # Tier 1: extended-hours batch.
        result.update(self.fetch_extended_prices(symbols))

        # Tier 2: regular daily batch for symbols still missing.
        missing = [s for s in symbols if s.upper() not in result]
        if missing:
            batch = self.fetch_prices(missing)
            for sym, price in batch.items():
                result[sym] = (price, self.current_session(sym))

        # Tier 3: individual fast_info for any still-missing symbols.
        still_missing = [s for s in symbols if s.upper() not in result]
        for sym in still_missing:
            single = self.fetch_price_single(sym)
            if single is not None:
                result[sym.upper()] = (single, self.current_session(sym))

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

        last = _last_close_per_symbol(close, symbols)
        for currency, symbol in zip(non_base, symbols):
            if symbol in last:
                rates[currency] = last[symbol]
        return rates

    def fetch_stock_detail(self, symbol: str) -> StockDetail:
        """Fetch comprehensive detail for a single ticker.

        Delegates to :class:`~stonks_cli.stock_detail.StockDetailFetcher`.
        Kept on ``PriceFetcher`` for backward compatibility with callers that
        already hold a ``PriceFetcher`` instance.
        """
        from stonks_cli.stock_detail import StockDetailFetcher

        return StockDetailFetcher().fetch_stock_detail(symbol)
