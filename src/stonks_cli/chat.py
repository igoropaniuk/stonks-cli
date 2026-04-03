"""AI chat screen with RAG context from portfolio and news."""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

from openai import AsyncOpenAI
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Input, Label, Markdown, Static

if TYPE_CHECKING:
    from openai import AsyncStream
    from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

    from stonks_cli.market import MarketSnapshot
    from stonks_cli.models import Portfolio
    from stonks_cli.news_fetcher import NewsItem

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-5.4-mini"

_PROMPT_TEMPLATE_PATH = Path(__file__).with_name("data") / "chat_prompt.txt"


def _load_prompt_template() -> str:
    """Load the system prompt template from the data directory."""
    try:
        return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "Use the provided context to answer the user's question."


def _load_readme_context() -> str:
    """Load README.md so chat requests include project-level context."""
    import importlib.resources as pkg_resources

    try:
        pkg_path = Path(str(pkg_resources.files("stonks_cli")))
        for candidate in (pkg_path.parents[1], pkg_path.parents[0]):
            readme = candidate / "README.md"
            try:
                return readme.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def _build_portfolios_context(
    portfolios: list[Portfolio],
    snap: MarketSnapshot,
) -> str:
    """Build the portfolio section for the system prompt."""
    from stonks_cli.models import portfolio_total
    from stonks_cli.table_rows import RowKind, build_row_data

    parts: list[str] = []

    for i, portfolio in enumerate(portfolios):
        label = portfolio.name or f"Portfolio {i + 1}"
        parts.append(f"{label} ({portfolio.base_currency})")
        rates = snap.forex_rates.get(portfolio.base_currency, {})
        total = portfolio_total(portfolio, snap.prices, rates)
        if total is None:
            parts.append(
                "Portfolio total: unavailable because some market prices or FX rates are missing."  # noqa: E501
            )
        else:
            parts.append(f"Portfolio total: {total:,.2f} {portfolio.base_currency}")

        priced_positions: list[str] = []
        unavailable_positions: list[str] = []
        watchlist_items: list[str] = []
        cash_items: list[str] = []

        for row in build_row_data(
            portfolio,
            snap.prices,
            snap.sessions,
            snap.prev_closes,
            snap.exchange_codes,
            rates,
        ):
            if row.kind == RowKind.POSITION:
                assert row.qty is not None and row.avg_cost is not None
                if row.last is None:
                    unavailable_positions.append(
                        f"{row.symbol}: qty={row.qty:g}, avg_cost={row.avg_cost:.8g}, last_price unavailable"  # noqa: E501
                    )
                else:
                    mkt_value = (
                        "unavailable"
                        if row.mkt_value is None
                        else f"{row.mkt_value:,.2f}"
                    )
                    pnl = "unavailable" if row.pnl is None else f"{row.pnl:+,.2f}"
                    priced_positions.append(
                        f"{row.symbol}: qty={row.qty:g}, avg_cost={row.avg_cost:.8g}, last_price={row.last:.8g}, market_value={mkt_value}, pnl={pnl}"  # noqa: E501
                    )
            elif row.kind == RowKind.WATCHLIST:
                if row.last is None:
                    watchlist_items.append(f"{row.symbol}: last_price unavailable")
                else:
                    watchlist_items.append(f"{row.symbol}: last_price={row.last:.8g}")
            elif row.kind == RowKind.CASH:
                assert row.qty is not None
                if row.last is None:
                    cash_items.append(
                        f"{row.symbol} cash: amount={row.qty:,.2f}, FX rate unavailable"
                    )
                else:
                    value = (
                        "unavailable"
                        if row.mkt_value is None
                        else f"{row.mkt_value:,.2f}"
                    )
                    cash_items.append(
                        f"{row.symbol} cash: amount={row.qty:,.2f}, fx_rate={row.last:.4f}, value_in_{portfolio.base_currency}={value}"  # noqa: E501
                    )

        if priced_positions:
            parts.append("Positions with live prices:")
            parts.extend(priced_positions)
        if unavailable_positions:
            parts.append(
                "Positions without live prices right now. Do not treat these as having current market value:"  # noqa: E501
            )
            parts.extend(unavailable_positions)
        if watchlist_items:
            parts.append("Watchlist:")
            parts.extend(watchlist_items)
        if cash_items:
            parts.append("Cash:")
            parts.extend(cash_items)

        parts.append("")

    return "\n".join(parts).strip()


def _build_news_context(news_items: deque[NewsItem]) -> str:
    """Build the news section for the system prompt."""
    if not news_items:
        return "No recent news available."
    return "\n".join(
        f"{item.published_at}  {item.headline}  ({item.source})" for item in news_items
    )


def _validate_reply(reply: str) -> str | None:
    """Return an error string if the reply is unusable, otherwise None."""
    stripped = reply.strip()
    if not stripped:
        return "Empty response received from the model."
    return None


