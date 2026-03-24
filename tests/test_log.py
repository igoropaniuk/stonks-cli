"""Tests for stonks_cli.log (setup_logging)."""

import logging
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.logging import RichHandler

from stonks_cli.log import _LOG_MAX_AGE_DAYS, _cleanup_stale_log_files, setup_logging


@pytest.fixture(autouse=True)
def _reset_logger():
    """Isolate each test: clear the stonks_cli logger before and restore after."""
    logger = logging.getLogger("stonks_cli")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    # Clear any state left by previous tests so setup_logging() won't return early.
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


class TestSetupLogging:
    def test_attaches_file_handler(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1

    def test_attaches_rich_handler(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        rich_handlers = [h for h in logger.handlers if isinstance(h, RichHandler)]
        assert len(rich_handlers) == 1

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        assert not log_dir.exists()
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()
        assert log_dir.exists()

    def test_idempotent_called_twice(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        assert len(logger.handlers) == 2  # one file + one rich, not doubled

    def test_default_level_is_warning(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        assert logger.level == logging.WARNING

    def test_custom_level_applied(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging(level=logging.DEBUG)

        logger = logging.getLogger("stonks_cli")
        assert logger.level == logging.DEBUG
        for handler in logger.handlers:
            assert handler.level == logging.DEBUG

    def test_does_not_propagate(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
        ):
            setup_logging()

        assert logging.getLogger("stonks_cli").propagate is False

    def test_file_oserror_falls_back_to_console_only(self, tmp_path):
        log_dir = tmp_path / "logs"
        with (
            patch("stonks_cli.log.LOG_DIR", log_dir),
            patch("stonks_cli.log.LOG_FILE", log_dir / "stonks.log"),
            patch(
                "stonks_cli.log.logging.FileHandler",
                side_effect=OSError("no space left"),
            ),
        ):
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        # Only the RichHandler should be attached; no file handler.
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], RichHandler)


class TestCleanupStaleLogFiles:
    def _make_old(self, path, days=_LOG_MAX_AGE_DAYS + 1):
        """Write a file and backdate its mtime by *days* days."""
        path.write_text("old log")
        old_mtime = time.time() - days * 24 * 60 * 60
        os.utime(path, (old_mtime, old_mtime))

    def test_removes_old_file(self, tmp_path):
        stale = tmp_path / "stonks.999999999.log"
        self._make_old(stale)
        with patch("stonks_cli.log.LOG_DIR", tmp_path):
            _cleanup_stale_log_files()
        assert not stale.exists()

    def test_keeps_recent_file(self, tmp_path):
        recent = tmp_path / "stonks.999999999.log"
        recent.write_text("recent log")  # mtime = now
        with patch("stonks_cli.log.LOG_DIR", tmp_path):
            _cleanup_stale_log_files()
        assert recent.exists()

    def test_keeps_own_pid_file(self, tmp_path):
        own = tmp_path / f"stonks.{os.getpid()}.log"
        self._make_old(own)
        with patch("stonks_cli.log.LOG_DIR", tmp_path):
            _cleanup_stale_log_files()
        assert own.exists()

    def test_ignores_unrelated_files(self, tmp_path):
        other = tmp_path / "other.log"
        self._make_old(other)
        with patch("stonks_cli.log.LOG_DIR", tmp_path):
            _cleanup_stale_log_files()
        assert other.exists()

    def test_warns_on_oserror(self, tmp_path, caplog):
        stale = tmp_path / "stonks.999999999.log"
        self._make_old(stale)
        _real_unlink = Path.unlink

        def _failing_unlink(self, *args, **kwargs):
            if self.name == stale.name:
                raise OSError("permission denied")
            return _real_unlink(self, *args, **kwargs)

        with (
            patch("stonks_cli.log.LOG_DIR", tmp_path),
            patch("pathlib.Path.unlink", _failing_unlink),
            caplog.at_level(logging.WARNING, logger="stonks_cli"),
        ):
            _cleanup_stale_log_files()
        assert any("stale log file" in r.message for r in caplog.records)

    def test_noop_when_log_dir_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch("stonks_cli.log.LOG_DIR", missing):
            _cleanup_stale_log_files()  # must not raise
