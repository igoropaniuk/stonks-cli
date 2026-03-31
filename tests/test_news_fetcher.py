"""Unit tests for NewsFetcher."""

from unittest.mock import patch

from stonks_cli.news_fetcher import NewsFetcher, NewsItem

_AAPL_ITEMS = [
    NewsItem(
        "Apple hits high", "Reuters", "Mar 31, 2026  09:00", "https://r.com/1", 1000
    ),
    NewsItem(
        "Apple Q1 results", "Bloomberg", "Mar 30, 2026  14:00", "https://b.com/2", 900
    ),
]
_NVDA_ITEMS = [
    NewsItem(
        "Nvidia beats estimates", "CNBC", "Mar 31, 2026  10:00", "https://c.com/3", 1100
    ),
    # duplicate URL -- should be deduplicated
    NewsItem(
        "Apple hits high", "Reuters", "Mar 31, 2026  09:00", "https://r.com/1", 1000
    ),
]

_RAW_ARTICLE = {
    "content": {
        "title": "Apple hits all-time high",
        "pubDate": "2026-03-31T09:00:00Z",
        "provider": {"displayName": "Reuters"},
        "canonicalUrl": {"url": "https://reuters.com/1"},
    }
}


def _make_fetcher(symbol_map: dict) -> NewsFetcher:
    fetcher = NewsFetcher()
    fetcher.fetch = lambda sym, limit=None: symbol_map.get(sym.upper(), [])  # type: ignore[method-assign]
    return fetcher


class TestParseItem:
    def test_parses_headline(self):
        item = NewsFetcher._parse_item(_RAW_ARTICLE)
        assert item is not None
        assert item.headline == "Apple hits all-time high"

    def test_parses_source(self):
        item = NewsFetcher._parse_item(_RAW_ARTICLE)
        assert item is not None
        assert item.source == "Reuters"

    def test_parses_url(self):
        item = NewsFetcher._parse_item(_RAW_ARTICLE)
        assert item is not None
        assert item.url == "https://reuters.com/1"

    def test_parses_timestamp(self):
        item = NewsFetcher._parse_item(_RAW_ARTICLE)
        assert item is not None
        assert item.timestamp > 0

    def test_parses_published_at_format(self):
        item = NewsFetcher._parse_item(_RAW_ARTICLE)
        assert item is not None
        # Should not be the fallback "N/A"
        assert item.published_at != "N/A"

    def test_returns_none_for_missing_title(self):
        assert NewsFetcher._parse_item({"content": {}}) is None

    def test_returns_none_for_missing_content(self):
        assert NewsFetcher._parse_item({}) is None

    def test_returns_none_for_non_dict_content(self):
        assert NewsFetcher._parse_item({"content": "not a dict"}) is None

    def test_returns_none_for_empty_title(self):
        raw = {"content": {"title": "   "}}
        assert NewsFetcher._parse_item(raw) is None

    def test_falls_back_to_click_through_url(self):
        raw = {
            "content": {
                "title": "Test",
                "pubDate": "2026-03-31T09:00:00Z",
                "provider": {"displayName": "X"},
                "clickThroughUrl": {"url": "https://click.com/1"},
            }
        }
        item = NewsFetcher._parse_item(raw)
        assert item is not None
        assert item.url == "https://click.com/1"

    def test_handles_missing_pub_date(self):
        raw = {
            "content": {
                "title": "No date article",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://reuters.com/1"},
            }
        }
        item = NewsFetcher._parse_item(raw)
        assert item is not None
        assert item.published_at == "N/A"
        assert item.timestamp == 0

    def test_handles_invalid_pub_date(self):
        raw = {
            "content": {
                "title": "Bad date",
                "pubDate": "not-a-date",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://reuters.com/1"},
            }
        }
        item = NewsFetcher._parse_item(raw)
        assert item is not None
        assert item.published_at == "N/A"
        assert item.timestamp == 0

    def test_handles_missing_provider(self):
        raw = {
            "content": {
                "title": "No source",
                "pubDate": "2026-03-31T09:00:00Z",
                "canonicalUrl": {"url": "https://reuters.com/1"},
            }
        }
        item = NewsFetcher._parse_item(raw)
        assert item is not None
        assert item.source == ""

    def test_handles_none_canonical_url(self):
        raw = {
            "content": {
                "title": "No URL",
                "pubDate": "2026-03-31T09:00:00Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": None,
                "clickThroughUrl": None,
            }
        }
        item = NewsFetcher._parse_item(raw)
        assert item is not None
        assert item.url == ""


