"""Logging configuration for stonks-cli."""

import logging
import os
import re
import time
from pathlib import Path

from platformdirs import user_log_dir
from rich.console import Console
from rich.logging import RichHandler

# ~/.local/state/stonks/log/  (Linux / macOS XDG)
# ~/Library/Logs/stonks/      (macOS native)
# %LOCALAPPDATA%\stonks\Logs\ (Windows)
LOG_DIR = Path(user_log_dir("stonks"))
# Per-process file: stonks.<pid>.log -- safe for concurrent instances.
LOG_FILE = LOG_DIR / f"stonks.{os.getpid()}.log"


_LOG_MAX_AGE_DAYS = 30
_LOG_FILE_RE = re.compile(r"stonks\.(\d+)\.log")


def _cleanup_stale_log_files() -> None:
    """Remove log files older than ``_LOG_MAX_AGE_DAYS`` days.

    Called once at startup so per-process files do not accumulate
    indefinitely.  Failures to remove a specific file are logged as
    warnings so permission issues stay visible.
    """
    cutoff = time.time() - _LOG_MAX_AGE_DAYS * 24 * 60 * 60
    own_pid = os.getpid()
    for path in LOG_DIR.glob("stonks.*.log"):
        m = _LOG_FILE_RE.fullmatch(path.name)
        if not m:
            continue
        if int(m.group(1)) == own_pid:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger("stonks_cli").warning(
                "Failed to remove stale log file %s", path
            )


def setup_logging(level: int = logging.WARNING) -> None:
    """Configure the ``stonks_cli`` logger hierarchy.

    Two handlers are attached to the ``stonks_cli`` root logger:

    * **FileHandler** -- *level* and above, written to :data:`LOG_FILE`
      (``stonks.<pid>.log``; one file per process, no rotation contention).
    * **RichHandler** -- *level* and above, written to *stderr*.
      Surfaces warnings/errors in the terminal without interfering with
      normal CLI output or the Textual TUI screen.

    The function is idempotent: calling it more than once is safe.
    If the log directory cannot be created (e.g. permission error), file
    logging is skipped and a warning is emitted via the console handler.
    """
    logger = logging.getLogger("stonks_cli")
    if logger.handlers:
        return  # already configured

    logger.setLevel(level)
    logger.propagate = False

    # --- console handler (always available) ---
    # RichHandler without an explicit console uses get_console() which targets
    # stdout; pass Console(stderr=True) to actually write to stderr.
    rh = RichHandler(
        level=level,
        show_time=False,
        show_path=False,
        rich_tracebacks=True,
        console=Console(stderr=True),
    )
    logger.addHandler(rh)

    # --- file handler (best-effort) ---
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_stale_log_files()
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(fh)
    except OSError:
        logger.warning(
            "Failed to set up file logging to %s; continuing with console only.",
            LOG_FILE,
            exc_info=True,
        )