class ChatScreen(Screen):
    """Full-screen AI chat with portfolio + news RAG context."""

    class HistoryUpdated(Message):
        """Posted after each assistant reply so the parent can persist history."""

        def __init__(self, history: list[dict[str, str]]) -> None:
            super().__init__()
            self.history = history

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Close"),
    ]

    CSS = """
    ChatScreen {
        background: $surface;
    }
    #chat-title {
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }
    #chat-log {
        height: 1fr;
        border: solid $accent;
        padding: 0 1;
    }
    #chat-log .user-msg {
        color: $text;
        margin-top: 1;
    }
    #chat-log .assistant-header {
        color: $success;
        text-style: bold;
        margin-top: 1;
    }
    #chat-log .chat-error {
        color: $error;
        margin-top: 1;
    }
    #chat-input {
        margin-top: 1;
        border: solid $accent;
    }
    #chat-input:focus {
        border: solid $accent-lighten-2;
    }
    """

    def __init__(
        self,
        portfolios: list[Portfolio],
        snap_getter: Callable[[], MarketSnapshot],
        news_items: deque[NewsItem],
        history: list[dict[str, str]],
    ) -> None:
        super().__init__()
        self._portfolios = portfolios
        self._snap_getter = snap_getter
        self._news_items = news_items
        self._history: list[dict[str, str]] = list(history)
        self._readme_context = _load_readme_context()
        self._prompt_template = _load_prompt_template()
        self._client = AsyncOpenAI(api_key=self._api_key(), base_url=self._base_url())

    def compose(self) -> ComposeResult:
        yield Label("  AI Chat  (Esc to close)", id="chat-title")
        yield VerticalScroll(id="chat-log")
        yield Input(
            placeholder="Ask about your portfolio or recent news...", id="chat-input"
        )
        yield Footer()

    async def on_unmount(self) -> None:
        await self._client.close()

    def on_mount(self) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            log.mount(
                Static(
                    "OPENAI_API_KEY is not set. Export it and restart stonks.",
                    classes="chat-error",
                )
            )
            self.query_one("#chat-input", Input).disabled = True
            return
        if not self._snap_getter().prices:
            log.mount(
                Static(
                    "Waiting for live prices... please try again in a moment.",
                    classes="chat-error",
                )
            )
            self.query_one("#chat-input", Input).disabled = True
            return
        self._replay_history(log)
        self.query_one("#chat-input", Input).focus()

    def _replay_history(self, log: VerticalScroll) -> None:
        for msg in self._history:
            if msg["role"] == "user":
                log.mount(
                    Static(f"[bold cyan]You:[/] {msg['content']}", classes="user-msg")
                )
            elif msg["role"] == "assistant":
                log.mount(
                    Static("[bold green]Assistant:[/]", classes="assistant-header")
                )  # noqa: E501
                log.mount(Markdown(msg["content"].strip()))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.clear()
        self._send(prompt)

    def _send(self, prompt: str) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(Static(f"[bold cyan]You:[/] {prompt}", classes="user-msg"))
        self._history.append({"role": "user", "content": prompt})
        self._stream_response(list(self._history))

    @staticmethod
    def _api_key() -> str:
        return os.environ.get("OPENAI_API_KEY", "")

    @staticmethod
    def _model() -> str:
        return os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)

    @staticmethod
    def _base_url() -> str | None:
        return os.environ.get("OPENAI_BASE_URL") or None

    def _build_context_block(self, snap: MarketSnapshot) -> str:
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        return (
            f"=== CURRENT TIME ===\n{now}\n\n"
            "=== README ===\n"
            f"{self._readme_context or 'Not available.'}\n\n"
            "=== PORTFOLIO DATA ===\n"
            f"{_build_portfolios_context(self._portfolios, snap)}\n\n"
            "=== RECENT NEWS ===\n"
            f"{_build_news_context(self._news_items)}"
        )

    def _build_messages(
        self, history: list[dict[str, str]]
    ) -> list[ChatCompletionMessageParam]:
        snap = self._snap_getter()
        system_content = self._prompt_template
        logger.debug("Chat system prompt:\n%s", system_content)

        context_block = self._build_context_block(snap)
        # Inject context into the last user message so the grounding data
        # stays close to the current question and is less likely to be
        # evicted from the model's context window in long sessions.
        last_user_idx = max(
            (i for i, m in enumerate(history) if m["role"] == "user"), default=None
        )
        augmented: list[ChatCompletionMessageParam] = []
        for i, msg in enumerate(history):
            if i == last_user_idx:
                augmented_content = f"{context_block}\n\n---\n\n{msg['content']}"
                logger.debug("Chat user prompt:\n%s", augmented_content)
                augmented.append({"role": "user", "content": augmented_content})
            else:
                augmented.append({"role": msg["role"], "content": msg["content"]})  # type: ignore[arg-type,misc]

        system: ChatCompletionMessageParam = {
            "role": "system",
            "content": system_content,
        }
        return [system, *augmented]

    @work(thread=False, exclusive=True)
    async def _stream_response(self, history: list[dict[str, str]]) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        if not self._api_key():
            await log.mount(
                Static("[red]Error: OPENAI_API_KEY not set.[/]", classes="chat-error")
            )
            return

        header = Static("[bold green]Assistant:[/]", classes="assistant-header")
        md_widget = Markdown("")
        await log.mount(header)
        await log.mount(md_widget)
        log.scroll_end(animate=False)

        full_reply = ""
        try:
            stream = cast(
                "AsyncStream[ChatCompletionChunk]",
                await self._client.chat.completions.create(
                    model=self._model(),
                    messages=self._build_messages(history),
                    stream=True,
                ),
            )
            chunk_count = 0
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    full_reply += delta
                    chunk_count += 1
                    if chunk_count % 10 == 0:
                        md_widget.update(full_reply)
                        log.scroll_end(animate=False)
            # Final update to render any remaining buffered chunks
            if full_reply:
                md_widget.update(full_reply)
                log.scroll_end(animate=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chat request failed: %s", exc)
            await log.mount(Static(f"[red]Error: {exc}[/]", classes="chat-error"))
            return

        error = _validate_reply(full_reply)
        if error:
            logger.warning("Invalid reply from model: %s", error)
            await log.mount(Static(f"[red]Error: {error}[/]", classes="chat-error"))
            return

        self._history.append({"role": "assistant", "content": full_reply})
        self.post_message(self.HistoryUpdated(list(self._history)))
