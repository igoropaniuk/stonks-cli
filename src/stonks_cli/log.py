"""Logging configuration for stonks-cli."""

import logging
import logging.handlers
from pathlib import Path

from platformdirs import user_log_dir
from rich.console import Console
from rich.logging import RichHandler

# ~/.local/state/stonks/log/stonks.log  (Linux / macOS XDG)
# ~/Library/Logs/stonks/stonks.log      (macOS native)
# %LOCALAPPDATA%\stonks\Logs\stonks.log (Windows)
LOG_DIR = Path(user_log_dir("stonks"))
LOG_FILE = LOG_DIR / "stonks.log"


def setup_logging(level: int = logging.WARNING) -> None:
    """Configure the ``stonks_cli`` logger hierarchy.

    Two handlers are attached to the ``stonks_cli`` root logger:

    * **RotatingFileHandler** -- *level* and above, written to :data:`LOG_FILE`
      (max 1 MB, 3 backups).
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
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
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
