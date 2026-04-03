"""Tests for stonks_cli.chat -- module-level functions and ChatScreen TUI."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult

from stonks_cli.chat import (
    ChatScreen,
    _build_news_context,
    _build_portfolios_context,
    _load_prompt_template,
    _load_readme_context,
    _validate_reply,
)
from stonks_cli.market import MarketSnapshot
from stonks_cli.models import CashPosition, Portfolio, Position, WatchlistItem
from stonks_cli.news_fetcher import NewsItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USD_RATES = {"USD": {"USD": 1.0}}


def _make_snap(prices: dict[str, float] | None = None) -> MarketSnapshot:
    return MarketSnapshot(prices=prices or {}, forex_rates=USD_RATES)


def _make_news_item(headline: str = "Headline", source: str = "Reuters") -> NewsItem:
    return NewsItem(
        headline=headline,
        source=source,
        published_at="2026-04-01 10:00",
        url="https://example.com",
    )


async def _make_chat_app(
    portfolios: list[Portfolio],
    snap: MarketSnapshot,
    news_items: deque | None = None,
    history: list[dict[str, str]] | None = None,
) -> App:
    news_items = news_items if news_items is not None else deque()
    history = history if history is not None else []
    snap_obj = snap

    class _App(App):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(
                ChatScreen(
                    portfolios,
                    lambda: snap_obj,
                    news_items,
                    history,
                )
            )

    return _App()


# ===========================================================================
# Module-level pure-function tests
# ===========================================================================


class TestLoadPromptTemplate:
    def test_returns_file_content_when_exists(self, tmp_path):
        content = "You are a helpful assistant."
        template_file = tmp_path / "chat_prompt.txt"
        template_file.write_text(content, encoding="utf-8")

        with patch("stonks_cli.chat._PROMPT_TEMPLATE_PATH", template_file):
            result = _load_prompt_template()

        assert result == content

    def test_returns_fallback_on_os_error(self, tmp_path):
        missing_file = tmp_path / "does_not_exist.txt"
        with patch("stonks_cli.chat._PROMPT_TEMPLATE_PATH", missing_file):
            result = _load_prompt_template()

        assert result  # non-empty fallback
        lower = result.lower()
        assert "answer" in lower or "question" in lower or "context" in lower


class TestLoadReadmeContext:
    def test_returns_content_when_readme_found(self):
        """Returns stripped README content when one of the candidate paths succeeds."""
        # Patch Path so __file__ resolves to a mock with deep enough .parents and
        # a successful read_text on the first / "README.md" candidate.
        mock_path_instance = MagicMock(spec=Path)
        mock_parent = MagicMock(spec=Path)
        mock_path_instance.resolve.return_value = mock_path_instance
        # parents[2] and parents[1] are both accessed; provide at least 3 entries.
        mock_path_instance.parents = [mock_parent, mock_parent, mock_parent]
        readme_mock = MagicMock(spec=Path)
        readme_mock.read_text.return_value = "  # stonks-cli  "
        mock_parent.__truediv__ = MagicMock(return_value=readme_mock)

        with patch("stonks_cli.chat.Path", return_value=mock_path_instance):
            result = _load_readme_context()

        assert "stonks-cli" in result

    def test_returns_empty_string_when_all_paths_raise(self):
        """Returns '' when every candidate path raises OSError."""
        mock_path_instance = MagicMock(spec=Path)
        mock_parent = MagicMock(spec=Path)
        mock_path_instance.resolve.return_value = mock_path_instance
        mock_path_instance.parents = [mock_parent, mock_parent, mock_parent]
        readme_mock = MagicMock(spec=Path)
        readme_mock.read_text.side_effect = OSError("not found")
        mock_parent.__truediv__ = MagicMock(return_value=readme_mock)

        with patch("stonks_cli.chat.Path", return_value=mock_path_instance):
            result = _load_readme_context()

        assert result == ""


class TestBuildPortfoliosContext:
    def _simple_portfolio(self) -> Portfolio:
        return Portfolio(
            positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)],
            name="My Portfolio",
        )

    def test_contains_symbol_qty_avg_cost_last_price(self):
        p = self._simple_portfolio()
        snap = _make_snap({"AAPL": 175.0})
        result = _build_portfolios_context([p], snap)

        assert "AAPL" in result
        assert "10" in result  # qty
        assert "150" in result  # avg_cost
        assert "175" in result  # last_price

    def test_no_prices_shows_unavailable(self):
        p = self._simple_portfolio()
        snap = _make_snap({})
        result = _build_portfolios_context([p], snap)

        assert "AAPL" in result
        assert "unavailable" in result

    def test_watchlist_item_appears(self):
        p = Portfolio(
            watchlist=[WatchlistItem(symbol="TSLA")],
        )
        snap = _make_snap({"TSLA": 200.0})
        result = _build_portfolios_context([p], snap)

        assert "TSLA" in result

    def test_cash_position_appears(self):
        p = Portfolio(
            cash=[CashPosition(currency="EUR", amount=1000.0)],
            base_currency="USD",
        )
        snap = MarketSnapshot(
            prices={"EUR": 1.1},
            forex_rates={"USD": {"USD": 1.0, "EUR": 1.1}},
        )
        result = _build_portfolios_context([p], snap)

        # EUR cash should appear somewhere
        assert "EUR" in result.upper()

    def test_empty_portfolio_returns_empty_or_header(self):
        """Empty portfolio -- header only or empty, no position data."""
        p = Portfolio()
        snap = _make_snap({})
        result = _build_portfolios_context([p], snap)
        # no positions data
        assert "AAPL" not in result and "unavailable" not in result


class TestBuildNewsContext:
    def test_empty_deque_returns_no_news_message(self):
        result = _build_news_context(deque())
        assert result == "No recent news available."

    def test_formats_news_items(self):
        items = deque(
            [
                _make_news_item("AAPL hits record high", "Bloomberg"),
                _make_news_item("Fed raises rates", "Reuters"),
            ]
        )
        result = _build_news_context(items)

        assert "AAPL hits record high" in result
        assert "Bloomberg" in result
        assert "Fed raises rates" in result
        assert "Reuters" in result
        assert "2026-04-01 10:00" in result


class TestValidateReply:
    def test_empty_string_returns_error(self):
        result = _validate_reply("")
        assert result is not None
        assert len(result) > 0

    def test_whitespace_only_returns_error(self):
        result = _validate_reply("   \n\t  ")
        assert result is not None
        assert len(result) > 0

    def test_valid_reply_returns_none(self):
        result = _validate_reply("Here is the answer.")
        assert result is None

    def test_valid_reply_with_whitespace_returns_none(self):
        result = _validate_reply("  Some content here  ")
        assert result is None


# ===========================================================================
# ChatScreen static method tests
# ===========================================================================


class TestStaticMethods:
    def test_api_key_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        assert ChatScreen._api_key() == "sk-test-key"

    def test_api_key_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert ChatScreen._api_key() == ""

    def test_model_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        assert ChatScreen._model() == "gpt-4o"

    def test_model_defaults_to_default_model(self, monkeypatch):
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        from stonks_cli.chat import _DEFAULT_MODEL

        assert ChatScreen._model() == _DEFAULT_MODEL

    def test_base_url_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://my-proxy.example.com")
        assert ChatScreen._base_url() == "https://my-proxy.example.com"

    def test_base_url_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        assert ChatScreen._base_url() is None

    def test_base_url_returns_none_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "")
        assert ChatScreen._base_url() is None


# ===========================================================================
# ChatScreen -- _build_messages (pure, no Textual)
# ===========================================================================


class TestBuildMessages:
    def _make_screen(self, history=None) -> ChatScreen:
        p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
        snap = _make_snap({"AAPL": 175.0})
        screen = ChatScreen.__new__(ChatScreen)
        screen._portfolios = [p]
        screen._snap_getter = lambda: snap
        screen._news_items = deque()
        screen._history = history or []
        screen._readme_context = ""
        screen._prompt_template = "Be helpful."
        return screen

    def test_system_message_is_first(self):
        screen = self._make_screen()
        msgs = screen._build_messages([{"role": "user", "content": "Hello"}])
        assert msgs[0]["role"] == "system"

    def test_context_injected_into_last_user_message(self):
        screen = self._make_screen()
        msgs = screen._build_messages([{"role": "user", "content": "What is my PnL?"}])
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs, "Expected at least one user message"
        last_content = user_msgs[-1]["content"]
        assert "PORTFOLIO" in last_content or "AAPL" in last_content
        assert "What is my PnL?" in last_content

    def test_context_injected_into_last_not_first_user_message(self):
        screen = self._make_screen()
        history = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        msgs = screen._build_messages(history)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert len(user_msgs) == 2
        # First user message should be unmodified
        assert user_msgs[0]["content"] == "First question"
        # Last user message should have context injected
        assert (
            "PORTFOLIO" in user_msgs[1]["content"] or "AAPL" in user_msgs[1]["content"]
        )
        assert "Second question" in user_msgs[1]["content"]

    def test_works_with_empty_history(self):
        screen = self._make_screen()
        msgs = screen._build_messages([])
        assert msgs[0]["role"] == "system"
        # Only system message when no history
        assert len(msgs) == 1


# ===========================================================================
# ChatScreen TUI tests
# ===========================================================================


@pytest.mark.asyncio
async def test_on_mount_no_api_key_disables_input(monkeypatch):
    """Without OPENAI_API_KEY the input is disabled."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        inp = screen.query_one("#chat-input")
        assert inp.disabled


