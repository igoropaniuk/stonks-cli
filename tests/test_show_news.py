"""Unit tests for show_news.format_news and format_news_panel."""

from stonks_cli.news_fetcher import NewsItem
from stonks_cli.show_news import format_news, format_news_panel

_ITEMS = [
    NewsItem(
        headline="Apple hits all-time high",
        source="Reuters",
        published_at="Mar 31, 2026  09:00",
        url="https://reuters.com/1",
    ),
    NewsItem(
        headline="iPhone sales beat estimates",
        source="Bloomberg",
        published_at="Mar 30, 2026  14:30",
        url="https://bloomberg.com/2",
    ),
]


class TestFormatNews:
    def test_header_contains_symbol(self):
        out = format_news("aapl", _ITEMS)
        assert "AAPL" in out

    def test_empty_items_returns_no_news_message(self):
        out = format_news("AAPL", [])
        assert "No recent news" in out
        assert "AAPL" in out

    def test_multiple_items_shown(self):
        out = format_news("AAPL", _ITEMS)
        assert "Apple hits all-time high" in out
        assert "iPhone sales beat estimates" in out

    def test_headlines_shown(self):
        out = format_news("AAPL", _ITEMS)
        assert "Apple hits all-time high" in out
        assert "iPhone sales beat estimates" in out

    def test_source_shown(self):
        out = format_news("AAPL", _ITEMS)
        assert "Reuters" in out
        assert "Bloomberg" in out

    def test_published_at_shown(self):
        out = format_news("AAPL", _ITEMS)
        assert "Mar 31, 2026  09:00" in out

    def test_url_shown(self):
        out = format_news("AAPL", _ITEMS)
        assert "https://reuters.com/1" in out
        assert "https://bloomberg.com/2" in out


class TestFormatNewsPanel:
    def test_header_shown(self):
        out = format_news_panel(_ITEMS)
        assert "News" in out

    def test_empty_items_returns_no_news_message(self):
        out = format_news_panel([])
        assert "No recent news" in out

    def test_headlines_shown(self):
        out = format_news_panel(_ITEMS)
        assert "Apple hits all-time high" in out
        assert "iPhone sales beat estimates" in out

    def test_source_and_time_shown(self):
        out = format_news_panel(_ITEMS)
        assert "Reuters" in out
        assert "Mar 31, 2026  09:00" in out

    def test_url_shown(self):
        out = format_news_panel(_ITEMS)
        assert "https://reuters.com/1" in out
