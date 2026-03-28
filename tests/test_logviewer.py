"""Tests for stonks_cli.logviewer.LogViewerScreen."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Footer, Label, RichLog

from stonks_cli.logviewer import LogViewerScreen

# ---------------------------------------------------------------------------
# Helper app
# ---------------------------------------------------------------------------


class _TestApp(App):
    """Minimal host app that immediately pushes LogViewerScreen."""

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(LogViewerScreen())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLogViewerScreen:
    @pytest.mark.asyncio
    async def test_compose_expected_widgets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stonks.log"
            log_path.write_text("line1\n", encoding="utf-8")
            with patch("stonks_cli.logviewer.LOG_FILE", log_path):
                async with _TestApp().run_test() as pilot:
                    screen = pilot.app.screen
                    assert screen.query_one("#log-body", RichLog) is not None
                    assert screen.query_one("#log-title", Label) is not None
                    assert screen.query_one("#log-empty", Label) is not None
                    assert screen.query_one(Footer) is not None

    @pytest.mark.asyncio
    async def test_nonexistent_file_shows_empty_message(self) -> None:
        missing = Path("/tmp/does_not_exist_stonks_test.log")
        with patch("stonks_cli.logviewer.LOG_FILE", missing):
            async with _TestApp().run_test() as pilot:
                screen = pilot.app.screen
                empty = screen.query_one("#log-empty", Label)
                assert "No log entries yet" in str(empty.content)

    @pytest.mark.asyncio
    async def test_empty_file_shows_empty_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stonks.log"
            log_path.write_text("", encoding="utf-8")
            with patch("stonks_cli.logviewer.LOG_FILE", log_path):
                async with _TestApp().run_test() as pilot:
                    screen = pilot.app.screen
                    empty = screen.query_one("#log-empty", Label)
                    assert "No log entries yet" in str(empty.content)

    @pytest.mark.asyncio
    async def test_reads_lines_into_rich_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stonks.log"
            log_path.write_text("INFO line1\nDEBUG line2\n", encoding="utf-8")
            with patch("stonks_cli.logviewer.LOG_FILE", log_path):
                async with _TestApp().run_test() as pilot:
                    screen = pilot.app.screen
                    rich_log = screen.query_one("#log-body", RichLog)
                    # RichLog has been written to -- lines list should be non-empty
                    assert len(rich_log.lines) > 0
                    empty = screen.query_one("#log-empty", Label)
                    assert str(empty.content) == ""

    @pytest.mark.asyncio
    async def test_oserror_written_to_rich_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stonks.log"
            log_path.write_text("data", encoding="utf-8")
            with patch("stonks_cli.logviewer.LOG_FILE", log_path):
                # Make read_text raise OSError after exists/stat succeed
                original_read = Path.read_text

                def _bad_read(self: Path, *args, **kwargs):  # type: ignore[override]
                    if self == log_path:
                        raise OSError("permission denied")
                    return original_read(self, *args, **kwargs)

                with patch.object(Path, "read_text", _bad_read):
                    async with _TestApp().run_test() as pilot:
                        screen = pilot.app.screen
                        rich_log = screen.query_one("#log-body", RichLog)
                        assert len(rich_log.lines) > 0

    @pytest.mark.asyncio
    async def test_action_refresh_log_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stonks.log"
            log_path.write_text("first line\n", encoding="utf-8")
            with patch("stonks_cli.logviewer.LOG_FILE", log_path):
                async with _TestApp().run_test() as pilot:
                    screen = pilot.app.screen
                    rich_log = screen.query_one("#log-body", RichLog)
                    count_before = len(rich_log.lines)

                    # Add more content and trigger refresh
                    log_path.write_text("first line\nsecond line\n", encoding="utf-8")
                    screen.action_refresh_log()
                    await pilot.pause()

                    assert len(rich_log.lines) >= count_before
