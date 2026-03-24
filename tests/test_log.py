"""Tests for stonks_cli.log (setup_logging)."""

import logging
import logging.handlers
from unittest.mock import patch

import pytest
from rich.logging import RichHandler

from stonks_cli.log import setup_logging


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
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
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
                "stonks_cli.log.logging.handlers.RotatingFileHandler",
                side_effect=OSError("no space left"),
            ),
        ):
            setup_logging()

        logger = logging.getLogger("stonks_cli")
        # Only the RichHandler should be attached; no file handler.
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], RichHandler)
