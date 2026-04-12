"""CoinGecko-backed cryptocurrency price fetching."""

import importlib.resources
import json
import logging
import os
import threading

import httpx

logger = logging.getLogger(__name__)


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


def resolve_coin_id(symbol: str, external_id: str | None = None) -> str | None:
    """Return the CoinGecko coin ID for a crypto symbol.

    Priority: explicit *external_id* > bundled coin map > ``None``.

    Args:
        symbol: Yahoo Finance-style crypto symbol (e.g. ``"BTC-USD"``).
        external_id: Explicit CoinGecko coin ID from the portfolio YAML.

    Returns:
        The coin ID string (e.g. ``"bitcoin"``), or ``None`` when not found.
    """
    if external_id:
        return external_id
    CryptoFetcher._ensure_coin_list()
    return _cg_symbol_to_id.get(_crypto_base(symbol))


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
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Unexpected error loading CoinGecko coin list", exc_info=True
                )

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
    def _resolve_without_api(
        symbols: list[str],
        ext: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], list[str]]:
        """Resolve symbols from caller-supplied IDs and the module-level cache.

        Checks *ext* first (when provided), then falls through to the
        ``_cg_symbol_to_id`` module cache.  No network calls are made.

        Args:
            symbols: Yahoo-style tickers (original case preserved).
            ext: Optional uppercase-keyed symbol -> CoinGecko ID mapping
                supplied by the caller (e.g. from portfolio ``external_id``
                fields).

        Returns:
            ``(mapping, remaining)`` where *remaining* lists symbols that
            could not be resolved from either source.
        """
        mapping: dict[str, str] = {}
        remaining: list[str] = []
        for sym in symbols:
            base = _crypto_base(sym)
            if ext is not None and sym.upper() in ext:
                mapping[sym] = ext[sym.upper()]
            else:
                with _cg_lock:
                    if base in _cg_symbol_to_id:
                        mapping[sym] = _cg_symbol_to_id[base]
                    else:
                        remaining.append(sym)
        return mapping, remaining

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
                with _cg_lock:
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
        mapping, unresolved = self._resolve_without_api(symbols, ext)
        if not unresolved:
            return mapping

        # Pass 2: bulk /coins/list for unambiguous symbols.
        self._ensure_coin_list()
        cache_hits, needs_search = self._resolve_without_api(unresolved)
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
            try:
                usd_price = item.get("usd")
                change_24h = item.get("usd_24h_change")
                if usd_price is None:
                    continue
                price = float(usd_price)
                for sym in id_to_syms.get(cg_id, []):
                    prices[sym] = price
                    if change_24h is not None:
                        prev_closes[sym] = price / (1 + float(change_24h) / 100)
            except (ValueError, TypeError):
                logger.warning(
                    "Could not parse price/change for CoinGecko ID %s",
                    cg_id,
                    exc_info=True,
                )
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
