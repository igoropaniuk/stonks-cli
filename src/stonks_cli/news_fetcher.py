"""yfinance-backed news fetcher for the ``feed`` command."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import yfinance as yf


@dataclass
class NewsItem:
    headline: str
    source: str
    published_at: str  # formatted local time
    url: str
    timestamp: int = field(default=0, compare=False)


class NewsFetcher:
    """Fetches the latest company news for a symbol via yfinance."""

    _MAX_ITEMS = 10

    @staticmethod
    def _parse_item(raw: dict) -> NewsItem | None:
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
                published = dt.astimezone().strftime("%b %d, %Y  %H:%M")
            except (ValueError, OSError):
                pass
        return NewsItem(
            headline=headline,
            source=source,
            published_at=published,
            url=url,
            timestamp=ts,
        )

    def fetch(self, symbol: str, limit: int | None = None) -> list[NewsItem]:
        limit = self._MAX_ITEMS if limit is None else limit
        raw_list = yf.Ticker(symbol.upper()).news or []
        seen_urls: set[str] = set()
        items: list[NewsItem] = []
        for raw in raw_list:
            item = self._parse_item(raw)
            if item is None:
                continue
            if item.url:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
            items.append(item)
        items.sort(key=lambda x: x.timestamp, reverse=True)
        return items[:limit]

    def fetch_for_symbols(
        self, symbols: list[str], max_items: int = 10
    ) -> list[NewsItem]:
        """Fetch, deduplicate, and sort news across all symbols.

        Returns the *max_items* most recent unique articles sorted by
        timestamp descending.
        """
        unique_symbols = sorted({s.upper() for s in symbols})
        seen_urls: set[str] = set()
        all_items: list[NewsItem] = []

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self.fetch, sym, max_items): sym
                for sym in unique_symbols
            }
            for future in as_completed(futures):
                for item in future.result():
                    if item.url and item.url not in seen_urls:
                        seen_urls.add(item.url)
                        all_items.append(item)

        all_items.sort(key=lambda x: x.timestamp, reverse=True)
        return all_items[:max_items]