@pytest.mark.asyncio
async def test_on_mount_with_api_key_but_no_prices_disables_input(monkeypatch):
    """With API key but empty prices, input is disabled."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({})  # no prices
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        inp = screen.query_one("#chat-input")
        assert inp.disabled


@pytest.mark.asyncio
async def test_on_mount_with_api_key_and_prices_enables_input(monkeypatch):
    """With API key and live prices, input is enabled."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        inp = screen.query_one("#chat-input")
        assert not inp.disabled


@pytest.mark.asyncio
async def test_on_mount_replays_history(monkeypatch):
    """API key + prices present: existing history replayed into the log."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    history = [
        {"role": "user", "content": "How is my portfolio?"},
        {"role": "assistant", "content": "It looks great!"},
    ]

    with patch.object(ChatScreen, "_replay_history") as mock_replay:
        app = await _make_chat_app([p], snap, history=history)
        async with app.run_test() as pilot:
            await pilot.pause()
            mock_replay.assert_called_once()


@pytest.mark.asyncio
async def test_replay_history_user_message(monkeypatch):
    """User messages in history are written to the log."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    history = [{"role": "user", "content": "Hello there"}]

    app = await _make_chat_app([p], snap, history=history)
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.containers import VerticalScroll

        log = app.screen.query_one("#chat-log", VerticalScroll)
        # The log.lines list should have entries after replay
        assert len(list(log.children)) > 0


