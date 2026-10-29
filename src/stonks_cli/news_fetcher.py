"""yfinance-backed news fetcher for the ``feed`` command."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    headline: str
    source: str
    published_at: str  # formatted local time
    url: str
    timestamp: int = field(default=0, compare=False)
    symbol: str = field(default="", compare=False)


class NewsFetcher:
    """Fetches the latest company news for a symbol via yfinance."""

    _MAX_ITEMS = 10
    _MAX_ITEM_AGE = timedelta(days=3)
    _SEARCH_NEWS_COUNT = 20

    @classmethod
    def _is_recent(cls, item: "NewsItem") -> bool:
        """Return True when the article timestamp is recent enough to display."""
        if item.timestamp <= 0:
            return False
        cutoff = datetime.now().astimezone() - cls._MAX_ITEM_AGE
        return item.timestamp >= int(cutoff.timestamp())

    @staticmethod
    def _parse_item(raw: dict) -> "NewsItem | None":
        content = raw.get("content", {})
        if not isinstance(content, dict):
            return None
        headline = (content.get("title") or "").strip()
        if not headline:
            return None
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or ""
        ).strip()
        source = ((content.get("provider") or {}).get("displayName") or "").strip()
        pub_date = content.get("pubDate") or ""
        ts = 0
        published = "N/A"
        if pub_date:
            try:
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
                published = dt.astimezone().strftime("%b %d %H:%M")
            except (ValueError, OSError):
                pass
        return NewsItem(
            headline=headline,
            source=source,
            published_at=published,
            url=url,
            timestamp=ts,
        )

    @staticmethod
    def _parse_search_item(raw: dict) -> "NewsItem | None":
        """Parse an article from ``yf.Search().news``, a flat dict."""
        headline = (raw.get("title") or "").strip()
        if not headline:
            return None
        ts = raw.get("providerPublishTime")
        published = "N/A"
        if isinstance(ts, int) and ts > 0:
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                published = dt.astimezone().strftime("%b %d %H:%M")
            except (ValueError, OSError, OverflowError):
                ts = 0
        else:
            ts = 0
        return NewsItem(
            headline=headline,
            source=(raw.get("publisher") or "").strip(),
            published_at=published,
            url=(raw.get("link") or "").strip(),
            timestamp=ts,
        )

    def _search_news(self, symbol: str) -> list["NewsItem | None"]:
        """Fall back to Yahoo's search API when ``Ticker.news`` is empty.

        Yahoo retired the endpoint behind ``Ticker.news`` in October 2026
        (yfinance issue #3004) and it now always returns nothing.  Search
        still returns news, but also articles that only mention the query
        text, so keep the ones Yahoo tags with *symbol*.
        """
        try:
            raw_list = (
                yf.Search(
                    symbol,
                    max_results=0,
                    news_count=self._SEARCH_NEWS_COUNT,
                    lists_count=0,
                    include_cb=False,
                    recommended=0,
                ).news
                or []
            )
        except Exception:
            logger.warning("News search for %s failed", symbol, exc_info=True)
            return []
        return [
            self._parse_search_item(raw)
            for raw in raw_list
            if symbol in (raw.get("relatedTickers") or [])
        ]

    def fetch(self, symbol: str, limit: int | None = None) -> list["NewsItem"]:
        limit = self._MAX_ITEMS if limit is None else limit
        symbol = symbol.upper()
        parsed = [self._parse_item(raw) for raw in yf.Ticker(symbol).news or []]
        if not any(parsed):
            parsed = self._search_news(symbol)
        seen_keys: set[str] = set()
        items: list[NewsItem] = []
        for item in parsed:
            if item is None:
                continue
            if not self._is_recent(item):
                continue
            key = item.url or f"{item.timestamp}:{item.headline}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            item.symbol = symbol
            items.append(item)
        items.sort(key=lambda x: x.timestamp, reverse=True)
        return items[:limit]

    def fetch_for_symbols(
        self, symbols: list[str], max_items: int = 10
    ) -> list["NewsItem"]:
        """Fetch, deduplicate, and sort news across all symbols.

        Returns the *max_items* most recent unique articles sorted by
        timestamp descending.
        """
        unique_symbols = sorted({s.upper() for s in symbols})
        seen_keys: set[str] = set()
        all_items: list[NewsItem] = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.fetch, sym, max_items): sym
                for sym in unique_symbols
            }
            for future in as_completed(futures):
                for item in future.result():
                    key = item.url or f"{item.timestamp}:{item.headline}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_items.append(item)

        all_items.sort(key=lambda x: x.timestamp, reverse=True)
        return all_items[:max_items]
