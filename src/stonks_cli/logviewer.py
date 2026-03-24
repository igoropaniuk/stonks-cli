"""Log viewer screen -- shows the stonks log file inside the TUI."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, RichLog

from stonks_cli.log import LOG_FILE


class LogViewerScreen(Screen):
    """Full-screen log viewer; press R to refresh, Escape/Q to close."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Close"),
        Binding("q", "app.pop_screen", "Close"),
        Binding("r", "refresh_log", "Refresh"),
    ]

    CSS = """
    LogViewerScreen {
        background: $surface;
    }
    #log-title {
        padding: 0 1;
        text-style: bold;
        color: $accent;
    }
    #log-body {
        height: 1fr;
        border: solid $accent;
        padding: 0 1;
    }
    #log-empty {
        padding: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(f"  Log -- {LOG_FILE}", id="log-title")
        yield RichLog(id="log-body", highlight=True, markup=False, wrap=False)
        yield Label("", id="log-empty")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    def action_refresh_log(self) -> None:
        self.query_one("#log-body", RichLog).clear()
        self._load()

    def _load(self) -> None:
        log = self.query_one("#log-body", RichLog)
        empty = self.query_one("#log-empty", Label)
        try:
            if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
                empty.update(
                    "No log entries yet."
                    " Run with --log-level DEBUG to capture more detail."
                )
                return
            empty.update("")
            for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
                log.write(line)
        except OSError as exc:
            log.write(f"Error reading log file: {exc}")
        log.scroll_end(animate=False)