@pytest.mark.asyncio
async def test_replay_history_assistant_message(monkeypatch):
    """Assistant messages in history are written to the log."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    history = [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]

    app = await _make_chat_app([p], snap, history=history)
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.containers import VerticalScroll

        log = app.screen.query_one("#chat-log", VerticalScroll)
        assert len(list(log.children)) > 0


@pytest.mark.asyncio
async def test_on_input_submitted_empty_prompt_does_not_send(monkeypatch):
    """Submitting an empty prompt does not call _send."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_send") as mock_send:
            from textual.widgets import Input

            inp = screen.query_one("#chat-input", Input)
            inp.focus()
            await pilot.pause()
            # Submit with no text entered
            await pilot.press("enter")
            await pilot.pause()
            mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_on_input_submitted_valid_prompt_calls_send(monkeypatch):
    """Submitting a valid prompt calls _send with the trimmed text."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_send") as mock_send:
            from textual.widgets import Input

            inp = screen.query_one("#chat-input", Input)
            inp.focus()
            await pilot.pause()
            # Set value directly and submit via enter key
            inp.value = "What is my PnL?"
            await pilot.press("enter")
            await pilot.pause()
            mock_send.assert_called_once_with("What is my PnL?")


@pytest.mark.asyncio
async def test_send_appends_to_history_and_writes_log(monkeypatch):
    """_send appends user message to history and writes to log."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    history: list[dict[str, str]] = []
    app = await _make_chat_app([p], snap, history=history)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        with patch.object(screen, "_stream_response"):
            screen._send("Tell me about AAPL")
            await pilot.pause()

        assert any(
            m["role"] == "user" and m["content"] == "Tell me about AAPL"
            for m in screen._history
        )
        from textual.containers import VerticalScroll

        log = screen.query_one("#chat-log", VerticalScroll)
        assert len(list(log.children)) > 0