class TestFetch:
    def test_calls_yfinance(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = [_RAW_ARTICLE]
            fetcher = NewsFetcher()
            items = fetcher.fetch("AAPL")
        mock_ticker_cls.assert_called_once_with("AAPL")
        assert len(items) == 1

    def test_handles_empty_news(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = []
            fetcher = NewsFetcher()
            items = fetcher.fetch("AAPL")
        assert items == []

    def test_handles_none_news(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = None
            fetcher = NewsFetcher()
            items = fetcher.fetch("AAPL")
        assert items == []

    def test_skips_unparseable_items(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = [{"content": {}}, _RAW_ARTICLE]
            fetcher = NewsFetcher()
            items = fetcher.fetch("AAPL")
        assert len(items) == 1
        assert items[0].headline == "Apple hits all-time high"

    def test_uppercases_symbol(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = []
            fetcher = NewsFetcher()
            fetcher.fetch("aapl")
        mock_ticker_cls.assert_called_once_with("AAPL")

    def test_sorted_by_timestamp_descending(self):
        older = {
            "content": {
                "title": "Older article",
                "pubDate": "2026-03-30T09:00:00Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://reuters.com/old"},
            }
        }
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = [older, _RAW_ARTICLE]
            items = NewsFetcher().fetch("AAPL")
        assert items[0].headline == "Apple hits all-time high"
        assert items[1].headline == "Older article"

    def test_respects_default_limit(self):
        many = [
            {
                "content": {
                    "title": f"Article {i}",
                    "pubDate": "2026-03-31T09:00:00Z",
                    "provider": {"displayName": "Reuters"},
                    "canonicalUrl": {"url": f"https://reuters.com/{i}"},
                }
            }
            for i in range(15)
        ]
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = many
            items = NewsFetcher().fetch("AAPL")
        assert len(items) <= NewsFetcher._MAX_ITEMS

    def test_custom_limit(self):
        many = [
            {
                "content": {
                    "title": f"Article {i}",
                    "pubDate": "2026-03-31T09:00:00Z",
                    "provider": {"displayName": "Reuters"},
                    "canonicalUrl": {"url": f"https://reuters.com/{i}"},
                }
            }
            for i in range(15)
        ]
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = many
            items = NewsFetcher().fetch("AAPL", limit=3)
        assert len(items) == 3

    def test_parses_all_items_before_slicing(self):
        """Invalid items should not consume the limit budget."""
        invalid = {"content": {}}
        valid = _RAW_ARTICLE
        with patch("yfinance.Ticker") as mock_ticker_cls:
            # 4 invalid + 1 valid; with limit=1 we should still get 1
            mock_ticker_cls.return_value.news = [
                invalid,
                invalid,
                invalid,
                invalid,
                valid,
            ]
            items = NewsFetcher().fetch("AAPL", limit=1)
        assert len(items) == 1
        assert items[0].headline == "Apple hits all-time high"

    def test_deduplicates_by_url(self):
        duplicate = dict(_RAW_ARTICLE)
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = [_RAW_ARTICLE, duplicate]
            items = NewsFetcher().fetch("AAPL")
        assert len(items) == 1

    def test_zero_limit_returns_empty(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker_cls.return_value.news = [_RAW_ARTICLE]
            items = NewsFetcher().fetch("AAPL", limit=0)
        assert items == []


class TestFetchForSymbols:
    def test_returns_merged_items(self):
        fetcher = _make_fetcher({"AAPL": _AAPL_ITEMS, "NVDA": _NVDA_ITEMS})
        items = fetcher.fetch_for_symbols(["AAPL", "NVDA"])
        headlines = [i.headline for i in items]
        assert "Apple hits high" in headlines
        assert "Nvidia beats estimates" in headlines

    def test_deduplicates_by_url(self):
        fetcher = _make_fetcher({"AAPL": _AAPL_ITEMS, "NVDA": _NVDA_ITEMS})
        items = fetcher.fetch_for_symbols(["AAPL", "NVDA"])
        urls = [i.url for i in items]
        assert len(urls) == len(set(urls))

    def test_sorted_by_timestamp_descending(self):
        fetcher = _make_fetcher({"AAPL": _AAPL_ITEMS, "NVDA": _NVDA_ITEMS})
        items = fetcher.fetch_for_symbols(["AAPL", "NVDA"])
        timestamps = [i.timestamp for i in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_respects_max_items(self):
        fetcher = _make_fetcher({"AAPL": _AAPL_ITEMS, "NVDA": _NVDA_ITEMS})
        items = fetcher.fetch_for_symbols(["AAPL", "NVDA"], max_items=2)
        assert len(items) <= 2

    def test_fetches_all_symbols(self):
        import threading

        calls: list[str] = []
        lock = threading.Lock()
        fetcher = _make_fetcher({})

        def _fetch(sym, limit=None):  # type: ignore[misc]
            with lock:
                calls.append(sym)
            return []

        fetcher.fetch = _fetch  # type: ignore[method-assign]
        fetcher.fetch_for_symbols(["A", "B", "C", "D", "E", "F"])
        assert set(calls) == {"A", "B", "C", "D", "E", "F"}

    def test_empty_symbols_returns_empty(self):
        fetcher = _make_fetcher({})
        assert fetcher.fetch_for_symbols([]) == []

    def test_skips_items_without_url(self):
        no_url = NewsItem("No URL article", "Reuters", "Mar 31, 2026  09:00", "", 500)
        fetcher = _make_fetcher({"AAPL": [no_url, *_AAPL_ITEMS]})
        items = fetcher.fetch_for_symbols(["AAPL"])
        assert all(i.url for i in items)