# ---------------------------------------------------------------------------
# _stream_response tests -- call __wrapped__ directly to bypass @work scheduling
# ---------------------------------------------------------------------------


def _make_mock_openai_client(stream_gen):
    """Build an AsyncOpenAI-shaped mock that yields from *stream_gen* when called."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=stream_gen)
    return mock_client


@pytest.mark.asyncio
async def test_stream_response_no_api_key_writes_error(monkeypatch):
    """_stream_response writes an error when OPENAI_API_KEY is not set."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        from textual.containers import VerticalScroll

        log = screen.query_one("#chat-log", VerticalScroll)
        lines_before = len(list(log.children))

        await ChatScreen._stream_response.__wrapped__(
            screen, [{"role": "user", "content": "Hi"}]
        )
        await pilot.pause()
        assert len(list(log.children)) > lines_before


@pytest.mark.asyncio
async def test_stream_response_openai_exception_writes_error(monkeypatch):
    """_stream_response writes an error when OpenAI raises an exception."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        from textual.containers import VerticalScroll

        log = screen.query_one("#chat-log", VerticalScroll)
        lines_before = len(list(log.children))

        screen._client = MagicMock()
        screen._client.close = AsyncMock()
        screen._client.chat = MagicMock()
        screen._client.chat.completions = MagicMock()
        screen._client.chat.completions.create = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        await ChatScreen._stream_response.__wrapped__(
            screen, [{"role": "user", "content": "Hi"}]
        )
        await pilot.pause()
        assert len(list(log.children)) > lines_before


@pytest.mark.asyncio
async def test_stream_response_empty_reply_writes_error(monkeypatch):
    """_stream_response writes an error when the model returns an empty reply."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        from textual.containers import VerticalScroll

        log = screen.query_one("#chat-log", VerticalScroll)
        lines_before = len(list(log.children))

        async def _empty_stream():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = None
            yield chunk

        screen._client = _make_mock_openai_client(_empty_stream())

        await ChatScreen._stream_response.__wrapped__(
            screen, [{"role": "user", "content": "Hi"}]
        )
        await pilot.pause()
        assert len(list(log.children)) > lines_before


@pytest.mark.asyncio
async def test_stream_response_success_appends_to_history(monkeypatch):
    """_stream_response appends assistant reply to _history on success."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    history: list[dict[str, str]] = [{"role": "user", "content": "Hello"}]
    app = await _make_chat_app([p], snap, history=history)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        async def _good_stream():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "Hello back!"
            yield chunk

        screen._client = _make_mock_openai_client(_good_stream())

        await ChatScreen._stream_response.__wrapped__(screen, list(screen._history))
        await pilot.pause()

        assistant_msgs = [m for m in screen._history if m["role"] == "assistant"]
        assert any("Hello back!" in m["content"] for m in assistant_msgs)


@pytest.mark.asyncio
async def test_stream_response_success_writes_to_log(monkeypatch):
    """_stream_response writes to log on success."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    p = Portfolio(positions=[Position(symbol="AAPL", quantity=10, avg_cost=150.0)])
    snap = _make_snap({"AAPL": 175.0})
    app = await _make_chat_app([p], snap)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        from textual.containers import VerticalScroll

        log = screen.query_one("#chat-log", VerticalScroll)
        lines_before = len(list(log.children))

        async def _good_stream():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "The answer is 42."
            yield chunk

        screen._client = _make_mock_openai_client(_good_stream())

        await ChatScreen._stream_response.__wrapped__(
            screen, [{"role": "user", "content": "What is 6*7?"}]
        )
        await pilot.pause()
        assert len(list(log.children)) > lines_before
